# `pdf_extractor/extraction/` — Multi-Backend PDF Text Extractors

Backend-specific PDF text extractors and the cascade orchestrator that
selects between them.

The package exposes a single high-level entry point —
`extract_pdf(pdf_path, ocr, ocr_text_quality_threshold, embed_model=None)`
— and one backend module per extractor. The `quality_control` pipeline
also pulls `extract_with_grobid` and `extract_with_pymupdf` directly to
build its two QC branches.

---

## Where it fits

```text
                ┌── Cascade entry (extract_pdf)
                │
                ▼
PyMuPDF.extract_with_pymupdf  ──► (blocks, font_metadata)
        │                            │ score >= threshold? ─► return
        ▼
pdfplumber.extract_with_pdfplumber ─► blocks
        │                            │ score >= threshold? ─► return
        ▼
Tesseract.extract_with_tesseract   ─► blocks
        │                            │ score >= threshold? ─► return
        ▼
PaddleOCR.extract_with_paddleocr   ─► blocks
                                       │ pick whichever OCR scored higher
                                       ▼
                                   blocks


GROBID.extract_with_grobid          (used by quality_control branch 0)
        ─► (tei_xml_str, blocks)
```

`extract_pdf` is consumed by the standalone parser CLI in
[`pdf_extractor/pdf_extractor.py`](../README.md). The QC pipeline in
[`quality_control/`](../../quality_control/README.md) bypasses the
cascade and uses `extract_with_grobid` and `extract_with_pymupdf`
directly to obtain the two branches it adjudicates between.

---

## Files

### `__init__.py` — cascade orchestrator

- `extract_pdf(pdf_path, ocr, ocr_text_quality_threshold, embed_model=None)`
  walks the four-tier cascade. The first tier whose alphanumeric-ratio
  score (`_compute_quality_score`) meets `ocr_text_quality_threshold`
  wins. If `ocr` is `False`, the cascade stops after PyMuPDF
  regardless of score. If neither OCR backend clears the threshold,
  the higher-scoring one of the two wins.
- `_compute_quality_score(blocks, embed_model=None)` — alphanumeric
  characters / non-whitespace characters. `embed_model` is accepted
  but currently unused (reserved for future embedding-based scoring).

### `schemas.py`

Canonical, framework-free output types and validation helpers.

- `BlockDict` — `text`, `page_index`, `block_bbox`, `spans`.
- `SpanDict` — span-level attributes (font, size, flags, color, bbox).
- `FontMetaDict` — per-document span/font records.
- Factory helpers (`make_block`, `make_span`, …) and `validate_blocks`
  used by every backend so all extractors return shape-identical
  payloads.

No imports outside the standard library; no import-time side effects.

### `PyMuPDF.py`

PyMuPDF (`fitz`) backend. Returns `(blocks, font_metadata)`.

- One `BlockDict` per block, with text formed by joining all spans in
  that block.
- One `FontMetaDict` per span across the whole document — used by
  [`pdf_extractor/utils/layout_utils.py`](../utils/README.md) for
  section-heading detection.
- `fitz` is imported lazily inside the function body.

### `pdfplumber.py`

pdfplumber backend. Returns `list[BlockDict]` only (geometry fields
are `None`/`[]`). One block per page; preserves `[PAGE n]`,
`[TABLE]`, and `[/TABLE]` markers in the block's `text` field. Table
detection settings live in `_TABLE_SETTINGS`.

### `Tesseract.py`

Tesseract OCR backend. Returns `list[BlockDict]` only.

- `pytesseract` and `pdf2image` are installed lazily on first use
  (`subprocess.check_call(... pip install ...)`), then imported.
- Suitable as a fallback when the native extractors return garbled or
  empty text.

### `PaddleOCR.py`

PaddleOCR backend. Returns `list[BlockDict]` only.

- `paddleocr`, `paddlepaddle`, and `pdf2image` are installed lazily.
- Used as the second OCR fallback. The cascade compares Tesseract and
  Paddle scores when neither clears the threshold and picks the
  higher one.

### `GROBID.py`

GROBID REST-API backend. Returns `(tei_xml_str, blocks)`.

- Calls the `processFulltextDocument` endpoint configured under
  `quality_control.grobid` in
  [`config/config.yaml`](../../config/README.md) (default
  `http://localhost:8070`).
- Parses the TEI XML into `BlockDict` objects for cascade-style
  scoring; the **raw XML string** is what the QC pipeline uses as
  `BranchOutput.payload` (branch 0).
- `requests` is imported lazily inside the function body.

---

## Inputs and outputs

- **Input:** a path to a single PDF file (str or `os.PathLike`).
- **Output:**
  - `extract_pdf(...)` → `(list[BlockDict], list[FontMetaDict])`. The
    `font_metadata` list is non-empty only on the PyMuPDF path; OCR
    paths return `[]`.
  - `extract_with_grobid(...)` → `(tei_xml_str, list[BlockDict])`.

The `BlockDict` contract is enforced by `schemas.validate_blocks` on
every cascade exit.

---

## Configuration

| Key (`config.yaml`) | Effect |
| ------------------- | ------ |
| `ocr` | When `False`, the cascade stops after PyMuPDF and returns whatever it produced. |
| `ocr_text_quality_threshold` | Minimum alphanumeric-ratio score to accept a tier without falling through. |
| `quality_control.grobid.url` | GROBID server URL used by `extract_with_grobid`. |
| `quality_control.grobid.timeout` | Per-request timeout (seconds). |
| `quality_control.grobid.consolidate_header` / `consolidate_citations` | GROBID consolidation flags. |
| `quality_control.grobid.tei_coordinates` | Whether to ask GROBID for TEI coordinates. |
| `quality_control.grobid.max_retries` | GROBID retry count on transient failures. |

---

## Dependencies

- `PyMuPDF>=1.24.0` (always)
- `pdfplumber>=0.10.0` (always)
- `requests>=2.28.0` (lazy; required for GROBID)
- `pytesseract`, `pdf2image` (lazy; required only when the Tesseract
  tier is reached)
- `paddleocr`, `paddlepaddle`, `pdf2image` (lazy; required only when
  the PaddleOCR tier is reached)

OCR system binaries (`tesseract`, `poppler-utils` for `pdf2image`) are
**not** installed by `requirements.txt` — they must be available on
`$PATH` for the OCR tiers to work.

---

## Caveats and assumptions

- `_compute_quality_score` is a deliberately simple alphanumeric-ratio
  proxy. It will mis-score documents that mix unusual scripts or
  heavy mathematical notation. The QC pipeline is the right place to
  add stricter quality gates.
- The OCR backends auto-install missing Python packages with
  `pip install` on first use. This is convenient for research use but
  a footgun in restricted environments.
- The GROBID backend assumes a running GROBID instance at the
  configured URL; if unreachable, the `quality_control` pipeline
  falls back to the PyMuPDF branch.

---

## Related

- Parent: [../README.md](../README.md)
- Sentence segmentation that consumes `BlockDict`: [../processing/README.md](../processing/README.md)
- Layout helper consuming `FontMetaDict`: [../utils/README.md](../utils/README.md)
- QC consumer of GROBID + PyMuPDF: [../../quality_control/README.md](../../quality_control/README.md)
- Config keys: [../../config/README.md](../../config/README.md)
- Root overview: [../../README.md](../../README.md)
