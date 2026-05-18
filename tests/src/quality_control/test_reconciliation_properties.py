"""
Property-based tests for quality-based branch reconciliation (Properties 7, 8).

Feature: audit-remediation
Validates: Requirements 4.1, 4.2, 4.3
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from quality_control.adjudicator import (
    select_primary_branch,
    _is_branch_failed,
    score_branch,
    BranchQualityScore,
)
from quality_control.models import Candidate


# ---------------------------------------------------------------------------
# Strategies for generating branch sets
# ---------------------------------------------------------------------------

# Generate a non-empty payload that represents content-producing branches.
_content_payload_strategy = st.one_of(
    st.text(min_size=10, max_size=500),
    st.lists(
        st.fixed_dictionaries({"text": st.text(min_size=5, max_size=200)}),
        min_size=1,
        max_size=5,
    ),
)

# Generate a failed/empty payload.
_failed_payload_strategy = st.one_of(
    st.just(None),
    st.just(""),
    st.just([]),
    st.just({}),
)

# Source names for branches.
_source_names = st.sampled_from([
    "grobid", "pdfplumber", "pymupdf", "paddleocr", "custom_extractor",
])


def _content_branch_strategy():
    """Strategy for a branch that has content (non-failed)."""
    return st.builds(
        Candidate,
        source=_source_names,
        index=st.integers(min_value=0, max_value=10),
        payload=_content_payload_strategy,
        status=st.sampled_from([None, "pass"]),
    )


def _failed_branch_strategy():
    """Strategy for a branch that is failed or empty."""
    return st.one_of(
        # Failed status with any payload
        st.builds(
            Candidate,
            source=_source_names,
            index=st.integers(min_value=0, max_value=10),
            payload=st.one_of(_content_payload_strategy, _failed_payload_strategy),
            status=st.just("fail"),
        ),
        # Non-fail status but empty payload
        st.builds(
            Candidate,
            source=_source_names,
            index=st.integers(min_value=0, max_value=10),
            payload=_failed_payload_strategy,
            status=st.sampled_from([None, "pass"]),
        ),
    )


# ---------------------------------------------------------------------------
# Property 7: Failed branches never selected as primary
# ---------------------------------------------------------------------------


@given(
    content_branches=st.lists(_content_branch_strategy(), min_size=1, max_size=4),
    failed_branches=st.lists(_failed_branch_strategy(), min_size=1, max_size=4),
)
@settings(max_examples=100)
def test_failed_branches_never_selected_as_primary(content_branches, failed_branches):
    """**Validates: Requirements 4.1, 4.2**

    For any set of extractor branches where at least one branch has non-empty
    text content, a branch that failed or returned empty text SHALL NOT be
    selected as the primary branch. The primary branch SHALL always be the one
    with the highest composite quality score among content-producing branches.
    """
    # Combine content and failed branches into a single set
    all_branches = content_branches + failed_branches

    # Ensure we have at least one content-producing branch
    assume(any(not _is_branch_failed(b) for b in all_branches))

    config = {"quality_control": {"discard_failed_branches": False}}

    selected, score, rationale = select_primary_branch(all_branches, config)

    # The selected branch must NOT be a failed branch
    assert not _is_branch_failed(selected), (
        f"Failed branch selected as primary: source={selected.source}, "
        f"status={selected.status}, payload={selected.payload!r:.100}"
    )

    # The selected branch must have the highest composite score among
    # content-producing branches
    all_scores = [
        (b, score_branch(b, all_branches))
        for b in all_branches
        if not _is_branch_failed(b)
    ]
    max_composite = max(s.composite for _, s in all_scores)
    assert score.composite == max_composite, (
        f"Selected branch composite={score.composite:.4f} != "
        f"max composite={max_composite:.4f}"
    )


# ---------------------------------------------------------------------------
# Property 8: Discard-failed-branches exclusion
# ---------------------------------------------------------------------------


@given(
    content_branches=st.lists(_content_branch_strategy(), min_size=1, max_size=4),
    failed_branches=st.lists(_failed_branch_strategy(), min_size=1, max_size=4),
)
@settings(max_examples=100)
def test_discard_failed_branches_exclusion(content_branches, failed_branches):
    """**Validates: Requirements 4.3**

    For any set of extractor branches with discard_failed_branches=true,
    branches with status="fail" or empty payload SHALL NOT appear in the
    candidate set used for primary-source selection.
    """
    all_branches = content_branches + failed_branches

    # Ensure we have at least one content-producing branch
    assume(any(not _is_branch_failed(b) for b in all_branches))

    config = {"quality_control": {"discard_failed_branches": True}}

    selected, score, rationale = select_primary_branch(all_branches, config)

    # The selected branch must NOT be a failed branch
    assert not _is_branch_failed(selected), (
        f"Failed branch selected as primary with discard_failed_branches=true: "
        f"source={selected.source}, status={selected.status}, "
        f"payload={selected.payload!r:.100}"
    )

    # Additionally verify that the selected branch is from the content set
    # (i.e., it was never in the failed/excluded candidate set)
    # Score all non-failed branches — these are the only valid candidates
    valid_candidates = [b for b in all_branches if not _is_branch_failed(b)]
    assert selected in valid_candidates, (
        "Selected branch is not in the valid candidate set"
    )

    # The selected branch must have the highest composite score among
    # valid candidates only
    valid_scores = [
        (b, score_branch(b, all_branches))
        for b in valid_candidates
    ]
    max_composite = max(s.composite for _, s in valid_scores)
    assert score.composite == max_composite, (
        f"Selected branch composite={score.composite:.4f} != "
        f"max valid composite={max_composite:.4f}"
    )
