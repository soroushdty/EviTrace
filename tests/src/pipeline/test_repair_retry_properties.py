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
        token_budget,
    )
    from pipeline.validator import ValidationError
    # NOTE: token_budget must be imported here, inside this same
    # patch.dict(sys.modules, ...) block -- see the identical note in
    # test_repair_retry.py for why a later top-level import would silently
    # produce a second, distinct module (and distinct exception classes).


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


# ---------------------------------------------------------------------------
# Property 14: Repair prompt is smaller than original chunk prompt
#
# Feature: token-efficient-extraction, Property 14: For any chunk validation
# failure, the constructed Repair_Prompt's estimated token count (chars/4)
# SHALL be strictly less than the original chunk prompt's estimated token
# count.
# Validates: Requirements 6.2
# ---------------------------------------------------------------------------


# Evidence package text. `min_size=0` deliberately includes the
# degenerate/near-empty evidence case (e.g. build_paper_evidence_package()'s
# `{"paper_id":"","evidence":[]}` fallback for a failed/empty evidence
# bundle, src/pipeline/evidence_index.py -- build_paper_evidence_package),
# not just large realistic evidence packages: a prior version of this
# strategy used `min_size=200`, which happened to stay just above the
# region where the FIXED repair-prompt template overhead (error message +
# "REQUIRED FORMAT" + _COMPACT_SCHEMA_FORMAT) alone could meet or exceed a
# small original_prompt_chars, masking a real bug (see
# test_property_14_repair_prompt_smaller_than_original_tiny_evidence below
# for the concrete reproduction that first caught it).
_evidence_paragraph_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Zs")),
    min_size=0,
    max_size=2000,
)

_raw_response_st = st.one_of(
    st.none(),
    st.text(min_size=0, max_size=5000),
)


@given(
    evidence_text=_evidence_paragraph_st,
    indices=_field_indices_st,
    raw_response=_raw_response_st,
    violation=st.sampled_from(["parse", "missing_key", "invalid_confidence", "wrong_indices"]),
)
@settings(max_examples=100)
def test_property_14_repair_prompt_smaller_than_original(
    evidence_text, indices, raw_response, violation,
):
    """For any chunk validation failure, the repair prompt's estimated token
    count SHALL be strictly less than the original chunk prompt's.

    The "original chunk prompt" is modeled as system prompt + evidence text
    + field definitions JSON -- the same three sections a real extract_chunk
    dispatch sends (see pdf_processor._check_and_mitigate_budget), which is
    always at least as large as what pdf_processor actually dispatches.

    **Validates: Requirements 6.2**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    fields = [{"field_index": i, "field_name": f"Field {i}", "definition": "x" * 50} for i in indices]
    original_prompt_text = (
        RepairRetryLoop._get_system_prompt_text()
        + evidence_text
        + json.dumps(fields)
    )
    original_tokens = token_budget.estimate_tokens(original_prompt_text)

    if violation == "parse":
        error = json.JSONDecodeError("Expecting value", "doc", 0)
    elif violation == "missing_key":
        error = ValidationError("Item 0 is missing keys: {'c'}")
    elif violation == "invalid_confidence":
        error = ValidationError(
            "Item 0 has invalid confidence value 'bad'. Allowed: ['h', 'l', 'm', 'nr']"
        )
    else:  # wrong_indices
        error = ValidationError(
            f"Field index mismatch.\n"
            f"  Expected: {sorted(indices)}\n"
            f"  Got:      {sorted(i + 1000 for i in indices)}"
        )

    repair_prompt = loop._build_repair_prompt(
        error, indices, raw_response=raw_response,
        original_prompt_chars=len(original_prompt_text),
    )
    repair_tokens = token_budget.estimate_tokens(repair_prompt)

    assert repair_tokens < original_tokens, (
        f"repair_tokens={repair_tokens} not strictly less than "
        f"original_tokens={original_tokens} (violation={violation}, "
        f"raw_response_len={len(raw_response) if raw_response else 0})"
    )


def test_property_14_repair_prompt_smaller_than_original_tiny_evidence():
    """Example-based regression test locking down the reviewer's exact
    reproduction of the task 8.2 review rejection: a realistic 297-char
    system prompt (agents.openai.prompts.get_system_prompt(), unmocked),
    a tiny (20-char) evidence "source" -- matching
    build_paper_evidence_package()'s degenerate empty-evidence fallback
    (`{"paper_id":"","evidence":[]}`, src/pipeline/evidence_index.py) --
    one tiny field definition, and a LARGE (3000-char) malformed raw
    response.

    Before the fix: original_prompt_chars=375 (93 tokens) but the
    constructed repair prompt came out to 411 chars (102 tokens) --
    LARGER, not smaller, violating Req 6.2 / Property 14. Root cause: the
    fixed repair-prompt template overhead (error message + "REQUIRED
    FORMAT" + the verbose _COMPACT_SCHEMA_FORMAT block) is itself ~350+
    chars, independent of the raw-response fragment size, so bounding only
    the fragment (the pre-fix approach) could not help once the original
    prompt was this small.

    **Validates: Requirements 6.2**
    """
    loop = RepairRetryLoop(max_repair_attempts=2)

    system_text = RepairRetryLoop._get_system_prompt_text()
    assert len(system_text) < 400  # sanity-check this is the small real prompt

    source = "x" * 20  # mirrors build_paper_evidence_package()'s degenerate case
    fields = [{"field_index": 1, "field_name": "F", "definition": "d"}]
    field_definitions_text = json.dumps(sorted(fields, key=lambda f: f.get("field_index", 0)))
    original_prompt_text = system_text + source + field_definitions_text
    original_prompt_chars = len(original_prompt_text)
    original_tokens = token_budget.estimate_tokens(original_prompt_text)

    error = ValidationError("Item 0 is missing keys: {'c'}")
    raw_response = "y" * 3000

    repair_prompt = loop._build_repair_prompt(
        error, [1], raw_response=raw_response,
        original_prompt_chars=original_prompt_chars,
    )
    repair_tokens = token_budget.estimate_tokens(repair_prompt)

    assert repair_tokens < original_tokens, (
        f"repair_tokens={repair_tokens} not strictly less than "
        f"original_tokens={original_tokens} (original_prompt_chars="
        f"{original_prompt_chars}, repair_prompt_chars={len(repair_prompt)})"
    )
    # The essential validation-error information must still be present even
    # in the shrunk/terse repair prompt.
    assert "missing keys" in repair_prompt
