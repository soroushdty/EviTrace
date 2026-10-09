"""Tests for ``provenance.recorder`` — the append-only in-run provenance sink.

Covers (provenance-core design, "Emission Layer → ProvenanceRecorder"):

- acceptance of source, evidence, claim, derivation and validation records,
  issues and per-stage marks without the emitting stage knowing how records are
  later assembled (1.2), and an API that exposes no update or delete method;
- idempotent duplicate handling: an identical re-record is a no-op, a
  conflicting re-record yields a ``duplicate_identity`` finding and keeps the
  first record;
- the issue-sink callable handed to the chunk validator, which turns a bad
  ``loc`` reference into a recorded finding (5.4);
- per-stage complete / failed marks and the ``not_run`` default, so a stage
  that emitted nothing is visible against its contract (1.6, 2.1), run-wide or
  scoped to one document by ``source_id`` (task 4.6: escalate-only per
  document, every distinct failure reason kept, malformed ids rejected);
- rejection of an undeclared stage with ``UnknownStageError``;
- the disabled mode, whose methods no-op while still returning identifiers;
- an immutable snapshot that is equal across repeated calls;
- the single lock guarding mutation and snapshot copying, exercised from many
  threads;
- no file or network access (1.5), asserted both statically and at runtime.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import inspect
import io
import socket
import threading
from pathlib import Path

import pytest

from provenance import recorder as recorder_module
from provenance.contracts import STAGE_CONTRACTS, STAGE_ORDER, UnknownStageError
from provenance.identity import (
    InvalidIdentifierError,
    derive_claim_id,
    derive_derivation_id,
    derive_source_id,
    derive_validation_id,
    format_evidence_id,
)
from provenance.models import (
    DEFAULT_SEVERITIES,
    UNKNOWN_PRIVACY,
    ClaimNode,
    DerivationStep,
    EvidenceNode,
    ProvenanceEdge,
    RecordedRun,
    RunIdentity,
    SourceAnchor,
    SourceNode,
    ValidationFinding,
    ValidationRecord,
)
from provenance.recorder import ProvenanceRecorder

_RUN = "run:0123456789abcdef"
_FP_A = "aaaabbbbccccdddd"
_FP_B = "eeeeffff00001111"


# ---------------------------------------------------------------------------
# Record factories (synthetic identifiers only)
# ---------------------------------------------------------------------------


def _run_identity() -> RunIdentity:
    return RunIdentity(
        run_id=_RUN,
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
        document_fingerprints={derive_source_id(_FP_A): _FP_A},
        created_at="2026-10-08T00:00:00+00:00",
    )


def _source(fingerprint: str = _FP_A, *, label: str = "paper_001.pdf") -> SourceNode:
    return SourceNode(
        node_id=derive_source_id(fingerprint),
        kind="source",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        document_fingerprint=fingerprint,
        source_format="pdf",
        source_label=label,
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
    )


def _evidence(local_id: str = "s12", *, fingerprint: str = _FP_A, rank: int | None = 7) -> EvidenceNode:
    source_id = derive_source_id(fingerprint)
    return EvidenceNode(
        node_id=format_evidence_id(source_id, local_id),
        kind="evidence",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        local_id=local_id,
        evidence_kind="sentence",
        source_id=source_id,
        anchor=SourceAnchor(precision="exact", page=3, coords=(1.0, 2.0, 3.0, 4.0)),
        producing_backend="grobid",
        rank_score=rank,
        parser_metadata={"tei_tag": "s"},
    )


def _claim(field_index: int = 3, *, fingerprint: str = _FP_A, digest: str = "d1g3st") -> ClaimNode:
    source_id = derive_source_id(fingerprint)
    return ClaimNode(
        node_id=derive_claim_id(source_id, field_index),
        kind="claim",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        source_id=source_id,
        field_index=field_index,
        field_name=f"field_{field_index}",
        value_digest=digest,
        origin="model",
        support="supported",
        cited_evidence_ids=(format_evidence_id(source_id, "s12"),),
        confidence="high",
    )


def _cites(claim: ClaimNode, evidence_id: str, *, rank: int = 1) -> ProvenanceEdge:
    return ProvenanceEdge(
        edge_id=f"cites_evidence:{claim.node_id}->{evidence_id}",
        kind="cites_evidence",
        from_id=claim.node_id,
        to_id=evidence_id,
        attributes={"rank": rank},
    )


def _derivation(stage: str = "extraction_chunk", *, outputs: tuple[str, ...] | None = None,
                model_id: str | None = "gpt-x") -> DerivationStep:
    inputs = (format_evidence_id(derive_source_id(_FP_A), "s12"),)
    outputs = outputs if outputs is not None else (derive_claim_id(derive_source_id(_FP_A), 3),)
    return DerivationStep(
        node_id=derive_derivation_id(stage, "extract", inputs, outputs),
        kind="derivation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        stage=stage,
        derivation_kind="extract",
        determinism="probabilistic",
        model_id=model_id,
        input_ids=inputs,
        output_ids=outputs,
    )


def _validation(check_name: str = "chain") -> ValidationRecord:
    return ValidationRecord(
        node_id=derive_validation_id(_RUN, check_name),
        kind="validation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        check_name=check_name,
        finding_ids=(),
        completed=True,
    )


def _finding(finding_id: str = "f-1", *, message: str = "cited evidence does not exist") -> ValidationFinding:
    return ValidationFinding(
        finding_id=finding_id,
        code="dangling_reference",
        severity="critical",
        node_id=derive_claim_id(derive_source_id(_FP_A), 3),
        edge_id=None,
        message=message,
        details={"reference": "missing-id"},
    )


def _recorder(*, enabled: bool = True) -> ProvenanceRecorder:
    return ProvenanceRecorder(_run_identity(), enabled=enabled)


def _record_one_of_everything(rec: ProvenanceRecorder) -> None:
    claim = _claim()
    rec.record_source(_source())
    rec.record_evidence([_evidence("s12"), _evidence("s13")])
    rec.record_claim(claim, [_cites(claim, _evidence("s12").node_id)])
    rec.record_derivation(_derivation())
    rec.record_validation(_validation())
    rec.record_issue(_finding())
    rec.issue_sink()("dangling_reference", "unknown loc", {"node_id": claim.node_id, "reference": "x"})
    rec.mark_stage_complete("evidence_index")
    rec.mark_stage_failed("synthesis", "model timeout")


# ---------------------------------------------------------------------------
# Public surface: record_* and mark_stage_* only — append-only by construction
# ---------------------------------------------------------------------------

_DESIGN_METHODS = {
    "record_source",
    "record_evidence",
    "record_claim",
    "record_derivation",
    "record_validation",
    "record_issue",
    "issue_sink",
    "mark_stage_complete",
    "mark_stage_failed",
    "snapshot",
}


def test_public_methods_are_exactly_the_designed_interface():
    public = {
        name
        for name, member in inspect.getmembers(ProvenanceRecorder)
        if not name.startswith("_") and callable(member)
    }
    assert public == _DESIGN_METHODS


def test_no_update_or_delete_method_exists():
    forbidden_fragments = ("update", "delete", "remove", "clear", "pop", "replace", "reset", "set_", "discard")
    for name in dir(ProvenanceRecorder):
        if name.startswith("_"):
            continue
        assert not any(fragment in name for fragment in forbidden_fragments), name


def test_constructor_signature_matches_design():
    params = inspect.signature(ProvenanceRecorder.__init__).parameters
    assert list(params) == ["self", "run", "enabled"]
    assert params["enabled"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["enabled"].default is True


def test_constructor_rejects_a_non_run_identity():
    with pytest.raises(TypeError):
        ProvenanceRecorder({"run_id": _RUN})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Recording each record kind (1.2)
# ---------------------------------------------------------------------------


def test_record_source_returns_the_node_id_and_snapshot_holds_it():
    rec = _recorder()
    source = _source()
    assert rec.record_source(source) == derive_source_id(_FP_A)
    snap = rec.snapshot()
    assert snap.nodes == {source.node_id: source}


def test_record_evidence_returns_ids_in_input_order():
    rec = _recorder()
    nodes = [_evidence("s13"), _evidence("s12"), _evidence("fig1")]
    ids = rec.record_evidence(nodes)
    assert ids == tuple(n.node_id for n in nodes)
    assert isinstance(ids, tuple)
    assert set(rec.snapshot().nodes) == set(ids)


def test_record_evidence_accepts_an_empty_sequence():
    rec = _recorder()
    assert rec.record_evidence([]) == ()
    assert rec.snapshot().nodes == {}


def test_record_claim_records_the_claim_and_its_edges():
    rec = _recorder()
    claim = _claim()
    edges = [_cites(claim, _evidence("s12").node_id), _cites(claim, _evidence("s13").node_id)]
    assert rec.record_claim(claim, edges) == claim.node_id
    snap = rec.snapshot()
    assert snap.nodes[claim.node_id] == claim
    assert snap.edges == tuple(edges)


def test_record_claim_with_no_edges_is_accepted():
    rec = _recorder()
    claim = _claim()
    rec.record_claim(claim, [])
    assert rec.snapshot().edges == ()


def test_record_derivation_and_validation_return_their_ids():
    rec = _recorder()
    step = _derivation()
    val = _validation()
    assert rec.record_derivation(step) == step.node_id
    assert rec.record_validation(val) == val.node_id
    assert set(rec.snapshot().nodes) == {step.node_id, val.node_id}


def test_record_issue_appends_the_finding_verbatim():
    rec = _recorder()
    finding = _finding()
    assert rec.record_issue(finding) is None
    assert rec.snapshot().findings == (finding,)


@pytest.mark.parametrize(
    "method, bad",
    [
        ("record_source", lambda: _claim()),
        ("record_claim", lambda: _source()),
        ("record_derivation", lambda: _validation()),
        ("record_validation", lambda: _derivation()),
        ("record_issue", lambda: _source()),
    ],
)
def test_record_methods_reject_the_wrong_record_type(method, bad):
    rec = _recorder()
    args = (bad(), []) if method == "record_claim" else (bad(),)
    with pytest.raises(TypeError):
        getattr(rec, method)(*args)
    assert rec.snapshot().nodes == {}
    assert rec.snapshot().findings == ()


def test_record_evidence_rejects_a_non_evidence_member_without_recording_any():
    rec = _recorder()
    with pytest.raises(TypeError):
        rec.record_evidence([_evidence("s12"), _claim()])  # type: ignore[list-item]
    assert rec.snapshot().nodes == {}


def test_record_claim_rejects_a_non_edge_without_recording_anything():
    rec = _recorder()
    with pytest.raises(TypeError):
        rec.record_claim(_claim(), [_source()])  # type: ignore[list-item]
    assert rec.snapshot().nodes == {}
    assert rec.snapshot().edges == ()


def test_records_from_many_stages_accumulate_and_never_disappear():
    rec = _recorder()
    seen: set[str] = set()
    for local_id in ("a", "b", "c"):
        seen |= set(rec.record_evidence([_evidence(local_id)]))
        assert seen <= set(rec.snapshot().nodes)
    seen.add(rec.record_source(_source()))
    seen.add(rec.record_claim(_claim(), []))
    assert seen == set(rec.snapshot().nodes)


# ---------------------------------------------------------------------------
# Duplicate identity: identical = no-op, conflicting = finding + keep first
# ---------------------------------------------------------------------------


def test_identical_duplicate_node_is_a_no_op():
    rec = _recorder()
    rec.record_source(_source())
    before = rec.snapshot()
    assert rec.record_source(_source()) == _source().node_id
    rec.record_evidence([_evidence("s12")])
    rec.record_evidence([_evidence("s12")])
    after = rec.snapshot()
    assert len(after.nodes) == 2
    assert after.findings == ()
    assert after.nodes[_source().node_id] == before.nodes[_source().node_id]


def test_identical_duplicate_derivation_is_a_no_op():
    rec = _recorder()
    rec.record_derivation(_derivation())
    rec.record_derivation(_derivation())
    snap = rec.snapshot()
    assert len(snap.nodes) == 1
    assert snap.findings == ()


def test_conflicting_duplicate_node_keeps_first_and_records_duplicate_identity():
    rec = _recorder()
    first = _source(label="first.pdf")
    second = _source(label="second.pdf")
    assert first.node_id == second.node_id
    rec.record_source(first)
    assert rec.record_source(second) == first.node_id
    snap = rec.snapshot()
    assert snap.nodes[first.node_id] == first
    assert len(snap.findings) == 1
    finding = snap.findings[0]
    assert finding.code == "duplicate_identity"
    assert finding.severity == DEFAULT_SEVERITIES["duplicate_identity"]
    assert finding.node_id == first.node_id
    assert finding.edge_id is None
    assert finding.finding_id
    assert "source_label" in finding.details["differing_fields"]


def test_conflicting_duplicate_across_record_kinds_keeps_first():
    rec = _recorder()
    claim = _claim()
    rec.record_claim(claim, [])
    impostor = dataclasses.replace(_validation(), node_id=claim.node_id)
    rec.record_validation(impostor)
    snap = rec.snapshot()
    assert snap.nodes[claim.node_id] == claim
    assert [f.code for f in snap.findings] == ["duplicate_identity"]
    assert snap.findings[0].details["kept_kind"] == "claim"
    assert snap.findings[0].details["rejected_kind"] == "validation"


def test_conflicting_duplicate_inside_one_evidence_batch_keeps_first():
    rec = _recorder()
    a = _evidence("s12", rank=1)
    b = _evidence("s12", rank=2)
    assert rec.record_evidence([a, b]) == (a.node_id, a.node_id)
    snap = rec.snapshot()
    assert snap.nodes[a.node_id] == a
    assert [f.code for f in snap.findings] == ["duplicate_identity"]


def test_retrying_the_same_conflicting_record_does_not_multiply_findings():
    rec = _recorder()
    rec.record_source(_source(label="first.pdf"))
    rec.record_source(_source(label="second.pdf"))
    rec.record_source(_source(label="second.pdf"))
    assert len(rec.snapshot().findings) == 1


def test_identical_duplicate_edge_is_a_no_op():
    rec = _recorder()
    claim = _claim()
    edge = _cites(claim, _evidence("s12").node_id)
    rec.record_claim(claim, [edge])
    rec.record_claim(claim, [edge])
    snap = rec.snapshot()
    assert snap.edges == (edge,)
    assert snap.findings == ()


def test_conflicting_duplicate_edge_keeps_first_and_names_the_edge():
    rec = _recorder()
    claim = _claim()
    first = _cites(claim, _evidence("s12").node_id, rank=1)
    second = _cites(claim, _evidence("s12").node_id, rank=2)
    rec.record_claim(claim, [first])
    rec.record_claim(claim, [second])
    snap = rec.snapshot()
    assert snap.edges == (first,)
    assert len(snap.findings) == 1
    finding = snap.findings[0]
    assert finding.code == "duplicate_identity"
    assert finding.edge_id == first.edge_id
    assert finding.node_id is None


def test_identical_duplicate_finding_is_a_no_op_and_conflicting_one_keeps_first():
    rec = _recorder()
    first = _finding("f-1")
    rec.record_issue(first)
    rec.record_issue(_finding("f-1"))
    assert rec.snapshot().findings == (first,)
    rec.record_issue(_finding("f-1", message="a different message"))
    snap = rec.snapshot()
    assert snap.findings[0] == first
    assert [f.code for f in snap.findings] == ["dangling_reference", "duplicate_identity"]
    assert snap.findings[1].details["finding_id"] == "f-1"


def test_generated_finding_ids_are_unique():
    rec = _recorder()
    rec.record_source(_source(label="a"))
    rec.record_source(_source(label="b"))
    rec.record_evidence([_evidence("s1", rank=1)])
    rec.record_evidence([_evidence("s1", rank=2)])
    sink = rec.issue_sink()
    sink("dangling_reference", "m1", {"reference": "r1"})
    sink("dangling_reference", "m2", {"reference": "r2"})
    ids = [f.finding_id for f in rec.snapshot().findings]
    assert len(ids) == 4
    assert len(set(ids)) == 4


# ---------------------------------------------------------------------------
# Issue sink handed to the chunk validator (5.4)
# ---------------------------------------------------------------------------


def test_issue_sink_records_a_finding_with_default_severity_and_retains_claim():
    rec = _recorder()
    claim = _claim()
    rec.record_claim(claim, [])
    sink = rec.issue_sink()
    assert callable(sink)
    details = {"node_id": claim.node_id, "reference": "bogus-loc", "field_index": 3}
    assert sink("dangling_reference", "loc contains unknown evidence ID 'bogus-loc'", details) is None
    snap = rec.snapshot()
    assert snap.nodes[claim.node_id] == claim
    assert len(snap.findings) == 1
    finding = snap.findings[0]
    assert finding.code == "dangling_reference"
    assert finding.severity == DEFAULT_SEVERITIES["dangling_reference"]
    assert finding.node_id == claim.node_id
    assert finding.edge_id is None
    assert finding.message == "loc contains unknown evidence ID 'bogus-loc'"
    assert finding.details == details


@pytest.mark.parametrize("code", ["dangling_reference", "duplicate_reference", "ambiguous_reference"])
def test_issue_sink_accepts_every_reference_issue_kind(code):
    rec = _recorder()
    rec.issue_sink()(code, "msg", {"reference": "r"})
    finding = rec.snapshot().findings[0]
    assert finding.code == code
    assert finding.severity == DEFAULT_SEVERITIES[code]


def test_issue_sink_without_subject_ids_records_none_for_both():
    rec = _recorder()
    rec.issue_sink()("duplicate_reference", "dup", {"reference": "r", "item_index": 0})
    finding = rec.snapshot().findings[0]
    assert finding.node_id is None
    assert finding.edge_id is None


def test_issue_sink_edge_subject_is_taken_from_details():
    rec = _recorder()
    rec.issue_sink()("ambiguous_reference", "amb", {"edge_id": "cites_evidence:a->b"})
    assert rec.snapshot().findings[0].edge_id == "cites_evidence:a->b"


def test_issue_sink_does_not_alias_the_callers_details():
    rec = _recorder()
    details = {"reference": "r", "candidates": ["a", "b"]}
    rec.issue_sink()("ambiguous_reference", "amb", details)
    details["reference"] = "changed"
    details["candidates"].append("c")
    assert rec.snapshot().findings[0].details == {"reference": "r", "candidates": ["a", "b"]}


def test_issue_sink_repeated_identical_issue_is_idempotent():
    rec = _recorder()
    sink = rec.issue_sink()
    sink("dangling_reference", "m", {"reference": "r"})
    sink("dangling_reference", "m", {"reference": "r"})
    assert len(rec.snapshot().findings) == 1


def test_issue_sink_rejects_an_unknown_finding_code():
    rec = _recorder()
    with pytest.raises(ValueError):
        rec.issue_sink()("not_a_code", "m", {})
    assert rec.snapshot().findings == ()


def test_issue_sink_rejects_malformed_arguments():
    sink = _recorder().issue_sink()
    with pytest.raises(TypeError):
        sink("dangling_reference", 42, {})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        sink("dangling_reference", "m", ["not", "a", "mapping"])  # type: ignore[arg-type]


def test_issue_sink_writes_into_the_same_recorder_each_call():
    rec = _recorder()
    rec.issue_sink()("dangling_reference", "m1", {"reference": "r1"})
    rec.issue_sink()("dangling_reference", "m2", {"reference": "r2"})
    assert [f.message for f in rec.snapshot().findings] == ["m1", "m2"]


# ---------------------------------------------------------------------------
# Stage status and undeclared stages (1.6, 2.1)
# ---------------------------------------------------------------------------


def test_every_declared_stage_starts_not_run():
    snap = _recorder().snapshot()
    assert tuple(snap.stage_status) == STAGE_ORDER
    assert set(snap.stage_status.values()) == {"not_run"}
    assert snap.stage_failure_reasons == {}


def test_mark_stage_complete_and_failed():
    rec = _recorder()
    rec.mark_stage_complete("evidence_index")
    rec.mark_stage_failed("synthesis", "model timeout")
    snap = rec.snapshot()
    assert snap.stage_status["evidence_index"] == "complete"
    assert snap.stage_status["synthesis"] == "failed"
    assert snap.stage_failure_reasons == {"synthesis": "model timeout"}
    untouched = set(STAGE_ORDER) - {"evidence_index", "synthesis"}
    assert {snap.stage_status[s] for s in untouched} == {"not_run"}


def test_a_failure_is_never_erased_by_a_later_completion():
    rec = _recorder()
    rec.mark_stage_failed("extraction_chunk", "chunk 2 failed for paper B")
    rec.mark_stage_complete("extraction_chunk")
    snap = rec.snapshot()
    assert snap.stage_status["extraction_chunk"] == "failed"
    assert snap.stage_failure_reasons["extraction_chunk"] == "chunk 2 failed for paper B"


def test_a_failure_after_completion_escalates_the_stage_to_failed():
    rec = _recorder()
    rec.mark_stage_complete("extraction_chunk")
    rec.mark_stage_failed("extraction_chunk", "late failure")
    assert rec.snapshot().stage_status["extraction_chunk"] == "failed"


def test_the_first_failure_reason_is_kept():
    rec = _recorder()
    rec.mark_stage_failed("synthesis", "first")
    rec.mark_stage_failed("synthesis", "second")
    assert rec.snapshot().stage_failure_reasons["synthesis"] == "first"


def test_mark_stage_failed_requires_a_string_reason():
    with pytest.raises(TypeError):
        _recorder().mark_stage_failed("synthesis", None)  # type: ignore[arg-type]


def test_a_stage_that_emitted_nothing_does_not_prevent_recording_others():
    rec = _recorder()
    rec.mark_stage_failed("evidence_index", "TEI unavailable")
    rec.record_source(_source())
    rec.record_derivation(_derivation())
    snap = rec.snapshot()
    assert len(snap.nodes) == 2
    assert snap.stage_status["evidence_index"] == "failed"


@pytest.mark.parametrize("enabled", [True, False])
def test_undeclared_stage_is_rejected_on_derivation(enabled):
    rec = _recorder(enabled=enabled)
    step = _derivation(stage="rogue_stage")
    with pytest.raises(UnknownStageError):
        rec.record_derivation(step)
    assert rec.snapshot().nodes == {}


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("mark", ["complete", "failed"])
def test_undeclared_stage_is_rejected_on_stage_marks(enabled, mark):
    rec = _recorder(enabled=enabled)
    with pytest.raises(UnknownStageError):
        if mark == "complete":
            rec.mark_stage_complete("rogue_stage")
        else:
            rec.mark_stage_failed("rogue_stage", "why")
    assert "rogue_stage" not in rec.snapshot().stage_status


def test_every_stage_the_recorder_accepts_is_declared_in_both_tables():
    """Contract-drift guard: the recorder accepts exactly the declared stages."""
    for stage in STAGE_ORDER:
        rec = _recorder()
        rec.mark_stage_complete(stage)
        rec.record_derivation(_derivation(stage=stage))
        assert stage in STAGE_CONTRACTS
        assert rec.snapshot().stage_status[stage] == "complete"


# ---------------------------------------------------------------------------
# Per-document stage marks (task 4.6; 2.1, 2.5)
# ---------------------------------------------------------------------------

_SRC_A = derive_source_id(_FP_A)
_SRC_B = derive_source_id(_FP_B)


@pytest.mark.parametrize("method", ["mark_stage_complete", "mark_stage_failed"])
def test_stage_marks_take_an_optional_keyword_only_source_id(method):
    params = inspect.signature(getattr(ProvenanceRecorder, method)).parameters
    assert "source_id" in params
    assert params["source_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["source_id"].default is None


def test_run_wide_marks_leave_the_per_document_status_empty():
    rec = _recorder()
    rec.mark_stage_complete("evidence_index")
    rec.mark_stage_failed("synthesis", "model timeout")
    snap = rec.snapshot()
    assert snap.document_stage_status == {}
    assert snap.document_stage_failure_reasons == {}
    assert snap.stage_status["synthesis"] == "failed"
    assert snap.stage_failure_reasons == {"synthesis": "model timeout"}


def test_a_per_document_mark_applies_to_that_document_only_and_leaves_the_run_wide_status():
    rec = _recorder()
    rec.mark_stage_complete("extraction_chunk", source_id=_SRC_A)
    rec.mark_stage_failed("synthesis", "model timeout", source_id=_SRC_B)
    snap = rec.snapshot()
    assert snap.document_stage_status == {
        _SRC_A: {"extraction_chunk": "complete"},
        _SRC_B: {"synthesis": "failed"},
    }
    assert snap.document_stage_failure_reasons == {_SRC_B: {"synthesis": ("model timeout",)}}
    assert set(snap.stage_status.values()) == {"not_run"}
    assert snap.stage_failure_reasons == {}


def test_per_document_marks_are_escalate_only():
    rec = _recorder()
    rec.mark_stage_failed("synthesis", "first", source_id=_SRC_A)
    rec.mark_stage_complete("synthesis", source_id=_SRC_A)
    rec.mark_stage_complete("extraction_chunk", source_id=_SRC_A)
    rec.mark_stage_failed("extraction_chunk", "late", source_id=_SRC_A)
    status = rec.snapshot().document_stage_status[_SRC_A]
    assert status == {"synthesis": "failed", "extraction_chunk": "failed"}


def test_every_distinct_per_document_failure_reason_is_kept_in_recording_order():
    rec = _recorder()
    rec.mark_stage_failed("synthesis", "chunk 2 timed out", source_id=_SRC_A)
    rec.mark_stage_failed("synthesis", "invalid JSON", source_id=_SRC_A)
    rec.mark_stage_failed("synthesis", "chunk 2 timed out", source_id=_SRC_A)
    rec.mark_stage_failed("synthesis", "other paper", source_id=_SRC_B)
    reasons = rec.snapshot().document_stage_failure_reasons
    assert reasons[_SRC_A] == {"synthesis": ("chunk 2 timed out", "invalid JSON")}
    assert reasons[_SRC_B] == {"synthesis": ("other paper",)}


def test_run_wide_and_per_document_marks_are_independent():
    rec = _recorder()
    rec.mark_stage_complete("synthesis")
    rec.mark_stage_failed("synthesis", "doc-level", source_id=_SRC_A)
    snap = rec.snapshot()
    assert snap.stage_status["synthesis"] == "complete"
    assert snap.stage_failure_reasons == {}
    assert snap.document_stage_status == {_SRC_A: {"synthesis": "failed"}}


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("bad", ["", "aaaabbbb", "src:", "src:a#b", "src:a b", "claim:src:a#f1", 3, b"src:a"])
@pytest.mark.parametrize("mark", ["complete", "failed"])
def test_a_malformed_source_id_is_rejected_and_records_nothing(enabled, bad, mark):
    rec = _recorder(enabled=enabled)
    with pytest.raises(InvalidIdentifierError):
        if mark == "complete":
            rec.mark_stage_complete("synthesis", source_id=bad)
        else:
            rec.mark_stage_failed("synthesis", "why", source_id=bad)
    snap = rec.snapshot()
    assert snap.document_stage_status == {}
    assert snap.document_stage_failure_reasons == {}
    assert set(snap.stage_status.values()) == {"not_run"}


@pytest.mark.parametrize("mark", ["complete", "failed"])
def test_undeclared_stage_is_rejected_on_per_document_marks(mark):
    rec = _recorder()
    with pytest.raises(UnknownStageError):
        if mark == "complete":
            rec.mark_stage_complete("rogue_stage", source_id=_SRC_A)
        else:
            rec.mark_stage_failed("rogue_stage", "why", source_id=_SRC_A)
    assert rec.snapshot().document_stage_status == {}


def test_per_document_failure_requires_a_string_reason():
    with pytest.raises(TypeError):
        _recorder().mark_stage_failed("synthesis", None, source_id=_SRC_A)  # type: ignore[arg-type]


def test_disabled_mode_records_no_per_document_marks():
    rec = _recorder(enabled=False)
    rec.mark_stage_complete("evidence_index", source_id=_SRC_A)
    rec.mark_stage_failed("synthesis", "boom", source_id=_SRC_A)
    snap = rec.snapshot()
    assert snap.document_stage_status == {}
    assert snap.document_stage_failure_reasons == {}


def test_per_document_snapshot_is_immutable_detached_and_stable():
    rec = _recorder()
    rec.mark_stage_failed("synthesis", "boom", source_id=_SRC_A)
    snap = rec.snapshot()
    assert rec.snapshot() == snap
    assert hash(rec.snapshot()) == hash(snap)
    with pytest.raises(TypeError):
        snap.document_stage_status[_SRC_B] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        snap.document_stage_status[_SRC_A]["evidence_index"] = "complete"  # type: ignore[index]
    with pytest.raises(TypeError):
        snap.document_stage_failure_reasons[_SRC_A]["synthesis"] = ()  # type: ignore[index]
    rec.mark_stage_failed("synthesis", "again", source_id=_SRC_A)
    rec.mark_stage_complete("evidence_index", source_id=_SRC_B)
    assert snap.document_stage_status == {_SRC_A: {"synthesis": "failed"}}
    assert snap.document_stage_failure_reasons == {_SRC_A: {"synthesis": ("boom",)}}


def test_per_document_snapshot_order_is_independent_of_marking_order():
    first, second = _recorder(), _recorder()
    first.mark_stage_complete("synthesis", source_id=_SRC_B)
    first.mark_stage_complete("evidence_index", source_id=_SRC_A)
    second.mark_stage_complete("evidence_index", source_id=_SRC_A)
    second.mark_stage_complete("synthesis", source_id=_SRC_B)
    a, b = first.snapshot(), second.snapshot()
    assert list(a.document_stage_status) == list(b.document_stage_status) == [_SRC_A, _SRC_B]


# ---------------------------------------------------------------------------
# Snapshot: immutable and stable
# ---------------------------------------------------------------------------


def test_snapshot_is_a_recorded_run_carrying_the_run_identity():
    snap = _recorder().snapshot()
    assert isinstance(snap, RecordedRun)
    assert snap.run == _run_identity()


def test_snapshot_twice_returns_equal_values():
    rec = _recorder()
    _record_one_of_everything(rec)
    first = rec.snapshot()
    second = rec.snapshot()
    assert first == second
    assert hash(first) == hash(second)


def test_snapshot_is_immutable_and_detached_from_the_recorder():
    rec = _recorder()
    rec.record_source(_source())
    snap = rec.snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.nodes = {}  # type: ignore[misc]
    with pytest.raises(TypeError):
        snap.nodes["x"] = _source()  # type: ignore[index]
    with pytest.raises(TypeError):
        snap.stage_status["synthesis"] = "complete"  # type: ignore[index]
    assert isinstance(snap.edges, tuple)
    assert isinstance(snap.findings, tuple)
    rec.record_evidence([_evidence("later")])
    rec.mark_stage_complete("synthesis")
    assert len(snap.nodes) == 1
    assert snap.stage_status["synthesis"] == "not_run"


def test_snapshot_preserves_recording_order():
    rec = _recorder()
    ids = [rec.record_source(_source()), *rec.record_evidence([_evidence("z"), _evidence("a")])]
    assert list(rec.snapshot().nodes) == ids


# ---------------------------------------------------------------------------
# Disabled mode
# ---------------------------------------------------------------------------


def test_disabled_mode_returns_identifiers_but_records_nothing():
    rec = _recorder(enabled=False)
    claim = _claim()
    assert rec.record_source(_source()) == _source().node_id
    assert rec.record_evidence([_evidence("s12"), _evidence("s13")]) == (
        _evidence("s12").node_id,
        _evidence("s13").node_id,
    )
    assert rec.record_claim(claim, [_cites(claim, "x")]) == claim.node_id
    assert rec.record_derivation(_derivation()) == _derivation().node_id
    assert rec.record_validation(_validation()) == _validation().node_id
    assert rec.record_issue(_finding()) is None
    sink = rec.issue_sink()
    assert sink("dangling_reference", "m", {"reference": "r"}) is None
    rec.mark_stage_complete("evidence_index")
    rec.mark_stage_failed("synthesis", "boom")
    snap = rec.snapshot()
    assert snap.run == _run_identity()
    assert snap.nodes == {}
    assert snap.edges == ()
    assert snap.findings == ()
    assert set(snap.stage_status.values()) == {"not_run"}
    assert snap.stage_failure_reasons == {}


def test_disabled_and_enabled_return_the_same_identifiers():
    calls = [
        lambda r: r.record_source(_source()),
        lambda r: r.record_evidence([_evidence("s1")]),
        lambda r: r.record_claim(_claim(), []),
        lambda r: r.record_derivation(_derivation()),
        lambda r: r.record_validation(_validation()),
    ]
    for call in calls:
        assert call(_recorder(enabled=True)) == call(_recorder(enabled=False))


# ---------------------------------------------------------------------------
# Concurrency: one lock guards mutation and snapshot copying
# ---------------------------------------------------------------------------


def test_concurrent_recording_from_many_threads_loses_nothing():
    rec = _recorder()
    n_threads, per_thread = 8, 150
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []

    def worker(t: int) -> None:
        try:
            barrier.wait()
            fingerprint = f"fp{t:04d}"
            rec.record_source(_source(fingerprint))
            for i in range(per_thread):
                rec.record_evidence([_evidence(f"s{i}", fingerprint=fingerprint)])
                claim = _claim(i, fingerprint=fingerprint)
                rec.record_claim(claim, [_cites(claim, _evidence(f"s{i}", fingerprint=fingerprint).node_id)])
                rec.issue_sink()("dangling_reference", f"t{t}-{i}", {"reference": f"r{t}-{i}"})
                if i % 10 == 0:
                    rec.snapshot()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    snap = rec.snapshot()
    assert len(snap.nodes) == n_threads * (1 + 2 * per_thread)
    assert len(snap.edges) == n_threads * per_thread
    assert len(snap.findings) == n_threads * per_thread
    assert len({f.finding_id for f in snap.findings}) == len(snap.findings)


def test_concurrent_identical_duplicates_collapse_to_one_record_without_findings():
    rec = _recorder()
    barrier = threading.Barrier(6)

    def worker() -> None:
        barrier.wait()
        for _ in range(200):
            rec.record_source(_source())
            rec.record_derivation(_derivation())

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    snap = rec.snapshot()
    assert len(snap.nodes) == 2
    assert snap.findings == ()


def _blocks_while_lock_held(rec: ProvenanceRecorder, action) -> bool:
    done = threading.Event()

    def run() -> None:
        action()
        done.set()

    with rec._lock:
        th = threading.Thread(target=run)
        th.start()
        blocked = not done.wait(0.2)
    th.join(2.0)
    return blocked and done.is_set()


@pytest.mark.parametrize(
    "action",
    [
        lambda r: r.snapshot(),
        lambda r: r.record_source(_source()),
        lambda r: r.record_evidence([_evidence("s1")]),
        lambda r: r.record_claim(_claim(), []),
        lambda r: r.record_derivation(_derivation()),
        lambda r: r.record_validation(_validation()),
        lambda r: r.record_issue(_finding()),
        lambda r: r.issue_sink()("dangling_reference", "m", {}),
        lambda r: r.mark_stage_complete("synthesis"),
        lambda r: r.mark_stage_failed("synthesis", "x"),
        lambda r: r.mark_stage_complete("synthesis", source_id=derive_source_id(_FP_A)),
        lambda r: r.mark_stage_failed("synthesis", "x", source_id=derive_source_id(_FP_A)),
    ],
)
def test_mutation_and_snapshot_wait_for_the_single_lock(action):
    rec = _recorder()
    assert _blocks_while_lock_held(rec, lambda: action(rec))


def test_recorder_holds_exactly_one_lock():
    rec = _recorder()
    lock_type = type(threading.Lock())
    locks = [v for v in vars(rec).values() if isinstance(v, lock_type)]
    assert len(locks) == 1
    assert locks[0] is rec._lock


# ---------------------------------------------------------------------------
# No file or network access (1.5); no global state
# ---------------------------------------------------------------------------

_RECORDER_SOURCE = Path(recorder_module.__file__)

_FORBIDDEN_IMPORT_ROOTS = {
    "io",
    "os",
    "pathlib",
    "shutil",
    "tempfile",
    "socket",
    "ssl",
    "http",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    "sqlite3",
    "pickle",
    "shelve",
    "subprocess",
    "hashlib",
}
_FORBIDDEN_CALLS = {"open", "print", "exec", "eval", "__import__"}


def test_recorder_source_has_no_file_or_network_imports_or_calls():
    tree = ast.parse(_RECORDER_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in _FORBIDDEN_CALLS, f"recorder calls {node.func.id}()"
    assert not (imported & _FORBIDDEN_IMPORT_ROOTS), imported & _FORBIDDEN_IMPORT_ROOTS


def test_recorder_imports_only_stdlib_provenance_and_logging():
    tree = ast.parse(_RECORDER_SOURCE.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    first_party = {m for m in modules if m.split(".")[0] in {"provenance", "utils", "pipeline", "agents",
                                                             "pdf_extractor", "quality_control"}}
    assert first_party <= {
        "provenance.models",
        "provenance.contracts",
        "provenance.identity",
        "utils.logging_utils",
    }


def test_recorder_module_holds_no_recorder_singleton():
    for value in vars(recorder_module).values():
        assert not isinstance(value, ProvenanceRecorder)


def test_full_recording_session_performs_no_file_or_network_access(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("ProvenanceRecorder attempted file or network access")

    monkeypatch.setattr(builtins, "open", deny)
    monkeypatch.setattr(io, "open", deny)
    monkeypatch.setattr(Path, "open", deny)
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)

    for enabled in (True, False):
        rec = ProvenanceRecorder(_run_identity(), enabled=enabled)
        _record_one_of_everything(rec)
        rec.record_source(_source(label="conflict.pdf"))
        rec.snapshot()
        rec.snapshot()
