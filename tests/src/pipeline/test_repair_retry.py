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
        _MAX_REPAIR_FRAGMENT_CHARS,
        token_budget,
    )
    from pipeline.validator import ValidationError

# NOTE: `token_budget` (and any other module reached only via `pipeline.*`)
# MUST be imported here, inside this same patch.dict(sys.modules, ...) block,
# not later at module level. `unittest.mock.patch.dict.__exit__` restores
# `sys.modules` to a full snapshot taken at __enter__ time (it clears and
# rebuilds the *entire* dict, not just the keys it touched) -- so anything
# first imported inside this block (pipeline, pipeline.pdf_processor,
# pipeline.token_budget, ...) is evicted from sys.modules the moment this
# `with` exits. A later top-level `from pipeline import token_budget` would
# therefore trigger a *second*, independent import producing a distinct
# `TokenBudgetExceededError` class object -- silently breaking
# `pytest.raises(token_budget.TokenBudgetExceededError)` / isinstance checks
# against exceptions raised from inside RepairRetryLoop (which holds a
# reference to the *first* import's module).


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


# ---------------------------------------------------------------------------
# _build_repair_prompt: invalid-output fragment (Requirement 6.1)
# ---------------------------------------------------------------------------


class TestBuildRepairPromptRawResponse:
    """Tests for the optional raw_response fragment in _build_repair_prompt()."""

    def setup_method(self):
        self.loop = RepairRetryLoop(max_repair_attempts=2)

    def test_raw_response_omitted_by_default(self):
        """Without raw_response, no invalid-output section is added (backward compatible)."""
        error = json.JSONDecodeError("Expecting value", "doc", 0)
        prompt = self.loop._build_repair_prompt(error, [1, 2, 3])

        assert "INVALID OUTPUT" not in prompt

    def test_raw_response_included_when_provided(self):
        """Requirement 6.1: the invalid output fragment that failed validation
        is included in the repair prompt when raw_response is given."""
        error = json.JSONDecodeError("Expecting value", "doc", 0)
        bad_output = '{"extractions": [{"i": 1, "v": "broken"'
        prompt = self.loop._build_repair_prompt(error, [1, 2, 3], raw_response=bad_output)

        assert "INVALID OUTPUT THAT FAILED VALIDATION" in prompt
        assert bad_output in prompt

    def test_raw_response_truncated_when_large(self):
        """Requirement 6.2 / Property 14: a large invalid-output fragment is
        truncated so the repair prompt doesn't balloon in size."""
        error = ValidationError("Item 0 is missing keys: {'c'}")
        huge_output = "x" * (_MAX_REPAIR_FRAGMENT_CHARS * 5)
        prompt = self.loop._build_repair_prompt(error, [1, 2, 3], raw_response=huge_output)

        assert huge_output not in prompt
        assert "[truncated]" in prompt
        # The fragment portion of the prompt must not exceed the configured cap.
        fragment_start = prompt.index("INVALID OUTPUT THAT FAILED VALIDATION:") + len(
            "INVALID OUTPUT THAT FAILED VALIDATION:\n"
        )
        fragment = prompt[fragment_start:]
        assert len(fragment) <= _MAX_REPAIR_FRAGMENT_CHARS + len("... [truncated]")


# ---------------------------------------------------------------------------
# extract_with_repair: telemetry threading (Requirement 6.5)
# ---------------------------------------------------------------------------


class TestRepairTelemetry:
    """Tests that extract_with_repair threads collector/stage/repair_attempt/
    error_type into extract_chunk calls (Requirement 6.5)."""

    def test_no_collector_by_default(self):
        """Default collector=None is passed through unchanged -- no telemetry."""
        loop = RepairRetryLoop(max_repair_attempts=2)
        indices = [1, 2]
        valid_response = json.dumps(
            {"extractions": [{"i": i, "v": f"v{i}", "loc": [], "c": "h"} for i in indices]}
        )
        mock_extract = AsyncMock(return_value=valid_response)
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            asyncio.run(loop.extract_with_repair(
                chunk_num=1,
                source="test_source",
                fields=[{"field_index": i} for i in indices],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        _, kwargs = mock_extract.call_args
        assert kwargs.get("collector") is None

    def test_collector_passed_to_every_dispatch(self):
        """A configured collector is passed to both the initial dispatch and
        every repair-attempt dispatch."""
        collector = MagicMock()
        loop = RepairRetryLoop(max_repair_attempts=2, collector=collector)
        indices = [1, 2]
        malformed = '{"extractions": [{"broken": true'
        valid_response = json.dumps(
            {"extractions": [{"i": i, "v": f"v{i}", "loc": [], "c": "h"} for i in indices]}
        )
        mock_extract = AsyncMock(side_effect=[malformed, valid_response])
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            asyncio.run(loop.extract_with_repair(
                chunk_num=1,
                source="test_source",
                fields=[{"field_index": i} for i in indices],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        assert mock_extract.call_count == 2
        for call in mock_extract.call_args_list:
            assert call.kwargs.get("collector") is collector

    def test_repair_dispatch_includes_stage_attempt_and_error_type(self):
        """Requirement 6.5: repair dispatches are labeled stage='validation_repair'
        with a 1-based attempt number and the error_type that triggered repair."""
        loop = RepairRetryLoop(max_repair_attempts=2)
        indices = [1, 2]
        malformed = '{"extractions": [{"broken": true'  # -> parse error
        valid_response = json.dumps(
            {"extractions": [{"i": i, "v": f"v{i}", "loc": [], "c": "h"} for i in indices]}
        )
        mock_extract = AsyncMock(side_effect=[malformed, valid_response])
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            asyncio.run(loop.extract_with_repair(
                chunk_num=1,
                source="test_source",
                fields=[{"field_index": i} for i in indices],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        assert mock_extract.call_count == 2
        # First call: initial attempt -- no repair-specific metadata.
        first_kwargs = mock_extract.call_args_list[0].kwargs
        assert first_kwargs.get("repair_attempt") is None
        assert first_kwargs.get("error_type") is None
        # Second call: repair attempt 1, labeled with the parse error type.
        second_kwargs = mock_extract.call_args_list[1].kwargs
        assert second_kwargs.get("stage") == "validation_repair"
        assert second_kwargs.get("repair_attempt") == 1
        assert second_kwargs.get("error_type") == "parse"

    def test_repair_dispatch_error_type_schema(self):
        """Schema validation failures label the repair dispatch error_type='schema'."""
        loop = RepairRetryLoop(max_repair_attempts=1)
        indices = [5, 6]
        invalid_schema = json.dumps(
            {"extractions": [{"i": i, "v": f"v{i}", "loc": []} for i in indices]}  # missing "c"
        )
        valid_response = json.dumps(
            {"extractions": [{"i": i, "v": f"v{i}", "loc": [], "c": "h"} for i in indices]}
        )
        mock_extract = AsyncMock(side_effect=[invalid_schema, valid_response])
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            asyncio.run(loop.extract_with_repair(
                chunk_num=1,
                source="test_source",
                fields=[{"field_index": i} for i in indices],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        second_kwargs = mock_extract.call_args_list[1].kwargs
        assert second_kwargs.get("error_type") == "schema"

    def test_repair_prompt_includes_invalid_output_fragment_at_runtime(self):
        """Requirement 6.1: the actual runtime repair dispatch (not just direct
        _build_repair_prompt() calls) includes the invalid output fragment."""
        loop = RepairRetryLoop(max_repair_attempts=1)
        indices = [1]
        malformed = '{"extractions": [{"i": 1, "v": "SENTINEL_BAD_VALUE"'
        valid_response = json.dumps(
            {"extractions": [{"i": 1, "v": "good", "loc": [], "c": "h"}]}
        )
        mock_extract = AsyncMock(side_effect=[malformed, valid_response])
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            asyncio.run(loop.extract_with_repair(
                chunk_num=1,
                source="test_source",
                fields=[{"field_index": 1}],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        repair_kwargs = mock_extract.call_args_list[1].kwargs
        assert "SENTINEL_BAD_VALUE" in repair_kwargs["repair_prompt"]


# ---------------------------------------------------------------------------
# extract_with_repair: token budget enforcement (Requirements 7.1, 7.2)
# ---------------------------------------------------------------------------


class TestRepairBudgetEnforcement:
    """Tests for the additive token-budget safety net in extract_with_repair()."""

    def test_no_budgets_is_a_no_op(self):
        """Default budgets=None: source is dispatched unchanged (identical to
        pre-token-budget behavior)."""
        loop = RepairRetryLoop(max_repair_attempts=1)
        indices = [1]
        source = "evidence " * 50
        valid_response = json.dumps(
            {"extractions": [{"i": 1, "v": "x", "loc": [], "c": "h"}]}
        )
        mock_extract = AsyncMock(return_value=valid_response)
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            asyncio.run(loop.extract_with_repair(
                chunk_num=1,
                source=source,
                fields=[{"field_index": 1}],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        kwargs = mock_extract.call_args.kwargs
        # source is a positional arg; check via call.args instead.
        assert mock_extract.call_args.args[1] == source

    def test_well_formed_prompt_within_budget_is_unchanged(self):
        """A normal-sized prompt passes check_budget() and dispatches unchanged
        (additive safety net -- no mitigation for well-formed inputs)."""
        loop = RepairRetryLoop(
            max_repair_attempts=1,
            budgets={"extraction_chunk": 100_000, "validation_repair": 20_000},
        )
        indices = [1]
        source = "small evidence package"
        valid_response = json.dumps(
            {"extractions": [{"i": 1, "v": "x", "loc": [], "c": "h"}]}
        )
        mock_extract = AsyncMock(return_value=valid_response)
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            asyncio.run(loop.extract_with_repair(
                chunk_num=1,
                source=source,
                fields=[{"field_index": 1}],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        assert mock_extract.call_args.args[1] == source

    def test_oversized_evidence_is_mitigated(self):
        """An evidence package that blows the extraction_chunk budget is
        pruned before dispatch (Requirement 7.2(a))."""
        loop = RepairRetryLoop(
            max_repair_attempts=1,
            # Large enough that system-prompt + field-definitions text alone
            # fit comfortably (so mitigation succeeds by pruning evidence
            # alone), but far smaller than the oversized evidence package.
            budgets={"extraction_chunk": 200},
            evidence_config={
                "max_evidence_items_per_chunk": 1,
                "max_evidence_chars_per_chunk": 100,
            },
        )
        indices = [1]
        # Many blank-line-delimited "items" so evidence pruning has room to work.
        oversized_source = "\n\n".join(f"evidence item {i} " * 20 for i in range(30))
        valid_response = json.dumps(
            {"extractions": [{"i": 1, "v": "x", "loc": [], "c": "h"}]}
        )
        mock_extract = AsyncMock(return_value=valid_response)
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            asyncio.run(loop.extract_with_repair(
                chunk_num=1,
                source=oversized_source,
                fields=[{"field_index": 1}],
                semaphore=asyncio.Semaphore(5),
                valid_location_ids={"ev1"},
                expected_indices=indices,
                pdf_name="test_pdf",
            ))

        dispatched_source = mock_extract.call_args.args[1]
        assert len(dispatched_source) < len(oversized_source)

    def test_budget_exceeded_after_mitigation_raises(self):
        """When even full mitigation cannot bring the prompt within budget,
        TokenBudgetExceededError propagates (Requirement 7.2(c))."""
        loop = RepairRetryLoop(
            max_repair_attempts=1,
            # An impossibly small budget that no amount of evidence pruning
            # (which only ever touches the "evidence" section) can satisfy,
            # since the field_definitions text alone already exceeds it.
            budgets={"extraction_chunk": 1},
            evidence_config={},
        )
        indices = [1]
        mock_extract = AsyncMock()
        with patch.dict(
            sys.modules,
            {"agents.openai.api_client": MagicMock(extract_chunk=mock_extract)},
        ):
            with pytest.raises(token_budget.TokenBudgetExceededError):
                asyncio.run(loop.extract_with_repair(
                    chunk_num=1,
                    source="some evidence text",
                    fields=[{"field_index": 1, "field_name": "x" * 100}],
                    semaphore=asyncio.Semaphore(5),
                    valid_location_ids={"ev1"},
                    expected_indices=indices,
                    pdf_name="test_pdf",
                ))

        mock_extract.assert_not_called()
