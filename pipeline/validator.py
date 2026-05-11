"""Validate model JSON output against the expected extraction schema."""
import json
import re
from typing import Any

from utils.logging_utils import get_logger

logger = get_logger(__name__)

# Validation constants
ALLOWED_CONFIDENCE = {"h", "m", "l", "nr"}
REQUIRED_KEYS = {"i", "v", "loc", "c"}


class ValidationError(Exception):
    """Raised when a subagent's output fails schema validation."""
    pass


def clean_json_string(raw: str) -> str:
    """Strip markdown code fences and surrounding whitespace."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _unwrap_top_level(data: Any) -> list[dict]:
    """
    Accept either the OpenAI structured-output wrapper
        {"extractions": [...]}
    or the legacy Claude-style top-level list.
    """
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("extractions", "fields", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    raise ValidationError(
        f"Expected a JSON array or object with an 'extractions' array, got {type(data).__name__}."
    )


def _parse_response_json(raw: str) -> list[dict]:
    """Parse raw API text to a validated list of extraction dicts."""
    cleaned = clean_json_string(raw)
    logger.debug(
        "validator: parsing JSON (raw=%d chars, cleaned=%d chars, tail=%r)",
        len(raw), len(cleaned), cleaned[-60:] if cleaned else "",
    )
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.debug(
            "validator: JSON parse failed at pos %s; raw output (full) = %r",
            getattr(exc, "pos", "?"), raw,
        )
        raise ValidationError(
            f"JSON parse failed: {exc}\n"
            f"Raw output (first 500 chars):\n{raw[:500]}"
        ) from exc
    items = _unwrap_top_level(parsed)
    logger.debug("validator: parsed %d extraction items", len(items))
    return items


def _validate_extraction_item(
    i: int,
    obj: Any,
    actual_indices: list[int],
    valid_location_ids: set[str] | None = None,
) -> None:
    """Validate a single extraction object and append its index to actual_indices."""
    if not isinstance(obj, dict):
        raise ValidationError(f"Item {i} is not a dict: {obj!r}")

    missing = REQUIRED_KEYS - obj.keys()
    extra = obj.keys() - REQUIRED_KEYS
    if missing:
        raise ValidationError(f"Item {i} is missing keys: {missing}")
    if extra:
        raise ValidationError(f"Item {i} has unexpected keys: {extra}")

    conf = obj.get("c", "")
    if conf not in ALLOWED_CONFIDENCE:
        raise ValidationError(
            f"Item {i} has invalid confidence value '{conf}'. "
            f"Allowed: {sorted(ALLOWED_CONFIDENCE)}"
        )

    if not isinstance(obj["i"], int):
        raise ValidationError(
            f"Item {i}: i must be an integer, got {obj['i']!r}"
        )

    # Structured Outputs asks for a string. This cleanup keeps downstream CSV
    # and JSON consistent if a compatible/non-strict model emits a number.
    if not isinstance(obj["v"], str):
        obj["v"] = str(obj["v"])

    for key in ("v", "c"):
        if not isinstance(obj[key], str):
            raise ValidationError(f"Item {i}: {key} must be a string, got {obj[key]!r}")

    loc = obj["loc"]
    if not isinstance(loc, list) or not all(isinstance(v, str) for v in loc):
        raise ValidationError(f"Item {i}: loc must be a list of strings, got {loc!r}")
    if valid_location_ids is not None:
        unknown = [v for v in loc if v not in valid_location_ids]
        if unknown:
            raise ValidationError(f"Item {i}: loc contains unknown evidence IDs: {unknown}")

    actual_indices.append(obj["i"])


def validate_chunk_output(
    raw: str,
    expected_indices: list[int],
    *,
    valid_location_ids: set[str] | None = None,
) -> list[dict]:
    """
    Parse and validate a subagent's raw text output.

    Args:
        raw:              Raw string returned by the API.
        expected_indices: The field_index values this chunk should contain.

    Returns:
        Parsed and validated list of extraction dicts.

    Raises:
        ValidationError: With a descriptive message for targeted retry logging.
    """
    data = _parse_response_json(raw)

    if len(data) != len(expected_indices):
        raise ValidationError(
            f"Expected {len(expected_indices)} objects, got {len(data)}.\n"
            f"Expected field indices: {expected_indices}"
        )

    actual_indices: list[int] = []
    for i, obj in enumerate(data):
        _validate_extraction_item(i, obj, actual_indices, valid_location_ids=valid_location_ids)

    if sorted(actual_indices) != sorted(expected_indices):
        raise ValidationError(
            f"Field index mismatch.\n"
            f"  Expected: {sorted(expected_indices)}\n"
            f"  Got:      {sorted(actual_indices)}"
        )

    return data


def reconstruct_fields(
    compact: list[dict],
    field_lookup: dict[int, dict],
    evidence_map: dict[str, dict] | None = None,
) -> list[dict]:
    """Expand compact model output to full extraction dicts using extraction_map metadata."""
    evidence_map = evidence_map or {}
    output: list[dict] = []
    for item in compact:
        loc_ids: list[str] = item.get("loc", [])
        resolved = [evidence_map[eid] for eid in loc_ids if eid in evidence_map]
        evidence_text = "\n".join(x.get("text", "") for x in resolved if x.get("text"))
        output.append(
            {
                "field_index": item["i"],
                "domain_group": field_lookup[item["i"]]["domain_group"],
                "field_name": field_lookup[item["i"]]["field_name"],
                "extracted_value": item["v"],
                "evidence": evidence_text,
                "location": loc_ids,
                "location_metadata": [
                    {
                        "id": x.get("id"),
                        "type": x.get("type"),
                        "section_path": x.get("section_path"),
                        "page": x.get("page"),
                        "coords": x.get("coords"),
                        "xpath": x.get("xpath"),
                        "source_pdf": x.get("source_pdf"),
                    }
                    for x in resolved
                ],
                "confidence": item["c"],
            }
        )
    return output
