# Tasks: text-processing-migration

## Task 1: Create `text_processing/` package skeleton

**Requirements:** 1.1, 1.3

### Subtasks

- [ ] 1.1 Create `text_processing/__init__.py` with empty `__all__` list
- [ ] 1.2 Create `text_processing/base.py` with placeholder comment
- [ ] 1.3 Create `text_processing/normalizers.py` with placeholder comment
- [ ] 1.4 Create `text_processing/tokenizers.py` with placeholder comment
- [ ] 1.5 Create `text_processing/matchers.py` with placeholder comment
- [ ] 1.6 Create `text_processing/embedding.py` with placeholder comment
- [ ] 1.7 Verify `import text_processing` succeeds with no heavy dependencies

---

## Task 2: Implement `TextProcessor` ABC and `SentenceSegment` ABC in `text_processing/base.py`

**Requirements:** 2.1–2.6, 3.1–3.7

### Subtasks

- [ ] 2.1 Define `TextProcessor(abc.ABC)` with six `@abstractmethod` declarations: `normalize`, `tokenize_words`, `tokenize_sentences`, `clean_ocr`, `compare`, `extract_keywords`
- [ ] 2.2 Define `SentenceSegment(TextProcessor, abc.ABC)` with `__init__(config)` setting `self._model = None` and abstract `tokenize_sentences`
- [ ] 2.3 Move `ScispaCySentenceSegment` from `utils/text_processor.py` preserving class name and lazy-load pattern
- [ ] 2.4 Move `WtpSplitSentenceSegment` from `utils/text_processor.py` preserving class name and lazy-load pattern
- [ ] 2.5 Move `NLTKPunktSentenceSegment` from `utils/text_processor.py` preserving class name and lazy-load pattern
- [ ] 2.6 Move `SpacySentencizerSegment` from `utils/text_processor.py` preserving class name and lazy-load pattern
- [ ] 2.7 Move `StanzaSentenceSegment` from `utils/text_processor.py` preserving class name and lazy-load pattern
- [ ] 2.8 Ensure each concrete backend raises `ImportError` with exact `pip install` command when package missing
- [ ] 2.9 Ensure each concrete backend implements unrelated abstract methods (`normalize`, `tokenize_words`, `clean_ocr`, `compare`, `extract_keywords`) by raising `NotImplementedError`

---

## Task 3: Implement normalizer subclasses in `text_processing/normalizers.py`

**Requirements:** 4.1–4.9

### Subtasks

- [ ] 3.1 Implement `WhitespaceNormalizer(TextProcessor)` — collapse whitespace + lowercase; verify idempotency
- [ ] 3.2 Implement `AggressiveNormalizer(TextProcessor)` — whitespace + strip non-word chars; verify idempotency
- [ ] 3.3 Implement `LineHealingNormalizer(TextProcessor)` — heal mid-sentence line breaks, collapse newlines/spaces; verify idempotency
- [ ] 3.4 Implement `UnicodeNormalizer(TextProcessor)` — NFC/NFKC + whitespace collapse; configurable `form` parameter; verify idempotency
- [ ] 3.5 Implement `OcrCleaner(TextProcessor)` — strip C0 controls and U+FFFD; preserve tab/LF/CR
- [ ] 3.6 Ensure all normalizers return empty string for empty input
- [ ] 3.7 Ensure all normalizer classes implement unrelated abstract methods by raising `NotImplementedError`

---

## Task 4: Implement `SimpleWordTokenizer` in `text_processing/tokenizers.py`

**Requirements:** 4.6

### Subtasks

- [ ] 4.1 Implement `SimpleWordTokenizer(TextProcessor)` that composes a `UnicodeNormalizer` and splits on whitespace
- [ ] 4.2 Ensure `tokenize_words("")` returns `[]`
- [ ] 4.3 Implement unrelated abstract methods by raising `NotImplementedError`

---

## Task 5: Implement `LexicalMatcher` in `text_processing/matchers.py`

**Requirements:** 5.1–5.14

### Subtasks

- [ ] 5.1 Implement `LexicalMatcher(TextProcessor)` with `search(needle, full_text, page_texts, blocks) -> dict | None`
- [ ] 5.2 Implement pre-check: return `None` when whitespace-normalized needle length < 10
- [ ] 5.3 Implement empty guards: return `None` for empty `full_text` or empty `page_texts`
- [ ] 5.4 Implement Pass 1 via `WhitespaceNormalizer` — substring check; score `1.0`; short-circuit (no Pass 2)
- [ ] 5.5 Implement Pass 2 via `AggressiveNormalizer` — only when Pass 1 fails; score `0.9`
- [ ] 5.6 Return `None` when both passes fail
- [ ] 5.7 Implement page attribution: find page containing normalized needle; cross-page fallback via `SequenceMatcher` with `DEBUG` log
- [ ] 5.8 Implement span recovery via `SequenceMatcher` on matched page text
- [ ] 5.9 Implement 64-char prefix/suffix extraction from original page text
- [ ] 5.10 Implement block/span bounding-box attribution
- [ ] 5.11 Verify returned dict schema: `found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`, `score`
- [ ] 5.12 Implement unrelated abstract methods by raising `NotImplementedError`

---

## Task 6: Implement `EmbeddingProcessor` in `text_processing/embedding.py`

**Requirements:** 6.1–6.9

### Subtasks

- [ ] 6.1 Implement `EmbeddingProcessor(TextProcessor)` with `__init__(model_name, max_sentences)` — no loading at construction
- [ ] 6.2 Implement `load_embedding_model(model_name)` with lazy `sentence-transformers` import
- [ ] 6.3 Implement `embed_query(query_text, model, query_prefix)` — prepend prefix, encode, L2-normalize
- [ ] 6.4 Implement `l2_normalise(vectors)` with lazy `faiss` import
- [ ] 6.5 Implement `build_faiss_index(embeddings)` with lazy `faiss` import and optional GPU
- [ ] 6.6 Implement `build_sentence_store(pdf_path, sentence_records, model)` — batch encode, truncation warning, FAISS index
- [ ] 6.7 Ensure `ImportError` with install hints when `faiss`/`torch`/`sentence-transformers` missing
- [ ] 6.8 Ensure `RuntimeWarning` emitted when `len(sentence_records) > max_sentences`
- [ ] 6.9 Implement unrelated abstract methods by raising `NotImplementedError`

---

## Task 7: Implement `SemanticMatcher` in `text_processing/matchers.py`

**Requirements:** 7.1–7.11

### Subtasks

- [ ] 7.1 Implement `SemanticMatcher(TextProcessor)` with `search(query, sentence_store, embed_fn, threshold, page_texts) -> dict | None`
- [ ] 7.2 Implement guard: return `None` when `sentence_store["faiss_index"] is None`
- [ ] 7.3 Implement guard: return `None` when `sentence_store["sentences"]` empty or missing
- [ ] 7.4 Implement FAISS top-1 inner-product search using caller-provided `embed_fn`
- [ ] 7.5 Implement guard: return `None` when top score < `threshold`
- [ ] 7.6 Extract `found_sentence`, `page_index`, `block_bbox`, `span_bboxes` from store
- [ ] 7.7 Extract 64-char prefix/suffix from `page_texts` when available
- [ ] 7.8 Verify returned dict schema: `found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`, `score`
- [ ] 7.9 Ensure `faiss` lazily imported inside `search` body only
- [ ] 7.10 Implement unrelated abstract methods by raising `NotImplementedError`

---

## Task 8: Wire `text_processing/__init__.py` exports

**Requirements:** 1.2, 1.4

### Subtasks

- [ ] 8.1 Import and export `TextProcessor`, `SentenceSegment` from `base`
- [ ] 8.2 Import and export `LexicalMatcher`, `SemanticMatcher` from `matchers`
- [ ] 8.3 Import and export `EmbeddingProcessor` from `embedding`
- [ ] 8.4 Verify `import text_processing` succeeds without heavy deps installed
- [ ] 8.5 Verify `import text_processing.embedding` succeeds without heavy deps

---

## Task 9: Update `sentence_processor.py` — migrate `normalise_text` to `LineHealingNormalizer`

**Requirements:** 9.1–9.6

### Subtasks

- [ ] 9.1 Remove `normalise_text` function definition from `pdf_extractor/processing/sentence_processor.py`
- [ ] 9.2 Add `from text_processing.normalizers import LineHealingNormalizer` import
- [ ] 9.3 Create module-level instance: `_line_healer = LineHealingNormalizer()`
- [ ] 9.4 Replace `normalise_text(text_block)` call in `process_sentences` with `_line_healer.normalize(text_block)`
- [ ] 9.5 Verify `is_noise`, `process_sentences`, `build_full_text` remain in file unchanged
- [ ] 9.6 Run existing `tests/utils/test_sentence_processor.py` to confirm no regression

---

## Task 10: Update callers of `exact_match_search` and `semantic_search`

**Requirements:** 8.1, 5.1, 7.1

### Subtasks

- [ ] 10.1 Update `quality_control/quality_control.py` — replace `from pdf_extractor.utils.text_utils import exact_match_search, semantic_search` with `from text_processing.matchers import LexicalMatcher, SemanticMatcher`
- [ ] 10.2 Replace `exact_match_search(...)` call sites with `LexicalMatcher().search(...)`
- [ ] 10.3 Replace `semantic_search(...)` call sites with `SemanticMatcher().search(...)`
- [ ] 10.4 Verify all call sites pass correct argument order and handle `dict | None` return

---

## Task 11: Update callers of `embedding_utils.*`

**Requirements:** 8.3, 6.1

### Subtasks

- [ ] 11.1 Find all imports of `pdf_extractor.utils.embedding_utils` across the codebase
- [ ] 11.2 Replace with imports from `text_processing.embedding.EmbeddingProcessor`
- [ ] 11.3 Update call sites to use `EmbeddingProcessor()` method calls instead of bare functions
- [ ] 11.4 Verify `build_sentence_store` callers pass correct arguments

---

## Task 12: Update `_load_text_processor` in `quality_control/quality_control.py`

**Requirements:** 3.7, 1.5

### Subtasks

- [ ] 12.1 Update default class path from `"utils.text_processor.TextProcessor"` to `"text_processing.base.ScispaCySentenceSegment"` (or appropriate concrete class)
- [ ] 12.2 Verify `importlib.import_module` correctly resolves the new path
- [ ] 12.3 Verify fallback behavior when class loading fails (returns `None`)

---

## Task 13: Delete legacy text utility files

**Requirements:** 8.1–8.8

### Subtasks

- [ ] 13.1 Delete `utils/text_processor.py`
- [ ] 13.2 Delete `pdf_extractor/utils/text_utils.py`
- [ ] 13.3 Delete `pdf_extractor/utils/embedding_utils.py`
- [ ] 13.4 Verify no shim or re-export remains at deleted paths
- [ ] 13.5 Verify `import utils.text_processor` raises `ModuleNotFoundError`
- [ ] 13.6 Verify `import pdf_extractor.utils.text_utils` raises `ModuleNotFoundError`
- [ ] 13.7 Verify `import pdf_extractor.utils.embedding_utils` raises `ModuleNotFoundError`
- [ ] 13.8 Update `pdf_extractor/utils/__init__.py` if it re-exports deleted names

---

## Task 14: Migrate and create tests

**Requirements:** 12.1–12.12

### Subtasks

- [ ] 14.1 Create `tests/text_processing/__init__.py`
- [ ] 14.2 Create `tests/text_processing/test_base_abc.py` — ABC enforcement tests (Property 1), lazy model loading tests (Property 2)
- [ ] 14.3 Create `tests/text_processing/test_normalizers.py` — example-based tests for all 5 normalizer subclasses
- [ ] 14.4 Create `tests/text_processing/test_normalizers_properties.py` — PBT idempotence via Hypothesis (Property 3)
- [ ] 14.5 Create `tests/text_processing/test_tokenizers.py` — `SimpleWordTokenizer` tests
- [ ] 14.6 Create `tests/text_processing/test_matchers.py` — `LexicalMatcher` and `SemanticMatcher` example-based tests
- [ ] 14.7 Create `tests/text_processing/test_matchers_properties.py` — PBT for Properties 4, 5, 6, 9, 10
- [ ] 14.8 Create `tests/text_processing/test_embedding.py` — `EmbeddingProcessor` tests with mocked deps (mark slow)
- [ ] 14.9 Create `tests/text_processing/test_embedding_properties.py` — PBT for Properties 7, 8 (mark slow)
- [ ] 14.10 Create `tests/text_processing/test_import_isolation.py` — verify import without heavy deps
- [ ] 14.11 Create `tests/text_processing/test_deleted_paths.py` — verify `ModuleNotFoundError` for legacy paths (Property 11)
- [ ] 14.12 Migrate `tests/utils/test_text_processor.py` content to `tests/text_processing/test_base_abc.py`
- [ ] 14.13 Migrate `tests/pdf_extractor/test_text_utils.py` content to `tests/text_processing/test_matchers.py`
- [ ] 14.14 Migrate `tests/pdf_extractor/test_embedding_utils.py` content to `tests/text_processing/test_embedding.py`
- [ ] 14.15 Update `tests/utils/test_sentence_processor.py` — change `normalise_text` imports to `LineHealingNormalizer`
- [ ] 14.16 Delete migrated test files from old locations

---

## Task 15: Add dependency-direction and steering-drift tests

**Requirements:** 1.5, 1.7, 11.6, 12.12

### Subtasks

- [ ] 15.1 Add `("text_processing", "quality_control")` forbidden pair to `tests/test_dependency_directions.py`
- [ ] 15.2 Create `tests/steering/test_text_processing_separation.py` — AST-walker verifying no `text_processing/` file imports from `quality_control/`
- [ ] 15.3 Run full test suite to confirm no regressions

---

## Task 16: Update documentation and steering files

**Requirements:** 13.1–13.4

### Subtasks

- [ ] 16.1 Update `pdf_extractor/utils/README.md` — state deletions and point to `text_processing/`
- [ ] 16.2 Update `.kiro/steering/product.md` — add `text_processing/` to architecture overview and module responsibilities table; remove legacy paths as canonical
- [ ] 16.3 Update `.kiro/steering/testing.md` — add `tests/text_processing/` to test layout table
- [ ] 16.4 Add text-processing migration entry to `CHANGELOG.md`

---

## Task 17: Register config key (if applicable)

**Requirements:** 1.5 (conditional)

### Subtasks

- [ ] 17.1 If a `text_processing:` top-level YAML key is introduced in `configs/config.yaml`, register it in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `utils/config_utils.py`
- [ ] 17.2 Verify `load_local_config` does not raise `ValueError` for the new key

---

## Task 18: Final verification

**Requirements:** All (acceptance summary)

### Subtasks

- [ ] 18.1 Run `python -m pytest -q` — all fast tests pass
- [ ] 18.2 Run `python -m pytest -q -m slow` — all slow tests pass
- [ ] 18.3 Verify `import text_processing` works without heavy deps
- [ ] 18.4 Verify imports from all three deleted paths raise `ModuleNotFoundError`
- [ ] 18.5 Verify `text_processing/` contains no import from `quality_control/`
- [ ] 18.6 Verify symbol audit table completeness — every public name accounted for
- [ ] 18.7 Verify no QC result schemas, config keys, or output hierarchy were modified
