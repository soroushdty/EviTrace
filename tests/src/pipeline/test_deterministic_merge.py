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

Task 2.3 note (read before editing the section at the bottom of this file):
task 2.3's checklist bullets are worded as if they all describe
`deterministic_merge()` behavior, but reading `src/pipeline/
deterministic_merge.py` in full shows several of them actually describe the
SYNTHESIS PROMPT CONSTRUCTION step (task 8.2, `pdf_processor.py`
integration, not yet implemented) rather than this module. This mirrors the
precedent already established in `test_deterministic_merge_properties.py`
(task 2.2) for Property 12. Per-bullet disposition:

- "Zero-candidate fields get 'nr' confidence" (Req 4.5, 5.2): REAL, tested
  here directly against `deterministic_merge()` -- Req 5.2 is exactly this
  behavior.
- "Synthesis output schema conformance with compact keys" (Req 4.6): the
  synthesis model's own output schema doesn't exist as code yet (task 8.2).
  `deterministic_merge()`'s `merged_fields` already uses the identical
  {i, v, loc, c} compact-key schema for its non-conflicting fields, so that
  is tested here as the closest current, real analog.
- "Synthesis prompt excludes full evidence" (Req 4.1): DEFERRED. No prompt
  construction code exists in this module to exercise; belongs to task 8.2.
- "Single-candidate with no conflict skips synthesis" (Req 4.3): REAL,
  tested here -- Req 5.5's single-provider rule plus `skipped_synthesis`
  cover exactly this case today.
- "Max 5 candidates per conflicting field" (Req 4.7): DEFERRED, same
  treatment as task 2.2's Property 12 -- `deterministic_merge()` has no
  per-field candidate list or truncation logic (`MergeResult.conflicts` is
  only a list of field indices, not candidate values), so the 5-candidate
  cap cannot be tested here. What IS tested is the precondition it will
  need: a conflicting field with more than 5 disagreeing contributors is
  still recorded as a single conflict entry, not silently
  dropped/fragmented, by the module as it exists today.
"""
from __future__ import annotations

from pipeline.deterministic_merge import (
    NOT_REPORTED_CONFIDENCE,
    NOT_REPORTED_VALUE,
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


# ---------------------------------------------------------------------------
# Task 2.3
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Zero-candidate fields get "nr" confidence (Req 4.5, 5.2) -- REAL
# ---------------------------------------------------------------------------


def test_zero_candidates_across_multiple_chunks_assigns_nr_confidence():
    """Req 4.5 ("IF a field has zero candidates that passed chunk-level
    validation, THEN THE Pipeline SHALL record the field with a
    not-reported value and confidence 'nr' without invoking synthesis") and
    Req 5.2 (the identical rule stated for Deterministic_Merge). A field
    with zero non-empty candidates -- mixing None, empty string, and total
    absence across three chunks -- must resolve to "nr"/"nr" and must not
    be a conflict or require synthesis.
    """
    chunk_results = [
        [{"i": 9, "v": None, "loc": [], "c": "nr"}],
        [{"i": 9, "v": "", "loc": [], "c": "nr"}],
        [],  # chunk omits field 9 entirely
    ]
    result = deterministic_merge(chunk_results, total_fields=9)
    merged = {f["i"]: f for f in result.merged_fields}

    assert merged[9]["v"] == NOT_REPORTED_VALUE
    assert merged[9]["c"] == NOT_REPORTED_CONFIDENCE
    assert merged[9]["loc"] == []
    assert 9 not in result.conflicts


def test_zero_candidates_field_not_present_in_any_chunk_at_all():
    """A field_index that no chunk mentions at all (not even with a null
    value) is the purest "zero candidates" case for Req 4.5/5.2: still
    resolves to not-reported without being flagged as needing synthesis.
    """
    chunk_results = [
        [{"i": 1, "v": "only field 1", "loc": [], "c": "h"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=4)
    merged = {f["i"]: f for f in result.merged_fields}

    for absent_index in (2, 3, 4):
        assert merged[absent_index]["v"] == NOT_REPORTED_VALUE
        assert merged[absent_index]["c"] == NOT_REPORTED_CONFIDENCE
        assert absent_index not in result.conflicts
    assert result.skipped_synthesis is True


# ---------------------------------------------------------------------------
# merged_fields conforms to the compact-key schema {i, v, loc, c}
# (Req 4.6 -- closest current analog; the synthesis model's own output
# schema is task 8.2, not yet implemented; see module docstring)
# ---------------------------------------------------------------------------


def test_merged_fields_conform_to_compact_key_schema():
    """Req 4.6 requires synthesis output to conform "to the existing final
    extraction JSON schema (compact keys: i, v, loc, c) with all required
    keys preserved." No synthesis-output-schema code exists yet (task 8.2),
    but `deterministic_merge()`'s own `merged_fields` already produces
    entries in this exact compact-key schema for every field it resolves
    without synthesis -- this test locks that down as the current, real
    analog: every entry has exactly the keys {i, v, loc, c}, with the
    documented types, and no extra/missing keys.
    """
    chunk_results = [
        [
            {"i": 1, "v": "agreed", "loc": ["S000001"], "c": "h"},
            {"i": 2, "v": None, "loc": [], "c": "nr"},
        ],
        [
            {"i": 1, "v": "agreed", "loc": ["S000002"], "c": "m"},
        ],
        # field 3 provided by no chunk at all -> "nr" via absence.
    ]
    result = deterministic_merge(chunk_results, total_fields=3)

    # field 3 (a conflict-free "nr" field) is included; nothing here should
    # be a conflict, since every field_index in 1..3 resolves deterministically.
    assert result.conflicts == []
    assert len(result.merged_fields) == 3

    for entry in result.merged_fields:
        assert set(entry.keys()) == {"i", "v", "loc", "c"}
        assert isinstance(entry["i"], int)
        assert entry["v"] is None or isinstance(entry["v"], str)
        assert isinstance(entry["loc"], list)
        assert all(isinstance(x, str) for x in entry["loc"])
        assert isinstance(entry["c"], str)
        assert entry["c"] in {"h", "m", "l", "nr"}


# ---------------------------------------------------------------------------
# Synthesis prompt excludes full evidence (Req 4.1) -- DEFERRED
#
# Req 4.1: "WHEN synthesis is invoked, THE Pipeline SHALL NOT include full
# prior chunk prompt text or full evidence packages in the synthesis
# prompt." This is a property of PROMPT CONSTRUCTION for the synthesis LLM
# call, which is implemented in task 8.2 (pdf_processor.py integration),
# not in `src/pipeline/deterministic_merge.py`. Reading deterministic_merge.py
# in full (module docstring, `deterministic_merge()`, `MergeResult`)
# confirms there is no prompt-building code here at all -- the module never
# constructs, receives, or references an LLM prompt string or an evidence
# package; it only merges already-parsed compact-format field dicts. There
# is therefore no real behavior in this module to exercise for Req 4.1, and
# no test is added for it here (mirrors the deferral precedent set by task
# 2.2 for Property 12 in test_deterministic_merge_properties.py). Task 8.2
# owns this behavior and should add the corresponding test alongside its
# synthesis prompt builder.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Single-candidate with no conflict skips synthesis (Req 4.3) -- REAL
# ---------------------------------------------------------------------------


def test_single_candidate_field_is_not_a_conflict_and_skips_synthesis():
    """Req 4.3: "WHEN a field has a single candidate that passed
    chunk-level validation with no Conflict, THE Pipeline SHALL skip LLM
    synthesis for that field and use the candidate value directly." At the
    deterministic_merge() level this is Req 5.5's single-provider rule: a
    field with exactly one chunk contributing a non-empty value, and no
    other field in the batch conflicting, must not appear in `conflicts`
    and must produce `skipped_synthesis = True` for the whole merge.
    """
    chunk_results = [
        [{"i": 1, "v": "sole candidate", "loc": ["S000007"], "c": "m"}],
    ]
    result = deterministic_merge(chunk_results, total_fields=1)

    assert 1 not in result.conflicts
    assert result.conflicts == []
    assert result.skipped_synthesis is True
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[1]["v"] == "sole candidate"
    assert merged[1]["loc"] == ["S000007"]
    assert merged[1]["c"] == "m"


def test_single_candidate_field_among_other_non_conflicting_fields_skips_synthesis():
    """Extends Req 4.3 to a multi-field batch: a single-candidate field
    alongside other fields that are also individually non-conflicting
    (all-agree, or all-null "nr") must still leave `conflicts` empty and
    `skipped_synthesis` True for the whole batch -- the single-candidate
    field is not, by itself, treated as needing synthesis.
    """
    chunk_results = [
        [
            {"i": 1, "v": "only candidate", "loc": ["S000001"], "c": "h"},
            {"i": 2, "v": "same", "loc": ["S000002"], "c": "h"},
        ],
        [
            {"i": 2, "v": "same", "loc": ["S000003"], "c": "l"},
            {"i": 3, "v": None, "loc": [], "c": "nr"},
        ],
    ]
    result = deterministic_merge(chunk_results, total_fields=3)

    assert result.conflicts == []
    assert result.skipped_synthesis is True
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[1]["v"] == "only candidate"
    assert merged[3]["v"] == NOT_REPORTED_VALUE


def test_single_candidate_field_does_not_prevent_other_fields_conflicting():
    """A single-candidate field (Req 4.3/5.5) resolves independently of a
    genuinely conflicting field elsewhere in the same batch: the
    non-conflicting field must not appear in `conflicts`, while the
    conflicting field must, and `skipped_synthesis` must be False overall
    (since not every field resolved deterministically).
    """
    chunk_results = [
        [
            {"i": 1, "v": "only candidate", "loc": ["S000001"], "c": "h"},
            {"i": 2, "v": "value-a", "loc": [], "c": "h"},
        ],
        [
            {"i": 2, "v": "value-b", "loc": [], "c": "h"},
        ],
    ]
    result = deterministic_merge(chunk_results, total_fields=2)

    assert 1 not in result.conflicts
    assert 2 in result.conflicts
    assert result.skipped_synthesis is False
    merged = {f["i"]: f for f in result.merged_fields}
    assert merged[1]["v"] == "only candidate"
    assert 2 not in merged


# ---------------------------------------------------------------------------
# Max 5 candidates per conflicting field (Req 4.7) -- DEFERRED, with a
# testable precondition (same treatment as task 2.2's Property 12)
#
# Req 4.7: "THE Pipeline SHALL limit the number of candidates sent to
# synthesis per conflicting field to a maximum of 5 candidates, selecting
# those with the highest confidence labels when more exist." Reading
# deterministic_merge.py in full confirms `MergeResult.conflicts` is only
# `list[int]` (field indices) -- there is no per-field candidate list, no
# confidence-based ranking/selection, and no truncation logic anywhere in
# this module. The actual 5-candidate cap belongs to the synthesis prompt
# builder (task 8.2), which does not exist yet, so no test can genuinely
# exercise the cap here. What CAN be verified now -- and is a precondition
# any future candidate-limiting step depends on -- is that a conflicting
# field with more than 5 disagreeing contributors is still correctly
# recorded as exactly one conflict entry by today's code, rather than being
# silently dropped, fragmented, or truncated by this module.
# ---------------------------------------------------------------------------


def test_conflicting_field_with_more_than_five_candidates_is_a_single_conflict_entry():
    """Precondition for Req 4.7 (see deferral note above): six chunks each
    contribute a distinct non-empty value for the same field (i.e. more
    "candidates" than the future 5-candidate synthesis cap). Today,
    `deterministic_merge()` has no candidate-list/limiting concept -- it
    must still record this as exactly one conflict for field_index 6,
    appearing exactly once in `conflicts` and excluded from
    `merged_fields`, with no partial/limited candidate data leaked out.
    """
    chunk_results = [
        [{"i": 6, "v": f"candidate-{n}", "loc": [f"S00000{n}"], "c": "h"}]
        for n in range(1, 7)  # 6 distinct candidates, one per chunk
    ]
    result = deterministic_merge(chunk_results, total_fields=6)

    assert result.conflicts == [6]
    assert result.conflicts.count(6) == 1
    assert 6 not in {f["i"] for f in result.merged_fields}
    assert result.skipped_synthesis is False
