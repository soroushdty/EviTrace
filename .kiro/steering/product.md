---
inclusion: always
---

# EviTrace — Product Steering

## Purpose

EviTrace is an automated literature review pipeline for biomedical research PDFs. It ingests scientific papers and produces auditable, structured records with typed semantic, structural, and alignment layers, source-anchored evidence citations, and W3C JSON-LD annotation artifacts.

Target users are clinical researchers, evidence synthesis teams, and biomedical informatics developers conducting scoping or systematic reviews that require auditable extraction provenance across native and scanned PDFs.

---

## Architecture

```
main.py  (CLI entry — asyncio.run)
  └── pipeline/orchestrator.py          # async run_pipeline(); PDF-level concurrency
        ├── pipeline/extraction_pipeline.py  # build_qc_bundle(); single source of truth for
        │                                    # scan detection → backend routing → QC → annotation
        ├── pdf_extractor/               # multi-backend extraction
        │     ├── extraction/            # one file per backend + scan_detector + schemas
        │     │     ├── GROBID.py        # semantic authority (TEI XML)
        │     │     ├── PyMuPDF.py       # font metadata / scanned-path cross-validation
        │     │     ├── pdfplumber.py    # structural text blocks
        │     │     ├── PaddleOCR.py     # scanned-page OCR (primary)
        │     │     ├── scan_detector.py # stateless per-page classifier
        │     │     └── schemas.py       # shared extraction schemas
        │     ├── processing/            # sentence_processor.py — sentence segmentation + full-text assembly
        │     ├── utils/                 # text_utils.py, embedding_utils.py (no layout_utils here)
        │     ├── annotation/            # w3c_annotation.py + artifact_generator.py
        │     ├── pdf_extractor.py       # standalone CLI (no OpenAI key required)
        │     └── pdf_validator.py       # PDF-level structural validation
        ├── quality_control/             # generic 4-stage QC: rater → IAA → adjudicator → reconciler
        │     ├── models.py              # ALL shared dataclasses — import from here only
        │     ├── concerns/              # injectable strategy objects
        │     ├── defaults/              # QualityReport, InterRaterReport, AdjudicationDecision
        │     ├── validator.py           # generic Validator + ValidationResult (injectable, schema-driven)
        │     ├── structure_validator.py # StructureSchemaValidator — sole reader of configs/structure_schema.json
        │     └── validate_context.py    # validate_qc_context_input() — QC-to-LLM handoff guard
        ├── agents/
        │     ├── openai/                # api_client.py, prompts.py
        │     └── validator.py           # AgentSchemaValidator — sole reader of configs/agent_schema.json
        ├── pipeline/evidence_index.py   # GROBID TEI → ranked evidence bundle + local cache
        ├── pipeline/validator.py        # local JSON-Schema validation of LLM chunk outputs
        ├── pipeline/extraction_report.py # generate_qc_report(); writes qc_report.csv + stdout summary
        └── utils/                       # config_utils, path_utils, logging_utils, text_processor, grobid_manager
```

Configuration lives in `configs/config.yaml` (note: `configs/`, not `config/`). Loaded once at startup via `utils/config_utils.py` and passed explicitly — never mutated as globals after startup.

---

## Per-PDF Processing Flow

1. **Scan detection** — `scan_detector.classify_page()` is a stateless pure function running five sequential stages: (1) empty text short-circuit, (2) low word count, (3) low alpha-char ratio after `clean_ocr`, (4) zero embedded fonts, (5) image-area dominance. A page is `native` only when no stage fires; otherwise `scanned` or `mixed`. Thresholds are configurable under `quality_control.scan_detection`.
2. **Backend routing** — Backends are complementary and non-competing. Routing is centralised in `pipeline/extraction_pipeline.py::build_qc_bundle()`:
   - All pages native → GROBID (semantic authority, TEI XML) + pdfplumber (structural text blocks); PyMuPDF font metadata stored in `ctx.unified.content`.
   - Any page scanned + `ocr=true` → PaddleOCR (primary, bounding boxes + text) + PyMuPDF built-in OCR (secondary cross-validation). Standalone Tesseract is **never** used.
   - Any page scanned + `ocr=false` → skip extraction, log WARNING, no branch created.
3. **Quality Control** — `run_quality_control(branches, document_id, config)` evaluates branches through local heuristics and exact-match search, reconciling a `UnifiedRecord` with `exact_text` and W3C JSON-LD annotations. Semantic search is scaffolded but disabled by default.
4. **QC-to-LLM handoff guard** — `validate_qc_context_input(ctx)` in `quality_control/validate_context.py` performs five pre-flight checks on the `QCBundle` (type, unified not None, non-empty document_id, content is dict, non-empty exact_text) plus a structural schema check before field extraction begins.
5. **Evidence index** — `build_or_load_evidence_bundle()` parses GROBID TEI XML into a ranked, section-scored index (sentences, tables, figure captions), cached to disk by `{paper_id}_{pdf_hash}`. When GROBID is absent, the default is to skip the PDF and record failure in the manifest. Fallback to sentence-splitting `exact_text` requires opting in via `quality_control.grobid_integration.failure_behavior: "fallback"`.
6. **Cache prewarm** — Optional tiny OpenAI call warms the shared `(system + evidence package)` prompt prefix. Synthesis-model warmup fires concurrently when models differ.
7. **Parallel extraction chunks** — Chunks `1..N-1` run concurrently with per-chunk evidence packages (section-aware scoring, keyword overlap, char/item budget limits). Fields 1–2 (author+year, publication year) are pre-filled locally from TEI metadata and never sent to the LLM.
8. **Local validation** — `validate_chunk_output()` checks each chunk's JSON against the expected `field_index` set, key schema, confidence enum, and `loc` ID membership.
9. **Synthesis chunk** — Final chunk runs with prior chunk results as read-only context.
10. **Persist** — Fields merged, sorted by `field_index`, written to `outputs/<paper>.extracted.json`; manifest marked `complete`.
11. **QC report** — `pipeline/extraction_report.py::generate_qc_report()` writes `outputs/qc_report.csv` flagging low-confidence and not-reported fields, and prints a summary to stdout.

---

## Key Design Principles

**No global mutation** — Config loaded once, passed explicitly. CLI overrides forwarded as parameters to `run_pipeline()`, never written back to module-level constants.

**Independently swappable stages** — Each stage (extraction, QC, LLM agent) can be used standalone. `pipeline/extraction_pipeline.py::build_qc_bundle()` is the single source of truth for the extraction flow; both `main.py` and the standalone `pdf_extractor.py` CLI delegate to it.

**Dependency direction rule** — `quality_control` must not import from `agents`, `pipeline`, or `pdf_extractor`. `validate_qc_context_input` was placed in `quality_control/validate_context.py` specifically to respect this boundary.

**OOP extensibility via ABCs** — Subclass to inject custom behavior:
- `QualityMetrics` → per-branch quality checks (`passes_check()`)
- `InterRaterMetrics` → IAA computation (`compute(reports)`)
- `AdjudicationRules` → adjudication logic (`adjudicate(reports, metrics)`)
- `TextProcessor` / `SentenceSegment` → text processing backends (loaded via fully-qualified class path in config)
- Concern strategies → `TextFidelityConcern`, `SectionVerificationConcern`, `TableFigureMergeConcern`

**Schema validators are singletons with a single owner** — Each JSON schema file has exactly one reader:
- `configs/agent_schema.json` → `agents/validator.py::AgentSchemaValidator`
- `configs/structure_schema.json` → `quality_control/structure_validator.py::StructureSchemaValidator`

**Prompt cache stability** — `_shared_paper_prefix` must be byte-identical across warmup, extraction chunks, and synthesis for the same PDF. Never inject PDF names, timestamps, chunk numbers, or run IDs into the shared prefix. Variable material goes after the prefix only.

**Concern strategies are asymmetric** — `TextFidelityConcern.reconcile(primary, reference, ...)` always treats `reference` as the preferred reading. Swap argument order to invert. `DEFAULT_TEXT_FIDELITY` uses `source_label="pdfplumber"`.

**W3C annotations are produced in one place** — `pdf_extractor/annotation/` is the sole producer. `w3c_annotation.project(unified)` reads only `unified.semantic` and `unified.alignment`. Never build W3C annotation dicts outside `annotation/artifact_generator.py`.

---

## Code Style Conventions

- Python 3.10+; `dataclasses` for all shared models; `from __future__ import annotations` for forward references.
- Async I/O via `asyncio`; blocking work (PDF extraction, QC) runs in `asyncio.to_thread`.
- All logging via `utils/logging_utils.py` (`get_logger(__name__)`); **never use `print`** in library code.
- Config lives in `configs/config.yaml`; loaded via `utils/config_utils.py`; never hardcode the path — use `utils/path_utils.py` helpers.
- File paths resolved through `utils/path_utils.py`; never hardcode paths.
- LLM output schema uses compact keys: `i` (field index), `v` (value), `loc` (list of evidence IDs), `c` (confidence: `h`/`m`/`l`/`nr`).
- Retry uses exponential backoff: `delay = base_delay * 2^(attempt - 1)`.
- Semaphores gate PDF-level (`pdf_semaphore`) and API-level (`api_semaphore`) concurrency.
- Heavy optional dependencies (`sentence-transformers`, `faiss`, `torch`, `paddleocr`) are imported **lazily inside function bodies** — never at module level.
- New top-level YAML keys must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `utils/config_utils.py` or `load_local_config` raises `ValueError`.
- All shared dataclasses live in `quality_control/models.py` — always import from there, never from individual QC submodules.

---

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `pipeline/orchestrator.py` | Top-level async runner; wires QC context into LLM extraction |
| `pipeline/extraction_pipeline.py` | `build_qc_bundle()` — single source of truth for scan detection → backend routing → QC → annotation for one PDF |
| `pipeline/pdf_processor.py` | Per-PDF LLM extraction orchestration (evidence bundle → chunks → synthesis → persist) |
| `pipeline/evidence_index.py` | GROBID TEI → ranked evidence bundle; disk cache; addon enrichment |
| `pipeline/manifest.py` | Idempotent checkpoint read/write |
| `pipeline/validator.py` | Local JSON-Schema validation of LLM chunk outputs; `reconstruct_fields` |
| `pipeline/extraction_map.py` | Load `extraction_map.json`; group fields by chunk; build field lookup |
| `pipeline/extraction_report.py` | `generate_qc_report()` — writes `qc_report.csv` and prints pipeline summary |
| `pdf_extractor/pdf_extractor.py` | Standalone CLI; runs full extraction pipeline without OpenAI key |
| `pdf_extractor/pdf_validator.py` | PDF-level structural validation |
| `pdf_extractor/extraction/` | One file per backend (GROBID, PyMuPDF, pdfplumber, PaddleOCR); `scan_detector`; `schemas` |
| `pdf_extractor/annotation/` | W3C JSON-LD projection (`w3c_annotation.py`) and serialization (`artifact_generator.py`) |
| `pdf_extractor/processing/sentence_processor.py` | Sentence segmentation and full-text assembly |
| `pdf_extractor/utils/text_utils.py` | `exact_match_search`, `semantic_search`, text normalization helpers |
| `pdf_extractor/utils/embedding_utils.py` | Embedding model loading, `embed_query`, `l2_normalise`, `build_faiss_index`, `build_sentence_store` |
| `quality_control/models.py` | ALL shared dataclasses — always import from here |
| `quality_control/quality_control.py` | Generic `run_pipeline()` + PDF-specific `run_quality_control()` |
| `quality_control/concerns/` | Injectable strategies: `TextFidelityConcern`, `SectionVerificationConcern`, `TableFigureMergeConcern` |
| `quality_control/defaults/` | Default concrete impls: `QualityReport`, `InterRaterReport`, `AdjudicationDecision` |
| `quality_control/local_metrics.py` | `LocalQCReport` — 8 heuristic checks, all threshold-driven |
| `quality_control/validator.py` | Generic `Validator` + `ValidationResult` — injectable, schema-driven, domain-agnostic |
| `quality_control/structure_validator.py` | `StructureSchemaValidator` — sole reader of `configs/structure_schema.json` |
| `quality_control/validate_context.py` | `validate_qc_context_input()` — QC-to-LLM handoff guard; `ValidationError` |
| `agents/openai/api_client.py` | Async OpenAI Responses API client; retry; cache prewarm; `extract_chunk` |
| `agents/openai/prompts.py` | `SYSTEM_PROMPT`; cache-stable `_shared_paper_prefix`; message builders |
| `agents/validator.py` | `AgentSchemaValidator` — sole reader of `configs/agent_schema.json` |
| `utils/config_utils.py` | `load_openai_config`, `load_qc_config`, `load_local_config`; QC defaults deep-merge |
| `utils/path_utils.py` | `PROJECT_ROOT`, `PDF_DIR`, `OUTPUT_DIR`, `EXTRACTION_MAP`, `MANIFEST_FILE`, `QC_REPORT_FILE`; PDF source resolution |
| `utils/logging_utils.py` | Shared `evi_trace` root logger; `setup_logging`; `log_cache_usage` |
| `utils/text_processor.py` | `TextProcessor` hub; `SentenceSegment` ABC + 5 concrete backends (scispaCy default) |
| `utils/grobid_manager.py` | `GrobidServerManager` context manager; Docker lifecycle; `/api/isalive` polling |

---

## Data Models (`quality_control/models.py`)

All pipeline stages communicate through a single `QCBundle` instance mutated in place.

| Class | Role |
|---|---|
| `Candidate` | One contributor's output: `source`, `index` (int), `payload`, `status`. `.extractor` and `.agent` alias `source`. |
| `QualityMetrics` | ABC — override `passes_check()` |
| `InterRaterMetrics` | ABC — override `compute(reports)` |
| `AdjudicationRules` | ABC — override `adjudicate(reports, metrics)`; `primary_agent` property |
| `SemanticLayer` | `metadata`, `sections`, `paragraphs`, `sentences`, `references` |
| `StructuralLayer` | `pages`, `blocks`, `tables`, `figures` |
| `AlignmentRecord` | `source`, `ocr_derived`, `agreement`, `edit_distance`, `preferred_reading`, `confidence` |
| `DocumentAlignment` | `paragraph_to_blocks`, `sentence_to_char_range`, `section_header_to_block`, `reconciliation_flags` |
| `UnifiedRecord` | Final output: `document_id`, `content`, `semantic`, `structural`, `alignment` |
| `LocalQCMetricRecord` | `metric_name`, `computed_value`, `threshold`, `triggered` |
| `QCBundle` | Full run state: `branches`, `reports`, `iaa_metrics`, `decision`, `unified`, `metrics_hierarchy` |

Default concrete implementations live in `quality_control/defaults/` and are exported from `quality_control.defaults`: `QualityReport`, `InterRaterReport`, `AdjudicationDecision`.

---

## Validation Layer

Three distinct validators serve different concerns:

| Validator | Location | Scope |
|---|---|---|
| `Validator` + `ValidationResult` | `quality_control/validator.py` | Generic, injectable, schema-driven — knows nothing about PDFs or agents |
| `StructureSchemaValidator` | `quality_control/structure_validator.py` | Validates `Candidate`, `QCBundle`, `PdfProcessorOutput`, `ExtractionMap`, `ChunkOutput` against `configs/structure_schema.json` |
| `AgentSchemaValidator` | `agents/validator.py` | Loads `configs/agent_schema.json`; exposes `get_system_prompt()`, `get_policies()`, `get_extraction_rules()` |
| `validate_qc_context_input` | `quality_control/validate_context.py` | Pre-flight guard before LLM extraction; raises `ValidationError` on failure |

---

## QC Metrics Hierarchy

| Tier | What runs | When |
|---|---|---|
| **Tier 1** | `LocalQCReport` — 8 heuristic checks (chars/page, GROBID/native ratio, long-sentence fraction, section coverage, caption coverage, coordinate availability, references-in-body, weird-char ratio) | Always |
| **Tier 2** | `exact_match_search` — two-pass normalised substring search across candidate branches | 1–2 Tier 1 metrics triggered |
| **Tier 3** | FAISS semantic search | Scaffolded only; disabled by default; not wired into adjudication |

Results stored in `ctx.metrics_hierarchy = {"tier1": [...], "tier2": [...], "tier3": [...]}`.

---

## Config and Schema Files (`configs/`)

| File | Purpose |
|---|---|
| `configs/config.yaml` | All runtime configuration — single source of truth |
| `configs/extraction_map.json` | 62 extraction fields across 13 domain groups — canonical field schema |
| `configs/agent_schema.json` | LLM agent system prompt, policies, extraction rules — read only by `AgentSchemaValidator` |
| `configs/structure_schema.json` | JSON Schema (Draft 7) for pipeline dataclasses — read only by `StructureSchemaValidator` |

`extraction_map.json` defines each field with: `field_index` (int, 1–62), `domain_group`, `field_name`, `definition`, `reviewer_question`, `format`, `categories_or_examples`. Fields 1–2 are pre-filled locally from GROBID TEI metadata and excluded from LLM chunks.

Config quick reference:

| Section | Key settings |
|---|---|
| `openai` | `api_key`, `chunk_model`, `synthesis_model`, `temperature` (null to omit), `prompt_cache.*` |
| `extraction` | `num_chunks` (3 or 5), `max_evidence_items_per_chunk`, `max_evidence_chars_per_chunk`, `evidence_cache_dir` |
| `concurrency` | `pdf_processing`, `global_api_limit` |
| `retry` | `max_retries`, `base_delay_seconds` |
| `quality_control.grobid` | `auto_start`, `docker_image`, `url`, `timeout`, `tei_coordinates`, `max_retries` |
| `quality_control.grobid_integration` | `failure_behavior` (`"manifest_fail"` \| `"fallback"`), `crop_figures`, `crop_tables` |
| `quality_control.scan_detection` | `text_density_threshold`, `alpha_ratio_threshold`, `image_dominance_threshold` |
| `quality_control.local_metrics` | 8 Tier 1 thresholds |
| `quality_control.semantic_qc` | `enabled` (false by default), `model_name`, `similarity_threshold` |
| `quality_control.addons` | `grobid_quantities`, `datastet`, `entity_fishing` (all disabled by default) |
| `text_processor` | `class`, `sentence_tokenizer.backend`, `word_tokenizer.backend`, `normalizer.backend`, `comparison.threshold` |
| `pdfs_path` | Folder, single PDF, or Google Drive URL |
| `log_file`, `log_level` | Log path and console level |

Override rule: **env > yaml > default**. All `openai.*` keys can be overridden via environment variables (e.g. `OPENAI_API_KEY`, `OPENAI_CHUNK_MODEL`).

---

## Outputs

| File | Description |
|---|---|
| `outputs/<paper>.extracted.json` | Per-paper extraction: one record per `field_index` with `field_name`, `domain_group`, `extracted_value`, `evidence`, `location`, `location_metadata`, `confidence` |
| `outputs/qc_report.csv` | Cross-paper flagged rows: confidence `l` or `nr` |
| `outputs/evidence_cache/<id>.evidence.json` | Cached evidence index (keyed by paper_id + pdf_hash) |
| `outputs/evidence_cache/<id>.tei.xml` | Cached GROBID TEI XML |
| `manifest.json` | Per-PDF status checkpoint (`complete`, `failed_qc_pipeline`, `failed_chunks`, `failed_chunk_<n>`) |
| `run.log` (configurable) | Per-run logs including token counts and cache-hit percentages |
