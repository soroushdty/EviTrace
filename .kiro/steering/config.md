---
inclusion: always
---

# EviTrace — Configuration Reference

## Single Source of Truth

All tunable parameters live in `config/config.yaml`. The file is loaded **once at startup** via `utils/config_utils.py` and values are passed explicitly as arguments — never mutated as globals after startup.

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
| `load_openai_config()` | Full OpenAI + extraction + concurrency + retry dict | Pipeline orchestrator, api_client |
| `load_qc_config()` | `{quality_control: ..., text_processor: ...}` deep-merged with defaults | QC pipeline, scan detector |
| `load_local_config()` | Local parser settings (pdfs_path, len_filter, ocr, etc.) | main.py, GrobidServerManager |

All three functions accept an optional `config_path` argument. When `None`, they resolve `config/config.yaml` relative to the project root.

---

## Adding New Config Keys

1. Add the key to `config/config.yaml` under the appropriate section.
2. If it is a **top-level** key, register it in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `utils/config_utils.py` — otherwise `load_local_config` raises `ValueError`.
3. Add a default value to `_QC_DEFAULTS` (for QC/text_processor keys) or `_LOCAL_DEFAULTS` (for local parser keys).
4. Document the key in the relevant section below.

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

### `quality_control.grobid`

```yaml
quality_control:
  grobid:
    auto_start: true             # auto-start Docker container
    docker_image: "lfoppiano/grobid:0.8.0"
    url: "http://localhost:8070"
    timeout: 180
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

### `quality_control.local_metrics`

```yaml
quality_control:
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
```

### `quality_control.semantic_qc`

```yaml
quality_control:
  semantic_qc:
    enabled: false               # scaffolded only; not wired into adjudication
    model_name: "BAAI/bge-base-en-v1.5"
    similarity_threshold: 0.85
    max_sentences: 10000
```

### `quality_control.addons`

All disabled by default. Provide a running service URL to enable:

```yaml
quality_control:
  addons:
    grobid_quantities:
      enabled: false
      url: ""
    datastet:
      enabled: false
      url: ""
    entity_fishing:
      enabled: false
      url: ""
```

### `text_processor`

```yaml
text_processor:
  class: "utils.text_processor.TextProcessor"   # or fully-qualified SentenceSegment subclass
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

### Paths and Logging

```yaml
pdfs_path: "input"               # folder, single PDF, or Google Drive URL
output_folder_path: "outputs"
extraction_map_path: "extraction_map.json"
log_file: "run.log"
log_level: "DEBUG"               # DEBUG | INFO | WARNING | ERROR | CRITICAL
```

---

## CLI Overrides

`main.py` accepts these flags that override config values at runtime:

```bash
python main.py --pdf-dir /path/to/pdfs   # override pdfs_path
python main.py --concurrency 2           # override concurrency.pdf_processing
python main.py --no-cache-prewarm        # disable prompt_cache.enable_prewarm
```

CLI overrides are forwarded as parameters to `run_pipeline()` — they are never written back to module-level constants.
