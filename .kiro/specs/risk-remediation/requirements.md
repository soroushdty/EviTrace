# Requirements Document

## Project Description (Input)
Top-10 Audit Risk Remediation: 10 surgical, independent bug fixes (C1-C10) found in a prior code audit, previously documented only as a loose design document at `.kiro/specs/risk-mediation.md` (no requirements.md/tasks.md/spec.json). That document is preserved in this spec directory as `legacy-design-reference.md`. This spec formalizes the work so it passes through the standard requirements → design → tasks → implementation gates.

*(The framing above is the original input, retained verbatim. Two of its claims have since been disproven and should not be relied on: the fixes are **not** all "surgical" — Requirement 4 spans the extraction, reconciliation, and annotation layers — and they are **not** all "independent" — Requirement 2 is blocked on Requirement 9. See the Staleness Verification appendix.)*

**C1 was subsequently found to be INVALID and must not be implemented as written.** The legacy document claimed `configs/final_output_schema.json` wrongly declares `domain_group` as an integer while "the extraction map produces string values like `1. Study identification`". That is false at the point validation happens: `src/pipeline/extraction_map.py:74` (`_build_field_lookup`) parses the descriptive string to its integer prefix via `int(f["domain_group"].split(".")[0])`, and both `src/pipeline/validator.py:225` and `src/pipeline/pdf_processor.py:1236` populate emitted field dicts from that lookup. The emitted value is therefore an integer and the schema is correct. This is a deliberate type boundary, documented in `CHANGELOG.md` under "Update stale tests to match intentional config/parsing fixes": the raw `extraction_map.json` value is the descriptive string (per `structure_schema.json`), the emitted field dict carries the integer (per `final_output_schema.json`). The audit predates commit `8daf0a0`, which introduced the parse. **Requirement 1 below is consequently retained as a regression guard on that boundary, not as a schema change** — its acceptance criteria deliberately say "in the form produced by the configured extraction map" rather than naming a type, so they remain correct and testable. Design must not alter `final_output_schema.json`.

The other nine findings were re-verified as still unapplied against the current codebase on 2026-07-21. Two were re-scoped during requirements clarification because they collided with decisions made by the since-completed `token-efficient-extraction` spec:

- **C10** originally prescribed raising evidence budgets to 40000 chars / 200 items. That reverses a deliberate token-efficiency decision. Re-scoped as an evidence-coverage outcome (Requirement 10) so design can choose values satisfying both coverage and the configured token budgets.
- **C6** originally prescribed simply running scan detection on the cache-hit path, giving back the 1-5s/PDF saving that path was built for. Re-scoped to additionally require the classification be persisted so the cost is paid once per document, not once per run (Requirement 6).

A third was re-scoped after the staleness verification:

- **C4** covered only the OCR annotation-region defect, which verification showed is unreachable because no sentence is ever marked OCR-derived. Re-scoped to cover the upstream provenance chain that makes the region behavior reachable and testable at all (Requirement 4). This widens that requirement beyond "surgical", which the legacy document's framing assumed; design should expect it to be the largest of the ten.

Some file paths in the legacy document are wrong — for example it cites `src/pipeline/quality_control.py`, which is `src/quality_control/quality_control.py`. This is **not** fallout from the `src-layout` migration, as originally assumed here: `git ls-tree c68b80d src/quality_control/` confirms that path already existed when the audit was written, so the document was inaccurate at authoring time. Its line numbers are advisory only; design must re-verify every reference against current code.

## Boundary Context

- **In scope**: Ten defects in already-shipped behavior — final-output schema validation, adjudication-to-reconciliation wiring, annotation identifier determinism, OCR provenance propagation and annotation region accuracy, synthesis-stage repair parity, page classification on the cached-parse path, figure/table section attribution, quality-control base-class enforcement, extractor naming in agreement and adjudication outputs, and evidence coverage per chunk.
- **Out of scope**: New pipeline stages, new extraction backends, changes to the extraction field map, changes to the public CLI surface, and any re-litigation of the token-budget enforcement thresholds established by the completed `token-efficient-extraction` work. No new module is introduced and no cross-package dependency direction changes.
- **Adjacent expectations**: This work assumes the existing four-stage quality-control sequence, the document parse cache, the prompt-cache prefix stability rule, and the token-budget enforcement stage all remain in place and keep their current contracts. Where a fix interacts with one of them (Requirements 6 and 10 in particular), the fix must satisfy its own objective without weakening those contracts. Each requirement is independently valuable, but the original claim that "none depends on another shipping first" is **false and has been retracted**: Requirement 2 is unreachable until Requirement 9 ships. Adjudication currently reports `primary_extractor == ""` for every run (quality reports are constructed without a `source`, so the name resolves to the empty string), which means a decision-driven branch lookup can never match and would silently fall through to its default. **Sequence Requirement 9 before Requirement 2.** All other requirements remain mutually independent.

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

### Requirement 4: OCR Provenance and Accurate OCR Annotation Regions

*Re-scoped 2026-07-21. The original requirement covered only the region defect (legacy C4). Verification established that the defect is real but **unreachable**: no sentence is ever marked OCR-derived, so the region code never executes outside hand-built test records, and fixing it alone would change nothing observable. The upstream cause is not a missing flag but **erased provenance** — `extraction_pipeline.py` merges `native_blocks + scanned_blocks` into one list with no per-block record of origin, so by the time sentences are built the information needed to set the flag no longer exists. `reconciler._build_semantic_layer` returns `sentences=[]` unconditionally, so the fallback loop in `quality_control.py` is the only sentence producer and it hardcodes `ocr_derived: False`. This requirement now covers the whole chain, because the region fix is not independently verifiable without it.*

**Objective:** As a reviewer verifying an OCR-derived claim in the PDF viewer, I want to see that the claim came from a scanned page and have the highlight point at the sentence I selected, so that evidence traceability holds on scanned pages instead of silently presenting OCR text as if it were native.

#### Acceptance Criteria

1. When blocks extracted from scanned pages are merged with blocks extracted from native pages, the pipeline shall preserve, per block, which extraction path produced it.
2. When a sentence is derived from a block that came from a scanned page, the pipeline shall mark that sentence as OCR-derived.
3. When a sentence is derived from a block that came from a native page, the pipeline shall mark that sentence as not OCR-derived, and existing character-offset behavior for such sentences shall be unchanged.
4. Where a document contains both scanned and native pages, the sentences of that document shall carry both markings according to their originating pages, rather than a single uniform value.
5. When an OCR-derived sentence is annotated, the annotation generator shall produce a region corresponding to that sentence's own source block rather than to the first block on the page.
6. When alignment data is produced for an OCR-derived sentence, it shall carry the region information needed to place that sentence on its page.
7. If region information is unavailable for an OCR-derived sentence, then the annotation generator shall emit a warning identifying the page and sentence and shall continue producing the remaining annotations.
8. When a document is processed end to end, the OCR marking observable in the emitted annotation artifact shall match the marking carried by the corresponding sentence, so that the behavior is verifiable without constructing records by hand.

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

---

## Appendix: Staleness Verification (2026-07-21)

The audit was authored 2026-05-29 (`c68b80d`); 42 commits have landed since, including the whole `token-efficient-extraction` spec. Because finding C1 turned out to be invalid, all remaining findings were re-verified against current code and post-audit history before design. **No finding may be implemented from the legacy document verbatim** — every one needs at least a path correction, and five need genuine rework.

Structural fact: `git diff c68b80d..HEAD -- src/quality_control src/artifact_generation` is **empty**. Those trees are byte-identical to the audit commit, so C2, C3, C4, C8 and C9 could not have been silently fixed.

| # | Verdict | What design must know |
|---|---|---|
| C1 / R1 | **INVALID** | See the Project Description. Schema is correct; R1 retained only as a regression guard. |
| C2 / R2 | **VALID, worse** | The decision *is* passed to `reconcile()` but is write-only metadata — used solely by `_build_provenance_dict`, never influencing content. Blocked on R9 (see Boundary Context). The legacy snippet is **functionally dead as written**: `b.source == decision.primary_extractor` can never match while that value is `""`. Missed by the audit: on the scanned path the second branch is named `paddleocr`, matching neither `pdfplumber` nor `pymupdf`, so `secondary_branch` is `None` and the structural layer — every OCR bbox — is silently discarded. |
| C3 / R3 | **VALID** | Unchanged (`uuid.uuid4()`). Nothing reads annotation ids, so a content hash is safe. But the sole call site passes no `base_uri`, so `document_source` is a constant for every paper — hashing it as-is would **collide across papers** on shared text ("Methods", boilerplate headers). Include a paper identifier. Two tests assert the uuid4 shape (`tests/src/pdf_extractor/test_w3c_annotation.py:274,284`) and must be updated. |
| C4 / R4 | **PARTIALLY VALID → R4 re-scoped** | The region defect is real but was **unreachable**: `ocr_derived` is never set `True` anywhere in `src/`, so the FragmentSelector branch ran only in hand-built test records, and fixing C4 alone would change nothing observable. Root cause is erased provenance, not a missing flag — `extraction_pipeline.py:496-499` merges `native_blocks + scanned_blocks` into one page-sorted list with **no per-block origin marker**, and blocks carry no `source`/`extractor` key; `reconciler._build_semantic_layer` returns `sentences=[]` unconditionally (`reconciler.py:82-123`), so the fallback loop at `quality_control.py:628-638` is the only sentence producer and hardcodes `"ocr_derived": False`. `w3c_annotation.py:247`'s `"ocr_derived": True` is a *consumer* inside `if rec.ocr_derived:`, not a producer. **R4 now covers the whole chain** (AC1–AC4 provenance, AC5–AC7 regions, AC8 end-to-end observability). Note the branch-naming interaction with C2: a mixed PDF's structural branch is named `paddleocr` whenever any scanned block exists, even though it also contains native blocks. The legacy fix additionally does not compile — `_compute_sentence_to_char_range` receives bare strings, with no block or page in scope. **Larger bug the audit missed**: the char-range map is built from *paragraph* texts but looked up by *sentence* texts, so most native annotations silently get `{"start": 0, "end": 0}`. That defeats the proposed bbox retrieval too and affects the majority path today. |
| C5 / R5 | **VALID** | Confirmed: synthesis calls `extract_chunk` raw, with no repair. Requirement 5.3's `failed_chunk_{n}` is **correct** — that status really is written by the synthesis path. The legacy fix would **regress**: `extract_with_repair` accepts no `prior_context`, so copying it verbatim drops the prior-chunk context that is the synthesis chunk's entire purpose, bypasses the confidence-aware pruning added by `bf0614b`, and double-mitigates the token budget under two different stage keys. Extend `extract_with_repair` rather than swapping the call. |
| C6 / R6 | **VALID, worse** | The "a cache hit implies native" justification fails for *mixed* PDFs, because the mixed path writes the cache itself — so run 1 populates it and run 2 takes the all-native branch. The real damage is not the label: on the cache-hit path OCR extraction never runs, so the same PDF yields **materially different QC branches depending on cache state** — a non-idempotency bug larger than the one reported. The mislabel itself is near-cosmetic (`page_routing` has no production consumer). Also, pages pdfplumber returns no blocks for get no routing entry at all. |
| C7 / R7 | **VALID, worse** | Confirmed: figure/table loops sit after the div loop and read the leaked final `section_path`. Impact is severe via `_section_score` — a table under "Results" scores `35+10` but under a trailing "References" div scores `−60+10`, a 95-point swing that sinks **all** tabular evidence to the bottom of the ranked bundle, where the token budget prunes it first. Second defect the audit missed: `section_path` is loop-carried and only reassigned when a `<head>` exists, so a div without one inherits the previous div's heading — sentences included. **The legacy fix is probably wrong**: it proposes `div.findall("./figure")`, but GROBID emits figures and tables as siblings of the divs, direct children of `<body>`, collected at end of document. If so, that change makes figure/table evidence vanish entirely while still passing the only in-repo fixture, which nests them. Unproven — no real TEI in the repo — so **verify against real GROBID output before implementing**. |
| C8 / R8 | **VALID** | Confirmed at runtime: `QualityMetrics.__abstractmethods__` is `None` and the base instantiates. `ABC` + `@dataclass` verified to work, and all four concrete subclasses already implement their abstract methods. But the legacy claim "no changes needed to them" is **false for tests**: `tests/src/quality_control/test_domain_agnosticism.py` instantiates the bare base classes at nine sites and will raise `TypeError`. Those mocks must become trivial subclasses. |
| C9 / R9 | **VALID, premise wrong in detail** | Extractors are **not** identified by positional index — the `str(i)` fallback never fires, because `QualityReport` exposes an `extractor` property aliasing its `source` field. The field simply is never populated, so the emitted name is `""`. Two consequences worse than mislabelling: IAA pairwise keys all collapse to `"_vs_"`, so with more than two branches **only the last pair survives**; and adjudication counts every branch under one key, making the majority vote degenerate and `primary_extractor` always `""`. The legacy fix adds a *new* `source_name` field, duplicating the existing `source`. Prefer populating what exists (`source=branch.source, index=branch.index` at the rater) and changing the call sites to `getattr(x, "extractor", "") or str(i)` so an empty name also triggers the R9.5 fallback. |
| C10 / R10 | **VALID as re-scoped** | R10.1 is arithmetically impossible today: at `max_evidence_chars_per_chunk: 10000`, a 30,000-char paper caps at ~33% coverage. **R10.1 and R10.2 are not in conflict** — 18,000 chars is ≈4,500 tokens against a 100,000-token `extraction_chunk` budget, so pruning would not fire; a value around 20,000–30,000 satisfies both comfortably. The config mismatch is **three-way**, not two: yaml says `10000`, its own comment says `30000`, `config_utils.py` defaults to `30000`, and `configs/README.md` documents `250/60000`. Two further notes: `max_evidence_items_per_chunk: 150` is checked first and is a second binding constraint; and the budget counts only item `text`, not the JSON envelope, so real payload exceeds the nominal figure. R10.4 is wholly unimplemented — no coverage ratio is computed anywhere. **Before writing tests, define "substantive text"** — it appears nowhere in code, and the evidence corpus derives from GROBID TEI rather than raw PDF text, so the denominator is currently unmeasurable. |

Two conclusions from static tracing rather than execution, flagged for a runtime check during design: that `primary_extractor` is always `""`, and that a fully-scanned PDF produces an empty unified record. Both chains are short and unambiguous, but neither was observed running.
