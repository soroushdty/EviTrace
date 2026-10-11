"""Tests for the optional issue sink of ``pipeline.validator`` (provenance-core 6.1).

With a sink, unknown / duplicated / ambiguous evidence references in an item's
``loc`` are recorded as findings and the item is retained; without one the
module raises (validation) or silently drops (reconstruction) exactly as before.

Requirements: 5.3, 5.4.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from pipeline.validator import (
    ValidationError,
    _validate_extraction_item,
    reconstruct_fields,
    validate_chunk_output,
)
from provenance.adapters.claim_adapter import claim_nodes_from_fields
from provenance.identity import derive_claim_id, derive_source_id, format_evidence_id
from provenance.models import DEFAULT_SEVERITIES, RunIdentity
from provenance.recorder import ProvenanceRecorder

ALLOWED_CODES = {"dangling_reference", "duplicate_reference", "ambiguous_reference"}

KNOWN = {"S000001", "S000002", "T000001"}


class _Sink:
    """List-appending fake sink with the recorder's ``(code, message, details)`` shape."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, code: str, message: str, details: Any) -> None:
        self.calls.append((code, message, dict(details)))

    @property
    def codes(self) -> list[str]:
        return [c for c, _, _ in self.calls]


def _item(i: int = 8, loc: Any = None, v: str = "x", c: str = "h") -> dict:
    return {"i": i, "v": v, "loc": ["S000001"] if loc is None else loc, "c": c}


def _raw(*items: dict) -> str:
    return json.dumps({"extractions": list(items)})


def _evidence_map(*ids: str) -> dict[str, dict]:
    return {eid: {"id": eid, "type": "sentence", "text": f"text of {eid}", "page": 1} for eid in ids}


_LOOKUP = {8: {"domain_group": "g", "field_name": "n8"}, 9: {"domain_group": "g", "field_name": "n9"}}


# ---------------------------------------------------------------------------
# Signatures: additive, keyword-only, default off
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("func", [_validate_extraction_item, validate_chunk_output, reconstruct_fields])
def test_issue_sink_is_keyword_only_and_defaults_to_none(func):
    param = inspect.signature(func).parameters["issue_sink"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


def test_existing_positional_parameters_are_unchanged():
    assert list(inspect.signature(_validate_extraction_item).parameters)[:4] == [
        "i",
        "obj",
        "actual_indices",
        "valid_location_ids",
    ]
    assert list(inspect.signature(reconstruct_fields).parameters)[:3] == ["compact", "field_lookup", "evidence_map"]
    assert list(inspect.signature(validate_chunk_output).parameters)[:2] == ["raw", "expected_indices"]


# ---------------------------------------------------------------------------
# No sink: existing behaviour, pinned explicitly
# ---------------------------------------------------------------------------


def test_without_sink_unknown_id_raises_with_the_same_message():
    with pytest.raises(ValidationError) as excinfo:
        _validate_extraction_item(3, _item(loc=["S000001", "S999999"]), [], KNOWN)
    assert str(excinfo.value) == "Item 3: loc contains unknown evidence IDs: ['S999999']"


def test_without_sink_near_match_still_raises_as_unknown():
    with pytest.raises(ValidationError, match="unknown evidence IDs"):
        _validate_extraction_item(0, _item(loc=["s000001"]), [], KNOWN)


def test_without_sink_duplicate_ids_pass_silently_and_item_is_kept():
    indices: list[int] = []
    _validate_extraction_item(0, _item(loc=["S000001", "S000001"]), indices, KNOWN)
    assert indices == [8]


def test_without_sink_chunk_output_with_unknown_id_raises():
    with pytest.raises(ValidationError, match="unknown evidence IDs"):
        validate_chunk_output(_raw(_item(loc=["S999999"])), [8], valid_location_ids=KNOWN)


def test_without_sink_reconstruct_silently_drops_unknown_ids_but_keeps_location():
    out = reconstruct_fields([_item(loc=["S000001", "S999999"])], _LOOKUP, _evidence_map("S000001"))
    assert out[0]["location"] == ["S000001", "S999999"]
    assert [m["id"] for m in out[0]["location_metadata"]] == ["S000001"]
    assert out[0]["evidence"] == "text of S000001"


# ---------------------------------------------------------------------------
# _validate_extraction_item with a sink
# ---------------------------------------------------------------------------


def test_sink_unknown_id_is_reported_and_the_item_is_kept():
    sink, indices = _Sink(), []
    obj = _item(i=8, loc=["S000001", "S999999"])
    _validate_extraction_item(2, obj, indices, KNOWN, issue_sink=sink)
    assert indices == [8]
    assert obj["loc"] == ["S000001", "S999999"]
    assert sink.codes == ["dangling_reference"]
    code, message, details = sink.calls[0]
    assert details["reference"] == "S999999"
    assert details["reference_kind"] == "evidence"
    assert details["field_index"] == 8
    assert details["position"] == 1
    assert "S999999" in message
    assert "node_id" not in details  # the per-document wrapper (6.4) adds it


def test_sink_reference_is_the_raw_string_never_stripped_or_casefolded():
    sink = _Sink()
    _validate_extraction_item(0, _item(loc=["  Weird ID "]), [], KNOWN, issue_sink=sink)
    assert sink.calls[0][2]["reference"] == "  Weird ID "


def test_sink_ambiguous_near_match_is_reported_not_dangling():
    sink, indices = _Sink(), []
    _validate_extraction_item(0, _item(loc=[" s000001 ", "t000001"]), indices, KNOWN, issue_sink=sink)
    assert indices == [8]
    assert sink.codes == ["ambiguous_reference", "ambiguous_reference"]
    assert [d["reference"] for _, _, d in sink.calls] == [" s000001 ", "t000001"]
    assert sink.calls[0][2]["candidates"] == ["S000001"]


def test_sink_duplicate_id_is_reported_once_with_all_positions():
    sink, indices = _Sink(), []
    _validate_extraction_item(0, _item(loc=["S000001", "S000002", "S000001", "S000001"]), indices, KNOWN, issue_sink=sink)
    assert indices == [8]
    assert sink.codes == ["duplicate_reference"]
    details = sink.calls[0][2]
    assert details["reference"] == "S000001"
    assert details["positions"] == [0, 2, 3]
    assert details["reference_kind"] == "evidence"


def test_sink_duplicate_unknown_id_reports_dangling_once_plus_duplicate():
    sink = _Sink()
    _validate_extraction_item(0, _item(loc=["X1", "X1"]), [], KNOWN, issue_sink=sink)
    assert sink.codes == ["dangling_reference", "duplicate_reference"]


def test_sink_clean_loc_reports_nothing():
    sink = _Sink()
    _validate_extraction_item(0, _item(loc=["S000001", "T000001"]), [], KNOWN, issue_sink=sink)
    assert sink.calls == []


def test_sink_without_known_ids_reports_duplicates_only():
    sink = _Sink()
    _validate_extraction_item(0, _item(loc=["A", "A", "B"]), [], None, issue_sink=sink)
    assert sink.codes == ["duplicate_reference"]


def test_sink_does_not_mask_other_validation_errors():
    bad_items = [
        "not a dict",
        {"i": 8, "v": "x", "c": "h"},  # missing loc
        {"i": 8, "v": "x", "loc": [], "c": "h", "extra": 1},
        _item(c="zz"),
        _item(i="8"),
        _item(loc="S000001"),
        _item(loc=[1]),
    ]
    for bad in bad_items:
        sink, indices = _Sink(), []
        with pytest.raises(ValidationError):
            _validate_extraction_item(0, bad, indices, KNOWN, issue_sink=sink)
        assert sink.calls == []
        assert indices == []


def test_sink_with_an_unusable_item_index_omits_field_index():
    sink = _Sink()
    _validate_extraction_item(0, _item(i=True, loc=["X1"]), [], KNOWN, issue_sink=sink)  # bool passes isinstance(int)
    _validate_extraction_item(0, _item(i=-4, loc=["X1"]), [], KNOWN, issue_sink=sink)
    assert all("field_index" not in d for _, _, d in sink.calls)
    assert len(sink.calls) == 2


# ---------------------------------------------------------------------------
# validate_chunk_output with a sink
# ---------------------------------------------------------------------------


def test_chunk_output_with_sink_retains_claims_and_records_each_problem():
    raw = _raw(
        _item(i=8, loc=["S000001", "S999999"]),
        _item(i=9, loc=["s000002", "T000001", "T000001"]),
    )
    sink = _Sink()
    data = validate_chunk_output(raw, [8, 9], valid_location_ids=KNOWN, issue_sink=sink)
    assert [d["i"] for d in data] == [8, 9]
    assert [(c, d["field_index"], d["reference"]) for c, _, d in sink.calls] == [
        ("dangling_reference", 8, "S999999"),
        ("ambiguous_reference", 9, "s000002"),
        ("duplicate_reference", 9, "T000001"),
    ]


def test_chunk_output_with_sink_returns_the_same_data_as_without_when_clean():
    raw = _raw(_item(i=8, loc=["S000001"]))
    assert validate_chunk_output(raw, [8], valid_location_ids=KNOWN, issue_sink=_Sink()) == validate_chunk_output(
        raw, [8], valid_location_ids=KNOWN
    )


def test_chunk_output_reports_nothing_when_the_chunk_is_rejected():
    # A rejected attempt (it will be repaired or retried) must not leave findings for claims that never exist.
    sink = _Sink()
    with pytest.raises(ValidationError):
        validate_chunk_output(
            _raw(_item(i=8, loc=["S999999"]), _item(i=9, c="zz")), [8, 9], valid_location_ids=KNOWN, issue_sink=sink
        )
    with pytest.raises(ValidationError, match="Field index mismatch"):
        validate_chunk_output(_raw(_item(i=8, loc=["S999999"])), [9], valid_location_ids=KNOWN, issue_sink=sink)
    with pytest.raises(ValidationError, match="Expected 2 objects"):
        validate_chunk_output(_raw(_item(i=8, loc=["S999999"])), [8, 9], valid_location_ids=KNOWN, issue_sink=sink)
    assert sink.calls == []


def test_chunk_output_malformed_items_still_raise_with_a_sink():
    with pytest.raises(ValidationError, match="invalid confidence"):
        validate_chunk_output(_raw(_item(c="zz")), [8], valid_location_ids=KNOWN, issue_sink=_Sink())
    with pytest.raises(ValidationError, match="JSON parse failed"):
        validate_chunk_output("not json", [8], valid_location_ids=KNOWN, issue_sink=_Sink())


# ---------------------------------------------------------------------------
# reconstruct_fields with a sink
# ---------------------------------------------------------------------------


def test_reconstruct_with_sink_returns_data_identical_to_without():
    compact = [
        _item(i=8, loc=["S000001", "S999999", "s000002", "S000001"]),
        _item(i=9, loc=["T000001"]),
    ]
    emap = _evidence_map("S000001", "S000002", "T000001")
    plain = reconstruct_fields(compact, _LOOKUP, emap)
    sink = _Sink()
    with_sink = reconstruct_fields(compact, _LOOKUP, emap, issue_sink=sink)
    assert json.dumps(with_sink, sort_keys=True) == json.dumps(plain, sort_keys=True)
    assert with_sink == plain
    assert sink.codes == ["dangling_reference", "ambiguous_reference", "duplicate_reference"]
    assert [d["reference"] for _, _, d in sink.calls] == ["S999999", "s000002", "S000001"]
    assert all(d["field_index"] == 8 and d["reference_kind"] == "evidence" for _, _, d in sink.calls)


def test_reconstruct_without_evidence_map_reports_duplicates_only():
    sink = _Sink()
    out = reconstruct_fields([_item(i=8, loc=["A", "A", "B"])], _LOOKUP, issue_sink=sink)
    assert sink.codes == ["duplicate_reference"]
    assert out[0]["location"] == ["A", "A", "B"]
    assert out[0]["location_metadata"] == []


def test_reconstruct_with_an_empty_evidence_map_reports_every_reference_as_dangling():
    sink = _Sink()
    reconstruct_fields([_item(i=8, loc=["A", "B"])], _LOOKUP, {}, issue_sink=sink)
    assert sink.codes == ["dangling_reference", "dangling_reference"]


def test_reconstruct_reports_nothing_for_a_clean_item():
    sink = _Sink()
    reconstruct_fields([_item(i=8, loc=["S000001"])], _LOOKUP, _evidence_map("S000001"), issue_sink=sink)
    assert sink.calls == []


def test_reconstruct_and_validate_report_the_same_merge_key():
    """The intentional double report meets on (code, reference, reference_kind, field_index)."""
    raw = _raw(_item(i=8, loc=["S000001", "S999999", "s000002", "S000001"]))
    v_sink, r_sink = _Sink(), _Sink()
    data = validate_chunk_output(raw, [8], valid_location_ids=KNOWN, issue_sink=v_sink)
    reconstruct_fields(data, _LOOKUP, _evidence_map(*KNOWN), issue_sink=r_sink)

    def key(call):
        code, _, d = call
        return (code, d["reference"], d["reference_kind"], d["field_index"])

    assert sorted(map(key, v_sink.calls)) == sorted(map(key, r_sink.calls))
    assert len(v_sink.calls) == 3


# ---------------------------------------------------------------------------
# Only the three allowed codes are ever emitted
# ---------------------------------------------------------------------------


def test_only_the_three_allowed_codes_are_ever_passed_to_the_sink():
    assert ALLOWED_CODES <= set(DEFAULT_SEVERITIES)
    sink = _Sink()
    locs = [["X"], ["x", "X"], ["S000001", "S000001"], ["  "], [""], ["a#b"], ["s000001", "s000001", "Q"]]
    for n, loc in enumerate(locs):
        _validate_extraction_item(n, _item(loc=loc), [], KNOWN, issue_sink=sink)
        _validate_extraction_item(n, _item(loc=loc), [], None, issue_sink=sink)
        reconstruct_fields([_item(loc=loc)], _LOOKUP, _evidence_map(*KNOWN), issue_sink=sink)
        reconstruct_fields([_item(loc=loc)], _LOOKUP, issue_sink=sink)
    assert sink.calls
    assert set(sink.codes) <= ALLOWED_CODES


# ---------------------------------------------------------------------------
# Classification agrees with the claim adapter
# ---------------------------------------------------------------------------

_FP = "aaaabbbbccccdddd"
_SOURCE_ID = derive_source_id(_FP)

_AGREEMENT_CASES = [
    ["S000001", "T000001"],
    ["S999999"],
    ["s000001"],
    [" S000001 "],
    ["S000001", "S000001"],
    ["X", "X"],
    ["s000001", "s000001"],
    ["s000001", "S000001"],
    ["", "S000001"],
    ["  "],
    ["S000001", "S999999", "t000001", "S999999", "S000001"],
]


@pytest.mark.parametrize("loc", _AGREEMENT_CASES, ids=[repr(c) for c in _AGREEMENT_CASES])
def test_classification_matches_the_claim_adapter(loc):
    known_full = {format_evidence_id(_SOURCE_ID, k) for k in KNOWN}
    _, _, adapter_findings = claim_nodes_from_fields(
        [{"field_index": 8, "location": loc}],
        source_id=_SOURCE_ID,
        run_id="run:0123456789abcdef",
        known_evidence_ids=known_full,
        origin="model",
        evidence_anchors={},
    )
    adapter = sorted(
        (f.code, f.details["reference"], f.details.get("reference_kind", "evidence"))
        for f in adapter_findings
        if f.code in ALLOWED_CODES
    )
    sink = _Sink()
    _validate_extraction_item(0, _item(loc=list(loc)), [], KNOWN, issue_sink=sink)
    ours = sorted((c, d["reference"], d["reference_kind"]) for c, _, d in sink.calls)
    assert ours == adapter


def test_candidates_name_the_same_nodes_as_the_claim_adapter():
    known_full = {format_evidence_id(_SOURCE_ID, k) for k in KNOWN}
    _, _, findings = claim_nodes_from_fields(
        [{"field_index": 8, "location": [" s000001"]}],
        source_id=_SOURCE_ID,
        run_id="run:0123456789abcdef",
        known_evidence_ids=known_full,
        origin="model",
        evidence_anchors={},
    )
    sink = _Sink()
    _validate_extraction_item(0, _item(loc=[" s000001"]), [], KNOWN, issue_sink=sink)
    assert [format_evidence_id(_SOURCE_ID, c) for c in sink.calls[0][2]["candidates"]] == findings[0].details["candidates"]


# ---------------------------------------------------------------------------
# The real recorder accepts the sink contract
# ---------------------------------------------------------------------------


def _recorder() -> ProvenanceRecorder:
    run = RunIdentity(
        run_id="run:0123456789abcdef",
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
        document_fingerprints={_SOURCE_ID: _FP},
        created_at="2026-10-08T00:00:00+00:00",
    )
    return ProvenanceRecorder(run)


def _wrap(sink, source_id: str):
    """What the per-document wrapper of task 6.4 does: name the claim via ``node_id``."""

    def wrapped(code, message, details):
        sink(code, message, {**details, "node_id": derive_claim_id(source_id, details["field_index"])})

    return wrapped


def test_real_recorder_records_validator_findings_when_the_wrapper_adds_node_id():
    rec = _recorder()
    raw = _raw(_item(i=8, loc=["S000001", "S999999", "s000002", "T000001", "T000001"]))
    data = validate_chunk_output(
        raw, [8], valid_location_ids=KNOWN, issue_sink=_wrap(rec.issue_sink(), _SOURCE_ID)
    )
    assert data[0]["loc"][1] == "S999999"  # claim retained
    findings = rec.snapshot().findings
    assert sorted(f.code for f in findings) == ["ambiguous_reference", "dangling_reference", "duplicate_reference"]
    claim_id = derive_claim_id(_SOURCE_ID, 8)
    assert all(f.node_id == claim_id for f in findings)
    assert {f.code: f.details["reference"] for f in findings} == {
        "dangling_reference": "S999999",
        "ambiguous_reference": "s000002",
        "duplicate_reference": "T000001",
    }
    for f in findings:
        assert f.severity == DEFAULT_SEVERITIES[f.code]


def test_real_recorder_accepts_the_raw_validator_call_without_node_id():
    # Documented behaviour: the recorder does not require node_id; the finding is recorded with node_id=None
    # (so it would not name the claim, hence the 6.4 wrapper).
    rec = _recorder()
    _validate_extraction_item(0, _item(loc=["S999999"]), [], KNOWN, issue_sink=rec.issue_sink())
    (finding,) = rec.snapshot().findings
    assert finding.code == "dangling_reference"
    assert finding.node_id is None
    assert finding.details["reference"] == "S999999"


def test_real_recorder_accepts_reconstruct_findings_too():
    rec = _recorder()
    reconstruct_fields(
        [_item(i=8, loc=["S999999"])],
        _LOOKUP,
        _evidence_map("S000001"),
        issue_sink=_wrap(rec.issue_sink(), _SOURCE_ID),
    )
    (finding,) = rec.snapshot().findings
    assert finding.node_id == derive_claim_id(_SOURCE_ID, 8)
