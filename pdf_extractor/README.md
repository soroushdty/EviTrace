# `pdf_extractor/` — Standalone PDF Text Extraction Module

The PDF parser that backs EviTrace. Resolves a single PDF source,
extracts text through a four-tier cascade (PyMuPDF → pdfplumber →
Tesseract → PaddleOCR), processes the extracted text into clean
sentence records, and writes structured JSON artifacts suitable for
downstream evidence matching.

This module is **standalone**: it can be invoked directly via
`python -m pdf_extractor.pdf_extractor` or imported and used in other
pipelines. The [`pipeline/`](../pipeline/README.md) orchestrator and
the [`quality_control/`](../quality_control/README.md) QC layer both
build on it.

---

## Where it fits

```text
config/config.yaml
        │
        ▼
pdf_extractor.pdf_extractor (CLI)
        │
        ├──► utils.path_utils.list_pdf_files_from_source   # resolve PDF source
        ├──► pdf_extractor.extraction.extract_pdf          # 4-tier cascade
        ├──► pdf_extractor.processing.sentence_processor   # segment + assemble
        └──► <stem>.json                                   # write artifact

quality_control.run_quality_control            # parallel use:
        ├──► pdf_extractor.extraction.extract_with_grobid    # branch 0 (TEI XML)
        └──► pdf_extractor.extraction.extract_with_pymupdf   # branch 1 (native blocks)
```

---

## Pipeline (CLI)

```text
run_pipeline()
 ├── pdf_extractor.utils.config_utils.load_config()                    # 1. load parser config
 ├── utils.path_utils.list_pdf_files_from_source()                     # 2. resolve PDF source
 └── for each PDF:
      ├── pdf_extractor.extraction.extract_pdf()                       # 3. extract text (cascade)
      ├── pdf_extractor.processing.sentence_processor.process_sentences()  # 4. sentence segmentation
      ├── pdf_extractor.processing.sentence_processor.build_full_text()    # 4. assemble full text
      └── write <stem>.json to output folder                           # 5. save artifact
```

---

## Repository layout

```text
pdf_extractor/
├── pdf_extractor.py        CLI entry point
├── __init__.py             Convenience: extract_pdf_text(...) helper
├── conftest.py             Pytest path-setup so `pdf_extractor.*` resolves
├── pyproject.toml          Pytest config (markers, importlib mode)
├── extraction/             Multi-backend extraction cascade  → ./extraction/README.md
├── processing/             Sentence segmentation              → ./processing/README.md
└── utils/                  Text / embedding / layout helpers → ./utils/README.md
```

| Sub-package | README |
| ----------- | ------ |
| `extraction/` | [extraction/README.md](extraction/README.md) |
| `processing/` | [processing/README.md](processing/README.md) |
| `utils/` | [utils/README.md](utils/README.md) |

---

## Top-level files

### `pdf_extractor.py`

CLI entry point. Drives the four-tier cascade, sentence processing,
and JSON artifact output.

```bash
python -m pdf_extractor.pdf_extractor                       # default config (config/config.yaml)
python -m pdf_extractor.pdf_extractor --config /path/to/cfg # explicit config
```

### `__init__.py`

Exposes a thin convenience wrapper for callers that only need plain
text:

```python
from pdf_extractor import extract_pdf_text

text = extract_pdf_text("paper.pdf", ocr=False, ocr_text_quality_threshold=0.5)
```

This is what the OpenAI orchestrator path uses indirectly (the
`UnifiedRecord` text travels through `quality_control` first).

### `conftest.py`

Inserts the project root at the front of `sys.path` so that
`pdf_extractor.*` and `utils.*` resolve correctly during pytest
collection. Also picked up at the repo root.

### `pyproject.toml`

Pytest configuration only (no packaging). Registers the `slow`
marker and configures `--import-mode=importlib`.

### `next steps.txt`

Free-form planning document for future work (extraction manifest,
optional semantic QC, task-specific evaluation metrics scaffold).
Not part of the runtime.

---

## Output artifact

Each processed PDF produces a `<stem>.json` in
`output_folder_path` (default `outputs/`):

```json
{
  "pdf_name": "paper.pdf",
  "pdf_id": "<sha256>",
  "pdf_uri": "file:///path/to/paper.pdf",
  "blocks": [...],
  "sentence_records": [...],
  "full_pdf_text": "...",
  "page_texts": {"0": "...", "1": "..."}
}
```

> **Note:** `page_texts` uses **integer** page-index keys in Python.
> When serialised to JSON they appear as `"0"`, `"1"`, etc.

---

## Configuration (`config.yaml`)

Defaults to `config/config.yaml` from the project root; override with
`--config /path/to/file.yaml`. Full schema:
[../config/README.md](../config/README.md).

| Field                        | Type   | Default     | Description                                                          |
| ---------------------------- | ------ | ----------- | -------------------------------------------------------------------- |
| `log_file`                   | string | `"log.txt"` | Log file path (relative to project root or absolute).                |
| `log_level`                  | string | `"INFO"`    | Console log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `len_filter`                 | int    | `40`        | Minimum sentence length (characters) to survive filtering.           |
| `ocr`                        | bool   | `true`      | Enable OCR fallback tiers when PyMuPDF quality is insufficient.      |
| `ocr_text_quality_threshold` | float  | `0.7`       | Minimum alphanumeric-ratio score to accept an extraction tier.       |
| `pdfs_path`                  | string | —           | A single PDF file path, a folder path, or a URL.                     |
| `output_folder_path`         | string | `"output"`  | Output folder for parser artifacts.                                  |

`pdfs_path` may be:

- A single `.pdf` file
- A directory of `.pdf` files
- An `http(s)://` URL (downloaded via `gdown`)
- A `drive.google.com/.../folders/...` URL (downloaded via
  `gdown.download_folder`)

---

## Quality control

Quality control is a separate module — `pdf_extractor` only feeds it
the extraction branches. For module structure, configurable QC stages,
and the Tier 1/2/3 metrics hierarchy, see
[../quality_control/README.md](../quality_control/README.md).

---

## Requirements

- Python 3.10+
- `PyMuPDF>=1.24.0`
- `pdfplumber>=0.10.0`
- `numpy>=2.0.0`
- `gdown>=5.1.0` (for URL / Drive sources)
- `requests>=2.28.0` (lazy import for the GROBID branch)
- `pytesseract` + `pdf2image` (lazy; required only when the Tesseract
  tier is reached)
- `paddleocr` + `paddlepaddle` + `pdf2image` (lazy; required only when
  the PaddleOCR tier is reached)

Optional, required only when `quality_control.semantic_qc.enabled` is
`true`:

- `sentence-transformers`
- `faiss-cpu` or `faiss-gpu`
- `torch`

```bash
pip install -r requirements.txt
```

---

## Running tests

```bash
python -m pytest -q                # default: skip slow tests
python -m pytest -q -m ""          # everything
python -m pytest -q -m slow        # only slow tests
```

Test layout: [../tests/README.md](../tests/README.md).

---

## Related

- Multi-backend extractors: [extraction/README.md](extraction/README.md)
- Sentence segmentation and full-text assembly: [processing/README.md](processing/README.md)
- Text / embedding / layout helpers: [utils/README.md](utils/README.md)
- QC layer that reuses `extract_with_grobid` / `extract_with_pymupdf`:
  [../quality_control/README.md](../quality_control/README.md)
- Configuration: [../config/README.md](../config/README.md)
- Root overview: [../README.md](../README.md)
