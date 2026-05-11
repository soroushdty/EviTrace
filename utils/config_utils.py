"""Centralized configuration loading, validation, and normalization for EviTrace.

This module handles loading and orchestrating configuration from:
1. config.yaml (user-adjustable settings)
2. Environment variables (for API keys and overrides)
3. Default values (for optional settings)
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import yaml

from .path_utils import resolve_project_path

# ============================================================================
# Quality control configuration defaults and helpers
# ============================================================================

_QC_DEFAULTS: dict = {
    # Top-level text_processor configuration (Requirement 9)
    # class: fully-qualified class path resolved via importlib
    # sentence_tokenizer.backend: valid values — "scispacy" | "wtpsplit" | "nltk_punkt" | "spacy_sentencizer" | "stanza"
    # word_tokenizer.backend: valid values — "simple" | "spacy" | "nltk"
    # normalizer.backend: valid values — "nfc" | "nfkc"
    "text_processor": {
        "class": "utils.text_processor.TextProcessor",
        "sentence_tokenizer": {"backend": "scispacy", "model": "en_core_sci_sm"},
        "word_tokenizer": {"backend": "simple"},
        "normalizer": {"backend": "nfkc"},
        "comparison": {"metric": "levenshtein", "threshold": 0.85},
        "ocr_cleaning": {"weird_char_threshold": 0.05},
    },
    "quality_control": {
        "discard_failed_branches": False,
        "status_field_location": "both",
        "artifact_generator": {"export_to_disk": False, "output_dir": "output/qc_artifacts"},
        "rater": {"attributes": []},
        "iaa_calculator": {"thresholds": {}, "agreement_metrics": []},
        "adjudicator": {"strategy": "placeholder"},
        "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
        "semantic_qc": {
            "enabled": False,
            "model_name": "BAAI/bge-base-en-v1.5",
            "query_prefix": "Represent this sentence for searching relevant passages: ",
            "similarity_threshold": 0.85,
            "max_sentences": 10000,
        },
        "local_metrics": {
            "min_chars_per_page": 100,
            "grobid_vs_native_ratio_threshold": 0.6,
            "long_sentence_word_threshold": 120,
            "long_sentence_max_fraction": 0.12,
            "expected_sections": ["abstract", "introduction", "methods", "results"],
            "caption_table_figure_check_enabled": True,
            "coordinate_coverage_threshold": 0.1,
            "references_in_body_threshold": 0.05,
            "weird_char_ratio_threshold": 0.05,
        },
        "grobid": {
            "url": "http://localhost:8070",
            "timeout": 120,
            "consolidate_header": 0,
            "consolidate_citations": 0,
            "generate_ids": True,
            "segment_sentences": True,
            "include_raw_citations": True,
            "include_raw_affiliations": False,
            "tei_coordinates": True,
            "max_retries": 2,
        },
        "grobid_integration": {
            "enabled": True,
            "failure_behavior": "fallback",
            "crop_figures": True,
            "crop_tables": True,
        },
        "addons": {
            "grobid_quantities": {"enabled": False, "url": "", "endpoint": "/service/process", "timeout": 20},
            "datastet": {"enabled": False, "url": "", "endpoint": "/service/processDataseerSentence", "timeout": 20},
            "entity_fishing": {"enabled": False, "url": "", "endpoint": "/service/disambiguate", "timeout": 20},
        },
        # Scan detection thresholds (Requirement 9 / design §scan_detector)
        # text_density_threshold: minimum word count for a native page (integer, ≥ 0)
        # alpha_ratio_threshold: minimum ratio of alphabetic chars in cleaned text (float, 0.0–1.0)
        # image_dominance_threshold: maximum image-area fraction before page is flagged scanned (float, 0.0–1.0)
        "scan_detection": {
            "text_density_threshold": 50,
            "alpha_ratio_threshold": 0.60,
            "image_dominance_threshold": 0.85,
        },
        # OCR rasterization settings (Requirement 9)
        # rasterization_dpi: DPI used when rasterizing a page for PaddleOCR (integer, e.g. 150 | 300)
        "ocr": {
            "rasterization_dpi": 150,
        },
        # Text fidelity concern settings (Requirement 9)
        # edit_distance_threshold: normalized Levenshtein distance above which texts are "divergent" (float, 0.0–1.0)
        "text_fidelity": {
            "edit_distance_threshold": 0.10,
        },
        # Section verification concern settings (Requirement 9)
        # font_size_tolerance: allowable font-size delta when verifying section headings (float, points)
        "section_verification": {
            "font_size_tolerance": 1.0,
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Return base deep-merged with override; override values win. Neither is mutated."""
    result = copy.deepcopy(base)
    for key, override_val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(override_val, dict):
            result[key] = _deep_merge(result[key], override_val)
        else:
            result[key] = copy.deepcopy(override_val)
    return result


def get_qc_config(config: dict) -> dict:
    """Extract the quality_control sub-dict from a loaded config."""
    return config["quality_control"]


def load_qc_config(config_path: str | None = None) -> dict:
    """Load quality_control and text_processor sections from config.yaml, deep-merged with defaults.

    Returns a dict with 'quality_control' and 'text_processor' top-level keys.
    Missing keys are filled in from _QC_DEFAULTS so that callers always receive
    the full documented default set regardless of what is present in config.yaml.
    """
    raw = _load_config_yaml(config_path)
    user_cfg = {
        "quality_control": raw.get("quality_control", {}) or {},
        "text_processor": raw.get("text_processor", {}) or {},
    }
    return _deep_merge(_QC_DEFAULTS, user_cfg)


# ============================================================================
# Defaults and required keys for local (EviTrace parser) configuration
# ============================================================================

_LOCAL_DEFAULTS: dict = {
    "log_file": "log.txt",
    "log_level": "INFO",
    "len_filter": 40,
    "ocr": True,
    "output_folder_path": "output",
}

_LOCAL_REQUIRED: frozenset[str] = frozenset({"pdfs_path"})
_LOCAL_ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {*_LOCAL_DEFAULTS.keys(), *_LOCAL_REQUIRED, "extraction_map_path"}
)
# All legitimate top-level keys across any supported config layout.
# Used to detect typos; anything outside this set raises ValueError.
_ALL_KNOWN_TOP_LEVEL_KEYS: frozenset[str] = _LOCAL_ALLOWED_TOP_LEVEL_KEYS | frozenset(
    {"openai", "extraction", "concurrency", "retry", "quality_control", "local", "text_processor"}
)


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
        # Prefer configs/config.yaml when present, fall back to repo-root config.yaml
        default1 = Path(__file__).parent.parent / "configs" / "config.yaml"
        default2 = Path(__file__).parent.parent / "config.yaml"
        if default1.exists():
            config_path = default1
        else:
            config_path = default2
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
    grobid_integration_cfg = cfg_yaml.get("quality_control", {}).get("grobid_integration", {})

    # API credentials
    api_key = os.environ.get("OPENAI_API_KEY", "") or openai_cfg.get("api_key", "")
    # Preserve environment-variable override: environment variables take
    # precedence over the value in `config.yaml` for `base_url`.
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
        "max_evidence_items_per_chunk": int(extraction_cfg.get("max_evidence_items_per_chunk", 250)),
        "max_evidence_chars_per_chunk": int(extraction_cfg.get("max_evidence_chars_per_chunk", 60000)),
        "evidence_cache_dir": extraction_cfg.get("evidence_cache_dir", "outputs/evidence_cache"),
        "grobid_failure_behavior": grobid_integration_cfg.get("failure_behavior", "fallback"),
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

def load_local_config(config_path: str | None = None) -> dict:
    """Load, validate, and normalize local parser settings from `config.yaml`.

    This function is tolerant of the full project-level `config.yaml` layout.
    It will first attempt to read a nested `local:` mapping and fall back to
    top-level keys if the nested block is not present. Unknown keys in the
    *local* mapping will raise `ValueError` to prevent typos.

    Args:
        config_path: Optional path to the YAML file. If None, defaults to
                     the repo's `config.yaml` next to this module's parent.

    Raises:
        ValueError: For unknown keys or a missing/empty pdfs_path.
        TypeError: For keys with wrong types.

    Returns:
        Dict with optional keys filled with defaults and `pdfs_path`
        resolved to an absolute path.
    """
    cfg_yaml = _load_config_yaml(config_path)

    unknown_top = set(cfg_yaml) - _ALL_KNOWN_TOP_LEVEL_KEYS
    if unknown_top:
        raise ValueError(f"Unknown config keys: {sorted(unknown_top)}")

    raw = cfg_yaml.get("local")
    if raw is None:
        raw = {key: value for key, value in cfg_yaml.items() if key in _LOCAL_ALLOWED_TOP_LEVEL_KEYS}

    # Only validate keys that pertain to the local config
    unknown = set(raw) - _LOCAL_REQUIRED - set(_LOCAL_DEFAULTS) - {"extraction_map_path"}
    if unknown:
        raise ValueError(f"Unknown local config keys: {sorted(unknown)}")

    cfg = {**_LOCAL_DEFAULTS, **raw}

    user_qc = {"quality_control": cfg_yaml.get("quality_control", {}) or {}}
    cfg["quality_control"] = _deep_merge(_QC_DEFAULTS, user_qc)["quality_control"]

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

    return cfg


# ============================================================================
# Public API: Centralized configuration access
# ============================================================================
