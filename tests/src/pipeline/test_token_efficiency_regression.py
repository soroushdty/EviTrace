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

Honesty note (read before editing) -- Req 9.3
-----------------------------------------------------------
One of the six Requirement 9 criteria cannot be satisfied exactly as
literally worded against this codebase's REAL, current configuration, for
reasons already documented elsewhere in this spec (tasks.md
"Implementation Notes", task 8.2's discovery re: domain_to_chunk):

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

Req 9.4 -- FIXED (previously documented here as a known limitation)
---------------------------------------------------------------------
Req 9.4 ("high-confidence Evidence_IDs remain present after evidence is
pruned") was originally flagged in this file (and independently confirmed
by /kiro-validate-impl's feature-level gate) as NOT satisfied in
production: pdf_processor.py's synthesis-stage budget mitigation forwarded
the real evidence package straight into token_budget.py's confidence-blind,
flat-text ``apply_mitigation()``/``_prune_evidence()``, which has no concept
of Evidence_ID or confidence and could drop a high-confidence reference
while keeping a low-confidence one.

This has been remediated in ``pdf_processor.py`` (see
``_collect_protected_evidence_ids``, ``_prune_evidence_json_preserving_
protected``, and ``_check_and_mitigate_budget``'s ``protected_evidence_ids``
parameter): at the synthesis call site -- the only point in the pipeline
where confidence-labeled data (deterministic-merge output, prefilled
fields, conflict-candidate records) is actually available at prune-time --
the union of Evidence_IDs referenced by any confidence-"h" field/candidate
is computed and passed through so pruning never drops them, even under
budget pressure. ``token_budget.py`` itself is intentionally left
unchanged (still flat-text/confidence-blind in isolation, per its own
module docstring "Scope note") since the fix lives one layer above it, in
the integration that has the structured data; the extraction-chunk and
validation_repair call sites are correspondingly unaffected because there
is genuinely no confidence data to protect at those prune-time points (no
field has been extracted yet).

``test_req_9_4_evidence_pruning_preserves_high_confidence_evidence_ids``
below now asserts the CORRECT (fixed) behavior directly against
``pdf_processor.py``'s real pruning helpers, and
``test_req_9_4_process_pdf_synthesis_preserves_high_confidence_evidence_id_end_to_end``
exercises the fix through the real ``process_pdf`` synthesis call path.
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
# (FIXED -- see module docstring's honesty note)
# ---------------------------------------------------------------------------


def _make_req_9_4_fixture_items(n: int = 5) -> list[dict]:
    """Deliberately long, near-identical filler evidence items so total
    evidence size comfortably exceeds a small forced budget."""
    return [
        {
            "id": f"S{i:06d}",
            "type": "sentence",
            "section_path": "body",
            "page": 1,
            "coords": None,
            "text": f"Evidence sentence number {i}. " + ("filler context text " * 20),
            "annotations": {},
            "score": 0,
        }
        for i in range(1, n + 1)
    ]


def test_req_9_4_evidence_pruning_preserves_high_confidence_evidence_ids():
    """Requirement 9.4 (FIXED -- see module docstring's honesty note):
    a high-confidence field's Evidence_ID must survive synthesis-stage
    evidence pruning even when it is positioned such that naive
    tail-truncating pruning would have dropped it first.

    Setup mirrors the original (now-fixed) known-limitation
    investigation: a fixture evidence bundle where the LOWEST Evidence_ID
    (S000001, sorts first in the evidence package -- i.e. what the old
    flat-text truncation kept) is referenced by a LOW-confidence field,
    and the HIGHEST Evidence_ID (S000005, sorts last -- what the old
    flat-text truncation dropped first) is referenced by a
    HIGH-confidence ("h") field. The evidence package is built via the
    REAL `build_paper_evidence_package`, and pruned via
    `pdf_processor._check_and_mitigate_budget` -- the exact function
    `process_pdf`'s synthesis integration calls in production -- with
    `protected_evidence_ids` computed by the real
    `pdf_processor._collect_protected_evidence_ids` helper.

    The forced budget is calibrated (not guessed) from the actual
    protected-only serialization's token estimate, so this test
    exercises the real threshold logic rather than an arbitrary cutoff.
    """
    items = _make_req_9_4_fixture_items(5)
    bundle = _make_bundle(evidence_items=items)
    all_fields = [
        {"field_index": 1, "field_name": "Low-confidence field", "definition": "d", "reviewer_question": "q"},
        {"field_index": 2, "field_name": "High-confidence field", "definition": "d", "reviewer_question": "q"},
    ]
    source_package = build_paper_evidence_package(bundle, all_fields, max_items=150, max_chars=100_000)

    # low-confidence field references the FIRST (lowest) Evidence_ID;
    # high-confidence field references the LAST (highest) Evidence_ID --
    # the one naive tail-truncating pruning drops first.
    low_confidence_evidence_id = "S000001"
    high_confidence_evidence_id = "S000005"
    assert low_confidence_evidence_id in source_package
    assert high_confidence_evidence_id in source_package

    merged_fields = [
        {"i": 1, "v": "low", "loc": [low_confidence_evidence_id], "c": "l"},
        {"i": 2, "v": "high", "loc": [high_confidence_evidence_id], "c": "h"},
    ]
    protected_ids = _pdf_processor._collect_protected_evidence_ids(merged_fields, [], [])
    assert protected_ids == {high_confidence_evidence_id}

    system_text = "system prompt placeholder"
    field_definitions_text = json.dumps(all_fields)
    other_parts = {"system": system_text, "field_definitions": field_definitions_text, "prior_context": ""}

    # Calibrate the forced budget from the real protected-only estimate
    # (budget=0 forces the helper to drop every unprotected item), so the
    # threshold below is derived from actual production logic rather than
    # an arbitrary guess.
    protected_only_text, _ = _pdf_processor._prune_evidence_json_preserving_protected(
        source_package, protected_ids, other_parts, budget=0,
    )
    protected_only_tokens = token_budget.estimate_tokens(
        token_budget._join_parts({**other_parts, "evidence": protected_only_text})
    )
    forced_budget = protected_only_tokens + 5

    full_tokens = token_budget.estimate_tokens(
        token_budget._join_parts({**other_parts, "evidence": source_package})
    )
    assert forced_budget < full_tokens, (
        "fixture sanity check: forced budget must actually be below the "
        "full (unpruned) prompt's estimate, or mitigation would never "
        "trigger in the first place"
    )

    mitigated_text = _pdf_processor._check_and_mitigate_budget(
        stage="synthesis",
        system_text=system_text,
        evidence_text=source_package,
        field_definitions_text=field_definitions_text,
        prior_context_text="",
        budgets={"synthesis": forced_budget},
        evidence_config={},
        pdf_name="fixture_paper",
        chunk_num=99,
        protected_evidence_ids=protected_ids,
    )

    assert len(mitigated_text) < len(source_package), "expected pruning to actually shrink the evidence"
    assert high_confidence_evidence_id in mitigated_text, (
        "Req 9.4 regression: the high-confidence Evidence_ID must survive "
        "synthesis-stage evidence pruning"
    )
    assert low_confidence_evidence_id not in mitigated_text, (
        "expected the low-confidence, unprotected Evidence_ID to be pruned "
        "to make room under the forced small budget -- if this now fails "
        "pruning may not be happening at all, which would make the "
        "high-confidence assertion above vacuous"
    )


def test_req_9_4_process_pdf_synthesis_preserves_high_confidence_evidence_id_end_to_end(tmp_path):
    """Requirement 9.4 end-to-end: exercises the fix through the REAL
    `process_pdf` synthesis call path (not just an isolated helper),
    proving a high-confidence Evidence_ID from an extraction chunk's
    deterministic-merge output actually reaches the synthesis LLM call
    intact after budget-forced pruning.

    Two-pass calibration (deterministic, not guessed):
      Pass 1 runs with the default (large) synthesis Token_Budget and
      captures the REAL, unmitigated synthesis evidence text plus the
      real system/field-definitions/prior_context synthesis inputs
      `process_pdf` builds for this fixture.
      Pass 2 recomputes the exact token threshold at which pruning must
      remove every unprotected item (via the same production helpers
      used by pass 1's fix), sets that as the synthesis Token_Budget, and
      reruns -- forcing real pruning through the real synthesis
      integration.

    Fixture: chunk 1 (extraction) yields field 3 with confidence "h"
    referencing the LAST-sorted (highest) Evidence_ID -- the one naive
    tail-truncating pruning would drop first -- and field 4 with
    confidence "l" referencing the FIRST-sorted (lowest) Evidence_ID.
    Chunk 2 (synthesis) owns its own exclusive field 5, so synthesis
    always runs (Req 5.6 is not at stake here).
    """
    pdf_name = "paper_req_9_4_e2e"
    items = _make_req_9_4_fixture_items(10)
    evidence_map = {it["id"]: it for it in items}
    bundle = _make_bundle(evidence_items=items, evidence_map=evidence_map)

    low_confidence_evidence_id = "S000001"
    high_confidence_evidence_id = "S000010"

    chunk_fields = {
        1: [
            {"field_index": 3, "domain_group": "2. Clinical context", "field_name": "Study design"},
            {"field_index": 4, "domain_group": "2. Clinical context", "field_name": "Sample size"},
        ],
        2: [{"field_index": 5, "domain_group": "13. Reviewer assessment", "field_name": "Synthesis notes"}],
    }
    field_lookup = {
        3: {"domain_group": 2, "field_name": "Study design"},
        4: {"domain_group": 2, "field_name": "Sample size"},
        5: {"domain_group": 13, "field_name": "Synthesis notes"},
    }

    captured_synthesis_calls: list = []

    def _make_side_effect():
        def _side_effect(chunk_num, source, fields, *args, **kwargs):
            if chunk_num == 1:
                return json.dumps({
                    "extractions": [
                        {"i": 3, "v": "RCT", "loc": [high_confidence_evidence_id], "c": "h"},
                        {"i": 4, "v": "120", "loc": [low_confidence_evidence_id], "c": "l"},
                    ]
                })
            elif chunk_num == 2:
                captured_synthesis_calls.append(
                    {"source": source, "fields": fields, "prior_context": kwargs.get("prior_context")}
                )
                return json.dumps({"extractions": [{"i": 5, "v": "Solid paper", "loc": [], "c": "h"}]})
            raise AssertionError(f"unexpected chunk_num {chunk_num}")
        return _side_effect

    openai_config_pass1 = _base_openai_config(num_chunks=2)
    mock_api_1 = MagicMock()
    mock_api_1.extract_chunk = AsyncMock(side_effect=_make_side_effect())
    mock_api_1.warm_pdf_cache = AsyncMock()

    result_1 = _run_process_pdf(
        pdf_name, chunk_fields, field_lookup, bundle, openai_config_pass1, mock_api_1, tmp_path,
    )
    assert result_1 is not None
    assert mock_api_1.extract_chunk.call_count == 2  # extraction chunk 1 + synthesis chunk 2
    assert len(captured_synthesis_calls) == 1
    full_synthesis_source = captured_synthesis_calls[0]["source"]
    full_synthesis_fields = captured_synthesis_calls[0]["fields"]
    full_prior_context = captured_synthesis_calls[0]["prior_context"]

    # Sanity: with the large default synthesis budget, nothing was pruned,
    # and both Evidence_IDs are present in the unmitigated evidence.
    assert high_confidence_evidence_id in full_synthesis_source
    assert low_confidence_evidence_id in full_synthesis_source

    # --- Calibrate a forced synthesis budget using the REAL production
    # helpers, from the REAL captured synthesis prompt components. ---
    system_text = _pdf_processor.RepairRetryLoop._get_system_prompt_text()
    field_definitions_text = json.dumps(
        sorted(full_synthesis_fields, key=lambda f: f.get("field_index", 0))
    )
    prior_context_text = json.dumps(full_prior_context)
    other_parts = {
        "system": system_text,
        "field_definitions": field_definitions_text,
        "prior_context": prior_context_text,
    }

    merged_fields_for_protection = [
        {"i": 3, "v": "RCT", "loc": [high_confidence_evidence_id], "c": "h"},
        {"i": 4, "v": "120", "loc": [low_confidence_evidence_id], "c": "l"},
    ]
    protected_ids = _pdf_processor._collect_protected_evidence_ids(
        merged_fields_for_protection, [], [],
    )
    assert protected_ids == {high_confidence_evidence_id}

    protected_only_text, _ = _pdf_processor._prune_evidence_json_preserving_protected(
        full_synthesis_source, protected_ids, other_parts, budget=0,
    )
    protected_only_tokens = token_budget.estimate_tokens(
        token_budget._join_parts({**other_parts, "evidence": protected_only_text})
    )
    forced_synthesis_budget = protected_only_tokens + 5

    full_tokens = token_budget.estimate_tokens(
        token_budget._join_parts({**other_parts, "evidence": full_synthesis_source})
    )
    assert forced_synthesis_budget < full_tokens, (
        "fixture sanity check: forced budget must be below the real, "
        "unmitigated synthesis prompt's estimate, or pass 2 would never "
        "actually trigger mitigation"
    )

    # --- Pass 2: rerun with the forced-small synthesis budget. ---
    openai_config_pass2 = _base_openai_config(num_chunks=2)
    openai_config_pass2["token_budgets"] = {"synthesis": forced_synthesis_budget}
    captured_synthesis_calls.clear()
    mock_api_2 = MagicMock()
    mock_api_2.extract_chunk = AsyncMock(side_effect=_make_side_effect())
    mock_api_2.warm_pdf_cache = AsyncMock()

    result_2 = _run_process_pdf(
        pdf_name, chunk_fields, field_lookup, bundle, openai_config_pass2, mock_api_2, tmp_path,
    )

    assert result_2 is not None, "process_pdf must still succeed under forced synthesis pruning"
    assert len(captured_synthesis_calls) == 1
    pruned_synthesis_source = captured_synthesis_calls[0]["source"]

    assert len(pruned_synthesis_source) < len(full_synthesis_source), (
        "expected the forced small synthesis budget to actually trigger "
        "evidence pruning"
    )
    assert high_confidence_evidence_id in pruned_synthesis_source, (
        "Req 9.4 regression: the high-confidence Evidence_ID "
        f"{high_confidence_evidence_id!r} must survive real, end-to-end "
        "synthesis-stage evidence pruning through process_pdf"
    )
    assert low_confidence_evidence_id not in pruned_synthesis_source, (
        "expected the low-confidence, unprotected Evidence_ID to be "
        "pruned -- if this now fails, pruning may not be happening at "
        "all, which would make the high-confidence assertion above "
        "vacuous"
    )

    # The final saved output is still correct (the fix only changes what
    # evidence text is SENT to the LLM prompt, not the extracted values).
    final_by_index = {f["field_index"]: f for f in result_2}
    assert final_by_index[3]["extracted_value"] == "RCT"
    assert final_by_index[4]["extracted_value"] == "120"
    assert final_by_index[5]["extracted_value"] == "Solid paper"


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
