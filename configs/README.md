# `configs/` — Configuration and Schema Files

Centralised configuration and JSON schema files for every runtime knob
in EviTrace.

The pipeline (and the standalone PDF extractor) read **a single YAML
file**, `configs/config.yaml`, parsed by
[`utils/config_utils.py`](../utils/README.md). Environment variables
take precedence for a small set of OpenAI-related keys (see the table
in the [root README](../README.md#configuration)).

---

## Where it fits

```text
configs/config.yaml
        │
        ▼
utils.config_utils
        ├── load_openai_config()  ──► agents/openai (chunk model, cache, retries) and pipeline orchestrator
        ├── load_qc_config()      ──► quality_control (rater, IAA, adjudicator, reconciler, semantic QC)
        └── load_local_config()   ──► pdf_extractor (PDFs path, OCR, len_filter, output folder)

configs/agent_schema.json
        └── agents/validator.AgentSchemaValidator  (sole reader)

configs/structure_schema.json
        └── quality_control/structure_validator.StructureSchemaValidator  (sole reader)

configs/extraction_map.json
        └── pipeline/extraction_map.load_chunk_fields()  (sole reader)

utils.path_utils                  ──► PDF_DIR, OUTPUT_DIR, EXTRACTION_MAP, MANIFEST_FILE
```

---

## Files

| File | Purpose |
| ---- | ------- |
| `config.yaml` | All user-adjustable runtime settings. Single source of truth. |
| `extraction_map.json` | 62 extraction fields across 13 domain groups — canonical field schema. |
| `agent_schema.json` | LLM agent system prompt, policies, extraction rules — read only by `AgentSchemaValidator`. |
| `structure_schema.json` | JSON Schema (Draft 7) for pipeline dataclasses — read only by `StructureSchemaValidator`. |

The repository also accepts a fall-back location of `config.yaml` at
the project root; `configs/config.yaml` is preferred when present
(see `_load_config_yaml` in [`utils/config_utils.py`](../utils/README.md)).

---

## `config.yaml` Schema

The YAML is logically grouped into the following sections.

### `openai`

```yaml
openai:
  api_key: ""                    # or set OPENAI_API_KEY env var (preferred)
  base_url: "https://api.openai.com/v1"
  chunk_model: "gpt-5.5"
  synthesis_model: "gpt-5.5"
  temperature: null              # omit from API request when null
  prompt_cache:
    key_prefix: "scoping-review-v1"
    retention: "24h"             # "24h" | "in_memory"
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
  num_chunks: 3                  # total chunks; the last one is synthesis
  max_evidence_items_per_chunk: 250
  max_evidence_chars_per_chunk: 60000
  evidence_cache_dir: "outputs/evidence_cache"
```

The chunk-to-domain mapping for `num_chunks: 3` and `num_chunks: 5`
is hard-coded in
[`utils/config_utils._get_domain_to_chunk`](../utils/README.md);
other values fall back to an even split with the last chunk reserved
for synthesis.

### `concurrency`

```yaml
concurrency:
  pdf_processing: 1              # PDFs processed in parallel
  global_api_limit: 15           # max concurrent OpenAI API calls (across all PDFs)
```

### `retry`

```yaml
retry:
  max_retries: 3
  base_delay_seconds: 5          # actual delay = base * 2^(attempt - 1)
```

### Paths

```yaml
pdfs_path: "pdfs"
output_folder_path: "outputs"
extraction_map_path: "configs/extraction_map.json"
```

`pdfs_path` may be a folder, a single PDF file, or a URL (including a
Google Drive folder URL). See
[`utils/path_utils.list_pdf_files_from_source`](../utils/README.md).

### Logging

```yaml
log_file: "run.log"              # relative to project root or absolute
log_level: "DEBUG"               # console level; the file handler is always DEBUG
```

### `pdf_extractor`-specific knobs

```yaml
len_filter: 40                   # min sentence length (chars) for sentence_processor
ocr: false                       # enable PaddleOCR for scanned pages
```

### `text_processor`

```yaml
text_processor:
  class: "utils.text_processor.TextProcessor"
  sentence_tokenizer:
    backend: "scispacy"          # scispacy | wtpsplit | nltk_punkt | spacy_sentencizer | stanza
    model: "en_core_sci_sm"
  word_tokenizer:
    backend: "simple"            # simple | spacy | nltk
  normalizer:
    backend: "nfkc"              # nfc | nfkc
  comparison:
    metric: "levenshtein"
    threshold: 0.85
  ocr_cleaning:
    weird_char_threshold: 0.05
```

### `quality_control`

```yaml
quality_control:
  discard_failed_branches: false
  status_field_location: "both"  # "both" | "branch" | "bundle"
  grobid:
    auto_start: true
    docker_image: "lfoppiano/grobid:0.8.0"
    url: "http://localhost:8070"
    timeout: 180
    consolidate_header: 0
    consolidate_citations: 0
    generate_ids: true
    segment_sentences: true
    include_raw_citations: true
    include_raw_affiliations: false
    tei_coordinates: true        # required for evidence index coords
    max_retries: 2
  grobid_integration:
    enabled: true
    failure_behavior: "manifest_fail"  # "manifest_fail" | "fallback"
    crop_figures: true
    crop_tables: true
  scan_detection:
    text_density_threshold: 50
    alpha_ratio_threshold: 0.60
    image_dominance_threshold: 0.85
  ocr:
    rasterization_dpi: 150
  local_metrics:
    min_chars_per_page: 100
    grobid_vs_native_ratio_threshold: 0.6
    long_sentence_word_threshold: 120
    long_sentence_max_fraction: 0.12
    expected_sections: ["abstract", "introduction", "methods", "results"]
    caption_table_figure_check_enabled: true
    coordinate_coverage_threshold: 0.1
    references_in_body_threshold: 0.05
    weird_char_ratio_threshold: 0.05
  semantic_qc:
    enabled: false               # scaffolded only; not wired into adjudication
    model_name: "BAAI/bge-base-en-v1.5"
    query_prefix: "Represent this sentence for searching relevant passages: "
    similarity_threshold: 0.85
    max_sentences: 10000
  text_fidelity:
    edit_distance_threshold: 0.10
  section_verification:
    font_size_tolerance: 1.0
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
  addons:
    grobid_quantities:
      enabled: false
      url: ""
      endpoint: "/service/process"
      timeout: 20
    datastet:
      enabled: false
      url: ""
      endpoint: "/service/processDataseerSentence"
      timeout: 20
    entity_fishing:
      enabled: false
      url: ""
      endpoint: "/service/disambiguate"
      timeout: 20
```

`failure_behavior: "manifest_fail"` — skip the PDF and record failure in
manifest (default).
`failure_behavior: "fallback"` — fall back to sentence-splitting `exact_text`
when GROBID is absent.

Defaults for every QC field live in
[`utils/config_utils._QC_DEFAULTS`](../utils/README.md) — user values
deep-merge over the defaults.

---

## `extraction_map.json`

Defines each of the 62 extraction fields with:
`field_index` (int, 1–62), `domain_group`, `field_name`, `definition`,
`reviewer_question`, `format`, `categories_or_examples`.

Fields 1–2 (author, publication year) are pre-filled locally from GROBID
TEI metadata and excluded from LLM chunks.

Read exclusively by `pipeline/extraction_map.py`.

---

## `agent_schema.json`

Contains the LLM agent system prompt, policies, and extraction rules.
Read exclusively by `agents/validator.AgentSchemaValidator`.

Required top-level keys: `system_prompt`, `policies`, `extraction_rules`.
The `version` and `type` metadata keys are intentionally not forwarded
to the LLM.

---

## `structure_schema.json`

JSON Schema (Draft 7) for pipeline dataclasses. Read exclusively by
`quality_control/structure_validator.StructureSchemaValidator`.

Defines schemas for: `Candidate`, `QCBundle`, `PdfProcessorOutput`,
`ExtractionMap`, `ChunkOutput`. The `validator_targets` key maps
validation method names to `#/$defs/<TypeName>` references.

---

## Environment variable overrides

A subset of OpenAI keys can be overridden without touching the file.
Override rule: **environment > yaml > default**.

See the [root README configuration table](../README.md#configuration)
for the full list.

---

## Operational notes

- **Do not commit secrets.** Prefer `OPENAI_API_KEY` as an environment
  variable rather than the `openai.api_key` field.
- The validator in `load_local_config` raises on **unknown top-level
  keys** to catch typos. Add new keys to
  `_ALL_KNOWN_TOP_LEVEL_KEYS` in
  [`utils/config_utils.py`](../utils/README.md) when extending the schema.
- A second copy of `config.yaml` at the project root is supported only
  as a fallback; new deployments should prefer `configs/config.yaml`.

---

## CLI overrides

`main.py` accepts these flags that override config values at runtime:

```bash
python main.py --pdf-dir /path/to/pdfs   # override pdfs_path
python main.py --concurrency 2           # override concurrency.pdf_processing
python main.py --no-cache-prewarm        # disable prompt_cache.enable_prewarm
```

The standalone extraction CLI also accepts `--config`:

```bash
python -m pdf_extractor.pdf_extractor --config /path/to/config.yaml
```

CLI overrides are forwarded as parameters to `run_pipeline()` — they are
never written back to module-level constants.

---

## Related

- Configuration loader and defaults: [../utils/README.md](../utils/README.md)
- Consumers of the OpenAI section: [../agents/openai/README.md](../agents/openai/README.md)
- Consumers of the QC section: [../quality_control/README.md](../quality_control/README.md)
- Consumers of the parser section: [../pdf_extractor/README.md](../pdf_extractor/README.md)
- Root overview: [../README.md](../README.md)
