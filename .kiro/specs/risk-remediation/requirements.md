# Requirements Document

## Project Description (Input)
Top-10 Audit Risk Remediation: 10 surgical, independent bug fixes (C1-C10) found in a prior code audit, previously documented only as a loose design document at `.kiro/specs/risk-mediation.md` (no requirements.md/tasks.md/spec.json). That document is preserved in this spec directory as `legacy-design-reference.md`. This spec formalizes the work so it passes through the standard requirements → design → tasks → implementation gates.

**C1 was subsequently found to be INVALID and must not be implemented as written.** The legacy document claimed `configs/final_output_schema.json` wrongly declares `domain_group` as an integer while "the extraction map produces string values like `1. Study identification`". That is false at the point validation happens: `src/pipeline/extraction_map.py:74` (`_build_field_lookup`) parses the descriptive string to its integer prefix via `int(f["domain_group"].split(".")[0])`, and both `src/pipeline/validator.py:225` and `src/pipeline/pdf_processor.py:1236` populate emitted field dicts from that lookup. The emitted value is therefore an integer and the schema is correct. This is a deliberate type boundary, documented in `CHANGELOG.md` under "Update stale tests to match intentional config/parsing fixes": the raw `extraction_map.json` value is the descriptive string (per `structure_schema.json`), the emitted field dict carries the integer (per `final_output_schema.json`). The audit predates commit `8daf0a0`, which introduced the parse. **Requirement 1 below is consequently retained as a regression guard on that boundary, not as a schema change** — its acceptance criteria deliberately say "in the form produced by the configured extraction map" rather than naming a type, so they remain correct and testable. Design must not alter `final_output_schema.json`.

The other nine findings were re-verified as still unapplied against the current codebase on 2026-07-21. Two were re-scoped during requirements clarification because they collided with decisions made by the since-completed `token-efficient-extraction` spec:

- **C10** originally prescribed raising evidence budgets to 40000 chars / 200 items. That reverses a deliberate token-efficiency decision. Re-scoped as an evidence-coverage outcome (Requirement 10) so design can choose values satisfying both coverage and the configured token budgets.
- **C6** originally prescribed simply running scan detection on the cache-hit path, giving back the 1-5s/PDF saving that path was built for. Re-scoped to additionally require the classification be persisted so the cost is paid once per document, not once per run (Requirement 6).

Some file paths in the legacy document are stale (e.g. `src/pipeline/quality_control.py` is now `src/quality_control/quality_control.py`) following the completed `src-layout` migration. The legacy line numbers are advisory only; design must re-verify against current code.

## Boundary Context

- **In scope**: Ten independent defects in already-shipped behavior — final-output schema validation, adjudication-to-reconciliation wiring, annotation identifier determinism, OCR annotation region accuracy, synthesis-stage repair parity, page classification on the cached-parse path, figure/table section attribution, quality-control base-class enforcement, extractor naming in agreement and adjudication outputs, and evidence coverage per chunk.
- **Out of scope**: New pipeline stages, new extraction backends, changes to the extraction field map, changes to the public CLI surface, and any re-litigation of the token-budget enforcement thresholds established by the completed `token-efficient-extraction` work. No new module is introduced and no cross-package dependency direction changes.
- **Adjacent expectations**: This work assumes the existing four-stage quality-control sequence, the document parse cache, the prompt-cache prefix stability rule, and the token-budget enforcement stage all remain in place and keep their current contracts. Where a fix interacts with one of them (Requirements 6 and 10 in particular), the fix must satisfy its own objective without weakening those contracts. Each of the ten requirements is independently valuable and independently verifiable; none depends on another shipping first.

## Requirements

### Requirement 1: Final Output Persistence

*Regression guard only — see the C1 note in the Project Description. No schema change is authorized by this requirement.*

**Objective:** As a reviewer running the pipeline, I want extracted fields to actually be written to disk, so that a completed run produces usable per-paper output instead of silently empty results.

#### Acceptance Criteria

1. When a merged field carries a domain-group value in the form produced by the configured extraction map, the final-output validator shall accept that field as valid.
2. When every field of a processed paper passes validation, the pipeline shall write the paper's extracted output file to the output directory.
3. If a field fails final-output validation, then the pipeline shall record the rejection with the identity of the offending field so an operator can diagnose it.
4. The pipeline shall not silently skip an output write; every skipped write shall be attributable to a logged validation failure.

### Requirement 2: Adjudication-Driven Reconciliation

**Objective:** As a reviewer auditing extraction provenance, I want the reconciled record to reflect the adjudication decision that was actually made, so that the adjudication stage is not decorative.

#### Acceptance Criteria

1. When adjudication selects a primary extractor and a branch from that extractor is available, the reconciliation stage shall derive the reconciled record's primary content from that branch.
2. When a primary branch has been selected, the reconciliation stage shall select a distinct remaining branch as the secondary input.
3. If no available branch matches the adjudicated primary extractor, then the reconciliation stage shall fall back to a documented deterministic ordering and shall log a warning identifying the unmatched extractor name.
4. When reconciliation completes, the reconciled record shall carry the adjudication rationale so that the decision is auditable from the output alone.

### Requirement 3: Stable Annotation Identifiers

**Objective:** As a reviewer comparing annotation artifacts across runs, I want annotation identifiers to be reproducible, so that diffs show real content changes rather than fresh random identifiers.

#### Acceptance Criteria

1. When the same source record and base identifier are annotated twice, the annotation generator shall produce identical annotation identifiers in both outputs.
2. When two annotations differ in their sentence text, page, or occurrence position within the document, the annotation generator shall assign them distinct identifiers.
3. When the same sentence text occurs more than once in a document, the annotation generator shall assign each occurrence its own distinct identifier.
4. The annotation generator shall derive identifiers only from stable document content and position, and shall not incorporate run time, file system paths, or randomness.

### Requirement 4: Accurate OCR Annotation Regions

**Objective:** As a reviewer verifying an OCR-derived claim in the PDF viewer, I want the highlighted region to point at the sentence I selected, so that evidence traceability holds on scanned pages.

#### Acceptance Criteria

1. When an OCR-derived sentence is annotated, the annotation generator shall produce a region corresponding to that sentence's own source block rather than to the first block on the page.
2. When alignment data is produced for an OCR-derived sentence, it shall carry the region information needed to place that sentence on its page.
3. When alignment data is produced for a natively extracted sentence, the absence of region information shall not cause an error, and existing character-offset behavior for native sentences shall be unchanged.
4. If region information is unavailable for an OCR-derived sentence, then the annotation generator shall emit a warning identifying the page and sentence and shall continue producing the remaining annotations.

### Requirement 5: Synthesis Stage Repair Parity

**Objective:** As a reviewer running an extraction, I want a malformed synthesis response to be repaired the same way a malformed extraction-chunk response is, so that one recoverable formatting error does not discard the whole paper.

#### Acceptance Criteria

1. When the synthesis stage receives a response that fails parsing or validation, the extraction pipeline shall attempt repair using the same retry behavior applied to extraction chunks.
2. When a repair attempt yields a valid response, the extraction pipeline shall proceed using that repaired response.
3. If all synthesis repair attempts are exhausted, then the extraction pipeline shall mark the paper as failed at the synthesis stage in the run manifest, with diagnostic detail attached.
4. The synthesis stage and the extraction-chunk stages shall expose the same retry limits and the same failure reporting shape to an operator.

### Requirement 6: Correct Page Classification on Cached Parses

**Objective:** As a reviewer processing a PDF with a mix of native and scanned pages, I want page classification to be correct whether or not a cached parse exists, so that scanned pages are not silently treated as native on re-runs.

#### Acceptance Criteria

1. When a cached document parse is available, the extraction pipeline shall determine each page's classification rather than assuming all pages are native.
2. When a document containing scanned pages is processed using a cached parse, the extraction pipeline shall not label those pages with an all-native routing reason.
3. When page classification has been determined for a document, the extraction pipeline shall persist that classification so that subsequent runs of the same document reuse it instead of recomputing it.
4. When a persisted classification is reused, the routing outcome shall be identical to the outcome produced when the classification was computed from the document.
5. If classification cannot be determined or reused, then the extraction pipeline shall log an error and proceed using the conservative all-native default rather than aborting the document.
6. Where a document's cached parse and persisted classification are both present, the extraction pipeline shall not re-read the document's pages to classify them.

### Requirement 7: Figure and Table Section Attribution

**Objective:** As a reviewer routing evidence by section, I want a figure or table caption to be attributed to the section it appears in, so that section-based ranking and filtering are trustworthy.

#### Acceptance Criteria

1. When a figure caption is indexed as evidence, the evidence index shall attribute it to the section heading of the section that contains it.
2. When a table is indexed as evidence, the evidence index shall attribute it to the section heading of the section that contains it.
3. When a document contains figures or tables in multiple sections, the evidence index shall not attribute them all to the same section.
4. Where a containing section has no heading, the evidence index shall apply the same documented fallback attribution used for sentences in that section.
5. The evidence index shall emit exactly one evidence item per figure and per table, with no duplicates introduced by attribution changes.

### Requirement 8: Quality-Control Extension Contract Enforcement

**Objective:** As a developer extending quality control with custom metrics, I want an incomplete implementation to fail immediately and clearly, so that I find the gap at development time rather than mid-run.

#### Acceptance Criteria

1. If a custom quality-metrics, inter-rater-metrics, or adjudication-rules implementation omits a required operation, then instantiating it shall raise an error naming the missing operation.
2. When a custom implementation provides all required operations, instantiation shall succeed with behavior unchanged from today.
3. When the pipeline runs with its existing built-in implementations, all of them shall instantiate successfully and produce results identical to current behavior.

### Requirement 9: Extractor Identity in Agreement and Adjudication Output

**Objective:** As a reviewer reading a quality report, I want to see which extractors were compared by name, so that agreement scores and adjudication decisions are interpretable without cross-referencing internal ordering.

#### Acceptance Criteria

1. When inter-rater agreement is computed across branches, the resulting pairwise entries shall be keyed by the extractors' source names.
2. When adjudication selects a primary extractor, the recorded selection shall be the extractor's source name.
3. When a quality report is produced for a branch, it shall carry the source name of the extractor that produced that branch.
4. The agreement and adjudication outputs shall contain no entry identifying an extractor solely by its positional index.
5. If a branch's source name is unavailable, then the system shall fall back to a documented identifier and shall remain able to produce agreement and adjudication output.

### Requirement 10: Evidence Coverage per Extraction Chunk

**Objective:** As a reviewer whose extraction depends on the right evidence reaching the model, I want each chunk's evidence selection to cover a substantial share of the paper's substantive text, so that fields are not missed because relevant sentences were pruned away.

#### Acceptance Criteria

1. When an evidence package is built using default configuration for a paper whose substantive text is 30,000 characters, the evidence index shall select at least 60 percent of that substantive text.
2. When evidence selection would exceed the configured token budget for its pipeline stage, the existing token-budget enforcement shall continue to apply, and coverage shall be reduced rather than the budget being exceeded.
3. When default evidence budgets are changed, the accompanying configuration documentation shall state the coverage rationale and shall agree with the values actually configured.
4. If an evidence package falls below the coverage expectation for a document, then the system shall record that shortfall so an operator can identify under-covered papers.
5. The evidence index shall continue to rank and prune evidence; this requirement shall not be satisfied by disabling pruning.
