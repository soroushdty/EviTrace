# `pdf_extractor/processing/` — Sentence Processing

Text normalisation, noise filtering, sentence segmentation, and
full-text assembly.

This package consumes the `BlockDict` payloads produced by the
[`extraction/`](../extraction/README.md) backends and turns them into
two artifact-ready forms:

1. A `sentence_records` list — one entry per surviving sentence with
   page index and bounding-box metadata.
2. A `(full_pdf_text, page_texts)` tuple — concatenated document text
   plus a `page_index → text` mapping.

Both are written to the per-paper `<stem>.json` artifact by the
parser CLI in [`pdf_extractor/pdf_extractor.py`](../README.md).

---

## Where it fits

```text
extraction/extract_pdf  ──►  list[BlockDict]
                                  │
                                  ▼
processing/sentence_processor.process_sentences   ──►  list[SentenceRecord]
processing/sentence_processor.build_full_text     ──►  (full_text, page_texts)
                                  │
                                  ▼
                  <stem>.json artifact written by pdf_extractor.pdf_extractor
```

---

## File: `sentence_processor.py`

Public functions:

| Function | Purpose |
| -------- | ------- |
| `normalise_text(text)` | Heals mid-sentence line breaks (including hyphenated word-wraps), collapses repeated whitespace, and otherwise prepares a raw block for sentence segmentation. |
| `is_noise(sentence)` | Pre-compiled-regex filter. Discards DOIs, emails, URLs, ORCID IDs, author lines, and section-heading-style strings before they enter the sentence list. |
| `process_sentences(text_blocks_with_pages, len_filter)` | Pipeline: normalise each block, segment into sentences, drop sentences shorter than `len_filter` characters, drop sentences that match `is_noise`. Emits sentence records carrying page index and bbox metadata. |
| `build_full_text(text_blocks_with_pages)` | Returns `(full_pdf_text, page_texts)` where `page_texts` is keyed by integer page index. The full text is assembled in block order. |

The module declares no global state and runs no code at import time.

### `len_filter`

The sentence-length cutoff is supplied by the caller and is read from
`len_filter` in [`config/config.yaml`](../../config/README.md). The
default is `40` characters.

### `page_texts` keys

`page_texts` uses **integer** page-index keys in Python. When the
parser serialises the artifact to JSON they appear as `"0"`, `"1"`,
…  Downstream consumers should expect string keys after a
round-trip through `json.dump`.

---

## Inputs and outputs

- **Input:** the `BlockDict` list from any
  [`extraction/`](../extraction/README.md) backend, plus the configured
  `len_filter`.
- **Outputs:**
  - `process_sentences(...)` → `list[SentenceRecord]` with fields
    `sentence`, `page_index`, `block_bbox`, `span_bboxes`, …
  - `build_full_text(...)` → `(full_pdf_text: str, page_texts:
    dict[int, str])`.

These are the structures consumed by:

- [`pdf_extractor/utils/text_utils.py`](../utils/README.md) for exact-
  and semantic-match search.
- [`pdf_extractor/utils/embedding_utils.py`](../utils/README.md) for
  building the sentence store used by Tier 3 semantic QC.

---

## Caveats

- `normalise_text` is **not** the same as
  `pdf_extractor.utils.text_utils.normalise_ws` /
  `normalise_full`. The latter pair are used for *comparison* in QC
  and intentionally strip punctuation; this module's `normalise_text`
  preserves punctuation so that sentence segmentation works.
- `is_noise` is regex-based and tuned for typical biomedical PDF
  artefacts (DOIs, ORCID IDs, etc.). Add patterns there rather than
  scattering filters across callers.

---

## Related

- Parent: [../README.md](../README.md)
- Producer of `BlockDict` inputs: [../extraction/README.md](../extraction/README.md)
- Comparison-time text normalisers: [../utils/README.md](../utils/README.md)
- Root overview: [../../README.md](../../README.md)
