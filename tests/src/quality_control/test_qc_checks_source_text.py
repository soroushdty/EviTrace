"""
tests/quality_control/test_qc_checks_source_text.py
----------------------------------------------------
Property-based tests and unit tests for SourceTextPresenceCheck.

Covers:
  - Property 3: SourceTextPresenceCheck output contract
    Validates: Requirements 2.5, 2.6, 3.4, 3.6, 3.7, 3.8
  - Unit tests (task 3.3):
    - Matcher called with correct positional args (needle, full_text, page_texts, blocks)
    - When matcher returns None: status="no_match", score=0.0
    - When matcher returns a dict: status="verified"
    - All six evidence keys present in both outcomes
    - Confidence passthrough: matcher returns {"confidence": 0.7} → score=0.7
    - Confidence clamping: {"confidence": 1.5} → score=1.0; {"confidence": -0.5} → score=0.0
    - Default confidence: matcher returns {} (no confidence key) → score=1.0
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quality_control.checks.source_text import SourceTextPresenceCheck
from quality_control.models import VerificationResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIX_EVIDENCE_KEYS = [
    "found_sentence",
    "page_index",
    "prefix",
    "suffix",
    "block_bbox",
    "span_bboxes",
]

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_NEEDLE = "the quick brown fox"
_FULL_TEXT = "In this document, the quick brown fox jumps over the lazy dog."
_PAGE_TEXTS = {0: "the quick brown fox jumps over the lazy dog."}
_BLOCKS = [{"text": "the quick brown fox", "page_index": 0, "bbox": [0, 0, 100, 20]}]


def _make_check(matcher: MagicMock) -> SourceTextPresenceCheck:
    return SourceTextPresenceCheck(matcher=matcher)


# ---------------------------------------------------------------------------
# Strategies for property-based tests
# ---------------------------------------------------------------------------

_page_texts_st = st.dictionaries(
    keys=st.integers(min_value=0, max_value=100),
    values=st.text(max_size=200),
    max_size=10,
)

_blocks_st = st.lists(
    st.dictionaries(
        keys=st.text(min_size=1, max_size=10),
        values=st.one_of(st.text(), st.integers()),
        max_size=5,
    ),
    max_size=10,
)

_matcher_dict_st = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=st.one_of(st.none(), st.text(), st.integers(), st.floats(allow_nan=False)),
    max_size=8,
)

_matcher_return_st = st.one_of(
    st.none(),
    _matcher_dict_st,
)


# ---------------------------------------------------------------------------
# Property 3: SourceTextPresenceCheck output contract
# Feature: qc-migration, Property 3: SourceTextPresenceCheck output contract
# Validates: Requirements 2.5, 2.6, 3.4, 3.6, 3.7, 3.8
# ---------------------------------------------------------------------------


@given(
    needle=st.text(),
    full_text=st.text(),
    page_texts=_page_texts_st,
    blocks=_blocks_st,
    matcher_return=_matcher_return_st,
)
@settings(max_examples=100)
def test_source_text_presence_check_output_contract(
    needle: str,
    full_text: str,
    page_texts: dict,
    blocks: list,
    matcher_return,
) -> None:
    """Property 3: For any inputs, SourceTextPresenceCheck satisfies its output contract.

    Asserts:
    1. The injected matcher is called with exactly (needle, full_text, page_texts, blocks).
    2. When matcher returns None: status="no_match", score=0.0, all six evidence keys
       present with None values.
    3. When matcher returns a non-None dict: status="verified", score in [0.0, 1.0],
       all six evidence keys present.
    """
    matcher = MagicMock(return_value=matcher_return)
    check = SourceTextPresenceCheck(matcher=matcher)

    result = check.run(needle, full_text, page_texts, blocks)

    # Assertion 1: matcher called with exactly the four positional args
    matcher.assert_called_once_with(needle, full_text, page_texts, blocks)

    # Assertions 2 / 3: status, score, and evidence contract
    if matcher_return is None:
        # No-match branch
        assert result.status == "no_match", (
            f"Expected status='no_match' when matcher returns None, got {result.status!r}"
        )
        assert result.score == 0.0, (
            f"Expected score=0.0 when matcher returns None, got {result.score}"
        )
        for key in SIX_EVIDENCE_KEYS:
            assert key in result.evidence, (
                f"Missing evidence key {key!r} in no_match result"
            )
            assert result.evidence[key] is None, (
                f"Expected evidence[{key!r}] to be None, got {result.evidence[key]!r}"
            )
    else:
        # Verified branch (matcher returned a dict)
        assert result.status == "verified", (
            f"Expected status='verified' when matcher returns a dict, got {result.status!r}"
        )
        assert 0.0 <= result.score <= 1.0, (
            f"score must be in [0.0, 1.0], got {result.score}"
        )
        for key in SIX_EVIDENCE_KEYS:
            assert key in result.evidence, (
                f"Missing evidence key {key!r} in verified result"
            )


# ---------------------------------------------------------------------------
# 1. Matcher called with correct args (Requirement 3.4)
# ---------------------------------------------------------------------------


def test_matcher_called_with_correct_positional_args() -> None:
    """run() must call matcher(needle, full_text, page_texts, blocks) exactly once."""
    matcher = MagicMock(return_value=None)
    check = _make_check(matcher)

    check.run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    matcher.assert_called_once_with(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)


def test_matcher_called_once_per_run_invocation() -> None:
    """Each call to run() triggers exactly one matcher call."""
    matcher = MagicMock(return_value=None)
    check = _make_check(matcher)

    check.run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
    check.run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert matcher.call_count == 2


def test_matcher_receives_exact_objects_passed_to_run() -> None:
    """The objects passed to run() are forwarded to matcher without copying."""
    matcher = MagicMock(return_value=None)
    check = _make_check(matcher)

    page_texts = {1: "page one text"}
    blocks = [{"text": "block"}]

    check.run("needle", "full text", page_texts, blocks)

    args = matcher.call_args[0]
    assert args[0] == "needle"
    assert args[1] == "full text"
    assert args[2] is page_texts
    assert args[3] is blocks


# ---------------------------------------------------------------------------
# 2. Matcher returns None → status="no_match", score=0.0 (Requirement 3.8)
# ---------------------------------------------------------------------------


def test_no_match_status_when_matcher_returns_none() -> None:
    """When matcher returns None, status must be 'no_match'."""
    matcher = MagicMock(return_value=None)
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.status == "no_match"


def test_no_match_score_is_zero_when_matcher_returns_none() -> None:
    """When matcher returns None, score must be 0.0."""
    matcher = MagicMock(return_value=None)
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == 0.0


def test_no_match_check_name_is_source_text_presence() -> None:
    """check_name must be 'source_text_presence' on no_match result."""
    matcher = MagicMock(return_value=None)
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.check_name == "source_text_presence"


# ---------------------------------------------------------------------------
# 3. Matcher returns a dict → status="verified" (Requirement 3.6)
# ---------------------------------------------------------------------------


def test_verified_status_when_matcher_returns_dict() -> None:
    """When matcher returns a non-None dict, status must be 'verified'."""
    matcher = MagicMock(return_value={"found_sentence": "the quick brown fox"})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.status == "verified"


def test_verified_check_name_is_source_text_presence() -> None:
    """check_name must be 'source_text_presence' on verified result."""
    matcher = MagicMock(return_value={})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.check_name == "source_text_presence"


def test_result_is_verification_result_instance() -> None:
    """run() must return a VerificationResult in both outcomes."""
    for return_val in [None, {}]:
        matcher = MagicMock(return_value=return_val)
        result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert isinstance(result, VerificationResult)


# ---------------------------------------------------------------------------
# 4. All six evidence keys present in both outcomes (Requirements 2.5, 2.6)
# ---------------------------------------------------------------------------


def test_all_six_evidence_keys_present_on_no_match() -> None:
    """All six standard evidence keys must be present when matcher returns None."""
    matcher = MagicMock(return_value=None)
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    for key in SIX_EVIDENCE_KEYS:
        assert key in result.evidence, f"Missing evidence key on no_match: {key!r}"


def test_all_six_evidence_keys_are_none_on_no_match() -> None:
    """All six evidence values must be None when matcher returns None."""
    matcher = MagicMock(return_value=None)
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    for key in SIX_EVIDENCE_KEYS:
        assert result.evidence[key] is None, f"Expected None for {key!r} on no_match"


def test_all_six_evidence_keys_present_on_verified() -> None:
    """All six standard evidence keys must be present when matcher returns a dict."""
    matcher = MagicMock(return_value={"found_sentence": "fox", "page_index": 0})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    for key in SIX_EVIDENCE_KEYS:
        assert key in result.evidence, f"Missing evidence key on verified: {key!r}"


def test_evidence_values_populated_from_matcher_result() -> None:
    """Evidence values are taken from the matcher result dict when present."""
    matcher_result = {
        "found_sentence": "the quick brown fox",
        "page_index": 2,
        "prefix": "In this document,",
        "suffix": "jumps over the lazy dog.",
        "block_bbox": [0, 0, 100, 20],
        "span_bboxes": [[0, 0, 50, 10]],
    }
    matcher = MagicMock(return_value=matcher_result)
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.evidence["found_sentence"] == "the quick brown fox"
    assert result.evidence["page_index"] == 2
    assert result.evidence["prefix"] == "In this document,"
    assert result.evidence["suffix"] == "jumps over the lazy dog."
    assert result.evidence["block_bbox"] == [0, 0, 100, 20]
    assert result.evidence["span_bboxes"] == [[0, 0, 50, 10]]


def test_missing_evidence_keys_in_matcher_result_default_to_none() -> None:
    """Evidence keys absent from the matcher result dict default to None."""
    matcher = MagicMock(return_value={"found_sentence": "fox"})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.evidence["found_sentence"] == "fox"
    assert result.evidence["page_index"] is None
    assert result.evidence["prefix"] is None
    assert result.evidence["suffix"] is None
    assert result.evidence["block_bbox"] is None
    assert result.evidence["span_bboxes"] is None


# ---------------------------------------------------------------------------
# 5. Confidence passthrough (Requirement 3.7)
# ---------------------------------------------------------------------------


def test_confidence_passthrough_0_7() -> None:
    """When matcher returns {"confidence": 0.7}, score must be 0.7."""
    matcher = MagicMock(return_value={"confidence": 0.7})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(0.7)


def test_confidence_passthrough_0_0() -> None:
    """When matcher returns {"confidence": 0.0}, score must be 0.0."""
    matcher = MagicMock(return_value={"confidence": 0.0})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(0.0)


def test_confidence_passthrough_1_0() -> None:
    """When matcher returns {"confidence": 1.0}, score must be 1.0."""
    matcher = MagicMock(return_value={"confidence": 1.0})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(1.0)


def test_confidence_passthrough_0_5() -> None:
    """When matcher returns {"confidence": 0.5}, score must be 0.5."""
    matcher = MagicMock(return_value={"confidence": 0.5})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 6. Confidence clamping (Requirement 3.7)
# ---------------------------------------------------------------------------


def test_confidence_clamped_above_1_0() -> None:
    """When matcher returns {"confidence": 1.5}, score must be clamped to 1.0."""
    matcher = MagicMock(return_value={"confidence": 1.5})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(1.0)


def test_confidence_clamped_below_0_0() -> None:
    """When matcher returns {"confidence": -0.5}, score must be clamped to 0.0."""
    matcher = MagicMock(return_value={"confidence": -0.5})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(0.0)


def test_confidence_large_positive_clamped_to_1_0() -> None:
    """Very large confidence values are clamped to 1.0."""
    matcher = MagicMock(return_value={"confidence": 999.0})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(1.0)


def test_confidence_large_negative_clamped_to_0_0() -> None:
    """Very negative confidence values are clamped to 0.0."""
    matcher = MagicMock(return_value={"confidence": -999.0})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 7. Default confidence when key absent (Requirement 3.7)
# ---------------------------------------------------------------------------


def test_default_confidence_when_no_confidence_key() -> None:
    """When matcher returns {} (no confidence key), score must default to 1.0."""
    matcher = MagicMock(return_value={})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(1.0)


def test_default_confidence_with_other_keys_but_no_confidence() -> None:
    """When matcher result has other keys but no 'confidence', score defaults to 1.0."""
    matcher = MagicMock(return_value={"found_sentence": "fox", "page_index": 0})
    result = _make_check(matcher).run(_NEEDLE, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)

    assert result.score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 8. Class-level check_name attribute (Requirement 3.2)
# ---------------------------------------------------------------------------


def test_check_name_class_attribute() -> None:
    """check_name must be a class-level attribute with value 'source_text_presence'."""
    assert SourceTextPresenceCheck.check_name == "source_text_presence"


def test_check_name_consistent_across_instances() -> None:
    """check_name is the same on all instances."""
    m1 = MagicMock(return_value=None)
    m2 = MagicMock(return_value={})
    c1 = _make_check(m1)
    c2 = _make_check(m2)
    assert c1.check_name == c2.check_name == "source_text_presence"
