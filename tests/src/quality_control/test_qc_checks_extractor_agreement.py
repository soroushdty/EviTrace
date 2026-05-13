"""
tests/quality_control/test_qc_checks_extractor_agreement.py
------------------------------------------------------------
Property-based tests for ExtractorAgreementCheck.

Covers:
  - Property 6: ExtractorAgreementCheck agreement_rate formula
    Validates: Requirements 5.12, 5.14
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from quality_control.checks.extractor_agreement import ExtractorAgreementCheck

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENABLED_CONFIG = {
    "quality_control": {
        "semantic_verification": {
            "similarity_threshold": 0.85,
            "extractor_agreement": {
                "enabled": True,
                "len_filter": 0,   # no length filter so all blocks pass through
                "max_examples": 10,
            },
        }
    }
}


def _blocks_from_sentences(sentences: list[str]) -> list[dict]:
    """Convert a list of sentence strings into block dicts."""
    return [{"text": s} for s in sentences]


# ---------------------------------------------------------------------------
# Property 6: ExtractorAgreementCheck agreement_rate formula
# Feature: qc-migration, Property 6: ExtractorAgreementCheck agreement_rate formula
# Validates: Requirements 5.12, 5.14
# ---------------------------------------------------------------------------


@given(
    primary_sentence_count=st.integers(min_value=0, max_value=100),
    exact_match_count=st.integers(min_value=0, max_value=100),
    near_match_count=st.integers(min_value=0, max_value=100),
)
@settings(max_examples=100)
def test_agreement_rate_formula(
    primary_sentence_count: int,
    exact_match_count: int,
    near_match_count: int,
) -> None:
    """agreement_rate == (exact + near) / primary when primary > 0; 0.0 when primary == 0.

    We control the counts by constructing primary blocks with known sentences and
    configuring the exact_matcher mock to match exactly `exact_match_count` of them,
    then the semantic_matcher mock to match exactly `near_match_count` of the remainder.
    """
    # Keep counts valid: matches cannot exceed primary sentence count
    assume(exact_match_count + near_match_count <= primary_sentence_count)

    # Build primary sentences as unique strings so matching is deterministic
    primary_sentences = [f"primary sentence {i}" for i in range(primary_sentence_count)]

    # Candidate sentences mirror primary sentences (same count) so there are
    # enough candidates to match against.
    candidate_sentences = [f"candidate sentence {i}" for i in range(primary_sentence_count)]

    primary_blocks = _blocks_from_sentences(primary_sentences)
    candidate_blocks = _blocks_from_sentences(candidate_sentences)

    # exact_matcher returns True for the first `exact_match_count` primary sentences
    exact_matched_set: set[str] = set(primary_sentences[:exact_match_count])

    def exact_matcher(primary: str, candidate: str) -> bool:
        # Match when the primary sentence is in the exact-match set AND the
        # candidate index mirrors the primary index (one-to-one pairing).
        if primary not in exact_matched_set:
            return False
        # Derive the index from the sentence text and check the paired candidate
        idx = int(primary.split()[-1])
        return candidate == f"candidate sentence {idx}"

    # semantic_matcher returns a score above threshold for the next
    # `near_match_count` primary sentences (those not already exact-matched)
    near_matched_set: set[str] = set(
        primary_sentences[exact_match_count: exact_match_count + near_match_count]
    )
    threshold = 0.85

    def semantic_matcher(primary: str, candidate: str) -> float:
        if primary not in near_matched_set:
            return 0.0
        idx = int(primary.split()[-1])
        if candidate == f"candidate sentence {idx}":
            return threshold  # exactly at threshold → counts as near match
        return 0.0

    check = ExtractorAgreementCheck(
        exact_matcher=exact_matcher,
        semantic_matcher=semantic_matcher,
    )

    report = check.run(primary_blocks, candidate_blocks, _ENABLED_CONFIG)

    # Compute expected rate
    if primary_sentence_count > 0:
        expected_rate = (exact_match_count + near_match_count) / primary_sentence_count
    else:
        expected_rate = 0.0

    assert report["agreement_rate"] == pytest.approx(expected_rate), (
        f"primary={primary_sentence_count}, exact={exact_match_count}, "
        f"near={near_match_count}: expected {expected_rate}, got {report['agreement_rate']}"
    )


@given(
    exact_match_count=st.integers(min_value=0, max_value=100),
    near_match_count=st.integers(min_value=0, max_value=100),
)
@settings(max_examples=100)
def test_agreement_rate_is_zero_when_primary_count_is_zero(
    exact_match_count: int,
    near_match_count: int,
) -> None:
    """When primary_sentence_count == 0, agreement_rate must be 0.0 regardless of other counts.

    With no primary blocks the check has nothing to match against, so the
    agreement_rate must always be 0.0 (Requirement 5.14).
    """
    # No primary blocks → primary_sentence_count == 0
    primary_blocks: list[dict] = []
    # Provide some candidate blocks to ensure the zero-rate is not trivially
    # caused by an empty candidate list.
    candidate_sentences = [f"candidate sentence {i}" for i in range(exact_match_count + near_match_count)]
    candidate_blocks = _blocks_from_sentences(candidate_sentences)

    exact_matcher = MagicMock(return_value=False)
    semantic_matcher = MagicMock(return_value=0.0)

    check = ExtractorAgreementCheck(
        exact_matcher=exact_matcher,
        semantic_matcher=semantic_matcher,
    )

    report = check.run(primary_blocks, candidate_blocks, _ENABLED_CONFIG)

    assert report["agreement_rate"] == 0.0, (
        f"Expected 0.0 when primary_sentence_count==0, got {report['agreement_rate']}"
    )
    assert report["primary_sentence_count"] == 0


# ---------------------------------------------------------------------------
# Unit tests for ExtractorAgreementCheck (task 5.4)
# Requirements: 5.8, 5.9, 5.18, 5.19, 5.20
# ---------------------------------------------------------------------------

# Config for unit tests (separate from the PBT _ENABLED_CONFIG above)
_UNIT_CONFIG = {
    "quality_control": {
        "semantic_verification": {
            "extractor_agreement": {
                "enabled": True,
                "len_filter": 40,
                "max_examples": 10,
            }
        }
    }
}

# Blocks with text long enough to pass len_filter=40
_LONG_SENTENCE = "This is a long enough sentence for testing purposes here."
_PRIMARY_BLOCKS = [{"text": _LONG_SENTENCE}]
_CANDIDATE_BLOCKS = [{"text": _LONG_SENTENCE}]


class TestExactOnlyMode:
    """Exact-only mode: semantic_matcher=None.

    Requirements 5.19, 5.20.
    """

    def test_near_match_count_is_zero(self) -> None:
        """Exact-only mode produces near_match_count=0 (req 5.19)."""
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: False,  # no exact matches
            semantic_matcher=None,
        )
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, _UNIT_CONFIG)
        assert result["near_match_count"] == 0

    def test_semantic_threshold_is_zero(self) -> None:
        """semantic_threshold is 0.0 when semantic_matcher is None (req 5.20)."""
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: False,
            semantic_matcher=None,
        )
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, _UNIT_CONFIG)
        assert result["semantic_threshold"] == 0.0

    def test_report_has_all_nine_required_keys(self) -> None:
        """Report contains all 9 required keys (req 5.12)."""
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: True,
            semantic_matcher=None,
        )
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, _UNIT_CONFIG)
        required_keys = {
            "primary_sentence_count",
            "candidate_sentence_count",
            "exact_match_count",
            "near_match_count",
            "unmatched_primary_count",
            "unmatched_candidate_count",
            "agreement_rate",
            "semantic_threshold",
            "examples",
        }
        assert required_keys.issubset(result.keys()), (
            f"Missing keys: {required_keys - result.keys()}"
        )

    def test_examples_dict_has_required_keys(self) -> None:
        """examples dict has keys: unmatched_primary, unmatched_candidate, near_matches."""
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: False,
            semantic_matcher=None,
        )
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, _UNIT_CONFIG)
        examples = result["examples"]
        assert "unmatched_primary" in examples
        assert "unmatched_candidate" in examples
        assert "near_matches" in examples

    def test_complete_report_with_exact_match(self) -> None:
        """Exact-only mode with a matching sentence produces correct counts."""
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: p == c,
            semantic_matcher=None,
        )
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, _UNIT_CONFIG)
        assert result["exact_match_count"] == 1
        assert result["near_match_count"] == 0
        assert result["unmatched_primary_count"] == 0
        assert result["agreement_rate"] == 1.0
        assert result["semantic_threshold"] == 0.0


class TestSemanticMode:
    """Semantic mode: semantic_matcher returns a score above threshold.

    Requirements 5.8, 5.9.
    """

    def test_semantic_matcher_increments_near_match_count(self) -> None:
        """When exact_matcher returns False and semantic_matcher returns 0.9
        (above default threshold 0.85), near_match_count > 0 (req 5.8, 5.9)."""
        # exact_matcher never matches; semantic_matcher always returns 0.9
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: False,
            semantic_matcher=lambda p, c: 0.9,
        )
        # Config with similarity_threshold=0.85 (default)
        config = {
            "quality_control": {
                "semantic_verification": {
                    "similarity_threshold": 0.85,
                    "extractor_agreement": {
                        "enabled": True,
                        "len_filter": 40,
                        "max_examples": 10,
                    },
                }
            }
        }
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, config)
        assert result["near_match_count"] > 0

    def test_semantic_matcher_called_only_for_unmatched(self) -> None:
        """semantic_matcher is only called for sentences not matched by exact_matcher
        (req 5.8, 5.9)."""
        semantic_calls: list[tuple[str, str]] = []

        def tracking_semantic(p: str, c: str) -> float:
            semantic_calls.append((p, c))
            return 0.9

        # Two primary sentences; exact_matcher matches the first one
        primary_blocks = [
            {"text": "This is a long enough sentence for testing purposes here."},
            {"text": "Another long enough sentence that will not be matched exactly."},
        ]
        candidate_blocks = [
            {"text": "This is a long enough sentence for testing purposes here."},
            {"text": "Another long enough sentence that will not be matched exactly."},
        ]

        def exact_matcher(p: str, c: str) -> bool:
            return p == c and "Another" not in p

        check = ExtractorAgreementCheck(
            exact_matcher=exact_matcher,
            semantic_matcher=tracking_semantic,
        )
        config = {
            "quality_control": {
                "semantic_verification": {
                    "similarity_threshold": 0.85,
                    "extractor_agreement": {
                        "enabled": True,
                        "len_filter": 40,
                        "max_examples": 10,
                    },
                }
            }
        }
        result = check.run(primary_blocks, candidate_blocks, config)
        # semantic_matcher should only be called for the unmatched sentence
        assert len(semantic_calls) > 0
        # The exactly-matched sentence should not appear in semantic calls
        for p, _c in semantic_calls:
            assert "Another" in p or "testing purposes" not in p

    def test_near_match_score_below_threshold_not_counted(self) -> None:
        """When semantic_matcher returns a score below threshold, near_match_count stays 0."""
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: False,
            semantic_matcher=lambda p, c: 0.5,  # below 0.85 threshold
        )
        config = {
            "quality_control": {
                "semantic_verification": {
                    "similarity_threshold": 0.85,
                    "extractor_agreement": {
                        "enabled": True,
                        "len_filter": 40,
                        "max_examples": 10,
                    },
                }
            }
        }
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, config)
        assert result["near_match_count"] == 0


class TestDisabledCheck:
    """When enabled=False, the check returns a skipped report."""

    def test_disabled_returns_skipped_status(self) -> None:
        """Disabled check returns status='skipped' (req 5.2)."""
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: True,
            semantic_matcher=None,
        )
        config = {
            "quality_control": {
                "semantic_verification": {
                    "extractor_agreement": {
                        "enabled": False,
                    }
                }
            }
        }
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, config)
        assert result.get("status") == "skipped"

    def test_disabled_near_match_count_zero(self) -> None:
        """Disabled check returns near_match_count=0."""
        check = ExtractorAgreementCheck(
            exact_matcher=lambda p, c: True,
            semantic_matcher=None,
        )
        config = {
            "quality_control": {
                "semantic_verification": {
                    "extractor_agreement": {"enabled": False}
                }
            }
        }
        result = check.run(_PRIMARY_BLOCKS, _CANDIDATE_BLOCKS, config)
        assert result["near_match_count"] == 0
