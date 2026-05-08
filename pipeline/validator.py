"""Validate model JSON output against the expected extraction schema."""
import json
import re
from typing import Any

from quality_control import QCContext

# Validation constants
ALLOWED_CONFIDENCE = {"h", "m", "l", "nr"}
REQUIRED_KEYS = {"i", "v", "e", "c"}


class ValidationError(Exception):
    """Raised when a subagent's output fails schema validation."""
    pass


def validate_qc_context_input(ctx: object) -> None:
    """Guard: verify ctx is a fully-reconciled QCContext before field extraction."""
    if not isinstance(ctx, QCContext):
        raise TypeError(
            f"pdf_processor input must be a QCContext, got {type(ctx).__name__!r}"
        )
    if ctx.unified is None:
        raise ValueError(
            "QCContext.unified is None — reconciler has not run yet"
        )
    if not isinstance(ctx.unified.document_id, str) or not ctx.unified.document_id:
        raise ValueError(
            f"QCContext.unified.document_id must be a non-empty str, "
            f"got {ctx.unified.document_id!r}"
        )
    if not isinstance(ctx.unified.content, dict):
        raise TypeError(
            f"QCContext.unified.content must be a dict, "
            f"got {type(ctx.unified.content).__name__!r}"
        )
    exact_text = ctx.unified.content.get("exact_text")
    if not isinstance(exact_text, str) or not exact_text.strip():
        raise ValueError(
            "QCContext.unified.content['exact_text'] must be a non-empty string"
        )


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
    try:
        parsed = json.loads(clean_json_string(raw))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"JSON parse failed: {exc}\n"
            f"Raw output (first 500 chars):\n{raw[:500]}"
        ) from exc
    return _unwrap_top_level(parsed)


def _validate_extraction_item(i: int, obj: Any, actual_indices: list[int]) -> None:
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

    for key in ("v", "e", "c"):
        if not isinstance(obj[key], str):
            raise ValidationError(f"Item {i}: {key} must be a string, got {obj[key]!r}")

    actual_indices.append(obj["i"])


def validate_chunk_output(raw: str, expected_indices: list[int]) -> list[dict]:
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
        _validate_extraction_item(i, obj, actual_indices)

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
) -> list[dict]:
    """Expand compact model output to full extraction dicts using extraction_map metadata."""
    return [
        {
            "field_index":     item["i"],
            "domain_group":    field_lookup[item["i"]]["domain_group"],
            "field_name":      field_lookup[item["i"]]["field_name"],
            "extracted_value": item["v"],
            "evidence":        item["e"],
            "confidence":      item["c"],
        }
        for item in compact
    ]
