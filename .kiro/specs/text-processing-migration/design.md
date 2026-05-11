# Text Processing Migration — Design

## Overview

This design covers Phase 2 of the EviTrace refactor: extracting all domain-agnostic text-processing primitives from their current scattered locations into a single canonical `text_processing/` root package. The migration consolidates four legacy modules — `utils/text_processor.py`, `pdf_extractor/utils/text_utils.py`, `pdf_extractor/utils/embedding_utils.py`, and the `normalise_text` function from `pdf_extractor/processing/sentence_processor.py` — into a typed, independently testable package with no dependency on `quality_control/`.

### Goals

- One canonical home for every domain-agnostic text primitive.
- `TextProcessor` becomes a pure ABC; all concrete behavior lives in typed subclasses.
- Lexical and semantic search return raw candidate dicts, never QC result objects.
- Heavy optional dependencies (`faiss`, `torch`, `sentence-transformers`, `spacy`, `scispacy`, `stanza`, `wtpsplit`) remain lazy — normal parser imports never trigger them.
- Legacy paths are deleted with no shims or re-export aliases.
- PDF-block-specific processing stays in `pdf_extractor/processing/sentence_processor.py`.

### Non-Goals (Phase 2 Boundary)

This phase does not implement QC fallback selection, OCR fallback decision logic, LLM-generated QC criteria, `route_row()` / `verify_row()` migration, backwards-compatibility shims, or any change to QC result schemas, QC config keys, or QC output hierarchy.

---

## Architecture

### Package Dependency Graph

```
text_processing/          ← new canonical package (no QC imports)
  base.py                 ← TextProcessor ABC, SentenceSegment ABC
  normalizers.py          ← WhitespaceNormalizer, FullNormalizer,
  |                          LineHealingNormalizer, UnicodeNormalizer, OcrCleaner
  tokenizers.py           ← SimpleWordTokenizer
  matchers.py             ← LexicalMatcher, SemanticMatcher
  embedding.py            ← EmbeddingProcessor
  __init__.py             ← re-exports TextProcessor, LexicalMatcher,
                             SemanticMatcher, EmbeddingProcessor

pdf_extractor/
  processing/
    sentence_processor.py ← imports LineHealingNormalizer from text_processing;
                             keeps is_noise, process_sentences, build_full_text
  utils/
    text_utils.py         ← DELETED
    embedding_utils.py    ← DELETED

utils/
  text_processor.py       ← DELETED

quality_control/          ← unchanged; may inject LexicalMatcher/SemanticMatcher
                             via adapter layer after both phases complete
```

### Dependency Direction Rule

`text_processing` must not import from `quality_control`, `pdf_extractor`, `pipeline`, or `agents`. This is enforced by the existing `tests/test_dependency_directions.py` AST walker (extended to cover `text_processing`) and by a new steering-drift test.

### Lazy Import Strategy

All heavy optional dependencies are imported inside method bodies, never at module level. The pattern used throughout the codebase is:

```python
def some_method(self, ...):
    try:
        import faiss
    except ImportError as exc:
        raise ImportError("faiss is required. Install with: pip install faiss-cpu") from exc
    ...
```

This ensures `import text_processing` and `import text_processing.embedding` succeed in any environment.

---

## Components and Interfaces

### `text_processing/base.py` — Abstract Base Classes

#### `TextProcessor` (pure ABC)

```python
import abc

class TextProcessor(abc.ABC):
    @abc.abstractmethod
    def normalize(self, text: str) -> str: ...

    @abc.abstractmethod
    def tokenize_words(self, text: str) -> list[str]: ...

    @abc.abstractmethod
    def tokenize_sentences(self, text: str) -> list[str]: ...

    @abc.abstractmethod
    def clean_ocr(self, text: str) -> str: ...

    @abc.abstractmethod
    def compare(self, a: str, b: str) -> float: ...

    @abc.abstractmethod
    def extract_keywords(self, text: str) -> list[str]: ...
```

Direct instantiation raises the standard `abc.ABC` `TypeError`. Concrete subclasses that do not own a particular responsibility implement unrelated abstract methods by raising `NotImplementedError` (not silently).

#### `SentenceSegment` (ABC, inherits `TextProcessor`)

```python
class SentenceSegment(TextProcessor, abc.ABC):
    def __init__(self, config: dict | None = None) -> None:
        self._model = None  # lazy-loaded on first tokenize_sentences() call

    @abc.abstractmethod
    def tokenize_sentences(self, text: str) -> list[str]: ...
```

`_model` is `None` until the first `tokenize_sentences()` call. The five concrete backends (`ScispaCySentenceSegment`, `WtpSplitSentenceSegment`, `NLTKPunktSentenceSegment`, `SpacySentencizerSegment`, `StanzaSentenceSegment`) are preserved with identical names and moved here from `utils/text_processor.py`.

**Loader pattern** (Requirement 3.8): When `text_processor.class` in `config.yaml` is a dotted path, the loader splits on the final `.`, calls `importlib.import_module(module_path)`, retrieves the class, and instantiates it with no positional arguments.

### `text_processing/normalizers.py` — Normalizer Subclasses

Each normalizer is a concrete `TextProcessor` subclass. Methods outside the normalizer's responsibility raise `NotImplementedError`.

| Class | Source | Key behavior |
|---|---|---|
| `WhitespaceNormalizer` | `normalise_ws` in `text_utils.py` | Collapse whitespace, lowercase; idempotent |
| `FullNormalizer` | `normalise_full` in `text_utils.py` | Whitespace + strip non-word chars; idempotent |
| `LineHealingNormalizer` | `normalise_text` in `sentence_processor.py` | Heal mid-sentence line breaks, collapse newlines/spaces; idempotent |
| `UnicodeNormalizer` | `TextProcessor.normalize` in `utils/text_processor.py` | Unicode NFC/NFKC + whitespace collapse; idempotent |
| `OcrCleaner` | `TextProcessor.clean_ocr` in `utils/text_processor.py` | Strip C0 controls and U+FFFD |

All four normalizers (`WhitespaceNormalizer`, `FullNormalizer`, `LineHealingNormalizer`, `UnicodeNormalizer`) are idempotent: `n(n(s)) == n(s)` for all strings `s`. Empty string input returns empty string.

### `text_processing/tokenizers.py` — Tokenizer Subclasses

| Class | Source | Key behavior |
|---|---|---|
| `SimpleWordTokenizer` | `tokenize_words` (simple backend) in `utils/text_processor.py` | Normalize then split on whitespace |

### `text_processing/matchers.py` — Search Subclasses

#### `LexicalMatcher`

```python
class LexicalMatcher(TextProcessor):
    def search(
        self,
        needle: str,
        full_text: str,
        page_texts: dict[int, str],
        blocks: list[dict],
    ) -> dict | None: ...
```

Preserves the two-pass normalization logic from `exact_match_search`:

- **Pre-check**: if `WhitespaceNormalizer().normalize(needle)` has length < 10, return `None`.
- **Pass 1**: `WhitespaceNormalizer` — if normalized needle is a substring of normalized `full_text`, return result immediately (score `1.0`). Pass 2 is never invoked.
- **Pass 2**: `FullNormalizer` — only attempted when Pass 1 fails. Score `0.9`.
- **Both fail**: return `None`.
- **Cross-page attribution**: when the needle is not found in any single page, attribute to the page with the longest common substring overlap and emit a `DEBUG`-level log.
- **Empty guards**: `full_text == ""` or `page_texts == {}` → return `None`.

Returned dict schema (raw candidate, not a QC object):

```python
{
    "found_sentence": str,
    "page_index":     int,
    "prefix":         str,   # up to 64 chars
    "suffix":         str,   # up to 64 chars
    "block_bbox":     tuple | None,
    "span_bboxes":    list[dict] | None,
    "score":          float,  # 1.0 or 0.9
}
```

#### `SemanticMatcher`

```python
class SemanticMatcher(TextProcessor):
    def search(
        self,
        query: str,
        sentence_store: dict,
        embed_fn: callable,
        threshold: float,
        page_texts: dict | None,
    ) -> dict | None: ...
```

- Returns `None` when `sentence_store["faiss_index"] is None`.
- Returns `None` when `sentence_store["sentences"]` is empty or missing.
- Returns `None` when top candidate score < `threshold`.
- Does not load an embedding model itself — delegates to caller-provided `embed_fn`.
- `faiss`, `torch`, `sentence-transformers` are not imported at module level; errors deferred to first call.

Returned dict schema (raw candidate):

```python
{
    "found_sentence": str,
    "page_index":     int,
    "prefix":         str,
    "suffix":         str,
    "block_bbox":     tuple | None,
    "span_bboxes":    list[dict] | None,
    "score":          float,  # cosine similarity in [0.0, 1.0]
}
```

### `text_processing/embedding.py` — EmbeddingProcessor

```python
class EmbeddingProcessor(TextProcessor):
    def __init__(
        self,
        model_name: str = "BAAI/bge-base-en-v1.5",
        max_sentences: int = 10_000,
    ) -> None: ...

    def load_embedding_model(self, model_name: str | None = None): ...
    def embed_query(self, query_text: str, model, query_prefix: str = ...) -> "np.ndarray": ...
    def l2_normalise(self, vectors: "np.ndarray") -> "np.ndarray": ...
    def build_faiss_index(self, embeddings: "np.ndarray") -> object: ...
    def build_sentence_store(self, pdf_path: str, sentence_records: list, model) -> dict: ...
```

- No model loading, FAISS index construction, Torch import, or GPU access at import time or construction time.
- `faiss`, `torch`, `sentence-transformers` are imported lazily inside method bodies.
- When `len(sentence_records) > max_sentences`, emits `RuntimeWarning` and truncates to first `max_sentences` records.
- Default `max_sentences = 10_000`; default `model_name = "BAAI/bge-base-en-v1.5"`.

### `text_processing/__init__.py` — Public API

```python
from text_processing.base import TextProcessor, SentenceSegment
from text_processing.matchers import LexicalMatcher, SemanticMatcher
from text_processing.embedding import EmbeddingProcessor

__all__ = ["TextProcessor", "SentenceSegment", "LexicalMatcher", "SemanticMatcher", "EmbeddingProcessor"]
```

Import succeeds without any heavy NLP, embedding, FAISS, Torch, GPU, or model-download dependencies.

---

## Data Models

### Candidate Dict (raw output of matchers)

Both `LexicalMatcher.search` and `SemanticMatcher.search` return a plain `dict` (or `None`). This is intentionally not a dataclass — it is a raw candidate that the pipeline or adapter layer converts into QC result objects after both phases are complete.

```
{
    "found_sentence": str,
    "page_index":     int,
    "prefix":         str,
    "suffix":         str,
    "block_bbox":     tuple[float, float, float, float] | None,
    "span_bboxes":    list[{"text": str, "bbox": tuple | None}] | None,
    "score":          float,
}
```

### SentenceStore Dict (output of EmbeddingProcessor.build_sentence_store)

```
{
    "pdf_path":     str,
    "sentences":    list[str],
    "pages":        list[int],
    "block_bboxes": list[tuple | None],
    "span_bboxes":  list[list | None],
    "embeddings":   np.ndarray,   # shape (N, D), float32, L2-normalised
    "faiss_index":  faiss.Index | None,
}
```

When `sentence_records` is empty, `embeddings` has shape `(0, 768)` and `faiss_index` is `None`.

### Symbol Audit Table

Every public module-level name (not prefixed with `_`) from the four legacy modules, with migration action:

| Symbol | Current location | Destination | Renamed to | Action | Test files with direct imports |
|---|---|---|---|---|---|
| `TextProcessor` | `utils/text_processor.py` | `text_processing/base.py` | `TextProcessor` | move | `tests/utils/test_text_processor.py` |
| `SentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | `SentenceSegment` | move | `tests/utils/test_text_processor.py` |
| `ScispaCySentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | `ScispaCySentenceSegment` | move | `tests/utils/test_text_processor.py` |
| `WtpSplitSentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | `WtpSplitSentenceSegment` | move | `tests/utils/test_text_processor.py` |
| `NLTKPunktSentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | `NLTKPunktSentenceSegment` | move | `tests/utils/test_text_processor.py` |
| `SpacySentencizerSegment` | `utils/text_processor.py` | `text_processing/base.py` | `SpacySentencizerSegment` | move | `tests/utils/test_text_processor.py` |
| `StanzaSentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | `StanzaSentenceSegment` | move | `tests/utils/test_text_processor.py` |
| `normalise_ws` | `pdf_extractor/utils/text_utils.py` | `text_processing/normalizers.py` | `WhitespaceNormalizer` | rename | `tests/utils/test_sentence_processor.py` |
| `normalise_full` | `pdf_extractor/utils/text_utils.py` | `text_processing/normalizers.py` | `FullNormalizer` | rename | `tests/utils/test_sentence_processor.py` |
| `exact_match_search` | `pdf_extractor/utils/text_utils.py` | `text_processing/matchers.py` | `LexicalMatcher.search` | rename | `tests/pdf_extractor/` |
| `semantic_search` | `pdf_extractor/utils/text_utils.py` | `text_processing/matchers.py` | `SemanticMatcher.search` | rename | `tests/pdf_extractor/` |
| `load_embedding_model` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.load_embedding_model` | rename | `tests/pdf_extractor/` |
| `embed_query` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.embed_query` | rename | `tests/pdf_extractor/` |
| `l2_normalise` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.l2_normalise` | rename | `tests/pdf_extractor/` |
| `build_faiss_index` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.build_faiss_index` | rename | `tests/pdf_extractor/` |
| `build_sentence_store` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.build_sentence_store` | rename | `tests/pdf_extractor/` |
| `normalise_text` | `pdf_extractor/processing/sentence_processor.py` | `text_processing/normalizers.py` | `LineHealingNormalizer` | rename | `tests/utils/test_sentence_processor.py` |
| `is_noise` | `pdf_extractor/processing/sentence_processor.py` | `pdf_extractor/processing/sentence_processor.py` | `is_noise` | keep | `tests/utils/test_sentence_processor.py` |
| `process_sentences` | `pdf_extractor/processing/sentence_processor.py` | `pdf_extractor/processing/sentence_processor.py` | `process_sentences` | keep | `tests/utils/test_sentence_processor.py` |
| `build_full_text` | `pdf_extractor/processing/sentence_processor.py` | `pdf_extractor/processing/sentence_processor.py` | `build_full_text` | keep | `tests/utils/test_sentence_processor.py` |

**Verification**: Every public module-level name appears exactly once. No QC-only private helpers are moved. `normalise_text` is marked as renamed to `LineHealingNormalizer`. `is_noise`, `process_sentences`, and `build_full_text` are marked as kept.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

This feature involves pure functions (normalizers, matchers, tokenizers) with clear input/output behavior and universal properties that hold across a wide input space. Property-based testing with [Hypothesis](https://hypothesis.readthedocs.io/) is appropriate. The library is already used in this codebase (`tests/utils/test_text_processor.py`, `.hypothesis/` directory present).

### Property 1: ABC Enforcement

*For any* class that inherits from `TextProcessor` but does not implement all six declared abstract methods (`normalize`, `tokenize_words`, `tokenize_sentences`, `clean_ocr`, `compare`, `extract_keywords`), attempting to instantiate that class SHALL raise `TypeError`. This includes direct instantiation of `TextProcessor` itself.

**Validates: Requirements 2.3, 2.4, 2.5, 2.6**

### Property 2: Lazy Model Loading

*For any* concrete `SentenceSegment` backend (with heavy NLP dependencies mocked), the `_model` attribute SHALL be `None` immediately after construction and SHALL be non-`None` after the first `tokenize_sentences()` call.

**Validates: Requirements 3.5, 3.6, 3.7**

### Property 3: Normalizer Idempotence

*For any* string `s` and any of the four normalizers (`WhitespaceNormalizer`, `FullNormalizer`, `LineHealingNormalizer`, `UnicodeNormalizer`), applying the normalizer twice SHALL produce the same result as applying it once: `n(n(s)) == n(s)`.

**Validates: Requirements 4.7**

### Property 4: LexicalMatcher Null Returns

*For any* needle whose whitespace-normalized form has length < 10, OR *for any* needle/text pair where both Pass 1 (`WhitespaceNormalizer`) and Pass 2 (`FullNormalizer`) fail to find the needle as a substring, `LexicalMatcher.search` SHALL return `None`.

**Validates: Requirements 5.7, 5.9**

### Property 5: LexicalMatcher Pass 1 Short-Circuit

*For any* needle/text pair where Pass 1 (`WhitespaceNormalizer`) succeeds in finding the normalized needle as a substring of normalized `full_text`, `LexicalMatcher.search` SHALL return a result immediately and SHALL NOT invoke `FullNormalizer.normalize`.

**Validates: Requirements 5.8**

### Property 6: LexicalMatcher Result Schema and Score Values

*For any* successful `LexicalMatcher.search` call, the returned dict SHALL contain all required keys (`found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`, `score`), the score SHALL be `1.0` when Pass 1 matched, and `0.9` when Pass 2 matched.

**Validates: Requirements 5.12, 5.13**

### Property 7: Optional Dependency Deferral

*For any* `EmbeddingProcessor` or `SemanticMatcher` method that requires `faiss`, `torch`, or `sentence-transformers`, when those packages are absent (patched to `None`), calling that method SHALL raise `ImportError` with a human-readable install hint. Importing `text_processing.embedding` or `text_processing.matchers` SHALL NOT raise `ImportError` regardless of whether the optional packages are installed.

**Validates: Requirements 6.3, 6.4, 7.3, 7.4**

### Property 8: Truncation Warning

*For any* `sentence_records` list whose length exceeds `max_sentences`, `EmbeddingProcessor.build_sentence_store` SHALL emit a `RuntimeWarning` and the returned store's `sentences` list SHALL have length equal to `max_sentences`.

**Validates: Requirements 6.5**

### Property 9: SemanticMatcher Null Returns

*For any* call to `SemanticMatcher.search` where `sentence_store["faiss_index"] is None`, OR where `sentence_store["sentences"]` is empty or missing, OR where the top candidate cosine similarity score is below `threshold`, the method SHALL return `None`.

**Validates: Requirements 7.5, 7.6, 7.7**

### Property 10: SemanticMatcher Result Schema and Score Range

*For any* successful `SemanticMatcher.search` call, the returned dict SHALL contain all required keys (`found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`, `score`), and the `score` SHALL be in the range `[0.0, 1.0]`.

**Validates: Requirements 7.10, 7.11**

### Property 11: Deleted Path Imports Fail

*For any* of the three deleted legacy module paths (`utils.text_processor`, `pdf_extractor.utils.text_utils`, `pdf_extractor.utils.embedding_utils`), attempting to import from that path SHALL raise `ModuleNotFoundError` or `ImportError` immediately.

**Validates: Requirements 8.10**

---

## Error Handling

### Import Errors for Optional Dependencies

All optional-dependency `ImportError`s include a human-readable `pip install` hint. The pattern is consistent with the existing codebase:

```python
try:
    import faiss
except ImportError as exc:
    raise ImportError(
        "faiss is required for semantic search. "
        "Install it with: pip install faiss-cpu"
    ) from exc
```

### ABC Instantiation Errors

`TextProcessor` and `SentenceSegment` are pure ABCs. Direct instantiation raises the standard Python `TypeError: Can't instantiate abstract class TextProcessor with abstract methods ...`. No custom error handling is needed.

### Short Needle Guard

`LexicalMatcher.search` returns `None` (not raises) when the whitespace-normalized needle is shorter than 10 characters. This matches the existing `exact_match_search` behavior.

### Empty Input Guards

`LexicalMatcher.search` returns `None` when `full_text` is empty or `page_texts` is empty. `SemanticMatcher.search` returns `None` when the store is unavailable or empty. These are silent `None` returns, not exceptions, consistent with the existing search functions.

### Cross-Page Attribution

When a matched sentence spans a page boundary and is not found in any single page, `LexicalMatcher.search` attributes the result to the page with the longest common substring overlap and emits a `DEBUG`-level log message via `logging.getLogger("text_processing")`. This matches the existing `logger.info` call in `exact_match_search` (upgraded to `DEBUG` per Requirement 5.10).

### Truncation Warning

`EmbeddingProcessor.build_sentence_store` uses `warnings.warn(..., RuntimeWarning, stacklevel=2)` when `len(sentence_records) > max_sentences`. The warning message includes the `pdf_path` and the actual record count.

### Loader Errors

When `text_processor.class` in config is a dotted path that cannot be resolved, `importlib.import_module` raises `ModuleNotFoundError` and `getattr` raises `AttributeError`. These propagate naturally — no wrapping needed.

---

## Testing Strategy

### Test Layout

```
tests/
└── text_processing/
    ├── __init__.py
    ├── test_base_abc.py              # ABC enforcement, SentenceSegment hierarchy
    ├── test_normalizers.py           # WhitespaceNormalizer, FullNormalizer,
    │                                 # LineHealingNormalizer, UnicodeNormalizer, OcrCleaner
    ├── test_normalizers_properties.py # PBT: idempotence (Property 3)
    ├── test_tokenizers.py            # SimpleWordTokenizer
    ├── test_matchers.py              # LexicalMatcher, SemanticMatcher (example-based)
    ├── test_matchers_properties.py   # PBT: Properties 4, 5, 6, 9, 10
    ├── test_embedding.py             # EmbeddingProcessor (mocked deps)
    ├── test_embedding_properties.py  # PBT: Properties 7, 8
    ├── test_import_isolation.py      # smoke: import without heavy deps
    └── test_deleted_paths.py         # Property 11: deleted paths raise ImportError
```

Steering-drift tests (AST-based) live in `tests/steering/`:

```
tests/steering/
└── test_text_processing_separation.py  # text_processing/ does not import quality_control/
```

### Unit Tests

Unit tests cover:
- ABC instantiation guards (Properties 1, 2)
- Each normalizer subclass in isolation with representative inputs
- `LexicalMatcher` with in-memory `page_texts` and `blocks` stubs
- `SemanticMatcher` with a mock FAISS index
- `EmbeddingProcessor` with mocked `faiss`, `torch`, `sentence-transformers`
- Cross-page attribution scenario (specific example)
- Empty input guards (edge cases 5.14, 5.15, 4.8, 4.9)

### Property-Based Tests

Property-based tests use [Hypothesis](https://hypothesis.readthedocs.io/) with `@given` and `@settings(max_examples=100)`, consistent with the existing repo convention in `tests/utils/test_text_processor.py`.

Each property test is tagged with a comment referencing the design property:

```python
# Feature: text-processing-migration, Property 3: Normalizer idempotence
@given(st.text())
@settings(max_examples=100)
def test_whitespace_normalizer_idempotent(s: str):
    n = WhitespaceNormalizer()
    assert n.normalize(n.normalize(s)) == n.normalize(s)
```

**Property 1 (ABC Enforcement)**: Generate subclasses that implement random subsets of the 6 abstract methods; verify `TypeError` on instantiation when any method is missing.

**Property 2 (Lazy Model Loading)**: For each backend with mocked deps, verify `_model is None` after construction and non-`None` after first `tokenize_sentences()` call.

**Property 3 (Normalizer Idempotence)**: `@given(st.text())` for each of the four normalizers; verify `n(n(s)) == n(s)`.

**Property 4 (LexicalMatcher Null Returns)**: Generate short strings (normalized length < 10) and needle/text pairs with no substring match; verify `None` is returned.

**Property 5 (Pass 1 Short-Circuit)**: Generate needle/text pairs where Pass 1 succeeds; mock `FullNormalizer.normalize`; verify it is never called.

**Property 6 (LexicalMatcher Result Schema)**: Generate valid needle/text/page_texts/blocks inputs; verify all required keys present and score values correct.

**Property 7 (Optional Dependency Deferral)**: Patch `faiss`, `torch`, `sentence-transformers` to `None`; verify import succeeds and method calls raise `ImportError`.

**Property 8 (Truncation Warning)**: `@given(st.integers(min_value=1, max_value=50000))` for list length; verify `RuntimeWarning` and truncation when length > `max_sentences`.

**Property 9 (SemanticMatcher Null Returns)**: Generate stores with `faiss_index=None`, empty sentences, and threshold/score pairs where score < threshold; verify `None`.

**Property 10 (SemanticMatcher Result Schema)**: Generate valid inputs with mock FAISS index; verify required keys and `0.0 <= score <= 1.0`.

**Property 11 (Deleted Path Imports Fail)**: Attempt to import each deleted path; verify `ModuleNotFoundError`.

### Slow Test Marking

Tests that exercise `EmbeddingProcessor` or `SemanticMatcher` heavy paths (even with mocked deps) carry `pytestmark = pytest.mark.slow` at module level, consistent with the repo convention.

### Mocking Convention

Heavy optional dependencies are mocked using `patch.dict("sys.modules", ...)` per repo convention:

```python
with patch.dict(sys.modules, {"faiss": None, "torch": None, "sentence_transformers": None}):
    # remove cached modules first
    for mod in list(sys.modules):
        if "text_processing.embedding" in mod:
            del sys.modules[mod]
    import text_processing.embedding
```

### Steering-Drift Tests

`tests/steering/test_text_processing_separation.py` uses the same AST-walker pattern as `tests/test_dependency_directions.py` to verify that no `.py` file under `text_processing/` imports from `quality_control/`. This test must pass at all times.

`tests/test_dependency_directions.py` is extended with a new forbidden pair: `("text_processing", "quality_control")`.

### Existing Test Updates

All test files under `tests/` that currently import from deleted paths (`utils.text_processor`, `pdf_extractor.utils.text_utils`, `pdf_extractor.utils.embedding_utils`) are updated to import from canonical `text_processing.*` paths. The existing `tests/utils/test_text_processor.py` is migrated to `tests/text_processing/test_base_abc.py`.
