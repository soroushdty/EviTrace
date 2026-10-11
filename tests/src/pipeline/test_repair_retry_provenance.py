"""``RepairRetryLoop`` with the provenance issue sink (provenance-core 6.4).

The loop validates strictly, without a sink, so an unknown evidence id is still
repaired exactly as before. The sink is consulted only at the end:

* on an accepted attempt, one sink-bearing validation of the accepted raw output
  reports its reference problems (the findings are buffered, so a rejected
  attempt leaves none behind);
* on exhaustion, when re-validating the last raw output *with* the sink succeeds
  -- every remaining problem was a reference problem -- the output is accepted
  and the claims are retained (Requirement 5.4) instead of raising
  ``RepairExhaustedError``.

Without a sink nothing differs from the unmodified loop.

Requirements: 5.3, 5.4.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pipeline.pdf_processor as pdf_processor
from pipeline.pdf_processor import RepairExhaustedError, RepairRetryLoop

_KNOWN = {"S000001", "S000002"}


def _payload(*items: dict[str, Any]) -> str:
    return json.dumps({"extractions": list(items)})


def _item(i: int = 3, loc: tuple[str, ...] = ("S000001",), v: str = "x", c: str = "h") -> dict[str, Any]:
    return {"i": i, "v": v, "loc": list(loc), "c": c}


class _Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, code: str, message: str, details: Any) -> None:
        self.calls.append((code, message, dict(details)))

    @property
    def codes(self) -> list[str]:
        return [c for c, _, _ in self.calls]


def _run(responses: list[str], *, sink: Any = None, attempts: int = 2) -> tuple[Any, AsyncMock]:
    api = MagicMock()
    api.extract_chunk = AsyncMock(side_effect=list(responses))
    loop = RepairRetryLoop(max_repair_attempts=attempts, provenance_sink=sink)

    async def go() -> Any:
        return await loop.extract_with_repair(
            1,
            "source",
            [{"field_index": 3}],
            asyncio.Semaphore(1),
            valid_location_ids=set(_KNOWN),
            expected_indices=[3],
            pdf_name="paper",
        )

    with patch.dict(sys.modules, {"agents.openai.api_client": api}), patch.object(
        pdf_processor.RepairRetryLoop, "_get_system_prompt_text", staticmethod(lambda: "system")
    ):
        try:
            return asyncio.run(go()), api.extract_chunk
        except RepairExhaustedError as exc:
            return exc, api.extract_chunk


def test_a_valid_first_attempt_reports_nothing_and_returns_the_validated_list():
    sink = _Sink()
    result, api = _run([_payload(_item())], sink=sink)
    assert result == [_item()]
    assert api.call_count == 1
    assert sink.calls == []


def test_a_duplicate_reference_in_the_accepted_output_is_reported_once():
    sink = _Sink()
    result, _ = _run([_payload(_item(loc=("S000001", "S000001")))], sink=sink)
    assert result == [_item(loc=("S000001", "S000001"))]
    assert sink.codes == ["duplicate_reference"]


def test_an_unknown_id_is_still_repaired_with_a_sink_and_the_failed_attempt_leaves_no_finding():
    sink = _Sink()
    result, api = _run([_payload(_item(loc=("S999999",))), _payload(_item())], sink=sink)
    assert result == [_item()]
    assert api.call_count == 2
    assert sink.calls == []


def test_the_extract_chunk_calls_are_identical_with_and_without_a_sink():
    responses = [_payload(_item(loc=("S999999",))), _payload(_item())]
    _, with_sink = _run(responses, sink=_Sink())
    _, without = _run(responses)

    def shape(api: AsyncMock) -> list[Any]:
        # the semaphore is the only per-run object among the arguments
        return [(call.args[:3], call.kwargs) for call in api.call_args_list]

    assert shape(with_sink) == shape(without)
    assert len(shape(with_sink)) == 2


def test_exhausted_on_unknown_ids_only_retains_the_output_and_reports_the_reference():
    sink = _Sink()
    bad = _payload(_item(loc=("S999999",)))
    result, api = _run([bad, bad, bad], sink=sink)
    assert result == [_item(loc=("S999999",))]
    assert api.call_count == 3
    assert sink.codes == ["dangling_reference"]
    assert sink.calls[0][2]["reference"] == "S999999"
    assert sink.calls[0][2]["field_index"] == 3


def test_exhausted_without_a_sink_raises_exactly_as_before():
    bad = _payload(_item(loc=("S999999",)))
    result, api = _run([bad, bad, bad])
    assert isinstance(result, RepairExhaustedError)
    assert api.call_count == 3
    assert result.metadata["status"] == "failed_validation"
    assert result.metadata["error_type"] == "schema"
    assert "unknown evidence IDs" in result.metadata["last_error"]


def test_exhausted_on_another_schema_error_still_raises_with_a_sink():
    sink = _Sink()
    bad = _payload(_item(c="zzz", loc=("S999999",)))
    result, api = _run([bad, bad, bad], sink=sink)
    assert isinstance(result, RepairExhaustedError)
    assert api.call_count == 3
    assert sink.calls == []
    assert result.metadata["attempts"] == 2


def test_exhausted_on_a_parse_error_still_raises_with_a_sink():
    sink = _Sink()
    result, _ = _run(["not json", "not json", "not json"], sink=sink)
    assert isinstance(result, RepairExhaustedError)
    assert result.metadata["error_type"] == "parse"
    assert sink.calls == []


def test_exhausted_on_a_wrong_index_still_raises_with_a_sink():
    sink = _Sink()
    bad = _payload(_item(i=9))
    result, _ = _run([bad, bad, bad], sink=sink)
    assert isinstance(result, RepairExhaustedError)
    assert sink.calls == []


def test_the_default_loop_has_no_provenance_sink():
    assert RepairRetryLoop().provenance_sink is None
