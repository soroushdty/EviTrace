# `pdf_extractor/utils/` — Parser Utilities

Self-contained helpers used by the PDF extractor and the QC pipeline:

- **Text utilities** for QC-style text comparison.
- **Embedding utilities** for the optional Tier 3 semantic QC.
- **Layout utilities** for section-heading detection and location
  cross-checks against PyMuPDF font metadata.

These modules are deliberately separate from
[`utils/`](../../utils/README.md) at the repo root, which holds
**cross-cutting** helpers (config, logging, paths). The helpers here
are scoped to the parser and may pull in optional heavy dependencies
(`sentence-transformers`, `faiss`, `torch`, `numpy`).

---

## Files

### `text_utils.py`

Text normalisation and search helpers used by the QC text-comparison
flow.

These functions are **distinct** from
[`pdf_extractor.processing.sentence_processor.normalise_text`](../processing/README.md),
which heals line breaks for segmentation. The functions here strip
noise so that two extracted strings can be compared directly.

| Function | Purpose |
| -------- | ------- |
| `normalise_ws(text)` | Pass 1: collapse all whitespace runs to a single space and lowercase. Punctuation is preserved. |
| `normalise_full(text)` | Pass 2: apply `normalise_ws`, then strip every character that is neither a word character (`\w`) nor whitespace. Punctuation is removed. |
| `exact_match_search(exact_sentence, full_pdf_text, page_texts, blocks)` | Two-pass exact substring search: first against `normalise_ws(full_pdf_text)`, then against `normalise_full(full_pdf_text)`. Walks `page_texts` and `blocks` to recover page index and bounding-box context for the match. |
| `semantic_search(query, sentence_store, embed_query_fn, similarity_threshold, k)` | FAISS-based cosine-similarity search over a pre-built sentence store. Delegates query encoding to `embed_query_fn` so the heavy embedding dependencies stay confined to `embedding_utils.py`. |

Only standard-library modules are imported here — no NumPy, no FAISS,
no torch.

### `embedding_utils.py`

Embedding engine for Tier 3 semantic QC.

All heavy dependencies (`sentence-transformers`, `faiss`, `torch`) are
imported **lazily inside the function bodies** so this module can be
imported in any environment regardless of which optional packages are
installed.

| Function | Purpose |
| -------- | ------- |
| `load_embedding_model(model_name)` | Lazy-load a `SentenceTransformer` model. |
| `l2_normalise(matrix)` | L2-normalise a 2-D float32 matrix in place via `faiss.normalize_L2`. |
| `build_faiss_index(embeddings)` | Build a `faiss.IndexFlatIP` (inner-product) index. After L2 normalisation IP is equivalent to cosine similarity. |
| `build_sentence_store(blocks, len_filter, model)` | Encode all sentences from a PDF and return the `Sentence_Store` dict (`sentences`, `pages`, `block_bboxes`, `span_bboxes`, `embeddings`, `faiss_index`). |
| `embed_query(query, model, query_prefix=None)` | Encode a single query string with an optional retrieval prefix. |

No global state is mutated at import time. In particular, neither
`np.random.seed()` nor `torch.manual_seed()` are called.

### `layout_utils.py`

Layout-aware helpers derived from PyMuPDF font-span metadata.

| Function | Purpose |
| -------- | ------- |
| `detect_section_heading(page_index, font_metadata)` | Return the text of the nearest preceding section heading on `page_index`. A span is classified as a heading when its font size is at least `median_size + 2.0` across all spans in the document. |
| `location_cross_check(claimed_location, page_index, font_metadata)` | Return `(found_location, location_drift)` by comparing the detected section heading against a claimed location string. Used to flag drift between an extracted value's claimed location and the layout-derived heading. |

Imports `numpy` only.

---

## Where it fits

```text
extraction/extract_pdf
        │   (font_metadata, blocks)
        ▼
layout_utils.detect_section_heading      (per-page heading detection)
layout_utils.location_cross_check        (drift check vs. claimed location)

processing/sentence_processor.build_full_text
        │   (full_text, page_texts, blocks)
        ▼
text_utils.exact_match_search            (Tier 2 exact match)

embedding_utils.build_sentence_store     (one-time encode)
        │
        ▼
text_utils.semantic_search ──► embedding_utils.embed_query   (Tier 3 semantic match)
```

Tier 3 semantic search is wired into the parser/QC scaffold but is
**not** currently used to drive final adjudication; see
[`quality_control/README.md`](../../quality_control/README.md).

---

## Configuration

| Key (`config.yaml`) | Effect |
| ------------------- | ------ |
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

`numpy>=2.0.0` is required for `layout_utils.py`.

---

## Related

- Parent: [../README.md](../README.md)
- Sentence segmentation that produces inputs: [../processing/README.md](../processing/README.md)
- Backends that produce font metadata: [../extraction/README.md](../extraction/README.md)
- QC consumer of these helpers: [../../quality_control/README.md](../../quality_control/README.md)
- Root overview: [../../README.md](../../README.md)
