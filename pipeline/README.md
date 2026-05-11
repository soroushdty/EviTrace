# `pipeline/` — End-to-End Extraction Orchestrator

This package runs the full per-PDF flow: it drives the
[`pdf_extractor`](../pdf_extractor/README.md) extraction backends, the
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
   ├──► pdf_extractor.extraction.{GROBID, PyMuPDF}        # branch extraction
   ├──► quality_control.run_quality_control               # QCBundle + UnifiedRecord
   │
   ├──► agents.openai.warm_pdf_cache                      # optional cache prewarm
   ├──► agents.openai.extract_chunk (× N-1, parallel)     # chunked extraction
   ├──► pipeline.validator.validate_chunk_output          # local index/schema checks
   ├──► agents.openai.extract_chunk (synthesis chunk)     # final chunk with prior context
   │
   ├──► pipeline.manifest.save_manifest                   # status checkpoint
   └──► pipeline.extraction_report.generate_qc_report     # outputs/qc_report.csv
```

Inputs:

- `List[Path]` of PDFs (resolved by `main.py` from the `--pdf-dir` flag
  or `config.pdfs_path`).
- Configuration loaded once via
  [`utils.config_utils.load_openai_config`](../utils/README.md) and
  [`utils.config_utils.load_qc_config`](../utils/README.md).
- The extraction map at `EXTRACTION_MAP` (default
  `extraction_map.json`).

Outputs (per PDF):

- `outputs/<pdf_stem>.extracted.json` — sorted list of
  `{field_index, domain_group, field_name, extracted_value, evidence,
  confidence}` records.
- An updated `manifest.json` entry (`complete`, `failed_qc_pipeline`,
  `failed_chunks`, or `failed_chunk_<n>`).
- A line in the rolled-up `outputs/qc_report.csv` after all PDFs are
  processed.

---

## Files

### `__init__.py`

Re-exports the public surface so callers can do
`import pipeline; pipeline.run_pipeline(...)`:

- `run_pipeline(...)` — the only public entry point.
- Module-level constants from `orchestrator.py`:
  `CHUNK_MODEL`, `SYNTHESIS_MODEL`, `NUM_CHUNKS`, `DOMAIN_TO_CHUNK`,
  `PDF_CONCURRENCY`, `GLOBAL_API_LIMIT`, `ENABLE_CACHE_PREWARM`,
  `PREWARM_SYNTHESIS_IF_MODEL_DIFF`. Mostly read by `main.py` for the
  startup log line.

### `orchestrator.py`

The async orchestrator.

- `run_pipeline(pdf_paths, *, pdf_concurrency=None, enable_cache_prewarm=None)`
  spawns one `asyncio` task per PDF, gated by an `asyncio.Semaphore`
  for PDF-level concurrency and a global semaphore for total API
  concurrency. CLI overrides flow through arguments (mutating
  module-level globals is intentionally avoided).
- `_build_qc_context(...)` (run in `asyncio.to_thread`) calls
  `extract_with_grobid` and `extract_with_pymupdf` for the same PDF,
  packs both into `Candidate` records, and delegates to
  `quality_control.run_quality_control`.

### `pdf_processor.py`

Per-PDF processing.

- `process_pdf(qc_context, chunk_fields, field_lookup, api_semaphore,
  manifest, manifest_lock, openai_config)` is the inner async function
  called by `_bounded` in the orchestrator.
- Steps:
  1. Validate the input is a fully-reconciled `QCBundle`
     (see `validator.validate_qc_context_input`).
  2. Skip the PDF if its `manifest` entry is already `complete` and
     the saved JSON exists (`_load_completed_result`).
  3. Run `_run_parallel_chunks` for chunks `1..N-1`. Optionally fires
     a synthesis-model warmup concurrently if
     `prewarm_synthesis_if_model_diff` is on and the models differ.
  4. Reconstruct full per-field records from chunks `1..N-1` via
     `validator.reconstruct_fields` and sort by `field_index` to use as
     `prior_context` for the synthesis chunk.
  5. Run the synthesis chunk; on success merge with prior fields,
     sort, save the JSON, and mark the manifest `complete`.
- Failures update the manifest with a status string (`failed_chunks`,
  `failed_chunk_<n>`, `failed_qc_pipeline`) and return `None`.

### `extraction_map.py`

Loads `extraction_map.json` and groups its 62 fields into per-chunk
field lists.

- `_infer_chunk_field_ranges()` walks every map entry, looks up each
  field's chunk via `DOMAIN_TO_CHUNK`, and produces a `chunk_num →
  (min_field_index, max_field_index)` table.
- `load_chunk_fields()` returns `chunk_num → list[field_record]` using
  those ranges.
- `_build_field_lookup()` returns `field_index →
  {domain_group, field_name}` for the post-chunk reconstruction step.

The `DOMAIN_TO_CHUNK` table itself is generated from
`extraction.num_chunks` in
[`utils/config_utils._get_domain_to_chunk`](../utils/README.md).

### `validator.py`

Pure-Python validation, independent of the OpenAI structured-output
schema.

- `validate_qc_context_input(ctx)` — guards the input to `process_pdf`.
- `clean_json_string(raw)` — strips Markdown code fences.
- `_unwrap_top_level(data)` — accepts both
  `{"extractions": [...]}` (Responses API) and a top-level list
  (legacy Claude-style).
- `validate_chunk_output(raw, expected_indices)` — parses the model
  text, checks shape, key set, confidence enum, types, and the exact
  `field_index` set, raising `ValidationError` with descriptive
  messages on any mismatch.
- `reconstruct_fields(compact, field_lookup)` — expands the compact
  `{i, v, e, c}` records into full per-field dicts.

### `manifest.py`

Tiny helpers around `manifest.json`:

- `load_manifest()` returns `{}` if the file does not exist.
- `save_manifest(manifest)` writes pretty-printed JSON.

The manifest is keyed by PDF stem and stores a `status` plus
optional fields like `failed_chunks` or `error`. It is the only
mechanism that makes the pipeline resumable after a crash.

### `extraction_report.py`

Post-run reporting:

- `_collect_qc_data(results)` aggregates rows whose confidence is
  `"l"` or `"nr"` and counts `extracted_value == "nr"` per
  `field_index`.
- `_write_qc_csv(flagged_rows)` writes `outputs/qc_report.csv` with
  one row per flagged field.
- `_print_summary(...)` prints PDFs processed, total fields,
  flagged-row count, and the top-10 fields by `nr` rate to stdout.
- `generate_qc_report(results)` — public entry point called by
  `main.py` after the pipeline completes.

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
- The branch-extraction step (GROBID + PyMuPDF) is synchronous and is
  pushed into a worker thread via `asyncio.to_thread` so it does not
  block the event loop.

Manifest writes are serialised through an `asyncio.Lock`
(`manifest_lock`) so concurrent PDFs cannot trample each other's
status updates.

---

## Configuration

This package reads everything via
[`utils.config_utils`](../utils/README.md). The keys it cares about
are:

- `openai.chunk_model`, `openai.synthesis_model`, `openai.temperature`
- `openai.prompt_cache.*` (used through `agents/openai`)
- `extraction.num_chunks` and the derived `DOMAIN_TO_CHUNK`
- `concurrency.pdf_processing`, `concurrency.global_api_limit`
- `retry.max_retries`, `retry.base_delay_seconds` (used through
  `agents/openai`)

CLI overrides accepted by `main.py`:

- `--pdf-dir <path>`
- `--concurrency <int>`
- `--no-cache-prewarm`

---

## Caveats

- `process_pdf` short-circuits on `manifest[pdf_name].status ==
  "complete"` only when the saved JSON also exists; if the file is
  missing the PDF is re-processed.
- The orchestrator currently expects a 2-branch (GROBID + PyMuPDF) QC
  layout; `_build_qc_context` is the single point that would change to
  add a third branch.
- All public field schemas (the OpenAI structured-output schema, the
  chunk-output validator, the reconstruction lookup) reference the
  same `extraction_map.json`. Editing the map is the canonical way to
  add or rename fields.

---

## Related

- Top-level CLI: [../README.md](../README.md)
- OpenAI client: [../agents/openai/README.md](../agents/openai/README.md)
- QC layer: [../quality_control/README.md](../quality_control/README.md)
- PDF extractor: [../pdf_extractor/README.md](../pdf_extractor/README.md)
- Configuration: [../config/README.md](../config/README.md)
- Shared utilities: [../utils/README.md](../utils/README.md)
