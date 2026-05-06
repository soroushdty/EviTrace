"""Centralized configuration loading, validation, and normalization for EviTrace.

This module handles loading and orchestrating configuration from:
1. config.yaml (user-adjustable settings)
2. Environment variables (for API keys and overrides)
3. Default values (for optional settings)
"""

from __future__ import annotations

import os
from pathlib import Path
import yaml

from .path_utils import resolve_project_path

# ============================================================================
# Defaults and required keys for local (EviTrace parser) configuration
# ============================================================================

_LOCAL_DEFAULTS: dict = {
    "log_file": "log.txt",
    "log_level": "INFO",
    "len_filter": 40,
    "ocr": True,
    "ocr_text_quality_threshold": 0.7,
    "output_folder_path": "output",
}

_LOCAL_REQUIRED: frozenset[str] = frozenset({"pdfs_path"})


# ============================================================================
# OpenAI Configuration Loading
# ============================================================================

def _load_config_yaml(config_path: str | None = None) -> dict:
    """Load the config.yaml file from the repo root.

    Args:
        config_path: Optional path to config.yaml. Defaults to repo root/config.yaml.

    Returns:
        Parsed YAML config dict.
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent / "config.yaml"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_openai_config(config_path: str | None = None) -> dict:
    """Load OpenAI configuration from config.yaml and environment variables.

    Environment variables take precedence over config.yaml values.

    Returns:
        Dict with keys: api_key, base_url, chunk_model, synthesis_model, temperature,
                       prompt_cache_key_prefix, prompt_cache_retention, enable_cache_prewarm,
                       cache_warmup_max_tokens, prewarm_synthesis_if_model_diff,
                       num_chunks, chunk_max_tokens, domain_to_chunk,
                       pdf_concurrency, global_api_limit, max_retries, retry_base_delay.
    """
    cfg_yaml = _load_config_yaml(config_path)
    openai_cfg = cfg_yaml.get("openai", {})
    extraction_cfg = cfg_yaml.get("extraction", {})
    concurrency_cfg = cfg_yaml.get("concurrency", {})
    retry_cfg = cfg_yaml.get("retry", {})

    # API credentials
    api_key = os.environ.get("OPENAI_API_KEY", "") or openai_cfg.get("api_key", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "") or openai_cfg.get("base_url", "")

    # Model choices
    chunk_model = os.environ.get("OPENAI_CHUNK_MODEL", None) or openai_cfg.get("chunk_model", "gpt-5.5")
    synthesis_model = os.environ.get("OPENAI_SYNTHESIS_MODEL", None) or openai_cfg.get("synthesis_model", "gpt-5.5")

    # Temperature
    temp_raw = os.environ.get("OPENAI_TEMPERATURE", "").strip()
    temperature = float(temp_raw) if temp_raw else openai_cfg.get("temperature")

    # Prompt cache config
    cache_cfg = openai_cfg.get("prompt_cache", {})
    cache_key_prefix = os.environ.get("OPENAI_PROMPT_CACHE_KEY_PREFIX", None) or cache_cfg.get("key_prefix", "scoping-review-v1")
    cache_retention = os.environ.get("OPENAI_PROMPT_CACHE_RETENTION", None) or cache_cfg.get("retention", "in_memory")
    enable_prewarm_raw = os.environ.get("OPENAI_ENABLE_CACHE_PREWARM", "").strip()
    if enable_prewarm_raw:
        enable_prewarm = enable_prewarm_raw.lower() not in {"0", "false", "no"}
    else:
        enable_prewarm = cache_cfg.get("enable_prewarm", True)
    warmup_max_tokens = int(os.environ.get("OPENAI_CACHE_WARMUP_MAX_TOKENS", None) or cache_cfg.get("warmup_max_tokens", 32))

    prewarm_diff_raw = os.environ.get("OPENAI_PREWARM_SYNTHESIS_IF_MODEL_DIFF", "").strip()
    if prewarm_diff_raw:
        prewarm_synthesis_diff = prewarm_diff_raw.lower() not in {"0", "false", "no"}
    else:
        prewarm_synthesis_diff = cache_cfg.get("prewarm_synthesis_if_model_diff", True)

    # Extraction config
    num_chunks = int(os.environ.get("OPENAI_NUM_CHUNKS", None) or extraction_cfg.get("num_chunks", 3))

    # Concurrency config
    pdf_concurrency = int(concurrency_cfg.get("pdf_processing", 3))
    global_api_limit = int(concurrency_cfg.get("global_api_limit", 15))

    # Retry config
    max_retries = int(retry_cfg.get("max_retries", 3))
    retry_base_delay = int(retry_cfg.get("base_delay_seconds", 5))

    # Calculate derived values
    chunk_max_tokens = _get_chunk_max_tokens(num_chunks)
    domain_to_chunk = _get_domain_to_chunk(num_chunks)

    return {
        "api_key": api_key,
        "base_url": base_url or None,
        "chunk_model": chunk_model,
        "synthesis_model": synthesis_model,
        "temperature": temperature,
        "prompt_cache_key_prefix": cache_key_prefix,
        "prompt_cache_retention": cache_retention,
        "enable_cache_prewarm": enable_prewarm,
        "cache_warmup_max_tokens": warmup_max_tokens,
        "prewarm_synthesis_if_model_diff": prewarm_synthesis_diff,
        "num_chunks": num_chunks,
        "chunk_max_tokens": chunk_max_tokens,
        "domain_to_chunk": domain_to_chunk,
        "pdf_concurrency": pdf_concurrency,
        "global_api_limit": global_api_limit,
        "max_retries": max_retries,
        "retry_base_delay": retry_base_delay,
    }


def _get_chunk_max_tokens(num_chunks: int) -> dict[int, int]:
    """Generate appropriate max tokens per chunk based on NUM_CHUNKS.

    Args:
        num_chunks: Total number of chunks (including synthesis).

    Returns:
        Dict mapping chunk number to max tokens.
    """
    if num_chunks == 5:
        return {1: 3500, 2: 3000, 3: 5000, 4: 3500, 5: 2500}
    elif num_chunks == 3:
        return {1: 3500, 2: 5000, 3: 2500}
    else:
        # For other values, distribute tokens evenly
        base_tokens = 3500
        return {i: base_tokens for i in range(1, num_chunks + 1)}


def _get_domain_to_chunk(num_chunks: int) -> dict[int, int]:
    """Generate domain-to-chunk mapping based on NUM_CHUNKS.

    Domains are distributed to chunks with the last chunk reserved for synthesis/review.

    Args:
        num_chunks: Total number of chunks (including synthesis).

    Returns:
        Dict mapping domain (1-13) to chunk number.
    """
    if num_chunks == 5:
        return {
            1: 1,   # 1. Study identification
            2: 1,   # 2. Clinical and study-design context
            3: 1,   # 3. Cohort and data source
            4: 2,   # 4. Prediction task
            5: 2,   # 5. Data inputs and modalities
            6: 3,   # 6. Graph structure and topology
            7: 3,   # 7. Node specification
            8: 3,   # 8. Edge specification
            9: 3,   # 9. Temporal representation
            10: 4,  # 10. Model architecture
            11: 4,  # 11. Evaluation and generalizability
            12: 4,  # 12. Interpretability and representation
            13: 5,  # 13. Reviewer assessment and critique (synthesis)
        }
    elif num_chunks == 3:
        return {
            1: 1,   # 1. Study identification
            2: 1,   # 2. Clinical and study-design context
            3: 1,   # 3. Cohort and data source
            4: 1,   # 4. Prediction task
            5: 1,   # 5. Data inputs and modalities
            6: 2,   # 6. Graph structure and topology
            7: 2,   # 7. Node specification
            8: 2,   # 8. Edge specification
            9: 2,   # 9. Temporal representation
            10: 2,  # 10. Model architecture
            11: 2,  # 11. Evaluation and generalizability
            12: 2,  # 12. Interpretability and representation
            13: 3,  # 13. Reviewer assessment and critique (synthesis)
        }
    else:
        # For other values, distribute domains evenly across NUM_CHUNKS-1 extraction chunks,
        # with the last chunk reserved for synthesis.
        mapping = {}
        chunks_for_extraction = num_chunks - 1
        domains_per_chunk = 12 // chunks_for_extraction  # Domains 1-12
        remainder = 12 % chunks_for_extraction

        domain = 1
        for chunk_num in range(1, chunks_for_extraction + 1):
            domains_in_this_chunk = domains_per_chunk + (1 if chunk_num <= remainder else 0)
            for _ in range(domains_in_this_chunk):
                mapping[domain] = chunk_num
                domain += 1
        mapping[13] = num_chunks  # Synthesis chunk
        return mapping


# ============================================================================
# Local (EviTrace Parser) Configuration Loading
# ============================================================================

def load_local_config(config_path: str) -> dict:
    """Load, validate, and normalize the local parser config file (YAML format).

    This was previously load_config() and handles EviTrace-specific settings
    like pdfs_path, ocr settings, logging, etc.

    Args:
        config_path: Path to the local config YAML file.

    Raises:
        ValueError: For unknown keys or a missing/empty pdfs_path.
        TypeError: For keys with wrong types.

    Returns:
        Dict with all optional keys filled with their defaults and
        pdfs_path resolved to an absolute path.
    """
    with open(config_path, encoding="utf-8") as f:
        raw: dict = yaml.safe_load(f) or {}

    unknown = set(raw) - _LOCAL_REQUIRED - set(_LOCAL_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")

    cfg = {**_LOCAL_DEFAULTS, **raw}

    pdfs_path: str = cfg.get("pdfs_path", "")
    if not isinstance(pdfs_path, str) or not pdfs_path.strip():
        raise ValueError("Config 'pdfs_path' must be a non-empty string")
    cfg["pdfs_path"] = resolve_project_path(pdfs_path)

    if not isinstance(cfg["len_filter"], int) or isinstance(cfg["len_filter"], bool):
        raise TypeError(
            f"Config 'len_filter' must be an integer, got {type(cfg['len_filter']).__name__}"
        )
    if not isinstance(cfg["ocr"], bool):
        raise TypeError(
            f"Config 'ocr' must be a boolean, got {type(cfg['ocr']).__name__}"
        )
    if not isinstance(cfg["ocr_text_quality_threshold"], (int, float)) or isinstance(
        cfg["ocr_text_quality_threshold"], bool
    ):
        raise TypeError("Config 'ocr_text_quality_threshold' must be a number")

    return cfg


# ============================================================================
# Public API: Centralized configuration access
# ============================================================================

# Maintain backward compatibility
load_config = load_local_config
