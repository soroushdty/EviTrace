# `pdf_extractor/` — Standalone PDF Text Extraction Module

The PDF parser that backs EviTrace. Resolves a single PDF source,
runs per-page scan detection, routes to the correct extraction backends,
processes the extracted text into a reconciled `UnifiedRecord`, and writes
structured JSON artifacts suitable for downstream evidence matching.

This module is **standalone**: it can be invoked directly via
`python -m pdf_extractor.pdf_extractor` or imported and used in other
pipelines. The [`pipeline/`](../pipeline/README.md) orchestrator delegates
to `pipeline/extraction_pipeline.py::build_qc_bundle()`, which is the
single source of truth for the full extraction flow.

---

## Where it fits

```text
configs/config.yaml
        │
        ▼
pdf_extractor.pdf_extractor (CLI)
        │
        ├──► utils.path_utils.list_pdf_files_from_source   # resolve PDF source
        ├──► pipeline.extraction_pipeline.build_qc_bundle  # scan detection → routing → QC → annotation
        └──► <stem>.extracted.json                         # write artifact

pipeline.extraction_pipeline.build_qc_bundle
        ├──► pdf_extractor.extraction.GROBID               # semantic authority (TEI XML)
        ├──► pdf_extractor.extraction.pdfplumber           # structural authority (text blocks)
        ├──► pdf_extractor.extraction.PaddleOCR            # scanned primary
        ├──► pdf_extractor.extraction.PyMuPDF              # font metadata / OCR cross-validator
        ├──► quality_control.run_quality_control           # QCBundle + UnifiedRecord
        └──► pdf_extractor.annotation                      # W3C JSON-LD projection
```

---

## Repository layout

```text
pdf_extractor/
├── pdf_extractor.py        CLI entry point (standalone, no OpenAI key required)
├── pdf_validator.py        PDF-level structural validation
├── layout_utils.py         detect_section_heading(), location_cross_check()
├── __init__.py             Exports: build_full_text, PDFValidationError
├── extraction/             Multi-backend extraction + scan_detector + schemas
├── processing/             Sentence segmentation and full-text assembly
├── annotation/             W3C JSON-LD projection and serialization
└── utils/                  text_utils, embedding_utils
```

| Sub-package | README |
| ----------- | ------ |
| `extraction/` | [extraction/README.md](extraction/README.md) |
| `processing/` | [processing/README.md](processing/README.md) |
| `annotation/` | W3C annotation layer (see below) |
| `utils/` | [utils/README.md](utils/README.md) |

---

## Top-level files

### `pdf_extractor.py`

CLI entry point. Runs the full multi-backend extraction pipeline and writes
`UnifiedRecord`-based JSON artifacts. Does **not** require an OpenAI API key.

```bash
python -m pdf_extractor.pdf_extractor                          # default config (configs/config.yaml)
python -m pdf_extractor.pdf_extractor --config /path/to/cfg   # explicit config
```

`run_pipeline(config_path)` — loads config, resolves PDF sources, starts
`GrobidServerManager`, calls `build_qc_bundle` for each PDF, and saves
artifacts.

`_unified_to_artifact(pdf_name, pdf_info, ctx) -> dict` — serialises a
`QCBundle` into a JSON-serialisable artifact dict.

`_save_artifact(output_folder, pdf_name, artifact) -> str` — writes the
artifact to `<output_folder>/<stem>.extracted.json`.

### `pdf_validator.py`

Standalone PDF file validation.

`validate_pdf(path) -> None` — validates a PDF in strict short-circuit order:
1. Magic bytes (`b"%PDF-"`).
2. File size (non-zero).
3. Password protection (`doc.needs_pass` must be `False`).
4. Fitz readability (`fitz.open()` must not raise).

Raises `PDFValidationError` at the first failing check.

`PDFValidationError` — exception class raised by `validate_pdf`.

### `layout_utils.py`

Layout-aware helpers used by `pdf_extractor/utils/text_utils.py` and
`pdf_extractor/utils/embedding_utils.py` for section heading detection
and location cross-checking.

- `detect_section_heading(page_index, font_metadata) -> str` — returns
  the text of the nearest preceding section heading for a given page.
  A span is classified as a heading when its font size is ≥
  `median_size + 2.0` across all document spans. Returns `''` when no
  heading is found.
- `location_cross_check(found_page_index, font_metadata, claimed_location) -> tuple[str, bool]`
  — builds a human-readable `found_location` string (e.g. `"p.3 — Introduction"`)
  and returns `(found_location, location_drift)`. Location drift is
  informational only — it never invalidates a match.

### `__init__.py`

Exports:
- `build_full_text` — from `pdf_extractor.processing.sentence_processor`
- `PDFValidationError` — from `pdf_extractor.pdf_validator`

### `annotation/`

W3C JSON-LD annotation layer. Sole producer of W3C annotation dicts.

- `AnnotationRecord` — dataclass for a single projected annotation record.
  Fields: `sentence_text`, `page_index`, `selector_type`,
  `selector_payload`, `quote_selector`, `ocr_derived`, `body_value`.
- `project(unified, base_uri="") -> list[AnnotationRecord]` — reads only
  `unified.semantic` and `unified.alignment`. Returns one record per
  sentence. Uses `TextPositionSelector` for native sentences and
  `FragmentSelector` for OCR-derived sentences.
- `generate_w3c_jsonld(records, base_uri="") -> list[dict]` — sole
  producer of W3C JSON-LD dicts. Each dict contains `@context`, `id`,
  `type`, `body`, `target`. Returns `[]` when `records` is empty.

---

## Output artifact

Each processed PDF produces a `<stem>.extracted.json` in
`output_folder_path` (default `outputs/`):

```json
{
  "pdf_name": "paper.pdf",
  "pdf_id": "<sha256>",
  "pdf_uri": "file:///path/to/paper.pdf",
  "document_id": "paper",
  "content": {
    "exact_text": "...",
    "annotations": [...],
    "pages": [...],
    "segments": [...],
    "source_pdf_path": "...",
    "grobid_tei_xml": "..."
  },
  "semantic": {...},
  "structural": {...},
  "alignment": {...},
  "branches": [{"source": "grobid", "index": 0, "status": "pass"}, ...],
  "metrics_hierarchy": {"local_metrics": [...], "exact_match": [...], "semantic_match": [...]}
}
```

---

## Configuration (`configs/config.yaml`)

Defaults to `configs/config.yaml` from the project root; override with
`--config /path/to/file.yaml`. Full schema:
[../configs/README.md](../configs/README.md).

| Field | Type | Default | Description |
| ----- | ---- | ------- | ----------- |
| `log_file` | string | `"log.txt"` | Log file path. |
| `log_level` | string | `"INFO"` | Console log level. |
| `len_filter` | int | `40` | Minimum sentence length (characters). |
| `ocr` | bool | `true` | Enable PaddleOCR for scanned pages. |
| `pdfs_path` | string | — | A single PDF, a folder, or a URL. |
| `output_folder_path` | string | `"output"` | Output folder for artifacts. |

`pdfs_path` may be a single `.pdf` file, a directory, an `http(s)://` URL,
or a `drive.google.com/.../folders/...` URL.

---

## Requirements

- Python 3.10+
- `PyMuPDF>=1.24.0`
- `pdfplumber>=0.10.0`
- `numpy>=2.0.0`
- `gdown>=5.1.0` (for URL / Drive sources)
- `requests>=2.28.0` (lazy; required for GROBID branch)
- `paddleocr` + `paddlepaddle` + `pdf2image` (lazy; required only for scanned pages)

Optional (Tier 3 semantic QC only):

- `sentence-transformers`
- `faiss-cpu` or `faiss-gpu`
- `torch`

---

## Running tests

```bash
python -m pytest -q           # default: skip slow tests
python -m pytest -q -m ""     # everything
python -m pytest -q -m slow   # only slow tests
```

Test layout: [../tests/README.md](../tests/README.md).

---

## Related

- Single source of truth for extraction flow: [../pipeline/README.md](../pipeline/README.md)
- Multi-backend extractors: [extraction/README.md](extraction/README.md)
- Sentence segmentation and full-text assembly: [processing/README.md](processing/README.md)
- Text / embedding helpers: [utils/README.md](utils/README.md)
- QC layer: [../quality_control/README.md](../quality_control/README.md)
- Configuration: [../configs/README.md](../configs/README.md)
- Root overview: [../README.md](../README.md)
