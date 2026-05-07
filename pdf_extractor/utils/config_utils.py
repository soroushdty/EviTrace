"""Config loading, validation, and normalization for the EviTrace parser."""

from __future__ import annotations

import copy

import yaml

from utils.path_utils import resolve_project_path

_DEFAULTS: dict = {
    "log_file": "log.txt",
    "log_level": "INFO",
    "len_filter": 40,
    "ocr": True,
    "ocr_text_quality_threshold": 0.7,
    "output_folder_path": "output",
}

_REQUIRED: frozenset[str] = frozenset({"pdfs_path"})

_QC_DEFAULTS: dict = {
    "quality_control": {
        "discard_failed_branches": False,
        "status_field_location": "both",
        "artifact_generator": {
            "export_to_disk": False,
            "output_dir": "output/qc_artifacts",
        },
        "rater": {
            "attributes": [],
        },
        "iaa_calculator": {
            "thresholds": {},
            "agreement_metrics": [],
        },
        "adjudicator": {
            "strategy": "placeholder",
        },
        "reconciler": {
            "enable_tei_export": False,
            "enable_annotation_export": False,
        },
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
    }
}

# Top-level keys that are valid but not in _DEFAULTS or _REQUIRED
_OPTIONAL_TOPLEVEL: frozenset[str] = frozenset({"quality_control"})


def _deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict that is *base* deep-merged with *override*.

    For keys present in both dicts whose values are both dicts, the merge
    recurses.  For all other cases the *override* value wins.  Neither
    *base* nor *override* is mutated.
    """
    result = copy.deepcopy(base)
    for key, override_val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(override_val, dict):
            result[key] = _deep_merge(result[key], override_val)
        else:
            result[key] = copy.deepcopy(override_val)
    return result


def load_config(config_path: str) -> dict:
    """Load, validate, and normalize the pipeline config file (YAML format).

    Raises ValueError for unknown keys or a missing/empty pdfs_path.
    Raises TypeError for keys with wrong types.
    Returns a dict with all optional keys filled with their defaults and
    pdfs_path resolved to an absolute path.
    """
    with open(config_path, encoding="utf-8") as f:
        raw: dict = yaml.safe_load(f) or {}

    known_keys = _REQUIRED | set(_DEFAULTS) | _OPTIONAL_TOPLEVEL
    unknown = set(raw) - known_keys
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")

    cfg = {**_DEFAULTS, **raw}

    # Deep-merge QC defaults; user-supplied values win at every level.
    user_qc = cfg.get("quality_control", {}) or {}
    cfg["quality_control"] = _deep_merge(_QC_DEFAULTS["quality_control"], user_qc)

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


def get_qc_config(config: dict) -> dict:
    """Extract and return the ``quality_control`` sub-dict from *config*.

    The returned dict is the same object stored under ``config["quality_control"]``.
    Callers should not mutate it; use :func:`copy.deepcopy` if a mutable copy
    is needed.
    """
    return config["quality_control"]
