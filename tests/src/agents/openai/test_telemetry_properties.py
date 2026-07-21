"""Property-based tests for agents.openai.telemetry.

Covers Properties 1, 2, 16, 17, and 18 from the token-efficient-extraction
design (see .kiro/specs/token-efficient-extraction/design.md, "Correctness
Properties" section).

Validates: Requirements 1.1, 1.4, 1.5, 8.1, 8.3, 8.4

Note on log capture: pytest's ``caplog`` fixture is function-scoped, and
Hypothesis raises a ``FailedHealthCheck`` (function-scoped fixture reused
across examples) when a function-scoped fixture is combined with
``@given``. Following the existing project convention (see
``tests/src/quality_control/test_qc_checks_semantic_source.py``), warning
assertions here use a small local ``_caplog_context`` context manager that
attaches a plain ``logging.Handler`` directly, instead of the ``caplog``
fixture.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from agents.openai.telemetry import (
    TelemetryCollector,
    TelemetryRecord,
    compute_prompt_fingerprint,
)

_TELEMETRY_LOGGER_NAME = "evi_trace.agents.openai.telemetry"

_STAGE_NAMES = [
    "extraction_chunk",
    "synthesis",
    "validation_repair",
    "cache_warmup",
    "finalization",
]

_IDENTIFIER_ALPHABET = st.characters(whitelist_categories=("L", "N"))


# ---------------------------------------------------------------------------
# Shared helpers
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


@contextlib.contextmanager
def _caplog_context(logger_name: str = _TELEMETRY_LOGGER_NAME):
    """Capture log records from ``logger_name`` during the with-block.

    A plain ``logging.Handler`` avoids the function-scoped-fixture health
    check Hypothesis raises when ``caplog`` is combined with ``@given``.
    """
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Handler()
    logger = logging.getLogger(logger_name)
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


# ---------------------------------------------------------------------------
# Property 1: Telemetry record completeness and uncached token invariant
# ---------------------------------------------------------------------------

_timestamp_strategy = st.datetimes(
    min_value=datetime(2020, 1, 1), max_value=datetime(2030, 1, 1)
).map(lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ"))


@st.composite
def _usage_data(draw):
    """Draw (input_tokens, cached_input_tokens, output_tokens) with the
    invariant 0 <= cached_input_tokens <= input_tokens honored by
    construction (never by ``assume``, to avoid discards)."""
    input_tokens = draw(st.integers(min_value=0, max_value=500_000))
    cached_input_tokens = draw(st.integers(min_value=0, max_value=input_tokens))
    output_tokens = draw(st.integers(min_value=0, max_value=500_000))
    return input_tokens, cached_input_tokens, output_tokens


@given(
    usage=_usage_data(),
    stage=st.sampled_from(_STAGE_NAMES),
    model=st.text(min_size=1, max_size=20, alphabet=_IDENTIFIER_ALPHABET),
    timestamp=_timestamp_strategy,
    stable_prefix=st.text(max_size=200),
    prompt_version=st.text(min_size=1, max_size=64, alphabet=_IDENTIFIER_ALPHABET),
)
@settings(max_examples=100)
def test_telemetry_record_completeness_and_uncached_invariant(
    usage, stage, model, timestamp, stable_prefix, prompt_version
):
    # Feature: token-efficient-extraction, Property 1: For any valid OpenAI API
    # response with usage data, the recorded TelemetryRecord SHALL contain all
    # required fields and uncached_input_tokens SHALL equal
    # input_tokens - cached_input_tokens.
    input_tokens, cached_input_tokens, output_tokens = usage
    fingerprint = compute_prompt_fingerprint(stable_prefix, prompt_version)
    uncached_input_tokens = input_tokens - cached_input_tokens
    total_tokens = input_tokens + output_tokens

    record = TelemetryRecord(
        stage=stage,
        model=model,
        timestamp=timestamp,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=uncached_input_tokens,
        total_tokens=total_tokens,
        prompt_fingerprint=fingerprint,
    )

    # Completeness: every required field is present and holds the value it
    # was constructed with.
    assert record.stage == stage
    assert record.model == model
    assert record.timestamp == timestamp
    assert record.input_tokens == input_tokens
    assert record.output_tokens == output_tokens
    assert record.cached_input_tokens == cached_input_tokens
    assert record.total_tokens == total_tokens
    assert record.prompt_fingerprint == fingerprint

    # Uncached token invariant.
    assert record.uncached_input_tokens == record.input_tokens - record.cached_input_tokens
    assert record.uncached_input_tokens == uncached_input_tokens
    assert record.total_tokens == record.input_tokens + record.output_tokens


# ---------------------------------------------------------------------------
# Property 2: Stage summary aggregation correctness
# ---------------------------------------------------------------------------

@st.composite
def _record_spec(draw):
    stage = draw(st.sampled_from(_STAGE_NAMES))
    input_tokens = draw(st.integers(min_value=0, max_value=50_000))
    cached_input_tokens = draw(st.integers(min_value=0, max_value=input_tokens))
    output_tokens = draw(st.integers(min_value=0, max_value=50_000))
    return stage, input_tokens, cached_input_tokens, output_tokens


@given(specs=st.lists(_record_spec(), min_size=0, max_size=30))
@settings(max_examples=100)
def test_stage_summary_aggregation_correctness(specs):
    # Feature: token-efficient-extraction, Property 2: For any list of
    # TelemetryRecords, the per-stage StageSummary SHALL have
    # total_input_tokens equal to the sum of input_tokens across all records
    # for that stage, and mean_cache_rate SHALL equal
    # total_cached_input_tokens / total_input_tokens (or 0.0 when
    # total_input_tokens is 0).
    collector = TelemetryCollector()

    stage_order: list[str] = []
    expected: dict[str, dict[str, int]] = {}
    for stage, input_tokens, cached_input_tokens, output_tokens in specs:
        collector.record(
            _make_record(
                stage=stage,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
            )
        )
        if stage not in expected:
            stage_order.append(stage)
            expected[stage] = {
                "input": 0,
                "output": 0,
                "cached": 0,
                "uncached": 0,
                "count": 0,
            }
        e = expected[stage]
        e["input"] += input_tokens
        e["output"] += output_tokens
        e["cached"] += cached_input_tokens
        e["uncached"] += input_tokens - cached_input_tokens
        e["count"] += 1

    summaries = collector.stage_summaries()

    # Every stage seen is represented exactly once, in first-seen order.
    assert [s.stage for s in summaries] == stage_order

    for summary in summaries:
        e = expected[summary.stage]
        assert summary.total_input_tokens == e["input"]
        assert summary.total_output_tokens == e["output"]
        assert summary.total_cached_input_tokens == e["cached"]
        assert summary.total_uncached_input_tokens == e["uncached"]
        assert summary.request_count == e["count"]

        expected_mean_cache_rate = (e["cached"] / e["input"]) if e["input"] else 0.0
        assert summary.mean_cache_rate == expected_mean_cache_rate
        assert 0.0 <= summary.mean_cache_rate <= 1.0


# ---------------------------------------------------------------------------
# Property 16: Prompt fingerprint correctness
# ---------------------------------------------------------------------------

@given(
    stable_prefix=st.text(max_size=500),
    prompt_version=st.text(max_size=64),
)
@settings(max_examples=100)
def test_prompt_fingerprint_correctness(stable_prefix, prompt_version):
    # Feature: token-efficient-extraction, Property 16: For any UTF-8 string
    # used as a Stable_Prefix, the computed stable_prefix_hash SHALL equal
    # the first 16 characters of the SHA-256 hex digest of that string's
    # UTF-8 bytes.
    fingerprint = compute_prompt_fingerprint(stable_prefix, prompt_version)

    expected_hash = hashlib.sha256(stable_prefix.encode("utf-8")).hexdigest()[:16]
    assert fingerprint.stable_prefix_hash == expected_hash
    assert len(fingerprint.stable_prefix_hash) == 16
    assert fingerprint.prompt_version == prompt_version

    # Determinism, implied by "the computed hash SHALL equal ...": recomputing
    # from the same inputs must reproduce the identical fingerprint.
    fingerprint_again = compute_prompt_fingerprint(stable_prefix, prompt_version)
    assert fingerprint_again == fingerprint


# ---------------------------------------------------------------------------
# Property 17: Cache diagnostics warning fires below threshold
# ---------------------------------------------------------------------------

@given(
    num_requests=st.integers(min_value=1, max_value=8),
    input_tokens=st.integers(min_value=1, max_value=10_000),
    cached_input_tokens=st.integers(min_value=0, max_value=10_000),
    threshold=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    stage=st.sampled_from(_STAGE_NAMES),
)
@settings(max_examples=100)
def test_cache_diagnostics_warns_only_below_threshold_with_at_least_3_requests(
    num_requests, input_tokens, cached_input_tokens, threshold, stage
):
    # Feature: token-efficient-extraction, Property 17: For any stage with at
    # least 3 completed requests where the observed cache rate is below the
    # configured threshold, the telemetry collector SHALL emit a cache
    # diagnostics warning. For any stage at or above the threshold, no
    # warning SHALL be emitted.
    cached_input_tokens = min(cached_input_tokens, input_tokens)

    collector = TelemetryCollector()
    for _ in range(num_requests):
        collector.record(
            _make_record(
                stage=stage,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
            )
        )

    with _caplog_context() as records:
        collector.check_cache_diagnostics(threshold=threshold)

    warnings = [r for r in records if r.levelno >= logging.WARNING]
    observed_rate_pct = (cached_input_tokens / input_tokens) * 100
    should_warn = num_requests >= 3 and observed_rate_pct < threshold

    if should_warn:
        assert len(warnings) == 1
        assert stage in warnings[0].getMessage()
    else:
        assert warnings == []


# ---------------------------------------------------------------------------
# Property 18: Prefix drift detection
# ---------------------------------------------------------------------------

@given(
    stage=st.sampled_from(_STAGE_NAMES),
    prompt_version=st.text(min_size=1, max_size=20, alphabet=_IDENTIFIER_ALPHABET),
    prefixes=st.lists(
        st.text(min_size=1, max_size=15, alphabet=_IDENTIFIER_ALPHABET),
        min_size=2,
        max_size=5,
        unique=True,
    ),
)
@settings(max_examples=100)
def test_prefix_drift_warns_when_same_stage_and_version_yield_distinct_hashes(
    stage, prompt_version, prefixes
):
    # Feature: token-efficient-extraction, Property 18: For any set of
    # TelemetryRecords within a single run where the same stage and
    # prompt_version produce two or more distinct stable_prefix_hash values,
    # the telemetry collector SHALL emit a drift warning.
    collector = TelemetryCollector()
    for prefix in prefixes:
        collector.record(
            _make_record(stage=stage, prompt_version=prompt_version, stable_prefix=prefix)
        )

    with _caplog_context() as records:
        collector.check_prefix_drift()

    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert stage in message
    assert prompt_version in message


@given(
    stage=st.sampled_from(_STAGE_NAMES),
    prompt_version=st.text(min_size=1, max_size=20, alphabet=_IDENTIFIER_ALPHABET),
    prefix=st.text(max_size=15, alphabet=_IDENTIFIER_ALPHABET),
    repeat_count=st.integers(min_value=1, max_value=6),
)
@settings(max_examples=100)
def test_prefix_drift_silent_when_stage_and_version_share_one_hash(
    stage, prompt_version, prefix, repeat_count
):
    # Feature: token-efficient-extraction, Property 18 (negative angle): a
    # single (stage, prompt_version) key that only ever produced one distinct
    # stable_prefix_hash within the run SHALL NOT trigger a drift warning,
    # regardless of how many records share that hash.
    collector = TelemetryCollector()
    for _ in range(repeat_count):
        collector.record(
            _make_record(stage=stage, prompt_version=prompt_version, stable_prefix=prefix)
        )

    with _caplog_context() as records:
        collector.check_prefix_drift()

    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert warnings == []
