# Implementation Tasks: architecture-migration

## Overview
13 major tasks, 44 sub-tasks. Organized into Foundation → Core → Integration → Validation phases following the 7-tier prerequisite ordering from the design. All requirements covered.

---

- [ ] 1. Foundation: Remove standalone Tesseract integration
- [x] 1.1 Delete Tesseract.py and remove all import references
  - Delete `pdf_extractor/extraction/Tesseract.py`
  - Remove `from .Tesseract import extract_with_tesseract` from `pdf_extractor/extraction/__init__.py`
  - `grep -r "extract_with_tesseract" .` returns zero results
  - `grep -r "pytesseract" .` returns zero results
  - _Requirements: 4_
  - _Boundary: pdf_extractor/extraction_

- [x] 1.2 Update test files that reference Tesseract
  - Remove all imports, mocks, and references to `extract_with_tesseract` from `test_text_extractor_tier2.py`
  - Verify `test_text_extractor_tier1.py` and `test_text_extractor_tier3.py` contain no Tesseract references
  - All three extractor test files pass with no Tesseract-related failures
  - _Requirements: 4, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 2. Foundation: Pluggable TextProcessor and SentenceSegment hierarchy
- [x] 2.1 Implement the TextProcessor class
  - Create `utils/text_processor.py` with the `TextProcessor` class
  - Implement `normalize()`: NFC/NFKC unicode normalization, whitespace collapse, ligature expansion (ﬁ→fi, ﬂ→fl), idempotent
  - Implement `compare()`: apply `normalize()` to both inputs, return `difflib.SequenceMatcher` ratio in `[0.0, 1.0]`
  - Implement `clean_ocr()`: strip U+FFFD and C0 control characters (`\x00`–`\x08`, `\x0b`, `\x0c`, `\x0e`–`\x1f`)
  - Implement `extract_keywords()`: lowercase, split, filter English stopwords
  - Implement `tokenize_words()`: normalize first, then delegate to configured word-tokenizer backend (`spacy` or `nltk`)
  - `__init__` resolves all three backend components from config; raises `ValueError` listing valid options for unknown backend names
  - `TextProcessor()` can be instantiated with `config={}` using all defaults without raising
  - _Requirements: 5.2, 5.3, 5.4, 5.5_
  - _Boundary: utils/text_processor_

- [x] 2.2 Implement SentenceSegment hierarchy
  - Define `SentenceSegment(TextProcessor)` abstract base class with `tokenize_sentences()` raising `NotImplementedError`
  - Implement `ScispaCySentenceSegment`: lazy-loads `en_core_sci_lg` on first call, caches model as instance attribute; raises `ImportError` with exact pip install command when scispaCy is absent
  - Implement `WtpSplitSentenceSegment`, `NLTKPunktSentenceSegment`, `SpacySentencizerSegment`, `StanzaSentenceSegment` following the same lazy-load + `ImportError`-with-hint pattern
  - `TextProcessor.__init__` reads `config["sentence_tokenizer"]["backend"]` and instantiates the matching `SentenceSegment` as `self._segmenter`
  - `TextProcessor.tokenize_sentences()` delegates entirely to `self._segmenter.tokenize_sentences()`
  - Passing a fully qualified custom class path in config loads that class via `importlib` without code changes
  - _Requirements: 5.1, 5.5_
  - _Boundary: utils/text_processor_

- [x] 2.3 Test TextProcessor and SentenceSegment
  - `normalize` is idempotent: `normalize(normalize(x)) == normalize(x)` for arbitrary strings
  - `normalize` collapses whitespace and expands ﬁ→fi, ﬂ→fl ligatures
  - `compare` returns `1.0` for identical strings, `0.0` for completely different strings
  - `clean_ocr` removes U+FFFD and C0 control characters
  - `extract_keywords` excludes stopwords and lowercases output
  - `tokenize_sentences` with each of the 5 backends raises `ImportError` with install hint when the backing package is absent (use mocks)
  - `tokenize_sentences` loads the model at most once per instance (mock `spacy.load` call counter)
  - Unknown `backend` value raises `ValueError` at `TextProcessor.__init__` time
  - `SentenceSegment` is a subclass of `TextProcessor`; all 5 built-in subclasses pass `isinstance(x, TextProcessor)`
  - Custom class-path injection via `_load_text_processor` works end-to-end with a mock backend
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 3. Foundation: QC data model extensions (P)
- [x] 3.1 (P) Add typed data layer dataclasses to models.py
  - Add `SemanticLayer`, `StructuralLayer`, `AlignmentMapEntry`, `AlignmentMap` dataclasses before `UnifiedRecord` in `quality_control/models.py`
  - All list fields use `field(default_factory=list)`
  - `AlignmentMapEntry.source` is `str` with default `"native"` — not constrained to any extractor name set
  - `AlignmentMapEntry.agreement` accepts `"full" | "partial" | "divergent" | "one_engine_only"`
  - `UnifiedRecord` gains three new optional fields: `semantic: SemanticLayer | None = None`, `structural: StructuralLayer | None = None`, `alignment: AlignmentMap | None = None`
  - Existing `UnifiedRecord.document_id` and `UnifiedRecord.content` fields are unchanged
  - `quality_control/__init__.py` exports all four new dataclasses in `__all__`
  - _Requirements: 1.1, 1.2, 1.3, 1.4_
  - _Boundary: quality_control/models_

- [x] 3.2 (P) Test new QC data model components
  - All four new dataclasses construct with default values, producing empty lists/None fields
  - `UnifiedRecord` with all three new layers set (non-None) is constructible
  - `UnifiedRecord.content` remains accessible alongside the new typed fields
  - `AlignmentMapEntry.source` accepts an arbitrary string — no test constrains it to a specific extractor name
  - Multiple `AlignmentMapEntry` objects accumulate correctly in `AlignmentMap.reconciliation_flags`
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 4. Foundation: Config extensions (P)
- [x] 4.1 (P) Extend _QC_DEFAULTS and config.yaml
  - Add `"text_processor"` top-level key to `_QC_DEFAULTS` in `utils/config_utils.py` with all sub-keys and documented defaults
  - Add `"scan_detection"`, `"ocr"`, `"text_fidelity"`, `"section_verification"` sub-keys inside `"quality_control"` in `_QC_DEFAULTS`
  - Add matching YAML blocks to `config/config.yaml` with inline comments documenting valid values
  - Instantiating `TextProcessor()` with an empty config dict (`{}`) uses all defaults without raising
  - Loading the existing `config.yaml` without any of the new keys still passes `load_qc_config()` validation
  - _Requirements: 9_
  - _Boundary: utils/config_utils, config_

---

- [ ] 5. Core: Per-page scan detection and schema extensions
  _Depends: 2.1_

- [x] 5.1 Extend block schemas for PaddleOCR metadata
  - Add `PaddleOCRBlockDict(BlockDict, total=False)` to `pdf_extractor/extraction/schemas.py` with optional `rasterization_dpi: int` and `ocr_confidence: float` fields
  - Add `make_ocr_block(text, page_index, block_bbox, rasterization_dpi, ocr_confidence) → PaddleOCRBlockDict` factory function
  - `validate_blocks()` continues to accept `PaddleOCRBlockDict` instances (existing validation unchanged)
  - All six existing callers of `make_block()` remain unmodified (named-argument pattern is preserved)
  - _Requirements: 3.2_
  - _Boundary: pdf_extractor/extraction/schemas_

- [x] 5.2 Create scan_detector module
  - Create `pdf_extractor/extraction/scan_detector.py` with `PageScanClassification` dataclass and `classify_page()` function
  - Stage 1 (empty text): fires immediately and short-circuits stages 2–5; `triggered_stages == [1]`
  - Stages 2–5 use config thresholds from `scan_detection.*` config keys
  - Stage 3 alpha-ratio computation calls `text_processor.clean_ocr()` on raw page text before counting characters
  - `is_native = True` only when `triggered_stages` is empty
  - `stage_values` dict records the computed signal for every evaluated stage (not just triggered stages)
  - Function is stateless: no module-level state, no caching
  - _Requirements: 2.1, 2.2, 2.3_
  - _Boundary: pdf_extractor/extraction/scan_detector_

- [x] 5.3 Add single-page font metadata extraction to PyMuPDF.py
  - Add `get_page_font_metadata(page) → list[FontMetaDict]` to `pdf_extractor/extraction/PyMuPDF.py`
  - Function extracts per-span font size, text, and page index from a single `fitz.Page`
  - Existing `extract_with_pymupdf(pdf_path)` function is unchanged
  - _Requirements: 3.1_
  - _Boundary: pdf_extractor/extraction/PyMuPDF_

- [x] 5.4 Test scan_detector
  - Stage 1 triggers on empty page text and short-circuits all subsequent stages
  - Stages 2, 3, 4, 5 each trigger independently when their respective condition fires (mocked `fitz.Page`)
  - `is_native = True` when zero stages fire
  - `stage_values` dict is populated with computed signal values for each evaluated stage
  - Mixed document: pages producing native and scanned classifications receive correct `is_native` values
  - _Requirements: 2.1, 2.2, 2.3, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 6. Core: Sentence processor update (P)
  _Depends: 2.1_

- [x] 6.1 (P) Remove regex sentence splitter and add TextProcessor parameter
  - Delete `_RE_SENTENCE_SPLIT` constant and its usage in `pdf_extractor/processing/sentence_processor.py`
  - Add `text_processor` as the third parameter to `process_sentences(text_blocks_with_pages, len_filter, text_processor)`
  - Replace the regex split call with `text_processor.tokenize_sentences(normalised)`
  - Update `pdf_extractor/pdf_extractor.py` to pass the `text_processor` instance to `process_sentences()`
  - `grep -r "_RE_SENTENCE_SPLIT" .` returns zero results
  - Existing sentence record output keys (`sentence`, `page_index`, `block_bbox`, `span_bboxes`) are unchanged
  - _Requirements: 5.1_
  - _Boundary: pdf_extractor/processing/sentence_processor, pdf_extractor/pdf_extractor_

---

- [ ] 7. Core: Concern strategy package
  _Depends: 3.1_

- [x] 7.1 (P) Implement TextFidelityConcern
  - Create `quality_control/concerns/text_fidelity.py` with `TextFidelityConcern` class
  - `reconcile(primary, reference, text_processor)` computes `edit_distance = 1.0 - text_processor.compare(primary, reference)`; sets `agreement` (`"full"`, `"partial"`, `"divergent"`) based on threshold; sets `preferred_reading = reference` (strategy encodes ground-truth side); sets `confidence = 1.0 - edit_distance`
  - `adjudicate(alignment_entries, config)` returns `{"preferred_source": str, "confidence": float, "rationale": str}`
  - `reconcile(a, b, tp)` and `reconcile(b, a, tp)` produce different `preferred_reading` values when `a != b`
  - `DEFAULT_TEXT_FIDELITY = TextFidelityConcern(source_label="pdfplumber")`
  - _Requirements: 7.3_
  - _Boundary: quality_control/concerns/text_fidelity_

- [x] 7.2 (P) Implement SectionVerificationConcern
  - Create `quality_control/concerns/section_verification.py` with `SectionVerificationConcern` class
  - `reconcile(primary_section, reference_block, text_processor)` compares heading text using `text_processor.compare()`; reduces confidence proportionally when `reference_block["font_size"]` is below the configured median threshold
  - Never modifies `primary_section` or any of its fields
  - Return type is `float` in `[0.0, 1.0]`, not a dict or `AlignmentMapEntry`
  - `DEFAULT_SECTION_VERIFICATION = SectionVerificationConcern()`
  - _Requirements: 7.3_
  - _Boundary: quality_control/concerns/section_verification_

- [x] 7.3 (P) Implement TableFigureMergeConcern
  - Create `quality_control/concerns/table_figure_merge.py` with `MissingContributionError(ValueError)` and `TableFigureMergeConcern` class
  - `merge(primary, reference)` raises `MissingContributionError` naming the absent side when either argument is `None`
  - When both arguments are present, returns merged dict with keys determined by constructor labels plus `"agreement"` and `"merged_text"`; primary record is unmodified in the merged output
  - `DEFAULT_TABLE_FIGURE_MERGE = TableFigureMergeConcern(primary_label="grobid", reference_label="pdfplumber")`
  - Create `quality_control/concerns/__init__.py` exporting all three strategy classes, their defaults, and `MissingContributionError`
  - _Requirements: 7.3_
  - _Boundary: quality_control/concerns/table_figure_merge_

- [x] 7.4 Test concern strategy package
  - `TextFidelityConcern`: identical inputs → `agreement="full"`, `edit_distance=0.0`; divergent inputs → `agreement="divergent"`; `preferred_reading` is always the `reference_artifact`; swapping argument order produces different `preferred_reading`
  - `SectionVerificationConcern`: matching heading and font size → high confidence; font size below threshold → reduced confidence; `primary_section` dict is not mutated; return value is `float`
  - `TableFigureMergeConcern`: both arguments present → merged record has both sub-fields with primary unmodified; `primary=None` → `MissingContributionError`; `reference=None` → `MissingContributionError`
  - `DEFAULT_TABLE_FIGURE_MERGE` uses `"grobid"` / `"pdfplumber"` as field label keys
  - _Requirements: 7.3, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 8. Core: W3C annotation layer (P)
  _Depends: 3.1_

- [ ] 8.1 (P) Create annotation data model and projection
  - Create `pdf_extractor/annotation/w3c_annotation.py` with `AnnotationRecord` dataclass and `project()` function
  - `project(unified, base_uri="")` reads only from `UnifiedRecord.semantic`, `UnifiedRecord.alignment` — never reads raw extractor output
  - Born-digital entries (`ocr_derived=False`): `selector_type="TextPositionSelector"`, `selector_payload={"start": int, "end": int}` from `alignment.sentence_to_char_range`
  - Scanned entries (`ocr_derived=True`): `selector_type="FragmentSelector"`, `selector_payload={"page": int, "xywh": str}` from PaddleOCR block bboxes in `structural.blocks`
  - Every record has a populated `quote_selector` with `exact`, `prefix`, `suffix` keys
  - `project()` returns `list[AnnotationRecord]` — no `json.dumps`, no dict construction, no JSON serialization
  - Create `pdf_extractor/annotation/__init__.py` exporting `project`, `AnnotationRecord`
  - _Requirements: 8.1_
  - _Boundary: pdf_extractor/annotation/w3c_annotation_

- [ ] 8.2 (P) Create W3C JSON-LD artifact generator
  - Create `pdf_extractor/annotation/artifact_generator.py` with `generate_w3c_jsonld()` as the sole producer of W3C JSON-LD dicts
  - Each dict contains `"@context"`, `"id"` (URN format: `urn:evitrace:anno:<uuid4>`), `"type"`, `"body"`, `"target"` fields
  - Born-digital records serialize with `TextPositionSelector`; scanned records serialize with `FragmentSelector` and `"ocr_derived": true`
  - `generate_w3c_jsonld([])` returns `[]` without raising
  - Export `generate_w3c_jsonld` from `pdf_extractor/annotation/__init__.py`
  - _Requirements: 8.2_
  - _Boundary: pdf_extractor/annotation/artifact_generator_

- [ ] 8.3 Test annotation layer
  - `project()` returns `list[AnnotationRecord]`, not `list[dict]`
  - Born-digital record has `selector_type == "TextPositionSelector"` and populated `quote_selector`
  - Scanned record has `selector_type == "FragmentSelector"` and `ocr_derived == True`
  - A single document with mixed page types produces records from both selector types
  - `generate_w3c_jsonld([])` returns `[]`
  - Born-digital record serializes to dict with all five required JSON-LD keys and `TextPositionSelector`
  - Scanned record serializes with `FragmentSelector` and `"ocr_derived": true` in body
  - Each `"id"` field matches `urn:evitrace:anno:` prefix pattern
  - _Requirements: 8.1, 8.2, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 9. Integration: Reconciler concern routing
  _Depends: 7.1, 7.2, 7.3_

- [ ] 9.1 Replace reconciler signature and implement concern routing
  - Replace hardcoded `grobid_artifact`/`pymupdf_artifact` parameters with extractor-agnostic `primary_artifact`, `secondary_artifact`, `primary_observation`, `secondary_observation`, `investigator_object` parameters plus keyword-only strategy and `text_processor` params
  - Add concern routing: call `text_fidelity_strategy.reconcile()` for paragraph/block pairs, `section_strategy.reconcile()` for section headings, `table_figure_strategy.merge()` for table/figure pairs
  - Strategy defaults fall back to `DEFAULT_TEXT_FIDELITY`, `DEFAULT_SECTION_VERIFICATION`, `DEFAULT_TABLE_FIGURE_MERGE` when not provided
  - `text_processor` defaults to `TextProcessor()` when not provided
  - The `PLACEHOLDER_NOTICE` backward-compat path (when `adjudication_decisions is None`) is retained
  - No string literal `"grobid"` or `"pdfplumber"` appears in `reconciler.py` control flow or output construction
  - _Requirements: 7.1_
  - _Boundary: quality_control/reconciler_

- [ ] 9.2 Implement AlignmentMap assembly and UnifiedRecord construction
  - Assemble `AlignmentMap` from collected `AlignmentMapEntry` results across all three concern types
  - Compute `sentence_to_char_range` by searching each sentence text in the `full_pdf_text` string from the primary artifact's `page_texts`; omit sentences not found and record them as `"one_engine_only"` entries in `reconciliation_flags`
  - Build `SemanticLayer` from primary-artifact block data (title, sections, paragraphs, references via text-pattern classification)
  - Build `StructuralLayer` from secondary-artifact blocks
  - Return `UnifiedRecord` with `semantic`, `structural`, and `alignment` populated alongside `content` (backward compat)
  - Returned `UnifiedRecord` has all three new fields set to non-None values when at least one branch is present
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 7.1_
  - _Boundary: quality_control/reconciler_

- [ ] 9.3 Test reconciler concern routing
  - `reconcile()` calls `text_fidelity_strategy.reconcile()` for paragraph-type content (verified via mock strategy)
  - `reconcile()` calls `section_strategy.reconcile()` for section-type content
  - `reconcile()` calls `table_figure_strategy.merge()` for table-type content
  - Custom strategy objects passed as keyword arguments are used instead of defaults
  - Returned `UnifiedRecord` has non-None `semantic`, `structural`, and `alignment` fields
  - `inspect.getsource(reconcile)` contains no literal strings `"grobid"` or `"pdfplumber"`
  - _Requirements: 7.1, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 10. Integration: Adjudicator strategy delegation (P)
  _Depends: 7.1, 7.2, 7.3_

- [ ] 10.1 (P) Remove symmetric scoring and decouple from reconciler
  - Remove `_compute_text_quality_score`, `_evaluate_extractor_quality`, and `_make_adjudication_decisions` functions
  - Remove `import reconciler` and the `reconciler.reconcile()` call at adjudicator line 218
  - Add `_adjudicate_concern(alignment_entries, strategy, config) → dict` helper that calls `strategy.adjudicate(alignment_entries, config)`
  - Refactor `adjudicate()` to iterate concern types, call `_adjudicate_concern` per type, and assemble a decisions dict; return the decisions dict (not the reconciler output)
  - `adjudicate()` accepts injectable `text_fidelity_strategy`, `section_strategy`, `table_figure_strategy` keyword params defaulting to `DEFAULT_*` instances
  - No string `"grobid"` or `"pdfplumber"` appears in `adjudicator.py` control flow or output-shaping logic
  - _Requirements: 7.2_
  - _Boundary: quality_control/adjudicator_

- [ ] 10.2 (P) Test adjudicator strategy delegation
  - `adjudicate()` calls `strategy.adjudicate(alignment_entries, config)` for each concern type (verified via mock)
  - `preferred_source` in the output dict is set entirely by the mock strategy's return value
  - A custom strategy returning `preferred_source="custom_extractor"` is used without modification
  - `inspect.getsource(adjudicate)` contains no literal strings `"grobid"` or `"pdfplumber"`
  - _Requirements: 7.2, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 11. Integration: Extraction routing replacement
  _Depends: 5.2_

- [ ] 11.1 Replace waterfall cascade with scan-detector routing
  - Remove `from .Tesseract import extract_with_tesseract` (if not already removed in Task 1.1)
  - Remove `_compute_quality_score` function
  - Replace waterfall cascade in `extract_pdf` with per-page `scan_detector.classify_page()` calls
  - Native pages: run pdfplumber (structural blocks) + PyMuPDF `get_page_font_metadata` (font metadata)
  - Scanned pages: run `extract_with_paddleocr(dpi=config["quality_control"]["ocr"]["rasterization_dpi"])`
  - When `ocr=False`: skip scan detection entirely and run pdfplumber only
  - GROBID is not called here — it runs separately as a distinct branch in `run_quality_control`
  - Function signature retains `ocr_text_quality_threshold` param for API compatibility (used as fallback only)
  - _Requirements: 3.1, 3.2, 3.3_
  - _Boundary: pdf_extractor/extraction/__init___

- [ ] 11.2 Update PaddleOCR to return per-block PDF coordinate bboxes
  - Add `dpi: int = 150` parameter to `extract_with_paddleocr()`
  - For each bounding box from PaddleOCR, apply coordinate mapping: `pdf_x = pixel_x * (72.0 / dpi)`, `pdf_y = pixel_y * (72.0 / dpi)`
  - Use `make_ocr_block()` factory to produce `PaddleOCRBlockDict` with `block_bbox` as 4-tuple of floats, `rasterization_dpi=dpi`, `ocr_confidence=float`
  - `block_bbox` is never `None` for PaddleOCR blocks after this change
  - `extract_with_paddleocr(path, dpi=150)` returns blocks where `block_bbox` is a 4-tuple of floats
  - _Requirements: 3.2_
  - _Boundary: pdf_extractor/extraction/PaddleOCR_

---

- [ ] 12. Integration: QC pipeline wiring
  _Depends: 2.1, 2.2, 6.1, 6.2, 6.3, 9.1, 9.2, 10.1_

- [ ] 12.1 Remove regex sentence splitter and wire TextProcessor into QC
  - Delete `_split_sentences` function (lines 138–144) from `quality_control/quality_control.py`
  - Add `_load_text_processor(config)` helper using `importlib` to resolve the configured class path
  - Instantiate one `TextProcessor` per `run_quality_control` call before any closures are defined
  - Replace all four `_split_sentences()` call sites with `text_processor.tokenize_sentences(text)`
  - Pass `text_processor` into `_build_tier1_report` so Invariant 1 is enforced throughout the rater closure
  - `grep -r "_split_sentences\|_RE_SENTENCE_SPLIT" quality_control/ pdf_extractor/` returns zero results
  - _Requirements: 5.1, 6.2_
  - _Boundary: quality_control/quality_control_

- [ ] 12.2 Wire concern strategies into the reconciler closure
  - In `_pdf_reconciler_fn`, identify the GROBID branch by `branch.extractor == "grobid"` and the pdfplumber/PyMuPDF branch by name; pass the payloads as extractor-agnostic `primary_artifact`/`secondary_artifact` to `reconciler.reconcile()`
  - Pass `text_fidelity_strategy=DEFAULT_TEXT_FIDELITY`, `section_strategy=DEFAULT_SECTION_VERIFICATION`, `table_figure_strategy=DEFAULT_TABLE_FIGURE_MERGE`, `text_processor=text_processor` to the reconciler call
  - The reconciler call site in `_pdf_reconciler_fn` contains no logic that selects a preferred extractor — all such logic is inside the injected strategies
  - `run_pipeline` function body contains no import or reference to any PDF-specific library, extractor name, or document-structure concept
  - _Requirements: 6.1, 6.2, 7.1_
  - _Boundary: quality_control/quality_control_

- [ ] 12.3 Wire annotation chain into the reconciler closure
  - After `reconciler.reconcile()` returns a `UnifiedRecord`, call `w3c_annotation.project(updated_unified)` to produce `list[AnnotationRecord]`
  - Call `artifact_generator.generate_w3c_jsonld(annotation_records)` and store the result on the `UnifiedRecord`
  - Both imports (`w3c_annotation`, `artifact_generator`) live inside `_pdf_reconciler_fn` or `run_quality_control` local scope — not at the `run_pipeline` body level
  - `UnifiedRecord` returned by `run_quality_control` has non-None `semantic`, `structural`, and `alignment` when at least one branch is present
  - _Requirements: 8.1, 8.2, 6.1_
  - _Boundary: quality_control/quality_control_

- [ ] 12.4 Test QC pipeline integration
  - Mock `TextProcessor.tokenize_sentences` to return `["sentence one", "sentence two"]`; verify `sentence_records` in `LocalQCReport` uses these mocked values (not a regex split)
  - Update `test_quality_control_reconciler.py` to pass mock concern strategies; verify `AlignmentMap` is populated and no hardcoded extractor names appear in the call
  - Update `test_quality_control_adjudicator.py` to pass mock strategies with `adjudicate()` methods; verify `preferred_source` comes from the mock strategy
  - All updated existing QC pipeline tests pass
  - _Requirements: 5.1, 7.1, 7.2, 10_
  - _Boundary: tests/pdf_extractor_

---

- [ ] 13. Validation: Domain-agnosticism invariant tests
  _Depends: 12.1, 12.2, 12.3, 10.1, 10.2, 9.3_

- [ ] 13.1 Test run_pipeline domain isolation
  - `run_pipeline` is callable with all-mock callables returning dummy model instances
  - `inspect.getsource(run_pipeline)` contains no string matching `fitz`, `grobid`, `pdfplumber`, `PyMuPDF`, `PaddleOCR`, `TEI`, `scan`, or `AlignmentMap`
  - Passing a non-PDF branch payload to `run_pipeline` does not raise
  - _Requirements: 6.1, 10_
  - _Boundary: tests/pdf_extractor_

- [ ] 13.2 Verify acceptance criteria grep checks
  - `grep -r "extract_with_tesseract" .` returns zero results
  - `grep -r "pytesseract" .` returns zero results
  - `grep -r "_split_sentences\|_RE_SENTENCE_SPLIT" quality_control/ pdf_extractor/` returns zero results
  - `inspect.getsource(reconcile)` contains no literal `"grobid"` or `"pdfplumber"` string in control-flow or output-shaping context
  - `inspect.getsource(adjudicate)` contains no hardcoded extractor name assigned to `preferred_source` or `primary_extractor`
  - A custom `SentenceSegment` subclass injected via `text_processor.class` config key is used end-to-end without pipeline code changes
  - `TextFidelityConcern.reconcile(a, b, tp)` and `TextFidelityConcern.reconcile(b, a, tp)` produce different `preferred_reading` values when `a != b`
  - `TableFigureMergeConcern.merge(None, x)` raises `MissingContributionError`; `TableFigureMergeConcern.merge(x, None)` raises `MissingContributionError`
  - `scan_detector.classify_page(page, tp, config)` returns `PageScanClassification` with `triggered_stages` recorded for every stage that fires
  - All existing tests (except targeted updates) pass without modification
  - _Requirements: 4, 5.1, 5.5, 6.1, 7.1, 7.2, 7.3, 10_
  - _Boundary: tests/pdf_extractor_
