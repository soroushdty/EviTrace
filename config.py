"""Configuration constants for the OpenAI scoping review extraction pipeline."""
import os
from pathlib import Path

# -- API ---------------------------------------------------------------------
# The OpenAI SDK will also read OPENAI_API_KEY from the environment, but keeping
# it explicit lets main.py fail early with a clear message.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "") or None

# Model choices. Override without editing code, e.g.:
#   export OPENAI_CHUNK_MODEL="gpt-4.1"
#   export OPENAI_SYNTHESIS_MODEL="gpt-4.1"
CHUNK_MODEL = os.environ.get("OPENAI_CHUNK_MODEL", "gpt-5.5")
SYNTHESIS_MODEL = os.environ.get("OPENAI_SYNTHESIS_MODEL", "gpt-5.5")

# Some newer OpenAI models reject the temperature parameter entirely.
# Leave OPENAI_TEMPERATURE unset by default so requests omit it.
_TEMP_RAW = os.environ.get("OPENAI_TEMPERATURE", "").strip()
TEMPERATURE = float(_TEMP_RAW) if _TEMP_RAW else None

# Prompt-cache configuration.
# Prompt caching itself is automatic on supported OpenAI models for long,
# repeated prefixes. This workflow adds one cheap warmup call per PDF and uses
# a per-paper prompt_cache_key derived from the extracted PDF text. Do not use a
# single global cache key for all PDFs.
PROMPT_CACHE_KEY_PREFIX = os.environ.get("OPENAI_PROMPT_CACHE_KEY_PREFIX", "scoping-review-v1")
PROMPT_CACHE_RETENTION = os.environ.get("OPENAI_PROMPT_CACHE_RETENTION", "in_memory")  # e.g. "24h", "in_memory"
ENABLE_CACHE_PREWARM = os.environ.get("OPENAI_ENABLE_CACHE_PREWARM", "1").lower() not in {"0", "false", "no"}
CACHE_WARMUP_MAX_TOKENS = int(os.environ.get("OPENAI_CACHE_WARMUP_MAX_TOKENS", "32"))

# If chunk and synthesis models differ, start a second tiny warmup for the
# synthesis model while chunks 1-4 are running. This usually avoids extra wall
# time and improves chunk-5 cache eligibility.
PREWARM_SYNTHESIS_IF_MODEL_DIFF = os.environ.get(
    "OPENAI_PREWARM_SYNTHESIS_IF_MODEL_DIFF", "1"
).lower() not in {"0", "false", "no"}

# Max output tokens per chunk. These caps are intentionally generous for about
# 30 words each in extracted_value and evidence while avoiding truncation/retry.
CHUNK_MAX_TOKENS = {1: 3500, 2: 3000, 3: 5000, 4: 3500, 5: 2500}

# -- Concurrency -------------------------------------------------------------
PDF_CONCURRENCY = 3       # PDFs processed in parallel
GLOBAL_API_LIMIT = 15     # max concurrent OpenAI API calls across all PDFs

# -- Retry -------------------------------------------------------------------
MAX_RETRIES = 3
RETRY_BASE_DELAY = 5      # seconds; actual delay = base * 2^(attempt - 1)

# -- Chunk -> field_index ranges (inclusive) ---------------------------------
# Chunk 1: domains 1-3   Study ID, clinical context, cohort
# Chunk 2: domains 4-5   Prediction task, data inputs and modalities
# Chunk 3: domains 6-9   Graph topology and temporal representation
# Chunk 4: domains 10-12 Model architecture, evaluation, interpretability
# Chunk 5: domain 13     Reviewer synthesis (receives prior context)
CHUNK_FIELD_RANGES = {
    1: (1, 15),
    2: (16, 25),
    3: (26, 44),
    4: (45, 56),
    5: (57, 62),
}

# -- Validation --------------------------------------------------------------
ALLOWED_CONFIDENCE = {"h", "m", "l", "nr"}
REQUIRED_KEYS = {"i", "v", "e", "c"}

# -- Paths -------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
EXTRACTION_MAP = BASE_DIR / "extraction_map.json"
PDF_DIR = BASE_DIR / "pdfs"
OUTPUT_DIR = BASE_DIR / "outputs"
MANIFEST_FILE = BASE_DIR / "manifest.json"
QC_REPORT_FILE = BASE_DIR / "outputs" / "qc_report.csv"
MASTER_OUTPUT = BASE_DIR / "outputs" / "all_extractions.json"
