# Research & Design Decisions — evidence-routing

## Summary

- **Feature**: `evidence-routing`
- **Discovery Scope**: Complex Integration — a new routing layer inserted into an existing, working extraction pipeline, consuming two in-flight upstream specs (`provenance-core`, `agreement-statistics`) and extending a completed one (`token-efficient-extraction`).
- **Key Findings**:
  - The evidence index already assigns the identifiers this spec must consume verbatim (`S%06d`, `F%06d`, `T%06d`) and already carries `type`, `section_path`, `page`, `coords`, `xpath`, `score`. The four indices multiagent R7.1 asks for are **views over the existing item list**, not a second parse.
  - The token-efficient-extraction pruning is split across two modules and only one of the three call sites is evidence-ID aware. **No discarded evidence identifier is recorded anywhere today.** R11.5 is therefore a real gap, and the correct move is to promote the existing ID-aware pruner rather than write a new one.
  - `_shared_paper_prefix` wraps exactly one variable: the serialized evidence package. Its byte-stability across chunks depends on `build_paper_evidence_package` producing one package per paper. Routing can therefore change *which* items enter that package without touching cache stability, as long as it produces one package per paper.
  - `parser_risk` does not exist in `src/` at all. `agreement-statistics` will introduce `PageAgreementRecord` with `parser_risky`, `skip_parser_counterfactual`, and `counterfactual_audit_recommended`. This spec is the first consumer and must tolerate the signal being absent.

## Research Log

### Existing evidence index and its identifiers

- **Context**: multiagent R7.1 asks for section, paragraph, table, and caption indices; multiagent R8.5 requires every locator identifier to refer to a canonical document object; `provenance-core` requires the pipeline identifier be adopted verbatim.
- **Sources consulted**: `src/pipeline/evidence_index.py`, `.kiro/specs/provenance-core/design.md`.
- **Findings**:
  - `_build_items_from_tei` emits one flat list. Sentence and standalone-paragraph items share the `S%06d` counter; figure captions use `F%06d`; tables use `T%06d`. Abstract paragraphs and the title also consume `S` identifiers.
  - Each item carries `id`, `type`, `section_path`, `page`, `coords`, `xpath`, `text`, `source_pdf`, `score`, `annotations`. `page` may be `None` when TEI carries no coordinates.
  - `section_path` is sticky: it tracks the last-seen `<head>` and therefore leaks across divs that lack a heading. Routing must treat `section_path` as a hint, not as ground truth.
  - `EvidenceBundle.evidence_map` already provides id-to-item lookup, so identifier validation is O(1) and needs no new structure.
  - `provenance-core` defines `node_id = f"{source_id}#{local_id}"` with `local_id` the pipeline string verbatim, and exports `format_evidence_id` as the only sanctioned constructor.
- **Implications**: the index builder is a **projection**, not a parser. Paragraph and sentence entries are distinguished by TEI origin rather than by identifier prefix, so the projection must record the originating structure explicitly. All routing-internal references use the bare local identifier; provenance emission wraps it once through `format_evidence_id`.

### Token budget and pruning ownership

- **Context**: multiagent R11.3 and R11.5 overlap the completed `token-efficient-extraction` work; the roadmap requires extending it rather than duplicating it.
- **Sources consulted**: `src/pipeline/token_budget.py`, `src/pipeline/pdf_processor.py`.
- **Findings**:
  - `token_budget.py` owns `estimate_tokens` (`len(text) // 4`), `check_budget`, `_prune_evidence`, `apply_mitigation`, `load_budgets`, `DEFAULT_BUDGETS`, and `TokenBudgetExceededError`. Its pruning is **flat-text**: it splits the evidence section on `"\n\n"` and has no notion of an evidence identifier.
  - `pdf_processor.py` owns `_prune_evidence_json_preserving_protected`, the only identifier-aware pruner. It parses the `build_paper_evidence_package` JSON shape, partitions into protected and unprotected, drops unprotected from the tail of the id-sorted order, and re-serializes. It is used by exactly one call site (synthesis) via `_check_and_mitigate_budget(protected_evidence_ids=...)`.
  - Three call sites exist: `extraction_chunk` and `validation_repair` (flat-text path) and `synthesis` (identifier-aware path).
  - Neither path records what it dropped. Pruning outcomes surface only as counts in `logger.warning`.
  - Three different default pairs for `max_evidence_items_per_chunk` / `max_evidence_chars_per_chunk` exist (config.yaml, `load_openai_config`, and `process_pdf`'s inline fallbacks).
- **Implications**: the identifier-aware pruner is the correct base to extend, but it lives in the wrong module and is hard-wired to one protection rule. Promote it into `token_budget.py`, generalize "protected set" into "priority order plus non-droppable set", and add a structured discard record. The existing synthesis behavior becomes one configuration of the generalized function, which keeps the completed spec's thresholds and outcomes intact.

### Prompt cache stability

- **Context**: adding two agents multiplies prompt shapes; the roadmap and steering both name `_shared_paper_prefix` stability as a hard constraint.
- **Sources consulted**: `src/agents/openai/prompts.py`, `src/agents/openai/api_client.py`, `src/agents/openai/telemetry.py`.
- **Findings**:
  - `_shared_paper_prefix(source_package)` contains only fixed instruction lines plus the serialized package. Nothing document-variable beyond the package itself.
  - `paper_cache_key` hashes the package; `TelemetryCollector.check_prefix_drift` already detects a per-stage prefix changing within a run.
  - `build_paper_evidence_package` exists precisely so one package serves every chunk. Its deprecated per-chunk sibling is retained only for tests.
- **Implications**: routing must not add a per-chunk variable into the shared prefix. Two safe moves: (a) give each routing agent its own separate stable prefix in a separate prompt module, and (b) let routing influence *selection* inside the single per-paper package, which changes the package for a document but keeps it identical across that document's warmup, chunks, and synthesis. Both preserve the invariant. The drift detector then becomes a free regression test for the new stages.

### Parser-risk signal availability

- **Context**: multiagent R5.6 and R5.8 are scoped to this spec as consumption clauses.
- **Sources consulted**: `.kiro/specs/agreement-statistics/design.md` and `requirements.md`, `src/quality_control/models.py`, `src/pipeline/extraction_pipeline.py`.
- **Findings**:
  - `agreement-statistics` publishes `PageAgreementRecord` per page carrying `parser_risky`, `skip_parser_counterfactual`, `counterfactual_audit_recommended`, the failing metric names, the thresholds in force, and `unavailable_signals`. It explicitly refuses to trigger any action itself.
  - Its design freezes the `PageAgreementRecord` shape and the three flag names precisely because this spec consumes them.
  - Nothing equivalent exists in `src/` today. The nearest artifact, `unified.content["page_routing"]`, is per page but carries only extractor selection and a routing reason, no risk.
  - Evidence items carry `page` from TEI coordinates, so a page-index join between evidence and page records is feasible, but `page` is `None` for coordinate-less items.
- **Implications**: consume through a narrow adapter with a documented empty default, so this spec is implementable and testable before `agreement-statistics` lands. Items with `page is None` resolve to parser-risk `unknown`, which requirement 7.6 already forbids treating as safe.

### Insertion point in the pipeline

- **Context**: the routing stage needs structured evidence items and the full field list at the same time.
- **Sources consulted**: `src/pipeline/orchestrator.py`, `src/pipeline/pdf_processor.py`, `src/pipeline/extraction_pipeline.py`.
- **Findings**: `process_pdf` builds the evidence bundle, computes valid location identifiers, prefills fields 1 and 2 from TEI, and only then serializes the package. Between the bundle build and the package build is the single point where structured items, the field list, the manifest, the API semaphore, and the telemetry collector all coexist.
- **Implications**: routing is one awaited call inserted at that point, returning a result object. Everything downstream stays string-shaped as it is today. Resumability piggybacks on the existing manifest and on a routing artifact cache keyed the same way the evidence cache is keyed.

### Raw model output persistence

- **Context**: multiagent R8.7 and R10.6 require the raw locator and counterfactual output to be stored.
- **Sources consulted**: `src/utils/logging_utils.py`, `src/utils/config_utils.py`.
- **Findings**: `log_model_response` writes a full response to disk only when `debug_artifact_dir` is set **and** the logger's effective level is DEBUG. `debug_artifact_dir` is read from the OpenAI config but is never produced by `load_openai_config`, so today it is always `None` and no raw output is ever persisted.
- **Implications**: routing cannot rely on the debug path. It must persist raw locator and counterfactual output unconditionally into its own run-scoped artifact directory, independent of log level.

## Architecture Pattern Evaluation

| Option | Description | Strengths | Risks / Limitations | Notes |
|--------|-------------|-----------|---------------------|-------|
| New top-level `src/evidence_routing/` package | Routing as a sibling of `pipeline`, `agents`, `quality_control` | Clean naming; matches how `provenance` and `privacy` are planned | Needs `pipeline.token_budget` and `pipeline.evidence_index`; `pipeline.pdf_processor` needs routing — a package-level cycle the AST dependency test would have to be taught to permit | Rejected |
| Subpackage `src/pipeline/routing/` | Routing inside the package that already legally depends on `agents`, `quality_control`, and `evidence_index` | No new dependency-direction rule; no cycle; reuses existing config and manifest plumbing | Slightly less prominent naming | **Selected** |
| Routing inside `src/quality_control/` | Route QC is quality control | Route QC is deterministic and thematically fits | `quality_control` may not import `agents` or `pipeline`; the locator stage would have to live elsewhere, splitting one feature across two packages | Rejected |
| Routing agents inside `src/pipeline/routing/` | Put prompt building and API calls next to the routing logic | Fewer files | Would put model-call code outside `src/agents/`, breaking the existing convention that every provider call lives in `agents` | Rejected |

## Design Decisions

### Decision: Routing lives in `src/pipeline/routing/`, its agents in `src/agents/openai/`

- **Context**: dependency direction is enforced by `tests/test_dependency_directions.py`; `agents` must not import `quality_control`, `pipeline`, or `pdf_extractor`.
- **Alternatives considered**: new top-level package; placement in `quality_control`.
- **Selected approach**: deterministic routing logic (indices, hints, retrieval, route QC, adjudication, pack assembly, provenance emission, orchestration) in `src/pipeline/routing/`; the two model-facing modules (`routing_prompts.py`, `routing_client.py`) plus a `RoutingAgentSchemaValidator` in `src/agents/`. Routing calls agents with plain serializable data only.
- **Rationale**: preserves every existing dependency rule with no new exemption, and keeps the "all provider calls live in `agents`" convention intact.
- **Trade-offs**: the feature spans two packages. Mitigated by making the seam a single function-call boundary with plain-dict payloads.
- **Follow-up**: extend `tests/test_dependency_directions.py` to assert `agents.openai.routing_*` imports nothing from `pipeline`.

### Decision: Promote the identifier-aware pruner into `token_budget.py` and generalize it

- **Context**: multiagent R11.3 and R11.5 overlap the completed token-efficient-extraction pruning; the roadmap forbids a second implementation.
- **Alternatives considered**: (1) new pack-local pruner; (2) call `apply_mitigation` and post-hoc diff to infer discards.
- **Selected approach**: move `_prune_evidence_json_preserving_protected` into `token_budget.py` as `prune_items_by_priority(items, *, budget, other_parts, priority, non_droppable) -> PruneOutcome`, returning kept items plus a structured discard list. `pdf_processor._prune_evidence_json_preserving_protected` becomes a thin wrapper that maps `protected_ids` onto `non_droppable` and preserves its current return signature and behavior exactly.
- **Rationale**: one pruner, one estimator, one budget table. The synthesis path gains discard records for free. Option 2 was rejected because flat-text mitigation destroys item boundaries, so the diff would be unreliable.
- **Trade-offs**: touches a completed spec's module. Bounded by requiring the existing synthesis behavior to be byte-identical under test.
- **Follow-up**: a characterization test pinning the current synthesis pruning output before the move, re-run after.

### Decision: Field-group routing units with per-field route objects

- **Context**: roadmap Open Question 5.
- **Alternatives considered**: per-field routing (62 locator calls per paper); one whole-document routing call (1 call, 62 routes in one response).
- **Selected approach**: one locator call per domain group (13 groups), each returning one route object per field in the group. Failed routes are re-requested per field. Granularity is configurable.
- **Rationale**: 13 calls per paper is affordable and keeps each response small enough to validate and repair cheaply; per-field route objects preserve the per-field audit trail multiagent R8.3 requires. A single whole-document call produces a response large enough that one schema failure costs all 62 routes.
- **Trade-offs**: groups vary from 2 to 8 fields, so call cost is uneven.
- **Follow-up**: record locator call count per document in telemetry so the choice can be re-evaluated against real cost.

### Decision: Identifiers always survive; quotes are trimmed first

- **Context**: roadmap Open Question 6.
- **Alternatives considered**: quote everything up to the cap; identifiers only, no quotes.
- **Selected approach**: quotes attach only to primary and promoted evidence, bounded by a configurable per-snippet character limit; everything else is identifier plus kind, page, and section. Under token pressure the trimming order is: shorten non-critical quotes, drop non-critical quotes entirely, drop lowest-priority identifiers, and never drop a critical field's primary identifier.
- **Rationale**: an identifier is what makes a route auditable and what the downstream extractor must cite; a quote is a convenience that saves the extractor a lookup. Identifiers are also two orders of magnitude cheaper than the text they name.
- **Trade-offs**: an extractor that receives identifiers without text must resolve them against the shared package. That is already how the compact `loc` output schema works, so no new capability is required.

### Decision: Canonical table source resolved by fallback, with the substitution recorded

- **Context**: roadmap Open Question 1.
- **Alternatives considered**: always the scholarly parser; always the structural block parser; per-document adjudication.
- **Selected approach**: the scholarly parser's TEI is canonical for body text, section structure, and section paths, and for tables when it yields non-empty structured rows. When it does not, or when the page carries a published table-detection disagreement, the structural block parser's table candidate supplies the table content and the index entry records `content_source`.
- **Rationale**: TEI table extraction on complex biomedical tables frequently yields empty `<row>` sets while the structural parser still recovers a usable grid. Making the fallback explicit and recorded is strictly better than either unconditional choice, and it keeps the decision auditable rather than silent.
- **Trade-offs**: two table representations can disagree in cell segmentation. The record of which parser supplied the content is what makes that disagreement visible.

### Decision: Routing influences the shared per-paper package rather than replacing it

- **Context**: extraction packs must reach the extractor without breaking prompt-cache stability, and value extraction is owned downstream.
- **Alternatives considered**: build one pack per field group and send it as the chunk prompt (breaks the shared prefix); produce packs as pure artifacts nothing reads (orphaned work).
- **Selected approach**: adjudicated routes supply an optional deterministic priority map to `build_paper_evidence_package`, which still emits exactly one package per paper. Extraction packs are additionally persisted as run artifacts for `multiagent-extraction` to consume.
- **Rationale**: the cache invariant is "identical across warmup, chunks, and synthesis for the same document", not "identical across documents". Priority-informed selection satisfies it while making routing change what the extractor actually sees today.
- **Trade-offs**: the value of routing is realized in two steps, the second owned downstream.
- **Follow-up**: `TelemetryCollector.check_prefix_drift` serves as the runtime guard; add an explicit test that all chunk prefixes for one document are byte-identical with routing enabled.

### Decision: Parser risk consumed through a narrow adapter with an empty default

- **Context**: `agreement-statistics` is specced but not implemented; this spec must be implementable now.
- **Selected approach**: a `ParserRiskView` adapter reads a mapping of page number to page-agreement record and exposes `risk_for(page)` returning risky, safe-to-skip, audit-recommended, or unknown. Its default construction is empty, yielding `unknown` for every page.
- **Rationale**: requirement 7.6 already mandates that absent signals mean unknown rather than safe, so the empty default is the specified behavior, not a stub.
- **Follow-up**: when `agreement-statistics` lands, only the adapter's constructor changes.

## Risks & Mitigations

- **Cost multiplication from two new agents** — gate the counterfactual locator by configured rules, cap counterfactual calls per document, and record every call with its triggering rule so the gate can be tuned from data.
- **Prompt-cache thrash from new prompt shapes** — separate prompt module with its own stable prefixes, never touching `_shared_paper_prefix`; existing prefix-drift detection extended to the new stages.
- **Regressing the completed token-efficient-extraction behavior** — characterization test pinning current synthesis pruning output before and after the pruner is promoted.
- **`section_path` stickiness producing wrong section labels in routes** — index entries record whether the section path came from an explicit heading or was inherited, and route QC treats inherited paths as weaker evidence of section identity.
- **Locator hallucinating identifiers** — every identifier is checked against `EvidenceBundle.evidence_map`; unknown identifiers are dropped individually and recorded as issues rather than failing the route.
- **Upstream specs not yet implemented** — both `provenance-core` emission and `agreement-statistics` consumption sit behind narrow adapters with documented no-op defaults, so routing runs and is testable without either.

## References

- `.kiro/specs/archive/original-idea-documents/evitrace_multiagent.md` — multiagent R5, R7–R11, the source requirements.
- `.kiro/specs/provenance-core/design.md` — evidence node identity, `format_evidence_id`, `DerivationStep`, `ProvenanceRecorder`.
- `.kiro/specs/agreement-statistics/design.md` — `PageAgreementRecord` shape and the three page flags.
- `.kiro/steering/roadmap.md` — Open Questions 1, 5, 6; the pruning-seam instruction.
- `.kiro/steering/config.md` — `_ALL_KNOWN_TOP_LEVEL_KEYS` registration rule.
