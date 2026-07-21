"""
agents/openai/telemetry.py
-----------------------------------------
Per-request and per-stage token telemetry for OpenAI API calls.

Data models
-----------
PromptFingerprint
    Identifies the prompt template/version behind a call, for cache
    diagnostics and drift detection.

TelemetryRecord
    One request-level record capturing token counts, stage label, model
    name, timestamp, and Prompt_Fingerprint for a single OpenAI API call.

StageSummary
    Aggregated per-stage totals and mean cache rate.

TelemetryCollector
    Thread-safe collector of TelemetryRecords for a single pipeline run,
    with aggregation and cache/drift diagnostics.

Functions
---------
compute_prompt_fingerprint
    Compute a PromptFingerprint from a Stable_Prefix string and a
    prompt-version identifier.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass

from utils.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class PromptFingerprint:
    """Identifies the prompt template/version behind an API call.

    Attributes
    ----------
    stable_prefix_hash:
        SHA-256 hash of the Stable_Prefix's UTF-8 bytes, truncated to 16
        hex characters.
    prompt_version:
        Prompt-version identifier string (at most 64 characters), matching
        the ``prompt_cache_key_prefix`` from configuration.
    """

    stable_prefix_hash: str
    prompt_version: str


@dataclass
class TelemetryRecord:
    """One request-level record for a single OpenAI API call.

    Attributes
    ----------
    stage:
        Pipeline stage that issued this request: ``"extraction_chunk"`` |
        ``"synthesis"`` | ``"validation_repair"`` | ``"cache_warmup"`` |
        ``"finalization"``.
    model:
        Model name used for this request.
    timestamp:
        Request timestamp in ISO 8601 UTC format.
    input_tokens:
        Total input tokens reported by the API.
    output_tokens:
        Total output tokens reported by the API.
    cached_input_tokens:
        Input tokens served from the server-side prompt cache.
    uncached_input_tokens:
        Input tokens not served from cache; equals
        ``input_tokens - cached_input_tokens``.
    total_tokens:
        Total tokens (input + output) reported by the API.
    prompt_fingerprint:
        PromptFingerprint identifying the Stable_Prefix and prompt version
        behind this request.
    field_index_start:
        First field index handled by this request; set only for
        extraction-chunk requests.
    field_index_end:
        Last field index handled by this request; set only for
        extraction-chunk requests.
    domain_group:
        Domain group name for the fields handled by this request; set only
        for extraction-chunk requests.
    repair_attempt:
        1-based repair attempt number; set only for validation-repair
        requests.
    error_type:
        Validation error type that triggered a repair: ``"parse"`` |
        ``"schema"``; set only for validation-repair requests.
    """

    stage: str
    model: str
    timestamp: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    total_tokens: int
    prompt_fingerprint: PromptFingerprint
    field_index_start: int | None = None
    field_index_end: int | None = None
    domain_group: str | None = None
    repair_attempt: int | None = None
    error_type: str | None = None


@dataclass
class StageSummary:
    """Aggregated token totals and mean cache rate for a single stage.

    Attributes
    ----------
    stage:
        Pipeline stage name this summary aggregates.
    total_input_tokens:
        Sum of ``input_tokens`` across all records for this stage.
    total_output_tokens:
        Sum of ``output_tokens`` across all records for this stage.
    total_cached_input_tokens:
        Sum of ``cached_input_tokens`` across all records for this stage.
    total_uncached_input_tokens:
        Sum of ``uncached_input_tokens`` across all records for this stage.
    request_count:
        Number of records aggregated for this stage.
    mean_cache_rate:
        ``total_cached_input_tokens / total_input_tokens``, in ``[0.0, 1.0]``.
    """

    stage: str
    total_input_tokens: int
    total_output_tokens: int
    total_cached_input_tokens: int
    total_uncached_input_tokens: int
    request_count: int
    mean_cache_rate: float


def compute_prompt_fingerprint(stable_prefix: str, prompt_version: str) -> PromptFingerprint:
    """Compute a PromptFingerprint from a Stable_Prefix and prompt version.

    ``stable_prefix_hash`` is the first 16 hex characters of the SHA-256
    hex digest of ``stable_prefix`` encoded as UTF-8.
    """
    digest = hashlib.sha256(stable_prefix.encode("utf-8")).hexdigest()
    return PromptFingerprint(stable_prefix_hash=digest[:16], prompt_version=prompt_version)


class TelemetryCollector:
    """Thread-safe collector of TelemetryRecords for a single pipeline run.

    Records are appended under a lock so the collector can be shared safely
    across concurrent extraction chunk requests (e.g. issued from multiple
    asyncio tasks running on different threads, or from sync call sites).
    """

    def __init__(self) -> None:
        self._records: list[TelemetryRecord] = []
        self._lock = threading.Lock()

    def record(self, record: TelemetryRecord) -> None:
        """Append a TelemetryRecord to the collector."""
        with self._lock:
            self._records.append(record)

    def all_records(self) -> list[TelemetryRecord]:
        """Return a snapshot list of all recorded TelemetryRecords, in
        the order they were recorded."""
        with self._lock:
            return list(self._records)

    def stage_summaries(self) -> list[StageSummary]:
        """Aggregate recorded TelemetryRecords into per-stage totals.

        Stages are returned in first-seen order. ``mean_cache_rate`` is
        computed as ``total_cached_input_tokens / total_input_tokens`` for
        the stage (0.0 if the stage has zero total input tokens), per
        Requirement 1.4.
        """
        records = self.all_records()

        stage_order: list[str] = []
        totals: dict[str, dict[str, int]] = {}
        for rec in records:
            if rec.stage not in totals:
                stage_order.append(rec.stage)
                totals[rec.stage] = {
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_cached_input_tokens": 0,
                    "total_uncached_input_tokens": 0,
                    "request_count": 0,
                }
            stage_totals = totals[rec.stage]
            stage_totals["total_input_tokens"] += rec.input_tokens
            stage_totals["total_output_tokens"] += rec.output_tokens
            stage_totals["total_cached_input_tokens"] += rec.cached_input_tokens
            stage_totals["total_uncached_input_tokens"] += rec.uncached_input_tokens
            stage_totals["request_count"] += 1

        summaries: list[StageSummary] = []
        for stage in stage_order:
            stage_totals = totals[stage]
            total_input = stage_totals["total_input_tokens"]
            mean_cache_rate = (
                stage_totals["total_cached_input_tokens"] / total_input
                if total_input
                else 0.0
            )
            summaries.append(
                StageSummary(
                    stage=stage,
                    total_input_tokens=stage_totals["total_input_tokens"],
                    total_output_tokens=stage_totals["total_output_tokens"],
                    total_cached_input_tokens=stage_totals["total_cached_input_tokens"],
                    total_uncached_input_tokens=stage_totals["total_uncached_input_tokens"],
                    request_count=stage_totals["request_count"],
                    mean_cache_rate=mean_cache_rate,
                )
            )
        return summaries

    def top_n_expensive(self, n: int = 5) -> list[TelemetryRecord]:
        """Return the top ``n`` TelemetryRecords by ``total_tokens`` descending."""
        records = self.all_records()
        return sorted(records, key=lambda rec: rec.total_tokens, reverse=True)[:n]

    def check_cache_diagnostics(self, threshold: float = 50.0) -> None:
        """Log a warning for any stage with >=3 requests whose cache rate
        falls below ``threshold`` (a percentage in [0, 100]).

        Implements Requirement 8.3 / Property 17.
        """
        for summary in self.stage_summaries():
            if summary.request_count < 3:
                continue
            observed_rate_pct = summary.mean_cache_rate * 100
            if observed_rate_pct < threshold:
                logger.warning(
                    "Cache diagnostics: stage=%s observed cache rate=%.1f%% "
                    "is below configured threshold=%.1f%%",
                    summary.stage,
                    observed_rate_pct,
                    threshold,
                )

    def check_prefix_drift(self) -> None:
        """Log a warning when the same (stage, prompt_version) pair
        produced more than one distinct ``stable_prefix_hash`` within this
        run.

        Implements Requirement 8.4 / Property 18.
        """
        records = self.all_records()

        key_order: list[tuple[str, str]] = []
        hashes_by_key: dict[tuple[str, str], list[str]] = {}
        for rec in records:
            key = (rec.stage, rec.prompt_fingerprint.prompt_version)
            if key not in hashes_by_key:
                key_order.append(key)
                hashes_by_key[key] = []
            stable_hash = rec.prompt_fingerprint.stable_prefix_hash
            if stable_hash not in hashes_by_key[key]:
                hashes_by_key[key].append(stable_hash)

        for key in key_order:
            stage, prompt_version = key
            distinct_hashes = hashes_by_key[key]
            if len(distinct_hashes) > 1:
                logger.warning(
                    "Prompt prefix drift detected: stage=%s prompt_version=%s "
                    "distinct stable_prefix_hash values=%s",
                    stage,
                    prompt_version,
                    distinct_hashes,
                )
