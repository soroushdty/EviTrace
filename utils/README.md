# `utils/` — Repository-Wide Shared Utilities

Cross-cutting helpers used by every other package in EviTrace:
configuration loading, logging, path resolution, text processing, and
GROBID server lifecycle management.

This module is import-safe — nothing here pulls in OpenAI, FAISS,
sentence-transformers, or other heavyweight dependencies — so it is
the right place for code that needs to be reachable from both the
parser and the orchestrator without creating circular imports.

---

## Where it fits

```text
configs/config.yaml ──► utils.config_utils ──► agents/openai, pipeline, pdf_extractor, quality_control
                                              │
utils.path_utils    ──► PROJECT_ROOT, PDF_DIR, OUTPUT_DIR,
                       EXTRACTION_MAP, MANIFEST_FILE, QC_REPORT_FILE

utils.logging_utils ──► every package's get_logger(__name__) calls

utils.text_processor ──► quality_control (scan_detector, reconciler),
                         pdf_extractor/processing (sentence_processor)

utils.grobid_manager ──► pdf_extractor.pdf_extractor (CLI), main.py
```

---

## Files

### `config_utils.py`

Configuration entry points and defaults.

| Function | Returns |
| -------- | ------- |
| `load_openai_config(config_path=None)` | Flat dict with all OpenAI/extraction/concurrency/retry keys, with environment-variable overrides applied. Used by `agents/openai` and `pipeline.orchestrator`. |
| `load_qc_config(config_path=None)` | `{"quality_control": {...}, "text_processor": {...}}` deep-merged with `_QC_DEFAULTS`. Used by `quality_control.run_quality_control` and `pipeline.extraction_pipeline`. |
| `load_local_config(config_path=None)` | Validated `pdf_extractor` parser settings deep-merged with `_LOCAL_DEFAULTS`. Resolves `pdfs_path` to an absolute path; rejects unknown top-level keys to catch typos. |
| `get_qc_config(config)` | Helper that returns `config["quality_control"]`. |

`load_openai_config` returns a flat dict with keys including:
`api_key`, `base_url`, `chunk_model`, `synthesis_model`, `temperature`,
`prompt_cache_key_prefix`, `prompt_cache_retention`, `enable_cache_prewarm`,
`cache_warmup_max_tokens`, `prewarm_synthesis_if_model_diff`,
`num_chunks`, `chunk_max_tokens`, `domain_to_chunk`,
`pdf_concurrency`, `global_api_limit`, `max_retries`, `retry_base_delay`,
`max_evidence_items_per_chunk`, `max_evidence_chars_per_chunk`,
`evidence_cache_dir`, `grobid_failure_behavior`.

Internal helpers:

- `_load_config_yaml(config_path)` — prefers `configs/config.yaml`, falls
  back to `config.yaml` at the project root.
- `_deep_merge(base, override)` — non-mutating recursive merge used for
  QC defaults.
- `_get_chunk_max_tokens(num_chunks)` — per-chunk output token budget.
  Hard-coded for `num_chunks ∈ {3, 5}`; falls back to a flat 3500-token
  budget otherwise.
- `_get_domain_to_chunk(num_chunks)` — assigns domains 1–13 to chunks.
  Hard-coded for `num_chunks ∈ {3, 5}`; otherwise distributes 1–12
  evenly across `num_chunks - 1` extraction chunks and reserves the
  last chunk for synthesis (domain 13).

The set of legitimate top-level keys for the YAML lives in
`_ALL_KNOWN_TOP_LEVEL_KEYS` and is enforced by `load_local_config`.
Currently registered top-level keys: `openai`, `extraction`, `concurrency`,
`retry`, `quality_control`, `local`, `text_processor`, `pdfs_path`,
`output_folder_path`, `extraction_map_path`, `log_file`, `log_level`,
`len_filter`, `ocr`.

### `path_utils.py`

Centralised path constants and PDF source resolution.

| Constant | Description |
| -------- | ----------- |
| `PROJECT_ROOT` / `BASE_DIR` | Absolute path to the repo root. |
| `EXTRACTION_MAP` | Resolved location of `extraction_map.json` (prefers `configs/`, falls back to root). |
| `PDF_DIR` | Default input directory (`pdfs/` unless overridden in YAML). |
| `OUTPUT_DIR` | Per-paper extraction output dir (`outputs/`). |
| `MANIFEST_FILE` | `manifest.json` at the project root. |
| `QC_REPORT_FILE` | `outputs/qc_report.csv`. |

Functions:

- `is_url(value)` — true for `http(s)://` URLs.
- `resolve_project_path(path_or_url)` — turn a relative path into an
  absolute project-rooted path.
- `resolve_log_path(log_file)` — same idea for log files.
- `_scan_local_pdf_paths(local_path)` — list `.pdf` files under a
  directory or accept a single PDF.
- `_download_pdf_source_url(url, ...)` — supports
  `drive.google.com/folders/...` URLs (folder downloads) and
  ad-hoc URL downloads via `gdown` (lazy import).
- `list_pdf_files_from_source(pdf_source)` — unified entry point
  that returns `(local_folder, {pdf_name: {id, local_path, uri}})`
  for any source (URL or local path).
- `list_pdf_files_from_dir(pdfs_dir)` — strict folder-only variant
  used by `pdf_extractor`.
- `create_output_folder(output_folder_path="output")` — creates and
  returns an absolute output folder path.

### `logging_utils.py`

A single shared root logger named `evi_trace`.

| Function | Description |
| -------- | ----------- |
| `get_logger(name)` | Standard `logging.getLogger(name)`; child of `evi_trace`. Use `get_logger(__name__)` everywhere. |
| `get_root_logger(name="evi_trace")` | Direct accessor for the root logger. |
| `setup_logging(log_file, console_level, file_level=DEBUG, overwrite=True)` | Idempotent setup: file handler at `file_level`, stream handler at `console_level`. Removes its own previously-installed handlers on repeat calls so duplicate log lines do not appear. Returns the configured logger. |
| `log_cache_usage(response, tag, logger=None)` | Robustly extracts `usage.input_tokens`, `output_tokens`, and `usage.input_tokens_details.cached_tokens` from OpenAI Responses API objects (or dicts in tests) and logs `tokens: input=..., cached=..., cache_hit=..%, output=...`. |

Format strings:

- File: `%(asctime)s | %(levelname)-8s | %(name)s | %(module)s:%(lineno)d | %(message)s`
- Console: `%(levelname)-8s | %(message)s`

### `text_processor.py`

Config-driven text transformation hub and sentence segmentation backends.

**`TextProcessor`** — main class. Accepts a `config` dict and wires up
normalizer, word tokenizer, and sentence segmenter backends at construction
time. Unknown backend names raise `ValueError` immediately.

| Method | Description |
| ------ | ----------- |
| `normalize(text)` | Unicode normalization (NFC or NFKC) + whitespace collapse. Idempotent. |
| `compare(a, b)` | `difflib.SequenceMatcher` ratio after normalizing both inputs. Returns float in `[0.0, 1.0]`. |
| `clean_ocr(text)` | Remove U+FFFD replacement characters and C0 control characters (`\x00`–`\x08`, `\x0b`, `\x0c`, `\x0e`–`\x1f`). Preserves `\t`, `\n`, `\r`. |
| `tokenize_words(text)` | Word tokenization via configured backend (`simple` / `spacy` / `nltk`). |
| `extract_keywords(text)` | Non-stopword tokens (hardcoded English stopword list; no NLP package required). |
| `tokenize_sentences(text)` | Delegates to the configured `SentenceSegment` backend. |

**`SentenceSegment`** — abstract base class. Inherits the full `TextProcessor`
interface. Subclasses must override `tokenize_sentences`. Five built-in
concrete backends:

| Class | Backend | Notes |
| ----- | ------- | ----- |
| `ScispaCySentenceSegment` | scispaCy | Default model `en_core_sci_sm`; auto-installs from S3 URL if missing. |
| `WtpSplitSentenceSegment` | wtpsplit | Loads `wtp-bert-mini`; raises `ImportError` with install hint if absent. |
| `NLTKPunktSentenceSegment` | NLTK Punkt | Raises `ImportError` if NLTK or Punkt data absent. |
| `SpacySentencizerSegment` | spaCy Sentencizer | Blank `en` pipeline with `sentencizer` component. |
| `StanzaSentenceSegment` | Stanford Stanza | English tokenize pipeline. |

Valid `sentence_tokenizer.backend` values: `"scispacy"`, `"wtpsplit"`,
`"nltk_punkt"`, `"spacy_sentencizer"`, `"stanza"`, or any fully-qualified
class path (loaded via `importlib`).

### `grobid_manager.py`

**`GrobidServerManager`** — context manager that automates the local GROBID
server lifecycle.

| Method | Description |
| ------ | ----------- |
| `__enter__()` | If `auto_start=true` and GROBID is not already alive, starts the configured Docker image, polls `/api/isalive` for up to 300 seconds, and prompts the user to start Docker Desktop if the daemon is not running. |
| `__exit__(...)` | Stops the temporary GROBID container if one was started. |

Reads config from `config["quality_control"]["grobid"]`. Key settings:
`auto_start`, `docker_image`, `url`.

---

## Usage

```python
# Logging
from utils.logging_utils import get_logger, setup_logging
setup_logging(log_file="run.log", console_level="INFO")
logger = get_logger(__name__)

# Configuration
from utils.config_utils import load_openai_config, load_local_config, load_qc_config
openai_cfg = load_openai_config()
local_cfg  = load_local_config()
qc_cfg     = load_qc_config()  # wrapped under {"quality_control": ..., "text_processor": ...}

# Paths
from utils.path_utils import PROJECT_ROOT, PDF_DIR, OUTPUT_DIR, EXTRACTION_MAP

# Text processing
from utils.text_processor import TextProcessor
tp = TextProcessor(config={"sentence_tokenizer": {"backend": "scispacy"}})
sentences = tp.tokenize_sentences("The patient was treated. Recovery was complete.")
```

---

## Dependencies

- `PyYAML` for `config.yaml` loading
- `jsonschema` for schema validation (used by `quality_control`)
- `gdown` (lazy) for URL / Drive folder ingestion
- Standard library otherwise

---

## Caveats

- `_load_local_settings` in `path_utils.py` swallows any YAML loading
  exception silently and returns `{}`. This is intentional so that
  importing the module never crashes (the same constants are imported
  by tests that may not have a config file), but it does mean
  malformed YAML can degrade silently to defaults.
- `setup_logging(overwrite=True)` is the default — log files are
  truncated at the start of every run. Pass `overwrite=False` to append.
- `load_local_config` raises `ValueError` on **any** unknown top-level
  YAML key. When adding a new section to `config.yaml`, register it
  in `_ALL_KNOWN_TOP_LEVEL_KEYS` first.
- Config is loaded from `configs/config.yaml` (preferred) or `config.yaml`
  at the project root (fallback). New deployments should use `configs/`.

---

## Related

- YAML files this package reads: [../configs/README.md](../configs/README.md)
- OpenAI client driven by `load_openai_config`: [../agents/openai/README.md](../agents/openai/README.md)
- Pipeline orchestrator: [../pipeline/README.md](../pipeline/README.md)
- PDF extractor: [../pdf_extractor/README.md](../pdf_extractor/README.md)
- QC pipeline: [../quality_control/README.md](../quality_control/README.md)
- Root overview: [../README.md](../README.md)
