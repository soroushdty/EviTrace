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
  base.py                 ← TextProcessor ABC, SentenceSegment ABC,
  |                          5 concrete sentence backends
  normalizers.py          ← WhitespaceNormalizer, FullNormalizer,
  |                          LineHealingNormalizer, UnicodeNormalizer, OcrCleaner
  tokenizers.py           ← SimpleWordTokenizer
  matchers.py             ← LexicalMatcher, SemanticMatcher
  embedding.py            ← EmbeddingProcessor
  __init__.py             ← re-exports TextProcessor, SentenceSegment,
                             LexicalMatcher, SemanticMatcher, EmbeddingProcessor

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

`text_processing` must not import from `quality_control`, `pdf_extractor`, `pipeline`, or `agents`. This is enforced by `tests/test_dependency_directions.py` (extended with new pair) and by a dedicated steering-drift test in `tests/steering/test_text_processing_separation.py`.

### Lazy Import Strategy

All heavy optional dependencies are imported inside method bodies, never at module level:

```python
def some_method(self, ...):
    try:
        import faiss
    except ImportError as exc:
        raise ImportError("faiss is required. Install with: pip install faiss-cpu") from exc
    ...
```

This ensures `import text_processing` succeeds in any environment.

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

Direct instantiation raises the standard ABC `TypeError`. Concrete subclasses that do not own a particular responsibility implement unrelated abstract methods by raising `NotImplementedError`.

#### `SentenceSegment` (ABC, inherits `TextProcessor`)

```python
class SentenceSegment(TextProcessor, abc.ABC):
    def __init__(self, config: dict | None = None) -> None:
        self._model = None  # lazy-loaded on first tokenize_sentences() call

    @abc.abstractmethod
    def tokenize_sentences(self, text: str) -> list[str]: ...
```

`_model` is `None` until the first `tokenize_sentences()` call. The five concrete backends are preserved with identical names and moved here from `utils/text_processor.py`.

**Loader pattern** (Req 3.7): When `text_processor.class` in `config.yaml` is a dotted path, the loader splits on the final `.`, calls `importlib.import_module(module_path)`, retrieves the class, and instantiates with no positional arguments.



### `text_processing/normalizers.py` — Normalizer Subclasses

Each normalizer is a concrete `TextProcessor` subclass. Methods outside the normalizer's typed responsibility raise `NotImplementedError`.

| Class | Source | Key behavior |
|---|---|---|
| `WhitespaceNormalizer` | `normalise_ws` in `text_utils.py` | Collapse whitespace, lowercase; idempotent |
| `FullNormalizer` | `normalise_full` in `text_utils.py` | Whitespace + strip non-word chars; idempotent |
| `LineHealingNormalizer` | `normalise_text` in `sentence_processor.py` | Heal mid-sentence line breaks, collapse newlines/spaces; idempotent |
| `UnicodeNormalizer` | `TextProcessor.normalize` in `utils/text_processor.py` | Unicode NFC/NFKC + whitespace collapse; idempotent |
| `OcrCleaner` | `TextProcessor.clean_ocr` in `utils/text_processor.py` | Strip C0 controls (U+0000–U+0008, U+000B, U+000C, U+000E–U+001F) and U+FFFD; preserves tab/LF/CR |

**Idempotency invariant**: For each of `WhitespaceNormalizer`, `FullNormalizer`, `LineHealingNormalizer`, `UnicodeNormalizer`: `n(n(s)) == n(s)` for all strings `s`. Empty input → empty output.

**Implementation details:**

```python
class WhitespaceNormalizer(TextProcessor):
    def normalize(self, text: str) -> str:
        return re.sub(r'\s+', ' ', text.lower()).strip()

class FullNormalizer(TextProcessor):
    def normalize(self, text: str) -> str:
        text = re.sub(r'\s+', ' ', text.lower()).strip()
        text = re.sub(r'[^\w\s]', '', text)
        return re.sub(r'\s+', ' ', text).strip()

class LineHealingNormalizer(TextProcessor):
    def normalize(self, text: str) -> str:
        text = re.sub(r'\n(?![A-Z\-\*•·])', ' ', text)
        text = re.sub(r'\n{2,}', '\n', text)
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()

class UnicodeNormalizer(TextProcessor):
    def __init__(self, form: str = "NFKC") -> None:
        self._form = form
    def normalize(self, text: str) -> str:
        if not text:
            return text
        normalized = unicodedata.normalize(self._form, text)
        return re.sub(r"\s+", " ", normalized).strip()

class OcrCleaner(TextProcessor):
    _C0_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")
    def clean_ocr(self, text: str) -> str:
        if not text:
            return text
        return self._C0_RE.sub("", text)
```

### `text_processing/tokenizers.py` — Tokenizer Subclasses

| Class | Source | Key behavior |
|---|---|---|
| `SimpleWordTokenizer` | `tokenize_words` (simple backend) in `utils/text_processor.py` | Normalize then split on whitespace |

```python
class SimpleWordTokenizer(TextProcessor):
    def __init__(self, normalizer: TextProcessor | None = None) -> None:
        self._normalizer = normalizer or UnicodeNormalizer()

    def tokenize_words(self, text: str) -> list[str]:
        normalized = self._normalizer.normalize(text)
        return normalized.split() if normalized else []
```

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

**Algorithm:**

1. **Pre-check**: if `WhitespaceNormalizer().normalize(needle)` length < 10 → return `None`.
2. **Empty guards**: `full_text == ""` or `page_texts == {}` → return `None`.
3. **Pass 1**: `WhitespaceNormalizer` — if normalized needle is a substring of normalized `full_text`, return result with `score=1.0`. Pass 2 never invoked.
4. **Pass 2**: `FullNormalizer` — only attempted when Pass 1 fails. `score=0.9`.
5. **Both fail**: return `None`.
6. **Page attribution**: find the page whose normalized text contains the needle. If no single page contains it (cross-page span), attribute to the page with the longest common substring overlap via `SequenceMatcher` and emit `DEBUG`-level log.
7. **Span recovery**: use `SequenceMatcher` on the matched page text to recover the original (un-normalized) span.
8. **Context extraction**: 64-character prefix and suffix from original page text.
9. **Block/span bbox**: scan blocks on the matched page for containing text; extract `block_bbox` and `span_bboxes`.

**Returned dict schema:**

```python
{
    "found_sentence": str,
    "page_index":     int,
    "prefix":         str,   # up to 64 chars
    "suffix":         str,   # up to 64 chars
    "block_bbox":     tuple | None,
    "span_bboxes":    list[dict] | None,
    "score":          float,  # 1.0 (Pass 1) or 0.9 (Pass 2)
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
        page_texts: dict | None = None,
    ) -> dict | None: ...
```

**Algorithm:**

1. **Guard**: `sentence_store["faiss_index"] is None` → return `None`.
2. **Guard**: `sentence_store["sentences"]` empty or missing → return `None`.
3. Embed query via caller-provided `embed_fn(query)` → shape `(1, D)`.
4. FAISS top-1 inner-product search → `similarity`, `best_idx`.
5. **Guard**: `similarity < threshold` → return `None`.
6. Extract `found_sentence`, `page_index`, `block_bbox`, `span_bboxes` from store.
7. Extract 64-char prefix/suffix from `page_texts` if available.

**Returned dict schema:**

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

**Dependency note**: `faiss` is imported lazily inside the `search` method body. The module-level import of `text_processing.matchers` never triggers `faiss`, `torch`, or `sentence-transformers`.



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

**Invariants:**
- No model loading, FAISS index construction, Torch import, or GPU access at import time or construction time.
- `faiss`, `torch`, `sentence-transformers` imported lazily inside method bodies.
- When `len(sentence_records) > max_sentences`, emits `RuntimeWarning` and truncates.
- Default `max_sentences = 10_000`; default `model_name = "BAAI/bge-base-en-v1.5"`.
- Query prefix: `"Represent this sentence for searching relevant passages: "` (BGE-specific; configurable via parameter).

### `text_processing/__init__.py` — Public API

```python
from text_processing.base import TextProcessor, SentenceSegment
from text_processing.matchers import LexicalMatcher, SemanticMatcher
from text_processing.embedding import EmbeddingProcessor

__all__ = [
    "TextProcessor",
    "SentenceSegment",
    "LexicalMatcher",
    "SemanticMatcher",
    "EmbeddingProcessor",
]
```

Import succeeds without any heavy NLP, embedding, FAISS, Torch, GPU, or model-download dependencies.

---

## Data Models

### Candidate Dict (raw output of matchers)

Both `LexicalMatcher.search` and `SemanticMatcher.search` return a plain `dict | None`. This is intentionally not a dataclass — it is a raw candidate that the QC pipeline or adapter layer converts into `VerificationResult` objects after both phases are complete.

```python
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

### SentenceStore Dict (output of `EmbeddingProcessor.build_sentence_store`)

```python
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

---

## Symbol Audit Table

Every public module-level name (not `_`-prefixed) from the four legacy modules:

| Symbol | Current location | Destination | Renamed to | Action | Affected test files |
|---|---|---|---|---|---|
| `TextProcessor` | `utils/text_processor.py` | `text_processing/base.py` | — | move | `tests/utils/test_text_processor.py` |
| `SentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | — | move | `tests/utils/test_text_processor.py` |
| `ScispaCySentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | — | move | `tests/utils/test_text_processor.py` |
| `WtpSplitSentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | — | move | `tests/utils/test_text_processor.py` |
| `NLTKPunktSentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | — | move | `tests/utils/test_text_processor.py` |
| `SpacySentencizerSegment` | `utils/text_processor.py` | `text_processing/base.py` | — | move | `tests/utils/test_text_processor.py` |
| `StanzaSentenceSegment` | `utils/text_processor.py` | `text_processing/base.py` | — | move | `tests/utils/test_text_processor.py` |
| `normalise_ws` | `pdf_extractor/utils/text_utils.py` | `text_processing/normalizers.py` | `WhitespaceNormalizer` | rename | `tests/pdf_extractor/test_text_utils.py` |
| `normalise_full` | `pdf_extractor/utils/text_utils.py` | `text_processing/normalizers.py` | `FullNormalizer` | rename | `tests/pdf_extractor/test_text_utils.py` |
| `exact_match_search` | `pdf_extractor/utils/text_utils.py` | `text_processing/matchers.py` | `LexicalMatcher.search` | rename | `tests/pdf_extractor/test_text_utils.py` |
| `semantic_search` | `pdf_extractor/utils/text_utils.py` | `text_processing/matchers.py` | `SemanticMatcher.search` | rename | `tests/pdf_extractor/test_text_utils.py` |
| `load_embedding_model` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.load_embedding_model` | rename | `tests/pdf_extractor/test_embedding_utils.py` |
| `embed_query` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.embed_query` | rename | `tests/pdf_extractor/test_embedding_utils.py` |
| `l2_normalise` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.l2_normalise` | rename | `tests/pdf_extractor/test_embedding_utils.py` |
| `build_faiss_index` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.build_faiss_index` | rename | `tests/pdf_extractor/test_embedding_utils.py` |
| `build_sentence_store` | `pdf_extractor/utils/embedding_utils.py` | `text_processing/embedding.py` | `EmbeddingProcessor.build_sentence_store` | rename | `tests/pdf_extractor/test_embedding_utils.py` |
| `normalise_text` | `pdf_extractor/processing/sentence_processor.py` | `text_processing/normalizers.py` | `LineHealingNormalizer` | rename | `tests/utils/test_sentence_processor.py` |
| `is_noise` | `pdf_extractor/processing/sentence_processor.py` | — | — | keep | `tests/utils/test_sentence_processor.py` |
| `process_sentences` | `pdf_extractor/processing/sentence_processor.py` | — | — | keep | `tests/utils/test_sentence_processor.py` |
| `build_full_text` | `pdf_extractor/processing/sentence_processor.py` | — | — | keep | `tests/utils/test_sentence_processor.py` |

**Verification**: Every public module-level name appears exactly once. No QC-only private helpers are moved.



---

## Correctness Properties

Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees. Property-based testing with [Hypothesis](https://hypothesis.readthedocs.io/) is appropriate for the pure functions in this package.

### Property 1: ABC Enforcement

*For any* class that inherits from `TextProcessor` but does not implement all six abstract methods, attempting to instantiate that class SHALL raise `TypeError`. This includes direct instantiation of `TextProcessor` itself.

**Validates: Requirements 2.3, 2.4, 2.5**

### Property 2: Lazy Model Loading

*For any* concrete `SentenceSegment` backend (with heavy deps mocked), `_model` SHALL be `None` after construction and non-`None` after the first `tokenize_sentences()` call.

**Validates: Requirements 3.4, 3.5**

### Property 3: Normalizer Idempotence

*For any* string `s` and any of the four normalizers (`WhitespaceNormalizer`, `FullNormalizer`, `LineHealingNormalizer`, `UnicodeNormalizer`), applying the normalizer twice SHALL produce the same result as applying it once: `n(n(s)) == n(s)`.

**Validates: Requirement 4.7**

### Property 4: LexicalMatcher Null Returns

*For any* needle whose whitespace-normalized form has length < 10, OR *for any* needle/text pair where both Pass 1 and Pass 2 fail, `LexicalMatcher.search` SHALL return `None`.

**Validates: Requirements 5.7, 5.9, 5.13**

### Property 5: LexicalMatcher Pass 1 Short-Circuit

*For any* needle/text pair where Pass 1 succeeds, `LexicalMatcher.search` SHALL NOT invoke `FullNormalizer.normalize`.

**Validates: Requirement 5.8**

### Property 6: LexicalMatcher Result Schema

*For any* successful `LexicalMatcher.search` call, the returned dict SHALL contain all seven required keys and `score` SHALL be `1.0` (Pass 1) or `0.9` (Pass 2).

**Validates: Requirement 5.11**

### Property 7: Optional Dependency Deferral

*For any* `EmbeddingProcessor` or `SemanticMatcher` method requiring `faiss`/`torch`/`sentence-transformers`, when those packages are absent, calling the method SHALL raise `ImportError`. Importing the module SHALL NOT raise.

**Validates: Requirements 6.3, 6.4, 7.3, 7.4**

### Property 8: Truncation Warning

*For any* `sentence_records` list exceeding `max_sentences`, `build_sentence_store` SHALL emit `RuntimeWarning` and the returned store's `sentences` SHALL have length `max_sentences`.

**Validates: Requirement 6.5**

### Property 9: SemanticMatcher Null Returns

*For any* call where `faiss_index is None`, OR `sentences` is empty, OR top score < `threshold`, `SemanticMatcher.search` SHALL return `None`.

**Validates: Requirements 7.5, 7.6, 7.7**

### Property 10: SemanticMatcher Result Schema

*For any* successful `SemanticMatcher.search` call, the returned dict SHALL contain all seven required keys and `score` SHALL be in `[0.0, 1.0]`.

**Validates: Requirements 7.8, 7.9**

### Property 11: Deleted Path Imports Fail

*For any* of the three deleted paths (`utils.text_processor`, `pdf_extractor.utils.text_utils`, `pdf_extractor.utils.embedding_utils`), importing SHALL raise `ModuleNotFoundError`.

**Validates: Requirement 8.7**

---

## Error Handling

| Scenario | Error | Message pattern |
|---|---|---|
| ABC direct instantiation | `TypeError` | Standard Python ABC message |
| Missing `sentence-transformers` | `ImportError` | `"sentence-transformers is required. Install with: pip install sentence-transformers"` |
| Missing `faiss` | `ImportError` | `"faiss is required. Install with: pip install faiss-cpu"` |
| Missing `scispacy`/`spacy` | `ImportError` | `"scispaCy is not installed. Install with:\n  pip install scispacy\n  python -m spacy download en_core_sci_lg"` |
| Missing `wtpsplit` | `ImportError` | `"wtpsplit is not installed. Install with:\n  pip install wtpsplit"` |
| Missing `nltk` | `ImportError` | `"NLTK is not installed. Install with:\n  pip install nltk\n  python -c \"import nltk; nltk.download('punkt')\""` |
| Missing `stanza` | `ImportError` | `"Stanza is not installed. Install with:\n  pip install stanza"` |
| Loader class path unresolvable | `ModuleNotFoundError` / `AttributeError` | Propagates naturally from `importlib` |
| Short needle (< 10 chars) | silent `None` return | — |
| Empty `full_text` / `page_texts` | silent `None` return | — |
| Truncation in `build_sentence_store` | `RuntimeWarning` | `"PDF '{pdf_path}' has {N} sentences; truncating to first {max_sentences}."` |
| Cross-page attribution | `DEBUG` log | `"Cross-page sentence detected; attributing to page {N} (overlap={M} chars)."` |

---

## Testing Strategy

### Test Layout

```
tests/text_processing/
├── __init__.py
├── test_base_abc.py                  # ABC enforcement, SentenceSegment hierarchy
├── test_normalizers.py               # All 5 normalizer subclasses (example-based)
├── test_normalizers_properties.py    # PBT: idempotence (Property 3)
├── test_tokenizers.py                # SimpleWordTokenizer
├── test_matchers.py                  # LexicalMatcher, SemanticMatcher (example-based)
├── test_matchers_properties.py       # PBT: Properties 4, 5, 6, 9, 10
├── test_embedding.py                 # EmbeddingProcessor (mocked deps)
├── test_embedding_properties.py      # PBT: Properties 7, 8
├── test_import_isolation.py          # smoke: import without heavy deps
└── test_deleted_paths.py             # Property 11: deleted paths raise ImportError

tests/steering/
└── test_text_processing_separation.py  # text_processing/ does not import quality_control/
```

### Mocking Convention

Heavy optional dependencies are mocked using `patch.dict("sys.modules", ...)`:

```python
with patch.dict(sys.modules, {"faiss": None, "torch": None, "sentence_transformers": None}):
    for mod in list(sys.modules):
        if "text_processing.embedding" in mod:
            del sys.modules[mod]
    import text_processing.embedding  # succeeds
```

For `SentenceSegment` backends, mock `spacy`/`scispacy` at the module level:

```python
mock_spacy = MagicMock()
mock_doc = MagicMock()
mock_doc.sents = [MagicMock(text="Sentence one.")]
mock_spacy.load.return_value = MagicMock(return_value=mock_doc)

with patch.dict(sys.modules, {"scispacy": MagicMock(), "spacy": mock_spacy}):
    seg = ScispaCySentenceSegment()
    result = seg.tokenize_sentences("Sentence one.")
    assert result == ["Sentence one."]
```

### Slow Test Marking

Tests exercising `EmbeddingProcessor` or `SemanticMatcher` heavy paths carry:

```python
pytestmark = pytest.mark.slow
```

### Property-Based Tests

Use Hypothesis with `@given` and `@settings(max_examples=100)`:

```python
from hypothesis import given, settings
from hypothesis import strategies as st

# Property 3: Normalizer idempotence
@given(st.text())
@settings(max_examples=100)
def test_whitespace_normalizer_idempotent(s: str):
    n = WhitespaceNormalizer()
    assert n.normalize(n.normalize(s)) == n.normalize(s)
```

### Steering-Drift Tests

`tests/steering/test_text_processing_separation.py` uses the AST-walker pattern from `tests/test_dependency_directions.py` to verify no `.py` file under `text_processing/` imports from `quality_control/`.

`tests/test_dependency_directions.py` is extended with:
```python
("text_processing", "quality_control"),
```

### Existing Test Updates

| Current test file | Action |
|---|---|
| `tests/utils/test_text_processor.py` | Migrate to `tests/text_processing/test_base_abc.py`; update imports |
| `tests/pdf_extractor/test_text_utils.py` | Migrate to `tests/text_processing/test_matchers.py`; update imports |
| `tests/pdf_extractor/test_embedding_utils.py` | Migrate to `tests/text_processing/test_embedding.py`; update imports |
| `tests/utils/test_sentence_processor.py` | Update `normalise_text` import to `LineHealingNormalizer` |

---

## Implementation Ordering

Implementation proceeds in dependency order (leaf modules first):

| Step | Module | Depends on |
|---|---|---|
| 1 | `text_processing/__init__.py` (empty shell) | — |
| 2 | `text_processing/base.py` (ABCs + 5 backends) | — |
| 3 | `text_processing/normalizers.py` | `base.py` (for ABC inheritance) |
| 4 | `text_processing/tokenizers.py` | `normalizers.py` (composes `UnicodeNormalizer`) |
| 5 | `text_processing/matchers.py` | `normalizers.py` (uses `WhitespaceNormalizer`, `FullNormalizer`) |
| 6 | `text_processing/embedding.py` | `base.py` (for ABC inheritance) |
| 7 | Wire `__init__.py` exports | Steps 2–6 |
| 8 | Update `sentence_processor.py` | `normalizers.py` (imports `LineHealingNormalizer`) |
| 9 | Update callers of `exact_match_search` / `semantic_search` | `matchers.py` |
| 10 | Update callers of `embedding_utils.*` | `embedding.py` |
| 11 | Update `_load_text_processor` in `quality_control.py` | `base.py` (new import path) |
| 12 | Delete legacy files | Steps 8–11 complete |
| 13 | Migrate tests | Steps 8–12 complete |
| 14 | Update documentation + steering | Steps 12–13 complete |
| 15 | Add dependency-direction + steering-drift tests | Step 14 |

Each step is independently committable and testable. Steps 1–7 can be done in a single commit (the package is standalone). Steps 8–11 are one commit each (wiring). Step 12 is one commit (deletion). Steps 13–15 are one commit (test + docs).

---

## Requirements Traceability

| Requirement | Design component | Verified by |
|---|---|---|
| 1 (Package) | `text_processing/` structure + `__init__.py` | `test_import_isolation.py`, dependency-direction test |
| 2 (ABC) | `TextProcessor` in `base.py` | `test_base_abc.py`, Property 1 |
| 3 (Segmenters) | `SentenceSegment` + 5 backends in `base.py` | `test_base_abc.py`, Property 2 |
| 4 (Normalizers) | 5 classes in `normalizers.py` | `test_normalizers.py`, `test_normalizers_properties.py`, Property 3 |
| 5 (LexicalMatcher) | `LexicalMatcher` in `matchers.py` | `test_matchers.py`, `test_matchers_properties.py`, Properties 4–6 |
| 6 (EmbeddingProcessor) | `EmbeddingProcessor` in `embedding.py` | `test_embedding.py`, `test_embedding_properties.py`, Properties 7–8 |
| 7 (SemanticMatcher) | `SemanticMatcher` in `matchers.py` | `test_matchers.py`, `test_matchers_properties.py`, Properties 9–10 |
| 8 (Delete legacy) | File deletions | `test_deleted_paths.py`, Property 11 |
| 9 (PDF boundary) | `sentence_processor.py` update | `test_sentence_processor.py` (updated imports) |
| 10 (Audit table) | Symbol audit table in this document | Manual review + test file import checks |
| 11 (QC independence) | No QC imports in `text_processing/` | `test_text_processing_separation.py`, dependency-direction test |
| 12 (Tests) | `tests/text_processing/` directory | pytest collection + CI |
| 13 (Documentation) | README + steering updates | Manual review |
| 14 (Non-goals) | Scope boundary enforcement | Steering-drift tests |
