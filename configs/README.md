# `config/` — YAML Configuration

Centralised configuration for every runtime knob in EviTrace.

The pipeline (and the standalone PDF extractor) read **a single
file**, `config/config.yaml`, parsed by
[`utils/config_utils.py`](../utils/README.md). Environment variables
take precedence for a small set of OpenAI-related keys (see the table
in the [root README](../README.md#configuration)).

---

## Where it fits

```text
config/config.yaml
        │
        ▼
utils.config_utils
        ├── load_openai_config()  ──► agents/openai (chunk model, cache, retries) and pipeline orchestrator
        ├── load_qc_config()      ──► quality_control (rater, IAA, adjudicator, reconciler, semantic QC)
        └── load_local_config()   ──► pdf_extractor (PDFs path, OCR, len_filter, output folder)

utils.path_utils                  ──► PDF_DIR, OUTPUT_DIR, EXTRACTION_MAP, MANIFEST_FILE
```

---

## Files

| File | Purpose |
| ---- | ------- |
| `config.yaml` | All user-adjustable settings. The only file in this directory today. |

The repository also accepts a fall-back location of `config.yaml` at
the project root; `config/config.yaml` is preferred when present
(see `_load_config_yaml` in [`utils/config_utils.py`](../utils/README.md)).

---

## Schema

The YAML is logically grouped into the following sections.

### `openai`

```yaml
openai:
  api_key: "sk-..."             # optional; OPENAI_API_KEY env var preferred
  base_url: "https://api.openai.com/v1"
  chunk_model: "gpt-5.5"
  synthesis_model: "gpt-5.5"
  temperature: null             # leave null to omit the parameter entirely
  prompt_cache:
    key_prefix: "scoping-review-v1"
    retention: "24h"            # or "in_memory"
    enable_prewarm: true
    warmup_max_tokens: 32
    prewarm_synthesis_if_model_diff: true
```

`temperature: null` is intentional: some GPT-5.x models reject the
`temperature` parameter outright. Leave it null unless you explicitly
want to send a value.

### `extraction`

```yaml
extraction:
  num_chunks: 3                 # total chunks; the last one is synthesis
```

The chunk-to-domain mapping for `num_chunks: 3` and `num_chunks: 5`
is hard-coded in
[`utils/config_utils._get_domain_to_chunk`](../utils/README.md);
other values fall back to an even split with the last chunk reserved
for synthesis.

### `concurrency`

```yaml
concurrency:
  pdf_processing: 1             # PDFs processed in parallel
  global_api_limit: 15          # max concurrent OpenAI API calls (across all PDFs)
```

### `retry`

```yaml
retry:
  max_retries: 3
  base_delay_seconds: 5         # actual delay = base * 2^(attempt - 1)
```

### Paths

```yaml
pdfs_path: "pdfs"
output_folder_path: "outputs"
extraction_map_path: "extraction_map.json"
```

`pdfs_path` may be a folder, a single PDF file, or a URL (including a
Google Drive folder URL). See
[`utils/path_utils.list_pdf_files_from_source`](../utils/README.md).

### Logging

```yaml
log_file: "run.log"             # relative to project root or absolute
log_level: "DEBUG"              # console level; the file handler is always DEBUG
```

### `pdf_extractor`-specific knobs

```yaml
len_filter: 40                  # min sentence length (chars) for sentence_processor
ocr: false                      # enable OCR fallback tiers
ocr_text_quality_threshold: 0.7 # min alphabetic-ratio score to accept a tier
```

### `quality_control`

The full schema is documented in
[`quality_control/README.md`](../quality_control/README.md).
Highlights:

```yaml
quality_control:
  discard_failed_branches: false
  status_field_location: "both"
  grobid:
    url: "http://localhost:8070"
    timeout: 120
    # ... see quality_control/README.md
  semantic_qc:
    enabled: false              # turning this on requires sentence-transformers, faiss, torch
    model_name: "BAAI/bge-base-en-v1.5"
    similarity_threshold: 0.85
  local_metrics:
    min_chars_per_page: 100
    grobid_vs_native_ratio_threshold: 0.6
    long_sentence_word_threshold: 120
    long_sentence_max_fraction: 0.12
    expected_sections: ["abstract", "introduction", "methods", "results"]
    coordinate_coverage_threshold: 0.1
    references_in_body_threshold: 0.05
    weird_char_ratio_threshold: 0.05
  artifact_generator:
    export_to_disk: false
    output_dir: "output/qc_artifacts"
  rater:
    attributes: []
  iaa_calculator:
    thresholds: {}
    agreement_metrics: []
  adjudicator:
    strategy: "placeholder"
  reconciler:
    enable_tei_export: false
    enable_annotation_export: false
```

Defaults for every QC field live in
[`utils/config_utils._QC_DEFAULTS`](../utils/README.md) — user values
deep-merge over the defaults.

---

## Environment variable overrides

A subset of OpenAI keys can be overridden without touching the file.
See the [root README configuration table](../README.md#configuration)
for the full list. The override rule is **environment > yaml >
default**.

---

## Operational notes

- **Do not commit secrets.** Prefer `OPENAI_API_KEY` as an environment
  variable rather than the `openai.api_key` field. Any key that lands
  in `config.yaml` is in version control history.
- The validator in `load_local_config` raises on **unknown top-level
  keys** to catch typos. Add new keys to
  `_ALL_KNOWN_TOP_LEVEL_KEYS` in
  [`utils/config_utils.py`](../utils/README.md) when extending the
  schema.
- A second copy of `config.yaml` at the project root is supported only
  as a fallback; new deployments should prefer `config/config.yaml`.

---

## Related

- Configuration loader and defaults: [../utils/README.md](../utils/README.md)
- Consumers of the OpenAI section: [../agents/openai/README.md](../agents/openai/README.md)
- Consumers of the QC section: [../quality_control/README.md](../quality_control/README.md)
- Consumers of the parser section: [../pdf_extractor/README.md](../pdf_extractor/README.md)
- Root overview: [../README.md](../README.md)
