"""
Property-based tests for agents/openai/prompts.py stability guarantees.

Feature: token-efficient-extraction
Validates: Requirements 2.1, 2.2, 2.3, 2.6, 3.1, 3.2, 3.3, 4.2, 6.2

This file is the canonical home (per design.md's "Testing Strategy" file
locations table) for Properties 3, 4, 5, 6, 7, 13, and 14. It is authored
for task 6.3, after task 6.1 (prompts.py + its builder/PBT tests, committed
at 5b910c9) and task 6.2 (evidence_index.py + its property tests, committed
at 47a9fe9). Read this docstring in full before adding or editing tests
below -- several properties are already fully covered elsewhere, and two are
not yet implementable; the notes below explain why, rather than silently
skipping or duplicating.

Property 3 -- implemented here (genuinely new angle)
------------------------------------------------------
`_shared_paper_prefix()` / `build_user_message()` / `build_cache_warmup_message()`
live in this package (`agents.openai.prompts`), so Property 3 (stable-prefix
byte-identity across chunk calls with different `chunk_fields`) is tested
directly below with fresh Hypothesis coverage. `test_prompts_pbt.py` (task
6.1) already has `test_shared_prefix_invariant`, which compares
`build_user_message()`'s leading slice against a *derived* call to
`_shared_paper_prefix()`. The tests below take the property's literal wording
("any two sets of chunk_fields") more directly: they construct two
independent `chunk_fields` lists and compare the two resulting messages'
leading segments directly against each other (and against
`build_cache_warmup_message()`'s output), which is a distinct comparison
shape from what task 6.1 already wrote.

Property 5 -- implemented here (genuinely new angle)
------------------------------------------------------
Field-definition ordering is also exercised in `test_prompts_pbt.py` (task
6.1), via string-parsing of the rendered JSON. The tests below instead (a)
exercise the private `_sorted_by_field_index()` helper directly -- the single
function both `build_user_message()` and `build_cache_warmup_message()`
delegate to -- with `json.loads()`-based (not string-split-based) assertions
on the rendered output, and (b) include duplicate `field_index` values to
confirm ties don't break the ascending-order guarantee.

Properties 4, 6, 7 -- NOT duplicated here (cross-package; already covered)
---------------------------------------------------------------------------
Properties 4 (evidence serialization sort stability), 6 (Evidence_ID
determinism), and 7 (evidence selection respects configured limits) all
describe the behavior of `build_paper_evidence_package()` and
`_build_items_from_tei()`, both defined in `src/pipeline/evidence_index.py`
-- not in `src/agents/openai/prompts.py`. design.md's file-location table
lists this file as the home for these properties too, but that table is a
plan-time approximation: task 6.2 already implemented and fully covered
these exact properties with dedicated, passing Hypothesis tests in
`tests/src/pipeline/test_pipeline_evidence_index.py` (committed at
47a9fe9):

  * Property 4  -> `test_build_paper_evidence_package_sorts_by_evidence_id_ascending`
                    and `test_build_paper_evidence_package_is_byte_identical_across_repeated_calls`
  * Property 6  -> `test_evidence_id_determinism_and_pattern_property`
                    (plus `test_evidence_id_pattern_covers_all_three_types`)
  * Property 7  -> `test_build_paper_evidence_package_respects_configured_limits_property`

Reading `tests/test_dependency_directions.py` in full confirms its
`_check_forbidden_imports()` walks only `PROJECT_ROOT / "src" / <package>`
-- it never scans `tests/`, so a `pipeline.evidence_index` import from a
test file under `tests/src/agents/openai/` would not trip that suite.
However, this repository's own testing convention (CLAUDE.md: "Test tree
mirrors `src/` under `tests/src/`") means a test physically located under
`tests/src/agents/openai/` that imports and re-tests `src/pipeline/`
production functions would misrepresent which package it belongs to, and
would duplicate ~170 already-passing lines of Hypothesis coverage for zero
additional confidence. Per this task's own guidance to avoid "silently
omitting" as well as "duplicating illegally," the choice made here is
transparent cross-reference over duplication: Properties 4, 6, and 7 are
NOT re-implemented in this file. `tests/src/pipeline/test_pipeline_evidence_index.py`
remains their one canonical, executable home.

Properties 13, 14 -- NOT implemented here (no production code yet; deferred)
-------------------------------------------------------------------------------
Property 13 (compact synthesis snippet truncation, 200 chars, word-boundary)
and Property 14 (Repair_Prompt strictly smaller than the original chunk
prompt) both describe behavior that `tasks.md` assigns to task 8.2
("Update `src/pipeline/pdf_processor.py` to integrate deterministic merge,
token budget, and repair telemetry"), which is UNCHECKED (`- [ ] 8.2`) as of
this writing and out of scope for this test-only task.

  * Property 13: `grep -rn "snippet\\|truncat" src/agents/ src/pipeline/`
    turns up no candidate-snippet formatting code anywhere. There is no
    compact synthesis-candidate builder (field_index, field_name, value,
    confidence, Evidence_IDs, <=200-char snippet) in the codebase yet --
    `pdf_processor.py::process_pdf()` currently sends the *entire*
    `prior_context` list to the synthesis chunk verbatim (see
    `extract_chunk(..., prior_context=prior_context, ...)`), with no
    snippet truncation step at all. There is nothing to test without
    inventing the missing logic, which this task's constraints forbid.

  * Property 14: `src/pipeline/pdf_processor.py` DOES already contain a
    `RepairRetryLoop._build_repair_prompt()` method, but (a) `git log
    --follow` shows it predates this spec entirely (present since the
    "audit-remediation spec implementation" commit and earlier, i.e. it is
    a different, older validation-retry feature, not an artifact of this
    spec's task 8.2), (b) it does not yet include the "invalid output
    fragment" that Requirement 6.1 requires a Repair_Prompt to carry, and
    (c) nowhere does it (or any caller) compute/compare chars/4 token
    estimates against the original chunk prompt, which is the entire
    substance of Property 14 ("the constructed Repair_Prompt's estimated
    token count ... SHALL be strictly less than the original chunk
    prompt's"). Testing today's `_build_repair_prompt()` against Property
    14 would only demonstrate a fact about a pre-existing, differently
    shaped, soon-to-be-replaced implementation -- not validate the
    Requirement-6.2-conformant Repair_Prompt that task 8.2 has yet to
    build. It also lives in `src/pipeline/`, so testing it here would
    repeat the same cross-package concern documented above for Properties
    4/6/7. This task's constraints ("do NOT fabricate snippet-truncation or
    repair-prompt logic") are honored by deferring rather than inventing.

Both are left as documented deferrals below (no test functions), matching
the precedent already set by `tests/src/pipeline/test_deterministic_merge_properties.py`'s
handling of Property 12 and `tests/src/pipeline/test_token_budget_properties.py`'s
handling of Property 21's scope note.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Load agents.openai.prompts directly from its file path so the test works
# regardless of how pytest resolves sys.path (--import-mode=importlib). This
# mirrors the loading pattern used in test_prompts_builders.py / test_prompts_pbt.py.
# ---------------------------------------------------------------------------
_AGENTS_ROOT = Path(__file__).resolve().parents[4] / "src"

if "agents" not in sys.modules or not hasattr(sys.modules["agents"], "agent_schema_validator"):
    import importlib as _importlib
    _agents_spec = _importlib.util.spec_from_file_location(
        "agents",
        _AGENTS_ROOT / "agents" / "__init__.py",
        submodule_search_locations=[str(_AGENTS_ROOT / "agents")],
    )
    assert _agents_spec is not None and _agents_spec.loader is not None
    _agents_mod = _importlib.util.module_from_spec(_agents_spec)
    sys.modules["agents"] = _agents_mod
    _agents_spec.loader.exec_module(_agents_mod)

_PROMPTS_PATH = _AGENTS_ROOT / "agents" / "openai" / "prompts.py"
_SPEC = importlib.util.spec_from_file_location("agents.openai.prompts", _PROMPTS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_PROMPTS_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["agents.openai.prompts"] = _PROMPTS_MODULE
_SPEC.loader.exec_module(_PROMPTS_MODULE)

_shared_paper_prefix = _PROMPTS_MODULE._shared_paper_prefix
_sorted_by_field_index = _PROMPTS_MODULE._sorted_by_field_index
build_user_message = _PROMPTS_MODULE.build_user_message
build_cache_warmup_message = _PROMPTS_MODULE.build_cache_warmup_message

# ---------------------------------------------------------------------------
# Shared Hypothesis strategies
# ---------------------------------------------------------------------------

st_source_package = st.text(min_size=1, max_size=500)


def _field_strategy(index_min: int = 1, index_max: int = 62) -> st.SearchStrategy:
    return st.fixed_dictionaries({
        "field_index": st.integers(min_value=index_min, max_value=index_max),
        "field_name": st.text(min_size=1, max_size=50),
        "definition": st.text(max_size=100),
    })


st_chunk_fields = st.lists(_field_strategy(), min_size=1, max_size=10)

# Two independent field-set strategies for the "any two sets of chunk_fields"
# wording in Property 3 -- deliberately drawn from disjoint field_index
# ranges so the two lists are very unlikely to be shaped identically.
st_chunk_fields_a = st.lists(_field_strategy(1, 30), min_size=1, max_size=8)
st_chunk_fields_b = st.lists(_field_strategy(31, 62), min_size=1, max_size=8)


# ---------------------------------------------------------------------------
# Property 3: Stable prefix byte-identity across chunk calls
#
# Feature: token-efficient-extraction, Property 3: For any paper-level
# evidence package and any two sets of chunk_fields, the Stable_Prefix
# portion of the constructed prompts SHALL be byte-identical when encoded as
# UTF-8.
# Validates: Requirements 2.1, 2.2, 2.6, 3.3
# ---------------------------------------------------------------------------


@given(st_source_package, st_chunk_fields_a, st_chunk_fields_b)
@settings(max_examples=100)
def test_property_3_stable_prefix_byte_identical_across_two_chunk_field_sets(
    source_package: str,
    chunk_fields_a: list,
    chunk_fields_b: list,
) -> None:
    """For the same source_package, two build_user_message() calls with
    independently-drawn chunk_fields lists SHALL share a byte-identical
    leading Stable_Prefix segment -- compared directly against each other,
    not merely each against a derived helper call.
    """
    expected_prefix = _shared_paper_prefix(source_package)
    prefix_len = len(expected_prefix)

    message_a = build_user_message(source_package, chunk_fields_a)
    message_b = build_user_message(source_package, chunk_fields_b)

    assert message_a[:prefix_len].encode("utf-8") == expected_prefix.encode("utf-8")
    assert message_b[:prefix_len].encode("utf-8") == expected_prefix.encode("utf-8")
    assert message_a[:prefix_len].encode("utf-8") == message_b[:prefix_len].encode("utf-8"), (
        "Stable_Prefix differs between two build_user_message() calls with "
        "different chunk_fields for the same source_package."
    )


@given(st_source_package, st_chunk_fields)
@settings(max_examples=100)
def test_property_3_stable_prefix_byte_identical_between_warmup_and_chunk_call(
    source_package: str,
    chunk_fields: list,
) -> None:
    """The Stable_Prefix produced for a cache-warmup call (no chunk_fields)
    SHALL be byte-identical to the Stable_Prefix produced for a real
    build_user_message() call for the same source_package, since both are
    seeded from the same paper-level evidence package and must land on the
    same server-side prompt-cache prefix.
    """
    expected_prefix = _shared_paper_prefix(source_package)
    prefix_len = len(expected_prefix)

    warmup_message = build_cache_warmup_message(source_package)
    chunk_message = build_user_message(source_package, chunk_fields)

    assert warmup_message[:prefix_len].encode("utf-8") == expected_prefix.encode("utf-8")
    assert chunk_message[:prefix_len].encode("utf-8") == expected_prefix.encode("utf-8")
    assert warmup_message[:prefix_len].encode("utf-8") == chunk_message[:prefix_len].encode("utf-8")


# ---------------------------------------------------------------------------
# Property 5: Field definitions ordered by field_index
#
# Feature: token-efficient-extraction, Property 5: For any list of field
# definitions included in a prompt, they SHALL appear in ascending numeric
# order of field_index.
# Validates: Requirements 2.3
# ---------------------------------------------------------------------------


@given(st.lists(_field_strategy(), min_size=0, max_size=15))
@settings(max_examples=100)
def test_property_5_sorted_by_field_index_direct(chunk_fields: list) -> None:
    """`_sorted_by_field_index()` -- the single helper both message builders
    delegate to -- SHALL always return its input sorted ascending by
    field_index, regardless of input order, and SHALL NOT mutate the
    caller's list.
    """
    original = list(chunk_fields)
    result = _sorted_by_field_index(chunk_fields)

    indices = [f["field_index"] for f in result]
    assert indices == sorted(indices)
    assert len(result) == len(chunk_fields)
    # No mutation of caller's list (order or identity of elements).
    assert chunk_fields == original


@given(
    st.lists(_field_strategy(1, 10), min_size=2, max_size=12),
)
@settings(max_examples=100)
def test_property_5_sorted_by_field_index_stable_under_duplicates(chunk_fields: list) -> None:
    """Duplicate field_index values (a valid input shape -- extraction_map.json
    fields are not guaranteed unique across arbitrary chunk_fields slices)
    SHALL NOT break the ascending-order guarantee: ties are simply adjacent
    in the output.
    """
    result = _sorted_by_field_index(chunk_fields)
    indices = [f["field_index"] for f in result]
    assert indices == sorted(indices)


@given(st_source_package, st.lists(_field_strategy(), min_size=1, max_size=15))
@settings(max_examples=100)
def test_property_5_field_definitions_ordered_in_rendered_cache_warmup_message(
    source_package: str,
    chunk_fields: list,
) -> None:
    """The canonical-home copy of the field-index-ordering check, verified
    via json.loads() of the rendered EXTRACTION MAP block (robust to
    reformatting) rather than line-based string parsing.
    """
    message = build_cache_warmup_message(source_package, chunk_fields=chunk_fields)

    map_start = message.index("EXTRACTION MAP")
    map_start = message.index("[", map_start)
    map_end = message.index("]\n", map_start) + 1
    rendered_fields = json.loads(message[map_start:map_end])

    indices = [f["field_index"] for f in rendered_fields]
    assert indices == sorted(indices), (
        f"field_index values are not ascending in build_cache_warmup_message output: {indices}"
    )
