"""
tests/src/pipeline/test_token_budget_properties.py
-----------------------------------------
Property-based tests for ``pipeline.token_budget`` (task 3.2), covering
design.md's Correctness Properties 15, 21, and 22.

Feature: token-efficient-extraction
Validates: Requirements 7.1, 9.4, 7.2

This file is the dedicated Hypothesis suite promised (but deferred) by
``tests/src/pipeline/test_token_budget.py``'s own module docstring, which
covers the example-based, acceptance-criteria-level behavior for
Requirement 7 (task 3.1).

Property 21 scope note (read before editing)
----------------------------------------------
design.md states Property 21 as: "For any evidence pruning operation, all
Evidence_IDs referenced by fields with confidence label 'h' SHALL remain
present in the pruned evidence set." ``token_budget.py``'s own module
docstring ("Scope note") and ``test_token_budget.py``'s module docstring
both document that this module receives only *flattened prompt text* (a
``dict[str, str]`` of named sections) -- never the structured Evidence_ID /
confidence-label data that a real evidence bundle carries. Its evidence
pruning (``_prune_evidence``) treats the "evidence" section as an opaque
string, splitting it into "items" on a ``"\\n\\n"`` convention it invents
for itself. It has no way to know which byte ranges of that string
correspond to which Evidence_ID, let alone which of those carry confidence
label "h" -- so it categorically cannot implement "preserve high-confidence
Evidence_ID references" as literally stated. Fabricating a test that
pretends otherwise (e.g. by faking confidence-labeled items in a way the
production code doesn't actually parse) would be dishonest coverage.

This mirrors the precedent set by:
  * task 2.2's ``test_deterministic_merge_properties.py`` Property 12 note
    (candidate-limiting logic lives in a not-yet-built prompt builder, so
    that file locks down the precondition the future step depends on
    instead of inventing the missing logic), and
  * task 3.1's own ``test_token_budget.py`` docstring, which explicitly
    defers Req 7.3 coverage for the same structural reason.

Given that, the two Property 21 tests below (`test_property_21_*`) instead
honestly characterize what the CURRENT flat-text ``_prune_evidence``
implementation actually and provably does:

  1. ``test_property_21_pruned_evidence_is_a_prefix_of_the_original`` --
     pruning only ever removes content from the END of the evidence text
     (item-count capping keeps the earliest items; trailing items are
     dropped one at a time keeping the earliest; character-level caps and
     last-resort truncation both keep a leading substring). The pruned
     evidence text is therefore *always* a prefix of the original. This is
     the necessary structural precondition for "earlier-listed items
     survive pruning over later ones" -- the closest honest analogue this
     module's flat-text pruning can offer to "preserve important
     references," given it has no concept of Evidence_ID or confidence at
     all.
  2. ``test_property_21_larger_budget_prunes_no_more_aggressively`` --
     pruning behavior is monotonic and predictable with respect to the
     budget: for a fixed input and config, a larger budget never results in
     *more* evidence being removed than a smaller budget would, and the
     smaller-budget result is itself always a prefix of the larger-budget
     result.

Full Property-21 semantics (mapping surviving text back to Evidence_IDs and
confidence labels) require structured evidence data that only exists once
real Evidence/field objects are threaded through in the task 8.2
``pdf_processor.py`` integration; that is where genuine "preserve
high-confidence Evidence_IDs" coverage belongs. See CONCERNS in this task's
status report for an explicit acknowledgment of this gap.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pipeline.token_budget import (
    DEFAULT_BUDGETS,
    TokenBudgetExceededError,
    _join_parts,
    _prune_evidence,
    apply_mitigation,
    estimate_tokens,
)

# ---------------------------------------------------------------------------
# Property 15: Token estimation is chars divided by 4
#
# design.md: "For any string input, estimate_tokens(text) SHALL return
# len(text) // 4."
# ---------------------------------------------------------------------------


# Feature: token-efficient-extraction, Property 15: For any string input,
# estimate_tokens(text) SHALL return len(text) // 4.
# Validates: Requirements 7.1
@given(text=st.text(max_size=3000))
@settings(max_examples=100)
def test_property_15_estimate_tokens_is_chars_floor_div_4(text):
    assert estimate_tokens(text) == len(text) // 4


# Feature: token-efficient-extraction, Property 15: For any string input,
# estimate_tokens(text) SHALL return len(text) // 4. This variant stresses
# multi-byte/non-ASCII characters specifically, since a buggy
# byte-length-based implementation (rather than Python len()/char-count
# based) would diverge from the spec on such input.
# Validates: Requirements 7.1
@given(
    text=st.text(
        alphabet=st.characters(min_codepoint=0x80, max_codepoint=0x1F600),
        max_size=800,
    )
)
@settings(max_examples=100)
def test_property_15_estimate_tokens_counts_characters_not_bytes(text):
    assert estimate_tokens(text) == len(text) // 4


# ---------------------------------------------------------------------------
# Property 22: Budget mitigation ordering
#
# design.md: "For any prompt exceeding its stage Token_Budget, mitigation
# SHALL be attempted in strict order: (a) evidence pruning, (b) request
# splitting, (c) rejection -- and the first strategy that brings the
# estimate within budget SHALL be used without attempting subsequent
# strategies."
#
# Each of the three tests below isolates one link in that ordering chain:
#   22a: when the budget already accommodates everything except evidence,
#        strategy (a) alone always succeeds -- (b)/(c) are never reached
#        (no split-required warning ever appears, and no exception is
#        raised).
#   22b: when the oversized section is NOT the evidence section (so
#        pruning is structurally a no-op), (a) cannot help, so mitigation
#        must always fall through to rejection (c).
#   22c: when the prompt already fits, no strategy is attempted at all --
#        the returned text is byte-identical to the unmitigated join and
#        no warnings are produced.
# ---------------------------------------------------------------------------


# Feature: token-efficient-extraction, Property 22: For any prompt
# exceeding its stage Token_Budget, mitigation SHALL be attempted in strict
# order: (a) evidence pruning, (b) request splitting, (c) rejection -- and
# the first strategy that brings the estimate within budget SHALL be used
# without attempting subsequent strategies. This test covers the case
# where evidence pruning alone is sufficient: (b)/(c) must never be
# reached.
# Validates: Requirements 7.2
@given(
    system_text=st.text(max_size=80),
    field_text=st.text(max_size=80),
    evidence_items=st.lists(st.text(max_size=40), min_size=0, max_size=12),
    slack=st.integers(min_value=0, max_value=30),
)
@settings(max_examples=100)
def test_property_22_a_pruning_alone_suffices_never_reaches_split_or_rejection(
    system_text, field_text, evidence_items, slack
):
    evidence_text = "\n\n".join(evidence_items)
    parts = {"system": system_text, "evidence": evidence_text, "field_definitions": field_text}

    # Budget covers everything except (possibly) the evidence section, plus
    # optional slack. Because _prune_evidence can always shrink the
    # evidence section all the way down to "" as a last resort, this budget
    # is *always* achievable through pruning alone -- proving (a) never
    # needs help from (b)/(c) in this regime.
    non_evidence_parts = {k: v for k, v in parts.items() if k != "evidence"}
    non_evidence_joined = _join_parts(non_evidence_parts)
    budget = estimate_tokens(non_evidence_joined) + slack

    text, warnings = apply_mitigation(parts, "extraction_chunk", budget=budget, config={})

    assert estimate_tokens(text) <= budget
    assert not any("split" in w.lower() for w in warnings)


# Feature: token-efficient-extraction, Property 22: ... the first strategy
# that brings the estimate within budget SHALL be used without attempting
# subsequent strategies. This test covers the case where the oversized
# section is NOT evidence, so strategy (a) is structurally powerless and
# mitigation must always fall through strategy (b)'s signal to (c)
# rejection.
# Validates: Requirements 7.2
@given(
    budget=st.integers(min_value=0, max_value=50),
    overage=st.integers(min_value=1, max_value=20),
    stage=st.sampled_from(sorted(DEFAULT_BUDGETS.keys())),
)
@settings(max_examples=100)
def test_property_22_b_pruning_powerless_always_falls_through_to_rejection(
    budget, overage, stage
):
    # No "evidence" key at all: _prune_evidence has nothing to prune, so
    # this oversized "system" section can never be brought within budget by
    # strategy (a). The overage guarantees estimate_tokens(text) is exactly
    # budget + overage (> budget), independent of budget's value.
    text_len = 4 * (budget + overage)
    text = "X" * text_len
    parts = {"system": text}

    with pytest.raises(TokenBudgetExceededError) as excinfo:
        apply_mitigation(parts, stage, budget=budget, config={})

    err = excinfo.value
    assert err.stage == stage
    assert err.budget == budget
    assert err.estimated == budget + overage
    assert err.estimated > err.budget
    # top_sections is computed from the (unmodified, since unprunable)
    # parts -- must still be ranked descending and capped at 3 (Req 7.4).
    assert len(err.top_sections) <= 3
    assert err.top_sections == sorted(err.top_sections, key=lambda t: t[1], reverse=True)


# Feature: token-efficient-extraction, Property 22: ... the first strategy
# that brings the estimate within budget SHALL be used without attempting
# subsequent strategies. This test covers the degenerate zeroth case: when
# the prompt already fits, NO strategy (not even (a)) is attempted -- the
# output is the verbatim join with no warnings.
# Validates: Requirements 7.2
@given(
    system_text=st.text(max_size=100),
    evidence_text=st.text(max_size=100),
    field_text=st.text(max_size=100),
    slack=st.integers(min_value=0, max_value=50),
)
@settings(max_examples=100)
def test_property_22_c_already_within_budget_no_strategy_attempted(
    system_text, evidence_text, field_text, slack
):
    parts = {"system": system_text, "evidence": evidence_text, "field_definitions": field_text}
    full_text = _join_parts(parts)
    budget = estimate_tokens(full_text) + slack

    text, warnings = apply_mitigation(parts, "extraction_chunk", budget=budget, config={})

    assert text == full_text
    assert warnings == []


# ---------------------------------------------------------------------------
# Property 21: Evidence pruning preserves high-confidence references
#
# See the module docstring's "Property 21 scope note" above for why the
# two tests below characterize the actual, provable, tail-truncating
# behavior of the current flat-text ``_prune_evidence`` implementation
# rather than asserting the full Evidence_ID/confidence-label semantics
# design.md states, which this module structurally cannot implement.
# ---------------------------------------------------------------------------


# Feature: token-efficient-extraction, Property 21: For any evidence
# pruning operation, all Evidence_IDs referenced by fields with confidence
# label "h" SHALL remain present in the pruned evidence set.
# HONEST SCOPE (see module docstring): this module has no Evidence_ID or
# confidence data to preserve by label. What IS provably true of the
# current implementation, and is the structural precondition any future
# confidence-aware pruning (task 8.2) would need to build on top of, is
# that pruning never removes content from the front of the evidence text
# -- only from the end. This test locks that invariant down.
# Validates: Requirements 9.4
@given(
    system_text=st.text(max_size=60),
    field_text=st.text(max_size=60),
    evidence_items=st.lists(st.text(max_size=30), min_size=0, max_size=15),
    max_items=st.one_of(st.none(), st.integers(min_value=1, max_value=10)),
    max_chars=st.one_of(st.none(), st.integers(min_value=1, max_value=200)),
    budget=st.integers(min_value=0, max_value=60),
)
@settings(max_examples=150)
def test_property_21_pruned_evidence_is_a_prefix_of_the_original(
    system_text, field_text, evidence_items, max_items, max_chars, budget
):
    evidence = "\n\n".join(evidence_items)
    parts = {"system": system_text, "evidence": evidence, "field_definitions": field_text}
    config: dict = {}
    if max_items is not None:
        config["max_evidence_items_per_chunk"] = max_items
    if max_chars is not None:
        config["max_evidence_chars_per_chunk"] = max_chars

    pruned_parts, pruned_flag = _prune_evidence(parts, budget, config)
    pruned_evidence = pruned_parts["evidence"]

    # Core honest invariant: whatever pruning happens, it always keeps a
    # leading substring of the original evidence text and never touches
    # the front.
    assert evidence.startswith(pruned_evidence)
    # The reported "did anything change" flag must agree with reality.
    assert pruned_flag == (pruned_evidence != evidence)
    # Non-evidence sections must never be touched by evidence pruning.
    assert pruned_parts["system"] == system_text
    assert pruned_parts["field_definitions"] == field_text


# Feature: token-efficient-extraction, Property 21: ... (see honest-scope
# note above). This test verifies pruning is monotonic and predictable
# with respect to the budget -- a stronger, and more honestly testable,
# stand-in than the untestable literal property text: a larger budget
# never causes MORE evidence to be discarded than a smaller budget would,
# for the same input and config, and the smaller-budget result is always a
# prefix of the larger-budget result (i.e. pruning decisions nest
# predictably as budget increases, rather than behaving erratically).
# Validates: Requirements 9.4
@given(
    system_text=st.text(max_size=60),
    field_text=st.text(max_size=60),
    evidence_items=st.lists(st.text(max_size=30), min_size=0, max_size=15),
    small_budget=st.integers(min_value=0, max_value=60),
    extra=st.integers(min_value=0, max_value=60),
)
@settings(max_examples=150)
def test_property_21_larger_budget_prunes_no_more_aggressively(
    system_text, field_text, evidence_items, small_budget, extra
):
    evidence = "\n\n".join(evidence_items)
    parts = {"system": system_text, "evidence": evidence, "field_definitions": field_text}
    config: dict = {}
    large_budget = small_budget + extra

    small_parts, _ = _prune_evidence(parts, small_budget, config)
    large_parts, _ = _prune_evidence(parts, large_budget, config)

    small_evidence = small_parts["evidence"]
    large_evidence = large_parts["evidence"]

    # A larger (or equal) budget must never result in a shorter pruned
    # evidence text than a smaller budget would, for the same input.
    assert len(large_evidence) >= len(small_evidence)
    # The smaller-budget result nests inside (is a prefix of) the
    # larger-budget result -- pruning decisions are predictable/monotonic,
    # not just individually prefix-preserving in isolation.
    assert large_evidence.startswith(small_evidence)
