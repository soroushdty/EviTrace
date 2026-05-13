# `pipeline/` — End-to-End Extraction Orchestrator

This package runs the full per-PDF flow: it drives the
[`pdf_extractor`](../pdf_extractor/README.md) extraction backends via
`extraction_pipeline.build_qc_bundle()`, the
[`quality_control`](../quality_control/README.md) adjudication, the
[`agents/openai`](../agents/openai/README.md) chunked extraction agent,
local schema validation, the manifest checkpoint, and the post-run QC
report.

`main.py` is a thin CLI wrapper around `pipeline.run_pipeline(...)`.

---

## Where it fits

```text
main.py
   │
   ▼
pipeline.run_pipeline(pdf_paths)
   │
   ├──► pipeline.extraction_pipeline.build_qc_bundle   # scan detection → routing → QC → annotation
   │         ├── pdf_extractor.extraction.GROBID        # semantic authority (TEI XML)
   │         ├── pdf_extractor.extraction.pdfplumber    # structural authority (text blocks)
   │         ├── pdf_extractor.extraction.PaddleOCR     # scanned primary
   │         ├── pdf_extractor.extraction.PyMuPDF       # font metadata / OCR cross-validator
   │         ├── quality_control.run_quality_control    # QCBundle + UnifiedRecord
   │         └── artifact_generation.w3c_annotation      # W3C JSON-LD projection
   │
   ├──► pipeline.evidence_index.build_or_load_evidence_bundle  # GROBID TEI → ranked evidence
   ├──► agents.openai.warm_pdf_cache                    # optional cache prewarm
   ├──► agents.openai.extract_chunk (× N-1, parallel)  # chunked extraction
   ├──► pipeline.validator.validate_chunk_output        # local index/schema checks
   ├──► agents.openai.extract_chunk (synthesis chunk)   # final chunk with prior context
   │
   ├──► pipeline.manifest.save_manifest                 # status checkpoint
   └──► pipeline.extraction_report.generate_qc_report  # outputs/qc_report.csv
```

Inputs:

- `List[Path]` of PDFs (resolved by `main.py` from the `--pdf-dir` flag
  or `config.pdfs_path`).
- Configuration loaded once via
  [`utils.config_utils.load_openai_config`](../utils/README.md) and
  [`utils.config_utils.load_qc_config`](../utils/README.md).
- The extraction map at `EXTRACTION_MAP` (default
  `configs/extraction_map.json`).

Outputs (per PDF):

- `outputs/<pdf_stem>.extracted.json` — sorted list of
  `{field_index, domain_group, field_name, extracted_value, evidence,
  location, location_metadata, confidence}` records.
- An updated `manifest.json` entry (`complete`, `failed_qc_pipeline`,
  `failed_chunks`, or `failed_chunk_<n>`).
- A line in the rolled-up `outputs/qc_report.csv` after all PDFs are
  processed.

---

## Files

### `__init__.py`

Re-exports the public surface:

- `run_pipeline(...)` — the only public entry point.
- Module-level constants from `orchestrator.py`:
  `CHUNK_MODEL`, `SYNTHESIS_MODEL`, `NUM_CHUNKS`, `DOMAIN_TO_CHUNK`,
  `PDF_CONCURRENCY`, `GLOBAL_API_LIMIT`, `ENABLE_CACHE_PREWARM`,
  `PREWARM_SYNTHESIS_IF_MODEL_DIFF`. Read by `main.py` for the startup
  log line.

### `extraction_pipeline.py`

**Single source of truth** for the full multi-backend extraction flow.
Both the standalone `pdf_extractor.py` CLI and the async orchestrator
delegate to `build_qc_bundle` rather than duplicating routing logic.

`build_qc_bundle(pdf_path, pdf_name, qc_config) -> QCBundle`

Per-page routing:
- All pages native → GROBID (semantic authority) + pdfplumber (structural
  authority); PyMuPDF font metadata stored in `ctx.unified.content`.
- Any page scanned + `ocr=true` → PaddleOCR (primary) + PyMuPDF OCR
  (secondary cross-validation).
- Any page scanned + `ocr=false` → skip extraction, log WARNING, no branch.

After QC, projects W3C JSON-LD annotations via `artifact_generation.w3c_annotation`
and stores them in `ctx.unified.content["annotations"]`.

### `orchestrator.py`

The async orchestrator.

`run_pipeline(pdf_paths, *, pdf_concurrency=None, enable_cache_prewarm=None) -> list[dict]`
— spawns one `asyncio` task per PDF, gated by an `asyncio.Semaphore`
for PDF-level concurrency and a global semaphore for total API
concurrency. CLI overrides flow through arguments (mutating module-level
globals is intentionally avoided).

### `pdf_processor.py`

Per-PDF LLM extraction orchestration.

`process_pdf(qc_context, chunk_fields, field_lookup, api_semaphore, manifest, manifest_lock, openai_config) -> list[dict] | None`

Steps:
1. `validate_qc_context_input(qc_context)` — pre-flight guard.
2. Skip if manifest entry is already `complete` and saved JSON exists.
3. `build_or_load_evidence_bundle(qc_context, config)` — build ranked
   evidence index; pre-fill fields 1–2 from TEI metadata.
4. Build per-chunk evidence packages (section-aware scoring, keyword
   overlap, char/item budget limits).
5. `_run_parallel_chunks(...)` — run chunks `1..N-1` in parallel with
   optional synthesis-model warmup.
6. `validate_chunk_output(raw, expected_indices, valid_location_ids)` —
   validate each chunk's JSON.
7. `reconstruct_fields(validated, field_lookup, evidence_map)` — expand
   compact `{i, v, loc, c}` records into full field dicts.
8. Run synthesis chunk with prior context.
9. Merge, sort, save JSON, mark manifest `complete`.
10. `attach_table_figure_crops(fields, bundle, config)` — crop table/figure
    regions for resolved `loc` IDs when configured.

### `evidence_index.py`

GROBID TEI XML → ranked evidence bundle with disk cache.

`build_or_load_evidence_bundle(qc_context, config) -> EvidenceBundle`
— parses GROBID TEI XML into a ranked, section-scored index (sentences,
tables, figure captions). Cached to disk by `{paper_id}_{pdf_hash}`.
Falls back to sentence-splitting `exact_text` when TEI XML is absent.
Enriches items with optional addon annotations (grobid-quantities,
datastet, entity-fishing).

`build_chunk_evidence_package(bundle, chunk_fields, *, max_items, max_chars) -> str`
— builds a compact per-chunk evidence package JSON string with
section-aware ranking and keyword overlap scoring.

`attach_table_figure_crops(fields, bundle, config) -> None`
— crops table/figure regions from the source PDF for resolved `loc` IDs
when `crop_figures` or `crop_tables` is enabled.

`EvidenceBundle` dataclass fields: `paper_id`, `tei_xml`,
`evidence_items`, `evidence_map`, `prefilled_fields`, `index_path`.

### `extraction_map.py`

Loads `configs/extraction_map.json` and groups its 62 fields into
per-chunk field lists.

- `load_chunk_fields() -> dict[int, list[dict]]` — returns
  `chunk_num → list[field_record]`.
- `_build_field_lookup() -> dict[int, dict]` — returns
  `field_index → {domain_group, field_name}`.
- `_infer_chunk_field_ranges() -> dict[int, tuple[int, int]]` — returns
  `chunk_num → (min_field_index, max_field_index)`.

### `validator.py`

Pure-Python validation, independent of the OpenAI structured-output schema.

- `ValidationError` — raised when a chunk's output fails validation.
- `clean_json_string(raw) -> str` — strips Markdown code fences.
- `_unwrap_top_level(data) -> list[dict]` — accepts both
  `{"extractions": [...]}` (Responses API) and a top-level list.
- `validate_chunk_output(raw, expected_indices, *, valid_location_ids=None) -> list[dict]`
  — parses the model text, checks shape, key set (`i`, `v`, `loc`, `c`),
  confidence enum, types, exact `field_index` set, and optionally validates
  `loc` IDs against `valid_location_ids`.
- `reconstruct_fields(compact, field_lookup, evidence_map=None) -> list[dict]`
  — expands compact `{i, v, loc, c}` records into full per-field dicts
  with `evidence`, `location`, `location_metadata`.

### `manifest.py`

Tiny helpers around `manifest.json`:

- `load_manifest() -> dict` — returns `{}` if the file does not exist.
- `save_manifest(manifest) -> None` — writes pretty-printed JSON.

The manifest is keyed by PDF stem and stores a `status` plus optional
fields like `failed_chunks` or `error`. It is the only mechanism that
makes the pipeline resumable after a crash.

### `extraction_report.py`

Post-run reporting:

- `generate_qc_report(results) -> None` — public entry point called by
  `main.py` after the pipeline completes. Writes `outputs/qc_report.csv`
  (flagged rows with confidence `"l"` or `"nr"`) and prints a summary
  including top-10 fields by `nr` rate.

---

## Concurrency model

- One `asyncio.Semaphore` (`pdf_semaphore`) caps how many PDFs are
  in-flight; defaults to `concurrency.pdf_processing` and can be
  overridden by `--concurrency`.
- A second semaphore (`api_semaphore`) caps **total** OpenAI API
  calls across all in-flight PDFs at `concurrency.global_api_limit`.
- All chunks `1..N-1` for a single PDF are launched in parallel with
  `asyncio.gather(...)`; the synthesis chunk runs sequentially after
  prior-context reconstruction.
- The branch-extraction step (GROBID + pdfplumber) is synchronous and is
  pushed into a worker thread via `asyncio.to_thread` so it does not
  block the event loop.

Manifest writes are serialised through an `asyncio.Lock`
(`manifest_lock`) so concurrent PDFs cannot trample each other's
status updates.

---

## Configuration

This package reads everything via
[`utils.config_utils`](../utils/README.md). The keys it cares about:

- `openai.chunk_model`, `openai.synthesis_model`, `openai.temperature`
- `openai.prompt_cache.*` (used through `agents/openai`)
- `extraction.num_chunks` and the derived `DOMAIN_TO_CHUNK`
- `extraction.max_evidence_items_per_chunk`, `extraction.max_evidence_chars_per_chunk`
- `extraction.evidence_cache_dir`
- `concurrency.pdf_processing`, `concurrency.global_api_limit`
- `retry.max_retries`, `retry.base_delay_seconds`
- `quality_control.grobid_integration.failure_behavior`
- `quality_control.grobid_integration.crop_figures`, `crop_tables`
- `quality_control.addons.*`

CLI overrides accepted by `main.py`:

- `--pdf-dir <path>`
- `--concurrency <int>`
- `--no-cache-prewarm`

---

## Caveats

- `process_pdf` short-circuits on `manifest[pdf_name].status == "complete"`
  only when the saved JSON also exists; if the file is missing the PDF is
  re-processed.
- Fields 1–2 (author, publication year) are pre-filled locally from GROBID
  TEI metadata and excluded from LLM chunks.
- All public field schemas (the OpenAI structured-output schema, the
  chunk-output validator, the reconstruction lookup) reference the same
  `extraction_map.json`. Editing the map is the canonical way to add or
  rename fields.

---

## Related

- Top-level CLI: [../README.md](../README.md)
- Single source of truth for extraction flow: [extraction_pipeline.py](extraction_pipeline.py)
- OpenAI client: [../agents/openai/README.md](../agents/openai/README.md)
- QC layer: [../quality_control/README.md](../quality_control/README.md)
- PDF extractor: [../pdf_extractor/README.md](../pdf_extractor/README.md)
- Configuration: [../configs/README.md](../configs/README.md)
- Shared utilities: [../utils/README.md](../utils/README.md)
