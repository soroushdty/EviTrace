# EviTrace Text Extractor Module

A standalone PDF parsing module designed as the foundation for the EviTrace evidence-matching system.

## Overview

EviTrace Parser resolves a single PDF source, extracts text through a four-tier cascade (PyMuPDF → pdfplumber → Tesseract → PaddleOCR), processes the extracted text into clean sentence records, and writes structured JSON artifacts suitable for downstream evidence matching.

---

## Pipeline

```text
run_pipeline()
 ├── pdf_extractor.utils.config_utils.load_config()            # Step 1 – load parser config
 ├── utils.path_utils.list_pdf_files_from_source() # Step 2 – resolve the PDF source
 └── for each PDF:
      ├── pdf_extractor.extraction.extract_pdf()           # Step 3 – extract text (cascade)
  ├── pdf_extractor.processing.sentence_processor.process_sentences() # Step 4 – sentence segmentation
  ├── pdf_extractor.processing.sentence_processor.build_full_text()   # Step 4 – assemble full text
      └── write <stem>.json to output folder     # Step 5 – save artifact
```

---

## Module Descriptions

### `pdf_extractor/pdf_extractor.py`

CLI entry point for the parser pipeline. Orchestrates the four-tier extraction cascade (PyMuPDF → pdfplumber → Tesseract → PaddleOCR), sentence processing, and JSON artifact output.

```bash
python -m pdf_extractor.pdf_extractor                # uses the project-root default config
python -m pdf_extractor.pdf_extractor --config /path/to/cfg
```

### `utils/path_utils.py`

Centralized path-resolution helpers. Supports local file/folder paths and URLs
(including Google Drive folder URLs via `gdown`).

- `list_pdf_files_from_dir` — resolves a PDF folder and returns metadata for all PDFs inside it
- `create_output_folder` — creates or resolves the output folder
- `resolve_project_path` — resolves config and output paths relative to the project root

### `pdf_extractor/extraction/`

Package providing a four-tier PDF text extraction cascade. The public entry
point is `pdf_extractor.extraction.extract_pdf(pdf_path, config)`.

- **Core tier** — PyMuPDF (fast, lossless, with font metadata and bboxes) and
  pdfplumber run in parallel; the higher-quality result is selected
- **Fallback Tier 1** — pdfplumber (table-aware layout extraction)
- **Fallback Tier 2** — Tesseract OCR (fallback when native extraction quality
  is below threshold)
- **Fallback Tier 3** — PaddleOCR (second OCR fallback; higher-scoring backend
  is chosen)

Quality is measured by the alphabetic-ratio heuristic. OCR libraries are
imported lazily.

### `pdf_extractor/processing/sentence_processor.py`

Text normalisation and sentence segmentation:

- `normalise_text` — heals mid-sentence line breaks, collapses whitespace
- `is_noise` — discards DOIs, emails, URLs, ORCID IDs, author lines, section headers
- `process_sentences` — segments blocks into filtered sentence records with page/bbox metadata
- `build_full_text` — assembles full-document text and per-page text dicts

### `pdf_extractor/utils/text_utils.py`

Text normalisation and search utilities for PDF quality control:

- `normalise_ws` — collapses whitespace runs and lowercases text
- `normalise_full` — applies `normalise_ws` then strips non-word characters
- `exact_match_search` — two-pass exact substring search of a sentence against
  extracted PDF text (Pass 1: whitespace-normalised; Pass 2: fully normalised)
- `semantic_search` — FAISS-based cosine-similarity search over a pre-built
  sentence store; delegates to `embed_query_fn` for query encoding

### `pdf_extractor/utils/embedding_utils.py`

Embedding engine for semantic QC (Metrics Tier 3). All heavy dependencies
(`sentence-transformers`, `faiss`, `torch`) are imported lazily so the module
can be imported without them installed:

- `load_embedding_model` — loads a `SentenceTransformer` model by name
- `l2_normalise` — L2-normalises a numpy embedding matrix
- `build_faiss_index` — builds a FAISS inner-product index from embeddings
- `build_sentence_store` — encodes all sentences from a PDF and returns a
  `Sentence_Store` dict containing `sentences`, `pages`, `block_bboxes`,
  `span_bboxes`, `embeddings`, and `faiss_index`
- `embed_query` — encodes a single query string with optional retrieval prefix

### `pdf_extractor/utils/layout_utils.py`

Layout-analysis helpers derived from PyMuPDF font-span metadata:

- `detect_section_heading` — returns the nearest preceding heading span for a
  given page index (heading = font size ≥ median + 2.0)
- `location_cross_check` — returns `(found_location, location_drift)` by
  comparing the detected section heading against a claimed location string

### `utils/logging_utils.py`

Idempotent logging setup with a file handler (always DEBUG) and a console
handler (configurable level). Safe to call multiple times.

---

## Quality Control Defaults

The QC pipeline ships with three ready-to-use default classes. You can run the
pipeline without writing any subclass — the defaults provide simple, reasonable
behaviour out of the box, and each can be replaced independently when you need
custom logic.

| Default class          | Inherits from       | Default behaviour                                                                                                                                               |
| ---------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `QualityReport`        | `QualityMetrics`    | Passes every branch unconditionally (`status = "pass"`). Override `passes_check()` to add real criteria.                                                        |
| `InterRaterReport`     | `InterRaterMetrics` | Pairwise pass/fail agreement: `1.0` if both branches share the same status, `0.0` otherwise. Override `compute()` for weighted or continuous agreement metrics. |
| `AdjudicationDecision` | `AdjudicationRules` | Elects the extractor with the most passing branches; confidence = fraction of passes. Override `adjudicate()` for weighted scoring or tie-breaking rules.       |

**Three levels of customisation:**

1. **Config only** — adjust thresholds in `config.yaml` under `quality_control.local_metrics`; `LocalQCReport` (the `QualityReport` subclass used by the rater) reads them automatically.
2. **Subclass one layer** — subclass whichever of the three classes you need and pass it to the relevant QC module; leave the others as defaults.
3. **Full custom** — subclass all three and wire them into `QCContext` for complete control over rating, agreement, and adjudication.

---

## Configuration (`config.yaml`)

The pipeline loads `config.yaml` from the project root by default. You can
override it with `--config /path/to/file.yaml`.

| Field                        | Type   | Default     | Description                                                          |
| ---------------------------- | ------ | ----------- | -------------------------------------------------------------------- |
| `log_file`                   | string | `"log.txt"` | Log file path (relative to project root or absolute).                |
| `log_level`                  | string | `"INFO"`    | Console log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `len_filter`                 | int    | `40`        | Minimum sentence length (characters) to survive filtering.           |
| `ocr`                        | bool   | `true`      | Enable OCR fallback tiers when PyMuPDF quality is insufficient.      |
| `ocr_text_quality_threshold` | float  | `0.7`       | Minimum alphabetic-ratio score to accept an extraction tier.         |
| `pdfs_path`                  | string | —           | A single PDF file path or URL to process.                            |
| `output_folder_path`         | string | `"output"`  | Output folder for parser artifacts.                                  |

---

## Output Artifacts

Each processed PDF produces a `<stem>.json` file in `output_folder_path`:

```json
{
  "pdf_name": "paper.pdf",
  "pdf_id": "<sha256>",
  "pdf_uri": "file:///path/to/paper.pdf",
  "blocks": [...],
  "sentence_records": [...],
  "full_pdf_text": "...",
  "page_texts": {0: "...", 1: "..."}
}
```

> **Note:** `page_texts` uses integer page-index keys in Python. When serialized
> to JSON (which requires string keys), these appear as `"0"`, `"1"`, etc.

---

## Requirements

- Python 3.10+
- `PyMuPDF>=1.24.0`
- `pdfplumber>=0.10.0`
- `numpy>=2.0.0`
- `gdown>=5.1.0` (for URL/Drive sources)
- `pytesseract` + `pdf2image` (lazily imported for Tesseract OCR)
- `paddleocr` + `paddlepaddle` (lazily imported for PaddleOCR)

**Optional heavy dependencies** (required only when
`quality_control.semantic_qc.enabled: true`):

- `sentence-transformers` — loads the BGE sentence encoder
- `faiss-cpu` or `faiss-gpu` — builds the FAISS similarity index
- `torch` — backend required by `sentence-transformers`

These three packages are never imported unless semantic QC is explicitly
enabled. The core parser runs without them.

Install core dependencies:

```bash
pip install -r requirements.txt
```

---

## Running Tests

```bash
python -m pytest -q
```
