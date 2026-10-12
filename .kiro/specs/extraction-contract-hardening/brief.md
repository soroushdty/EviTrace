# Brief: extraction-contract-hardening

## Problem
The contract between the pipeline and the extraction model is under-specified and weaker than it looks, and provenance (a downstream auditor of that contract) cannot be more credible than the contract itself:
- **Evidence ids are validated against the wrong set.** `process_pdf` passes `valid_location_ids = set(bundle.evidence_map.keys())` — the *entire* evidence index — to `validate_chunk_output`, while the model is shown only `select_paper_evidence(max_items=150, max_chars=30000)`, further pruned per call by `_check_and_mitigate_budget`. On the three TEI fixtures at default caps 79% (arXiv), 41% (PLOS) and 18% (bioRxiv) of the ids are never shown. A claim "grounded" in an id the model never saw passes validation silently; conversely nothing records what the model could see.
- **"Not reported" has no defined meaning.** The prompt never says what a missing value looks like; the strict output schema makes `v` a string and `loc` a possibly-empty array, `c` an enum `h|m|l|nr`. `v == "nr"` is only a pipeline convention (`deterministic_merge.NOT_REPORTED_VALUE`, the prefill, `extraction_report.py`). The validator accepts `nr`/`nr`/`[]`, `nr`/`h`/`[S1]`, a real value with `c=nr`, an empty `v`, and a real value with `c=h` and no `loc` alike. Downstream specs (multiagent-extraction R2.1–2.4: verified vs unverified absence; agreement-statistics 1.3; evaluation-harness 3.3; reviewer-ui 4.4) already assume a meaningful `nr`.
- **Stale and misleading contract artefacts.** `configs/agent_schema.json` `policies`/`extraction_rules` are never sent to the model (only getters in `agents/validator.py`) and describe a different contract (`s: not_reported`, `v: null`, integer `c`). Fallback author/year prefills (paper id, PDF-metadata year, filename year…) cite no evidence yet are labelled confidence `"h"`. `num_chunks=1` raises `ZeroDivisionError` in `_get_domain_to_chunk` at config load. `src/pipeline/README.md` documents a `generate_qc_report` that no longer exists (it is `generate_flagged_fields_report`).

## Current State
See `.kiro/specs/provenance-schema-v2/investigation-2026-10-12.md` (cross-cutting findings, W1, W3) for the measured facts. Code: `src/pipeline/pdf_processor.py` (`process_pdf`, `RepairRetryLoop.extract_with_repair`, `_check_and_mitigate_budget`, `_collect_protected_evidence_ids`), `src/pipeline/validator.py` (`validate_chunk_output`, `_validate_extraction_item`, `reconstruct_fields`), `src/pipeline/evidence_index.py` (`select_paper_evidence`, `serialise_evidence_package`), `src/pipeline/deterministic_merge.py`, `src/agents/openai/{api_client,prompts}.py`, `src/agents/validator.py`, `configs/agent_schema.json`, `src/utils/config_utils.py` (`_get_domain_to_chunk`), `src/pipeline/extraction_report.py`.

## Desired Outcome
1. Every evidence id a model cites is validated against **the evidence actually present in that call's prompt** (the *shown set*, after selection and token-budget mitigation), with the shown set computed in one place, immutable per call, and exposed so that provenance can record exactly what each call could see. A cited id outside the shown set is a validation error handled by the existing repair loop, exactly like an unknown id today. Failure semantics stay a pipeline concern, **independent of whether provenance is enabled**.
2. A written, enforced **missing-value contract**: what the model must emit for "not reported" (value token, confidence, `loc` meaning — the evidence ids where absence was checked), how the validator treats each combination (accept / normalise / reject), how the deterministic merge and the prefill produce `nr`, and how a pipeline-synthesised `nr` (empty `v` normalised by the merge, prefill fallback) is distinguishable from a model-asserted one. The prompt states the contract (a declared prompt-content change; the pinned prefix goldens in `tests/src/provenance/test_provenance_prompt_prefix_regression.py` move once, deliberately).
3. The contract artefacts agree with each other and with the code: `agent_schema.json` carries only what is sent or enforced (or is reconciled with the real contract); fallback prefills carry a confidence that does not overstate (they cite no evidence); `num_chunks=1` either works or fails with a clear configuration error; the pipeline README is corrected.

## Recommended defaults (the requirements/design phases confirm or amend each with a recorded rationale)
- Absence token: exactly `"nr"` after strip/casefold; empty `v` is normalised to `nr` (existing merge behaviour). Confidence `nr` ⇔ value `nr` (a real value with `c=nr`, or `nr` with `c∈{h,m,l}`, is a validation error handled by repair). `nr` with non-empty `loc` = verified absence (ids where the absence was checked, must be shown ids); `nr` with empty `loc` = unverified absence. A pipeline-synthesised `nr` is marked as such in the reconstructed field (a field the provenance layer can read) rather than inferred.
- Shown set = the exact ids serialised into the prompt of that call (post-mitigation); repair prompts re-use the original call's shown set; synthesis uses its own. Validation never consults the full map except for a distinct, clearly worded error when an id is in the map but not shown (so repair can say "id X exists but was not provided").
- Fallback author/year prefills get confidence `l` (or `nr` for the paper-id author fallback — design decides) instead of `h`.

## Scope
- **In**: shown-set computation and per-call validation; exposing the shown set per call; the missing-value contract (prompt text, schema semantics, validator rules, merge/prefill behaviour, synthesised-`nr` marker); `agent_schema.json` reconciliation; fallback confidence; `num_chunks=1`; README/CHANGELOG/steering/test updates; tests (including a regression that cited-but-unshown ids now trigger repair, and a golden prefix update).
- **Out**: any provenance/graph/schema change (owned by `provenance-schema-v2`, which consumes the shown set and the synthesised-`nr` marker); tolerance policies for one bad id among 62 fields (retain-and-downgrade, case/whitespace canonicalisation) — explicitly not decided here; changing the 62-field extraction map; multi-agent routing/verification (multiagent-extraction, evidence-routing); the OpenAI client/transport.

## Boundary Candidates
- Shown-set computation (evidence selection + mitigation) vs validation vs repair-prompt wording.
- Contract definition (prompt + schema + validator) vs its consumers (merge, prefill, exporter, report).
- Pipeline behaviour vs provenance recording (this spec changes extraction acceptance; provenance only observes).

## Out of Boundary
- `provenance-schema-v2` records what this spec computes; it must not re-derive the shown set or re-interpret `nr` on its own.
- Prompt-cache stability: the shared prefix must stay byte-identical across a paper's calls; the static contract text is part of that prefix by design, variable material goes after it.

## Upstream / Downstream
- **Upstream**: `provenance-core` (implemented). **Downstream**: `provenance-schema-v2` (shown-set lineage, synthesised-`nr` origin, `answer` classification), multiagent-extraction (R2.x absence taxonomy), evaluation-harness, agreement-statistics, reviewer-ui.

## Constraints
- Python 3.12.x, `src/` layout; dependency direction rules (`agents` must not import `pipeline`/`quality_control`/`pdf_extractor`); `pdf_processor` keeps `provenance=None` behaviour byte-identical *except* where this spec deliberately changes extraction (declare each change).
- Heavy optional deps stay lazy. Config keys registered in `_ALL_KNOWN_TOP_LEVEL_KEYS`. Never read `agent_schema.json` outside `AgentSchemaValidator`.
- No real patient data in fixtures; real model output does not exist in the repo — tests use stubs; do not invent frequencies.
