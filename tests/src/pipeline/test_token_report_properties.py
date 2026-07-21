"""Property-based tests for pipeline.token_report.

Covers Properties 19 and 20 from the token-efficient-extraction design
(see .kiro/specs/token-efficient-extraction/design.md, "Correctness
Properties" section).

Validates: Requirements 9.5, 10.2, 10.3, 10.5

Note on file I/O with Hypothesis: following the project convention (see
``tests/src/pipeline/test_atomic_write_properties.py`` and
``tests/src/pipeline/test_evidence_cache_properties.py``), each example
uses ``tmp_path_factory.mktemp(...)`` inside the test body rather than the
function-scoped ``tmp_path`` fixture, since Hypothesis raises a
``FailedHealthCheck`` when a function-scoped fixture is reused across
``@given`` examples.

Note on Property 19's telemetry_unavailable edge case: per task 5.1's
review and Requirement 10.6's explicit carve-out, when a TokenReport has
``status == "telemetry_unavailable"``, ``per_stage`` is ``[]`` (so its
element-wise sums are trivially ``0``) while the top-level totals are
``None`` -- not ``0``. Property 19's sum invariant therefore targets the
normal-data ("complete") case, which is its evident intent and what
design.md's statement literally describes for a populated report; the
telemetry_unavailable case is covered separately below purely to confirm
it degrades gracefully rather than crashing.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agents.openai.telemetry import PromptFingerprint, TelemetryCollector, TelemetryRecord
from pipeline.token_report import generate_token_report

_STAGE_NAMES = [
    "extraction_chunk",
    "synthesis",
    "validation_repair",
    "cache_warmup",
    "finalization",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_record(
    *,
    stage: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    timestamp: str = "2026-01-01T00:00:00Z",
    model: str = "gpt-5.5",
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
        prompt_fingerprint=PromptFingerprint(stable_prefix_hash="abc123", prompt_version="v1"),
    )


@st.composite
def _record_spec(draw):
    """Draw (stage, input_tokens, cached_input_tokens, output_tokens) with
    the invariant 0 <= cached_input_tokens <= input_tokens honored by
    construction (never by ``assume``, to avoid discards)."""
    stage = draw(st.sampled_from(_STAGE_NAMES))
    input_tokens = draw(st.integers(min_value=0, max_value=50_000))
    cached_input_tokens = draw(st.integers(min_value=0, max_value=input_tokens))
    output_tokens = draw(st.integers(min_value=0, max_value=50_000))
    return stage, input_tokens, cached_input_tokens, output_tokens


def _collector_from_specs(specs) -> TelemetryCollector:
    collector = TelemetryCollector()
    for stage, input_tokens, cached_input_tokens, output_tokens in specs:
        collector.record(
            _make_record(
                stage=stage,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
            )
        )
    return collector


# ---------------------------------------------------------------------------
# Property 19: Token report sum invariant
# ---------------------------------------------------------------------------

@given(specs=st.lists(_record_spec(), min_size=1, max_size=25))
@settings(max_examples=100)
def test_property_19_per_stage_sums_equal_top_level_totals(specs, tmp_path_factory):
    # Feature: token-efficient-extraction, Property 19: For any generated
    # TokenReport, the sum of total_input_tokens across all per_stage entries
    # SHALL equal the top-level total_input_tokens, and the same SHALL hold
    # for total_output_tokens, total_cached_input_tokens, and
    # total_uncached_input_tokens.
    output_dir = tmp_path_factory.mktemp("token_report_prop19")
    collector = _collector_from_specs(specs)

    report = generate_token_report(collector, output_dir)

    assert report.status == "complete"
    assert sum(s.total_input_tokens for s in report.per_stage) == report.total_input_tokens
    assert sum(s.total_output_tokens for s in report.per_stage) == report.total_output_tokens
    assert (
        sum(s.total_cached_input_tokens for s in report.per_stage)
        == report.total_cached_input_tokens
    )
    assert (
        sum(s.total_uncached_input_tokens for s in report.per_stage)
        == report.total_uncached_input_tokens
    )
    # Corollary: per-stage request counts also partition the full record set.
    assert sum(s.request_count for s in report.per_stage) == len(specs)


def test_property_19_telemetry_unavailable_degrades_gracefully_not_literal_sum(
    tmp_path_factory,
):
    """Documented exception to Property 19 (Req 10.6 carve-out, flagged in
    task 5.1's review): with zero records, per_stage == [] so its sums are
    trivially 0, but the top-level totals are None -- distinguishing "no
    data" from a misleading zero rather than satisfying the sum invariant
    literally. This confirms the no-data case is handled gracefully, not
    that Property 19's equality holds for it."""
    output_dir = tmp_path_factory.mktemp("token_report_prop19_empty")
    collector = TelemetryCollector()

    report = generate_token_report(collector, output_dir)

    assert report.status == "telemetry_unavailable"
    assert report.per_stage == []
    assert sum(s.total_input_tokens for s in report.per_stage) == 0
    assert report.total_input_tokens is None


# ---------------------------------------------------------------------------
# Property 20: Token report delta correctness
# ---------------------------------------------------------------------------

@given(
    prior_specs=st.lists(_record_spec(), min_size=1, max_size=15),
    current_specs=st.lists(_record_spec(), min_size=1, max_size=15),
)
@settings(max_examples=100)
def test_property_20_delta_correctness(prior_specs, current_specs, tmp_path_factory):
    # Feature: token-efficient-extraction, Property 20: For any two
    # TokenReports (current and prior), the delta SHALL correctly compute
    # cache_rate_change as current.overall_cache_rate - prior.overall_cache_rate,
    # avg_uncached_per_request_change as the difference in average uncached
    # tokens per request, and total_tokens_change as
    # current.total_tokens - prior.total_tokens.
    output_dir = tmp_path_factory.mktemp("token_report_prop20")

    prior_report = generate_token_report(_collector_from_specs(prior_specs), output_dir)
    # generate_token_report reads the just-written token_report.json from
    # output_dir as the "prior" report when generating the current one.
    current_report = generate_token_report(_collector_from_specs(current_specs), output_dir)

    assert current_report.delta is not None
    assert set(current_report.delta.keys()) == {
        "cache_rate_change",
        "avg_uncached_per_request_change",
        "total_tokens_change",
    }

    expected_cache_rate_change = (
        current_report.overall_cache_rate - prior_report.overall_cache_rate
    )
    assert current_report.delta["cache_rate_change"] == pytest.approx(
        expected_cache_rate_change
    )

    prior_count = len(prior_report.telemetry_records)
    current_count = len(current_report.telemetry_records)
    prior_avg_uncached = prior_report.total_uncached_input_tokens / prior_count
    current_avg_uncached = current_report.total_uncached_input_tokens / current_count
    expected_avg_uncached_change = current_avg_uncached - prior_avg_uncached
    assert current_report.delta["avg_uncached_per_request_change"] == pytest.approx(
        expected_avg_uncached_change
    )

    prior_total_tokens = prior_report.total_input_tokens + prior_report.total_output_tokens
    current_total_tokens = (
        current_report.total_input_tokens + current_report.total_output_tokens
    )
    expected_total_tokens_change = current_total_tokens - prior_total_tokens
    assert current_report.delta["total_tokens_change"] == pytest.approx(
        expected_total_tokens_change
    )
