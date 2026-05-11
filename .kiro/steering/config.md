---
inclusion: always
---

# EviTrace — Configuration Reference

## Single Source of Truth

All tunable parameters live in `configs/config.yaml` (note: `configs/`, not `config/`). The file is loaded **once at startup** via `utils/config_utils.py` and values are passed explicitly as arguments — never mutated as globals after startup.

Override rule: **env > yaml > default**

---

## Environment Variable Overrides

All `openai.*` keys can be overridden via environment variables:

| Env var | Config key |
|---|---|
| `OPENAI_API_KEY` | `openai.api_key` |
| `OPENAI_BASE_URL` | `openai.base_url` |
| `OPENAI_CHUNK_MODEL` | `openai.chunk_model` |
| `OPENAI_SYNTHESIS_MODEL` | `openai.synthesis_model` |
| `OPENAI_TEMPERATURE` | `openai.temperature` |
| `OPENAI_PROMPT_CACHE_KEY_PREFIX` | `openai.prompt_cache.key_prefix` |
| `OPENAI_PROMPT_CACHE_RETENTION` | `openai.prompt_cache.retention` |
| `OPENAI_ENABLE_CACHE_PREWARM` | `openai.prompt_cache.enable_prewarm` |
| `OPENAI_CACHE_WARMUP_MAX_TOKENS` | `openai.prompt_cache.warmup_max_tokens` |
| `OPENAI_PREWARM_SYNTHESIS_IF_MODEL_DIFF` | `openai.prompt_cache.prewarm_synthesis_if_model_diff` |
| `OPENAI_NUM_CHUNKS` | `extraction.num_chunks` |

---

## Config Loading Functions

| Function | Returns | Use for |
|---|---|---|
| `load_openai_config()` | OpenAI + extraction + concurrency + retry dict | Pipeline orchestrator, api_client |
| `load_qc_config()` | `{quality_control: ..., text_processor: ...}` deep-merged with `_QC_DEFAULTS` | QC pipeline, scan detector, extraction_pipeline |
| `load_local_config()` | Local parser settings (pdfs_path, len_filter, ocr, etc.) deep-merged with `_LOCAL_DEFAULTS` | main.py, GrobidServerManager, pdf_extractor CLI |

All three functions accept an optional `config_path` argument. When `None`, they resolve `configs/config.yaml` first; if absent, fall back to repo-root `config.yaml`.

---

## Adding New Config Keys

1. Add the key to `configs/config.yaml` under the appropriate section.
2. If it is a **top-level** key, register it in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `utils/config_utils.py` — otherwise `load_local_config` raises `ValueError`.
3. Add a default value to `_QC_DEFAULTS` (for `quality_control` / `text_processor` keys) or `_LOCAL_DEFAULTS` (for local parser keys).
4. Document the key in the relevant section below.

Currently registered top-level keys: `openai`, `extraction`, `concurrency`, `retry`, `quality_control`, `text_processor`, `local`, `pdfs_path`, `output_folder_path`, `extraction_map_path`, `log_file`, `log_level`, `len_filter`, `ocr`.

---

## Key Config Sections

### `openai`

```yaml
openai:
  api_key: ""                    # or set OPENAI_API_KEY env var
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

### `extraction`

```yaml
extraction:
  num_chunks: 3                  # 3 or 5 supported natively
  max_evidence_items_per_chunk: 250
  max_evidence_chars_per_chunk: 60000
  evidence_cache_dir: "outputs/evidence_cache"
```

### `concurrency`

```yaml
concurrency:
  pdf_processing: 1              # PDFs processed in parallel
  global_api_limit: 15           # max concurrent OpenAI API calls
```

### `retry`

```yaml
retry:
  max_retries: 3
  base_delay_seconds: 5          # actual delay = base * 2^(attempt-1)
```

### Paths and Logging

```yaml
pdfs_path: "input"               # folder, single PDF, or Google Drive URL
output_folder_path: "outputs"
extraction_map_path: "configs/extraction_map.json"
log_file: "run.log"
log_level: "DEBUG"               # DEBUG | INFO | WARNING | ERROR | CRITICAL
len_filter: 40                   # minimum sentence length for extraction (chars)
ocr: false                       # enable PaddleOCR for scanned pages
```

### `text_processor`

```yaml
text_processor:
  class: "text_processing.base.ScispaCySentenceSegment"   # or fully-qualified SentenceSegment subclass
  sentence_tokenizer:
    backend: "scispacy"          # scispacy | wtpsplit | nltk_punkt | spacy_sentencizer | stanza
    model: "en_core_sci_sm"
  word_tokenizer:
    backend: "spacy"             # simple | spacy | nltk
  normalizer:
    backend: "nfkc"              # nfc | nfkc
  comparison:
    metric: "levenshtein"
    threshold: 0.85
  ocr_cleaning:
    weird_char_threshold: 0.05
```

### `quality_control.grobid`

```yaml
quality_control:
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
```

### `quality_control.grobid_integration`

```yaml
quality_control:
  grobid_integration:
    enabled: true
    failure_behavior: "manifest_fail"  # "manifest_fail" | "fallback"
    crop_figures: true
    crop_tables: true
```

`failure_behavior: "manifest_fail"` — skip the PDF and record failure in manifest (default).  
`failure_behavior: "fallback"` — fall back to sentence-splitting `exact_text` when GROBID is absent.

### `quality_control.scan_detection`

```yaml
quality_control:
  scan_detection:
    text_density_threshold: 50       # min word count for native page
    alpha_ratio_threshold: 0.60      # min alpha-char fraction after clean_ocr
    image_dominance_threshold: 0.85  # max image-area fraction before scanned
```

### `quality_control.ocr`

```yaml
quality_control:
  ocr:
    rasterization_dpi: 150           # DPI for PaddleOCR page rasterization
```

### `quality_control.local_metrics`

```yaml
quality_control:
  local_metrics:
    min_chars_per_page: 100
    extraction_coverage_ratio_threshold: 0.6   # renamed from grobid_vs_native_ratio_threshold
    long_sentence_word_threshold: 120
    long_sentence_max_fraction: 0.12
    expected_sections: ["abstract", "introduction", "methods", "results"]
    caption_table_figure_check_enabled: true
    coordinate_coverage_threshold: 0.1
    references_in_body_threshold: 0.05
    weird_char_ratio_threshold: 0.05
```

Results from `ExtractionCoverageReport` are stored in `ctx.metrics_hierarchy["extraction_coverage"]`.

### `quality_control.source_text_verification`

```yaml
quality_control:
  source_text_verification:
    enabled: true                # set false to bypass lexical source-text check
```

When `enabled` is `false`, the source-text check is bypassed without evaluating any check logic and the result is recorded as passed in `ctx.metrics_hierarchy["source_text_verification"]`.

### `quality_control.semantic_verification`

```yaml
quality_control:
  semantic_verification:
    enabled: false               # set true to run SemanticSourceVerificationCheck
    model_name: "BAAI/bge-base-en-v1.5"   # stored but model is never loaded by QC pipeline
    similarity_threshold: 0.85
    max_sentences: 10000
    on_index_unavailable: "skip"  # "skip" | "fail" | "degrade"
    extractor_agreement:
      enabled: false             # set true to run ExtractorAgreementCheck
      len_filter: 40             # discard candidate sentences shorter than this (chars)
      max_examples: 10           # max items per list key in the examples dict
```

`on_index_unavailable` controls behaviour when the sentence store is absent or empty:
- `"skip"` — return `VerificationResult` with `status="unavailable"` (default)
- `"fail"` — raise `RuntimeError`
- `"degrade"` — call the injected matcher as a lexical fallback and emit a `WARNING` log

When `enabled` is `false`, `sentence_transformers`, `faiss`, and `torch` are never imported through the QC code path.

Results are stored in `ctx.metrics_hierarchy["semantic_verification"]`. When `extractor_agreement.enabled` is `true`, the agreement report dict is stored under `ctx.metrics_hierarchy["semantic_verification"]["extractor_agreement"]`.

### `quality_control.task_quality_scaffold`

```yaml
quality_control:
  task_quality_scaffold:
    enabled: true                # include scaffold placeholders in per-PDF output
```

When `enabled` is `true`, `build_task_quality_scaffold()` returns a JSON-serializable dict with placeholder entries (`status="scaffolded"`, `value=null`) for: `field_recall`, `critical_field_recall`, `evidence_validity`, `evidence_compactness`, `cost_reduction`, `manual_qc_rate`, `interobserver_agreement`, `pipeline_agreement`. The scaffold is stored under the key `"task_quality_scaffold"` in per-PDF output JSON.

### `quality_control.text_fidelity` and `quality_control.section_verification`

```yaml
quality_control:
  text_fidelity:
    edit_distance_threshold: 0.10    # normalized Levenshtein; above = divergent
  section_verification:
    font_size_tolerance: 1.0         # allowable font-size delta in points
```

### `quality_control.addons`

Disabled by default. Provide a running service URL and set `enabled: true` to activate:

```yaml
quality_control:
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

When `enabled: true` and a valid `url` is provided, the addon service is called during evidence index enrichment.

### `quality_control` pipeline stage settings

```yaml
quality_control:
  discard_failed_branches: false
  status_field_location: "both"    # "both" | "branch" | "bundle"
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

---

## CLI Overrides

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

CLI overrides are forwarded as parameters to `run_pipeline()` — they are never written back to module-level constants.
