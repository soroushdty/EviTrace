# EviTrace

> Automated, evidence-grounded attribute extraction from scientific PDFs.

EviTrace ingests biomedical papers as PDFs and produces a structured,
auditable per-paper JSON containing the extracted answers for a user-defined
extraction map.

The pipeline combines:

1. A multi-backend **PDF text extractor** (GROBID, PyMuPDF, pdfplumber, PaddleOCR).
2. A **quality-control / adjudication** stage that compares extractor
   branches and produces a single reconciled record.
3. An ** extraction agent** with prompt-cache prewarm and
   strict JSON-Schema structured outputs.
4. A **synthesis layer** using the prior extracted fields as
   read-only context to produce synthesized field (e.g. reviewer/critique fields).

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

EviTrace is a research tool for performing reviews of scientific
literature, with a focus on biomedical papers. the pipeline is general enough
to be used for the structured extraction of data with any user-defined set of domains and fields.

The repository is organised as a pipeline of **independent, swappable
modules** so that the PDF extractor, the Quality Control layer, and the LLM agent
can each be reused outside the end-to-end review workflow.

---

## Features

- Multi-backend PDF text extraction with quality-driven cascade
  (PyMuPDF → pdfplumber → Tesseract → PaddleOCR), plus an optional
  GROBID branch used by the QC adjudicator.
- Generic branch-adjudication QC pipeline with pluggable rater, IAA,
  adjudicator, and reconciler stages.
- Chunked extraction against the OpenAI Responses API with prompt cache
  prewarm and a synthesis chunk that consumes prior chunks as
  read-only context.
- Strict JSON-Schema structured outputs with local field-index
  validation independent of the API schema.
- Idempotent run manifest (`manifest.json`) so partial runs can be
  resumed safely.
- Per-run flagged-fields QC report (`outputs/qc_report.csv`) for manual
  review of low-confidence or not-reported fields.
- Configurable number of extraction chunks; supports both 3-chunk and
  5-chunk layouts out of the box.

---

## Setup

### Requirements

- Python 3.10+
- An OpenAI API key (Responses API access)
- Optional: a running [GROBID](https://github.com/kermitt2/grobid)
  instance for the GROBID branch of the QC pipeline (default URL
  `http://localhost:8070`)
- Optional: PaddleOCR system dependencies if you want the
  OCR fallback tiers in the PDF extractor

### Install

```bash
git clone <repo-url> EviTrace
cd EviTrace
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
```

### Add input PDFs

Drop PDFs into the `pdfs/` folder (or any folder pointed to by
`pdfs_path` in `config/config.yaml`):

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

A `pipeline.log` (or the path configured in `log_file`) is written
alongside the run. Look for token/cache lines such as:

```text
[paper | warmup | gpt-4.1] tokens: input=..., cached=..., cache_hit=..., output=...
[paper | chunk 1 | gpt-4.1] tokens: input=..., cached=..., cache_hit=..., output=...
```

### Run only the PDF extractor

The PDF extractor is also runnable as a standalone module that emits a
per-paper JSON artifact suitable for downstream evidence matching:

```bash
python -m pdf_extractor.pdf_extractor                 # uses config/config.yaml
python -m pdf_extractor.pdf_extractor --config /path/to/config.yaml
```

See [`pdf_extractor/README.md`](pdf_extractor/README.md) for details.

### Run the test suite

```bash
python -m pytest -q
```

See [`tests/README.md`](tests/README.md) for the layout of the test
tree.

---

## Workflow

The end-to-end workflow per PDF, as orchestrated by
[`pipeline/`](pipeline/README.md):

1. **Branch extraction.** [`pdf_extractor.extraction.GROBID`](pdf_extractor/extraction/README.md)
   produces a TEI XML branch and
   [`pdf_extractor.extraction.PyMuPDF`](pdf_extractor/extraction/README.md)
   produces a native-text branch for the same PDF.
2. **Quality control.** [`quality_control.run_quality_control`](quality_control/README.md)
   evaluates the two branches, computes Tier 1 local heuristics,
   adjudicates a primary branch, and reconciles a `UnifiedRecord` that
   carries the canonical `exact_text` for the paper.
3. **Cache prewarm (optional).** A tiny call against the chunk model
   warms the OpenAI prompt cache for the shared `(system + PDF text)`
   prefix. If the synthesis model differs and `prewarm_synthesis_if_model_diff`
   is enabled, a separate synthesis-model warmup runs concurrently.
4. **Parallel extraction chunks.** Chunks `1..N-1` run in parallel
   against the chunk model, each scoped to a subset of fields by
   domain. See [`pipeline/extraction_map.py`](pipeline/README.md).
5. **Local validation.** Each chunk's structured-output JSON is
   validated locally against the expected `field_index` set
   (see [`pipeline/validator.py`](pipeline/README.md)).
6. **Synthesis chunk.** Chunk `N` runs against the synthesis model with
   the prior chunks' results passed as a trailing read-only context
   block.
7. **Persist.** Fields from all chunks are merged, sorted by
   `field_index`, written to `outputs/<paper>.extracted.json`, and the
   PDF is marked `complete` in `manifest.json`.
8. **Report.** After all PDFs are processed, a flagged-fields CSV is
   written to `outputs/qc_report.csv` for manual review.

```text
pdfs/paper.pdf
      |
      v
[GROBID + PyMuPDF branches]  ── quality_control ──► UnifiedRecord (exact_text)
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

PDFs themselves run concurrently up to `concurrency.pdf_processing`.
A global semaphore (`concurrency.global_api_limit`) caps total
concurrent OpenAI API calls across all active PDFs.

---

## Repository Structure

```text
EviTrace/
├── main.py                  Top-level entry point (CLI)
├── extraction_map.json      Field definitions: 62 fields across 13 domain groups
├── requirements.txt         Runtime + test dependencies
├── config/                  YAML configuration (OpenAI, QC, paths, logging)
├── agents/                  External-agent integrations (currently OpenAI only)
│   └── openai/              Async OpenAI Responses API client + prompt builders
├── pipeline/                End-to-end orchestrator: chunks, validation, manifest, QC report
├── pdf_extractor/           Standalone PDF text extraction module
│   ├── extraction/          Multi-backend extraction cascade (PyMuPDF, pdfplumber, OCR, GROBID)
│   ├── processing/          Sentence segmentation and full-text assembly
│   └── utils/               Text, embedding, and layout helpers
├── quality_control/         Generic branch-adjudication QC pipeline
├── utils/                   Repo-wide shared helpers (config, logging, paths)
└── tests/                   Pytest suite (pdf_extractor + quality_control)
```

| Directory | Purpose | Documentation |
| --------- | ------- | ------------- |
| `agents/` | External agent integrations | [agents/README.md](agents/README.md) |
| `agents/openai/` | OpenAI Responses API client and prompt builders | [agents/openai/README.md](agents/openai/README.md) |
| `config/` | YAML configuration files | [config/README.md](config/README.md) |
| `pipeline/` | End-to-end chunked extraction orchestrator | [pipeline/README.md](pipeline/README.md) |
| `pdf_extractor/` | Standalone PDF text extraction module | [pdf_extractor/README.md](pdf_extractor/README.md) |
| `pdf_extractor/extraction/` | Backend-specific PDF text extractors | [pdf_extractor/extraction/README.md](pdf_extractor/extraction/README.md) |
| `pdf_extractor/processing/` | Sentence segmentation and full-text assembly | [pdf_extractor/processing/README.md](pdf_extractor/processing/README.md) |
| `pdf_extractor/utils/` | Text, embedding, and layout utilities | [pdf_extractor/utils/README.md](pdf_extractor/utils/README.md) |
| `quality_control/` | Branch adjudication and reconciliation | [quality_control/README.md](quality_control/README.md) |
| `utils/` | Repo-wide config, logging, and path helpers | [utils/README.md](utils/README.md) |
| `tests/` | Test suite | [tests/README.md](tests/README.md) |

---

## Configuration

The pipeline reads a single YAML file at `config/config.yaml`
(see [config/README.md](config/README.md) for the full schema).
Environment variables override config values for a small set of
OpenAI-related keys, including:

| Variable                                     | Effect                                                |
| -------------------------------------------- | ----------------------------------------------------- |
| `OPENAI_API_KEY`                             | Required. Auth for the Responses API.                 |
| `OPENAI_BASE_URL`                            | Override the API base URL.                            |
| `OPENAI_CHUNK_MODEL`                         | Model used for chunks `1..N-1`.                       |
| `OPENAI_SYNTHESIS_MODEL`                     | Model used for the final synthesis chunk.             |
| `OPENAI_TEMPERATURE`                         | Force a specific temperature (omitted by default).    |
| `OPENAI_PROMPT_CACHE_KEY_PREFIX`             | Cache-key prefix; combined with a SHA-256 of `exact_text`. |
| `OPENAI_PROMPT_CACHE_RETENTION`              | E.g. `"24h"`, `"in_memory"`.                          |
| `OPENAI_ENABLE_CACHE_PREWARM`                | `0`/`false` to disable per-PDF warmup.                |
| `OPENAI_CACHE_WARMUP_MAX_TOKENS`             | Max output tokens for the warmup call.                |
| `OPENAI_PREWARM_SYNTHESIS_IF_MODEL_DIFF`     | Fire a synthesis-model warmup when models differ.     |
| `OPENAI_NUM_CHUNKS`                          | Override `extraction.num_chunks` from the YAML.       |

### Recommended model configuration

Maximum cache reuse is simplest when chunk and synthesis models match:

```bash
export OPENAI_CHUNK_MODEL="gpt-4.1"
export OPENAI_SYNTHESIS_MODEL="gpt-4.1"
export OPENAI_PROMPT_CACHE_RETENTION="24h"
```

For cheaper testing, the chunk model can be smaller than the synthesis
model — the pipeline will run a separate synthesis-model warmup during
the parallel chunks if the models differ:

```bash
export OPENAI_CHUNK_MODEL="gpt-4.1-mini"
export OPENAI_SYNTHESIS_MODEL="gpt-4.1"
```

### Cache notes

- The cached prefix is `system_prompt + shared paper text`. Filename,
  run ID, timestamp, chunk number, and attempt number are deliberately
  excluded so the prefix matches across calls for the same PDF.
- Warmup failure is non-fatal: the run continues and the per-chunk
  cache hit may simply be lower.
- Cached token counts (`cache_hit=...` in the log) are the source of
  truth.

### GPT-5.x note

Some GPT-5.x accounts/models reject the `temperature` parameter. The
client omits `temperature` unless `OPENAI_TEMPERATURE` is explicitly
set in the environment.

---

## Outputs

| File                              | Description                                                          |
| --------------------------------- | -------------------------------------------------------------------- |
| `outputs/<paper>.extracted.json`  | Per-paper extraction (one record per `field_index`).                 |
| `outputs/qc_report.csv`           | Cross-paper flagged rows: confidence `low` or `not reported`.        |
| `manifest.json`                   | Per-PDF status checkpoint; safe to re-run after a crash.             |
| `pipeline.log` (configurable)     | Per-run logs including token counts and cache-hit percentages.       |

Each extracted record looks like:

```json
{
  "field_index": 1,
  "domain_group": "1. Study identification",
  "field_name": "title",
  "extracted_value": "...",
  "evidence": "...",
  "confidence": "h"
}
```

`confidence` is one of `h` (direct), `m` (minor synthesis), `l`
(ambiguous/weak), or `nr` (not reported).

---

## Technologies Used

- **Python 3.10+**
- **OpenAI Responses API** (`openai>=1.0.0`) — chunked structured-output extraction with prompt caching
- **PyMuPDF** (`PyMuPDF>=1.24.0`) — primary native-text extractor
- **pdfplumber** (`pdfplumber>=0.10.0`) — table-aware fallback extractor
- **GROBID** — TEI XML extractor used as a QC branch (optional service)
- **Tesseract** (`pytesseract`, `pdf2image`) — OCR fallback (lazy import)
- **PaddleOCR** (`paddleocr`, `paddlepaddle`) — second OCR fallback (lazy import)
- **PyYAML** — configuration loading
- **gdown** — URL / Google Drive folder ingestion for the parser
- **NumPy** — numeric helpers in layout / embedding utilities
- **pytest** — test runner
- **Optional:** `sentence-transformers`, `faiss-cpu`/`faiss-gpu`, `torch`
  for the semantic-QC scaffold (Tier 3); never imported unless
  `quality_control.semantic_qc.enabled` is `true`.

---

## Project Status

Active research project. Core pipeline is functional end-to-end. The
following components are explicitly scaffolded but **not driving final
adjudication** today:

- Tier 3 semantic QC (embeddings + FAISS).
- Multi-agent adjudication beyond the GROBID/PyMuPDF branch pair.
- Optional `extraction_manifest.json` summarising every QC step
  (planned; see `pdf_extractor/next steps.txt`).

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
