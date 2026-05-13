"""Extraction map helpers

Provides:
- _infer_chunk_field_ranges
- load_chunk_fields
- _build_field_lookup
"""
import json
from typing import Dict, List, Tuple
from pathlib import Path

from utils.path_utils import EXTRACTION_MAP


def _infer_chunk_field_ranges() -> Dict[int, Tuple[int, int]]:
    """Infer chunk field index ranges from extraction_map.json and domain-to-chunk mapping.

    Returns:
        A dict mapping chunk_num to (min_field_index, max_field_index).
    """
    with open(EXTRACTION_MAP, encoding="utf-8") as f:
        all_fields: List[dict] = json.load(f)

    # Import here to avoid a circular import at module import time.
    from utils.config_utils import load_openai_config

    cfg = load_openai_config()
    DOMAIN_TO_CHUNK: dict[int, int] = cfg["domain_to_chunk"]

    # Group fields by chunk number
    chunk_to_fields: dict[int, list[int]] = {}
    for field in all_fields:
        domain_prefix = int(field["domain_group"].split(".")[0])
        chunk_num = DOMAIN_TO_CHUNK.get(domain_prefix)
        if chunk_num is None:
            raise ValueError(
                f"Domain {domain_prefix} from '{field['domain_group']}' not found in DOMAIN_TO_CHUNK mapping"
            )
        chunk_to_fields.setdefault(chunk_num, []).append(field["field_index"])

    # Build ranges from grouped fields
    result: dict[int, tuple[int, int]] = {}
    for chunk_num, field_indices in chunk_to_fields.items():
        result[chunk_num] = (min(field_indices), max(field_indices))

    return result


def load_chunk_fields() -> Dict[int, List[dict]]:
    """Load extraction_map.json and split into per-chunk field lists.

    Field assignments are inferred from extraction_map.json domain groups and
    the DOMAIN_TO_CHUNK configuration.
    """
    with open(EXTRACTION_MAP, encoding="utf-8") as f:
        all_fields: List[dict] = json.load(f)

    chunk_field_ranges = _infer_chunk_field_ranges()
    result: dict[int, list[dict]] = {}
    for chunk_num, (lo, hi) in chunk_field_ranges.items():
        result[chunk_num] = [
            field for field in all_fields
            if lo <= field["field_index"] <= hi
        ]
    return result


def _build_field_lookup() -> Dict[int, dict]:
    """Build a field_index → {domain_group, field_name} lookup from extraction_map.json."""
    with open(EXTRACTION_MAP, encoding="utf-8") as f:
        all_fields: List[dict] = json.load(f)
    return {
        f["field_index"]: {"domain_group": f["domain_group"], "field_name": f["field_name"]}
        for f in all_fields
    }
