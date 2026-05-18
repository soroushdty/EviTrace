"""Unit tests for RepairRetryLoop in pdf_processor.py.

Tests cover:
- Repair prompt construction for JSON parse errors
- Repair prompt construction for schema validation errors
- Repair prompt includes valid field-index range for out-of-range errors
- Exhaustion metadata structure
- Recovery from malformed JSON followed by valid response
"""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import with mocked heavy dependencies
# ---------------------------------------------------------------------------

# Stub out agents.openai.api_client before importing pdf_processor
_mock_api_client = MagicMock()
_mock_extract_chunk = AsyncMock()
_mock_api_client.extract_chunk = _mock_extract_chunk

# Ensure the module is importable
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
# _build_repair_prompt tests
# ---------------------------------------------------------------------------


class TestBuildRepairPrompt:
    """Tests for RepairRetryLoop._build_repair_prompt()."""

    def setup_method(self):
        self.loop = RepairRetryLoop(max_repair_attempts=2)

    def test_json_parse_error_includes_error_message(self):
        """JSON parse errors include the error message in the repair prompt."""
        error = json.JSONDecodeError("Expecting value", "doc", 0)
        prompt = self.loop._build_repair_prompt(error, [1, 2, 3])

        assert "JSON PARSE ERROR" in prompt
        assert "Expecting value" in prompt

    def test_json_parse_error_includes_compact_schema(self):
        """JSON parse errors include the Compact_Schema format."""
        error = json.JSONDecodeError("Unexpected token", "doc", 5)
        prompt = self.loop._build_repair_prompt(error, [1, 2, 3])

        assert "REQUIRED FORMAT" in prompt
        assert '"i"' in prompt
        assert '"v"' in prompt
        assert '"loc"' in prompt
        assert '"c"' in prompt

    def test_schema_validation_error_includes_failures(self):
        """Schema validation errors list specific failures."""
        error = ValidationError("Item 0 is missing keys: {'v'}")
        prompt = self.loop._build_repair_prompt(error, [5, 6, 7])

        assert "SCHEMA VALIDATION ERROR" in prompt
        assert "missing keys" in prompt
        assert "{'v'}" in prompt

    def test_schema_validation_error_includes_valid_range_for_index_mismatch(self):
        """Out-of-range field indexes specify the valid range."""
        error = ValidationError(
            "Field index mismatch.\n"
            "  Expected: [3, 4, 5]\n"
            "  Got:      [1, 2, 3]"
        )
        prompt = self.loop._build_repair_prompt(error, [3, 4, 5])

        assert "VALID FIELD INDEX RANGE: [3, 5]" in prompt
        assert "Expected field indices: [3, 4, 5]" in prompt

    def test_schema_validation_error_includes_valid_range_for_index_keyword(self):
        """Errors mentioning 'index' trigger valid range display."""
        error = ValidationError("Item 2: field index 99 is out of range")
        prompt = self.loop._build_repair_prompt(error, [10, 11, 12])

        assert "VALID FIELD INDEX RANGE: [10, 12]" in prompt

    def test_schema_validation_invalid_confidence(self):
        """Invalid confidence values are reported in the repair prompt."""
        error = ValidationError(
            "Item 1 has invalid confidence value 'high'. Allowed: ['h', 'l', 'm', 'nr']"
        )
        prompt = self.loop._build_repair_prompt(error, [1, 2])

        assert "SCHEMA VALIDATION ERROR" in prompt
        assert "invalid confidence" in prompt
        assert "REQUIRED FORMAT" in prompt

    def test_repair_prompt_always_includes_format(self):
        """Every repair prompt includes the required format specification."""
        # JSON parse error
        prompt1 = self.loop._build_repair_prompt(
            json.JSONDecodeError("err", "doc", 0), [1]
        )
        assert "REQUIRED FORMAT" in prompt1

        # Schema validation error
        prompt2 = self.loop._build_repair_prompt(
            ValidationError("some error"), [1]
        )
        assert "REQUIRED FORMAT" in prompt2


# ---------------------------------------------------------------------------
# extract_with_repair tests (async via asyncio.run)
# ---------------------------------------------------------------------------


class TestExtractWithRepair:
    """Tests for RepairRetryLoop.extract_with_repair()."""

    def _make_valid_response(self, indices: list[int]) -> str:
        """Create a valid JSON response for given field indices."""
        extractions = [
            {"i": idx, "v": f"value_{idx}", "loc": ["ev1"], "c": "h"}
            for idx in indices
        ]
        return json.dumps({"extractions": extractions})

    def _make_malformed_json(self) -> str:
        """Create a malformed JSON string."""
        return '{"extractions": [{"i": 1, "v": "test", "loc": ["ev1"], "c": "h"'

    def _make_invalid_schema_response(self, indices: list[int]) -> str:
        """Create JSON that parses but fails schema validation (missing key)."""
        extractions = [
            {"i": idx, "v": f"value_{idx}", "loc": ["ev1"]}  # missing "c"
            for idx in indices
        ]
        return json.dumps({"extractions": extractions})

    def test_success_on_first_try(self):
        """When initial extraction is valid, no repair is needed."""
        loop = RepairRetryLoop(max_repair_attempts=2)
        indices = [1, 2, 3]
        valid_response = self._make_valid_response(indices)

        mock_extract = AsyncMock(return_value=valid_response)
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

        assert len(result) == 3
        assert all(item["i"] in indices for item in result)
        # Only called once — no repair needed
        assert mock_extract.call_count == 1

    def test_recovery_after_malformed_json(self):
        """Repair succeeds after initial malformed JSON response."""
        loop = RepairRetryLoop(max_repair_attempts=2)
        indices = [1, 2]
        malformed = self._make_malformed_json()
        valid = self._make_valid_response(indices)

        mock_extract = AsyncMock(side_effect=[malformed, valid])
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

        assert len(result) == 2
        # Called twice: initial + 1 repair
        assert mock_extract.call_count == 2
        # Second call should have repair_prompt
        _, kwargs = mock_extract.call_args
        assert kwargs.get("repair_prompt") is not None

    def test_recovery_after_schema_validation_error(self):
        """Repair succeeds after initial schema validation failure."""
        loop = RepairRetryLoop(max_repair_attempts=2)
        indices = [5, 6]
        invalid_schema = self._make_invalid_schema_response(indices)
        valid = self._make_valid_response(indices)

        mock_extract = AsyncMock(side_effect=[invalid_schema, valid])
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            result = asyncio.run(loop.extract_with_repair(
                chunk_num=2,
                source="test_source",
                fields=[{"field_index": i} for i in indices],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        assert len(result) == 2
        assert mock_extract.call_count == 2

    def test_exhaustion_raises_with_metadata(self):
        """After max_repair_attempts, raises RepairExhaustedError with metadata."""
        loop = RepairRetryLoop(max_repair_attempts=2)
        indices = [1, 2]
        malformed = self._make_malformed_json()

        mock_extract = AsyncMock(return_value=malformed)
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            with pytest.raises(RepairExhaustedError) as exc_info:
                asyncio.run(loop.extract_with_repair(
                    chunk_num=3,
                    source="test_source",
                    fields=[{"field_index": i} for i in indices],
                    semaphore=asyncio.Semaphore(5),
                    valid_location_ids={"ev1"},
                    expected_indices=indices,
                    pdf_name="test_pdf",
                ))

        metadata = exc_info.value.metadata
        assert metadata["status"] == "failed_validation"
        assert metadata["chunk"] == 3
        assert metadata["error_type"] == "parse"
        assert metadata["attempts"] == 2
        assert "last_error" in metadata
        assert len(metadata["last_error"]) > 0

    def test_exhaustion_schema_error_type(self):
        """Exhaustion with schema errors records error_type='schema'."""
        loop = RepairRetryLoop(max_repair_attempts=1)
        indices = [1, 2]
        invalid_schema = self._make_invalid_schema_response(indices)

        mock_extract = AsyncMock(return_value=invalid_schema)
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            with pytest.raises(RepairExhaustedError) as exc_info:
                asyncio.run(loop.extract_with_repair(
                    chunk_num=1,
                    source="test_source",
                    fields=[{"field_index": i} for i in indices],
                    semaphore=asyncio.Semaphore(5),
                    valid_location_ids={"ev1"},
                    expected_indices=indices,
                    pdf_name="test_pdf",
                ))

        metadata = exc_info.value.metadata
        assert metadata["error_type"] == "schema"
        assert metadata["attempts"] == 1

    def test_repair_returns_only_valid_output(self):
        """Successful repair returns only the repaired valid output, not the original."""
        loop = RepairRetryLoop(max_repair_attempts=2)
        indices = [10, 11]
        malformed = '{"extractions": [{"i": 10, "v": "BAD", "loc": [], "c": "invalid_conf"}]}'
        valid = self._make_valid_response(indices)

        mock_extract = AsyncMock(side_effect=[malformed, valid])
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

        # Result should be the valid repaired output
        assert all(item["c"] == "h" for item in result)
        assert sorted(item["i"] for item in result) == indices

    def test_max_repair_attempts_configurable(self):
        """max_repair_attempts controls how many retries are attempted."""
        loop = RepairRetryLoop(max_repair_attempts=3)
        indices = [1]
        malformed = self._make_malformed_json()

        mock_extract = AsyncMock(return_value=malformed)
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            with pytest.raises(RepairExhaustedError) as exc_info:
                asyncio.run(loop.extract_with_repair(
                    chunk_num=1,
                    source="test_source",
                    fields=[{"field_index": 1}],
                    semaphore=asyncio.Semaphore(5),
                    valid_location_ids={"ev1"},
                    expected_indices=indices,
                    pdf_name="test_pdf",
                ))

        # 1 initial + 3 repair attempts = 4 total calls
        assert mock_extract.call_count == 4
        assert exc_info.value.metadata["attempts"] == 3
