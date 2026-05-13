"""
tests/quality_control/test_qc_verification_result.py
-----------------------------------------------------
Unit tests and property-based tests for VerificationResult field validation.

Covers:
  - Property 2: VerificationResult score is constrained to [0.0, 1.0]
    Validates: Requirements 2.4
  - Unit tests for status validation (Requirements 2.1, 2.2, 2.3)
  - Unit tests for evidence key presence (Requirements 2.5, 2.6)
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from quality_control.models import VerificationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_STATUSES = ["verified", "candidate_match", "no_match", "skipped", "unavailable"]

SIX_EVIDENCE_KEYS = [
    "found_sentence",
    "page_index",
    "prefix",
    "suffix",
    "block_bbox",
    "span_bboxes",
]

_EMPTY_EVIDENCE = {k: None for k in SIX_EVIDENCE_KEYS}


def _make_result(**kwargs) -> VerificationResult:
    """Build a minimal valid VerificationResult, overriding any field via kwargs."""
    defaults = dict(
        check_name="test_check",
        status="no_match",
        score=0.0,
        evidence={},
        details={},
    )
    defaults.update(kwargs)
    return VerificationResult(**defaults)


# ---------------------------------------------------------------------------
# Property 2: score is constrained to [0.0, 1.0]
# Feature: qc-migration, Property 2: VerificationResult score is constrained to [0.0, 1.0]
# Validates: Requirements 2.4
# ---------------------------------------------------------------------------


@given(st.floats(allow_nan=False, allow_infinity=False))
@settings(max_examples=100)
def test_score_outside_range_raises_value_error(score: float) -> None:
    """ValueError is raised for any score strictly outside [0.0, 1.0]."""
    assume(score < 0.0 or score > 1.0)
    with pytest.raises(ValueError, match="score"):
        _make_result(score=score)


@given(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
@settings(max_examples=100)
def test_score_inside_range_succeeds(score: float) -> None:
    """Construction succeeds for any score in [0.0, 1.0]."""
    result = _make_result(score=score)
    assert result.score == score


# ---------------------------------------------------------------------------
# Unit tests — status validation (Requirements 2.1, 2.2, 2.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", VALID_STATUSES)
def test_valid_status_succeeds(status: str) -> None:
    """Each of the five valid status values constructs without error."""
    result = _make_result(status=status)
    assert result.status == status


def test_invalid_status_raises_value_error() -> None:
    """An unrecognised status value raises ValueError."""
    with pytest.raises(ValueError, match="status"):
        _make_result(status="unknown")


@pytest.mark.parametrize(
    "bad_status",
    ["", "VERIFIED", "Verified", "pass", "fail", "error", "pending"],
)
def test_various_invalid_statuses_raise_value_error(bad_status: str) -> None:
    """A range of invalid status strings all raise ValueError."""
    with pytest.raises(ValueError):
        _make_result(status=bad_status)


# ---------------------------------------------------------------------------
# Unit tests — evidence key presence (Requirements 2.5, 2.6)
# ---------------------------------------------------------------------------


def test_all_six_evidence_keys_present_with_none_values() -> None:
    """An evidence dict with all six keys set to None is valid and round-trips."""
    result = _make_result(evidence=_EMPTY_EVIDENCE)
    for key in SIX_EVIDENCE_KEYS:
        assert key in result.evidence, f"Missing evidence key: {key!r}"
        assert result.evidence[key] is None


def test_evidence_keys_are_exactly_the_six_standard_keys() -> None:
    """The six standard keys are the expected set (no extras, no missing)."""
    assert set(SIX_EVIDENCE_KEYS) == {
        "found_sentence",
        "page_index",
        "prefix",
        "suffix",
        "block_bbox",
        "span_bboxes",
    }


def test_evidence_with_populated_values_is_accepted() -> None:
    """Evidence dict with non-None values for all six keys is accepted."""
    evidence = {
        "found_sentence": "The quick brown fox.",
        "page_index": 3,
        "prefix": "Before the sentence.",
        "suffix": "After the sentence.",
        "block_bbox": [10.0, 20.0, 200.0, 40.0],
        "span_bboxes": [[10.0, 20.0, 100.0, 40.0]],
    }
    result = _make_result(evidence=evidence)
    assert result.evidence["found_sentence"] == "The quick brown fox."
    assert result.evidence["page_index"] == 3


# ---------------------------------------------------------------------------
# Unit tests — boundary score values
# ---------------------------------------------------------------------------


def test_score_exactly_zero_is_valid() -> None:
    result = _make_result(score=0.0)
    assert result.score == 0.0


def test_score_exactly_one_is_valid() -> None:
    result = _make_result(score=1.0)
    assert result.score == 1.0


def test_score_just_below_zero_raises() -> None:
    with pytest.raises(ValueError):
        _make_result(score=-0.0001)


def test_score_just_above_one_raises() -> None:
    with pytest.raises(ValueError):
        _make_result(score=1.0001)


# ---------------------------------------------------------------------------
# Unit tests — field presence (Requirements 2.1, 2.2)
# ---------------------------------------------------------------------------


def test_verification_result_has_required_fields() -> None:
    """VerificationResult exposes all five required fields."""
    result = _make_result(
        check_name="my_check",
        status="verified",
        score=0.95,
        evidence=_EMPTY_EVIDENCE,
        details={"note": "ok"},
    )
    assert result.check_name == "my_check"
    assert result.status == "verified"
    assert result.score == 0.95
    assert isinstance(result.evidence, dict)
    assert isinstance(result.details, dict)
