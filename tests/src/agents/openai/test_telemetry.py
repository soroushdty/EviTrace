"""Unit tests for agents.openai.telemetry — data models and prompt fingerprinting.

Requirements: 1.1, 1.2, 1.5, 8.1
"""
import hashlib

from agents.openai.telemetry import (
    PromptFingerprint,
    StageSummary,
    TelemetryRecord,
    compute_prompt_fingerprint,
)


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
