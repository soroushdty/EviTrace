# Research & Design Decisions — multiagent-extraction

## Summary

- **Feature**: `multiagent-extraction`
- **Discovery Scope**: Extension (integration-focused) over an existing single-agent extraction pipeline, with two completed upstream specs supplying hard contracts.
- **Key Findings**:
  - The existing extraction path already satisfies most of multiagent R12.1–R12.3 and R12.6–R12.7 mechanically: compact output (`i`, `v`, `loc`, `c`), local validation against the expected field-index set, key schema, confidence enum, and evidence-identifier membership (`src/pipeline/validator.py`), plus a bounded malformed-output repair loop (`RepairRetryLoop`, `src/pipeline/pdf_processor.py:471`). What is genuinely missing is *route-derived* packs, mandatory citation enforcement (R12.5), and a short quote in the answer contract.
  - `_shared_paper_prefix` (`src/agents/openai/prompts.py:27`) wraps exactly one variable — the single serialized paper package. `evidence-routing` already made that package route-priority aware while keeping it one-per-paper. Route material for Agent 1A therefore has to be appended **after** the prefix, never inside it. This is the only viable placement that preserves cache stability, and it is what makes R12 achievable without touching the prefix at all.
  - `RepairRetryLoop` is a *malformed-output* repairer keyed on parse and schema failures. Multiagent R18's Agent 3 is a *wrong-answer* repairer keyed on QC and verifier verdicts. Different triggers, different inputs, different action vocabulary (`revised|kept_original|marked_not_reported|manual_review`). They must be stacked, not merged.
  - `agreement-statistics` stabilizes `RaterFieldOutput` as the input contract that this spec populates, and publishes results under `metrics_hierarchy["inter_rater_agreement"]`. This spec is the only producer of two-rater data and the only consumer of the published statistics for escalation purposes.
  - `evidence-routing` publishes `ExtractionPack`, `AdjudicatedRoute`, and `RouteVerdict`, stores bare `S/F/T%06d` identifiers, and owns `token_budget.prune_items_by_priority` with `DiscardRecord`. Nothing in this spec may re-implement pruning, re-scope identifiers, or reuse `routing_prompts`.

## Research Log

### Existing extraction call path and its prompt cache

- **Context**: Four new agent roles multiply prompt variants; the constraint is that `_shared_paper_prefix` stays byte-identical across warmup, chunks, and synthesis.
- **Sources Consulted**: `src/agents/openai/prompts.py`, `src/agents/openai/api_client.py`, `src/agents/openai/telemetry.py`, `src/pipeline/pdf_processor.py::process_pdf`, `evidence-routing/design.md` §routing_prompts.
- **Findings**:
  - `build_user_message` emits `_shared_paper_prefix(source_package)` then the extraction map then optional prior context. Every chunk for one paper receives byte-identical `source_package`, which is what produces the cache hit.
  - `evidence-routing` established the precedent for a *separate* prompt module (`routing_prompts.py`) with its own `_shared_routing_prefix`, plus a guard test asserting the module never references `_shared_paper_prefix`.
  - `TelemetryCollector.check_prefix_drift` already exists and is extended per stage label; `evidence-routing` added `locator` and `counterfactual_locator`.
- **Implications**: Agent 1A extends `build_user_message` with an optional routed-evidence block placed after the shared prefix. Agents 1B, 1c, and 3 get their own module with three independent stable prefixes, following `routing_prompts` exactly. Three new telemetry stage labels are added.

### Answer contract and what "compact" already means

- **Context**: R12.2 requires field index, value, evidence identifiers, short quote, and confidence. The shipped contract has no quote.
- **Sources Consulted**: `src/pipeline/validator.py` (`REQUIRED_KEYS = {"i","v","loc","c"}`, `ALLOWED_CONFIDENCE = {"h","m","l","nr"}`), `configs/agent_schema.json` output key legend.
- **Findings**: `agent_schema.json`'s legend already names an `e` (evidence ids) and `ra` (rationale) key that the shipped validator does not use; the live contract is the four-key form. Adding a quote key changes the strict JSON schema sent to the provider and therefore changes every existing chunk response shape.
- **Implications**: The quote is added as one optional key (`q`) on the answer object, defaulting absent. Making it optional keeps the existing four-key responses valid, so the change is additive and the existing validation and reconstruction paths keep working. R14.3's fuzzy quote match is conditional on the quote being present, which the requirement already words as "when quotes are provided".

### Quote-to-evidence fuzzy matching

- **Context**: R14.3 needs a deterministic, dependency-free similarity measure between a short model-emitted quote and the cited evidence text.
- **Sources Consulted**: `src/text_processing/` (`normalizers.py`, `tokenizers.py`, `matchers.py`), CLAUDE.md dependency rules.
- **Findings**: `text_processing` is a standalone package with normalizers (casefolding, whitespace collapse) and matchers, already used by the pipeline, with no heavy optional dependency at module level. `pipeline` importing `text_processing` is permitted; `text_processing` importing `quality_control` is not, and nothing here inverts that.
- **Implications**: Quote matching is built on existing `text_processing` normalization plus a token-level containment/overlap score computed locally. No new dependency, no embeddings, and the score is deterministic — which R14.8 requires.

### Two-rater data and the agreement boundary

- **Context**: R15.1–R15.8 are explicitly out of scope; only R15.9 is in scope.
- **Sources Consulted**: `agreement-statistics/requirements.md` (Requirements 1, 8, 12, 13), `agreement-statistics/design.md` (`RaterFieldOutput`, `ComparisonUnit`, `metrics_hierarchy` shapes, `NO_COMPARISON_DATA`).
- **Findings**: `agreement-statistics` explicitly notes that comparison units are absent in the single-agent pipeline as shipped, and that the IAA stage reports `NO_COMPARISON_DATA` until this spec supplies a second rater. `RaterFieldOutput` carries `rater`, `document_id`, `field_id`, `field_group`, `value`, `evidence_ids`, `confidence`, `support_status`, `not_reported`, `page_index`, `criticality`.
- **Implications**: This spec's only write into the agreement boundary is emitting `RaterFieldOutput` per extractor per field. Its only read is the published per-group statistics for the R15.9 escalation policy and the Open Question 2 release thresholds. Both directions are one-way and neither recomputes anything.

### Routing contracts consumed

- **Context**: Packs and routes must be consumed verbatim.
- **Sources Consulted**: `evidence-routing/design.md` (`ExtractionPack`, `AdjudicatedRoute`, `RouteVerdict`, `RoutingResult`, `prune_items_by_priority`, `DiscardRecord`, `format_evidence_id`).
- **Findings**: `AdjudicatedRoute` already carries `is_critical`, `parser_risk`, `requires_stricter_handling`, `primary_evidence_ids`, `ordered_evidence`, `decision_rule`, and `empty_reason`. `ExtractionPack` carries `field_definitions`, `snippets` (with `text=None` meaning identifier-only), `route_trace`, `parser_risk_flags`, `document_metadata`, and `oversize_field_indices`. `RoutingResult.enabled` is `False` on the disabled path.
- **Implications**: Every gating input this spec needs — criticality, parser risk, stricter handling, route weakness — already exists on `AdjudicatedRoute`. No new signal has to be derived, and no routing type is re-declared. `RoutingResult.enabled == False` is exactly the fallback trigger for Requirement 1.2.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| Deterministic-sandwich stage graph | Every model call fenced by a deterministic stage on both sides; gates decide which fields advance | Matches `evidence-routing`'s established pattern; each stage independently ablatable (needed by `evaluation-harness` R25.3–R25.6); reproducible from recorded inputs | More modules than a single orchestrator function | **Selected** |
| Single orchestrator with inline gating | One `run_multiagent_extraction` function calling agents in sequence | Fewer files | Untestable gates, unablatable agents, no seam for per-stage provenance | Rejected |
| Agent-per-field task graph (`asyncio` fan-out per field) | Each field runs its own 1A→1B→1c→3 pipeline concurrently | Maximum concurrency | Destroys prompt-cache locality; 62 × 4 calls per document is exactly the cost blow-up the gates exist to prevent | Rejected |
| Extend `RepairRetryLoop` to cover wrong answers | Reuse the existing repair loop for both malformed and wrong output | One repair path | Conflates two different triggers, inputs, and action vocabularies; the brief explicitly forbids it | Rejected |

## Design Decisions

### Decision: Route material is appended after the shared paper prefix, never inside it

- **Context**: R12.1 wants extraction from packs; the hard constraint is `_shared_paper_prefix` byte-stability.
- **Alternatives Considered**:
  1. Replace the paper package with the routing pack per chunk — would give each chunk a different prefix and destroy the cache.
  2. Build one merged pack per paper and put it in the prefix — cache-safe, but loses per-field route targeting, which is the point of R12.
  3. Keep the paper package in the prefix and append a per-chunk routed-evidence block after it.
- **Selected Approach**: Option 3. `_shared_paper_prefix` is untouched; the routed-evidence block for the chunk's routing units is emitted immediately after it and before the extraction map.
- **Rationale**: Preserves the cache prefix exactly, keeps route targeting per field, and needs no change to the prefix function at all — the strongest possible form of the constraint.
- **Trade-offs**: The cached prefix stops at the end of the paper package rather than extending through the routed block, so the routed block is paid for on every chunk. It is bounded by the routing token cap and is far smaller than the paper package.
- **Follow-up**: A regression test must assert that with the feature enabled, every chunk prefix for one document remains byte-identical and `_shared_paper_prefix`'s source is unchanged.

### Decision: Resolution of roadmap Open Question 2 — how much dual extraction after calibration

- **Context**: Dual extraction on all 62 fields doubles extraction cost permanently. Running it on nothing makes agreement uncomputable.
- **Alternatives Considered**:
  1. Fixed percentage forever — simple, but spends the same on a group with kappa 0.95 and one with kappa 0.35.
  2. Critical fields only — cheap, but produces no agreement data for the rest, so the reliability claim never generalizes.
  3. Risk-targeted mandatory set plus a graduation rule plus a residual random sample.
- **Selected Approach**: Option 3, as recorded in Requirement 4. Calibration mode dual-extracts everything. Production mode dual-extracts critical fields, weak or stricter-handling routes, parser-risky routes, and any field group that has not *graduated*; a group graduates when it has at least `min_comparison_units` (default **50**) published comparison units and a published value-dimension chance-corrected statistic at or above `release_agreement_threshold` (default **0.80**). A configurable residual fraction (default **0.10**) of already-graduated fields is still dual-extracted, selected deterministically from a recorded seed.
- **Rationale**: 50 units is the smallest count at which a kappa point estimate is not dominated by sampling noise for the small category counts this schema produces, and it is above `agreement-statistics`'s own `min_units` default of 10 — a group must clear the statistic's own defined-ness bar with margin before it stops being measured. 0.80 is the conventional "substantial-to-almost-perfect" boundary in the reliability literature and is the threshold most systematic-review methodology guidance uses for accepting dual extraction as reliable. The residual sample exists because graduation is not permanent: a schema edit, a model change, or a corpus shift can degrade a group, and without ongoing sampling nothing would ever detect it.
- **Trade-offs**: Steady-state cost is roughly 10% of full dual extraction plus the mandatory risk-targeted set, rather than 100%. The cost is slower detection of a regression in a graduated group — bounded by the sampling rate, and surfaced by the same agreement statistics that drove graduation.
- **Follow-up**: Both defaults are configuration, and both are recorded on every dual-extraction decision so that a changed threshold is visible in the audit trail. Changing either re-opens this question.

### Decision: Resolution of roadmap Open Question 3 — which fields require mandatory Agent 1c verification

- **Context**: Verification is the most expensive gate because its input includes alternative evidence. Verifying everything is unaffordable; verifying nothing makes "confidently wrong" undetectable.
- **Alternatives Considered**:
  1. Confidence-threshold only — misses the exact failure mode the verifier exists for, a confidently wrong answer.
  2. Critical fields only — misses non-critical fields that failed QC or on which the extractors disagree.
  3. A mandatory set defined by *signals that already indicate something is wrong*, plus a discretionary budgeted tier.
- **Selected Approach**: Option 3, as recorded in Requirement 8. **Mandatory, never budget-suppressed**: every critical field (including one answered not-reported, because an unverified absence claim on a critical field is the highest-cost silent error in evidence synthesis); every field whose QC issue maps to verification; every field where the two extractors disagree on value or support status; every field whose route carries `requires_stricter_handling`. **Discretionary, budget-suppressed**: non-critical low-confidence answers and non-critical not-reported answers. **Not verified**: everything else.
- **Rationale**: Each mandatory trigger is a signal that a deterministic stage has already found something wrong or that the field's cost of error is high. Confidence alone is deliberately excluded from the mandatory set because self-reported confidence is exactly the signal that fails on the target failure mode. Letting the budget suppress a critical-field verification would make the budget silently redefine the safety property, so R8.5 requires the bound to be exceeded and recorded instead.
- **Trade-offs**: A document with many critical fields can exceed its verification call bound. That is a recorded, visible cost overrun rather than a silent coverage gap.
- **Follow-up**: The critical-field designation comes from `corpus-and-schema-builder`; until it exists, the documented default criticality applies and is recorded as defaulted, so the mandatory set is conservative rather than empty by accident.

### Decision: Agent 3 stacks above the existing malformed-output repair loop

- **Context**: `RepairRetryLoop` already exists and repairs parse/schema failures.
- **Selected Approach**: `RepairRetryLoop` stays exactly where it is, inside the extraction call. Agent 3 sits above it, triggered only by QC or verifier verdicts on schema-valid output, with its own four-action vocabulary and its own prompt.
- **Rationale**: The triggers are disjoint by construction — Agent 3 can never see schema-invalid output, because such output never leaves the extraction call. R11.8 encodes this as a requirement so it cannot be quietly merged later.
- **Trade-offs**: Two repair layers to reason about; mitigated by the disjoint-trigger invariant and a test asserting it.

### Decision: Answer quality control lives outside `src/agents/`

- **Context**: The dependency-direction test forbids `agents` importing `quality_control`, `pipeline`, or `pdf_extractor`.
- **Selected Approach**: All deterministic stages (extraction QC, gating policies, adjudication) live under `src/pipeline/multiagent/`; only prompt building, schema validation, and provider calls live under `src/agents/`.
- **Rationale**: Mirrors the split `evidence-routing` used for `routing_prompts`/`routing_client` versus `pipeline/routing/`, and keeps the AST-enforced direction intact.
- **Trade-offs**: Answer QC does not live in `src/quality_control/`, which handles *parser branch* QC. Co-locating them would force `quality_control` to import `pipeline` types, which is forbidden. The name overlap is documented rather than resolved by a forbidden import.

### Decision: Build the deterministic gates rather than adopt a workflow framework

- **Context**: The stage graph looks like a candidate for an agent-orchestration library.
- **Selected Approach**: Build. Plain `asyncio` sequencing plus frozen dataclasses, matching the existing pipeline and `evidence-routing`.
- **Rationale**: Every gate here is a project-specific policy over project-specific records; a framework would contribute scheduling this pipeline already has (`asyncio.gather` under a shared semaphore) while adding a dependency, a second concurrency model, and an opaque retry ladder that would compete with the existing one. No new third-party dependency is introduced.

## Risks & Mitigations

- **Cost blow-up** — worst case is four model passes per field. Mitigated by per-agent per-document call bounds (R4.7, R8.3, R12.7), by gating being a requirement rather than an optimization, and by graduation removing dual extraction from stable groups.
- **Prompt-cache regression** — four new prompt shapes could perturb the shared prefix. Mitigated by placing all new material after the prefix, by giving each new agent its own module and prefix, and by a guard test asserting the new prompt module never references `_shared_paper_prefix`.
- **Correlated errors between 1A and 1B** — a second call to the same model with the same framing measures nothing. Mitigated by R3.3's decorrelation levers being recorded, so a run where no lever was applied is visibly weaker evidence.
- **Quote-match false negatives** — an over-strict threshold turns every valid answer into an unsupported-high-confidence issue and floods verification. Mitigated by the threshold being configuration, by the match running on normalized tokens rather than raw strings, and by quote absence being explicitly not a mismatch.
- **Agreement escalation feedback loop** — escalation changes which fields are dual-extracted, which changes the comparison set, which changes the statistic. Mitigated by R7.2's requirement to record the statistic, unit count, and threshold at decision time, so the sample composition behind any escalation is reconstructable.
- **Silent no-op before upstreams land** — with routing disabled and no agreement results, this feature could quietly degrade to today's behavior. Mitigated by R1.2, R4.5, and R7.3 all requiring the degraded state to be recorded as a named condition rather than defaulted through.

## References

- `.kiro/specs/evidence-routing/requirements.md`, `design.md` — consumed contracts: `ExtractionPack`, `AdjudicatedRoute`, `RouteVerdict`, `prune_items_by_priority`, `DiscardRecord`, `format_evidence_id`, `routing_prompts` precedent.
- `.kiro/specs/agreement-statistics/requirements.md`, `design.md` — consumed and produced contracts: `RaterFieldOutput`, published `metrics_hierarchy["inter_rater_agreement"]`, `UndefinedReason`, `PageAgreementRecord` page flags.
- `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` §Requirements 12–18 — source clauses.
- `.kiro/steering/roadmap.md` — Open Questions 2 and 3, cross-cutting NFRs, standing product boundaries.
- `src/agents/openai/prompts.py`, `src/pipeline/pdf_processor.py`, `src/pipeline/validator.py`, `src/pipeline/token_budget.py`, `src/text_processing/` — existing implementation surfaces.
