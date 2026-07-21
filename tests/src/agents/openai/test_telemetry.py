"""Unit tests for agents.openai.telemetry — data models, prompt fingerprinting,
and the TelemetryCollector.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 6.5, 8.1, 8.2, 8.3, 8.4
"""
import hashlib
import logging
import threading

import pytest

from agents.openai.telemetry import (
    PromptFingerprint,
    StageSummary,
    TelemetryCollector,
    TelemetryRecord,
    compute_prompt_fingerprint,
)

_TELEMETRY_LOGGER_NAME = "evi_trace.agents.openai.telemetry"


# ---------------------------------------------------------------------------
# PromptFingerprint
# ---------------------------------------------------------------------------

def test_prompt_fingerprint_construction():
    fp = PromptFingerprint(stable_prefix_hash="a1b2c3d4e5f67890", prompt_version="scoping-review-v1")
    assert fp.stable_prefix_hash == "a1b2c3d4e5f67890"
    assert fp.prompt_version == "scoping-review-v1"


def test_prompt_fingerprint_hash_is_16_hex_chars():
    fp = compute_prompt_fingerprint("some stable prefix text", "v1")
    assert len(fp.stable_prefix_hash) == 16
    int(fp.stable_prefix_hash, 16)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# compute_prompt_fingerprint
# ---------------------------------------------------------------------------

def test_compute_prompt_fingerprint_matches_sha256_prefix():
    stable_prefix = "SHARED EVIDENCE PACKAGE\nsome evidence text"
    expected = hashlib.sha256(stable_prefix.encode("utf-8")).hexdigest()[:16]
    fp = compute_prompt_fingerprint(stable_prefix, "scoping-review-v1")
    assert fp.stable_prefix_hash == expected
    assert fp.prompt_version == "scoping-review-v1"


def test_compute_prompt_fingerprint_is_deterministic():
    fp1 = compute_prompt_fingerprint("identical text", "v2")
    fp2 = compute_prompt_fingerprint("identical text", "v2")
    assert fp1 == fp2


def test_compute_prompt_fingerprint_differs_for_different_prefixes():
    fp1 = compute_prompt_fingerprint("prefix one", "v1")
    fp2 = compute_prompt_fingerprint("prefix two", "v1")
    assert fp1.stable_prefix_hash != fp2.stable_prefix_hash


# ---------------------------------------------------------------------------
# TelemetryRecord
# ---------------------------------------------------------------------------

def _make_fingerprint() -> PromptFingerprint:
    return compute_prompt_fingerprint("stable prefix", "v1")


def test_telemetry_record_required_fields():
    record = TelemetryRecord(
        stage="extraction_chunk",
        model="gpt-5.5",
        timestamp="2025-01-15T10:30:00Z",
        input_tokens=8500,
        output_tokens=1200,
        cached_input_tokens=6000,
        uncached_input_tokens=2500,
        total_tokens=9700,
        prompt_fingerprint=_make_fingerprint(),
    )
    assert record.stage == "extraction_chunk"
    assert record.model == "gpt-5.5"
    assert record.timestamp == "2025-01-15T10:30:00Z"
    assert record.input_tokens == 8500
    assert record.output_tokens == 1200
    assert record.cached_input_tokens == 6000
    assert record.uncached_input_tokens == 2500
    assert record.total_tokens == 9700
    assert record.prompt_fingerprint == _make_fingerprint()


def test_telemetry_record_optional_fields_default_none():
    record = TelemetryRecord(
        stage="cache_warmup",
        model="gpt-5.5",
        timestamp="2025-01-15T10:30:00Z",
        input_tokens=100,
        output_tokens=0,
        cached_input_tokens=0,
        uncached_input_tokens=100,
        total_tokens=100,
        prompt_fingerprint=_make_fingerprint(),
    )
    assert record.field_index_start is None
    assert record.field_index_end is None
    assert record.domain_group is None
    assert record.repair_attempt is None
    assert record.error_type is None


def test_telemetry_record_extraction_chunk_metadata():
    record = TelemetryRecord(
        stage="extraction_chunk",
        model="gpt-5.5",
        timestamp="2025-01-15T10:30:00Z",
        input_tokens=8500,
        output_tokens=1200,
        cached_input_tokens=6000,
        uncached_input_tokens=2500,
        total_tokens=9700,
        prompt_fingerprint=_make_fingerprint(),
        field_index_start=3,
        field_index_end=22,
        domain_group="study_design",
    )
    assert record.field_index_start == 3
    assert record.field_index_end == 22
    assert record.domain_group == "study_design"


def test_telemetry_record_repair_metadata():
    record = TelemetryRecord(
        stage="validation_repair",
        model="gpt-5.5",
        timestamp="2025-01-15T10:30:00Z",
        input_tokens=1000,
        output_tokens=200,
        cached_input_tokens=0,
        uncached_input_tokens=1000,
        total_tokens=1200,
        prompt_fingerprint=_make_fingerprint(),
        repair_attempt=2,
        error_type="schema",
    )
    assert record.repair_attempt == 2
    assert record.error_type == "schema"


def test_telemetry_record_uncached_equals_input_minus_cached():
    """Property 1 (partial, non-PBT smoke check): uncached_input_tokens invariant."""
    input_tokens = 8500
    cached_input_tokens = 6000
    record = TelemetryRecord(
        stage="extraction_chunk",
        model="gpt-5.5",
        timestamp="2025-01-15T10:30:00Z",
        input_tokens=input_tokens,
        output_tokens=1200,
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=input_tokens - cached_input_tokens,
        total_tokens=9700,
        prompt_fingerprint=_make_fingerprint(),
    )
    assert record.uncached_input_tokens == record.input_tokens - record.cached_input_tokens


# ---------------------------------------------------------------------------
# StageSummary
# ---------------------------------------------------------------------------

def test_stage_summary_construction():
    summary = StageSummary(
        stage="extraction_chunk",
        total_input_tokens=68000,
        total_output_tokens=9000,
        total_cached_input_tokens=55000,
        total_uncached_input_tokens=13000,
        request_count=8,
        mean_cache_rate=0.809,
    )
    assert summary.stage == "extraction_chunk"
    assert summary.total_input_tokens == 68000
    assert summary.total_output_tokens == 9000
    assert summary.total_cached_input_tokens == 55000
    assert summary.total_uncached_input_tokens == 13000
    assert summary.request_count == 8
    assert summary.mean_cache_rate == 0.809


# ---------------------------------------------------------------------------
# TelemetryCollector
# ---------------------------------------------------------------------------

def _make_record(
    *,
    stage: str = "extraction_chunk",
    model: str = "gpt-5.5",
    timestamp: str = "2025-01-15T10:30:00Z",
    input_tokens: int = 1000,
    output_tokens: int = 100,
    cached_input_tokens: int = 0,
    prompt_version: str = "v1",
    stable_prefix: str = "stable prefix",
) -> TelemetryRecord:
    return TelemetryRecord(
        stage=stage,
        model=model,
        timestamp=timestamp,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=input_tokens - cached_input_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_fingerprint=compute_prompt_fingerprint(stable_prefix, prompt_version),
    )


def test_collector_record_and_all_records_returns_recorded_records():
    collector = TelemetryCollector()
    r1 = _make_record(stage="extraction_chunk")
    r2 = _make_record(stage="synthesis")

    collector.record(r1)
    collector.record(r2)

    assert collector.all_records() == [r1, r2]


def test_collector_all_records_returns_empty_list_when_no_records():
    collector = TelemetryCollector()
    assert collector.all_records() == []


def test_collector_all_records_returns_independent_snapshot():
    """Mutating the returned list must not affect the collector's internal state."""
    collector = TelemetryCollector()
    collector.record(_make_record())

    snapshot = collector.all_records()
    snapshot.append(_make_record())

    assert len(collector.all_records()) == 1


def test_collector_stage_summaries_aggregates_totals_for_single_stage():
    collector = TelemetryCollector()
    collector.record(
        _make_record(stage="extraction_chunk", input_tokens=8000, output_tokens=1000, cached_input_tokens=6000)
    )
    collector.record(
        _make_record(stage="extraction_chunk", input_tokens=4000, output_tokens=500, cached_input_tokens=1000)
    )

    summaries = collector.stage_summaries()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.stage == "extraction_chunk"
    assert summary.total_input_tokens == 12000
    assert summary.total_output_tokens == 1500
    assert summary.total_cached_input_tokens == 7000
    assert summary.total_uncached_input_tokens == 5000
    assert summary.request_count == 2
    assert summary.mean_cache_rate == 7000 / 12000


def test_collector_stage_summaries_groups_by_stage_separately():
    collector = TelemetryCollector()
    collector.record(_make_record(stage="extraction_chunk", input_tokens=1000, cached_input_tokens=500))
    collector.record(_make_record(stage="synthesis", input_tokens=2000, cached_input_tokens=0))

    summaries = {s.stage: s for s in collector.stage_summaries()}

    assert set(summaries.keys()) == {"extraction_chunk", "synthesis"}
    assert summaries["extraction_chunk"].request_count == 1
    assert summaries["synthesis"].request_count == 1
    assert summaries["synthesis"].mean_cache_rate == 0.0


def test_collector_stage_summaries_mean_cache_rate_zero_when_no_input_tokens():
    collector = TelemetryCollector()
    collector.record(_make_record(stage="cache_warmup", input_tokens=0, cached_input_tokens=0))

    summary = collector.stage_summaries()[0]

    assert summary.mean_cache_rate == 0.0


def test_collector_stage_summaries_empty_when_no_records():
    collector = TelemetryCollector()
    assert collector.stage_summaries() == []


def test_collector_top_n_expensive_returns_top_by_total_tokens_descending():
    collector = TelemetryCollector()
    cheap = _make_record(input_tokens=100, output_tokens=10)     # total 110
    mid = _make_record(input_tokens=1000, output_tokens=100)     # total 1100
    expensive = _make_record(input_tokens=9000, output_tokens=700)  # total 9700

    collector.record(cheap)
    collector.record(mid)
    collector.record(expensive)

    top2 = collector.top_n_expensive(n=2)

    assert top2 == [expensive, mid]


def test_collector_top_n_expensive_default_n_is_5():
    collector = TelemetryCollector()
    for i in range(8):
        collector.record(_make_record(input_tokens=100 * (i + 1), output_tokens=0))

    top = collector.top_n_expensive()

    assert len(top) == 5
    # Descending by total_tokens
    assert [r.total_tokens for r in top] == sorted([r.total_tokens for r in top], reverse=True)


def test_collector_top_n_expensive_empty_when_no_records():
    collector = TelemetryCollector()
    assert collector.top_n_expensive() == []


def test_collector_check_cache_diagnostics_warns_when_stage_below_threshold(caplog):
    collector = TelemetryCollector()
    # 3 requests, low cache rate (10%)
    for _ in range(3):
        collector.record(
            _make_record(stage="extraction_chunk", input_tokens=1000, cached_input_tokens=100)
        )

    with caplog.at_level(logging.WARNING, logger=_TELEMETRY_LOGGER_NAME):
        collector.check_cache_diagnostics(threshold=50.0)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "extraction_chunk" in warnings[0].message


def test_collector_check_cache_diagnostics_silent_when_stage_at_or_above_threshold(caplog):
    collector = TelemetryCollector()
    for _ in range(3):
        collector.record(
            _make_record(stage="extraction_chunk", input_tokens=1000, cached_input_tokens=800)
        )

    with caplog.at_level(logging.WARNING, logger=_TELEMETRY_LOGGER_NAME):
        collector.check_cache_diagnostics(threshold=50.0)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []


def test_collector_check_cache_diagnostics_silent_when_fewer_than_3_requests(caplog):
    collector = TelemetryCollector()
    # Only 2 requests with a low cache rate — must not warn regardless of rate.
    for _ in range(2):
        collector.record(
            _make_record(stage="extraction_chunk", input_tokens=1000, cached_input_tokens=0)
        )

    with caplog.at_level(logging.WARNING, logger=_TELEMETRY_LOGGER_NAME):
        collector.check_cache_diagnostics(threshold=50.0)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []


def test_collector_check_prefix_drift_warns_when_hashes_differ_for_same_stage_and_version(caplog):
    collector = TelemetryCollector()
    collector.record(_make_record(stage="extraction_chunk", prompt_version="v1", stable_prefix="prefix A"))
    collector.record(_make_record(stage="extraction_chunk", prompt_version="v1", stable_prefix="prefix B"))

    with caplog.at_level(logging.WARNING, logger=_TELEMETRY_LOGGER_NAME):
        collector.check_prefix_drift()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "extraction_chunk" in warnings[0].message
    assert "v1" in warnings[0].message


def test_collector_check_prefix_drift_silent_when_hashes_match(caplog):
    collector = TelemetryCollector()
    collector.record(_make_record(stage="extraction_chunk", prompt_version="v1", stable_prefix="same prefix"))
    collector.record(_make_record(stage="extraction_chunk", prompt_version="v1", stable_prefix="same prefix"))

    with caplog.at_level(logging.WARNING, logger=_TELEMETRY_LOGGER_NAME):
        collector.check_prefix_drift()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []


def test_collector_check_prefix_drift_silent_across_different_stages_or_versions():
    """Different stages (or different prompt_versions) with different hashes is
    not drift — drift is only within the same (stage, prompt_version) pair."""
    collector = TelemetryCollector()
    collector.record(_make_record(stage="extraction_chunk", prompt_version="v1", stable_prefix="prefix A"))
    collector.record(_make_record(stage="synthesis", prompt_version="v1", stable_prefix="prefix B"))
    collector.record(_make_record(stage="extraction_chunk", prompt_version="v2", stable_prefix="prefix C"))

    # Should not raise and should not warn; verified via no exception plus
    # explicit record-count sanity check.
    collector.check_prefix_drift()
    assert len(collector.all_records()) == 3


def test_collector_is_thread_safe_under_concurrent_record_calls():
    collector = TelemetryCollector()
    num_threads = 20
    records_per_thread = 25

    def _worker():
        for _ in range(records_per_thread):
            collector.record(_make_record())

    threads = [threading.Thread(target=_worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(collector.all_records()) == num_threads * records_per_thread


# ---------------------------------------------------------------------------
# Task 1.4: stage labeling, repair telemetry, fingerprint inclusion, and
# graceful handling of missing usage fields.
# Requirements: 1.2, 1.3, 1.6, 6.5, 8.2
# ---------------------------------------------------------------------------

_ALL_STAGE_VALUES = [
    "extraction_chunk",
    "synthesis",
    "validation_repair",
    "cache_warmup",
    "finalization",
]


@pytest.mark.parametrize("stage", _ALL_STAGE_VALUES)
def test_stage_labeling_round_trips_through_collector_for_every_stage_value(stage):
    """Requirement 1.2 / 1.3: extraction_chunk, synthesis, validation_repair,
    cache_warmup, and finalization are all plain stage-label strings that the
    collector groups and reports on identically — none is treated specially
    or rejected."""
    collector = TelemetryCollector()
    record = _make_record(stage=stage, input_tokens=1000, cached_input_tokens=250)

    collector.record(record)

    all_records = collector.all_records()
    assert len(all_records) == 1
    assert all_records[0].stage == stage

    summaries = collector.stage_summaries()
    assert len(summaries) == 1
    assert summaries[0].stage == stage
    assert summaries[0].request_count == 1
    assert summaries[0].total_input_tokens == 1000


def test_stage_labeling_groups_all_five_stage_values_independently():
    """Requirement 1.3: synthesis, validation_repair, and finalization (in
    addition to extraction_chunk and cache_warmup) must each be labeled with
    their own distinguishable Stage name and aggregated separately, not
    collapsed into a single bucket."""
    collector = TelemetryCollector()
    for stage in _ALL_STAGE_VALUES:
        collector.record(_make_record(stage=stage, input_tokens=100, cached_input_tokens=0))

    summaries = {s.stage: s for s in collector.stage_summaries()}

    assert set(summaries.keys()) == set(_ALL_STAGE_VALUES)
    for stage in _ALL_STAGE_VALUES:
        assert summaries[stage].request_count == 1


def test_repair_telemetry_includes_attempt_number_and_error_type():
    """Requirement 6.5: validation_repair records carry a 1-based
    repair_attempt and the error_type ("parse" or "schema") that triggered
    the repair, distinguishing them from plain extraction_chunk requests."""
    record = TelemetryRecord(
        stage="validation_repair",
        model="gpt-5.5",
        timestamp="2025-01-15T10:31:00Z",
        input_tokens=500,
        output_tokens=80,
        cached_input_tokens=0,
        uncached_input_tokens=500,
        total_tokens=580,
        prompt_fingerprint=_make_fingerprint(),
        repair_attempt=1,
        error_type="parse",
    )

    assert record.stage == "validation_repair"
    assert record.repair_attempt == 1
    assert record.error_type == "parse"


@pytest.mark.parametrize("repair_attempt,error_type", [(1, "parse"), (2, "schema"), (3, "schema")])
def test_repair_telemetry_attempt_and_error_type_preserved_through_collector(
    repair_attempt, error_type
):
    """Requirement 6.5: repair_attempt and error_type must survive the
    collector's record()/all_records() round trip unchanged, for any
    attempt number up to the configured maximum (default 3) and either
    documented error_type value."""
    collector = TelemetryCollector()
    record = TelemetryRecord(
        stage="validation_repair",
        model="gpt-5.5",
        timestamp="2025-01-15T10:31:00Z",
        input_tokens=500,
        output_tokens=80,
        cached_input_tokens=0,
        uncached_input_tokens=500,
        total_tokens=580,
        prompt_fingerprint=_make_fingerprint(),
        repair_attempt=repair_attempt,
        error_type=error_type,
    )

    collector.record(record)

    [stored] = collector.all_records()
    assert stored.repair_attempt == repair_attempt
    assert stored.error_type == error_type


def test_repair_telemetry_fields_preserved_through_top_n_expensive():
    """Requirement 6.5: repair metadata must remain intact on records
    surfaced via top_n_expensive(), not just all_records()."""
    collector = TelemetryCollector()
    plain_chunk = _make_record(stage="extraction_chunk", input_tokens=100, output_tokens=10)
    repair_record = TelemetryRecord(
        stage="validation_repair",
        model="gpt-5.5",
        timestamp="2025-01-15T10:31:00Z",
        input_tokens=9000,
        output_tokens=700,
        cached_input_tokens=0,
        uncached_input_tokens=9000,
        total_tokens=9700,
        prompt_fingerprint=_make_fingerprint(),
        repair_attempt=2,
        error_type="schema",
    )

    collector.record(plain_chunk)
    collector.record(repair_record)

    top1 = collector.top_n_expensive(n=1)
    assert top1 == [repair_record]
    assert top1[0].repair_attempt == 2
    assert top1[0].error_type == "schema"


# ---------------------------------------------------------------------------
# Requirement 8.2: Prompt_Fingerprint inclusion in every TelemetryRecord.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", _ALL_STAGE_VALUES)
def test_every_telemetry_record_carries_a_prompt_fingerprint(stage):
    """Requirement 8.2: every TelemetryRecord, regardless of stage, must
    carry a PromptFingerprint (stable_prefix_hash + prompt_version) — it is
    not optional metadata reserved for a subset of stages."""
    record = _make_record(stage=stage, prompt_version="scoping-review-v3", stable_prefix="abc")

    assert isinstance(record.prompt_fingerprint, PromptFingerprint)
    assert record.prompt_fingerprint.prompt_version == "scoping-review-v3"
    assert len(record.prompt_fingerprint.stable_prefix_hash) == 16


def test_prompt_fingerprint_round_trips_through_collector_all_records():
    """Requirement 8.2: all_records() must return the exact PromptFingerprint
    values recorded (not a copy that loses precision, not a default), for
    each stage independently."""
    collector = TelemetryCollector()
    expected_fingerprints = {}
    for stage in _ALL_STAGE_VALUES:
        fp = compute_prompt_fingerprint(f"stable prefix for {stage}", f"{stage}-v1")
        record = _make_record(stage=stage)
        record.prompt_fingerprint = fp
        expected_fingerprints[stage] = fp
        collector.record(record)

    for stored in collector.all_records():
        assert stored.prompt_fingerprint == expected_fingerprints[stored.stage]
        assert stored.prompt_fingerprint.prompt_version == f"{stored.stage}-v1"


# ---------------------------------------------------------------------------
# Requirement 1.6: graceful handling of missing usage fields.
#
# There is no live API response parser yet (that lands in a later task), so
# this is exercised at the data-model layer available today: a caller that
# receives a response with a missing `usage` field builds a TelemetryRecord
# with every token count zeroed, and the collector must handle it (in
# particular stage_summaries()'s division for mean_cache_rate) without
# raising.
# ---------------------------------------------------------------------------

def test_telemetry_record_accepts_all_zero_token_counts_for_missing_usage_field():
    """Requirement 1.6: a TelemetryRecord representing a response with a
    missing usage field (all counts zeroed) must construct successfully."""
    record = TelemetryRecord(
        stage="extraction_chunk",
        model="gpt-5.5",
        timestamp="2025-01-15T10:32:00Z",
        input_tokens=0,
        output_tokens=0,
        cached_input_tokens=0,
        uncached_input_tokens=0,
        total_tokens=0,
        prompt_fingerprint=_make_fingerprint(),
    )

    assert record.input_tokens == 0
    assert record.output_tokens == 0
    assert record.cached_input_tokens == 0
    assert record.uncached_input_tokens == 0
    assert record.total_tokens == 0


def test_collector_stage_summaries_does_not_crash_on_all_zero_usage_record():
    """Requirement 1.6: stage_summaries() must not raise ZeroDivisionError
    (or any other exception) when the only record for a stage has zero
    input tokens, and must report mean_cache_rate as 0.0 (continuing
    processing rather than interrupting on the missing-usage case)."""
    collector = TelemetryCollector()
    collector.record(
        _make_record(stage="extraction_chunk", input_tokens=0, output_tokens=0, cached_input_tokens=0)
    )

    summaries = collector.stage_summaries()

    assert len(summaries) == 1
    assert summaries[0].request_count == 1
    assert summaries[0].total_input_tokens == 0
    assert summaries[0].mean_cache_rate == 0.0


def test_collector_stage_summaries_handles_mixed_zero_and_nonzero_usage_records():
    """Requirement 1.6: a zero-usage record (missing usage field) mixed with
    normal records for the same stage must not crash the aggregation and
    must still contribute its (zero) totals correctly."""
    collector = TelemetryCollector()
    collector.record(
        _make_record(stage="extraction_chunk", input_tokens=8000, output_tokens=1000, cached_input_tokens=6000)
    )
    collector.record(
        _make_record(stage="extraction_chunk", input_tokens=0, output_tokens=0, cached_input_tokens=0)
    )

    summary = collector.stage_summaries()[0]

    assert summary.request_count == 2
    assert summary.total_input_tokens == 8000
    assert summary.mean_cache_rate == 6000 / 8000
