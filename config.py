"""Configuration constants for the OpenAI scoping review extraction pipeline.

DEPRECATED: Configuration has been refactored into config.yaml and utils/config_utils.py.

This module now loads configuration from config_utils for backward compatibility.
New code should use utils.config_utils.load_openai_config() directly.

Configuration is loaded dynamically when this module is imported.
User-adjustable settings are in config.yaml at the repo root.
"""

from __future__ import annotations

# Import configuration loading from the centralized utility
from utils.config_utils import load_openai_config, _get_chunk_max_tokens, _get_domain_to_chunk

# Import path constants from the centralized utility
from utils.path_utils import BASE_DIR, EXTRACTION_MAP, PDF_DIR, OUTPUT_DIR, MANIFEST_FILE, QC_REPORT_FILE

# ============================================================================
# Load configuration from config.yaml
# ============================================================================

_openai_config = load_openai_config()

# -- API Configuration -------------------------------------------------------
OPENAI_API_KEY: str = _openai_config["api_key"]
OPENAI_BASE_URL: str | None = _openai_config["base_url"]

# Model choices
CHUNK_MODEL: str = _openai_config["chunk_model"]
SYNTHESIS_MODEL: str = _openai_config["synthesis_model"]
TEMPERATURE: float | None = _openai_config["temperature"]

# Prompt-cache configuration
PROMPT_CACHE_KEY_PREFIX: str = _openai_config["prompt_cache_key_prefix"]
PROMPT_CACHE_RETENTION: str = _openai_config["prompt_cache_retention"]
ENABLE_CACHE_PREWARM: bool = _openai_config["enable_cache_prewarm"]
CACHE_WARMUP_MAX_TOKENS: int = _openai_config["cache_warmup_max_tokens"]
PREWARM_SYNTHESIS_IF_MODEL_DIFF: bool = _openai_config["prewarm_synthesis_if_model_diff"]

# -- Chunk Configuration -----------------------------------------------------
NUM_CHUNKS: int = _openai_config["num_chunks"]
CHUNK_MAX_TOKENS: dict[int, int] = _openai_config["chunk_max_tokens"]
DOMAIN_TO_CHUNK: dict[int, int] = _openai_config["domain_to_chunk"]

# -- Concurrency Configuration -----------------------------------------------
PDF_CONCURRENCY: int = _openai_config["pdf_concurrency"]
GLOBAL_API_LIMIT: int = _openai_config["global_api_limit"]

# -- Retry Configuration -----------------------------------------------------
MAX_RETRIES: int = _openai_config["max_retries"]
RETRY_BASE_DELAY: int = _openai_config["retry_base_delay"]

# -- Validation Constants (static) -------------------------------------------
ALLOWED_CONFIDENCE = {"h", "m", "l", "nr"}
REQUIRED_KEYS = {"i", "v", "e", "c"}

# -- Paths (imported from path_utils) ----------------------------------------
# BASE_DIR, EXTRACTION_MAP, PDF_DIR, OUTPUT_DIR, MANIFEST_FILE, QC_REPORT_FILE
# are imported above for backward compatibility

