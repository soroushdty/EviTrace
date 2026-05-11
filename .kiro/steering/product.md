---
inclusion: always
---

# EviTrace — Product Steering

## Purpose

EviTrace is an automated literature review pipeline for biomedical research PDFs. It ingests scientific papers and produces auditable, structured records with typed semantic, structural, and alignment layers, source-anchored evidence citations, and W3C JSON-LD annotation artifacts.

## Target Users

Clinical researchers, evidence synthesis teams, and biomedical informatics developers conducting scoping reviews, systematic reviews, or clinical evidence mapping workflows that require auditable extraction provenance across native and scanned PDFs.

## Problem Being Solved

Manual extraction of study attributes from scientific papers is error-prone, time-consuming, and hard to audit. EviTrace automates this using per-page PDF classification, complementary backend routing, domain-agnostic quality control reconciliation, and LLM-based structured extraction with source-anchored confidence scoring.

---

## Architecture Overview

```
main.py  (CLI entry point — asyncio.run)
  └── pipeline/orchestrator.py        # async run_pipeline(); PDF-level concurrency
        ├── pdf_extractor/             # multi-backend extraction (GROBID, PyMuPDF, pdfplumber, PaddleOCR)
        │     ├── extraction/          # one file per backend + scan_detector + schemas
        │     ├── processing/          # sentence segmentation + full-text assembly
        │     ├── utils/               # text_utils, embedding_utils, layout_utils
        │     └── annotation/          # W3C JSON-LD projection + artifact_generator
        ├── quality_control/           # generic 4-stage QC: rater → IAA → adjudicator → reconciler
        │     ├── models.py            # ALL shared dataclasses — import from here only
        │     └── concerns/            # injectable strategy objects
        ├── agents/openai/             # async LLM extraction (chunked + synthesis)
        │     ├── api_client.py        # retry, cache prewarm, structured-output calls
        │     └── prompts.py           # SYSTEM_PROMPT + cache-stable message builders
        ├── pipeline/evidence_index.py # GROBID TEI → ranked evidence bundle + local cache
        ├── pipeline/validator.py      # local JSON-Schema validation of LLM outputs
        └── utils/                     # config_utils, path_utils, logging_utils, text_processor, grobid_manager
```

All configuration lives in `config/config.yaml`. Runtime values are loaded once at module import via `utils/config_utils.py` and passed explicitly — never mutated as globals after startup.
---

## End-to-End Per-PDF Flow

1. **Per-page scan detection** — `scan_detector.classify_page()` runs a quick PyMuPDF pass over every page using five sequential criteria: (1) empty text short-circuit, (2) low word count, (3) low alpha-char ratio after `clean_ocr`, (4) zero embedded fonts, (5) image-area dominance. Each page is classified as `native`, `scanned`, or `mixed`. A page is native only when no stage fires.
2. **Backend routing by page class** — Pages are routed to complementary, non-competing backend paths based on their classification. Each backend has distinct authority and rules:
   - Native/digital pages → digital path: GROBID (semantic authority, TEI XML), pdfplumber (structural authority, text blocks), PyMuPDF (font metadata and comparison signals).
   - Scanned pages → OCR path: PaddleOCR (primary, bounding boxes + text), PyMuPDF built-in OCR (secondary cross-validation). Standalone Tesseract is never used.
3. **Quality Control pipeline** — `run_quality_control(branches, document_id, config)` evaluates all branches through local heuristics and exact-match search, and reconciles a `UnifiedRecord` carrying `exact_text` and W3C JSON-LD annotations. Local heuristics and Exact-match search always runs. An optional semantic search fallback is scaffolded only and disabled by default.
4. **Evidence index** — `build_or_load_evidence_bundle()` parses the GROBID TEI XML into a ranked, section-scored evidence index (sentences, tables, figure captions). Results are cached to disk keyed by `{paper_id}_{pdf_hash}`. When GROBID output is absent, the default behavior is to skip the PDF, log the failure explicitly, and record it in the manifest. A fallback to sentence-splitting `exact_text` is available but must be opted into via `quality_control.grobid_integration.failure_behavior: "fallback"` in `config.yaml`.
5. **Cache prewarm** — An optional tiny OpenAI call warms the shared `(system + evidence package)` prompt prefix. A separate synthesis-model warmup fires concurrently when models differ.
6. **Parallel extraction chunks** — Chunks `1..N-1` run concurrently, each receiving a per-chunk evidence package built from the ranked index (section-aware scoring, keyword overlap, char/item budget limits). Fields 1–2 (author+year, publication year) are pre-filled locally from TEI metadata and never sent to the LLM.
7. **Local validation** — Each chunk's JSON is validated by `validate_chunk_output()` against the exact expected `field_index` set, key schema, confidence enum, and `loc` ID membership.
8. **Synthesis chunk** — The final chunk runs with prior chunk results as read-only context. Produces synthesised/critique fields.
9. **Persist** — Fields are merged, sorted by `field_index`, written to `outputs/<paper>.extracted.json`, and the manifest is marked `complete`. Table/figure crops are optionally saved alongside.
10. **Quality Control report** — `outputs/qc_report.csv` flags low-confidence and not-reported fields across all PDFs.

---

## Core Capabilities

**Evidence Grounding** — Every extracted field carries a source-text citation via `loc` IDs (referencing the evidence index), a confidence tier (`h`/`m`/`l`/`nr`), and full `location_metadata` (type, section_path, page, coords, xpath, source_pdf). Alignment records link semantic content to structural page evidence with agreement levels, edit-distance signals, and concern-strategy-selected preferred readings.

**Multi-Backend PDF Extraction** — Per-page scan detection routes each page to complementary, non-competing backends:
- GROBID → semantic structure (TEI XML); primary evidence source
- pdfplumber → native structural text blocks
- PyMuPDF → font metadata and scanned-path comparison signals
- PaddleOCR → scanned-page OCR text and bounding boxes

**Per-Page Scan Detection** — `scan_detector.classify_page()` is a stateless pure function running five stages in order: (1) empty text short-circuit, (2) low word count, (3) low alpha-char ratio after `clean_ocr`, (4) zero embedded fonts, (5) image-area dominance. A page is native only when no stage fires. Thresholds are configurable under `quality_control.scan_detection`.

**Token-Efficient LLM Extraction** — Only the ranked evidence package is sent to the API (no bibliography, no raw PDF text). LLM outputs use compact keys (`i`, `v`, `loc`, `c`) to minimize tokens while enabling lossless local reconstruction. The LLM acts as a reasoning layer only; all validation and reconstruction happen locally via `pipeline/validator.py`.

**Evidence Index** — `pipeline/evidence_index.py` builds a structured, section-scored index from GROBID TEI XML. Each item carries `id`, `type` (sentence/table/figure_caption), `section_path`, `page`, `coords`, `xpath`, `text`, and `score`. Scores are boosted for abstract/methods/results and penalised for references/acknowledgements. Optional addon enrichment (GROBID Quantities, Datastet, Entity Fishing) annotates items with quantities, datasets, and entities.

**Chunked LLM Extraction** — Domain-scoped parallel extraction chunks, with a final synthesis chunk that receives prior chunk results as context. Structured outputs are validated locally against field indexes via `pipeline/validator.py`. The `loc` field in each extraction item must contain only IDs present in the evidence bundle.

**Idempotent Checkpointing** — `manifest.json` tracks per-PDF processing status (`complete`, `failed_qc_pipeline`, `failed_chunks`, `failed_chunk_<n>`). Partial runs resume safely after interruptions.

**Quality Control Reporting** — Concern-strategy-injectable reconciliation and adjudication produce domain-agnostic QC outputs: low-confidence/missing-field signals, normalized text-comparison signals, and W3C JSON-LD annotations projected from the typed semantic, structural, and alignment layers.

**GROBID Lifecycle Management** — `utils/grobid_manager.py` (`GrobidServerManager`) is a context manager that auto-starts a GROBID Docker container when `quality_control.grobid.auto_start` is true, polls `/api/isalive`, and stops the container on exit. Used as `with GrobidServerManager(local_cfg):` in `main.py`.

---

## Key Design Principles

**Independently swappable stages** — Each pipeline stage (extraction, QC, LLM agent) can be used standalone. PDF-specific behavior is isolated to extraction and QC closures; the core pipeline runner is domain-agnostic and configuration-driven.

**OOP extensibility** — Users subclass abstract base classes to inject custom behavior:
- `QualityMetrics` → custom per-branch quality checks
- `InterRaterMetrics` → custom inter-rater agreement computation
- `AdjudicationRules` → custom adjudication logic
- `TextProcessor` / `SentenceSegment` → custom text processing backends (loaded via fully-qualified class path in config)
- Concern strategy objects (`TextFidelityConcern`, `SectionVerificationConcern`, `TableFigureMergeConcern`) → custom reconciliation strategies

**Generic QC pipeline** — `run_pipeline()` in `quality_control/quality_control.py` accepts injectable stage callables (`rater_fn`, `iaa_fn`, `adjudicator_fn`, `reconciler_fn`). It is not PDF-specific and can adjudicate between any set of branch outputs (LLM agents, extractors, etc.).

**No global mutation** — Config values are loaded once and passed explicitly as arguments. CLI overrides are forwarded as parameters to `run_pipeline()`, not written back to module-level constants.

**Prompt cache stability** — The shared user-message prefix (`_shared_paper_prefix`) must remain byte-identical across warmup, extraction chunks, and synthesis for the same PDF. Never inject PDF names, timestamps, chunk numbers, or run IDs into the shared prefix. Variable material goes after the prefix only.

**Concern strategies are asymmetric** — `TextFidelityConcern.reconcile(primary, reference, ...)` always treats `reference` as the preferred reading. Callers that need the opposite asymmetry swap argument order. `DEFAULT_TEXT_FIDELITY` uses `source_label="pdfplumber"`.

---

## Code Style Conventions

- Python 3.10+; use `dataclasses` for all shared models; prefer `from __future__ import annotations` for forward references.
- Async I/O via `asyncio`; blocking work (PDF extraction, QC) runs in `asyncio.to_thread`.
- All logging via `utils/logging_utils.py` (`get_logger(__name__)`); never use `print` in library code.
- Config values are loaded once at startup via `utils/config_utils.py` and passed explicitly as arguments; `config.yaml` is the single source of truth and `config_utils.py` is the sole reader of it.
- File paths resolved through `utils/path_utils.py`; never hardcode paths.
- LLM output schema uses compact keys: `i` (field index), `v` (value), `loc` (list of evidence IDs), `c` (confidence tier: `h`/`m`/`l`/`nr`).
- Retry logic uses exponential backoff: `delay = base_delay * 2^(attempt - 1)`.
- Semaphores gate both PDF-level (`pdf_semaphore`) and API-level (`api_semaphore`) concurrency.
- Heavy optional dependencies (`sentence-transformers`, `faiss`, `torch`, `paddleocr`) are imported lazily inside function bodies — never at module level. Tesseract is invoked only through PyMuPDF's built-in OCR interface, never as a standalone dependency.
- New top-level YAML keys must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `utils/config_utils.py` or `load_local_config` will raise `ValueError`.

---

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `pipeline/orchestrator.py` | Top-level async runner; wires QC context into LLM extraction |
| `pipeline/pdf_processor.py` | Per-PDF LLM extraction orchestration (evidence bundle → chunks → synthesis → persist) |
| `pipeline/evidence_index.py` | GROBID TEI → ranked evidence bundle; disk cache; addon enrichment |
| `pipeline/manifest.py` | Idempotent checkpoint read/write |
| `pipeline/validator.py` | Local JSON-Schema validation of LLM chunk outputs; `reconstruct_fields` |
| `pipeline/extraction_map.py` | Load `extraction_map.json`; group fields by chunk; build field lookup |
| `pdf_extractor/extraction/` | One file per backend (GROBID, PyMuPDF, pdfplumber, PaddleOCR); `scan_detector`; `schemas` |
| `pdf_extractor/annotation/` | W3C JSON-LD projection (`w3c_annotation.py`) and serialization (`artifact_generator.py`) |
| `quality_control/models.py` | ALL shared dataclasses — always import from here, not from individual modules |
| `quality_control/quality_control.py` | Generic `run_pipeline()` + PDF-specific `run_quality_control()` |
| `quality_control/concerns/` | Injectable strategy objects: `TextFidelityConcern`, `SectionVerificationConcern`, `TableFigureMergeConcern` |
| `quality_control/local_metrics.py` | Tier 1 `LocalQCReport` heuristics (8 metrics, all threshold-driven) |
| `agents/openai/api_client.py` | Async OpenAI Responses API client; retry; cache prewarm; `extract_chunk` |
| `agents/openai/prompts.py` | `SYSTEM_PROMPT`; cache-stable `_shared_paper_prefix`; message builders |
| `utils/config_utils.py` | `load_openai_config`, `load_qc_config`, `load_local_config`; QC defaults deep-merge |
| `utils/path_utils.py` | `PROJECT_ROOT`, `PDF_DIR`, `OUTPUT_DIR`, `EXTRACTION_MAP`, `MANIFEST_FILE`; PDF source resolution |
| `utils/logging_utils.py` | Shared `evi_trace` root logger; `setup_logging`; `log_cache_usage` |
| `utils/text_processor.py` | `TextProcessor` hub; `SentenceSegment` ABC + 5 concrete backends (scispaCy default) |
| `utils/grobid_manager.py` | `GrobidServerManager` context manager; Docker lifecycle; `/api/isalive` polling |

---

## Data Models (quality_control/models.py)

All pipeline stages communicate through a single `QCBundle` instance mutated in place.

| Class | Role |
|---|---|
| `Candidate` | One contributor's output entering QC: `source`, `index` (int), `payload`, `status`. `.extractor` and `.agent` are aliases for `source`. |
| `QualityMetrics` | ABC for per-branch quality checks. Subclass and override `passes_check()`. |
| `QualityReport` | Default concrete report; unconditionally passes. |
| `InterRaterMetrics` | ABC for IAA computation. Subclass and override `compute(reports)`. |
| `InterRaterReport` | Default: pairwise pass/fail agreement scores. |
| `AdjudicationRules` | ABC for adjudication. Subclass and override `adjudicate(reports, metrics)`. |
| `AdjudicationDecision` | Default: elects extractor with most passing branches. |
| `SemanticLayer` | Typed semantic layer: `metadata`, `sections`, `paragraphs`, `sentences`, `references`. |
| `StructuralLayer` | Typed structural layer: `pages`, `blocks`, `tables`, `figures`. |
| `AlignmentRecord` | One semantic-to-structural alignment: `source`, `ocr_derived`, `agreement`, `edit_distance`, `preferred_reading`, `confidence`. |
| `DocumentAlignment` | Container: `paragraph_to_blocks`, `sentence_to_char_range`, `section_header_to_block`, `reconciliation_flags`. |
| `UnifiedRecord` | Final reconciled output: `document_id`, `content` (dict), `semantic`, `structural`, `alignment`. |
| `LocalQCMetricRecord` | One Tier 1 metric result: `metric_name`, `computed_value`, `threshold`, `triggered`. |
| `QCBundle` | Full run state: `branches`, `reports`, `iaa_metrics`, `decision`, `unified`, `metrics_hierarchy`. |

---

## Extraction Map

`extraction_map.json` defines 62 fields across 13 domain groups. Each entry has:
- `field_index` (int, 1–62) — canonical field identifier used in all LLM I/O
- `domain_group` — e.g. `"1. Study identification"`, `"2. Clinical and study-design context"`
- `field_name`, `definition`, `reviewer_question`, `format`, `categories_or_examples`

Fields 1–2 (first author+year, publication year) are pre-filled locally from GROBID TEI metadata and excluded from LLM chunks. To add or rename fields, edit `extraction_map.json` — this is the canonical source of truth for all field schemas.

---

## W3C Annotation Pipeline

`pdf_extractor/annotation/` is the sole producer of W3C JSON-LD annotation artifacts.

1. `w3c_annotation.project(unified)` — reads only `unified.semantic` and `unified.alignment`; never reads raw extractor output. Returns `list[AnnotationRecord]`.
2. `annotation/artifact_generator.generate_w3c_jsonld(records)` — serializes records to W3C JSON-LD dicts. Each dict has `@context`, `id` (UUID URN), `type`, `body`, `target`.
   - Native pages → `TextPositionSelector` (char offsets) + `TextQuoteSelector`
   - OCR/scanned pages → `FragmentSelector` (page + xywh bbox) + `TextQuoteSelector`

Never build W3C annotation dicts outside `annotation/artifact_generator.py`.

---

## QC Metrics Hierarchy

Three tiers run per document, independent of the extractor hierarchy:

| Tier | What runs | When |
|---|---|---|
| **Tier 1** | `LocalQCReport` — 8 heuristic checks (chars/page, GROBID/native ratio, long-sentence fraction, section coverage, caption coverage, coordinate availability, references-in-body, weird-char ratio) | Always |
| **Tier 2** | `exact_match_search` — two-pass normalised substring search across candidate branches | When 1–2 Tier 1 metrics triggered (borderline branch) |
| **Tier 3** | FAISS semantic search | Scaffolded only; `semantic_qc.enabled: false` by default; not wired into adjudication |

Results stored in `ctx.metrics_hierarchy = {"tier1": [...], "tier2": [...], "tier3": [...]}`.

---

## Concern Strategies

Injectable strategy objects in `quality_control/concerns/`. Import defaults from `quality_control.concerns`:

| Strategy | Default instance | Purpose |
|---|---|---|
| `TextFidelityConcern` | `DEFAULT_TEXT_FIDELITY` (source_label="pdfplumber") | Asymmetric text comparison; `reference` arg is always preferred reading |
| `SectionVerificationConcern` | `DEFAULT_SECTION_VERIFICATION` | Heading confidence with font-size penalty |
| `TableFigureMergeConcern` | `DEFAULT_TABLE_FIGURE_MERGE` (primary="grobid", reference="pdfplumber") | Merge caption + spatial record; raises `MissingContributionError` when either side is None |

---

## TextProcessor and Sentence Segmentation

`utils/text_processor.py` provides a config-driven text transformation hub.

- `TextProcessor` — normalise, compare (Levenshtein ratio), clean_ocr, tokenize_words, extract_keywords, tokenize_sentences (delegates to segmenter).
- `SentenceSegment` — ABC; subclasses ARE the segmenter and also inherit the full `TextProcessor` interface.
- Built-in backends: `scispacy` (default, `en_core_sci_sm`), `wtpsplit`, `nltk_punkt`, `spacy_sentencizer`, `stanza`.
- Custom backends: set `text_processor.class` in config to a fully-qualified class path; loaded via `importlib`.
- All heavy NLP models are lazy-loaded on first `tokenize_sentences()` call.

---

## Configuration Reference

All tunable parameters live in `config/config.yaml`. Key sections:

| Section | Key settings |
|---|---|
| `openai` | `api_key`, `chunk_model`, `synthesis_model`, `temperature` (null to omit), `prompt_cache.*` |
| `extraction` | `num_chunks` (3 or 5 supported natively), `max_evidence_items_per_chunk`, `max_evidence_chars_per_chunk`, `evidence_cache_dir` |
| `concurrency` | `pdf_processing` (PDF parallelism), `global_api_limit` (API call cap) |
| `retry` | `max_retries`, `base_delay_seconds` (exponential: `base * 2^(attempt-1)`) |
| `quality_control.grobid` | `auto_start`, `docker_image`, `url`, `timeout`, `tei_coordinates`, `max_retries` |
| `quality_control.grobid_integration` | `failure_behavior` (`"manifest_fail"` or `"fallback"`), `crop_figures`, `crop_tables` |
| `quality_control.scan_detection` | `text_density_threshold`, `alpha_ratio_threshold`, `image_dominance_threshold` |
| `quality_control.local_metrics` | 8 Tier 1 thresholds |
| `quality_control.semantic_qc` | `enabled` (false by default), `model_name`, `similarity_threshold` |
| `quality_control.addons` | `grobid_quantities`, `datastet`, `entity_fishing` (all disabled by default) |
| `text_processor` | `class`, `sentence_tokenizer.backend`, `word_tokenizer.backend`, `normalizer.backend`, `comparison.threshold` |
| `pdfs_path` | Folder, single PDF, or URL (including Google Drive folder URLs) |
| `log_file`, `log_level` | Log file path and console level |

Environment variables override YAML for all `openai.*` keys (e.g. `OPENAI_API_KEY`, `OPENAI_CHUNK_MODEL`). Override rule: **env > yaml > default**.

---

## Testing Conventions

- Run from repo root: `python -m pytest -q` (slow tests skipped by default).
- Include slow tests: `python -m pytest -q -m ""` or `python -m pytest -q -m slow`.
- Slow tests carry `pytestmark = pytest.mark.slow` — applied to extraction-tier and embedding tests.
- Heavy optional dependencies are mocked in tests; never require `paddleocr`, `faiss`, `torch`, etc. to be installed for the test suite to pass.
- Property-based tests use Hypothesis (`@given`, `@settings`).
- Test files follow `test_<module-or-feature>_<aspect>.py`.
- No dedicated tests for `agents/openai` or `pipeline/orchestrator` — these rely on integration exercise via parser/QC tests plus manual end-to-end runs.
- Steering-drift regression tests (`test_steering_drift_*.py`) verify that specific bug conditions are preserved and fixed.

---

## Outputs

| File | Description |
|---|---|
| `outputs/<paper>.extracted.json` | Per-paper extraction: one record per `field_index` with `field_name`, `domain_group`, `extracted_value`, `evidence`, `location`, `location_metadata`, `confidence` |
| `outputs/qc_report.csv` | Cross-paper flagged rows: confidence `l` or `nr` |
| `outputs/evidence_cache/<id>.evidence.json` | Cached evidence index per PDF (keyed by paper_id + pdf_hash) |
| `outputs/evidence_cache/<id>.tei.xml` | Cached GROBID TEI XML |
| `manifest.json` | Per-PDF status checkpoint |
| `run.log` (configurable) | Per-run logs including token counts and cache-hit percentages |
