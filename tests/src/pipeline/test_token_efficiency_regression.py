"""
tests/src/pipeline/test_token_efficiency_regression.py
-----------------------------------------
Fixture-based regression tests for Requirement 9 ("Token-Efficiency
Regression Tests") -- the final implementation task (10.1) of the
token-efficient-extraction spec.

Feature: token-efficient-extraction
Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6

Unlike the property-based suites elsewhere in this spec
(test_token_budget_properties.py, test_deterministic_merge_properties.py,
test_token_report_properties.py, test_prompts_stability_properties.py),
this file exercises realistic, fixture-driven scenarios built from the
REAL production config defaults (configs/config.yaml's
max_evidence_items_per_chunk / max_evidence_chars_per_chunk, configs/
extraction_map.json's 62 canonical fields, and the real num_chunks=5
domain_to_chunk mapping from src/utils/config_utils.py's
``_get_domain_to_chunk``) rather than Hypothesis-generated data, per
design.md's "Integration / Regression Tests" section and this task's
boundary (create ONLY this file; no production module may be modified).

Honesty note (read before editing) -- Req 9.3 and Req 9.4
-----------------------------------------------------------
Two of the six Requirement 9 criteria cannot be satisfied exactly as
literally worded against this codebase's REAL, current configuration, for
reasons already documented elsewhere in this spec (tasks.md
"Implementation Notes", task 8.2's discovery re: domain_to_chunk, and task
3.1/3.2's token_budget.py "Scope note" re: flat-text evidence pruning):

* Req 9.3 ("no synthesis call when all fields are non-conflicting"): this
  codebase's real ``_get_domain_to_chunk(5)`` mapping assigns every field
  index to exactly one chunk, so ``deterministic_merge()``'s ``conflicts``
  list is structurally always empty for extraction-chunk fields, AND the
  real synthesis chunk (domain 13, "reviewer assessment") always owns its
  own exclusive fields. So in the REAL 5-chunk production config, synthesis
  ALWAYS runs regardless of conflicts -- a literal "run the real pipeline,
  zero conflicts, assert zero synthesis calls" test would always fail
  here, not because the feature is broken, but because that scenario is
  unreachable in today's configuration. This file instead tests the
  artificial-but-explicitly-supported scenario pdf_processor.py's
  integration (task 8.2) was written to handle: the synthesis chunk owning
  NO fields of its own (see test_req_9_3 below), mirroring
  ``test_process_pdf_skips_synthesis_when_no_conflicts_and_no_exclusive_fields``
  in tests/src/pipeline/test_pdf_processor_helpers.py, but as an
  independent, fixture-based regression guard specific to this file (not a
  duplicate import of that test).

* Req 9.4 ("high-confidence Evidence_IDs remain present after evidence is
  pruned from a chunk prompt"): confirmed by direct investigation (see
  test_req_9_4 below) that pdf_processor.py's real integration
  (``_check_and_mitigate_budget`` -> ``token_budget.apply_mitigation`` ->
  ``token_budget._prune_evidence``) passes the REAL evidence package -- a
  single-line ``json.dumps(..., ensure_ascii=False)`` string with no blank
  lines at all -- to a pruning routine whose item-splitting convention is
  the ``"\\n\\n"`` delimiter. Since the real evidence text never contains
  that delimiter, item-count/trailing-item pruning is a complete no-op in
  production, and pruning falls straight through to raw char-level
  truncation of the JSON blob. This is confidence-blind and Evidence_ID-
  blind by construction: whichever item happens to sort first
  (lowest Evidence_ID) survives truncation, and whichever sorts last is
  dropped, with zero regard for confidence labels. task 3.1/3.2's
  ``test_token_budget_properties.py`` "Property 21 scope note" already
  documented this gap at the token_budget.py-only level and deferred full
  coverage to "the task 8.2 pdf_processor.py integration, which has that
  [structured] data" -- but reading pdf_processor.py's actual integration
  (this task's job) shows task 8.2 did NOT add confidence-aware pruning;
  it re-uses the same flat-text ``token_budget.apply_mitigation`` verbatim.
  Rather than fabricate a passing test that pretends the requirement is
  met, this file documents the gap with a concrete, reproducible
  demonstration using real production code (no faked internals), matching
  the established precedent of "honestly characterize what the CURRENT
  implementation actually and provably does" set by test_token_budget_
  properties.py's Property 21 tests.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline import token_budget
from pipeline.deterministic_merge import deterministic_merge
from pipeline.evidence_index import EvidenceBundle, build_paper_evidence_package
from pipeline.token_report import generate_token_report
from agents.openai.telemetry import PromptFingerprint, TelemetryCollector, TelemetryRecord

import pipeline.pdf_processor as _pdf_processor

# ---------------------------------------------------------------------------
# agents.openai.prompts -- loaded exactly like
# tests/src/agents/openai/test_prompts_stability_properties.py, so this file
# gets the real, un-mocked prompt builders regardless of --import-mode.
# ---------------------------------------------------------------------------
import importlib.util as _importlib_util

_AGENTS_ROOT = Path(__file__).resolve().parents[3] / "src"

if "agents" not in sys.modules or not hasattr(sys.modules["agents"], "agent_schema_validator"):
    _agents_spec = _importlib_util.spec_from_file_location(
        "agents",
        _AGENTS_ROOT / "agents" / "__init__.py",
        submodule_search_locations=[str(_AGENTS_ROOT / "agents")],
    )
    assert _agents_spec is not None and _agents_spec.loader is not None
    _agents_mod = _importlib_util.module_from_spec(_agents_spec)
    sys.modules["agents"] = _agents_mod
    _agents_spec.loader.exec_module(_agents_mod)

if "agents.openai.prompts" in sys.modules:
    _PROMPTS_MODULE = sys.modules["agents.openai.prompts"]
else:
    _PROMPTS_PATH = _AGENTS_ROOT / "agents" / "openai" / "prompts.py"
    _SPEC = _importlib_util.spec_from_file_location("agents.openai.prompts", _PROMPTS_PATH)
    assert _SPEC is not None and _SPEC.loader is not None
    _PROMPTS_MODULE = _importlib_util.module_from_spec(_SPEC)
    sys.modules["agents.openai.prompts"] = _PROMPTS_MODULE
    _SPEC.loader.exec_module(_PROMPTS_MODULE)

_shared_paper_prefix = _PROMPTS_MODULE._shared_paper_prefix
build_user_message = _PROMPTS_MODULE.build_user_message
compute_stable_prefix = _PROMPTS_MODULE.compute_stable_prefix
get_system_prompt = _PROMPTS_MODULE.get_system_prompt

# ---------------------------------------------------------------------------
# Shared fixture data: the REAL 62-field extraction map and the REAL
# num_chunks=5 domain_to_chunk mapping (mirrors
# src/utils/config_utils.py::_get_domain_to_chunk(5) literally -- see that
# function for the source of truth this table is copied from).
# ---------------------------------------------------------------------------
_EXTRACTION_MAP_PATH = Path(__file__).resolve().parents[3] / "configs" / "extraction_map.json"

_DOMAIN_TO_CHUNK_5 = {
    1: 1, 2: 1, 3: 1,
    4: 2, 5: 2,
    6: 3, 7: 3, 8: 3, 9: 3,
    10: 4, 11: 4, 12: 4,
    13: 5,
}

# Real defaults from configs/config.yaml (openai.max_evidence_items_per_chunk,
# openai.max_evidence_chars_per_chunk).
_REAL_MAX_EVIDENCE_ITEMS = 150
_REAL_MAX_EVIDENCE_CHARS = 10_000

# Req 9.1's baseline threshold (Requirement 9.1: "do not exceed 5,000 tokens
# (the configured baseline threshold default)").
_BASELINE_UNCACHED_TOKEN_THRESHOLD = 5000


def _load_all_fields() -> list[dict]:
    return json.loads(_EXTRACTION_MAP_PATH.read_text(encoding="utf-8"))


def _domain_num(field: dict) -> int:
    return int(field["domain_group"].split(".")[0])


def _fields_by_chunk(all_fields: list[dict]) -> dict[int, list[dict]]:
    by_chunk: dict[int, list[dict]] = {c: [] for c in range(1, 6)}
    for f in all_fields:
        by_chunk[_DOMAIN_TO_CHUNK_5[_domain_num(f)]].append(f)
    return by_chunk


def _make_evidence_items(n: int = 60) -> list[dict]:
    """Realistic, fixed-length evidence items (a fixture 'paper')."""
    items = []
    for i in range(1, n + 1):
        items.append(
            {
                "id": f"S{i:06d}",
                "type": "sentence",
                "section_path": "body",
                "page": 1,
                "coords": None,
                "text": (
                    f"Evidence sentence {i} describing study design, sample "
                    "characteristics, model architecture, or evaluation "
                    "results relevant to this fixture paper."
                ),
                "annotations": {},
                "score": 0,
            }
        )
    return items


def _make_bundle(evidence_items=None, evidence_map=None) -> EvidenceBundle:
    return EvidenceBundle(
        paper_id="fixture_paper",
        tei_xml="",
        evidence_items=evidence_items if evidence_items is not None else _make_evidence_items(),
        evidence_map=evidence_map or {},
        prefilled_fields={},
        index_path=Path("unused"),
    )


# ---------------------------------------------------------------------------
# Req 9.6 infrastructure: a shared assertion helper used by every threshold
# check in this file, so a breach anywhere in this suite surfaces the
# measured value, the threshold, and the breaching component in the failure
# message (Requirement 9.6).
# ---------------------------------------------------------------------------


def _assert_metric_within_threshold(
    *, measured: float, threshold: float, component: str, comparator: str = "<=",
) -> None:
    """Assert a metric against its threshold; failure message names all
    three of measured value, threshold, and breaching component (Req 9.6).
    """
    if comparator == "<=":
        ok = measured <= threshold
    elif comparator == ">=":
        ok = measured >= threshold
    else:
        raise ValueError(f"unsupported comparator {comparator!r}")

    assert ok, (
        f"Token-efficiency regression breach in component {component!r}: "
        f"measured={measured!r} threshold={threshold!r} "
        f"comparator={comparator!r}"
    )


# ---------------------------------------------------------------------------
# Req 9.1: estimated uncached input tokens per request <= 5000 baseline
# ---------------------------------------------------------------------------


def test_req_9_1_uncached_input_tokens_per_request_within_baseline_threshold():
    """Requirement 9.1: for realistic fixture data built from the REAL
    62-field extraction map, the REAL 5-chunk domain_to_chunk mapping, and
    the REAL default evidence caps (configs/config.yaml), the estimated
    UNCACHED input tokens per request -- i.e. everything in the built user
    message beyond the shared, cacheable ``_shared_paper_prefix`` (the
    extraction-map block, and for synthesis the prior_context block, plus
    the trailing instruction line) -- must not exceed the 5,000-token
    baseline default (Correctness: this is the per-request cost that is
    NOT covered by OpenAI's prompt cache on repeat calls for the same
    paper).

    Mutation check: inflating any extraction chunk's field-definition JSON
    (e.g. by duplicating `definition` text many times over) or ballooning
    prior_context would push `uncached_tokens` past 5000 and fail this
    test with the measured/threshold/component detail from
    `_assert_metric_within_threshold` (Req 9.6).
    """
    all_fields = _load_all_fields()
    assert len(all_fields) == 62  # sanity: fixture matches the real map

    bundle = _make_bundle()
    source_package = build_paper_evidence_package(
        bundle, all_fields, max_items=_REAL_MAX_EVIDENCE_ITEMS, max_chars=_REAL_MAX_EVIDENCE_CHARS,
    )

    # Everything up through the shared evidence package is the cacheable
    # Stable_Prefix; it is warmed once per paper and hits cache on every
    # subsequent chunk/synthesis call, so it never contributes to
    # per-request UNCACHED tokens after the first call.
    cached_prefix_tokens = token_budget.estimate_tokens(_shared_paper_prefix(source_package))

    by_chunk = _fields_by_chunk(all_fields)

    # Extraction chunks 1-4.
    for chunk_num in range(1, 5):
        chunk_fields = by_chunk[chunk_num]
        message = build_user_message(source_package, chunk_fields)
        total_tokens = token_budget.estimate_tokens(message)
        uncached_tokens = total_tokens - cached_prefix_tokens
        _assert_metric_within_threshold(
            measured=uncached_tokens,
            threshold=_BASELINE_UNCACHED_TOKEN_THRESHOLD,
            component=f"extraction_chunk {chunk_num} uncached input tokens",
        )

    # Synthesis chunk (worst case for uncached size: prior_context summarizes
    # every field resolved by the OTHER four chunks, in addition to its own
    # exclusive domain-13 fields).
    synthesis_fields = by_chunk[5]
    synthesis_indices = {f["field_index"] for f in synthesis_fields}
    prior_context = [
        {
            "field_index": f["field_index"],
            "field_name": f["field_name"],
            "value": "placeholder extracted value",
            "confidence": "h",
        }
        for f in all_fields
        if f["field_index"] not in synthesis_indices
    ]
    synthesis_message = build_user_message(source_package, synthesis_fields, prior_context=prior_context)
    synthesis_total_tokens = token_budget.estimate_tokens(synthesis_message)
    synthesis_uncached_tokens = synthesis_total_tokens - cached_prefix_tokens
    _assert_metric_within_threshold(
        measured=synthesis_uncached_tokens,
        threshold=_BASELINE_UNCACHED_TOKEN_THRESHOLD,
        component="synthesis chunk uncached input tokens (extraction map + prior_context)",
    )


# ---------------------------------------------------------------------------
# Req 9.2: byte-level LCP ratio >= 90% between stable prefixes
# ---------------------------------------------------------------------------


def _lcp_ratio(before: str, after: str) -> float:
    """Byte-level longest-common-prefix ratio between two strings.

    Defined here (this is test infrastructure, owned by this file) as
    ``lcp_length / len(before)`` -- i.e. "what fraction of the PREVIOUSLY
    cached Stable_Prefix bytes are still a valid cache-hit prefix after the
    change." Operates on UTF-8 encoded bytes per Requirement 9.2's literal
    "byte-level" wording.
    """
    before_bytes = before.encode("utf-8")
    after_bytes = after.encode("utf-8")
    lcp_len = 0
    for b1, b2 in zip(before_bytes, after_bytes):
        if b1 != b2:
            break
        lcp_len += 1
    if not before_bytes:
        return 1.0
    return lcp_len / len(before_bytes)


def test_req_9_2_lcp_helper_computes_full_ratio_for_identical_strings():
    """Sanity check of the Req 9.2 helper itself: identical strings have a
    byte-level LCP ratio of exactly 1.0."""
    assert _lcp_ratio("identical text", "identical text") == 1.0


def test_req_9_2_lcp_ratio_stable_for_benign_trailing_addition():
    """Requirement 9.2: a benign, cache-friendly prompt-template change --
    appending new trailing rules text strictly AFTER the existing
    system_prompt + evidence_package content (exactly the kind of change
    Requirement 2's "variable material goes after the Stable_Prefix"
    design principle is meant to keep safe) -- must keep the byte-level LCP
    ratio between the before/after Stable_Prefix at or above 90%.

    Mutation check: if `compute_stable_prefix` (or a future prompts.py
    change) ever inserted new content BEFORE the evidence package instead
    of strictly after it, this test would start failing because the LCP
    would truncate at the insertion point instead of extending through the
    entire original prefix -- see test_req_9_2_lcp_ratio_helper_detects_
    prefix_breaking_change below for a concrete demonstration of exactly
    that failure mode.
    """
    all_fields = _load_all_fields()
    bundle = _make_bundle()
    source_package = build_paper_evidence_package(
        bundle, all_fields, max_items=_REAL_MAX_EVIDENCE_ITEMS, max_chars=_REAL_MAX_EVIDENCE_CHARS,
    )
    system_prompt = get_system_prompt()

    stable_prefix_before = compute_stable_prefix(system_prompt, source_package, "")
    stable_prefix_after = compute_stable_prefix(
        system_prompt, source_package, "NEW TRAILING RULE: prefer SI units where ambiguous.",
    )

    ratio = _lcp_ratio(stable_prefix_before, stable_prefix_after)
    _assert_metric_within_threshold(
        measured=ratio, threshold=0.90, component="Stable_Prefix LCP ratio (benign trailing addition)",
        comparator=">=",
    )


def test_req_9_2_lcp_ratio_helper_detects_prefix_breaking_change():
    """Demonstrates the Req 9.2 LCP-ratio check has real discriminating
    power (not a vacuously-passing metric): a change that inserts new
    content at the FRONT of the system prompt -- e.g. a hypothetical
    future schema-version marker prepended ahead of the real prompt text --
    breaks byte alignment from position 0 onward and drives the ratio
    close to 0%, i.e. well below the 90% threshold. This is the failure
    mode a real regression (accidentally injecting per-run metadata into
    the Stable_Prefix, which Requirement 2 explicitly forbids) would
    trigger.
    """
    all_fields = _load_all_fields()
    bundle = _make_bundle()
    source_package = build_paper_evidence_package(
        bundle, all_fields, max_items=_REAL_MAX_EVIDENCE_ITEMS, max_chars=_REAL_MAX_EVIDENCE_CHARS,
    )
    system_prompt = get_system_prompt()

    stable_prefix_before = compute_stable_prefix(system_prompt, source_package, "")
    stable_prefix_after = compute_stable_prefix(
        "[SCHEMA v2] " + system_prompt, source_package, "",
    )

    ratio = _lcp_ratio(stable_prefix_before, stable_prefix_after)
    assert ratio < 0.90, (
        f"expected a prefix-breaking mutation to drive the LCP ratio below "
        f"the 90% threshold, but measured={ratio!r}"
    )


# ---------------------------------------------------------------------------
# Req 9.3: no synthesis model call when all fields are non-conflicting
# (see module docstring's honesty note for scope)
# ---------------------------------------------------------------------------


def _make_qc_context(pdf_name: str, exact_text: str = "sample text"):
    from quality_control.models import Candidate, QCBundle, UnifiedRecord

    unified = UnifiedRecord(
        document_id=pdf_name,
        content={"exact_text": exact_text, "source_pdf_path": ""},
    )
    return QCBundle(
        branches=[Candidate(source="grobid", index=0, payload="<TEI/>", status=None)],
        unified=unified,
    )


def _base_openai_config(num_chunks: int) -> dict:
    return {
        "chunk_model": "gpt-test",
        "synthesis_model": "gpt-test",
        "enable_cache_prewarm": False,
        "num_chunks": num_chunks,
        "prewarm_synthesis_if_model_diff": False,
        "max_evidence_items_per_chunk": 250,
        "max_evidence_chars_per_chunk": 60000,
    }


def _run_process_pdf(pdf_name, chunk_fields, field_lookup, bundle, openai_config, mock_api, tmp_path):
    """Minimal process_pdf harness, self-contained to this regression test
    file (mirrors the pattern in tests/src/pipeline/test_pdf_processor_
    helpers.py's `_run_process_pdf`, but duplicated locally per this task's
    boundary of touching only this one new file)."""
    manifest: dict = {}
    qc_context = _make_qc_context(pdf_name)

    with patch.object(_pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.dict(sys.modules, {"agents.openai.api_client": mock_api}), \
         patch.object(_pdf_processor, "validate_qc_context_input"), \
         patch.object(_pdf_processor, "build_or_load_evidence_bundle", return_value=bundle):

        async def _run():
            semaphore = asyncio.Semaphore(5)
            lock = asyncio.Lock()
            return await _pdf_processor.process_pdf(
                qc_context=qc_context,
                chunk_fields=chunk_fields,
                field_lookup=field_lookup,
                api_semaphore=semaphore,
                manifest=manifest,
                manifest_lock=lock,
                openai_config=openai_config,
            )

        return asyncio.run(_run())


def test_req_9_3_no_synthesis_call_when_all_fields_non_conflicting(tmp_path):
    """Requirement 9.3 (see module docstring's honesty note for the scope
    caveat): a fixture scenario where every field_index resolves without a
    cross-chunk conflict AND the synthesis chunk owns no exclusive fields
    of its own -- confirms zero LLM calls are made for the synthesis
    chunk (extract_chunk's call count equals only the number of extraction
    chunks, never num_chunks).

    Mutation check: if `process_pdf`'s `effective_synthesis_fields` guard
    (pdf_processor.py) were removed or regressed to unconditionally call
    the synthesis chunk, `mock_api.extract_chunk.call_count` would be 3
    instead of 2 here, failing this test.
    """
    pdf_name = "paper_regression_skip"
    chunk_fields = {
        1: [{"field_index": 3, "domain_group": "2. Clinical context", "field_name": "Study design"}],
        2: [{"field_index": 4, "domain_group": "2. Clinical context", "field_name": "Sample size"}],
        # chunk 3 (synthesis) intentionally owns no fields of its own.
    }
    field_lookup = {
        3: {"domain_group": 2, "field_name": "Study design"},
        4: {"domain_group": 2, "field_name": "Sample size"},
    }
    bundle = _make_bundle(evidence_map={
        "ev-1": {"id": "ev-1", "type": "sentence", "text": "evidence 1"},
        "ev-2": {"id": "ev-2", "type": "sentence", "text": "evidence 2"},
    })
    openai_config = _base_openai_config(num_chunks=3)

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": ["ev-1"], "c": "h"}]})
        elif chunk_num == 2:
            return json.dumps({"extractions": [{"i": 4, "v": "120", "loc": ["ev-2"], "c": "m"}]})
        raise AssertionError(
            f"Req 9.3 regression: extract_chunk should not be called for chunk "
            f"{chunk_num} (the synthesis chunk) when no conflicts exist and the "
            f"synthesis chunk owns no exclusive fields."
        )

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    result = _run_process_pdf(pdf_name, chunk_fields, field_lookup, bundle, openai_config, mock_api, tmp_path)

    _assert_metric_within_threshold(
        measured=mock_api.extract_chunk.call_count,
        threshold=2,
        component="process_pdf extract_chunk call count (synthesis should be skipped)",
        comparator="<=",
    )
    assert mock_api.extract_chunk.call_count == 2
    assert result is not None
    final_by_index = {f["field_index"]: f for f in result}
    assert final_by_index[3]["extracted_value"] == "RCT"
    assert final_by_index[4]["extracted_value"] == "120"


# ---------------------------------------------------------------------------
# Req 9.4: high-confidence Evidence_IDs preserved after evidence pruning
# (KNOWN LIMITATION -- see module docstring's honesty note)
# ---------------------------------------------------------------------------


def test_req_9_4_known_limitation_evidence_pruning_is_confidence_blind():
    """Requirement 9.4, as literally worded, is NOT satisfied by the
    current production code, and this test documents that concretely
    rather than fabricating a passing check (see module docstring's
    honesty note for the full investigation).

    Setup: a fixture evidence bundle where the LOWEST Evidence_ID
    (S000001, sorts first in the evidence package) is referenced by a
    LOW-confidence field, and the HIGHEST Evidence_ID (S000005, sorts
    last) is referenced by a HIGH-confidence ("h") field -- i.e. exactly
    the scenario Req 9.4 says must survive pruning. The evidence package is
    built via the REAL `build_paper_evidence_package` (single-line JSON,
    no blank lines), and pruned via the REAL
    `token_budget.apply_mitigation` under a budget small enough to force
    truncation -- the same call path `pdf_processor.py`'s
    `_check_and_mitigate_budget` uses in production (Requirement 7
    integration).

    Observed (and asserted) result: pruning keeps the EARLIEST-sorted
    Evidence_ID (S000001, low confidence) and drops the LAST one (S000005,
    high confidence) -- the opposite of what Req 9.4 requires. This is a
    direct, structural consequence of `_prune_evidence`'s blank-line
    ("\\n\\n") item-delimiter convention never matching a real, single-line
    JSON evidence package: item-count/trailing-item pruning becomes a
    no-op, and pruning falls through to raw char-level truncation, which
    has no concept of Evidence_ID or confidence at all.
    """
    items = [
        {
            "id": f"S{i:06d}",
            "type": "sentence",
            "section_path": "body",
            "page": 1,
            "coords": None,
            # Deliberately long, near-identical filler so total evidence
            # size comfortably exceeds a small forced budget.
            "text": f"Evidence sentence number {i}. " + ("filler context text " * 20),
            "annotations": {},
            "score": 0,
        }
        for i in range(1, 6)
    ]
    bundle = _make_bundle(evidence_items=items)
    all_fields = [
        {"field_index": 1, "field_name": "Low-confidence field", "definition": "d", "reviewer_question": "q"},
        {"field_index": 2, "field_name": "High-confidence field", "definition": "d", "reviewer_question": "q"},
    ]
    source_package = build_paper_evidence_package(bundle, all_fields, max_items=150, max_chars=100_000)

    # low-confidence field references the FIRST (lowest) Evidence_ID;
    # high-confidence field references the LAST (highest) Evidence_ID.
    low_confidence_evidence_id = "S000001"
    high_confidence_evidence_id = "S000005"
    assert low_confidence_evidence_id in source_package
    assert high_confidence_evidence_id in source_package

    prompt_parts = {
        "system": "system prompt placeholder",
        "evidence": source_package,
        "field_definitions": json.dumps(all_fields),
        "prior_context": "",
    }
    full_tokens = token_budget.estimate_tokens(token_budget._join_parts(prompt_parts))
    # Force pruning: a budget well below the full estimate, but large
    # enough that mitigation succeeds without raising
    # TokenBudgetExceededError (Req 9.6's failure-reporting semantics for
    # that error path are exercised by test_token_budget.py already; this
    # test targets the pruning OUTCOME, not the reject path).
    forced_budget = full_tokens // 2

    mitigated_text, warnings = token_budget.apply_mitigation(
        prompt_parts, "extraction_chunk", forced_budget, {},
    )

    assert warnings, "expected evidence pruning to have been applied for this forced-small budget"
    assert len(mitigated_text) < len(token_budget._join_parts(prompt_parts)), (
        "expected pruning to actually shrink the prompt"
    )

    # This is the KNOWN LIMITATION: the high-confidence Evidence_ID is
    # dropped, while the low-confidence one survives -- the inverse of
    # Req 9.4's requirement. If a future change makes production pruning
    # confidence-aware, this assertion should be revisited (it would then
    # need to flip to asserting PRESERVATION of the high-confidence ID).
    assert high_confidence_evidence_id not in mitigated_text, (
        "KNOWN LIMITATION regression: expected the flat-text, confidence-"
        "blind pruning path to drop the high-confidence Evidence_ID "
        f"{high_confidence_evidence_id!r} under a forced small budget "
        "(see this test's docstring and the module docstring's Req 9.4 "
        "honesty note) -- if this now fails, either production pruning "
        "became confidence-aware (great -- update this test to assert "
        "preservation instead) or the pruning behavior changed in some "
        "other way that should be investigated."
    )
    assert low_confidence_evidence_id in mitigated_text


# ---------------------------------------------------------------------------
# Req 9.5: token_report.json schema conformance + per-stage sum invariant
# ---------------------------------------------------------------------------

_TOKEN_REPORT_JSON_SCHEMA = {
    "type": "object",
    "required": [
        "status",
        "per_stage",
        "top_5_expensive",
        "telemetry_records",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["complete", "telemetry_unavailable"]},
        "total_input_tokens": {"type": ["integer", "null"]},
        "total_output_tokens": {"type": ["integer", "null"]},
        "total_cached_input_tokens": {"type": ["integer", "null"]},
        "total_uncached_input_tokens": {"type": ["integer", "null"]},
        "overall_cache_rate": {"type": ["number", "null"]},
        "output_to_input_ratio": {"type": ["number", "null"]},
        "per_stage": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "stage",
                    "total_input_tokens",
                    "total_output_tokens",
                    "total_cached_input_tokens",
                    "total_uncached_input_tokens",
                    "request_count",
                    "mean_cache_rate",
                ],
                "properties": {
                    "stage": {"type": "string"},
                    "total_input_tokens": {"type": "integer"},
                    "total_output_tokens": {"type": "integer"},
                    "total_cached_input_tokens": {"type": "integer"},
                    "total_uncached_input_tokens": {"type": "integer"},
                    "request_count": {"type": "integer"},
                    "mean_cache_rate": {"type": "number"},
                },
            },
        },
        "top_5_expensive": {"type": "array"},
        "telemetry_records": {"type": "array"},
        "delta": {"type": ["object", "null"]},
    },
}


def _make_telemetry_record(
    *, stage: str, input_tokens: int, output_tokens: int, cached_input_tokens: int,
) -> TelemetryRecord:
    return TelemetryRecord(
        stage=stage,
        model="gpt-5.5",
        timestamp="2026-07-21T00:00:00Z",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=input_tokens - cached_input_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_fingerprint=PromptFingerprint(stable_prefix_hash="fixture0123456789", prompt_version="v1"),
    )


def test_req_9_5_token_report_schema_conformance_and_stage_sums(tmp_path):
    """Requirement 9.5: for a realistic fixture run (one cache_warmup call,
    four extraction_chunk calls, one synthesis call -- mirroring the real
    5-chunk pipeline shape), the generated token_report.json (a) conforms
    to the Token_Report JSON schema (design.md's "TokenReport (JSON
    schema)" data model), and (b) the sum of per-Stage input tokens,
    output tokens, cached input tokens, and uncached input tokens each
    equal their corresponding overall totals.

    This is a REGRESSION-framed guard specific to this file (a realistic
    fixture scenario, validated against jsonschema), distinct from task
    5.2's Hypothesis-driven Property 19 in test_token_report_properties.py
    which covers the same invariant across randomly generated inputs.

    Mutation check: if `generate_token_report` ever miscomputed a
    per-stage total (e.g. double-counting a record, or omitting cached
    tokens from one stage's sum), either the schema check or the sum
    invariant below would fail.
    """
    import jsonschema

    collector = TelemetryCollector()
    collector.record(_make_telemetry_record(stage="cache_warmup", input_tokens=500, output_tokens=10, cached_input_tokens=0))
    for chunk_num in range(1, 5):
        collector.record(
            _make_telemetry_record(
                stage="extraction_chunk", input_tokens=8000 + chunk_num * 10,
                output_tokens=900, cached_input_tokens=6000,
            )
        )
    collector.record(_make_telemetry_record(stage="synthesis", input_tokens=9500, output_tokens=1200, cached_input_tokens=7000))

    report = generate_token_report(collector, tmp_path)

    report_path = tmp_path / "token_report.json"
    assert report_path.exists()
    written = json.loads(report_path.read_text(encoding="utf-8"))

    # (a) Schema conformance.
    jsonschema.validate(instance=written, schema=_TOKEN_REPORT_JSON_SCHEMA)

    # (b) Per-stage sums equal overall totals (Req 9.5's literal wording).
    for metric in (
        "total_input_tokens", "total_output_tokens",
        "total_cached_input_tokens", "total_uncached_input_tokens",
    ):
        per_stage_sum = sum(stage[metric] for stage in written["per_stage"])
        overall_total = written[metric]
        _assert_metric_within_threshold(
            measured=per_stage_sum, threshold=overall_total, component=f"token_report per-stage sum of {metric}",
            comparator="<=",
        )
        _assert_metric_within_threshold(
            measured=overall_total, threshold=per_stage_sum, component=f"token_report overall total of {metric}",
            comparator="<=",
        )
        assert per_stage_sum == overall_total


# ---------------------------------------------------------------------------
# Req 9.6: failure output includes measured value, threshold, and breaching
# component -- exercised directly against this file's own shared assertion
# helper (Req 9.6 is about the test infrastructure's own failure-reporting
# quality, not a production-code behavior; see requirements.md 9.6's
# wording: "THE Pipeline SHALL include in the test failure output ...").
# ---------------------------------------------------------------------------


def test_req_9_6_failure_output_includes_measured_threshold_and_component():
    """Requirement 9.6: deliberately trigger a failure through
    `_assert_metric_within_threshold` -- the shared helper every Req 9.1,
    9.2, 9.3, and 9.5 check in this file routes its threshold comparison
    through -- and confirm the resulting AssertionError message contains
    all three required pieces of information: the measured value, the
    threshold value, and the name of the breaching Stage/prompt component.
    """
    with pytest.raises(AssertionError) as excinfo:
        _assert_metric_within_threshold(
            measured=9999, threshold=5000, component="extraction_chunk 2 uncached input tokens",
        )
    message = str(excinfo.value)
    assert "9999" in message, "failure output must include the measured value"
    assert "5000" in message, "failure output must include the threshold value"
    assert "extraction_chunk 2 uncached input tokens" in message, (
        "failure output must name the breaching Stage/prompt component"
    )

    # Also exercise the ">=" comparator direction (used by the Req 9.2 LCP
    # ratio check), to confirm the message format holds for both
    # directions, not just "<=".
    with pytest.raises(AssertionError) as excinfo_ge:
        _assert_metric_within_threshold(
            measured=0.5, threshold=0.90, component="Stable_Prefix LCP ratio", comparator=">=",
        )
    message_ge = str(excinfo_ge.value)
    assert "0.5" in message_ge
    assert "0.9" in message_ge
    assert "Stable_Prefix LCP ratio" in message_ge
