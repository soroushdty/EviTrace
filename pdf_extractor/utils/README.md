# `pdf_extractor/utils/` — Parser Utilities

Self-contained helpers used by the PDF extractor and the QC pipeline:

- **Text utilities** for QC-style text comparison and exact-match search.
- **Embedding utilities** for the optional Tier 3 semantic QC.

These modules are deliberately separate from
[`utils/`](../../utils/README.md) at the repo root, which holds
**cross-cutting** helpers (config, logging, paths, text processing). The
helpers here are scoped to the parser and may pull in optional heavy
dependencies (`sentence-transformers`, `faiss`, `torch`, `numpy`).

> **Note:** `layout_utils.py` is not present in this package. Layout-aware
> helpers (section heading detection, location cross-checks) are not part
> of the current public API.

---

## Files

### `text_utils.py`

Text normalisation and search helpers used by the QC text-comparison flow.

These functions are **distinct** from
[`pdf_extractor.processing.sentence_processor.normalise_text`](../processing/README.md),
which heals line breaks for segmentation. The functions here strip noise
so that two extracted strings can be compared directly.

Only standard-library modules are imported — no NumPy, no FAISS, no torch.

| Function | Signature | Purpose |
| -------- | --------- | ------- |
| `normalise_ws(text)` | `(str) -> str` | Pass 1: collapse all whitespace runs to a single space and lowercase. Punctuation is preserved. Idempotent. |
| `normalise_full(text)` | `(str) -> str` | Pass 2: apply `normalise_ws`, then strip every character that is neither a word character (`\w`) nor whitespace. Punctuation is removed. Idempotent. |
| `exact_match_search(exact_sentence, full_pdf_text, page_texts, blocks)` | `(str, str, dict, list) -> dict \| None` | Two-pass exact substring search: Pass 1 against `normalise_ws(full_pdf_text)`, Pass 2 against `normalise_full(full_pdf_text)`. Walks `page_texts` and `blocks` to recover page index and bounding-box context. Returns a result dict or `None`. |
| `semantic_search(exact_sentence, sentence_store, embed_query_fn, semantic_match_threshold, page_texts)` | `(str, dict, callable, float, dict \| None) -> dict \| None` | FAISS-based cosine-similarity search over a pre-built sentence store. Delegates query encoding to `embed_query_fn`. Returns `None` when the index is unavailable. |

`exact_match_search` result dict (on success):
```python
{
    'verification_status': 'exact_match',
    'confidence': 1.0,
    'found_sentence': str,
    'page_index': int,
    'prefix': str,   # up to 64 chars before match
    'suffix': str,   # up to 64 chars after match
    'block_bbox': tuple | None,
    'span_bboxes': list | None,
}
```

`semantic_search` result dict:
```python
{
    'verification_status': 'near_match' | 'not_found',
    'confidence': float,
    'found_sentence': str,
    'page_index': int | None,
    'prefix': str,
    'suffix': str,
    'block_bbox': tuple | None,
    'span_bboxes': list | None,
}
```

### `embedding_utils.py`

Embedding engine for Tier 3 semantic QC.

All heavy dependencies (`sentence-transformers`, `faiss`, `torch`) are
imported **lazily inside the function bodies** so this module can be
imported in any environment regardless of which optional packages are
installed.

| Function | Signature | Purpose |
| -------- | --------- | ------- |
| `load_embedding_model(model_name=_BGE_MODEL_NAME)` | `(str) -> SentenceTransformer` | Lazy-load a `SentenceTransformer` model. Raises `ImportError` with install hint if `sentence-transformers` is absent. |
| `embed_query(query_text, model, query_prefix=_BGE_QUERY_PREFIX)` | `(str, model, str) -> np.ndarray` | Encode a single query string with an optional retrieval prefix. Returns L2-normalised `(1, D)` float32 array. |
| `l2_normalise(vectors)` | `(np.ndarray) -> np.ndarray` | L2-normalise a 2-D float32 array in-place via `faiss.normalize_L2`. Returns unchanged if `shape[0] == 0`. |
| `build_faiss_index(embeddings)` | `(np.ndarray) -> faiss.Index` | Build a `faiss.IndexFlatIP` (inner-product) index. Moves to GPU 0 if CUDA is available. |
| `build_sentence_store(pdf_path, sentence_records, model)` | `(str, list[dict], model) -> dict` | Encode all sentences from a PDF in a single batched call and return the `SentenceStore` dict. Truncates to `_MAX_SENTENCES` (10,000) with a `RuntimeWarning`. |

`SentenceStore` dict keys: `pdf_path`, `sentences`, `pages`,
`block_bboxes`, `span_bboxes`, `embeddings`, `faiss_index`.

Default constants: `_BGE_MODEL_NAME = "BAAI/bge-base-en-v1.5"`,
`_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "`,
`_EMBEDDING_DIM = 768`, `_MAX_SENTENCES = 10_000`.

No global state is mutated at import time.

---

## Where it fits

```text
processing/sentence_processor.build_full_text
        │   (full_text, page_texts, blocks)
        ▼
text_utils.exact_match_search            (Tier 2 exact match)

embedding_utils.build_sentence_store     (one-time encode)
        │
        ▼
text_utils.semantic_search ──► embedding_utils.embed_query   (Tier 3 semantic match)
```

Tier 3 semantic search is wired into the QC scaffold but is **not**
currently used to drive final adjudication; see
[`quality_control/README.md`](../../quality_control/README.md).

---

## Configuration

| Key (`configs/config.yaml`) | Effect |
| --------------------------- | ------ |
| `quality_control.semantic_qc.enabled` | Master switch for Tier 3. When `false`, none of the heavy dependencies are imported. |
| `quality_control.semantic_qc.model_name` | `SentenceTransformer` model passed to `load_embedding_model`. |
| `quality_control.semantic_qc.query_prefix` | String prepended to every query in `embed_query`. |
| `quality_control.semantic_qc.similarity_threshold` | Cutoff for `semantic_search` matches. |
| `quality_control.semantic_qc.max_sentences` | Upper bound on the number of sentences encoded into the store. |

---

## Optional dependencies

Required only when Tier 3 semantic QC is enabled:

- `sentence-transformers` — loads the BGE encoder
- `faiss-cpu` or `faiss-gpu` — builds the similarity index
- `torch` — backend required by `sentence-transformers`

`numpy>=2.0.0` is always required for `embedding_utils.py`.

---

## Related

- Parent: [../README.md](../README.md)
- Sentence segmentation that produces inputs: [../processing/README.md](../processing/README.md)
- Backends that produce blocks: [../extraction/README.md](../extraction/README.md)
- QC consumer of these helpers: [../../quality_control/README.md](../../quality_control/README.md)
- Root overview: [../../README.md](../../README.md)
