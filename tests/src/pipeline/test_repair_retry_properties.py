"""
Property-based tests for RepairRetryLoop (Properties 9, 10).

Feature: audit-remediation
Validates: Requirements 5.1, 5.2, 5.5
"""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Module import with mocked heavy dependencies
# ---------------------------------------------------------------------------

_mock_api_client = MagicMock()
_mock_extract_chunk = AsyncMock()
_mock_api_client.extract_chunk = _mock_extract_chunk

with patch.dict(
    sys.modules,
    {
        "agents": MagicMock(),
        "agents.openai": MagicMock(),
        "agents.openai.api_client": _mock_api_client,
    },
):
    from pipeline.pdf_processor import (
        RepairRetryLoop,
        RepairExhaustedError,
        _COMPACT_SCHEMA_FORMAT,
    )
    from pipeline.validator import ValidationError


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for generating malformed JSON strings that will fail parsing
_malformed_json_st = st.one_of(
    # Truncated JSON objects
    st.builds(
        lambda keys: "{" + ", ".join(f'"{k}": ' for k in keys),
        keys=st.lists(st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=8), min_size=1, max_size=4),
    ),
    # Missing closing brackets
    st.builds(
        lambda n: '{"extractions": [' + ", ".join('{"i": 1}' for _ in range(n)),
        n=st.integers(min_value=1, max_value=3),
    ),
    # Random non-JSON text
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P")),
        min_size=1,
        max_size=100,
    ).filter(lambda s: not _is_valid_json(s)),
    # Trailing commas
    st.just('{"extractions": [{"i": 1, "v": "x", "loc": [], "c": "h",}]}'),
    # Unquoted keys
    st.just('{extractions: [{"i": 1}]}'),
)

# Strategy for valid field indices
_field_indices_st = st.lists(
    st.integers(min_value=1, max_value=62),
    min_size=1,
    max_size=5,
    unique=True,
)

# Strategy for confidence values
_confidence_st = st.sampled_from(["h", "m", "l", "nr"])

# Strategy for location IDs
_location_id_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=15,
)


def _is_valid_json(s: str) -> bool:
    """Check if a string is valid JSON."""
    try:
        json.loads(s)
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def _make_valid_response(indices: list[int], loc_ids: list[str] | None = None) -> str:
    """Create a valid JSON response for given field indices."""
    if loc_ids is None:
        loc_ids = ["ev1"]
    extractions = [
        {"i": idx, "v": f"value_{idx}", "loc": loc_ids, "c": "h"}
        for idx in indices
    ]
    return json.dumps({"extractions": extractions})


def _make_schema_invalid_response(indices: list[int], violation: str = "missing_key") -> str:
    """Create JSON that parses but fails schema validation."""
    if violation == "missing_key":
        # Missing "c" key
        extractions = [
            {"i": idx, "v": f"value_{idx}", "loc": ["ev1"]}
            for idx in indices
        ]
    elif violation == "invalid_confidence":
        # Invalid confidence value
        extractions = [
            {"i": idx, "v": f"value_{idx}", "loc": ["ev1"], "c": "INVALID"}
            for idx in indices
        ]
    elif violation == "wrong_indices":
        # Wrong field indices (out of expected range)
        extractions = [
            {"i": idx + 1000, "v": f"value_{idx}", "loc": ["ev1"], "c": "h"}
            for idx in indices
        ]
    else:
        extractions = [
            {"i": idx, "v": f"value_{idx}", "loc": ["ev1"]}
            for idx in indices
        ]
    return json.dumps({"extractions": extractions})


# ---------------------------------------------------------------------------
# Property 9: Repair prompt includes error context
# ---------------------------------------------------------------------------


@given(malformed=_malformed_json_st, indices=_field_indices_st)
@settings(max_examples=100)
def test_property_9_parse_error_prompt_contains_error_message(malformed, indices):
    """For any malformed LLM response that fails JSON parsing, the repair prompt
    SHALL contain the parse error message and the Compact_Schema format specification.

    **Validates: Requirements 5.1**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    # Attempt to parse the malformed string to get the actual error
    try:
        json.loads(malformed)
        # If it somehow parses, skip this example
        assume(False)
    except json.JSONDecodeError as parse_error:
        # Build repair prompt with the parse error
        prompt = loop._build_repair_prompt(parse_error, indices)

        # Prompt SHALL contain the parse error message
        assert "JSON PARSE ERROR" in prompt
        # The error message text should appear in the prompt
        assert str(parse_error) in prompt or parse_error.msg in prompt

        # Prompt SHALL contain the Compact_Schema format specification
        assert _COMPACT_SCHEMA_FORMAT in prompt


@given(malformed=_malformed_json_st, indices=_field_indices_st)
@settings(max_examples=100)
def test_property_9_parse_error_prompt_contains_compact_schema_keys(malformed, indices):
    """For any malformed LLM response that fails JSON parsing, the repair prompt
    SHALL contain all required Compact_Schema keys ("i", "v", "loc", "c").

    **Validates: Requirements 5.1**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    try:
        json.loads(malformed)
        assume(False)
    except json.JSONDecodeError as parse_error:
        prompt = loop._build_repair_prompt(parse_error, indices)

        # All compact schema keys must be mentioned
        assert '"i"' in prompt
        assert '"v"' in prompt
        assert '"loc"' in prompt
        assert '"c"' in prompt


@given(
    indices=_field_indices_st,
    violation=st.sampled_from(["missing_key", "invalid_confidence", "wrong_indices"]),
)
@settings(max_examples=100)
def test_property_9_schema_validation_prompt_lists_failures(indices, violation):
    """For any response that parses as JSON but fails schema validation, the
    repair prompt SHALL list the specific validation failures.

    **Validates: Requirements 5.2**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    # Construct representative ValidationError messages for each violation type
    if violation == "missing_key":
        error_msg = f"Item 0 is missing keys: {{'c'}}"
    elif violation == "invalid_confidence":
        error_msg = (
            f"Item 0 has invalid confidence value 'INVALID'. "
            f"Allowed: ['h', 'l', 'm', 'nr']"
        )
    else:  # wrong_indices
        error_msg = (
            f"Field index mismatch.\n"
            f"  Expected: {sorted(indices)}\n"
            f"  Got:      {sorted(i + 1000 for i in indices)}"
        )

    val_error = ValidationError(error_msg)
    prompt = loop._build_repair_prompt(val_error, indices)

    # Prompt SHALL contain "SCHEMA VALIDATION ERROR"
    assert "SCHEMA VALIDATION ERROR" in prompt

    # Prompt SHALL list the specific validation failure message
    assert error_msg in prompt

    # Prompt SHALL include the required format
    assert "REQUIRED FORMAT" in prompt
    assert _COMPACT_SCHEMA_FORMAT in prompt


@given(indices=_field_indices_st)
@settings(max_examples=100)
def test_property_9_schema_validation_prompt_includes_index_range_on_mismatch(indices):
    """For any response with out-of-range field indexes, the repair prompt
    SHALL specify the valid field-index range.

    **Validates: Requirements 5.2**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    # Create a validation error about field index mismatch
    error = ValidationError(
        f"Field index mismatch.\n"
        f"  Expected: {sorted(indices)}\n"
        f"  Got:      {sorted(i + 1000 for i in indices)}"
    )

    prompt = loop._build_repair_prompt(error, indices)

    # Prompt SHALL specify the valid range
    idx_min = min(indices)
    idx_max = max(indices)
    assert f"VALID FIELD INDEX RANGE: [{idx_min}, {idx_max}]" in prompt
    assert f"Expected field indices: {sorted(indices)}" in prompt


@given(indices=_field_indices_st)
@settings(max_examples=100)
def test_property_9_wrapped_parse_error_detected_correctly(indices):
    """For any ValidationError that wraps a JSONDecodeError via __cause__,
    the repair prompt SHALL treat it as a parse error (include JSON PARSE ERROR).

    **Validates: Requirements 5.1**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    # Create a ValidationError wrapping a JSONDecodeError (as done in validator.py)
    inner_error = json.JSONDecodeError("Expecting value", "doc", 0)
    wrapped_error = ValidationError(f"JSON parse failed: {inner_error}")
    wrapped_error.__cause__ = inner_error

    prompt = loop._build_repair_prompt(wrapped_error, indices)

    # Should be treated as a parse error
    assert "JSON PARSE ERROR" in prompt
    assert _COMPACT_SCHEMA_FORMAT in prompt


# ---------------------------------------------------------------------------
# Property 10: Successful repair yields only valid output
# ---------------------------------------------------------------------------


@given(
    indices=_field_indices_st,
    confidence=_confidence_st,
)
@settings(max_examples=100)
def test_property_10_repair_returns_only_valid_output(indices, confidence):
    """For any chunk extraction where the initial response fails but a repair
    retry succeeds, the final returned result SHALL contain only the repaired
    valid output. The original malformed response SHALL NOT appear in the
    returned data.

    **Validates: Requirements 5.5**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    # Malformed initial response
    malformed = '{"extractions": [{"broken": true'

    # Valid repaired response
    valid_extractions = [
        {"i": idx, "v": f"repaired_{idx}", "loc": ["ev1"], "c": confidence}
        for idx in indices
    ]
    valid_response = json.dumps({"extractions": valid_extractions})

    mock_extract = AsyncMock(side_effect=[malformed, valid_response])
    with patch.dict(
        sys.modules,
        {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
    ):
        result = asyncio.run(loop.extract_with_repair(
            chunk_num=1,
            source="test_source",
            fields=[{"field_index": i} for i in indices],
            semaphore=asyncio.Semaphore(5),
            valid_location_ids={"ev1"},
            expected_indices=indices,
            pdf_name="test_pdf",
        ))

    # Result SHALL contain only the repaired valid output
    assert len(result) == len(indices)
    assert sorted(item["i"] for item in result) == sorted(indices)

    # All values should be from the repaired response
    for item in result:
        assert item["v"] == f"repaired_{item['i']}"
        assert item["c"] == confidence

    # The original malformed response SHALL NOT appear in the returned data
    result_str = json.dumps(result)
    assert "broken" not in result_str


@given(
    indices=_field_indices_st,
    loc_ids=st.lists(_location_id_st, min_size=1, max_size=3, unique=True),
)
@settings(max_examples=100)
def test_property_10_repair_after_schema_error_yields_valid_only(indices, loc_ids):
    """For any chunk extraction where the initial response fails schema validation
    but a repair retry succeeds, the final result SHALL contain only the repaired
    valid output with correct structure.

    **Validates: Requirements 5.5**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    # Initial response that parses but fails validation (missing "c" key)
    invalid_extractions = [
        {"i": idx, "v": f"bad_{idx}", "loc": loc_ids}
        for idx in indices
    ]
    invalid_response = json.dumps({"extractions": invalid_extractions})

    # Valid repaired response
    valid_extractions = [
        {"i": idx, "v": f"good_{idx}", "loc": loc_ids, "c": "m"}
        for idx in indices
    ]
    valid_response = json.dumps({"extractions": valid_extractions})

    mock_extract = AsyncMock(side_effect=[invalid_response, valid_response])
    with patch.dict(
        sys.modules,
        {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
    ):
        result = asyncio.run(loop.extract_with_repair(
            chunk_num=1,
            source="test_source",
            fields=[{"field_index": i} for i in indices],
            semaphore=asyncio.Semaphore(5),
            valid_location_ids=set(loc_ids),
            expected_indices=indices,
            pdf_name="test_pdf",
        ))

    # Result SHALL contain only the repaired valid output
    assert len(result) == len(indices)
    for item in result:
        # Values from the repaired response, not the original
        assert item["v"] == f"good_{item['i']}"
        assert item["c"] == "m"
        assert item["loc"] == loc_ids

    # Original bad values SHALL NOT appear
    for item in result:
        assert "bad_" not in item["v"]


@given(
    indices=_field_indices_st,
    num_failures=st.integers(min_value=1, max_value=2),
)
@settings(max_examples=100)
def test_property_10_repair_after_multiple_failures_yields_valid_only(indices, num_failures):
    """For any chunk extraction that fails multiple times before succeeding,
    the final result SHALL contain only the last successful repaired output.

    **Validates: Requirements 5.5**
    """
    assume(num_failures <= 2)  # max_repair_attempts=2

    loop = RepairRetryLoop(max_repair_attempts=2)

    # Generate multiple malformed responses followed by a valid one
    malformed_responses = [
        f'{{"extractions": [{{"i": 999, "v": "attempt_{attempt}"}}]}}'
        for attempt in range(num_failures)
    ]
    valid_extractions = [
        {"i": idx, "v": f"final_{idx}", "loc": ["ev1"], "c": "h"}
        for idx in indices
    ]
    valid_response = json.dumps({"extractions": valid_extractions})

    all_responses = malformed_responses + [valid_response]
    mock_extract = AsyncMock(side_effect=all_responses)

    with patch.dict(
        sys.modules,
        {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
    ):
        result = asyncio.run(loop.extract_with_repair(
            chunk_num=1,
            source="test_source",
            fields=[{"field_index": i} for i in indices],
            semaphore=asyncio.Semaphore(5),
            valid_location_ids={"ev1"},
            expected_indices=indices,
            pdf_name="test_pdf",
        ))

    # Result SHALL contain only the final valid output
    assert len(result) == len(indices)
    for item in result:
        assert item["v"] == f"final_{item['i']}"

    # None of the intermediate attempt values should appear
    result_str = json.dumps(result)
    for attempt in range(num_failures):
        assert f"attempt_{attempt}" not in result_str
