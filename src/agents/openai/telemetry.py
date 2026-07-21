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

Functions
---------
compute_prompt_fingerprint
    Compute a PromptFingerprint from a Stable_Prefix string and a
    prompt-version identifier.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


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
