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

Both are used by `pdf_extractor/utils/text_utils.py` for exact-match
search and by `pdf_extractor/utils/embedding_utils.py` for building the
sentence store used by Tier 3 semantic QC.

---

## Where it fits

```text
extraction/extract_pdf  ──►  list[BlockDict]
                                  │
                                  ▼
processing/sentence_processor.process_sentences(blocks, len_filter, text_processor)
                                  ──►  list[SentenceRecord]

processing/sentence_processor.build_full_text(blocks)
                                  ──►  (full_text: str, page_texts: dict[int, str])
                                  │
                                  ▼
pdf_extractor/utils/text_utils.exact_match_search   (Tier 2 exact match)
pdf_extractor/utils/embedding_utils.build_sentence_store  (Tier 3 semantic QC)
```

---

## File: `sentence_processor.py`

Public functions:

| Function | Signature | Purpose |
| -------- | --------- | ------- |
| `normalise_text(text)` | `(str) -> str` | Heals mid-sentence line breaks (including hyphenated word-wraps), collapses repeated whitespace, and prepares a raw block for sentence segmentation. Preserves punctuation. |
| `is_noise(sentence)` | `(str) -> bool` | Pre-compiled-regex filter. Returns `True` for DOIs, emails, URLs, ORCID IDs, author lines, numbered section headers, and mostly-non-alpha strings. |
| `process_sentences(text_blocks_with_pages, len_filter, text_processor)` | `(list, int, TextProcessor) -> list[dict]` | Pipeline: normalise each block, segment into sentences via `text_processor.tokenize_sentences()`, drop sentences shorter than `len_filter` characters, drop sentences that match `is_noise`. Returns sentence records with `sentence`, `page_index`, `block_bbox`, `span_bboxes`. |
| `build_full_text(text_blocks_with_pages)` | `(list) -> tuple[str, dict[int, str]]` | Returns `(full_pdf_text, page_texts)` where `page_texts` is keyed by integer page index. No normalisation applied — preserves original text for exact-match location logic. |

The module declares no global state and runs no code at import time
(except pre-compiling the `is_noise` regex patterns, which happens once
when the module loads).

### `len_filter`

The sentence-length cutoff is supplied by the caller and is read from
`len_filter` in [`configs/config.yaml`](../../configs/README.md). The
default is `40` characters.

### `page_texts` keys

`page_texts` uses **integer** page-index keys in Python. When serialised
to JSON they appear as `"0"`, `"1"`, etc. Downstream consumers should
expect string keys after a round-trip through `json.dump`.

### `is_noise` patterns

The following patterns are filtered:

- `[N]` or `N. ` reference/bibliography lines
- DOI patterns (`doi:`, `https://doi.org`, `10.<digits>/`)
- Email addresses
- `http://` or `https://` URLs
- ORCID identifiers (`orcid.org` or `XXXX-XXXX-XXXX-XXXX`)
- Author/affiliation lines (3+ comma-separated capitalised names + digits)
- Mostly non-alphabetic strings (digits, punctuation, whitespace only)
- Numbered section headers (`3.2 Study design`, `10.1 …`)

---

## Inputs and outputs

- **Input:** the `BlockDict` list from any
  [`extraction/`](../extraction/README.md) backend, plus the configured
  `len_filter` and a `TextProcessor` instance.
- **Outputs:**
  - `process_sentences(...)` → `list[dict]` with fields
    `sentence`, `page_index`, `block_bbox`, `span_bboxes`.
  - `build_full_text(...)` → `(full_pdf_text: str, page_texts: dict[int, str])`.

---

## Caveats

- `normalise_text` is **not** the same as
  `pdf_extractor.utils.text_utils.normalise_ws` / `normalise_full`. The
  latter pair are used for *comparison* in QC and intentionally strip
  punctuation; this module's `normalise_text` preserves punctuation so
  that sentence segmentation works correctly.
- `is_noise` is regex-based and tuned for typical biomedical PDF artefacts.
  Add patterns there rather than scattering filters across callers.
- `process_sentences` requires a `TextProcessor` instance to be passed
  explicitly — the sentence segmenter backend is chosen by the caller,
  not hard-wired in this module.

---

## Related

- Parent: [../README.md](../README.md)
- Producer of `BlockDict` inputs: [../extraction/README.md](../extraction/README.md)
- Comparison-time text normalisers: [../utils/README.md](../utils/README.md)
- TextProcessor (sentence segmenter): [../../utils/README.md](../../utils/README.md)
- Root overview: [../../README.md](../../README.md)
