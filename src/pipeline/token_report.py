"""
pipeline/token_report.py
-----------------------------------------
Run-level token-efficiency report artifact (Requirement 10).

Aggregates the token telemetry collected over a pipeline run
(``agents.openai.telemetry.TelemetryCollector``) into a single
``TokenReport`` and writes it as ``token_report.json`` to the run's
output directory.

Data model
----------
TokenReport
    Top-level aggregate token totals/rates, per-stage breakdown, the top
    five most expensive requests, raw telemetry records, an optional
    delta comparison against a prior report, and a status field
    distinguishing a normal run from one with no telemetry data at all.

Functions
---------
generate_token_report
    Build a ``TokenReport`` from a ``TelemetryCollector`` and persist it
    to ``output_dir / "token_report.json"``.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agents.openai.telemetry import StageSummary, TelemetryCollector, TelemetryRecord
from utils.logging_utils import get_logger

logger = get_logger(__name__)

TOKEN_REPORT_FILENAME = "token_report.json"


@dataclass
class TokenReport:
    """Run-level token-efficiency report (Requirement 10).

    Attributes
    ----------
    total_input_tokens, total_output_tokens, total_cached_input_tokens,
    total_uncached_input_tokens:
        Aggregate token totals across every recorded request in the run.
        ``None`` when ``status == "telemetry_unavailable"`` (Req 10.6) --
        distinguishing "no data" from a misleading zero.
    overall_cache_rate:
        ``total_cached_input_tokens / total_input_tokens`` (0.0 when
        ``total_input_tokens`` is 0); ``None`` when telemetry unavailable.
    output_to_input_ratio:
        ``total_output_tokens / total_input_tokens`` (0.0 when
        ``total_input_tokens`` is 0); ``None`` when telemetry unavailable.
    per_stage:
        Per-stage ``StageSummary`` breakdown (``collector.stage_summaries()``).
    top_5_expensive:
        Top five requests by ``total_tokens`` descending, serialized as
        dicts (``collector.top_n_expensive(5)``).
    telemetry_records:
        All raw ``TelemetryRecord``s for the run, serialized as dicts.
    delta:
        Comparison against a prior ``token_report.json`` in the same
        output directory, if one exists and is readable: keys
        ``cache_rate_change``, ``avg_uncached_per_request_change``,
        ``total_tokens_change``. ``None`` if no prior report exists, the
        prior report is unreadable/malformed, or telemetry is unavailable
        for the current run.
    status:
        ``"complete"`` (normal run with telemetry) or
        ``"telemetry_unavailable"`` (Req 10.6: no telemetry data at all).
    """

    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_cached_input_tokens: int | None = None
    total_uncached_input_tokens: int | None = None
    overall_cache_rate: float | None = None
    output_to_input_ratio: float | None = None
    per_stage: list[StageSummary] = field(default_factory=list)
    top_5_expensive: list[dict] = field(default_factory=list)
    telemetry_records: list[dict] = field(default_factory=list)
    delta: dict | None = None
    status: str = "complete"


def _record_to_dict(record: TelemetryRecord) -> dict:
    """Serialize a TelemetryRecord (including its nested PromptFingerprint)
    to a plain dict."""
    return asdict(record)


def _compute_delta(current: TokenReport, prior_report_path: Path) -> dict | None:
    """Compute a delta comparison against a prior ``token_report.json``.

    Returns ``None`` (and logs a WARNING) if no prior report exists, or if
    it exists but is unreadable/malformed/incomplete -- per the Error
    Handling table row "Prior token_report.json unreadable or malformed".
    """
    if not prior_report_path.exists():
        return None

    try:
        with open(prior_report_path, "r", encoding="utf-8") as f:
            prior = json.load(f)

        if prior.get("status") != "complete":
            raise ValueError(
                f"prior token_report.json status is {prior.get('status')!r}, "
                "expected 'complete'"
            )

        prior_cache_rate = prior["overall_cache_rate"]
        prior_uncached = prior["total_uncached_input_tokens"]
        prior_input = prior["total_input_tokens"]
        prior_output = prior["total_output_tokens"]
        prior_records = prior["telemetry_records"]

        if any(
            v is None
            for v in (prior_cache_rate, prior_uncached, prior_input, prior_output)
        ):
            raise ValueError("prior token_report.json has null metric fields")

        prior_request_count = len(prior_records)
        prior_avg_uncached = (
            prior_uncached / prior_request_count if prior_request_count else 0.0
        )
        prior_total_tokens = prior_input + prior_output

        current_request_count = len(current.telemetry_records)
        current_avg_uncached = (
            current.total_uncached_input_tokens / current_request_count
            if current_request_count
            else 0.0
        )
        current_total_tokens = (
            current.total_input_tokens + current.total_output_tokens
        )

        return {
            "cache_rate_change": current.overall_cache_rate - prior_cache_rate,
            "avg_uncached_per_request_change": (
                current_avg_uncached - prior_avg_uncached
            ),
            "total_tokens_change": current_total_tokens - prior_total_tokens,
        }
    except Exception as exc:  # noqa: BLE001 -- any malformed-prior case is non-fatal
        logger.warning(
            "Skipping token report delta comparison: prior %s is unreadable "
            "or malformed (%s: %s)",
            prior_report_path,
            type(exc).__name__,
            exc,
        )
        return None


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as JSON to ``path`` atomically (temp file + replace),
    matching the write pattern used by ``pipeline.manifest.save_manifest``.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def generate_token_report(collector: TelemetryCollector, output_dir: Path) -> TokenReport:
    """Generate and write ``token_report.json`` to ``output_dir``.

    Aggregates every ``TelemetryRecord`` held by ``collector`` into
    top-level totals and rates (Req 10.1-10.4), computes a delta against
    a prior ``token_report.json`` in ``output_dir`` if one exists and is
    readable (Req 10.5), and -- when the collector holds no records at
    all -- writes a ``status="telemetry_unavailable"`` report instead of
    zero-valued metric fields (Req 10.6).

    Returns the ``TokenReport`` that was written.
    """
    output_dir = Path(output_dir)
    report_path = output_dir / TOKEN_REPORT_FILENAME

    records = collector.all_records()

    if not records:
        report = TokenReport(status="telemetry_unavailable")
        output_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(report_path, asdict(report))
        return report

    total_input = sum(r.input_tokens for r in records)
    total_output = sum(r.output_tokens for r in records)
    total_cached = sum(r.cached_input_tokens for r in records)
    total_uncached = sum(r.uncached_input_tokens for r in records)

    overall_cache_rate = (total_cached / total_input) if total_input else 0.0
    output_to_input_ratio = (total_output / total_input) if total_input else 0.0

    per_stage = collector.stage_summaries()
    top_5_expensive = [_record_to_dict(r) for r in collector.top_n_expensive(5)]
    telemetry_records = [_record_to_dict(r) for r in records]

    report = TokenReport(
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cached_input_tokens=total_cached,
        total_uncached_input_tokens=total_uncached,
        overall_cache_rate=overall_cache_rate,
        output_to_input_ratio=output_to_input_ratio,
        per_stage=per_stage,
        top_5_expensive=top_5_expensive,
        telemetry_records=telemetry_records,
        delta=None,
        status="complete",
    )

    report.delta = _compute_delta(report, report_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(report_path, asdict(report))
    return report
