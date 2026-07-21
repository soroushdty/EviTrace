"""
Property-based tests for pipeline/deterministic_merge.py (Properties 8-12).

Feature: token-efficient-extraction
Validates: Requirements 5.7, 5.1, 5.5, 5.6, 4.3, 5.3, 5.4, 4.7

This file provides Hypothesis property coverage complementary to the
example-based unit tests in test_deterministic_merge.py (task 2.1), per
design.md's "Correctness Properties" 8-12 and the "Property-Based Testing"
testing-strategy section.

Property 12 note (read before editing): design.md's Property 12
("Synthesis candidate limiting" -- cap conflicting-field candidates sent to
synthesis at 5, highest-confidence first) describes behavior of the
synthesis PROMPT BUILDER, which is implemented in a later task (8.2,
pdf_processor.py integration). `deterministic_merge()` does not build or
limit any candidate list -- its `MergeResult` only carries `conflicts:
list[int]` (which fields need synthesis), not per-field candidate values.
Reading src/pipeline/deterministic_merge.py in full confirms there is no
candidate-selection/truncation logic here to test. Per the task's guidance,
this file does NOT invent such logic; instead
`test_property_12_conflicts_precisely_identify_disagreeing_fields` locks
down the precondition a correct future candidate-limiting step depends on:
that `conflicts` identifies exactly (no more, no fewer) the fields with
genuine post-normalization disagreement. The 5-candidate/highest-confidence
selection itself remains untested here and is deferred to task 8.2.
"""
from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from pipeline.deterministic_merge import deterministic_merge, normalize_value

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Confidence ranking mirrors design.md's stated ordering (h > m > l > nr)
# independently of deterministic_merge's private _CONFIDENCE_RANK -- so a
# regression to the internal ranking table is still caught by comparing
# against this independently-declared expectation.
_CONFIDENCE_LABELS = ("h", "m", "l", "nr")
_CONFIDENCE_RANK_FOR_TEST = {"h": 3, "m": 2, "l": 1, "nr": 0}

_confidence_strategy = st.sampled_from(_CONFIDENCE_LABELS)

_evidence_id_strategy = st.builds(
    lambda prefix, n: f"{prefix}{n:06d}",
    st.sampled_from("STF"),
    st.integers(min_value=0, max_value=999999),
)

_loc_strategy = st.lists(_evidence_id_strategy, min_size=0, max_size=5)

_WORD_ALPHABET = string.ascii_letters + string.digits
_word_strategy = st.text(alphabet=_WORD_ALPHABET, min_size=1, max_size=6)
_whitespace_run_strategy = st.sampled_from([" ", "  ", "\t", " \t ", "\n", "   "])
_edge_whitespace_strategy = st.sampled_from(["", " ", "\t", "  ", "\n"])


@st.composite
def _agreeing_raw_variants(draw, n: int):
    """Draw ``n`` raw string variants that all normalize (via
    normalize_value) to the same canonical value, plus that canonical value.

    Each variant joins the same word sequence with a (possibly multi-char)
    whitespace run and adds optional leading/trailing whitespace -- exactly
    the "string-equivalent after whitespace normalization" relationship
    Requirement 5.1 / 5.7 describe.
    """
    words = draw(st.lists(_word_strategy, min_size=1, max_size=4))
    canonical = " ".join(words)
    variants = []
    for _ in range(n):
        sep = draw(_whitespace_run_strategy)
        leading = draw(_edge_whitespace_strategy)
        trailing = draw(_edge_whitespace_strategy)
        variants.append(leading + sep.join(words) + trailing)
    return canonical, variants


# General-purpose entry/chunk_results strategies for the confluence (order
# independence) and conflict-identification properties: arbitrary values,
# not constructed to guarantee agreement or conflict either way.
_TOTAL_FIELDS_GENERAL = 4

_general_entry_strategy = st.fixed_dictionaries(
    {
        "i": st.integers(min_value=1, max_value=_TOTAL_FIELDS_GENERAL),
        "v": st.one_of(st.none(), st.text(alphabet=_WORD_ALPHABET + " \t\n", min_size=0, max_size=8)),
        "loc": _loc_strategy,
        "c": _confidence_strategy,
    }
)
_general_chunk_strategy = st.lists(_general_entry_strategy, min_size=0, max_size=4)
_general_chunk_results_strategy = st.lists(_general_chunk_strategy, min_size=1, max_size=5)


@st.composite
def _chunk_results_with_permutation(draw):
    """Draw a chunk_results list plus one permutation of its chunk order.

    Field 1 is always forced to have >=2 contributors that agree
    post-normalization but differ in raw whitespace (the historically
    order-sensitive case -- see
    test_order_independence_swapping_normalized_equal_contributors in
    test_deterministic_merge.py). Random text across independently-drawn
    entries essentially never coincides by chance, so without this forced
    case the permutation check below would almost always exercise only the
    (trivially order-independent) "genuine conflict" and "absent" paths and
    could miss a regression in canonical-value selection.
    Fields 2..total_fields are filled with arbitrary "noise" entries
    (independently random per chunk: value, absence, agreement, or
    conflict) to keep the input realistic.
    """
    n_chunks = draw(st.integers(min_value=2, max_value=5))
    chunks: list[list[dict]] = [[] for _ in range(n_chunks)]

    n_agree_providers = draw(st.integers(min_value=2, max_value=n_chunks))
    _canonical, variants = draw(_agreeing_raw_variants(n_agree_providers))
    provider_positions = draw(st.permutations(list(range(n_chunks))))[:n_agree_providers]
    for slot, chunk_idx in enumerate(provider_positions):
        chunks[chunk_idx].append(
            {
                "i": 1,
                "v": variants[slot],
                "loc": draw(_loc_strategy),
                "c": draw(_confidence_strategy),
            }
        )

    for field_index in range(2, _TOTAL_FIELDS_GENERAL + 1):
        for chunk_idx in range(n_chunks):
            if draw(st.booleans()):
                chunks[chunk_idx].append(
                    {
                        "i": field_index,
                        "v": draw(
                            st.one_of(
                                st.none(),
                                st.text(alphabet=_WORD_ALPHABET + " \t\n", min_size=0, max_size=8),
                            )
                        ),
                        "loc": draw(_loc_strategy),
                        "c": draw(_confidence_strategy),
                    }
                )

    perm_indices = draw(st.permutations(list(range(n_chunks))))
    permuted = [chunks[i] for i in perm_indices]
    return chunks, permuted


# ---------------------------------------------------------------------------
# Property 8: Deterministic merge is order-independent (confluence)
#
# Feature: token-efficient-extraction, Property 8: For any set of chunk
# results, applying `deterministic_merge` with any permutation of chunk
# order SHALL produce identical `MergeResult` output (same `merged_fields`,
# same `conflicts`).
# Validates: Requirements 5.7
# ---------------------------------------------------------------------------


@given(pair=_chunk_results_with_permutation())
@settings(max_examples=100)
def test_property_8_merge_is_order_independent(pair):
    chunk_results, permuted = pair

    baseline = deterministic_merge(chunk_results, total_fields=_TOTAL_FIELDS_GENERAL)
    result = deterministic_merge(permuted, total_fields=_TOTAL_FIELDS_GENERAL)

    assert result.merged_fields == baseline.merged_fields
    assert result.conflicts == baseline.conflicts
    assert result.skipped_synthesis == baseline.skipped_synthesis


# ---------------------------------------------------------------------------
# Property 9: Non-conflicting fields merge without LLM
#
# Feature: token-efficient-extraction, Property 9: For any set of chunk
# results where every field either has string-equivalent values (after
# whitespace normalization) across all providing chunks, or is provided by
# only a subset of chunks with no disagreement, `deterministic_merge` SHALL
# produce a `MergeResult` with an empty `conflicts` list and
# `skipped_synthesis = True`.
# Validates: Requirements 5.1, 5.5, 5.6, 4.3
# ---------------------------------------------------------------------------

_N_CHUNKS_FOR_PROPERTY_9 = 3
_TOTAL_FIELDS_FOR_PROPERTY_9 = 4

# "No value" representations that deterministic_merge treats identically:
# an absent entry (None -> omitted below), or an explicit null/empty value.
_absent_slot_strategy = st.sampled_from([None, {"v": None}, {"v": ""}])


@st.composite
def _non_conflicting_field_slots(draw):
    """Draw per-chunk slots (length _N_CHUNKS_FOR_PROPERTY_9) for ONE field
    such that the field is guaranteed non-conflicting: all-agree (Req 5.1),
    single-provider (Req 5.5), or all-absent (Req 5.2/4.3).

    Each slot is either None (field omitted from that chunk entirely) or a
    dict with "v"/"loc"/"c" describing that chunk's contribution.
    """
    n = _N_CHUNKS_FOR_PROPERTY_9
    scenario = draw(st.sampled_from(["all_agree", "single_provider", "all_absent"]))

    if scenario == "all_absent":
        return [draw(_absent_slot_strategy) for _ in range(n)]

    if scenario == "single_provider":
        _canonical, variants = draw(_agreeing_raw_variants(1))
        loc = draw(_loc_strategy)
        conf = draw(_confidence_strategy)
        provider = draw(st.integers(min_value=0, max_value=n - 1))
        slots = []
        for i in range(n):
            if i == provider:
                slots.append({"v": variants[0], "loc": loc, "c": conf})
            else:
                slots.append(draw(_absent_slot_strategy))
        return slots

    # all_agree: every chunk provides a whitespace-only-differing variant of
    # the same canonical value.
    _canonical, variants = draw(_agreeing_raw_variants(n))
    return [
        {
            "v": variants[i],
            "loc": draw(_loc_strategy),
            "c": draw(_confidence_strategy),
        }
        for i in range(n)
    ]


@st.composite
def _all_non_conflicting_chunk_results(draw):
    n = _N_CHUNKS_FOR_PROPERTY_9
    chunks: list[list[dict]] = [[] for _ in range(n)]
    for field_index in range(1, _TOTAL_FIELDS_FOR_PROPERTY_9 + 1):
        slots = draw(_non_conflicting_field_slots())
        for chunk_idx, slot in enumerate(slots):
            if slot is None:
                continue
            chunks[chunk_idx].append(
                {
                    "i": field_index,
                    "v": slot.get("v"),
                    "loc": slot.get("loc", []),
                    "c": slot.get("c", "nr"),
                }
            )
    return chunks


@given(chunk_results=_all_non_conflicting_chunk_results())
@settings(max_examples=100)
def test_property_9_non_conflicting_fields_skip_llm(chunk_results):
    result = deterministic_merge(chunk_results, total_fields=_TOTAL_FIELDS_FOR_PROPERTY_9)

    assert result.conflicts == []
    assert result.skipped_synthesis is True
    # Every field_index in range is resolved (either a real value or "nr").
    assert {f["i"] for f in result.merged_fields} == set(
        range(1, _TOTAL_FIELDS_FOR_PROPERTY_9 + 1)
    )


# ---------------------------------------------------------------------------
# Property 10: Evidence_ID deduplication produces sorted unique union
#
# Feature: token-efficient-extraction, Property 10: For any set of `loc`
# lists across chunks for the same field, the merged `loc` SHALL be the
# sorted unique union of all Evidence_IDs in ascending lexicographic order.
# Validates: Requirements 5.3
# ---------------------------------------------------------------------------


@st.composite
def _agreeing_field_with_locs(draw, min_n=1, max_n=5):
    """Draw n agreeing raw variants (all normalize to one canonical value)
    plus one independently-drawn loc list and confidence label per variant,
    so the field is guaranteed non-conflicting for property 10/11 checks.
    """
    n = draw(st.integers(min_value=min_n, max_value=max_n))
    _canonical, variants = draw(_agreeing_raw_variants(n))
    loc_lists = [draw(_loc_strategy) for _ in range(n)]
    confs = [draw(_confidence_strategy) for _ in range(n)]
    return variants, loc_lists, confs


@given(program=_agreeing_field_with_locs(min_n=1, max_n=6))
@settings(max_examples=100)
def test_property_10_evidence_id_dedup_sorted_unique_union(program):
    variants, loc_lists, confs = program
    chunk_results = [
        [{"i": 1, "v": variants[i], "loc": loc_lists[i], "c": confs[i]}]
        for i in range(len(variants))
    ]

    result = deterministic_merge(chunk_results, total_fields=1)
    merged = {f["i"]: f for f in result.merged_fields}

    expected_loc = sorted({evidence_id for loc in loc_lists for evidence_id in loc})
    assert merged[1]["loc"] == expected_loc
    # Sanity: genuinely sorted and deduplicated (would still pass trivially
    # if expected_loc happened to already be sorted-unique; the explicit
    # sorted(set(...)) recomputation below guards against an implementation
    # that merely concatenates without dedup/sort).
    assert merged[1]["loc"] == sorted(set(merged[1]["loc"]))
    assert len(merged[1]["loc"]) == len(set(merged[1]["loc"]))


# ---------------------------------------------------------------------------
# Property 11: Confidence resolution selects highest label
#
# Feature: token-efficient-extraction, Property 11: For any set of
# confidence labels across chunks for a field with string-equivalent
# values, the merged confidence SHALL be the maximum according to the
# ordering `h > m > l > nr`.
# Validates: Requirements 5.4
# ---------------------------------------------------------------------------


@given(program=_agreeing_field_with_locs(min_n=1, max_n=6))
@settings(max_examples=100)
def test_property_11_confidence_resolution_selects_highest(program):
    variants, loc_lists, confs = program
    chunk_results = [
        [{"i": 1, "v": variants[i], "loc": loc_lists[i], "c": confs[i]}]
        for i in range(len(variants))
    ]

    result = deterministic_merge(chunk_results, total_fields=1)
    merged = {f["i"]: f for f in result.merged_fields}

    expected_confidence = max(confs, key=lambda c: _CONFIDENCE_RANK_FOR_TEST[c])
    assert merged[1]["c"] == expected_confidence
    # Sanity: the selected confidence must at least be present among the
    # inputs and at the top rank -- catches a mutation that always returns
    # a fixed label regardless of input.
    assert _CONFIDENCE_RANK_FOR_TEST[merged[1]["c"]] == max(
        _CONFIDENCE_RANK_FOR_TEST[c] for c in confs
    )


# ---------------------------------------------------------------------------
# Property 12: Synthesis candidate limiting -- DEFERRED (see module docstring)
#
# Feature: token-efficient-extraction, Property 12: For any conflicting
# field with more than 5 candidates, the synthesis input SHALL contain at
# most 5 candidates, and those 5 SHALL be the ones with the highest
# confidence labels.
# Validates: Requirements 4.7
#
# `deterministic_merge()` has no candidate list or selection/truncation
# logic (confirmed by reading src/pipeline/deterministic_merge.py in full:
# MergeResult carries only field_index-level `conflicts: list[int]`). The
# actual 5-candidate cap belongs to the synthesis prompt builder (task 8.2).
# What CAN be verified now, and must hold for that future step to be
# correct, is the precondition below: `conflicts` identifies exactly the
# fields with genuine post-normalization disagreement -- neither more
# (which would waste synthesis calls on non-conflicting fields) nor fewer
# (which would silently skip synthesis for a field that actually needs
# it, defeating any candidate-limiting logic built on top of `conflicts`).
# ---------------------------------------------------------------------------


@given(chunk_results=_general_chunk_results_strategy)
@settings(max_examples=100)
def test_property_12_conflicts_precisely_identify_disagreeing_fields(chunk_results):
    """Precondition test for Property 12 (see module docstring): this does
    NOT test candidate limiting (not implemented yet); it locks down that
    `conflicts` exactly matches the set of fields with 2+ distinct
    normalized non-empty values across chunks -- the data a future
    candidate-limiting step must be able to trust.
    """
    result = deterministic_merge(chunk_results, total_fields=_TOTAL_FIELDS_GENERAL)

    contributed: dict[int, list[str]] = {}
    for chunk in chunk_results:
        for entry in chunk:
            field_index = entry.get("i")
            if not isinstance(field_index, int):
                continue
            if not (1 <= field_index <= _TOTAL_FIELDS_GENERAL):
                continue
            raw_value = entry.get("v")
            if raw_value is not None and not isinstance(raw_value, str):
                continue
            normalized = normalize_value(raw_value)
            if normalized:
                contributed.setdefault(field_index, []).append(normalized)

    expected_conflicts = sorted(
        field_index
        for field_index, values in contributed.items()
        if len(set(values)) > 1
    )
    assert result.conflicts == expected_conflicts

    # Every non-conflicting, contributed field must actually have been
    # resolved to the single agreed-upon value (further reinforcing that
    # `conflicts` and `merged_fields` partition the field space correctly,
    # which any future candidate-limiting step built on top of this
    # function will rely on).
    merged = {f["i"]: f for f in result.merged_fields}
    for field_index, values in contributed.items():
        if len(set(values)) == 1 and field_index not in result.conflicts:
            assert merged[field_index]["v"] == values[0]
