# Brief: evidence-routing

## Problem
Reviewers extracting 62 fields from a biomedical paper cannot tell *why* a given
passage was shown to the extractor. Today the evidence handed to the LLM is
selected by a generic section-score heuristic that knows nothing about the field
being asked. When the heuristic misses the paragraph that actually answers a
field, the extractor either hallucinates from adjacent text or reports
not-reported — and nothing in the audit trail records that the *location* choice,
not the extraction, was the failure. The roadmap identifies this routing layer as
the genuinely new core idea of `evitrace_multiagent.md`.

## Current State
- `src/pipeline/evidence_index.py` parses GROBID TEI into a ranked item index
  (`_section_score`, `_build_items_from_tei`) and builds per-chunk packages
  (`build_chunk_evidence_package`, `build_paper_evidence_package`). Ranking is
  global and field-agnostic — there is no field-specific retrieval hint layer
  (multiagent R7.2/R7.3), no table index or caption index distinct from the flat
  item list (R7.1).
- `src/agents/` contains exactly one provider (`openai/api_client.py`,
  `prompts.py`, `telemetry.py`) plus `validator.py`. There is no locator agent,
  no counterfactual agent, and no per-agent prompt/schema plumbing.
- `src/pipeline/orchestrator.py` parallelizes over PDFs and chunks. There is no
  notion of an agent stage graph.
- Nothing produces a route object, route QC verdict, or route provenance record.
  Grep for `locator|counterfactual|verifier` across `src/` returns zero hits.

## Desired Outcome
Every extraction field carries an explicit, audited route: primary and backup
evidence IDs, page/section, confidence, risk flags, and rationale (R8.3), each
route checked for coverage and plausibility before extraction runs (R9.1–R9.5),
weak or critical routes challenged by a counterfactual locator (R10.1–R10.5), and
a deterministic adjudicator that merges all candidate sources into a token-capped
extraction pack with route provenance preserved (R11.1–R11.6).

## Approach
Three-layer split mirroring the doc: deterministic local retrieval first (R7),
one LLM locator that only *points* and never extracts values (R8.2), then
deterministic QC/adjudication around it (R9, R11), with the LLM counterfactual
(R10) invoked only on routes QC flags. Keeping the deterministic layers on both
sides of each LLM call bounds cost and keeps the audit trail reproducible.

## Scope
- **In**: field-specific retrieval hints and section/paragraph/table/caption
  indices (R7); Agent 0 locator contract, route schema, retry/repair path,
  raw-output persistence (R8); route QC checks and escalation routing (R9);
  Agent 0c counterfactual locator (R10); deterministic route adjudication and
  token-capped pack assembly with discarded-ID recording (R11); consuming
  parser-risk flags to enforce stricter extraction/verification when a critical
  field routes to a parser-risky page (multiagent R5.6) and to trigger a
  parser-counterfactual audit or human-review escalation when parser agreement
  is low or table/numeric content is at risk (multiagent R5.8).
- **Out**: extraction of any field value (multiagent R12+, owned by
  `multiagent-extraction`); parser/canonical-document work (R3–R6, already built
  and closed out as a direct-implementation candidate); agreement statistics
  (R15, owned by `agreement-statistics`); any UI surfacing of routes (R19).

## Boundary Candidates
- Deterministic local retrieval/index build vs. LLM locator invocation.
- Route object schema and validation vs. route escalation policy.
- Counterfactual locator (LLM, optional, sampled) vs. adjudication (local,
  always-on, deterministic).
- Extraction-pack assembly and token capping vs. the extraction agents that
  consume packs.

## Out of Boundary
- Deciding field *values*, confidence in values, or answer correctness.
- Replacing or re-tuning the `token-efficient-extraction` budget thresholds.
- Provenance node identity and graph storage — defined once in `provenance-core`
  and consumed here, never redefined.

## Upstream / Downstream
- **Upstream**: `provenance-core` (evidence node identity, claim→evidence links);
  existing `src/pipeline/evidence_index.py`, `extraction_pipeline.py`,
  `src/quality_control/` reconciled `UnifiedRecord`.
- **Downstream**: `multiagent-extraction` (consumes extraction packs and route
  traces), `evaluation-harness` (R25.3 ablation of Agent 0c), `reviewer-ui`
  (displays route rationale and evidence provenance).

## Existing Spec Touchpoints
- **Extends**: `xtrace-toolkit` R-LLM-7 (LLM input built only from the anchored
  evidence bundle) — routing is the mechanism that makes this field-aware.
  This spec covers the part of the multiagent doc that `xtrace-toolkit` does not.
- **Adjacent**: `provenance-core` (owns evidence identity), `agreement-statistics`
  (owns kappa/alpha computation), `privacy-core` (owns the LLM gateway that
  locator calls will pass through).

## Constraints
- `_shared_paper_prefix` in `src/agents/openai/prompts.py` must stay byte-identical
  across warmup, chunks, and synthesis for a PDF. Adding locator and counterfactual
  prompt variants multiplies prompt shapes and risks cache thrash; new agents need
  their own stable prefixes rather than perturbing the existing one.
- Cost: each added agent is another full LLM pass per field/field-group; Agent 0c
  must be gated (R10.1) rather than universal.
- Dependency direction (`tests/test_dependency_directions.py`): `agents` must not
  import `quality_control`/`pipeline`/`pdf_extractor`. Route QC is deterministic
  and belongs on the `quality_control`/`pipeline` side of that line.
- New top-level YAML keys must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS`
  (`src/utils/config_utils.py`); heavy optional deps stay lazily imported.
