"""
tests/src/pipeline/test_token_report.py
-----------------------------------------
Acceptance-criteria-level unit tests for ``pipeline.token_report``
(task 5.1), covering Requirement 10 (10.1-10.6):

  * token_report.json is written to the run output directory (10.1)
  * top-level aggregate fields, including overall_cache_rate and
    output_to_input_ratio with zero-division guards (10.2)
  * per-stage breakdown fields (10.3)
  * raw telemetry records + aggregated per-stage summary both present
    when telemetry data is available (10.4)
  * delta comparison against a prior token_report.json (10.5), including
    the "prior unreadable/malformed" error-handling case
  * status="telemetry_unavailable" with no misleading zero-valued metric
    fields when no telemetry data exists (10.6)

Dedicated property-based suites (Properties 19, 20: sum invariant and
delta correctness) live in ``test_token_report_properties.py`` (a later
task); this file covers the concrete acceptance-criteria behavior only.
"""
from __future__ import annotations

import json

import pytest

from agents.openai.telemetry import (
    PromptFingerprint,
    TelemetryCollector,
    TelemetryRecord,
)
from pipeline.token_report import TokenReport, generate_token_report


def _fp(prefix: str = "abc123", version: str = "v1") -> PromptFingerprint:
    return PromptFingerprint(stable_prefix_hash=prefix, prompt_version=version)


def _record(
    stage: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    timestamp: str = "2026-01-01T00:00:00Z",
    model: str = "gpt-5.5",
) -> TelemetryRecord:
    uncached = input_tokens - cached_input_tokens
    return TelemetryRecord(
        stage=stage,
        model=model,
        timestamp=timestamp,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=uncached,
        total_tokens=input_tokens + output_tokens,
        prompt_fingerprint=_fp(),
    )


def _collector_with(records: list[TelemetryRecord]) -> TelemetryCollector:
    collector = TelemetryCollector()
    for rec in records:
        collector.record(rec)
    return collector


class TestGenerateTokenReportBasic:
    def test_writes_token_report_json_to_output_dir(self, tmp_path):
        """Req 10.1: token_report.json is written to the run output dir."""
        collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]
        )
        generate_token_report(collector, tmp_path)

        report_path = tmp_path / "token_report.json"
        assert report_path.exists()
        with open(report_path) as f:
            data = json.load(f)
        assert data["status"] == "complete"

    def test_returns_token_report_dataclass_instance(self, tmp_path):
        collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]
        )
        report = generate_token_report(collector, tmp_path)
        assert isinstance(report, TokenReport)

    def test_top_level_aggregate_totals(self, tmp_path):
        """Req 10.2: total_input/output/cached/uncached tokens aggregated
        across every record."""
        collector = _collector_with(
            [
                _record("extraction_chunk", 1000, 100, 800),
                _record("synthesis", 500, 50, 300),
            ]
        )
        report = generate_token_report(collector, tmp_path)

        assert report.total_input_tokens == 1500
        assert report.total_output_tokens == 150
        assert report.total_cached_input_tokens == 1100
        assert report.total_uncached_input_tokens == 400

    def test_overall_cache_rate_and_output_to_input_ratio(self, tmp_path):
        """Req 10.2: overall_cache_rate = cached/input, output_to_input_ratio
        = output/input."""
        collector = _collector_with(
            [_record("extraction_chunk", 1000, 200, 750)]
        )
        report = generate_token_report(collector, tmp_path)

        assert report.overall_cache_rate == pytest.approx(750 / 1000)
        assert report.output_to_input_ratio == pytest.approx(200 / 1000)

    def test_overall_cache_rate_zero_division_guard(self, tmp_path):
        """Req 10.2: overall_cache_rate and output_to_input_ratio must not
        raise ZeroDivisionError when total_input_tokens is 0."""
        collector = _collector_with(
            [_record("extraction_chunk", 0, 0, 0)]
        )
        report = generate_token_report(collector, tmp_path)

        assert report.overall_cache_rate == 0.0
        assert report.output_to_input_ratio == 0.0

    def test_per_stage_breakdown_fields(self, tmp_path):
        """Req 10.3: per-stage breakdown includes stage name, totals,
        request_count, and mean_cache_rate."""
        collector = _collector_with(
            [
                _record("extraction_chunk", 1000, 100, 800),
                _record("extraction_chunk", 500, 50, 400),
                _record("synthesis", 200, 20, 100),
            ]
        )
        report = generate_token_report(collector, tmp_path)

        stages = {s.stage: s for s in report.per_stage}
        assert set(stages) == {"extraction_chunk", "synthesis"}

        chunk_summary = stages["extraction_chunk"]
        assert chunk_summary.total_input_tokens == 1500
        assert chunk_summary.total_output_tokens == 150
        assert chunk_summary.total_cached_input_tokens == 1200
        assert chunk_summary.total_uncached_input_tokens == 300
        assert chunk_summary.request_count == 2
        assert chunk_summary.mean_cache_rate == pytest.approx(1200 / 1500)

    def test_per_stage_sum_matches_top_level_totals(self, tmp_path):
        """Sum invariant underlying Property 19: per-stage totals sum to
        the top-level totals."""
        collector = _collector_with(
            [
                _record("extraction_chunk", 1000, 100, 800),
                _record("synthesis", 500, 50, 300),
                _record("validation_repair", 200, 20, 100),
            ]
        )
        report = generate_token_report(collector, tmp_path)

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

    def test_top_5_expensive_ranked_descending_by_total_tokens(self, tmp_path):
        """Req 10.2: top five most expensive requests ranked by total
        tokens descending."""
        records = [
            _record("extraction_chunk", 100, 10, 50),   # total 110
            _record("extraction_chunk", 900, 90, 100),  # total 990
            _record("extraction_chunk", 500, 50, 100),  # total 550
            _record("synthesis", 300, 30, 100),         # total 330
            _record("synthesis", 700, 70, 100),         # total 770
            _record("synthesis", 200, 20, 100),         # total 220
        ]
        collector = _collector_with(records)
        report = generate_token_report(collector, tmp_path)

        assert len(report.top_5_expensive) == 5
        totals = [r["total_tokens"] for r in report.top_5_expensive]
        assert totals == sorted(totals, reverse=True)
        assert totals[0] == 990
        assert 110 not in totals  # smallest (6th) record excluded

    def test_top_5_expensive_entries_are_dicts(self, tmp_path):
        collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]
        )
        report = generate_token_report(collector, tmp_path)
        assert all(isinstance(r, dict) for r in report.top_5_expensive)
        assert report.top_5_expensive[0]["stage"] == "extraction_chunk"
        # Nested PromptFingerprint dataclass must serialize to a plain dict too.
        assert isinstance(report.top_5_expensive[0]["prompt_fingerprint"], dict)

    def test_raw_telemetry_records_and_per_stage_both_present(self, tmp_path):
        """Req 10.4: when telemetry data is available, both the raw
        telemetry record array and the aggregated per-stage summary are
        included."""
        collector = _collector_with(
            [
                _record("extraction_chunk", 1000, 100, 800),
                _record("synthesis", 500, 50, 300),
            ]
        )
        report = generate_token_report(collector, tmp_path)

        assert len(report.telemetry_records) == 2
        assert all(isinstance(r, dict) for r in report.telemetry_records)
        assert len(report.per_stage) == 2

    def test_json_file_matches_returned_report(self, tmp_path):
        collector = _collector_with(
            [
                _record("extraction_chunk", 1000, 100, 800),
                _record("synthesis", 500, 50, 300),
            ]
        )
        report = generate_token_report(collector, tmp_path)

        with open(tmp_path / "token_report.json") as f:
            data = json.load(f)

        assert data["total_input_tokens"] == report.total_input_tokens
        assert data["total_output_tokens"] == report.total_output_tokens
        assert len(data["per_stage"]) == len(report.per_stage)
        assert len(data["telemetry_records"]) == len(report.telemetry_records)
        assert data["status"] == "complete"

    def test_creates_output_dir_if_missing(self, tmp_path):
        output_dir = tmp_path / "nested" / "outputs"
        collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]
        )
        generate_token_report(collector, output_dir)
        assert (output_dir / "token_report.json").exists()


class TestTelemetryUnavailable:
    def test_status_telemetry_unavailable_when_no_records(self, tmp_path):
        """Req 10.6: no telemetry data => status field indicates
        unavailability."""
        collector = TelemetryCollector()
        report = generate_token_report(collector, tmp_path)
        assert report.status == "telemetry_unavailable"

    def test_no_misleading_zero_valued_metric_fields(self, tmp_path):
        """Req 10.6: metric fields must not be zero-valued when telemetry
        is unavailable -- they must be None/absent, not 0."""
        collector = TelemetryCollector()
        report = generate_token_report(collector, tmp_path)

        assert report.total_input_tokens is None
        assert report.total_output_tokens is None
        assert report.total_cached_input_tokens is None
        assert report.total_uncached_input_tokens is None
        assert report.overall_cache_rate is None
        assert report.output_to_input_ratio is None
        assert report.per_stage == []
        assert report.top_5_expensive == []
        assert report.telemetry_records == []
        assert report.delta is None

    def test_telemetry_unavailable_written_to_json_as_null_not_zero(self, tmp_path):
        collector = TelemetryCollector()
        generate_token_report(collector, tmp_path)

        with open(tmp_path / "token_report.json") as f:
            data = json.load(f)

        assert data["status"] == "telemetry_unavailable"
        assert data["total_input_tokens"] is None
        assert data["overall_cache_rate"] is None

    def test_telemetry_unavailable_full_field_set_null_or_empty_on_disk(self, tmp_path):
        """Req 10.6 depth: every metric field -- not just the two spot-checked
        above -- must round-trip through the actual on-disk JSON as null/empty,
        never a misleading zero or omitted key. Mirrors the full in-memory
        assertion set of test_no_misleading_zero_valued_metric_fields, but
        verified against the real file bytes rather than the dataclass."""
        collector = TelemetryCollector()
        generate_token_report(collector, tmp_path)

        with open(tmp_path / "token_report.json") as f:
            data = json.load(f)

        assert data["status"] == "telemetry_unavailable"
        assert data["total_input_tokens"] is None
        assert data["total_output_tokens"] is None
        assert data["total_cached_input_tokens"] is None
        assert data["total_uncached_input_tokens"] is None
        assert data["overall_cache_rate"] is None
        assert data["output_to_input_ratio"] is None
        assert data["per_stage"] == []
        assert data["top_5_expensive"] == []
        assert data["telemetry_records"] == []
        assert data["delta"] is None
        # Every key from the dataclass schema must be present (not silently
        # dropped by the JSON encoder for a null-heavy record).
        assert set(data) == {
            "total_input_tokens",
            "total_output_tokens",
            "total_cached_input_tokens",
            "total_uncached_input_tokens",
            "overall_cache_rate",
            "output_to_input_ratio",
            "per_stage",
            "top_5_expensive",
            "telemetry_records",
            "delta",
            "status",
        }


class TestDeltaComparison:
    def test_no_delta_when_no_prior_report(self, tmp_path):
        collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]
        )
        report = generate_token_report(collector, tmp_path)
        assert report.delta is None

    def test_delta_computed_against_prior_report(self, tmp_path):
        """Req 10.5 / Property 20: delta includes cache_rate_change,
        avg_uncached_per_request_change, total_tokens_change."""
        prior_collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]  # cache_rate 0.8, uncached 200
        )
        prior_report = generate_token_report(prior_collector, tmp_path)

        current_collector = _collector_with(
            [
                _record("extraction_chunk", 1000, 100, 900),  # cache_rate 0.9, uncached 100
            ]
        )
        current_report = generate_token_report(current_collector, tmp_path)

        assert current_report.delta is not None
        expected_cache_rate_change = (
            current_report.overall_cache_rate - prior_report.overall_cache_rate
        )
        assert current_report.delta["cache_rate_change"] == pytest.approx(
            expected_cache_rate_change
        )
        # prior: 200 uncached / 1 request = 200; current: 100 / 1 = 100
        assert current_report.delta["avg_uncached_per_request_change"] == pytest.approx(
            100 - 200
        )
        # prior total_tokens = 1100; current total_tokens = 1100
        assert current_report.delta["total_tokens_change"] == pytest.approx(0)

    def test_delta_skipped_and_warning_logged_when_prior_malformed(self, tmp_path, caplog):
        """Error Handling: prior token_report.json unreadable/malformed =>
        skip delta, log WARNING, no exception raised."""
        prior_path = tmp_path / "token_report.json"
        prior_path.write_text("{ this is not valid json", encoding="utf-8")

        collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]
        )
        with caplog.at_level("WARNING"):
            report = generate_token_report(collector, tmp_path)

        assert report.delta is None
        assert any(
            "token_report.json" in rec.message.lower()
            or "token report" in rec.message.lower()
            for rec in caplog.records
        )

    def test_delta_skipped_when_prior_missing_expected_keys(self, tmp_path, caplog):
        prior_path = tmp_path / "token_report.json"
        prior_path.write_text(json.dumps({"status": "complete"}), encoding="utf-8")

        collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]
        )
        with caplog.at_level("WARNING"):
            report = generate_token_report(collector, tmp_path)

        assert report.delta is None

    def test_delta_skipped_when_prior_status_telemetry_unavailable(self, tmp_path):
        empty_collector = TelemetryCollector()
        generate_token_report(empty_collector, tmp_path)

        collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]
        )
        report = generate_token_report(collector, tmp_path)
        assert report.delta is None

    def test_delta_written_to_json_file_on_disk(self, tmp_path):
        """Req 10.5 depth: the computed delta must actually be persisted in
        token_report.json on disk, not merely populated on the in-memory
        TokenReport returned to the caller. (Existing
        test_json_file_matches_returned_report checks totals/per_stage/
        telemetry_records/status against the file but never the delta key.)"""
        prior_collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]  # cache_rate 0.8
        )
        generate_token_report(prior_collector, tmp_path)

        current_collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 900)]  # cache_rate 0.9
        )
        current_report = generate_token_report(current_collector, tmp_path)

        with open(tmp_path / "token_report.json") as f:
            data = json.load(f)

        assert data["delta"] is not None
        assert data["delta"] == current_report.delta
        assert data["delta"]["cache_rate_change"] == pytest.approx(0.1)
        assert data["delta"]["avg_uncached_per_request_change"] == pytest.approx(-100)
        assert data["delta"]["total_tokens_change"] == pytest.approx(0)

    def test_delta_chains_against_most_recently_written_prior_report(self, tmp_path):
        """Req 10.5 depth: a third real generate_token_report() call must
        diff against the SECOND report (the one most recently written to
        the same output_dir), not the first -- proving the delta is a
        genuine on-disk file comparison across separate pipeline runs
        rather than a fixed/cached baseline. Existing
        test_delta_computed_against_prior_report only exercises a single
        prior-vs-current pair, which cannot distinguish "compares against
        the latest file" from "compares against the first-ever file"."""
        gen1_collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 800)]  # cache_rate 0.8
        )
        generate_token_report(gen1_collector, tmp_path)

        gen2_collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 900)]  # cache_rate 0.9
        )
        generate_token_report(gen2_collector, tmp_path)

        gen3_collector = _collector_with(
            [_record("extraction_chunk", 1000, 100, 950)]  # cache_rate 0.95
        )
        gen3_report = generate_token_report(gen3_collector, tmp_path)

        assert gen3_report.delta is not None
        # Against gen2 (0.95 - 0.9 = 0.05) -- would be 0.15 if it had
        # incorrectly compared against gen1 instead.
        assert gen3_report.delta["cache_rate_change"] == pytest.approx(0.05)
        # gen2 uncached/request = 100; gen3 = 50 -> change -50 (would be
        # -150 if diffed against gen1's 200).
        assert gen3_report.delta["avg_uncached_per_request_change"] == pytest.approx(-50)
