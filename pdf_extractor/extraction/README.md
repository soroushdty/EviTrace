# `pdf_extractor/extraction/` — Per-Page Routed PDF Text Extractors

Backend-specific PDF text extractors, a per-page scan detector, and
shared output schemas. Each backend has a distinct authority role;
they are complementary and non-competing — routing is determined
per-page by `scan_detector`, not by a quality score.

---

## Public API

Five names are exported from `pdf_extractor.extraction`:

| Export | Kind | Description |
| ------ | ---- | ----------- |
| `extract_with_pymupdf` | function | PyMuPDF extraction (font metadata + scanned cross-validator) |
| `extract_with_pdfplumber` | function | pdfplumber extraction (structural authority) |
| `extract_with_paddleocr` | function | PaddleOCR extraction (scanned primary) |
| `scan_detector` | module | Per-page scan classification |
| `schemas` | module | Canonical output types and validation helpers |
| `PyMuPDF` | module | PyMuPDF backend module (re-exported for patch target resolution) |

`extract_with_grobid` is **not** part of the `pdf_extractor.extraction`
public API — it is called directly by `pipeline/extraction_pipeline.py`.

---

## Where it fits

```text
pipeline/extraction_pipeline.build_qc_bundle(pdf_path, pdf_name, qc_config)
      │
      ├── scan_detector.classify_page(page, text_processor, config, page_index)
      │         │
      │         ├── native/mixed page ──► digital path
      │         │         ├── GROBID          (semantic authority — TEI XML)
      │         │         ├── pdfplumber      (structural authority — text blocks)
      │         │         └── PyMuPDF         (font metadata stored in unified.content)
      │         │
      │         └── scanned page ──► OCR path
      │                   ├── PaddleOCR       (primary — bounding boxes + text)
      │                   └── PyMuPDF OCR     (built-in OCR — cross-validation)
      │
      └── quality_control/  ← consumes branch outputs for QC reconciliation
```

---

## Per-Page Scan Detection

`scan_detector.classify_page(page, text_processor, config, page_index=0) -> PageScanClassification`
is a stateless pure function that runs five sequential stages on a single
PyMuPDF page object:

1. **Empty text short-circuit** — `page.get_text("text").strip() == ""`.
   Fires immediately and short-circuits stages 2–5.
2. **Low word count** — word count < `config["scan_detection"]["text_density_threshold"]`.
3. **Low alpha-char ratio** — alpha-char fraction after `text_processor.clean_ocr()`
   < `config["scan_detection"]["alpha_ratio_threshold"]`.
4. **Zero embedded fonts** — `len(page.get_fonts()) == 0`.
5. **Image-area dominance** — image coverage > `config["scan_detection"]["image_dominance_threshold"]`.

A page is classified as `native` only when **no stage fires**.

`PageScanClassification` dataclass fields: `page_index`, `is_native`,
`triggered_stages` (list of stage numbers 1–5), `stage_values` (dict of
computed signals: `word_count`, `alpha_ratio`, `font_count`, `image_coverage`).

Thresholds are configurable under `quality_control.scan_detection` in
`configs/config.yaml`.

---

## Backend Roles

### GROBID — semantic authority

`extract_with_grobid(pdf_path, config) -> tuple[str, list[BlockDict]]`

Calls the GROBID `processFulltextDocument` REST endpoint and returns
`(tei_xml_str, list[BlockDict])`. The raw TEI XML string is the primary
payload consumed by the QC pipeline as `Candidate.payload` for the GROBID
branch. Used on the digital path only.

### pdfplumber — structural authority

`extract_with_pdfplumber(pdf_path) -> list[BlockDict]`

Returns `list[BlockDict]` for native/digital pages. One block per page;
preserves `[PAGE n]`, `[TABLE]`, and `[/TABLE]` markers in the block's
`text` field. Geometry fields are `None`/`[]` (pdfplumber does not produce
bounding boxes in the same coordinate space as PyMuPDF).

### PyMuPDF — font metadata + scanned cross-validator

`extract_with_pymupdf(pdf_path) -> tuple[list[BlockDict], list[FontMetaDict]]`

Returns `(list[BlockDict], list[FontMetaDict])`.

- On the **digital path**: provides font metadata stored in
  `ctx.unified.content` for section-heading detection and comparison signals.
- On the **scanned path**: runs PyMuPDF's built-in OCR as a cross-validation
  signal alongside PaddleOCR.

`fitz` is imported lazily inside the function body.

### PaddleOCR — scanned primary

`extract_with_paddleocr(pdf_path) -> list[BlockDict]`

Returns `list[PaddleOCRBlockDict]` for scanned pages. Each block carries
bounding-box coordinates (`block_bbox`) and OCR text. `paddleocr`,
`paddlepaddle`, and `pdf2image` are imported lazily.

---

## Files

| File | Purpose |
| ---- | ------- |
| `__init__.py` | Package re-exports (the six public API names) |
| `schemas.py` | `BlockDict`, `SpanDict`, `FontMetaDict`, `PaddleOCRBlockDict`; factory helpers; `validate_blocks` |
| `PyMuPDF.py` | PyMuPDF backend (`extract_with_pymupdf`) |
| `pdfplumber.py` | pdfplumber backend (`extract_with_pdfplumber`) |
| `PaddleOCR.py` | PaddleOCR backend (`extract_with_paddleocr`) |
| `GROBID.py` | GROBID REST backend (`extract_with_grobid` — called by `pipeline/extraction_pipeline.py`) |
| `scan_detector.py` | `classify_page()` — five-stage per-page scan classification |

### `schemas.py`

Canonical, framework-free output types and validation helpers.

- `BlockDict` — `text`, `page_index`, `block_bbox`, `spans`.
- `SpanDict` — span-level attributes (font, size, flags, color, bbox).
- `FontMetaDict` — per-document span/font records (size, text, page).
- `PaddleOCRBlockDict` — extends `BlockDict` with `rasterization_dpi` and
  `ocr_confidence`.
- Factory helpers: `make_block(text, page_index, block_bbox, spans)`,
  `make_ocr_block(text, page_index, block_bbox, rasterization_dpi, ocr_confidence)`,
  `make_font_meta(size, text, page)`.
- `validate_blocks(blocks)` — validates that every element conforms to
  `BlockDict` (required keys, correct types). Raises `ValueError` on
  violation. Called by every backend on exit.

No imports outside the standard library; no import-time side effects.

---

## Inputs and outputs

- **Input:** a path to a single PDF file (str or `os.PathLike`).
- **Output per backend:**
  - `extract_with_pymupdf(...)` → `(list[BlockDict], list[FontMetaDict])`
  - `extract_with_pdfplumber(...)` → `list[BlockDict]`
  - `extract_with_paddleocr(...)` → `list[PaddleOCRBlockDict]`
  - `extract_with_grobid(...)` → `(tei_xml_str, list[BlockDict])`

The `BlockDict` contract is enforced by `schemas.validate_blocks` on
every backend exit.

---

## Configuration

| Key (`configs/config.yaml`) | Effect |
| --------------------------- | ------ |
| `ocr` | When `False`, scanned pages are not sent to the OCR path. |
| `quality_control.grobid.url` | GROBID server URL. |
| `quality_control.grobid.timeout` | Per-request timeout (seconds). |
| `quality_control.grobid.tei_coordinates` | Whether to request TEI coordinates from GROBID. |
| `quality_control.grobid.max_retries` | GROBID retry count on transient failures. |
| `quality_control.scan_detection.text_density_threshold` | Min word count for a native page. |
| `quality_control.scan_detection.alpha_ratio_threshold` | Min alpha-char fraction after `clean_ocr`. |
| `quality_control.scan_detection.image_dominance_threshold` | Max image-area fraction before scanned. |
| `quality_control.ocr.rasterization_dpi` | DPI for PaddleOCR page rasterization. |

---

## Dependencies

- `PyMuPDF>=1.24.0` (always)
- `pdfplumber>=0.10.0` (always)
- `requests>=2.28.0` (lazy; required for GROBID)
- `paddleocr`, `paddlepaddle`, `pdf2image` (lazy; required only for scanned pages)

---

## Related

- Parent: [../README.md](../README.md)
- Single source of truth for routing: [../../pipeline/README.md](../../pipeline/README.md)
- Sentence segmentation that consumes `BlockDict`: [../processing/README.md](../processing/README.md)
- QC consumer of GROBID + pdfplumber branches: [../../quality_control/README.md](../../quality_control/README.md)
- Config keys: [../../configs/README.md](../../configs/README.md)
- Root overview: [../../README.md](../../README.md)
