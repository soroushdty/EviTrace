# Research & Design Decisions — risk-remediation

---
**Purpose**: Gap analysis between `requirements.md` and the current codebase (`/kiro-validate-gap`, 2026-09-21), to inform requirement approval and the design phase.

**Method**: Every appendix claim in `requirements.md` was re-verified against the working tree at `a57294d` (plus the uncommitted R8 fix). Static claims were checked by reading; behavioural claims were checked at runtime with a fake `TextProcessor` and hand-built `QCBundle`s (no GROBID/OpenAI/PaddleOCR calls), except R7, which was probed against the **live local GROBID 0.8.2** on three open-access papers because no real TEI exists in the repository.
---

## Summary

- **Feature**: `risk-remediation`
- **Discovery Scope**: Extension (ten defects in shipped behaviour across `pipeline`, `quality_control`, `artifact_generation`, `pdf_extractor`)
- **Key Findings**:
  - **R8 is already implemented** (uncommitted, this session): `ABC` added to the three QC base classes, nine test call sites converted to stub subclasses, seven regression tests added. Requirement 8 needs only a status note.
  - **R7's legacy fix is proven wrong on real data.** In 3/3 real GROBID outputs, 0 of 35 `<figure>` elements are inside a `<div>`; all are `<body>` siblings collected after the last div. `div.findall("./figure")` would emit **zero** figure/table evidence while passing both in-repo fixtures. GROBID expresses no containment for figures, so AC 7.1/7.2 ("the section that contains it") are **not determinable as written** — see Requirement revisions.
  - **R4 is wider than the re-scope assumed.** OCR text never reaches the semantic layer at all: sentences are built only from the GROBID (primary) artifact, and the merged pdfplumber/PaddleOCR block list is the secondary (structural) artifact. Marking blocks is necessary but not sufficient — a sentence source for scanned pages must be added, and the reconciler must stop dropping `paddleocr`-named branches.
  - **Three coordinate defects outside any requirement** were found while probing R7: the pipeline sends `teiCoordinates` as one comma-joined value so production TEI has **no `coords` at all**; both `_parse_coords` implementations expect a format GROBID does not emit; and sentence `<s>` elements never carry coords. Consequence: every evidence item's `page` is `None` today. Not blocking any current requirement, but material to the product and to R4/R7 secondary approaches.
  - R10's config mismatch is **four-way**, not three-way (a fourth dead default lives in `pdf_processor.py:1181-1182`), and no coverage ratio exists anywhere.

## Requirement-to-Asset Map

Tags: **Missing** = capability absent; **Constraint** = existing code shapes the fix; **Unknown** = needs design-time decision; **Done** = already satisfied.

| Req | Current asset (verified location) | Gap | Notes |
|---|---|---|---|
| 1.1–1.3 | `FinalOutputValidator` (`pipeline/validator.py:261-337`), gate in `_save_pdf_output` (`pdf_processor.py:928-981`), `domain_group` int parse (`extraction_map.py:74`) | **Done** | Every error logged with `field_index`/`field_name`; manifest set to `failed_schema_validation`; write skipped. C1 INVALID re-confirmed. |
| 1.4 | `_atomic_write_json` (`pdf_processor.py:78-104`) re-raises; caught only at `orchestrator.py:146-149` | **Constraint** (partial) | Non-validation write failure is logged but the manifest keeps no failure status. `_save_pdf_output` also writes the manifest without `manifest_lock` (`:969-971`), unlike every other manifest write. |
| 1.x tests | `test_final_output_validator*.py`, `test_pdf_processor_helpers.py:98-209` | **Missing** negative path | `test_property_1_output_file_written_iff_valid` (`:215`) only exercises the valid branch. |
| 2.1–2.2 | `_pdf_reconciler_fn` (`quality_control.py:589-608`) hard-codes primary=`"grobid"`, secondary∈`{"pdfplumber","pymupdf"}`; `reconcile()` (`reconciler.py:297`) uses `adjudication_decisions` only for provenance (`:363,:462,:557`) | **Missing** | Decision is write-only metadata (VERIFIED at runtime). The appendix's "legacy snippet is dead" is moot — no such snippet exists in code. `"pymupdf"` is never a branch name; `"paddleocr"` is, on every mixed/scanned run → secondary is `None` → `structural.blocks == []` (VERIFIED: 0 blocks). |
| 2.3 | `all_branches` already ordered by `Candidate.index` | **Missing** | Natural fallback = index order + warning naming the unmatched extractor. |
| 2.4 | `content["provenance"]["adjudication_decisions"]` = `{primary_extractor, confidence, rationale}` | **Done** once R9 lands | Nothing records which branch was *actually used*; design should add `primary_branch_source`/`secondary_branch_source` beside it. |
| 3.1–3.4 | `uuid.uuid4()` at `w3c_annotation.py:202`; `base_uri` never passed at the sole call site (`extraction_pipeline.py:587-595`) → `document_source` constant | **Missing** | No reader of annotation ids in `src/` (safe to change). Duplicate sentence text yields identical `selector_payload` and `quote_selector` today — distinctness must come from an enumeration/occurrence index, not existing fields. Paper identifier available in scope: `pdf_name` and `pdf_digest`. Tests `test_w3c_annotation.py:273-291` hard-code the uuid4 regex. |
| 4.1 | Merge at `extraction_pipeline.py:496-499`; blocks are `BlockDict` TypedDicts (`schemas.py:27-31`), extra keys tolerated by `validate_blocks` | **Missing** | PaddleOCR blocks carry an *implicit* marker (`rasterization_dpi`, `ocr_confidence`; `schemas.py:41-59`); pdfplumber blocks have `block_bbox=None`. Explicit `source` key is the least-disruptive marker. |
| 4.2, 4.4 | Sentences produced only by fallback loop `quality_control.py:623-638` from `semantic.paragraphs`, which `_build_semantic_layer` (`reconciler.py:82-125`) builds from **primary (GROBID) blocks only** | **Missing — no code path** | OCR blocks never become sentences (finding N1). AC 4.2 is unreachable without a sentence source for scanned pages. Paragraph dicts carry `block_index` (`reconciler.py:109`) — a ready hook for provenance. |
| 4.3 | `_compute_sentence_to_char_range` (`reconciler.py:151-183`) is fed **paragraph** texts (`:443-447`) and looked up by **sentence** text (`w3c_annotation.py:105-132`) | **Constraint** | Miss → `{"start":0,"end":0}` (VERIFIED). Severity is lower than the appendix states: with `segment_sentences: true` (config default) TEI `<s>` are one block each so most lookups hit; guaranteed miss for multi-sentence blocks (abstract `<p>`, `figDesc`). Also hard-codes `"page_index": 0` (`:181`). "Existing behaviour unchanged" in 4.3 must be read as *not regressed*, not *preserved bug-for-bug*. |
| 4.5 | Region branch `w3c_annotation.py:135-150` takes the first structural block on the page | **Missing** | VERIFIED wrong block at runtime. Also latent `TypeError`: `.get("block_bbox", default)` returns `None` when the key exists with `None` (pdfplumber blocks). |
| 4.6 | `AlignmentRecord` (`models.py:248-278`) has `ocr_derived`, `ocr_engines` but **no bbox**; `sentence_to_char_range` entries are free dicts | **Missing** | Appendix's "AlignmentMapEntry" is a stale name. Design must choose: new `AlignmentRecord` field vs. extend the char-range dict. `structure_schema.json` does not validate `semantic`/`alignment` internals — no schema change needed. |
| 4.7 | No warning path exists | **Missing** | — |
| 4.8 | `content["annotations"]` written at `extraction_pipeline.py:595`; no consumer | **Missing** test only | Needs an end-to-end fixture with a mixed PDF path (mocked backends). |
| 5.1–5.2 | `RepairRetryLoop.extract_with_repair` (`pdf_processor.py:501-688`); synthesis calls `extract_chunk` raw (`:1461-1485`) then `validate_chunk_output` once (`:1498`) | **Missing** | Three things synthesis passes that the repair loop lacks (all VERIFIED): `prior_context`, stage key `"synthesis"` (120k) vs hard-coded `"extraction_chunk"` (100k), `protected_evidence_ids`. Extend `extract_with_repair` with keyword-optional params; do not swap the call. |
| 5.3 | Synthesis writes `{"status": f"failed_chunk_{n}", "error": str(exc)}` (`:1521-1523`); chunks write `{"status":"failed_chunks","failed_chunks":[...]}` (`:1130-1135`) | **Constraint** | Shapes differ; `RepairExhaustedError.metadata` (`:677-688`) is only logged for chunks, never persisted. |
| 5.4 | `max_repair_attempts=2` is a constructor default (`:481`); `_run_parallel_chunks` (`:1090-1096`) never passes it; **no config key exists** | **Missing** | Needs a config key under `retry` or `extraction` (registered per `.kiro/steering/config.md`). Transport retries (`retry.max_retries`) already apply to both stages. |
| 6.1–6.2 | Cache-hit branch `extraction_pipeline.py:287-311` skips `scan_detector` entirely and labels every page `all_native`; OCR never runs on a hit; mixed path writes the TEI cache (`:442`) | **Missing** | Non-idempotency VERIFIED by tracing: run 1 (mixed) → `[grobid, paddleocr]`; run 2 → `[grobid, pdfplumber]` with `"[PAGE n]\n"` stubs for scanned pages. A second all-native default exists in the `fitz`-missing fallback (`:335-355`). |
| 6.3–6.4, 6.6 | Only GROBID TEI is cached (`{sha256}.tei.xml`, raw text, `_grobid_cache_read/_write` `:178-209`); there is no "document parse" cache | **Missing** | Natural sibling: `{sha256}.pages.json` in the same `tei_cache_dir`, carrying `PageScanClassification` fields (`page_index, is_native, triggered_stages, stage_values`) so `routing_reason` reproduces identically. `evidence_index` cache is downstream and keyed differently — wrong granularity. |
| 6.5 | — | **Missing** | Log + all-native default on read failure; straightforward. |
| 6.x tests | None exercise the cache-hit branch (all set `tei_cache_dir: ""` or patch `_grobid_cache_read`) | **Missing** | New helpers must be patchable at `pipeline.extraction_pipeline.*`, matching the existing seam list. |
| 7.1–7.3 | Figure loop `evidence_index.py:496-517`, table loop `:519-545` after the div loop, reading the leaked final `section_path` (`:443-449`) | **Missing + Unknown** | On real TEI, 100% of figure/table items share the last body div's heading (p1 `'Ferret immunization and infection'`, p2 `'Scenario'`, p3 `'Conclusion'`). The References sink cannot occur: `<back>` is never in `<body>`. **GROBID gives no containment** — see revisions. Usable signal: `<ref type="figure|table" target="#fig_N">` inside body divs (resolvable for 11/13, 3/7, 2/10 figures). |
| 7.4 | `section_path` is loop-carried; head-less div inherits previous head | **Missing** | 0/45 real body divs lacked a head, but the defect is real; reset per div to `"body"`. |
| 7.5 | `.//figure` matches `<figure type="table">` and `.//table` matches its nested `<table>` | **Missing** | Every GROBID table yields **two** items (F caption + T rows), the T item's xpath is the non-unique `.//table`. Iterate `./figure` once; branch on `type`. |
| 7.x fixtures | `test_pipeline_evidence_index.py:139-165`, `test_evidence_index_stability.py:113-117` nest figures inside a `<div>` with fake `"1;x,y,w,h"` coords | **Constraint** | Fixtures mis-model GROBID; a real-shaped fixture (or a checked-in real TEI, 60–190 KB) is required or the fix cannot be tested honestly. |
| 8.1–8.3 | `models.py` `ABC` bases; `test_qc_models.py` regression tests; builtins verified | **Done** (uncommitted) | 1477 passed, 2 skipped, full suite. |
| 9.1–9.4 | Production reports built at `quality_control.py:401-408` **without** `source=`/`index=`; `rater.observe` (`rater.py:16-39`) does populate them but has **zero callers**; IAA key `f"{name_a}_vs_{name_b}"` (`inter_rater_report.py:42-44`); adjudicator `getattr(r,"extractor",str(i))` (`adjudication_decision.py:37`) | **Missing** | Runtime: `report.source == ['','','']`, `pairwise == {'_vs_': 1.0}`, `primary_extractor == ''`, rationale `' selected: 3/3 branches passed'`. With three reports pass/fail/pass only the last pair survives. `str(i)` never fires because the property always exists. |
| 9.5 | — | **Missing** | `getattr(x, "extractor", "") or str(i)` makes an empty name trigger the fallback. |
| 10.1 | Two caps in `build_paper_evidence_package` (`evidence_index.py:1109-1126`): items cap first (hard break), then char cap (skip-and-continue); char budget counts `len(text)` only | **Constraint** | At `10000` chars a 30 000-char corpus caps at ≤33 %. 30 000 chars ≈ 7 500 tokens ≪ 100 000 budget → 10.1/10.2 compatible (VERIFIED). |
| 10.3 | Four disagreeing sources: `config.yaml:37-45` (comment 30000, value **10000**, items 150), `config_utils.py:397-398` (150/**30000**), `configs/README.md:81-87` (**250/60000**, no `token_budgets` section), `pdf_processor.py:1181-1182` (**250/60000**, dead) | **Missing** | Tests mirror some of these: `test_token_efficiency_regression.py:150-153` (150/10 000), `test_orchestrator_concurrency.py:31-32` (250/60 000). |
| 10.4 | No coverage ratio anywhere (only a reduction-ratio log at `pdf_processor.py:1285-1291`) | **Missing + Unknown** | Denominator candidates below. Recording sites: manifest entry (free-form dict), `ExtractionCoverageMetricRecord` (`models.py:370-390`, shape fits, import direction already exists), evidence cache JSON (written pre-selection). |
| 10.5 | Ranking/pruning intact | **Done** | — |

## Research Log

### R7 — where GROBID puts figures and tables
- **Context**: Appendix C7 flagged the legacy fix as "probably wrong, unproven — no real TEI in the repo".
- **Sources**: Live GROBID 0.8.2 (`lfoppiano/grobid:0.8.2-crf`, `/api/isalive` → true) on bioRxiv 10.1101/2020.03.24.004655 (12 figs), arXiv 2003.10218 (10 figs + 3 tables), PLOS ONE 10.1371/journal.pone.0230405 (9 figs + 1 table), using the pipeline's exact form parameters; repo parser `_build_items_from_tei` run on the resulting TEI.
- **Findings**: every `<figure>` has ancestor chain `body → figure`; none inside a `<div>`; all appear after the last `<div>`; tables are `<figure type="table">` with a nested `<table>` (no `xml:id`); body `<head>` elements carry `xml:id` (pipeline sends `generateIDs=1`) but never `n=`; references/acknowledgements/funding/annex live in `<back>`; `<ref type="figure" target="#fig_N">` links exist inside divs.
- **Implications**: (1) containment is not expressible → attribute by *first citing section* with a documented `"body"` fallback; (2) `<back><div type="annex">` supplementary text is never indexed (out of scope, note for roadmap); (3) the AC 7.1/7.2 wording must change.

### Coordinates (side finding, outside R7)
- `GROBID.py:459` sends `teiCoordinates="p,figure,formula,head,biblStruct"` as **one** form value; GROBID treats it as a single unknown element and emits **zero** `coords`. Sending repeated fields yields coords (49 on p3).
- Both `evidence_index._parse_coords` (`:186-201`) and `GROBID._parse_coords` (`:165-200`) expect `"page;x0,y0,x1,y1"`; GROBID emits `"page,x,y,w,h;page,x,y,w,h"`. Both return `None` on real input.
- `<s>` never has coords (deliberately omitted) and the sentence loop reads `sent.attrib.get("coords")` (`:456`) rather than the parent `<p>`.
- **Implication**: `page` is `None` for every evidence item in production; `attach_table_figure_crops` (`:1146`) can never locate a page. Not required by any current requirement; recommend adding as Requirement 11 or routing to the roadmap (decision for the requirements owner).

### R4 — why `ocr_derived` can never be `True`
- Sentence production has a single producer (fallback loop `quality_control.py:623-638`) fed by `semantic.paragraphs`, which is built from the **primary** artifact = GROBID TEI. The merged pdfplumber/PaddleOCR blocks are the **secondary** artifact and land only in `StructuralLayer`. On a fully scanned PDF (`tei_xml == ""`) the only branch is `paddleocr` (`extraction_pipeline.py:504`) and both artifacts are empty → `content["exact_text"] == ""`, which violates `structure_schema.json` (`minLength 1`) if `validate_context` runs. Design must (a) add a sentence source for scanned pages and (b) let the reconciler take a `paddleocr` secondary (shared root with R2).
- Also noted: `pymupdf` OCR blocks are computed and only logged (`:472-483`); the docstring's "secondary cross-validation" (`:224-225`) and the comment "filter to native pages" (`:438`) describe behaviour that does not exist. Doc drift only.

### R10 — a measurable denominator for "substantive text"
- Candidate A: `sum(len(item["text"]) for item in bundle.evidence_items)` — same units as the numerator (`char_budget`), TEI-derived, already in memory at build time.
- Candidate A′: A restricted to items whose `section_path` is not `"Metadata"` and whose `_section_score` ≥ 0 (excludes title and penalised sections).
- Candidate B: `len(content["exact_text"])` — mixes raw-PDF chars with TEI chars and (in the existing log) the JSON envelope; rejected.
- **Recommendation for design**: A′, with the ratio recorded per paper in the manifest entry and (optionally) as an `ExtractionCoverageMetricRecord`.

### R9 — why the majority vote is degenerate
- `hasattr(QualityReport(), "extractor")` is always `True`, so the `str(i)` fallback is dead; the emitted name is `""`; pairwise keys collapse to a single `"_vs_"`; `pass_counts` has one key. Fix is a two-kwarg addition at `quality_control.py:401` plus `or str(i)` at two call sites. `rater.observe` (which already does this correctly) is dead production code — design may either route through it or delete it.

## Implementation Approach Options

| Option | Description | Strengths | Risks / Limitations | Notes |
|---|---|---|---|---|
| A — Extend in place | All fixes as edits to existing functions (`_pdf_reconciler_fn`, `extract_with_repair`, `build_qc_bundle`, `_build_items_from_tei`, `project`) | Smallest diff; matches "no new module" boundary; each fix independently revertible | `build_qc_bundle` (already ~350 lines) and `pdf_processor.py` grow further; R4 and R6 add branches to the hottest function in the pipeline | Fits R1, R2, R3, R5, R7, R9, R10 |
| B — New helpers within existing packages | New private functions/modules for the new capabilities: page-classification persistence (`pipeline/`), OCR sentence source + block provenance (`quality_control/`), figure attribution (`pipeline/evidence_index.py` private helpers) | Testable in isolation; keeps `build_qc_bundle` readable; new seams are patchable at module path as existing tests require | Slightly larger surface; must not introduce a new *package* or change dependency direction | Fits R4, R6 |
| C — Hybrid (recommended) | A for the seven contained fixes, B for R4 and R6 | Matches the requirements' own claim that R4 is "the largest of the ten" | Two implementation styles in one spec | Sequence R9 → R2 → R4 (they share the branch-naming root) |

## Implementation Complexity & Risk

| Req | Effort | Risk | Justification |
|---|---|---|---|
| 1 | S | Low | Add negative-path test; optionally manifest status on write failure + lock. |
| 2 | S | Low | Caller-side branch selection; unblocked once R9 lands. |
| 3 | S | Low | Deterministic id from `(paper_id, page, occurrence, text)`; two tests to update. |
| 4 | L | **High** | New sentence source for OCR pages, provenance key through three layers, region lookup by block, warning path, end-to-end fixture. Touches `pipeline`, `quality_control`, `artifact_generation` (directions unchanged). |
| 5 | M | Medium | Extend `extract_with_repair` with keyword-optional params; unify failure shape; new config key. Ordering-sensitive tests (`call_args_list[2]`). |
| 6 | M | Medium | New sidecar cache file + read/write helpers; OCR must run on cache hit for scanned pages; first-ever tests for the cache-hit branch. |
| 7 | M | Medium | Citation-based attribution; single figure loop; real-shaped fixture. Correctness now evidence-backed. |
| 8 | — | — | Done. |
| 9 | S | Low | Two kwargs + two `or str(i)`; add pipeline-level assertions. |
| 10 | M | Medium | Define denominator, raise char cap (20 000–30 000), reconcile four sources + tests, record shortfall per paper. |

Overall: **L–XL**, driven by R4. All other requirements are S–M.

## Requirement revisions recommended before approval

These are places where the analysis shows the requirement is unimplementable, ambiguous, or silent as written. They are proposals for the requirements owner, not changes made.

1. **R7 AC 7.1–7.2** — "the section heading of the section that contains it" is not determinable from GROBID TEI (no figure is inside a `<div>`). Propose: *"…shall attribute it to the heading of the first body section that cites it (`<ref type="figure|table">`); where no section cites it, the documented fallback in 7.4 applies."* The appendix row C7 should be updated to record that the References-sink scenario cannot occur and the real failure is uniform inheritance of the last body heading.
2. **R7 AC 7.4** — extend to name the fallback explicitly (`"body"`, neutral score), and state that `section_path` resets per div.
3. **R4** — add an acceptance criterion making the sentence source explicit: *"When a page is scanned and OCR text exists for it, the pipeline shall derive sentences for that page from the OCR blocks, so that OCR-derived sentences exist to be marked."* Without it, AC 4.2 has no code path and the design would be silently widening scope.
4. **R4 / R2 shared root** — record in Boundary Context that the reconciler's rejection of `paddleocr`-named secondaries is the common cause behind both R2 (structural layer dropped) and R4 (no OCR bbox reachable), and that R2 fixes it.
5. **R4 AC 4.3** — reword "existing character-offset behavior … shall be unchanged" to "shall not regress"; the existing behaviour is itself defective (paragraph-keyed map, `page_index` hard-coded 0) and the fix may improve it.
6. **R5 AC 5.4** — state that the repair-attempt limit becomes a configuration key (today it is a constructor default with no operator surface); name the section (`retry`) so config-registration rules apply.
7. **R8** — annotate as implemented 2026-09-21 (uncommitted at time of writing) so design/tasks don't re-plan it.
8. **R10 AC 10.1** — define the denominator in the requirement (proposed: total character length of all TEI-derived evidence items excluding the `Metadata` section) so "substantive text" is measurable; the 60 % figure is otherwise untestable.
9. **New — coordinates (three defects)** — decide whether to add a Requirement 11 ("Evidence items shall carry the page on which they occur when GROBID provides coordinates") covering the `teiCoordinates` request bug, the coords-format parsers, and sentence page inheritance from `<p>`, or route it to the roadmap's direct-implementation list. It is not blocking R1–R10 but is the reason every evidence `page` is `None`.
10. **Observation, no revision proposed** — a fully scanned PDF currently produces an empty `UnifiedRecord` that fails `structure_schema.json`; R4's new sentence source resolves it as a side effect. Worth a preservation test.

## Research Needed (carry to design)

- R3: exact id recipe (hash inputs, occurrence counter placement in `project()` vs `generate_w3c_jsonld()`), and whether `base_uri` should become the paper identifier at the call site.
- R4: where the OCR sentence source lives (reconciler secondary-artifact path vs. a new step in `_pdf_reconciler_fn`), and whether region info goes on `AlignmentRecord` (new field) or the char-range dict.
- R5: unified failure shape for `failed_chunks` / `failed_chunk_{n}` that keeps `test_qc_pipeline_integration.py` and `_load_completed_result` working.
- R6: sidecar schema for `{sha256}.pages.json` and versioning; behaviour when TEI is cached but the sidecar is absent (first run after upgrade).
- R10: final default for `max_evidence_chars_per_chunk` (20 000–30 000 range satisfies 10.1 and 10.2); which of the four sources becomes canonical; where the shortfall is recorded.

## References

- `.kiro/specs/risk-remediation/requirements.md` — Appendix: Staleness Verification (2026-07-21)
- `.kiro/specs/risk-remediation/legacy-design-reference.md` — original audit (advisory only)
- `.kiro/steering/config.md` — config key registration rules
- `.kiro/steering/testing.md` — QCBundle construction, mocking seams, migration-test pair convention
- GROBID TEI probe artefacts (session scratchpad, not committed): `p1/p2/p3.pipeline.tei.xml`, `analyze.py`, `refs.py`

---

# Design-phase additions (2026-09-21)

## Additional verification during design

- **Three coordinate parsers, one wrong grammar.** Besides `GROBID._parse_coords` and `evidence_index._parse_coords`, `quality_control.py:214-224` `_page_from_coords` also splits on `;` expecting exactly two parts. On real GROBID input (`page,x,y,w,h` per box, `;` between boxes) a single-box value has one part and a multi-box value has N parts, so it returns page 0 for **every** element. Requirement 11 must therefore cover all three, and `quality_control` cannot import the canonical parser from `pdf_extractor` (dependency rule) — see Decision D6.
- **`quality_control._extract_tei_payload` already inherits sentence page from the parent `<p>`** (`:248-262`); `evidence_index` does not (`:456`). R11.3 applies only to `evidence_index`.
- **`_build_structural_layer` partitions blocks** into `blocks` / `tables` / `figures` (`reconciler.py:128-150`), so an index into `structural.blocks` does not correspond to a secondary-artifact block index. Any "sentence → its block" link that relies on positional indices into `structural.blocks` is unsound. Settles Decision D2.
- `_call_grobid_api` posts with `requests.post(files=..., data=form_data)` (`GROBID.py:109-110`); a list value for a `data` key is encoded as repeated multipart fields, which is the form GROBID accepts. Settles R11.1 without new dependencies.
- The comment at `GROBID.py:454-457` claims "the QC pipeline falls back to parent-paragraph page when sentence coords are absent" — true for `quality_control`, false for `evidence_index`.

## Synthesis

### Generalisation
- **One provenance key, propagated not interpreted.** R9, R2 and R4 are all "which extractor produced this?" at different layers (branch → report → decision → block → paragraph → sentence). The design threads a single `source` string from `Candidate.source` down to block dicts, and a derived boolean `ocr_derived` from block to sentence to annotation. `quality_control` only *propagates* these keys; the meaning of a source name (which extractors are OCR) is decided in `pipeline`, keeping QC domain-agnostic.
- **One routing function for both cache paths.** R6's defect is that the cache-hit branch re-implements routing badly. Extracting "given per-page classifications, build the branches" into one function used by both the miss and hit paths removes the divergence rather than patching it.
- **One failure-record shape.** R5.3/5.4 and R1.4 both want a manifest entry that says *what stage failed and why*. A single `FailureRecord` shape is used by extraction chunks, synthesis and the output write.
- **Alignment layer owns "where is this sentence".** R4.3 (char offsets), R4.5/4.6 (OCR regions) and R3 (occurrence distinctness) all need a per-sentence location record. The design makes `sentence_to_char_range` positionally aligned with `semantic.sentences` and gives each entry the page and, for OCR sentences, the bbox. `project()` then reads only `semantic` + `alignment`, which is what `CLAUDE.md` already documents and the current code violates by scanning `structural.blocks`.

### Build vs adopt
- **Annotation ids: adopt RFC 4122 name-based UUIDs (`uuid.uuid5`)** over a custom SHA slice. Same stdlib, standard shape, deterministic, keeps the `urn:evitrace:anno:<uuid>` URN form so only the version nibble in two tests changes.
- **Coordinates: adopt GROBID's documented grammar** (`page,x,y,w,h` boxes; `;`-separated) rather than the invented one in the fixtures.
- **Figure attribution: build (small).** No library resolves TEI `<ref target>` to sections; a 20-line map over `<body>` divs is the whole thing.
- **Config keys: reuse the existing registration mechanism** (`_ALL_KNOWN_TOP_LEVEL_KEYS` untouched — all new keys are nested under existing `retry` / `extraction` sections).

### Simplification
- No new packages, no new modules (requirements boundary). New behaviour is added as private functions in the modules that already own the seam; the one exception considered — a shared coords parser in `utils/` — was rejected because it would be a new module *and* would make `quality_control` depend on a parser whose grammar is a `pdf_extractor` concern. Two small parsers with a cross-agreement test is the smaller design.
- `rater.observe` (dead production code that already populates `source`) is **not** wired in; the two-kwarg fix at the real construction site is smaller. Deleting `observe` is out of scope.
- No `min_evidence_coverage` *enforcement* — 10.4 asks only that a shortfall be recorded. A threshold key exists solely to decide when to log/flag.
- The R6 sidecar stores exactly the fields needed to reproduce `routing_reason`; no general "document parse cache" is introduced.

## Design Decisions

### D1 — Branch selection lives in `_pdf_reconciler_fn`, keyed by `decision.primary_extractor`
- **Context**: R2.1–2.3. `reconcile()`'s contract says the caller chooses roles; the caller hard-codes them.
- **Alternatives**: (a) move selection into `reconcile()`; (b) keep it in the caller, driven by the decision.
- **Selected**: (b). `reconcile()`'s signature and 400+ lines of tests are untouched; `_pdf_reconciler_fn` picks primary = first branch whose `extractor == decision.primary_extractor`, else `all_branches[0]` with a WARNING naming the unmatched extractor; secondary = first remaining branch in index order. Provenance gains `primary_branch_source` / `secondary_branch_source`.
- **Trade-off**: the reconciler remains role-agnostic (good) but the selection rule is only testable through `run_quality_control` (acceptable; that is where the bug is).

### D2 — Alignment layer carries sentence location; `project()` stops reading `structural`
- **Context**: R4.3/4.5/4.6/4.7, R3.2/3.3. Region lookup by "first block on the page" is wrong; index lookups into `structural.blocks` are unsound (partitioned list); text-keyed char ranges collapse duplicates.
- **Alternatives**: (a) sentence dicts carry `block_index` into the *secondary artifact* and `project()` resolves via `structural`; (b) reconciler writes one alignment entry per sentence (positionally aligned) with `page_index`, `start`, `end`, and `bbox` when the source block has one; `project()` zips sentences with entries.
- **Selected**: (b). Single owner for location; duplicates get distinct offsets via a monotonic search cursor; `project()` honours the documented "semantic + alignment only" rule. Missing bbox for an OCR sentence → WARNING with page and sentence prefix, `FragmentSelector` omitted for that record (4.7), remaining annotations continue.
- **Follow-up**: `AlignmentRecord` (the dataclass) is not the vehicle — `sentence_to_char_range` entries are dicts today and stay dicts; `structure_schema.json` does not validate them.

### D3 — Reconciler becomes the single sentence producer, including OCR pages
- **Context**: R4.2/4.4/4.9. Today the only producer is a fallback loop in `quality_control.py` over GROBID paragraphs; OCR blocks never become sentences.
- **Selected**: `reconcile()` (which already receives `text_processor`) builds sentences from primary paragraphs (`ocr_derived` copied from the paragraph's block) **and** from secondary blocks whose `ocr_derived` is `True` and whose page has no primary text; the loop in `quality_control.py` is removed. Sentence dicts: `{"text","page_index","ocr_derived","source"}`.
- **Trade-off**: touches the reconciler's core; mitigated by the existing reconciler test suite and a preservation test that a native-only document produces byte-identical sentences.

### D4 — Extend `extract_with_repair`; do not swap the synthesis call
- **Context**: R5. Three synthesis-only inputs would be lost by a verbatim swap.
- **Selected**: `extract_with_repair(..., prior_context=None, stage="extraction_chunk", protected_evidence_ids=None)`; budget mitigation and `extract_chunk` receive them. Synthesis calls it with `stage="synthesis"`. `retry.max_repair_attempts` (default 2) is loaded via `load_openai_config()` and passed to `RepairRetryLoop`.
- **Failure shape**: both stages write `{"status": <existing value>, "error": str, "failures": [FailureRecord]}`; chunk path keeps `failed_chunks: [int]` for compatibility.

### D5 — Page-classification sidecar beside the TEI cache; one routing function
- **Context**: R6. Only TEI is cached; hit path assumes native.
- **Selected**: `{digest}.pages.json` in `tei_cache_dir` = `{"version": 1, "scan_detection_config_hash": <sha256 of the scan_detection config subsection>, "pages": [{"page_index","is_native","triggered_stages","stage_values"}]}`. Read on TEI hit; on absence/corruption/hash mismatch → recompute via `scan_detector` (if `fitz` importable) and rewrite; if it cannot be computed → ERROR log + all-native default (6.5). Both paths then call one `_build_branches_for_classifications(...)`, so a hit with scanned pages runs OCR exactly like a miss (6.4).
- **Trade-off**: pdfplumber still runs on a hit (it did before); 6.6 is about *classification* not re-reading pages, which the sidecar satisfies.

### D6 — Coordinates: canonical parser in `pdf_extractor`, mirrored in `quality_control`
- **Context**: R11; dependency rule forbids `quality_control → pdf_extractor`; boundary forbids new modules.
- **Selected**: `GROBID.parse_tei_coords(coords) -> list[CoordBox]` is canonical; `evidence_index` imports it (allowed direction); `quality_control._page_from_coords` is rewritten to the same grammar locally. A cross-agreement test feeds both the same fixture strings. `teiCoordinates` sent as a list value → repeated multipart fields.

### D7 — Figure/table attribution by first citing section
- **Context**: R7 (reworded). See probe.
- **Selected**: one pass over `body` builds `section_by_xmlid: dict[str, str]` from `<ref type="figure"|"table" target="#id">` inside each div (first citing div wins, document order); `section_path` is reset per div (`head text or "body"`); one loop over `./figure` emits one item per figure (`type=="table"` → `T` item whose text is caption + rows, xpath from the figure's `xml:id`), section from the map or `"body"`. The separate `.//table` loop is removed.

### D8 — Evidence coverage measured where selection happens
- **Context**: R10. `build_paper_evidence_package` returns a JSON string; nothing measures coverage.
- **Selected**: `select_paper_evidence(bundle, all_fields, *, max_items, max_chars) -> tuple[list[dict], EvidenceSelectionStats]`; `build_paper_evidence_package` becomes a serialising wrapper (signature unchanged). `pdf_processor` records `evidence_coverage` (`ratio`, `selected_chars`, `substantive_chars`, `below_threshold`) in the manifest entry and logs WARNING when `ratio < extraction.min_evidence_coverage_ratio` (default 0.6). Canonical defaults become 150 items / 30 000 chars in `config.yaml`, `config_utils`, `configs/README.md`; the dead 250/60 000 fallback in `pdf_processor` is aligned.

## Risks & Mitigations
- R4 reconciler refactor regresses native documents — preservation test: native-only fixture produces identical `semantic.sentences` texts/pages before and after; full suite.
- R6 hit path now runs OCR for scanned pages → longer re-runs for mixed PDFs — that is the correct behaviour (it was silently skipped); log at INFO how many pages route to OCR on a hit.
- R5 ordering-sensitive tests (`call_args_list[2]`) — keep synthesis as the third `extract_chunk` call and keep `prior_context` a kwarg.
- R7 uncited figures fall to `"body"` (neutral) — acceptable; probe shows 4/7, 2/13, 8/10 uncited; the alternative is a wrong heading.
- R11 changes what production TEI contains (coords appear) — QC's `page_texts` will start carrying real pages; `extraction_coverage_ratio` metric behaviour on multi-page documents should be checked in the integration test.

## Design validation (2026-09-21, independent review)

Verdict **GO (conditional)**; three critical issues, all contract clarifications, applied to `design.md`:
1. **OCR text and `full_text`/`exact_text`** — the draft left unstated whether OCR page text joins `full_text`; without it every OCR sentence would miss the cursor search and generate flag noise. Now explicit: `reconcile()` builds `full_text` from primary blocks plus the OCR blocks used for sentences, in page order; all-scanned documents are an explicit case.
2. **FailureRecorder had three behaviours for one error path.** Now one: `_save_pdf_output` returns `(ok, FailureRecord | None)`, never touches the manifest, lets I/O errors propagate; `process_pdf` records both outcomes under `manifest_lock`. `_load_completed_result` lives in `pdf_processor.py`, not `manifest.py`.
3. **10.1 was asserted via the char cap, but the item cap binds.** Measured on the real fixtures: 150 items ≈ 26–29 k chars; coverage at 150/30 000 is biorxiv 82 %, plosone 62 %, arxiv 30 % (97 k-char paper, outside 10.1's premise). Design keeps 150 (it is what makes pruning happen), pins the 10.1 test to the biorxiv fixture, and requires the config comment to name the item cap as binding.
Minor: `_page_from_coords` is a closure inside `_extract_tei_payload` — hoisted to module-level `_page_from_tei_coords` for the cross-agreement test; config tests live in `tests/src/utils/test_quality_control_config.py`.
Spot-checks (13) all VERIFIED or REFUTED in the design's favour; D2/D3 break no `src/` consumer, only the hand-built fixtures already listed.
