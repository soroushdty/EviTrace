"""Unit tests for pipeline/deterministic_merge.py.

Covers basic construction and the core merge rules from Requirement 5 and
Requirement 4.3 (Deterministic Merge Before LLM Synthesis):

- MergeResult dataclass construction/defaults
- normalize_value whitespace handling
- All-agree merge uses the normalized value as canonical form (Req 5.1),
  independent of chunk_results list position (Req 5.7)
- Single-provider merge (Req 5.5)
- All-null/absent -> "nr" (Req 5.2, 4.3)
- Disagreement -> conflict (Req 5.6 requires no conflicts to skip synthesis)
- Evidence_ID dedup, sorted ascending (Req 5.3)
- Confidence resolution h > m > l > nr (Req 5.4)
- Order-independence across a permutation of chunk_results (Req 5.7)

Full property-based coverage of these rules (order-independence /
confluence, non-conflicting merge, dedup, confidence resolution) is the
scope of tasks 2.2/2.3; this file only establishes the TDD RED/GREEN cycle
and basic example coverage for task 2.1.
"""
from __future__ import annotations

from pipeline.deterministic_merge import (
    MergeResult,
    deterministic_merge,
    normalize_value,
)


# ---------------------------------------------------------------------------
# MergeResult dataclass
# ---------------------------------------------------------------------------


def test_merge_result_construction_defaults():
    result = MergeResult()
    assert result.merged_fields == []
    assert result.conflicts == []
    assert result.skipped_synthesis is False


def test_merge_result_construction_explicit():
    result = MergeResult(
        merged_fields=[{"i": 1, "v": "x", "loc": [], "c": "h"}],
        conflicts=[3],
        skipped_synthesis=False,
    )
    assert result.merged_fields == [{"i": 1, "v": "x", "loc": [], "c": "h"}]
    assert result.conflicts == [3]
    assert result.skipped_synthesis is False


# ---------------------------------------------------------------------------
# normalize_value
# ---------------------------------------------------------------------------


def test_normalize_value_strips_and_collapses_whitespace():
    assert normalize_value("  hello   world  ") == "hello world"


def test_normalize_value_collapses_tabs_and_newlines():
    assert normalize_value("hello\t\nworld") == "hello world"


def test_normalize_value_none_passthrough():
    assert normalize_value(None) is None


def test_normalize_value_empty_string():
    assert normalize_value("") == ""


def test_normalize_value_already_normalized():
    assert normalize_value("hello world") == "hello world"


# ---------------------------------------------------------------------------
# deterministic_merge: all-agree -> normalized canonical value (Req 5.1)
# ---------------------------------------------------------------------------


def test_all_agree_after_normalization_uses_normalized_canonical_value():
    chunk_results = [
        [{"i": 1, "v": "Alpha  Beta", "loc": ["S000001"], "c": "h"}],
        [{"i": 1, "v": "Alpha Beta", "loc": ["S000002"], "c": "m"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=1)
    assert result.conflicts == []
    assert result.skipped_synthesis is True
    merged = {f["i"]: f for f in result.merged_fields}
    # Canonical form is the shared normalized value, not either raw string
    # verbatim -- this is what keeps the result independent of which chunk
    # is "first" (Req 5.7) when raw strings differ only in whitespace.
    assert merged[1]["v"] == "Alpha Beta"
    assert merged[1]["loc"] == ["S000001", "S000002"]
    assert merged[1]["c"] == "h"


# ---------------------------------------------------------------------------
# deterministic_merge: single provider (Req 5.5)
# ---------------------------------------------------------------------------


def test_single_provider_non_conflicting():
    chunk_results = [
        [{"i": 5, "v": "42", "loc": ["S000010"], "c": "m"}],
        [{"i": 5, "v": None, "loc": [], "c": "nr"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=5)
    assert 5 not in result.conflicts
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[5]["v"] == "42"
    assert merged[5]["loc"] == ["S000010"]
    assert merged[5]["c"] == "m"
    assert result.skipped_synthesis is True


# ---------------------------------------------------------------------------
# deterministic_merge: all null/empty -> "nr" (Req 5.2, 4.3)
# ---------------------------------------------------------------------------


def test_all_null_assigns_not_reported():
    chunk_results = [
        [{"i": 7, "v": None, "loc": [], "c": "nr"}],
        [{"i": 7, "v": "", "loc": [], "c": "nr"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=7)
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[7]["v"] == "nr"
    assert merged[7]["c"] == "nr"
    assert merged[7]["loc"] == []
    assert 7 not in result.conflicts


def test_field_absent_from_all_chunks_assigns_not_reported():
    chunk_results = [
        [{"i": 1, "v": "x", "loc": [], "c": "h"}],
        [{"i": 1, "v": "x", "loc": [], "c": "h"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=2)
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[2]["v"] == "nr"
    assert merged[2]["c"] == "nr"


# ---------------------------------------------------------------------------
# deterministic_merge: disagreement -> conflict (Req 5.6)
# ---------------------------------------------------------------------------


def test_disagreement_marks_conflict_and_excludes_from_merged_fields():
    chunk_results = [
        [{"i": 3, "v": "foo", "loc": ["S000001"], "c": "h"}],
        [{"i": 3, "v": "bar", "loc": ["S000002"], "c": "h"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=3)
    assert result.conflicts == [3]
    assert 3 not in {f["i"] for f in result.merged_fields}
    assert result.skipped_synthesis is False


def test_skipped_synthesis_true_only_when_no_conflicts():
    chunk_results = [
        [{"i": 1, "v": "same", "loc": [], "c": "h"}],
        [{"i": 1, "v": "same", "loc": [], "c": "h"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=1)
    assert result.skipped_synthesis is True

    conflicting = [
        [{"i": 1, "v": "a", "loc": [], "c": "h"}],
        [{"i": 1, "v": "b", "loc": [], "c": "h"}],
    ]
    result2 = deterministic_merge(conflicting, total_fields=1)
    assert result2.skipped_synthesis is False


# ---------------------------------------------------------------------------
# Evidence_ID dedup, sorted ascending (Req 5.3)
# ---------------------------------------------------------------------------


def test_evidence_id_dedup_sorted_ascending():
    chunk_results = [
        [{"i": 1, "v": "same", "loc": ["S000003", "S000001"], "c": "h"}],
        [{"i": 1, "v": "same", "loc": ["S000001", "S000002"], "c": "h"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=1)
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[1]["loc"] == ["S000001", "S000002", "S000003"]


# ---------------------------------------------------------------------------
# Confidence resolution h > m > l > nr (Req 5.4)
# ---------------------------------------------------------------------------


def test_confidence_resolution_selects_highest():
    chunk_results = [
        [{"i": 1, "v": "same", "loc": [], "c": "l"}],
        [{"i": 1, "v": "same", "loc": [], "c": "h"}],
        [{"i": 1, "v": "same", "loc": [], "c": "m"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=1)
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[1]["c"] == "h"


# ---------------------------------------------------------------------------
# Order independence (Req 5.7)
# ---------------------------------------------------------------------------


def test_order_independence_across_permutation():
    chunk_results = [
        [{"i": 1, "v": "Alpha  Beta", "loc": ["S000005"], "c": "m"}],
        [{"i": 1, "v": "Alpha Beta", "loc": ["S000001"], "c": "h"}],
        [{"i": 2, "v": "conflict-a", "loc": [], "c": "h"}],
    ]
    permuted = [chunk_results[2], chunk_results[0], chunk_results[1]]

    result_a = deterministic_merge(chunk_results, total_fields=2)
    result_b = deterministic_merge(permuted, total_fields=2)

    merged_a = {f["i"]: f for f in result_a.merged_fields}
    merged_b = {f["i"]: f for f in result_b.merged_fields}

    assert merged_a[1] == merged_b[1]
    assert result_a.conflicts == result_b.conflicts


def test_order_independence_swapping_normalized_equal_contributors():
    """Regression test for a critical order-independence bug (Req 5.7 /
    design.md Property 8): swapping the RELATIVE order of two chunks that
    agree post-normalization but differ in raw whitespace must not change
    which raw string is picked as the canonical value. Prior to the fix,
    `deterministic_merge` used `enumerate(chunk_results)` list position as
    "chunk identity" for a lowest-chunk-wins tie-break, so permuting these
    two chunks changed the merged 'v' from one raw string to the other.
    The fix outputs the normalized value (shared by all agreeing
    contributors) as canonical, which cannot depend on list position.
    """
    chunk_a_first = [
        [{"i": 1, "v": "Alpha  Beta", "loc": ["S000005"], "c": "m"}],  # double space
        [{"i": 1, "v": "Alpha Beta", "loc": ["S000001"], "c": "h"}],  # single space
    ]
    chunk_b_first = [
        [{"i": 1, "v": "Alpha Beta", "loc": ["S000001"], "c": "h"}],
        [{"i": 1, "v": "Alpha  Beta", "loc": ["S000005"], "c": "m"}],
    ]

    result_a_first = deterministic_merge(chunk_a_first, total_fields=1)
    result_b_first = deterministic_merge(chunk_b_first, total_fields=1)

    assert result_a_first.merged_fields == result_b_first.merged_fields
    merged = {f["i"]: f for f in result_a_first.merged_fields}
    assert merged[1]["v"] == "Alpha Beta"
    assert merged[1]["loc"] == ["S000001", "S000005"]
    assert merged[1]["c"] == "h"


def test_order_independence_across_all_permutations_of_three_chunks():
    """Property-8-style check with 3+ chunks: every permutation of three
    chunks -- two of which agree post-normalization on field 1 with
    different raw whitespace, plus an unrelated conflicting field 2 --
    must produce identical merged_fields and conflicts.
    """
    import itertools

    chunk_x = [
        {"i": 1, "v": "Foo   Bar", "loc": ["S000009"], "c": "l"},
        {"i": 2, "v": "one", "loc": ["S000010"], "c": "h"},
    ]
    chunk_y = [
        {"i": 1, "v": "Foo Bar", "loc": ["S000002"], "c": "h"},
    ]
    chunk_z = [
        {"i": 1, "v": "Foo Bar", "loc": ["S000004"], "c": "m"},
        {"i": 2, "v": "two", "loc": ["S000011"], "c": "h"},
    ]
    chunks = [chunk_x, chunk_y, chunk_z]

    results = [
        deterministic_merge(list(perm), total_fields=2)
        for perm in itertools.permutations(chunks)
    ]

    baseline_merged = {f["i"]: f for f in results[0].merged_fields}
    baseline_conflicts = results[0].conflicts
    for result in results[1:]:
        merged = {f["i"]: f for f in result.merged_fields}
        assert merged == baseline_merged
        assert result.conflicts == baseline_conflicts

    # Sanity: field 1 resolves deterministically (all agree post-normalization),
    # field 2 is a genuine conflict ("one" vs "two").
    assert baseline_merged[1]["v"] == "Foo Bar"
    assert baseline_merged[1]["loc"] == ["S000002", "S000004", "S000009"]
    assert baseline_merged[1]["c"] == "h"
    assert baseline_conflicts == [2]


def test_multi_field_end_to_end_over_total_fields_range():
    # total_fields=3; only fields 1 and 2 appear in any chunk -> field 3 is "nr".
    chunk_results = [
        [
            {"i": 1, "v": "X", "loc": ["S000001"], "c": "h"},
            {"i": 2, "v": "same value", "loc": ["S000002"], "c": "l"},
        ],
        [
            {"i": 2, "v": "same  value", "loc": ["S000003"], "c": "h"},
        ],
    ]
    result = deterministic_merge(chunk_results, total_fields=3)
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[1]["v"] == "X"
    assert merged[2]["v"] == "same value"
    assert merged[2]["c"] == "h"
    assert merged[2]["loc"] == ["S000002", "S000003"]
    assert merged[3]["v"] == "nr"
    assert result.skipped_synthesis is True
