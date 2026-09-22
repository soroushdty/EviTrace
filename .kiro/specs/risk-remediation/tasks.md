# Implementation Plan — risk-remediation

Binding order from design.md "Implementation sequencing": extractor identity → branch roles → OCR provenance chain; coordinates → figure attribution → evidence coverage; synthesis repair and page-classification cache are independent. Requirement 8 is already implemented (see task 12.2).

- [ ] 1. Foundation: configuration keys, canonical defaults, and shared test fixtures
- [x] 1.1 Add the repair-attempt limit and evidence-coverage threshold to configuration and reconcile the evidence-budget defaults
  - Add `retry.max_repair_attempts` (default 2) and `extraction.min_evidence_coverage_ratio` (default 0.6) to the config file, loader defaults, and env-override table (`OPENAI_MAX_REPAIR_ATTEMPTS`, `OPENAI_MIN_EVIDENCE_COVERAGE_RATIO`), following the existing env > yaml > default rule
  - Set `extraction.max_evidence_chars_per_chunk` to 30000 in the config file so it matches its own comment and the loader default; align the fallback pair in the PDF processor to 150 / 30000
  - Rewrite the evidence-budget comment to state the coverage rationale and that the 150-item cap is the binding constraint at typical sentence lengths; add a `token_budgets` subsection and the new `retry` key to the config README; update the steering config reference's `extraction` block
  - Done when `load_openai_config()` returns both new keys with defaults and env overrides, and a test asserts the config file, loader default, and README agree on 150 / 30000
  - _Requirements: 5.4, 10.3_
  - _Boundary: Config_

- [x] 1.2 Provide shared test helpers for the real GROBID TEI fixtures
  - Add a test helper that loads each file under the checked-in `tests/fixtures/grobid_tei/` directory by name and parses it
  - Add a small table of GROBID-format coordinate strings (single box, multi-box, malformed, empty) reusable by the coordinate tests in tasks 7 and 8
  - Done when a smoke test loads all three fixtures and asserts the known figure/table counts from the fixture README
  - _Requirements: 11.6_

- [ ] 2. Extractor identity in quality reports, agreement, and adjudication
- [x] 2.1 Populate the extractor name on production quality reports and use it as the identity key
  - Pass the branch's source name and index when the local-metrics report is constructed in the QC pipeline
  - Resolve names in inter-rater and adjudication built-ins as the report's extractor name, falling back to the positional index string when the name is empty or absent
  - Done when a three-branch run produces three pairwise keys named by source (for example `grobid_vs_paddleocr`), a `primary_extractor` equal to one of the branch sources, and a rationale that names it
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_
  - _Boundary: ExtractorIdentity_

- [x] 2.2 Test extractor identity at the pipeline level
  - Pipeline-level tests: report source equals branch source; pairwise key count is N·(N−1)/2 with distinct names; adjudication with pass/fail/pass across three named branches picks a real name
  - Unit test: reports with empty names fall back to `0_vs_1`-style keys
  - Done when these tests fail against the pre-change built-ins and pass after 2.1
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

- [ ] 3. Adjudication-driven reconciliation
- [x] 3.1 Select primary and secondary branches from the adjudication decision with a deterministic fallback
  - Replace the hard-coded extractor-name lookups in the QC reconciler callback with selection driven by the decision's primary extractor; when no branch matches, fall back to index order and log a warning naming the unmatched extractor
  - Choose the secondary as the first remaining branch in index order so the OCR-named branch is no longer dropped
  - Record the sources actually used as primary and secondary and the selection mode alongside the existing adjudication rationale in the output provenance
  - Done when a mixed run whose second branch is named `paddleocr` yields a non-empty structural layer, and provenance shows which branch was used and why
  - _Requirements: 2.1, 2.2, 2.3, 2.4_
  - _Boundary: BranchRoleSelector_

- [x] 3.2 Test branch-role selection
  - Cases: adjudicated match; no match → first branch with the warning text captured; single branch → no secondary; `paddleocr` secondary populates structural blocks; rationale present in provenance
  - Done when tests fail on the hard-coded selection and pass after 3.1; existing direct `reconcile()` tests unchanged
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [ ] 4. Block provenance from the extraction pipeline
- [x] 4.1 Tag every extracted block with its producing extractor and OCR flag
  - Document the optional origin keys on the block schema; add a single tagging step applied when native, scanned, all-native, and cache-hit block lists are built; keep the set of OCR-producing extractor names as one constant in the extraction pipeline
  - Set the same two keys (source `grobid`, not-OCR) on the blocks the quality-control TEI block builder emits — a deliberate one-line edit across the QC boundary so every block, whatever its producer, carries explicit origin keys
  - Extend the merged-order routing test to assert each merged block's source and OCR flag match the page it came from
  - Done when every block entering QC carries `source` and `ocr_derived`, and the routing test suite passes
  - _Requirements: 4.1_
  - _Boundary: BlockProvenance_

- [ ] 5. Sentence production and sentence location in the reconciler
- [x] 5.1 Make the reconciler the sole producer of sentences, including OCR pages
  - Copy origin keys from blocks onto paragraph records; produce sentence records from paragraphs (inheriting the flag) and from OCR blocks on pages that have no primary text; visit pages in order with native sentences before OCR sentences
  - Build the document text from primary blocks plus the OCR blocks used for sentences, in page order, and use that same text for the exact-text output
  - Paragraph and sentence records read origin keys with `.get` defaults (not-OCR, empty source) so blocks from older callers still work
  - Done when a mixed fixture yields sentences with both markings, an all-scanned fixture yields OCR sentences and non-empty exact text, and a native-only fixture yields identical sentence texts and pages to today
  - _Requirements: 4.2, 4.3, 4.4, 4.9_
  - _Boundary: SentenceProducer_

- [x] 5.2 Remove the fallback sentence loop from the QC pipeline
  - Delete the post-reconciliation sentence loop in the QC pipeline callback so the reconciler is the only producer; nothing else in the QC pipeline module changes
  - Done when the QC pipeline suite passes with the loop gone and sentence counts on native-only pipeline fixtures are unchanged (mixed and scanned fixtures gain OCR sentences by design)
  - _Requirements: 4.2, 4.9_
  - _Boundary: SentenceProducer_

- [ ] 5.3 Emit one location entry per sentence with page, offsets, occurrence, and region
  - Compute offsets per sentence with a monotonic cursor so repeated text gets distinct, increasing ranges; a miss records −1 offsets and a reconciliation flag, never a silent zero range
  - Carry the real page index, the OCR flag, the zero-based occurrence count for identical text, and the source block's bounding box when it has one
  - Keep the entries positionally aligned with the sentence list (same length, same order)
  - Done when a duplicate-sentence fixture yields distinct ranges and occurrences 0 and 1, a multi-sentence paragraph yields per-sentence ranges, and an OCR sentence's entry carries a bounding box
  - _Requirements: 3.3, 4.3, 4.6_
  - _Boundary: SentenceLocation_

- [ ] 5.4 Test sentence production and location in the reconciler suite
  - Preservation: native-only document produces identical sentence texts and pages before and after
  - Bug conditions: mixed markings, all-scanned document, OCR text present in exact text, per-sentence offsets, duplicate occurrences, alignment length equals sentence count
  - Done when the reconciler suite passes with the new producer and location entries
  - _Requirements: 4.2, 4.3, 4.4, 4.6, 4.9, 3.3_

- [ ] 6. Stable annotation identifiers and accurate OCR regions
- [ ] 6.1 Build annotation selectors from each sentence's own location entry and warn when a region is missing
  - Zip sentences with their location entries; stop reading the structural layer; native sentences get position selectors when offsets are valid, otherwise quote selectors only
  - OCR sentences with a bounding box get a fragment selector from that box; OCR sentences without one log a warning naming page and sentence prefix and continue with a quote selector
  - Handle a missing or length-mismatched alignment by falling back to quote selectors with a single log line
  - Done when the region test with two OCR blocks on one page selects the second sentence's own box, and the missing-region test captures the warning and still emits the remaining annotations
  - _Requirements: 4.5, 4.7_
  - _Boundary: RegionSelector_
  - _Depends: 5.3_

- [ ] 6.2 Derive deterministic annotation identifiers and scope them to the paper
  - Replace random identifiers with name-based UUIDs computed from document source, page, occurrence, and sentence text under a fixed namespace
  - Pass a paper-scoped document source from the extraction pipeline's single annotation call site so identical text in different papers gets different identifiers
  - Done when annotating the same record twice yields byte-identical identifiers, two papers with the same sentence differ, and duplicate sentences in one paper differ
  - _Requirements: 3.1, 3.2, 3.3, 3.4_
  - _Boundary: AnnotationIdentity_
  - _Depends: 5.3_

- [ ] 6.3 Update the annotation test suite
  - Replace the two random-UUID shape assertions with determinism, distinctness, and cross-paper tests; convert hand-built records that supplied regions through the structural layer to alignment entries
  - Done when the annotation suite passes and no test constructs a structural layer to drive region selection
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 4.5, 4.7_

- [ ] 7. GROBID coordinates: request, parse, and propagate
- [ ] 7.1 (P) Request coordinates in the form GROBID accepts and parse its real grammar
  - Send the coordinate element list as repeated form fields instead of one comma-joined value
  - Add a canonical parser for `page,x,y,w,h` boxes separated by `;` that returns a list of boxes (page as emitted, 1-based) and an empty list for absent or malformed input; make the existing GROBID parser delegate to it (first box's page converted to 0-based as today, union of boxes on that page)
  - Done when a unit test asserts the posted form carries the list value, and a slow, opt-in test against a live GROBID returns TEI with coordinate attributes
  - _Requirements: 11.1, 11.2, 11.5_
  - _Boundary: GrobidRequest, CoordsParser_

- [ ] 7.2 Use the canonical parser in the evidence index and inherit sentence pages from paragraphs
  - Route the evidence index's coordinate parsing through the canonical parser, keeping evidence-item pages 1-based as today; when a sentence has no coordinates, take the page from its enclosing paragraph
  - Done when parsing a real fixture yields a non-`None` page for coordinate-bearing figures and for sentences inside coordinate-bearing paragraphs
  - _Requirements: 11.2, 11.3, 11.5_
  - _Boundary: CoordsParser_

- [ ] 7.3 Mirror the grammar in the quality-control TEI parser
  - Hoist the page-from-coordinates closure to a module-level function and rewrite it to the GROBID grammar (first box's page converted to 0-based, zero on absence or malformed input); the module may not import the extractor package
  - Done when GROBID-derived QC blocks from a real fixture land on their real pages instead of page zero
  - _Requirements: 11.2, 11.5_
  - _Boundary: CoordsParser_

- [ ] 7.4 Cross-agreement and fixture tests for coordinates
  - Feed the shared coordinate-string table from 1.2 to the QC page function and the GROBID parser's 0-based page and assert they agree (both 0-based); assert the canonical parser's first box page equals that value plus one; assert the legacy `page;x0,y0,x1,y1` fixture format is no longer used by any TEI fixture that asserts pages
  - Done when the cross-agreement test passes and all TEI fixtures asserting pages use the real grammar
  - _Requirements: 11.2, 11.5, 11.6_
  - _Depends: 1.2_

- [ ] 8. Figure and table section attribution
- [ ] 8.1 Attribute figures and tables to the first citing section, one item each, with real pages
  - Build a map from figure/table identifiers to the heading of the first body section that cites them; reset the section label per section (heading text or the neutral `body` label) instead of carrying it across sections
  - Iterate top-level figures once after the sections: a table-typed figure yields one table item (caption plus rows, addressed by the figure's identifier), any other figure yields one caption item; remove the separate table loop; attribute uncited items to the neutral label; take the page from the parser of 7.1
  - Done when the arXiv fixture yields 10 caption items and 3 table items across at least two distinct sections with `tab_0` attributed to its citing section, and a head-less section fixture attributes its sentences to the neutral label
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 11.4_
  - _Boundary: FigureAttribution_
  - _Depends: 7.2_

- [ ] 8.2 Test figure attribution on real and synthetic fixtures
  - Real fixtures: item counts, ≥2 distinct sections, exactly one item per figure and per table, uncited figures → neutral label; the bioRxiv fixture drops caption-less figures as today
  - Existing nested-figure fixtures still produce items with the neutral label
  - Done when the evidence-index suite passes with the single-loop implementation
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 9. Evidence coverage per paper
- [ ] 9.1 Measure evidence selection coverage and expose it from the evidence index
  - Split selection from serialisation: a selection function returns the chosen items plus statistics (total items, substantive characters excluding the metadata section, selected items and characters, coverage ratio); the package builder wraps it with an unchanged signature
  - Leave the ranking loop and both caps unchanged
  - Done when the bioRxiv fixture at default configuration reports a coverage ratio of at least 0.6, and the existing property tests on item and character caps still pass
  - _Requirements: 10.1, 10.2, 10.5_
  - _Boundary: EvidenceCoverage_
  - _Depends: 1.1, 8.1_
  - Sequential after task 8: same module, and the bioRxiv item count assumed by the coverage test holds only after 8.1's table de-duplication

- [ ] 9.2 Record the coverage outcome per paper
  - After selection in the PDF processor, write the ratio, selected and substantive characters, and a below-threshold flag into the paper's manifest entry; log a warning when below the configured threshold
  - Done when a completed paper's manifest entry carries the coverage record and a below-threshold paper produces the warning
  - _Requirements: 10.4_
  - _Boundary: EvidenceCoverage_

- [ ] 9.3 Align evidence-budget test constants and add coverage tests
  - Update the token-efficiency regression mirror constant and the concurrency test's hard-coded 250/60000 to the canonical 150/30000
  - Done when the pipeline suite passes at the new defaults and a test asserts the manifest coverage record shape
  - _Requirements: 10.1, 10.3, 10.4_

- [ ] 10. Synthesis-stage repair and a single failure-record shape
- [ ] 10.1 Extend the repair loop with the synthesis-only inputs
  - Add keyword-optional prior context, stage key, and protected evidence identifiers to the repair loop; include prior context in the budget estimate; forward stage and protected identifiers to the budget check and prior context to every model call; defaults reproduce current behaviour exactly
  - Done when existing repair-loop tests pass unchanged and a new test shows all three inputs forwarded on both the initial and repair calls
  - _Requirements: 5.1, 5.2, 5.4_
  - _Boundary: SynthesisRepair_
  - Sequential after 9.2: same module as the coverage record

- [ ] 10.2 Route the synthesis stage through the repair loop with the configured attempt limit
  - Replace the raw synthesis call and single validation with the repair loop using the synthesis stage key; construct the repair loop once per paper with the configured attempt limit and share it with the chunk stage
  - Done when a malformed-then-valid synthesis response is repaired and merged, synthesis remains the third model call in the helper tests, and prior context is present on both calls
  - _Requirements: 5.1, 5.2, 5.4_
  - _Boundary: SynthesisRepair_
  - _Depends: 1.1_

- [ ] 10.3 Record failures with one manifest shape for chunks, synthesis, and the output write
  - Add a single failure-recording helper writing status, last error, and a list of failure records (stage, chunk, error type, last error, attempts); keep the existing status vocabulary and the compatibility list of failed chunk numbers; add the `failed_output_write` status
  - Make the output-write gate return success or a failure record without touching the manifest and without catching I/O errors; have the paper processor persist validation failures and write errors under the manifest lock, re-raising write errors
  - Done when chunk exhaustion, synthesis exhaustion, schema-validation failure, and a write error each produce a manifest entry with the same record shape, and the pipeline README documents it
  - _Requirements: 1.3, 1.4, 5.3, 5.4_
  - _Boundary: FailureRecorder_

- [ ] 10.4 Test synthesis repair, failure records, and the final-output negative path
  - Synthesis exhaustion → `failed_chunk_<n>` with failure records; write error → `failed_output_write` and re-raise; invalid field → no output file, `failed_schema_validation`, and a log line carrying the field identity; valid fields → file written
  - Extend the integration test's expected-status set; adapt the output-gate tests to the new return shape
  - Done when these tests pass and the pre-existing valid-path tests are unchanged in outcome
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 5.3, 5.4_

- [ ] 11. Page classification on cached parses
- [ ] 11.1 Unify branch construction into one function used by the cache-miss path
  - Extract the classification-to-branches logic (native/scanned index sets, OCR when needed and enabled, block tagging, page-ordered merge, branch naming, routing results) from the miss path into one function; the miss path calls it — a pure refactor at this step
  - Done when every existing routing test passes unchanged and the function is a pure function of its inputs (property test: same inputs → same branches and routing results)
  - _Requirements: 6.2, 6.4_
  - _Boundary: RoutingUnifier_
  - _Depends: 4.1_
  - Not parallel with task 4: same module

- [ ] 11.2 Persist per-page classification beside the TEI cache and route the cache-hit path through the unified function
  - Write a versioned sidecar keyed by the PDF digest containing a hash of the scan-detection configuration and each page's classification fields; read it on a TEI cache hit and treat absence, corruption, version mismatch, or hash mismatch as a miss with an informational log naming the reason
  - On a hit without a usable sidecar, classify pages if the PDF library is importable and write the sidecar; otherwise log an error and use the all-native default
  - Make the cache-hit path call the unified function from 11.1 with the persisted or recomputed classifications instead of building all-native branches itself
  - Done when a second run on a mixed PDF reads the sidecar without opening pages for classification, routes scanned pages to OCR, and produces the same branches and routing results as the first run
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  - _Boundary: PageClassificationCache, RoutingUnifier_
  - Integration task across the two task-11 boundaries; not parallel with task 4 (same module)

- [ ] 11.3 Add the first cache-hit tests
  - Hit with valid sidecar and scanned pages → OCR invoked, routing reasons equal to a miss run; hit without sidecar → classification computed and sidecar written; stale hash → recompute; unreadable sidecar and no PDF library → error log and all-native
  - New helpers are patched at the extraction-pipeline module path like the existing seams
  - Done when these tests pass alongside the existing routing suites
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [ ] 12. Integration and validation
- [ ] 12.1 End-to-end provenance through the QC bundle
  - With mocked backends, build the QC bundle for a mixed PDF twice (miss then hit) and assert: every annotation's OCR marking equals its sentence's marking; structural blocks are non-empty; provenance names the branches used; branches and routing are identical across the two runs
  - Done when the integration test passes
  - _Requirements: 4.8, 6.4, 2.4_
  - _Depends: 6.1, 6.2, 11.2_

- [ ] 12.2 Migration bug-condition test file
  - One sub-check per requirement group that fails on the pre-change tree and passes now: named primary extractor; three pairwise keys for three branches; structural blocks with an OCR-named secondary; stable annotation identifiers; OCR sentence for a scanned fixture; region from the sentence's own box; synthesis repaired; sidecar written; ≥2 figure sections on the arXiv fixture; one item per table; coverage ≥ 0.6 on the bioRxiv fixture; real coordinate string parses to a page; the three QC bases reject direct instantiation
  - Sub-checks use `pytest.fail()` with messages naming the requirement
  - Done when the file passes on the current tree and its sub-checks are shown to fail on the pre-change tree
  - _Requirements: 2.1, 3.1, 4.2, 4.5, 5.1, 6.3, 7.3, 7.5, 8.1, 8.2, 8.3, 9.2, 10.1, 11.2_

- [ ] 12.3 Migration preservation test file
  - Behaviour that must not regress: native-only sentence texts and pages; direct reconciler calls; final-output validator acceptance of map-produced fields; the `failed_chunks` compatibility list; native position selectors; the existing built-in QC implementations instantiate and behave as before
  - Done when the file passes on both the pre-change and current trees
  - _Requirements: 1.1, 1.2, 4.3, 8.2, 8.3_

- [ ] 12.4 Full verification and changelog
  - Run the complete suite including slow tests and the dependency-direction suite; run the config-key consistency test from 1.1
  - Add the changelog entry per the changelog rules covering: behaviour changes (annotation ids, OCR sentences, branch selection, synthesis repair, coverage record, sidecar file, coordinates), config default change, and new keys
  - Done when every test passes and the changelog entry exists
  - _Requirements: 10.3_

## Implementation Notes
- 1.1: `tests/src/pipeline/test_token_efficiency_regression.py:150-153` and `test_orchestrator_concurrency.py:31-32` hard-code evidence budgets as literals (they never read config.yaml), so the 30000 change does not break them — task 9.3 must still update them. Root `README.md:327-339` env-override table lacks the two new env vars (outside 1.1 boundary) — route to final validation / 12.4.
- 1.2: shared TEI helpers live in `tests/helpers/grobid_tei.py` (import as `from tests.helpers.grobid_tei import ...`); root conftest.py now also appends the repo root to sys.path (load-bearing on pytest 8.0.x) — `.kiro/steering/testing.md:40` needs a one-line sync at 12.4. `COORD_CASES` has no whitespace-bearing case; task 7 may add one if `parse_tei_coords` tolerates whitespace.
- 2.1: with distinct names, `AdjudicationDecision.adjudicate`'s pre-existing formula gives `confidence <= 1/N` (one report per name) and ties break by index order (index 0 wins when all pass; all-fail still elects index 0 at 0.0). No requirement governs `confidence`; no src consumer branches on it. **Open question for 3.1 / final validation**: what should adjudication confidence mean now? `test_builtin_impls_identity.py:71` pins `1/3` and must be relaxed if the formula changes. The 2.1 tests already cover every bullet of task 2.2 (reviewer reproduced their failure against pre-change code).
- 2.2: satisfied by the tests delivered in 2.1 (`test_quality_control_identity.py`, `test_builtin_impls_identity.py`); controller re-verified the done condition in a worktree at 84ba4b1 (pre-2.1): 6 failed / 3 passed there, 9 passed on the current tree. No separate implementer dispatched.
- 3.1: provenance keys are attached in `_pdf_reconciler_fn` after `reconcile()` returns (`_build_provenance_dict` takes fixed kwargs). The fallback warning is only reachable via the helper's unit test — since 2.1 the adjudicator always names a real branch end-to-end. Empty branch list also logs the fallback warning (harmless).
- 3.2: `run_quality_control` has no adjudicator injection seam; end-to-end fallback test monkeypatches the module-level `AdjudicationDecision` name (late-bound in `_pdf_adjudicator_fn`). A non-GROBID primary is produced end-to-end by feeding GROBID a degraded TEI (real rater fails it 5/8 metrics at `max_triggered_fraction` 0.5) — margin is one metric; the test's precondition guard makes drift loud.
- 4.1: `_tag_blocks` returns shallow copies (routing tests share `_make_block` dicts across mocked extractors). `_extract_branch_payload`'s legacy non-TEI fallbacks (`quality_control.py:336,349`) still emit untagged blocks — unreachable from pipeline payloads; 5.1 must read origin keys with `.get` defaults. `scanned_pymupdf_blocks` is only logged, never a branch.
- 5.1: `reconcile()` keeps `sentence_sources` (paragraph dict with `block_index` for native sentences; the secondary block dict with `block_bbox` for OCR sentences) as a local for 5.3. `content["segments"]`/`["pages"]` are still primary-only while `exact_text` includes used OCR blocks — confirm acceptable at 5.3/12.4 (all known consumers read `exact_text`). Pre-existing env gap: config selects `nltk_punkt` but nltk is not installed in the venv (`requirements.txt:34` commented) — route to 12.4. Native-only output is byte-identical except that empty tokenizer outputs are skipped.
- 5.2: quality_control.py calls `reconciler.reconcile(...)` via module attribute (line ~653) — tests that patch reconcile must target `quality_control.reconciler.reconcile`. Pre-existing: `_extract_tei_payload` concatenates adjacent `<p>` texts without a separator.
