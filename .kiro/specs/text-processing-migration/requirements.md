# Text Processing Migration — Requirements

## Purpose

This file specifies the TextProcessor and domain-agnostic text-processing migration (Phase 2). It extracts all domain-agnostic text primitives from their current scattered locations into a canonical `text_processing/` root package. Implementation must start only after Phase 1 (`qc-migration/requirements.md`) is complete — that spec stabilises QC result schemas, check contracts, and output hierarchy without touching text-processing internals.

## Scope Boundary

**This migration owns:**
- `text_processing/` root package (new)
- `TextProcessor` pure ABC and `SentenceSegment` ABC
- Five concrete sentence segmentation backends (preserved names)
- Text normalizer subclasses (`WhitespaceNormalizer`, `FullNormalizer`, `LineHealingNormalizer`, `UnicodeNormalizer`, `OcrCleaner`)
- `SimpleWordTokenizer` subclass
- `LexicalMatcher` and `SemanticMatcher` subclasses
- `EmbeddingProcessor` subclass
- Deletion of `utils/text_processor.py`, `pdf_extractor/utils/text_utils.py`, `pdf_extractor/utils/embedding_utils.py`
- Migration of `normalise_text` from `sentence_processor.py` to `LineHealingNormalizer`
- `tests/text_processing/` test directory
- Text-processing documentation updates

**This migration does NOT own:**
- QC result schemas, QC check contracts, QC output hierarchy (`qc-migration` Phase 1)
- QC class renames or QC package renames
- QC configuration schema creation
- `quality_control/checks/` implementation
- PDF-block-specific functions (`is_noise`, `process_sentences`, `build_full_text`)
- Concern strategy classes (`TextFidelityConcern`, etc.)
- Pipeline orchestration (`pipeline/extraction_pipeline.py`)
- `pdf_extractor/extraction/` backend logic

## Execution Order

1. Confirm Phase 1 (`qc-migration`) is complete before starting.
2. Do not change QC result schemas or QC check public contracts.
3. Implement `text_processing/` as an independent package.
4. Replace legacy implementations with canonical `text_processing/` implementations.
5. Wire callers to the new package. No compatibility shims.
6. Delete legacy files.
7. Update tests and documentation.

---

## Requirements

### Requirement 1: Establish the `text_processing/` Root Package

**User Story:** As a developer extending EviTrace, I want all domain-agnostic text primitives in one root package, so that they can be found, reused, and tested independently of QC.

#### Acceptance Criteria

1. A `text_processing/` root-level Python package SHALL exist with an `__init__.py`.
2. The package `__init__.py` SHALL export `TextProcessor`, `SentenceSegment`, `LexicalMatcher`, `SemanticMatcher`, and `EmbeddingProcessor`.
3. The package SHALL contain importable submodules: `text_processing.base`, `text_processing.normalizers`, `text_processing.tokenizers`, `text_processing.matchers`, `text_processing.embedding`.
4. `import text_processing` SHALL succeed without `faiss`, `torch`, `sentence-transformers`, `spacy`, `scispacy`, `stanza`, or `wtpsplit` installed.
5. The package SHALL NOT import from `quality_control/`, `pdf_extractor/`, `pipeline/`, or `agents/`.
6. The package SHALL NOT define QC result dataclasses, QC output hierarchy, QC check status values, or QC orchestration policy.
7. A new forbidden-import pair `("text_processing", "quality_control")` SHALL be added to `tests/test_dependency_directions.py`.

---

### Requirement 2: Make `TextProcessor` a Pure Abstract Base Class

**User Story:** As a developer subclassing text-processing components, I want `TextProcessor` to be a pure ABC, so that concrete behavior is owned by typed subclasses and there is no "god class."

#### Acceptance Criteria

1. `TextProcessor` SHALL live in `text_processing/base.py` and inherit from `abc.ABC`.
2. `TextProcessor` SHALL declare exactly six abstract methods: `normalize`, `tokenize_words`, `tokenize_sentences`, `clean_ocr`, `compare`, `extract_keywords`.
3. `TextProcessor` SHALL NOT contain concrete implementations of any of these methods.
4. Direct instantiation of `TextProcessor` SHALL raise the standard ABC `TypeError`.
5. Any concrete subclass that does not implement all six abstract methods SHALL raise `TypeError` on instantiation.
6. Concrete single-purpose subclasses SHALL implement unrelated abstract methods by raising `NotImplementedError` (not silently returning).

---

### Requirement 3: Preserve Sentence Segmenter Names

**User Story:** As a developer using sentence boundary detection, I want existing segmenter names preserved while their canonical home moves to `text_processing/`.

#### Acceptance Criteria

1. `SentenceSegment` SHALL inherit from both `TextProcessor` and `abc.ABC`, and SHALL declare `tokenize_sentences` as abstract.
2. The following concrete class names SHALL be preserved unchanged: `ScispaCySentenceSegment`, `WtpSplitSentenceSegment`, `NLTKPunktSentenceSegment`, `SpacySentencizerSegment`, `StanzaSentenceSegment`.
3. All five SHALL live in `text_processing/base.py`.
4. Each backend's NLP model SHALL be deferred until the first `tokenize_sentences()` call (lazy loading via `self._model`).
5. `self._model` SHALL be `None` before the first `tokenize_sentences()` call and non-`None` after it.
6. When the required package for a backend is not installed, the backend SHALL raise `ImportError` with the exact `pip install` command.
7. When `text_processor.class` in config is a fully qualified class path, the loader SHALL split on the final `.`, import via `importlib.import_module`, and instantiate with no positional arguments.

---

### Requirement 4: Move Concrete Text Processing to Typed Subclasses

**User Story:** As a developer, I want each text-processing responsibility in a dedicated typed subclass, so that each capability is independently testable and replaceable.

#### Acceptance Criteria

1. `text_processing/normalizers.py` SHALL provide `WhitespaceNormalizer` (current `normalise_ws` logic: collapse whitespace, lowercase).
2. `text_processing/normalizers.py` SHALL provide `FullNormalizer` (current `normalise_full` logic: whitespace + strip non-word chars).
3. `text_processing/normalizers.py` SHALL provide `LineHealingNormalizer` (current `normalise_text` logic from `sentence_processor.py`: heal mid-sentence line breaks, collapse newlines/spaces).
4. `text_processing/normalizers.py` SHALL provide `UnicodeNormalizer` (current `TextProcessor.normalize` logic: Unicode NFC/NFKC + whitespace collapse).
5. `text_processing/normalizers.py` SHALL provide `OcrCleaner` (current `TextProcessor.clean_ocr` logic: strip C0 controls and U+FFFD).
6. `text_processing/tokenizers.py` SHALL provide `SimpleWordTokenizer` (normalize then split on whitespace).
7. All four normalizers (`WhitespaceNormalizer`, `FullNormalizer`, `LineHealingNormalizer`, `UnicodeNormalizer`) SHALL be idempotent: `n(n(s)) == n(s)`.
8. All normalizers SHALL return empty string for empty string input.
9. Normalizer and tokenizer classes SHALL NOT define QC output statuses, QC evidence structures, or QC report schemas.

---

### Requirement 5: Implement `LexicalMatcher`

**User Story:** As a developer, I want exact-match search in a typed class under `text_processing/`, so that callers can reuse it without importing from `pdf_extractor/utils/`.

#### Acceptance Criteria

1. `LexicalMatcher` SHALL be a subclass of `TextProcessor` in `text_processing/matchers.py`.
2. `LexicalMatcher` SHALL implement `search(needle, full_text, page_texts, blocks) -> dict | None`.
3. The method SHALL preserve the existing two-pass normalization logic: Pass 1 via `WhitespaceNormalizer`, Pass 2 via `FullNormalizer` (only attempted when Pass 1 fails).
4. The method SHALL preserve cross-page span recovery via `SequenceMatcher`.
5. The method SHALL preserve 64-character prefix and suffix extraction.
6. The method SHALL preserve block and span bounding-box attribution.
7. When the whitespace-normalized needle has length < 10, the method SHALL return `None`.
8. When Pass 1 succeeds, the method SHALL NOT invoke Pass 2 (short-circuit).
9. When both passes fail, the method SHALL return `None`.
10. When a match spans a page boundary, the result SHALL be attributed to the page with the longest common substring overlap and SHALL emit a `DEBUG`-level log.
11. The returned dict SHALL contain: `found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`, `score` (1.0 for Pass 1, 0.9 for Pass 2).
12. The method SHALL return a raw candidate dict, not a QC result object.
13. When `full_text` is empty or `page_texts` is empty, the method SHALL return `None`.
14. `LexicalMatcher` SHALL NOT import from `quality_control/`.

---

### Requirement 6: Implement `EmbeddingProcessor`

**User Story:** As a developer, I want embedding model loading and FAISS index construction isolated in one class, so that semantic search dependencies are lazy and optional.

#### Acceptance Criteria

1. `EmbeddingProcessor` SHALL be a subclass of `TextProcessor` in `text_processing/embedding.py`.
2. `EmbeddingProcessor` SHALL implement: `load_embedding_model`, `embed_query`, `l2_normalise`, `build_faiss_index`, `build_sentence_store`.
3. Importing `text_processing.embedding` SHALL NOT raise `ImportError` when `faiss`, `torch`, or `sentence-transformers` are absent.
4. Missing-dependency errors SHALL be deferred to the first method call that requires them.
5. When `len(sentence_records) > max_sentences`, `build_sentence_store` SHALL emit `RuntimeWarning` and truncate to first `max_sentences` records.
6. Default `max_sentences` SHALL be `10_000`.
7. Default model name SHALL be `"BAAI/bge-base-en-v1.5"`.
8. No model loading, FAISS index construction, Torch import, or GPU access SHALL occur at import time or construction time.
9. `EmbeddingProcessor` SHALL NOT import from `quality_control/`.

---

### Requirement 7: Implement `SemanticMatcher`

**User Story:** As a developer, I want FAISS-based semantic search in a typed class, so that QC and other callers consume semantic candidates without owning FAISS logic.

#### Acceptance Criteria

1. `SemanticMatcher` SHALL be a subclass of `TextProcessor` in `text_processing/matchers.py`.
2. `SemanticMatcher` SHALL implement `search(query, sentence_store, embed_fn, threshold, page_texts) -> dict | None`.
3. Importing `text_processing.matchers` SHALL NOT raise `ImportError` when `faiss`, `torch`, or `sentence-transformers` are absent.
4. Missing-dependency errors SHALL be deferred to the first method call that requires them.
5. When `sentence_store["faiss_index"]` is `None`, the method SHALL return `None`.
6. When `sentence_store["sentences"]` is empty or missing, the method SHALL return `None`.
7. When top candidate score < `threshold`, the method SHALL return `None`.
8. The returned dict SHALL contain: `found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`, `score`.
9. The `score` SHALL represent cosine similarity in [0.0, 1.0].
10. The method SHALL NOT load an embedding model itself — it delegates to caller-provided `embed_fn`.
11. `SemanticMatcher` SHALL NOT import from `quality_control/`.

---

### Requirement 8: Delete Legacy Text Utility Paths

**User Story:** As a developer, I want obsolete text utility paths removed, so that there is one canonical location for each text-processing symbol.

#### Acceptance Criteria

1. `pdf_extractor/utils/text_utils.py` SHALL be deleted.
2. `pdf_extractor/utils/embedding_utils.py` SHALL be deleted.
3. `utils/text_processor.py` SHALL be deleted.
4. No shim module SHALL remain at any deleted path.
5. No re-export alias SHALL remain for any deleted text-processing symbol.
6. All test files with imports from deleted paths SHALL be updated to canonical `text_processing.*` paths.
7. When a caller imports from a deleted path, Python SHALL raise `ModuleNotFoundError` or `ImportError`.
8. All affected `__init__.py` files SHALL NOT re-export deleted names.

---

### Requirement 9: Preserve PDF Block Processing Boundary

**User Story:** As a developer maintaining PDF extraction, I want PDF-block-specific processing outside `text_processing/`, so that the new package remains domain-agnostic.

#### Acceptance Criteria

1. `normalise_text` SHALL migrate from `sentence_processor.py` to `text_processing/normalizers.py` as `LineHealingNormalizer`.
2. The function definition SHALL be removed from `sentence_processor.py` and replaced with `from text_processing.normalizers import LineHealingNormalizer`.
3. The call site SHALL invoke `LineHealingNormalizer().normalize(text)` (or cache a module-level instance for repeated calls).
4. `is_noise`, `process_sentences`, and `build_full_text` SHALL remain in `pdf_extractor/processing/sentence_processor.py`.
5. These three functions SHALL remain outside `text_processing/` because they operate on PDF extractor block dictionaries.
6. `text_processing/` SHALL NOT own PDF extractor block schemas or backend selection.

---

### Requirement 10: Complete Text-Processing Symbol Audit

**User Story:** As a developer performing the migration, I want a per-symbol audit table, so that no symbol is lost or duplicated.

#### Acceptance Criteria

1. The design document SHALL include a per-symbol audit table covering every public module-level name (not `_`-prefixed) in: `utils/text_processor.py`, `pdf_extractor/utils/text_utils.py`, `pdf_extractor/utils/embedding_utils.py`, `pdf_extractor/processing/sentence_processor.py`.
2. For each symbol, the table SHALL specify: current location, destination, replacement name (if renamed), migration action (`move`/`rename`/`delete`/`keep`), affected test files.
3. `normalise_text` SHALL be marked as renamed to `LineHealingNormalizer`.
4. `is_noise`, `process_sentences`, `build_full_text` SHALL be marked as kept.
5. Every module-level public name SHALL appear exactly once in the table.

---

### Requirement 11: Keep Text Processing Independent from QC

**User Story:** As a maintainer, I want the TextProcessor migration to avoid changing QC contracts, so that the two phases remain independently implementable.

#### Acceptance Criteria

1. This phase SHALL NOT require renaming `LocalQCReport`, `LocalQCMetricRecord`, or `quality_control/defaults/`.
2. This phase SHALL NOT require changing `QCBundle.metrics_hierarchy`.
3. This phase SHALL NOT require creating `VerificationResult`, `SourceTextPresenceCheck`, `SemanticSourceVerificationCheck`, `ExtractorAgreementCheck`, or `build_task_quality_scaffold`.
4. This phase SHALL NOT require creating or changing QC config defaults.
5. `text_processing/` SHALL expose raw candidate dicts and text-processing primitives only.
6. A steering-drift test SHALL verify that `text_processing/` does not import from `quality_control/`.

---

### Requirement 12: Text-Processing Tests

**User Story:** As a developer, I want comprehensive tests for text-processing behavior, so that the migration catches import, behavior, and optional-dependency regressions.

#### Acceptance Criteria

1. `tests/text_processing/` SHALL be created mirroring the `text_processing/` package structure.
2. Tests SHALL cover ABC instantiation guards (direct `TextProcessor()` → `TypeError`).
3. Tests SHALL cover each normalizer subclass in isolation with representative inputs.
4. Tests SHALL cover `LexicalMatcher` with in-memory stubs.
5. Tests SHALL cover `SemanticMatcher` with a mock FAISS index.
6. Tests SHALL cover `EmbeddingProcessor` with mocked optional dependencies.
7. Property-based tests for normalizer idempotence SHALL use `@given(st.text())` with `@settings(max_examples=100)`.
8. Tests exercising heavy semantic paths SHALL mock `faiss`, `torch`, `sentence-transformers` via `patch.dict("sys.modules", ...)`.
9. Tests exercising heavy semantic paths SHALL carry `pytestmark = pytest.mark.slow`.
10. Tests SHALL verify importing `text_processing` does not pull in heavy deps.
11. Tests SHALL verify imports from deleted legacy paths fail with `ModuleNotFoundError`.
12. `tests/steering/test_text_processing_separation.py` SHALL verify `text_processing/` does not import from `quality_control/` (AST-based).

---

### Requirement 13: Text-Processing Documentation

**User Story:** As an onboarding developer, I want documentation that identifies the canonical package and deleted old paths.

#### Acceptance Criteria

1. `pdf_extractor/utils/README.md` SHALL state that `text_utils.py` and `embedding_utils.py` have been deleted and point to `text_processing/`.
2. `.kiro/steering/product.md` SHALL include `text_processing/` in the architecture overview and module responsibilities table.
3. `.kiro/steering/product.md` SHALL NOT list `utils/text_processor.py`, `pdf_extractor/utils/text_utils.py`, or `pdf_extractor/utils/embedding_utils.py` as canonical.
4. `.kiro/steering/testing.md` SHALL add `tests/text_processing/` to the test layout table.

---

### Requirement 14: Text-Processing Non-Goals

**User Story:** As a maintainer, I want explicit non-goals, so that this phase does not expand beyond its boundary.

#### Acceptance Criteria

1. This phase SHALL NOT implement QC fallback selection or OCR fallback decision logic.
2. This phase SHALL NOT choose between GROBID/PyMuPDF/pdfplumber/PaddleOCR based on semantic metrics.
3. This phase SHALL NOT implement LLM-generated QC criteria.
4. This phase SHALL NOT migrate `route_row()` or `verify_row()` into parser core.
5. This phase SHALL NOT make `faiss`, `torch`, `sentence-transformers`, or GPU access required for normal imports.
6. This phase SHALL NOT introduce backwards-compatibility shims for deleted paths.
7. This phase SHALL NOT change QC result schemas, QC config keys, or QC output hierarchy (those are Phase 1).
8. This phase SHALL NOT change the evidence-map field schema or LLM output compact-key schema.

---

## Acceptance Summary

The text-processing migration is complete when:

1. `text_processing/` is the sole canonical home for domain-agnostic text-processing primitives.
2. `TextProcessor` is abstract; concrete behavior lives in typed subclasses.
3. Legacy text utility paths (`utils/text_processor.py`, `pdf_extractor/utils/text_utils.py`, `pdf_extractor/utils/embedding_utils.py`) are deleted with no shims.
4. Lexical and semantic search return raw candidate dicts, not QC result objects.
5. Semantic dependencies are optional and lazily imported.
6. PDF-block-specific processing remains in `pdf_extractor/processing/sentence_processor.py`.
7. Text-processing tests and documentation enforce independence from QC.
8. All public symbols from the four legacy modules are accounted for in the audit table (none lost, none duplicated).
