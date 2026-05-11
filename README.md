# EviTrace

> Automated, evidence-grounded attribute extraction from scientific PDFs.

EviTrace ingests biomedical papers as PDFs and produces a structured,
auditable per-paper JSON containing the extracted answers for a user-defined
extraction map.

The pipeline combines:

1. A multi-backend **PDF text extractor** (GROBID, pdfplumber, PyMuPDF, PaddleOCR)
   with per-page scan detection and complementary, non-competing backend routing.
2. A **quality-control / adjudication** stage that compares extractor
   branches through a four-stage pipeline (rater → IAA → adjudicator → reconciler)
   and produces a single reconciled `UnifiedRecord`.
3. A **W3C JSON-LD annotation layer** projected from the `UnifiedRecord` by
   `pdf_extractor/annotation/`.
4. A **chunked LLM extraction agent** (OpenAI Responses API) with prompt-cache
   prewarm, section-aware evidence indexing, and strict JSON-Schema structured outputs.
5. A **synthesis chunk** that consumes prior extracted fields as read-only context.

---

## Table of Contents

- [General Information](#general-information)
- [Features](#features)
- [Setup](#setup)
- [Usage](#usage)
- [Workflow](#workflow)
- [Repository Structure](#repository-structure)
- [Configuration](#configuration)
- [Outputs](#outputs)
- [Technologies Used](#technologies-used)
- [Project Status](#project-status)
- [Acknowledgements](#acknowledgements)
- [Contact](#contact)
- [License](#license)

---

## General Information

EviTrace is a research tool for performing structured reviews of scientific
literature, with a focus on biomedical papers. The pipeline is general enough
to be used for the structured extraction of data with any user-defined set of
domains and fields.

The repository is organised as a pipeline of **independent, swappable
modules** so that the PDF extractor, the Quality Control layer, and the LLM
agent can each be reused outside the end-to-end review workflow.

---

## Features

- Per-page scan-detector routing to complementary, non-competing backends:
  GROBID (semantic authority) and pdfplumber (structural authority) for native
  pages; PaddleOCR (primary) and PyMuPDF built-in OCR (cross-validator) for
  scanned pages. Standalone Tesseract is never used.
- Generic four-stage QC pipeline (rater → IAA → adjudicator → reconciler)
  with injectable concern strategies (`TextFidelityConcern`,
  `SectionVerificationConcern`, `TableFigureMergeConcern`).
- Three-tier metrics hierarchy: Tier 1 (8 local heuristics), Tier 2
  (exact-match search), Tier 3 (FAISS semantic search — scaffolded only).
- GROBID TEI XML → ranked evidence bundle with section-aware scoring,
  keyword overlap, and char/item budget limits; cached to disk by
  `{paper_id}_{pdf_hash}`.
- Fields 1–2 (author, publication year) pre-filled locally from TEI metadata
  and never sent to the LLM.
- Chunked extraction against the OpenAI Responses API with prompt-cache
  prewarm and a synthesis chunk that consumes prior chunks as read-only context.
- Strict JSON-Schema structured outputs with local field-index validation
  independent of the API schema.
- W3C JSON-LD annotation artifacts produced by `pdf_extractor/annotation/`
  (sole producer — never built elsewhere).
- Idempotent run manifest (`manifest.json`) so partial runs can be resumed safely.
- Per-run flagged-fields QC report (`outputs/qc_report.csv`) for manual
  review of low-confidence or not-reported fields.
- Configurable number of extraction chunks; supports both 3-chunk and
  5-chunk layouts out of the box.
- Optional GROBID addon enrichment (grobid-quantities, datastet, entity-fishing).

---

## Setup

### Requirements

- Python 3.10+
- An OpenAI API key (Responses API access)
- Optional: a running [GROBID](https://github.com/kermitt2/grobid)
  instance (default URL `http://localhost:8070`); `auto_start: true` in
  config will launch it via Docker automatically
- Optional: PaddleOCR system dependencies if you want OCR for scanned pages

### Install

```bash
git clone <repo-url> EviTrace
cd EviTrace
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
```

### Add input PDFs

Drop PDFs into the `pdfs/` folder (or any folder pointed to by
`pdfs_path` in `configs/config.yaml`):

```bash
mkdir -p pdfs
cp /path/to/your/papers/*.pdf pdfs/
```

---

## Usage

### Run the end-to-end pipeline

```bash
python main.py                        # use pdfs/ folder, config defaults
python main.py --pdf-dir /data/papers # override input directory
python main.py --concurrency 2        # dial down if hitting rate limits
python main.py --no-cache-prewarm     # disable per-PDF cache warmup
```

A `run.log` (or the path configured in `log_file`) is written alongside
the run. Look for token/cache lines such as:

```text
[paper | warmup | gpt-4.1] tokens: input=..., cached=..., cache_hit=..%, output=...
[paper | chunk 1 | gpt-4.1] tokens: input=..., cached=..., cache_hit=..%, output=...
```

### Run only the PDF extractor

The PDF extractor is also runnable as a standalone module that emits a
per-paper JSON artifact (no OpenAI API key required):

```bash
python -m pdf_extractor.pdf_extractor                          # uses configs/config.yaml
python -m pdf_extractor.pdf_extractor --config /path/to/cfg   # explicit config
```

See [`pdf_extractor/README.md`](pdf_extractor/README.md) for details.

### Run the test suite

```bash
python -m pytest -q           # fast suite (slow tests excluded)
python -m pytest -q -m slow   # slow tests only
python -m pytest -q -m ""     # everything
```

See [`tests/README.md`](tests/README.md) for the layout of the test tree.

---

## Workflow

The end-to-end workflow per PDF, as orchestrated by
[`pipeline/`](pipeline/README.md):

1. **Per-page scan detection.** `scan_detector.classify_page()` runs five
   sequential stages on every page (empty text, low word count, low
   alpha-char ratio after `clean_ocr`, zero embedded fonts, image-area
   dominance) and classifies each page as `native` or `scanned`.
2. **Backend routing.** Native pages → GROBID (semantic authority, TEI XML)
   + pdfplumber (structural authority, text blocks); PyMuPDF font metadata
   stored in `ctx.unified.content`. Scanned pages with `ocr=true` →
   PaddleOCR (primary) + PyMuPDF built-in OCR (cross-validator). Scanned
   pages with `ocr=false` → skip extraction, log WARNING.
3. **Quality control.** `run_quality_control(branches, document_id, config)`
   runs the four-stage pipeline, computes the three-tier metrics hierarchy,
   and reconciles a `UnifiedRecord` carrying `exact_text` and W3C JSON-LD
   annotations.
4. **QC-to-LLM handoff guard.** `validate_qc_context_input(ctx)` performs
   five pre-flight checks on the `QCBundle` before field extraction begins.
5. **Evidence index.** `build_or_load_evidence_bundle()` parses GROBID TEI
   XML into a ranked, section-scored index (sentences, tables, figure
   captions), cached to disk by `{paper_id}_{pdf_hash}`. Fields 1–2 are
   pre-filled locally from TEI metadata.
6. **Cache prewarm (optional).** A tiny call warms the shared
   `(system + evidence package)` prefix. Synthesis-model warmup fires
   concurrently when models differ.
7. **Parallel extraction chunks.** Chunks `1..N-1` run concurrently with
   per-chunk evidence packages (section-aware scoring, keyword overlap,
   char/item budget limits).
8. **Local validation.** Each chunk's JSON is validated against the expected
   `field_index` set, key schema, confidence enum, and `loc` ID membership.
9. **Synthesis chunk.** Final chunk runs with prior chunk results as
   read-only context.
10. **Persist.** Fields merged, sorted by `field_index`, written to
    `outputs/<paper>.extracted.json`; manifest marked `complete`.
11. **QC report.** `generate_qc_report()` writes `outputs/qc_report.csv`
    flagging low-confidence and not-reported fields, and prints a summary.

```text
pdfs/paper.pdf
      |
      v
[scan_detector.classify_page()]  ── per-page classification ──► native | scanned
      |
      ├── native pages ──► GROBID (semantic) + pdfplumber (structural) + PyMuPDF (font metadata)
      └── scanned pages ──► PaddleOCR (primary) + PyMuPDF OCR (cross-validator)
      |
      v
[quality_control.run_quality_control]  ── QC pipeline ──► UnifiedRecord (exact_text + annotations)
      |
      v
[pipeline/evidence_index] ── GROBID TEI → ranked evidence bundle (cached)
      |
      v
[OpenAI chunk model] tiny cache warmup
      |
      +── chunk 1 ──┐
      +── chunk 2 ──┤  parallel
      +── ...    ──┘
      |
      v
[Python] validate chunks 1..N-1
      |
      v
[OpenAI synthesis model] chunk N (synthesis), receives prior chunks
      |
      v
[Python] merge all fields, save JSON, update manifest, generate QC CSV
```

PDFs run concurrently up to `concurrency.pdf_processing`. A global
semaphore (`concurrency.global_api_limit`) caps total concurrent OpenAI
API calls across all active PDFs.

---

## Repository Structure

```text
EviTrace/
├── main.py                       Top-level CLI entry point (asyncio.run)
├── requirements.txt              Runtime + test dependencies
├── pyproject.toml                pytest configuration (markers, importlib mode)
├── configs/                      YAML configuration + JSON schemas
│   ├── config.yaml               All runtime configuration
│   ├── extraction_map.json       62 extraction fields across 13 domain groups
│   ├── agent_schema.json         LLM system prompt, policies, extraction rules
│   └── structure_schema.json     JSON Schema (Draft 7) for pipeline dataclasses
├── agents/                       External-agent integrations (currently OpenAI only)
│   ├── validator.py              AgentSchemaValidator — sole reader of agent_schema.json
│   └── openai/                   Async OpenAI Responses API client + prompt builders
├── pipeline/                     End-to-end orchestrator
│   ├── extraction_pipeline.py    build_qc_bundle() — single source of truth for extraction flow
│   ├── orchestrator.py           Async run_pipeline(); PDF-level concurrency
│   ├── pdf_processor.py          Per-PDF LLM extraction orchestration
│   ├── evidence_index.py         GROBID TEI → ranked evidence bundle + disk cache
│   ├── extraction_map.py         Load extraction_map.json; group fields by chunk
│   ├── extraction_report.py      generate_qc_report(); writes qc_report.csv
│   ├── manifest.py               Idempotent checkpoint read/write
│   └── validator.py              Local JSON-Schema validation of LLM chunk outputs
├── pdf_extractor/                Standalone PDF text extraction module
│   ├── pdf_extractor.py          Standalone CLI (no OpenAI key required)
│   ├── pdf_validator.py          PDF-level structural validation
│   ├── extraction/               Per-backend extractors + scan_detector + schemas
│   ├── processing/               Sentence segmentation and full-text assembly
│   ├── annotation/               W3C JSON-LD projection and serialization
│   └── utils/                    text_utils, embedding_utils (no layout_utils here)
├── quality_control/              Generic four-stage QC pipeline
│   ├── models.py                 ALL shared dataclasses — import from here only
│   ├── quality_control.py        run_pipeline() + run_quality_control()
│   ├── local_metrics.py          LocalQCReport — 8 Tier 1 heuristic checks
│   ├── validator.py              Generic Validator + ValidationResult
│   ├── structure_validator.py    StructureSchemaValidator — sole reader of structure_schema.json
│   ├── validate_context.py       validate_qc_context_input() — QC-to-LLM handoff guard
│   ├── defaults/                 QualityReport, InterRaterReport, AdjudicationDecision
│   └── concerns/                 TextFidelityConcern, SectionVerificationConcern, TableFigureMergeConcern
└── utils/                        Repo-wide shared helpers
    ├── config_utils.py           load_openai_config, load_qc_config, load_local_config
    ├── path_utils.py             PROJECT_ROOT, PDF_DIR, OUTPUT_DIR, EXTRACTION_MAP, etc.
    ├── logging_utils.py          get_logger, setup_logging, log_cache_usage
    ├── text_processor.py         TextProcessor hub + SentenceSegment ABC + 5 backends
    └── grobid_manager.py         GrobidServerManager context manager; Docker lifecycle
```

| Directory | Purpose | Documentation |
| --------- | ------- | ------------- |
| `agents/` | External agent integrations | [agents/README.md](agents/README.md) |
| `agents/openai/` | OpenAI Responses API client and prompt builders | [agents/openai/README.md](agents/openai/README.md) |
| `configs/` | YAML configuration and JSON schema files | [configs/README.md](configs/README.md) |
| `pipeline/` | End-to-end chunked extraction orchestrator | [pipeline/README.md](pipeline/README.md) |
| `pdf_extractor/` | Standalone PDF text extraction module | [pdf_extractor/README.md](pdf_extractor/README.md) |
| `pdf_extractor/extraction/` | Per-page scan-detector routing and backend extractors | [pdf_extractor/extraction/README.md](pdf_extractor/extraction/README.md) |
| `pdf_extractor/processing/` | Sentence segmentation and full-text assembly | [pdf_extractor/processing/README.md](pdf_extractor/processing/README.md) |
| `pdf_extractor/utils/` | Text and embedding utilities | [pdf_extractor/utils/README.md](pdf_extractor/utils/README.md) |
| `quality_control/` | Branch adjudication and reconciliation | [quality_control/README.md](quality_control/README.md) |
| `utils/` | Repo-wide config, logging, and path helpers | [utils/README.md](utils/README.md) |
| `tests/` | Test suite | [tests/README.md](tests/README.md) |

---

## Configuration

The pipeline reads a single YAML file at `configs/config.yaml`
(see [configs/README.md](configs/README.md) for the full schema).
Environment variables override config values for OpenAI-related keys:

| Variable | Effect |
| -------- | ------ |
| `OPENAI_API_KEY` | Required. Auth for the Responses API. |
| `OPENAI_BASE_URL` | Override the API base URL. |
| `OPENAI_CHUNK_MODEL` | Model used for chunks `1..N-1`. |
| `OPENAI_SYNTHESIS_MODEL` | Model used for the final synthesis chunk. |
| `OPENAI_TEMPERATURE` | Force a specific temperature (omitted by default). |
| `OPENAI_PROMPT_CACHE_KEY_PREFIX` | Cache-key prefix; combined with SHA-256 of the evidence package. |
| `OPENAI_PROMPT_CACHE_RETENTION` | E.g. `"24h"`, `"in_memory"`. |
| `OPENAI_ENABLE_CACHE_PREWARM` | `0`/`false` to disable per-PDF warmup. |
| `OPENAI_CACHE_WARMUP_MAX_TOKENS` | Max output tokens for the warmup call. |
| `OPENAI_PREWARM_SYNTHESIS_IF_MODEL_DIFF` | Fire a synthesis-model warmup when models differ. |
| `OPENAI_NUM_CHUNKS` | Override `extraction.num_chunks` from the YAML. |

Override rule: **env > yaml > default**.

### Recommended model configuration

```bash
export OPENAI_CHUNK_MODEL="gpt-4.1"
export OPENAI_SYNTHESIS_MODEL="gpt-4.1"
export OPENAI_PROMPT_CACHE_RETENTION="24h"
```

### Cache notes

- The cached prefix is `system_prompt + shared evidence package`. Filename,
  run ID, timestamp, chunk number, and attempt number are deliberately
  excluded so the prefix matches across calls for the same PDF.
- Warmup failure is non-fatal: the run continues and the per-chunk cache
  hit may simply be lower.

### GPT-5.x note

Some GPT-5.x accounts/models reject the `temperature` parameter. The
client omits `temperature` unless `OPENAI_TEMPERATURE` is explicitly set.

---

## Outputs

| File | Description |
| ---- | ----------- |
| `outputs/<paper>.extracted.json` | Per-paper extraction (one record per `field_index`). |
| `outputs/qc_report.csv` | Cross-paper flagged rows: confidence `l` or `nr`. |
| `outputs/evidence_cache/<id>.evidence.json` | Cached evidence index (keyed by paper_id + pdf_hash). |
| `outputs/evidence_cache/<id>.tei.xml` | Cached GROBID TEI XML. |
| `manifest.json` | Per-PDF status checkpoint; safe to re-run after a crash. |
| `run.log` (configurable) | Per-run logs including token counts and cache-hit percentages. |

Each extracted record looks like:

```json
{
  "field_index": 1,
  "domain_group": "1. Study identification",
  "field_name": "title",
  "extracted_value": "...",
  "evidence": "...",
  "location": ["S000001"],
  "location_metadata": [...],
  "confidence": "h"
}
```

`confidence` is one of `h` (direct), `m` (minor synthesis), `l`
(ambiguous/weak), or `nr` (not reported).

---

## Technologies Used

- **Python 3.10+**
- **OpenAI Responses API** (`openai>=1.0.0`) — chunked structured-output extraction with prompt caching
- **PyMuPDF** (`PyMuPDF>=1.24.0`) — font metadata + scanned-path cross-validation
- **pdfplumber** (`pdfplumber>=0.10.0`) — structural text blocks (native path)
- **GROBID** — TEI XML semantic authority (optional service; auto-start via Docker)
- **PaddleOCR** (`paddleocr`, `paddlepaddle`) — OCR backend for scanned pages (lazy import)
- **PyYAML** — configuration loading
- **jsonschema** — JSON Schema Draft 7 validation
- **gdown** — URL / Google Drive folder ingestion
- **NumPy** — numeric helpers in embedding utilities
- **pytest** + **Hypothesis** — test runner + property-based testing
- **Optional:** `sentence-transformers`, `faiss-cpu`/`faiss-gpu`, `torch`
  for the semantic-QC scaffold (Tier 3); never imported unless
  `quality_control.semantic_qc.enabled` is `true`.

---

## Project Status

Active research project. Core pipeline is functional end-to-end. The
following components are explicitly scaffolded but **not driving final
adjudication** today:

- Semantic QC (embeddings + FAISS) — scaffolded only; not wired into adjudication.
- Multi-agent adjudication beyond the GROBID/pdfplumber branch pair.
- Optional GROBID addon enrichment (grobid-quantities, datastet, entity-fishing)
  — disabled by default; requires running service URLs.

Behaviour subject to change as the research evolves.

---

## Acknowledgements

- [GROBID](https://github.com/kermitt2/grobid) for the TEI extraction backend.
- [PyMuPDF](https://pymupdf.readthedocs.io/) and
  [pdfplumber](https://github.com/jsvine/pdfplumber) for the native
  text and layout extractors.

---

## Contact

For questions, please open an issue on the project repository.

---

## License

This project is licensed under the [GNU General Public License v3.0.](https://www.gnu.org/licenses/gpl-3.0.en.html)
