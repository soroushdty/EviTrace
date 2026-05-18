"""Validate model JSON output against the expected extraction schema."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import jsonschema

from utils.logging_utils import get_logger

logger = get_logger(__name__)

# Validation constants
ALLOWED_CONFIDENCE = {"h", "m", "l", "nr"}
REQUIRED_KEYS = {"i", "v", "loc", "c"}


class ValidationError(Exception):
    """Raised when a subagent's output fails schema validation."""
    pass


def clean_json_string(raw: str) -> str:
    """Strip markdown code fences and surrounding whitespace.

    Robust to three wrapper styles the models occasionally emit despite the
    strict JSON-schema response format:

    1. Plain JSON (the happy path): returned unchanged.
    2. Fenced JSON ("```json\\n{...}\\n```"): fences stripped.
    3. Fenced JSON with prose around the fences ("here is the result:
       ```json\\n{...}\\n```\\nlet me know if..."): the fenced block is
       extracted.
    4. Bare JSON with leading/trailing prose but no fences: the outermost
       ``{...}`` span is extracted.

    We try each strategy in order and only fall back to the next one when the
    previous would drop content. This preserves content-integrity under every
    emitter quirk we have seen from GPT-class models.
    """
    text = raw.strip()
    if not text:
        return text

    # 1. Happy path: if the string already looks like a JSON object/array at
    #    the start and end, return it as-is.
    if text.startswith(("{", "[")) and text.endswith(("}", "]")):
        return text

    # 2 + 3. Fenced block, possibly with prose around the fences. Pull the
    #        first ``` ... ``` block (optionally tagged "json").
    fence_match = re.search(
        r"```(?:json|JSON)?\s*\n?(.*?)```",
        text,
        flags=re.DOTALL,
    )
    if fence_match:
        inner = fence_match.group(1).strip()
        if inner:
            return inner

    # 4. No fences but prose bracketing a JSON value -- extract the first
    #    balanced {...} or [...] span. This is the weakest recovery so we
    #    only try it after the simpler paths failed.
    for opener, closer in (("{", "}"), ("[", "]")):
        first = text.find(opener)
        last = text.rfind(closer)
        if first != -1 and last != -1 and first < last:
            return text[first : last + 1].strip()

    return text


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
    valid_location_ids: Optional[set] = None,
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
    valid_location_ids: Optional[set] = None,
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


# ---------------------------------------------------------------------------
# Final Output Schema Validation (Requirement 1)
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Result of schema validation with structured error details."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)


class FinalOutputValidator:
    """Validates merged field lists against configs/final_output_schema.json.

    Loads the JSON Schema (Draft 7) once at construction time and reuses it
    across all subsequent ``validate()`` calls.
    """

    def __init__(self, schema_path: str = "configs/final_output_schema.json"):
        schema_file = Path(schema_path)
        if not schema_file.is_absolute():
            # Resolve relative to project root (two levels up from this file)
            project_root = Path(__file__).resolve().parent.parent.parent
            schema_file = project_root / schema_path
        with open(schema_file, "r", encoding="utf-8") as f:
            self._schema: dict = json.load(f)
        self._validator = jsonschema.Draft7Validator(self._schema)

    def validate(self, fields: list[dict]) -> ValidationResult:
        """Validate *fields* against the Final Output Schema.

        Returns a :class:`ValidationResult` with ``is_valid=True`` when the
        data conforms, or ``is_valid=False`` with human-readable error strings
        that include field_index, field_name (when available), and JSON path.
        """
        errors: list[str] = []
        for error in self._validator.iter_errors(fields):
            errors.append(self.format_error(error, fields))
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)

    def format_error(
        self,
        error: jsonschema.ValidationError,
        fields: list[dict] | None = None,
    ) -> str:
        """Format a single validation error with contextual identifiers.

        The message includes:
        - ``field_index`` of the offending record (if determinable)
        - ``field_name`` of the offending record (if present)
        - The JSON path where the error occurred
        """
        json_path = _json_path_from_error(error)
        field_index: int | None = None
        field_name: str | None = None

        # Try to extract field_index and field_name from the error context.
        # The path deque contains the traversal into the array, e.g. [0, 'confidence'].
        path_parts = list(error.absolute_path)
        if path_parts and isinstance(path_parts[0], int):
            idx = path_parts[0]
            record: dict | None = None

            # First try: look up from the original fields list passed in.
            if fields is not None:
                try:
                    record = fields[idx]
                except (IndexError, TypeError):
                    pass

            # Second try: if error.instance is the record dict itself
            # (happens for top-level item errors like missing required keys).
            if record is None and isinstance(error.instance, dict):
                record = error.instance

            if isinstance(record, dict):
                field_index = record.get("field_index")
                field_name = record.get("field_name")

        parts: list[str] = []
        if field_index is not None:
            parts.append(f"field_index={field_index}")
        if field_name:
            parts.append(f"field_name={field_name!r}")
        parts.append(f"path={json_path}")
        parts.append(str(error.message))

        return " | ".join(parts)


def _json_path_from_error(error: jsonschema.ValidationError) -> str:
    """Build a JSONPath-like string from the error's absolute_path."""
    parts: list[str] = []
    for segment in error.absolute_path:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(f".{segment}" if parts else segment)
    return "$" + "".join(parts) if parts else "$"
