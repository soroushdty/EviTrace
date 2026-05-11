# Text Processing Requirements

## Purpose
This file specifies the TextProcessor and domain-agnostic text-processing migration only. It is Phase 2 and must start only after `qc_requirements.md` is complete.

## Scope Boundary
This migration owns `text_processing/`, TextProcessor abstraction, sentence segmentation backends, text normalizers, tokenizers, lexical search implementation, semantic search implementation, embedding/FAISS utilities, deleted legacy text utility paths, text-processing tests, and text-processing documentation.

This migration does not own QC result schemas, QC check contracts, QC output hierarchy, QC class renames, QC package renames, or QC configuration schema creation. Those belong to `qc_requirements.md` and must already be complete.

## Execution Order
1. Confirm `qc_requirements.md` is complete before starting this file.
2. Do not change QC result schemas or QC check public contracts while implementing this file.
3. Implement `text_processing/` as an independent package.
4. Replace legacy text utility implementations with canonical `text_processing/` implementations.
5. Wire callers to the new package without reintroducing old names or compatibility shims.

## Requirements

### Requirement 1: Establish the `text_processing/` Root Package
**User Story:** As a developer extending EviTrace, I want all domain-agnostic text primitives to live in one root package, so that they can be found, reused, and tested independently of QC.

#### Acceptance Criteria
1. THE `text_processing/` root-level Python package SHALL exist.
2. THE package SHALL include an `__init__.py` that exports at minimum `TextProcessor`, `LexicalMatcher`, `SemanticMatcher`, and `EmbeddingProcessor`.
3. THE package SHALL contain these importable submodules:
   - `text_processing.base`
   - `text_processing.normalizers`
   - `text_processing.tokenizers`
   - `text_processing.matchers`
   - `text_processing.embedding`
4. WHEN `import text_processing` is executed, THE package SHALL import successfully without requiring `faiss`, `torch`, `sentence-transformers`, `spacy`, `scispacy`, `stanza`, or `wtpsplit`.
5. IF a `text_processing:` YAML top-level section is introduced, THE top-level key SHALL be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `utils/config_utils.py`.
6. THE `text_processing/` package SHALL NOT import from `quality_control/quality_control.py` or any QC check module.
7. THE `text_processing/` package SHALL NOT define QC result dataclasses, QC output hierarchy, QC check status values, or QC orchestration policy.

### Requirement 2: Make `TextProcessor` a Pure Abstract Base Class
**User Story:** As a developer subclassing text-processing components, I want `TextProcessor` to be a pure abstract base class, so that concrete behavior is owned by typed subclasses.

#### Acceptance Criteria
1. THE `TextProcessor` class SHALL live in `text_processing/base.py`.
2. THE `TextProcessor` class SHALL inherit from `abc.ABC`.
3. THE `TextProcessor` class SHALL NOT contain concrete implementations of `normalize`, `tokenize_words`, `tokenize_sentences`, `clean_ocr`, `compare`, or `extract_keywords`.
4. THE `TextProcessor` ABC SHALL declare at minimum those six methods with `@abstractmethod`.
5. WHEN a caller instantiates `TextProcessor` directly, THE standard ABC `TypeError` SHALL be raised.
6. ANY concrete subclass that does not implement all declared abstract methods SHALL raise `TypeError` on instantiation.
7. Concrete single-purpose subclasses MAY implement unrelated abstract methods by raising `NotImplementedError`.
8. Concrete single-purpose subclasses SHALL NOT silently perform work outside their typed responsibility.

### Requirement 3: Preserve Sentence Segmenter Names Under `text_processing/`
**User Story:** As a developer using sentence boundary detection, I want existing sentence segmenter names preserved while their canonical home moves under `text_processing/`.

#### Acceptance Criteria
1. THE `SentenceSegment` ABC SHALL inherit from both `TextProcessor` and `abc.ABC`.
2. THE `SentenceSegment` ABC SHALL declare `tokenize_sentences` as abstract.
3. THE `SentenceSegment` ABC SHALL remain the abstract parent for existing concrete sentence-segmentation backends.
4. THE following concrete backend names SHALL be preserved:
   - `ScispaCySentenceSegment`
   - `WtpSplitSentenceSegment`
   - `NLTKPunktSentenceSegment`
   - `SpacySentencizerSegment`
   - `StanzaSentenceSegment`
5. THE heavy NLP model for each sentence backend SHALL be deferred until the first `tokenize_sentences()` call.
6. THE heavy NLP model for each sentence backend SHALL be stored in `self._model`.
7. THE value of `self._model` SHALL be `None` before the first `tokenize_sentences()` call.
8. WHERE a caller configures `text_processor.class` in `config.yaml` with a fully qualified class path, THE loader SHALL split the path on the final `.`, import the module via `importlib.import_module`, retrieve the class, and instantiate it with no positional arguments.

### Requirement 4: Move Concrete Text Processing Behavior to Typed Subclasses
**User Story:** As a developer, I want each text-processing responsibility to live in a dedicated typed subclass, so that each capability can be tested and replaced independently.

#### Acceptance Criteria
1. THE package SHALL provide `WhitespaceNormalizer` in `text_processing/normalizers.py` implementing the current `normalise_ws` logic.
2. THE package SHALL provide `FullNormalizer` in `text_processing/normalizers.py` implementing the current `normalise_full` logic.
3. THE package SHALL provide `LineHealingNormalizer` in `text_processing/normalizers.py` implementing the current `normalise_text` logic from `pdf_extractor/processing/sentence_processor.py`.
4. THE package SHALL provide `UnicodeNormalizer` in `text_processing/normalizers.py` implementing the current `utils/text_processor.TextProcessor.normalize` logic.
5. THE package SHALL provide `OcrCleaner` in `text_processing/normalizers.py` implementing the current `clean_ocr` logic.
6. THE package SHALL provide `SimpleWordTokenizer` in `text_processing/tokenizers.py` implementing the current simple `tokenize_words` logic.
7. WHEN `WhitespaceNormalizer`, `FullNormalizer`, `LineHealingNormalizer`, or `UnicodeNormalizer` is applied twice to the same string, THE result SHALL equal applying it once.
8. WHEN `WhitespaceNormalizer.normalize` receives an empty string, THE method SHALL return an empty string.
9. WHEN `FullNormalizer.normalize` receives an empty string, THE method SHALL return an empty string.
10. The normalizer and tokenizer classes SHALL NOT define QC output statuses, QC evidence structures, or QC report schemas.

### Requirement 5: Implement `LexicalMatcher`
**User Story:** As a developer, I want exact-match search to live in a typed class under `text_processing/`, so that callers can reuse it without importing from PDF extractor utilities.

#### Acceptance Criteria
1. THE `LexicalMatcher` class SHALL be a subclass of `TextProcessor` in `text_processing/matchers.py`.
2. THE `LexicalMatcher` SHALL implement:
   ```python
   search(needle: str, full_text: str, page_texts: dict[int, str], blocks: list[dict]) -> dict | None
   ```
3. THE method SHALL preserve the existing two-pass normalization logic from `exact_match_search`.
4. THE method SHALL preserve cross-page span recovery.
5. THE method SHALL preserve 64-character prefix and suffix extraction.
6. THE method SHALL preserve block and span bounding-box attribution.
7. WHEN the whitespace-normalized needle is shorter than 10 characters, THE method SHALL return `None`.
8. WHEN Pass 1 via `WhitespaceNormalizer` finds the normalized needle as a substring of normalized `full_text`, THE method SHALL return a result immediately and SHALL NOT invoke Pass 2.
9. WHEN both Pass 1 and Pass 2 via `FullNormalizer` fail, THE method SHALL return `None`.
10. WHEN a matched sentence spans adjacent pages but is not found in either page alone, THE result SHALL be attributed to the page with the longest common substring overlap and SHALL emit a `DEBUG`-level log message.
11. THE method SHALL return a raw candidate dict, not a QC result object.
12. THE returned candidate dict SHALL contain at minimum `found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`, and `score`.
13. THE score SHALL be `1.0` for Pass 1 and `0.9` for Pass 2.
14. WHEN called with empty `full_text`, THE method SHALL return `None`.
15. WHEN called with empty `page_texts`, THE method SHALL return `None`.
16. `LexicalMatcher` SHALL NOT import from `quality_control/`.

### Requirement 6: Implement `EmbeddingProcessor`
**User Story:** As a developer, I want embedding model loading and FAISS index construction to live in one text-processing class, so that semantic search dependencies are isolated and lazy.

#### Acceptance Criteria
1. THE `EmbeddingProcessor` class SHALL be a subclass of `TextProcessor` in `text_processing/embedding.py`.
2. THE `EmbeddingProcessor` SHALL wrap canonical implementations of `load_embedding_model`, `embed_query`, `l2_normalise`, `build_faiss_index`, and `build_sentence_store`.
3. WHEN `faiss`, `torch`, or `sentence-transformers` are not installed, importing `text_processing.embedding` SHALL NOT raise `ImportError`.
4. Missing optional-dependency errors SHALL be deferred to the first method call that requires the missing dependency.
5. IF the number of sentence records passed to `EmbeddingProcessor.build_sentence_store` exceeds `max_sentences`, THE method SHALL emit a `RuntimeWarning` and truncate to the first `max_sentences` records.
6. THE default `max_sentences` value SHALL be `10000` unless the caller passes a different value.
7. THE default model name SHALL be `BAAI/bge-base-en-v1.5` unless the caller passes a different value.
8. THE class SHALL NOT load an embedding model, build a FAISS index, import Torch, or touch GPU resources during normal package import.
9. THE class SHALL NOT import from `quality_control/`.
10. THE class SHALL NOT define QC behavior for unavailable semantic indexes.

### Requirement 7: Implement `SemanticMatcher`
**User Story:** As a developer, I want FAISS-based semantic search to live in a typed text-processing class, so that QC and other callers can consume semantic candidates without owning FAISS logic.

#### Acceptance Criteria
1. THE `SemanticMatcher` class SHALL be a subclass of `TextProcessor` in `text_processing/matchers.py`.
2. THE `SemanticMatcher` SHALL implement:
   ```python
   search(query: str, sentence_store: dict, embed_fn: callable, threshold: float, page_texts: dict | None) -> dict | None
   ```
3. WHEN `faiss`, `torch`, or `sentence-transformers` are not installed, importing `text_processing.matchers` SHALL NOT raise `ImportError`.
4. Missing optional-dependency errors SHALL be deferred to the first method call that requires the missing dependency.
5. WHEN `SemanticMatcher.search` is called with a `sentence_store` whose `faiss_index` key is `None`, THE method SHALL return `None`.
6. WHEN `SemanticMatcher.search` is called with a store whose `sentences` list is empty or missing, THE method SHALL return `None`.
7. WHEN the top candidate score is below `threshold`, THE method SHALL return `None`.
8. THE caller SHALL distinguish no-match conditions from index unavailability by validating store availability before calling the matcher.
9. THE method SHALL return a raw candidate dict, not a QC result object.
10. THE returned candidate dict SHALL contain at minimum `found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`, and `score`.
11. THE semantic `score` SHALL represent cosine similarity in range `0.0` to `1.0` after L2 normalization and inner-product search.
12. THE method SHALL NOT load an embedding model unless the caller-provided `embed_fn` does so.
13. THE class SHALL NOT import from `quality_control/`.

### Requirement 8: Delete Legacy Text Utility Paths Without Shims
**User Story:** As a developer, I want obsolete text utility paths removed, so that there is one canonical location for each text-processing symbol.

#### Acceptance Criteria
1. THE file `pdf_extractor/utils/text_utils.py` SHALL be deleted.
2. THE canonical implementations of `normalise_ws`, `normalise_full`, `exact_match_search`, and `semantic_search` SHALL live exclusively in `text_processing/` under class-based APIs.
3. THE file `pdf_extractor/utils/embedding_utils.py` SHALL be deleted.
4. THE canonical implementations of `load_embedding_model`, `embed_query`, `l2_normalise`, `build_faiss_index`, and `build_sentence_store` SHALL live exclusively in `text_processing/embedding.py`.
5. THE file `utils/text_processor.py` SHALL be deleted.
6. THE canonical implementations of `TextProcessor` and all `SentenceSegment` subclasses SHALL live exclusively in `text_processing/`.
7. No shim module SHALL remain at any deleted path.
8. No re-export alias SHALL remain for any deleted text-processing symbol.
9. ANY test file under `tests/` with direct imports from deleted paths SHALL be updated to canonical paths.
10. WHEN a caller imports from a deleted path, Python SHALL raise `ModuleNotFoundError` or `ImportError` immediately.
11. ALL affected `__init__.py` files SHALL NOT re-export deleted names or aliases.

### Requirement 9: Preserve PDF Block Processing Boundary
**User Story:** As a developer maintaining PDF extraction, I want PDF-block-specific sentence processing to stay outside `text_processing/`, so that the new package remains domain-agnostic.

#### Acceptance Criteria
1. THE `normalise_text` function in `pdf_extractor/processing/sentence_processor.py` SHALL migrate to `text_processing/normalizers.py` as `LineHealingNormalizer`.
2. THE function definition for `normalise_text` SHALL be removed from `sentence_processor.py`.
3. THE removed function SHALL be replaced with an import of `LineHealingNormalizer`.
4. THE call site SHALL invoke `LineHealingNormalizer().normalize(text)`.
5. THE functions `is_noise`, `process_sentences`, and `build_full_text` SHALL remain in `pdf_extractor/processing/sentence_processor.py`.
6. THE functions `is_noise`, `process_sentences`, and `build_full_text` SHALL remain outside `text_processing/` because they operate on block dictionaries produced by PDF extractor backends.
7. THE text-processing package SHALL NOT own PDF extractor block schemas.
8. THE text-processing package SHALL NOT own PDF extractor backend selection.

### Requirement 10: Complete Text-Processing Symbol Audit
**User Story:** As a developer performing the migration, I want a complete per-symbol audit table, so that no text-processing symbol is lost or duplicated.

#### Acceptance Criteria
1. THE text-processing spec or linked implementation notes SHALL include a per-symbol audit table covering every public module-level name, meaning any name not prefixed with `_`, in:
   - `utils/text_processor.py`
   - `pdf_extractor/utils/text_utils.py`
   - `pdf_extractor/utils/embedding_utils.py`
   - `pdf_extractor/processing/sentence_processor.py`
2. FOR EACH symbol, THE audit table SHALL specify current location, destination package/module, replacement name if renamed, migration action, and existing test files with direct imports referencing that symbol.
3. Allowed migration actions SHALL be `move`, `rename`, `delete`, or `keep`.
4. The audit table SHALL mark `normalise_text` as renamed to `LineHealingNormalizer`.
5. The audit table SHALL mark `is_noise`, `process_sentences`, and `build_full_text` as kept in `pdf_extractor/processing/sentence_processor.py`.
6. The audit table SHALL be verified for completeness by confirming that every module-level name not prefixed with `_` appears exactly once.
7. The audit table SHALL be verified for uniqueness by confirming that no symbol appears twice.
8. QC-only private helpers SHALL NOT be moved by this text-processing migration.

### Requirement 11: Keep Text Processing Independent from QC
**User Story:** As a maintainer, I want the TextProcessor migration to avoid changing QC contracts, so that the second phase can be implemented independently after QC is stable.

#### Acceptance Criteria
1. THIS file SHALL NOT require renaming `LocalQCReport`.
2. THIS file SHALL NOT require renaming `LocalQCMetricRecord`.
3. THIS file SHALL NOT require renaming `quality_control/defaults/`.
4. THIS file SHALL NOT require changing `QCBundle.metrics_hierarchy`.
5. THIS file SHALL NOT require creating `VerificationResult`.
6. THIS file SHALL NOT require creating `SourceTextPresenceCheck`, `SemanticSourceVerificationCheck`, `ExtractorAgreementCheck`, or `build_task_quality_scaffold`.
7. THIS file SHALL NOT require creating or changing QC config defaults.
8. THIS file SHALL NOT require defining QC behavior for `on_index_unavailable`.
9. THE `text_processing/` package SHALL expose raw candidate dictionaries and text-processing primitives only.
10. The pipeline or adapter layer MAY inject `LexicalMatcher` and `SemanticMatcher` into QC checks after both phases are complete.
11. A steering-drift test SHALL verify that `text_processing/` does not import from `quality_control/`.

### Requirement 12: Text-Processing Tests
**User Story:** As a developer, I want comprehensive tests for text-processing package behavior, so that the migration catches import, behavior, and optional-dependency regressions.

#### Acceptance Criteria
1. THE `tests/text_processing/` directory SHALL be created to mirror the `text_processing/` package structure.
2. Text-processing tests SHALL cover ABC instantiation guards.
3. Text-processing tests SHALL cover each normalizer subclass in isolation.
4. Text-processing tests SHALL cover `LexicalMatcher` with in-memory `page_texts` and `blocks` stubs.
5. Text-processing tests SHALL cover `SemanticMatcher` with a mock FAISS index.
6. Text-processing tests SHALL cover `EmbeddingProcessor` with mocked optional dependencies.
7. Property-based tests for normalizer idempotence SHALL use Hypothesis `@given` with `st.text()` and `@settings(max_examples=100)`.
8. ALL tests exercising `EmbeddingProcessor` or `SemanticMatcher` heavy paths SHALL mock `faiss`, `torch`, and `sentence-transformers` using `patch.dict("sys.modules", ...)` per repo convention.
9. Tests that exercise heavy semantic paths SHALL carry `pytestmark = pytest.mark.slow`.
10. Production-import tests SHALL verify that normal parser imports do not import test modules and do not require semantic dependencies.
11. Tests SHALL verify that `LexicalMatcher.search` returns `None` for needles shorter than 10 characters after whitespace normalization.
12. Tests SHALL verify that importing `text_processing` does not require heavy NLP, embedding, FAISS, Torch, GPU, or model-download dependencies.
13. Tests SHALL verify that imports from deleted legacy paths fail with `ModuleNotFoundError` or `ImportError`.
14. Steering-drift tests SHALL verify that deleted text utility functions are gone from old paths.
15. Steering-drift tests SHALL verify that `TextProcessor` is abstract.

### Requirement 13: Text-Processing Documentation
**User Story:** As a developer onboarding to text processing, I want documentation to identify the canonical package and deleted old paths.

#### Acceptance Criteria
1. THE file `pdf_extractor/utils/README.md` SHALL state that `text_utils.py` and `embedding_utils.py` have been deleted and that canonical implementations now live in `text_processing/`.
2. THE same README SHALL NOT present `text_utils`, `embedding_utils`, `normalise_ws`, `normalise_full`, `exact_match_search`, `semantic_search`, `load_embedding_model`, `embed_query`, `build_faiss_index`, or `build_sentence_store` as canonical APIs.
3. THE file `.kiro/steering/product.md` SHALL include `text_processing/` in the architecture overview and module responsibilities table.
4. THE same steering file SHALL NOT identify `utils/text_processor.py`, `pdf_extractor/utils/text_utils.py`, or `pdf_extractor/utils/embedding_utils.py` as canonical locations.
5. THE file `.kiro/steering/testing.md` SHALL add `tests/text_processing/` to the test layout table.
6. THE testing steering file SHALL document `text_processing/` as a covered area.
7. THE `CHANGELOG.md` SHALL include a text-processing migration entry enumerating moved symbols, renamed symbols, deleted modules, and added package names.
8. Text-processing documentation SHALL NOT describe QC output hierarchy or QC result statuses as owned by `text_processing/`.

### Requirement 14: Text-Processing Non-Goals
**User Story:** As a maintainer, I want explicit text-processing non-goals, so that this phase does not expand into QC or extraction policy changes.

#### Acceptance Criteria
1. THIS phase SHALL NOT implement QC fallback selection.
2. THIS phase SHALL NOT implement OCR fallback decision logic.
3. THIS phase SHALL NOT patch pages from alternate extractors.
4. THIS phase SHALL NOT choose GROBID, PyMuPDF, pdfplumber, PaddleOCR, or OCR output based on semantic agreement metrics.
5. THIS phase SHALL NOT implement LLM-generated task-specific QC criteria.
6. THIS phase SHALL NOT migrate downstream spreadsheet hallucination verification into parser core.
7. THIS phase SHALL NOT implement `route_row()` or `verify_row()` migration into parser core.
8. THIS phase SHALL NOT make FAISS, Torch, `sentence-transformers`, GPU access, or downloaded embedding models required for normal parser imports or normal parser runs.
9. THIS phase SHALL NOT introduce backwards-compatibility shims for deleted text-processing paths or names.
10. THIS phase SHALL NOT change the evidence-map field schema or the LLM output compact-key schema.
11. THIS phase SHALL NOT change QC result schemas, QC config keys, or QC output hierarchy defined by `qc_requirements.md`.

## Acceptance Summary
The TextProcessor migration is complete when:
1. `text_processing/` is the only canonical home for domain-agnostic text-processing primitives.
2. `TextProcessor` is abstract and concrete behavior lives in typed subclasses.
3. Legacy text utility paths are deleted with no shims.
4. Lexical and semantic search implementations return raw candidate dictionaries, not QC result objects.
5. Semantic dependencies are optional and lazily imported.
6. PDF block-specific processing remains in `pdf_extractor/processing/sentence_processor.py`.
7. Text-processing tests and documentation enforce independence from QC.
