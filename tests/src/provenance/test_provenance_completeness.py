"""Tests for ``provenance.completeness`` — provenance completeness at run, document and claim level.

Covers task 4.5 of provenance-core (design "Analysis Layer → CompletenessComputer",
requirements 2.1–2.6):

- the stage-contract diff: a stage whose status is ``complete`` but that did
  not emit its declared record kinds makes the affected artifact
  ``incomplete``, naming the absent kinds and the responsible stage (2.1, 2.3);
  a ``not_run`` stage is never treated as missing;
- three-level states with the aggregation rule: a document is the most
  severe of its claims, the run the most severe of its documents, and
  ``undetermined`` outranks ``incomplete`` (2.2);
- claim support computed from the graph: ``missing_anchor`` and
  ``unsupported`` are distinct reasons and never merged (2.4);
- ``undetermined`` with the recorded reason for artifacts covered by a failed
  stage or a missing segment, attributed per document where the graph allows
  (2.5);
- both ``stage_contracts_strict`` behaviours;
- data only: no file, no rendered output, attachment via ``attach_completeness`` (2.6);
- a realistic run (real adapters + recorder + ``build_graph``) is
  ``complete`` at all three levels.

Synthetic identifiers only.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import io
import os
import typing
from pathlib import Path

import pytest

from provenance import completeness as completeness_module
from provenance.adapters import claim_nodes_from_fields
from provenance.completeness import compute_completeness
from provenance.contracts import STAGE_CONTRACTS, STAGE_ORDER
from provenance.graph import ProvenanceGraph, attach_completeness, attach_validation, build_graph
from provenance.identity import (
    derive_claim_id,
    derive_derivation_id,
    derive_source_id,
    format_evidence_id,
)
from provenance.models import (
    UNKNOWN_PRIVACY,
    ArtifactCompleteness,
    ChainValidationReport,
    ClaimNode,
    CompletenessReport,
    DerivationStep,
    EvidenceNode,
    ProvenanceEdge,
    ProvenanceNode,
    RecordedRun,
    RunIdentity,
    SourceAnchor,
    SourceNode,
)
from provenance.recorder import ProvenanceRecorder
from provenance.validation import validate_chain

_RUN = "run:0123456789abcdef"
_FP = "aaaabbbbccccdddd"
_FP2 = "eeeeffff00001111"
_SRC = derive_source_id(_FP)
_SRC2 = derive_source_id(_FP2)
_E1 = format_evidence_id(_SRC, "e1")
_E2 = format_evidence_id(_SRC, "e2")
_MODULE_PATH = Path(completeness_module.__file__)

_COMPLETE = ArtifactCompleteness(state="complete", reason=None, missing_record_kinds=(), responsible_stages=())


@pytest.fixture(autouse=True)
def _no_safety_net(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if a test's expectation is met only through the catch-all degradation path."""
    if "safety_net" in request.node.name:
        return

    def _raise(*args: object, **_kwargs: object) -> typing.NoReturn:
        raise AssertionError(f"compute_completeness hit its safety net: {args}")

    monkeypatch.setattr(completeness_module.logger, "warning", _raise)


# ---------------------------------------------------------------------------
# Minimal record builders
# ---------------------------------------------------------------------------


def _run_identity(*fingerprints: str) -> RunIdentity:
    return RunIdentity(
        run_id=_RUN,
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
        document_fingerprints={derive_source_id(fp): fp for fp in fingerprints},
        created_at="2026-10-08T00:00:00+00:00",
    )


def _source(fp: str = _FP) -> SourceNode:
    return SourceNode(
        node_id=derive_source_id(fp),
        kind="source",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        document_fingerprint=fp,
        source_format="pdf",
        source_label="lorem.pdf",
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
    )


def _anchor(precision: str) -> SourceAnchor:
    if precision == "exact":
        return SourceAnchor(precision="exact", page=1, coords=(1.0, 2.0, 3.0, 4.0), section_path="Methods")
    if precision == "approximate":
        return SourceAnchor(precision="approximate", page=1, missing=("coords",))
    if precision == "absent":
        return SourceAnchor(precision="absent", missing=("page", "coords", "section_path", "structural_path"))
    return SourceAnchor(precision=precision)  # type: ignore[arg-type] -- deliberately out of vocabulary


def _evidence(local_id: str, *, source_id: str = _SRC, precision: str = "exact") -> EvidenceNode:
    return EvidenceNode(
        node_id=format_evidence_id(source_id, local_id),
        kind="evidence",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        local_id=local_id,
        evidence_kind="sentence",
        source_id=source_id,
        anchor=_anchor(precision),
        producing_backend="grobid",
        rank_score=3,
        parser_metadata={},
    )


def _claim(cited: tuple[str, ...], *, field_index: int = 7, source_id: str = _SRC, support: str = "supported") -> ClaimNode:
    return ClaimNode(
        node_id=derive_claim_id(source_id, field_index),
        kind="claim",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        source_id=source_id,
        field_index=field_index,
        field_name="sample_size",
        value_digest="0123456789abcdef",
        origin="model",
        support=support,  # type: ignore[arg-type]
        cited_evidence_ids=cited,
        confidence="high",
    )


def _step(inputs: tuple[str, ...], outputs: tuple[str, ...], *, stage: str = "extraction_chunk", kind: str = "extract") -> DerivationStep:
    return DerivationStep(
        node_id=derive_derivation_id(stage, kind, inputs, outputs),
        kind="derivation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        stage=stage,
        derivation_kind=kind,  # type: ignore[arg-type]
        determinism="probabilistic",
        model_id="gpt-x",
        input_ids=inputs,
        output_ids=outputs,
    )


def _cites(*claims: ClaimNode) -> list[ProvenanceEdge]:
    return [
        ProvenanceEdge(edge_id=f"cites_evidence:{c.node_id}->{t}", kind="cites_evidence", from_id=c.node_id, to_id=t, attributes={})
        for c in claims
        for t in dict.fromkeys(c.cited_evidence_ids)
    ]


_BASE_STATUS = {
    "document_extraction": "complete",
    "evidence_index": "complete",
    "extraction_chunk": "complete",
}


def _recorded(
    nodes: typing.Iterable[object],
    *,
    edges: typing.Iterable[ProvenanceEdge] = (),
    status: typing.Mapping[str, str] | None = None,
    reasons: typing.Mapping[str, str] | None = None,
    fingerprints: tuple[str, ...] = (_FP,),
    raw_nodes: typing.Mapping[str, object] | None = None,
) -> RecordedRun:
    node_map: dict[str, object] = {getattr(n, "node_id"): n for n in nodes}
    node_map.update(raw_nodes or {})
    full_status = {stage: "not_run" for stage in STAGE_ORDER}
    full_status.update(_BASE_STATUS if status is None else status)
    return RecordedRun(
        run=_run_identity(*fingerprints),
        nodes=node_map,  # type: ignore[arg-type]
        edges=tuple(edges),
        findings=(),
        stage_status=full_status,  # type: ignore[arg-type]
        stage_failure_reasons=dict(reasons or {}),
    )


def _minimal_nodes() -> list[ProvenanceNode]:
    """One document: source, one exact evidence node, one claim citing it, one producing chunk step."""
    claim = _claim((_E1,))
    return [_source(), _evidence("e1"), claim, _step((_E1,), (claim.node_id,))]


def _compute(recorded: RecordedRun, **kwargs: typing.Any) -> tuple[ProvenanceGraph, CompletenessReport]:
    graph = build_graph(recorded)
    return graph, compute_completeness(graph, recorded, **kwargs)


def _with_edges(nodes: list[ProvenanceNode]) -> list[ProvenanceEdge]:
    return _cites(*(n for n in nodes if isinstance(n, ClaimNode)))


# ---------------------------------------------------------------------------
# Baseline: a fully traced run is complete at every level (2.2)
# ---------------------------------------------------------------------------


def test_minimal_traced_run_is_complete_at_all_three_levels() -> None:
    nodes = _minimal_nodes()
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    assert isinstance(report, CompletenessReport)
    assert report.run == _COMPLETE
    assert dict(report.documents) == {_SRC: _COMPLETE}
    assert dict(report.claims) == {derive_claim_id(_SRC, 7): _COMPLETE}


def _realistic_recorder(*, fail: tuple[str, str] | None = None) -> ProvenanceRecorder:
    """Two documents through the real adapters and recorder (same shape as the validation suite)."""
    from provenance.adapters import (
        derivation_from_stage_event,
        derivations_from_qc_provenance,
        evidence_nodes_from_items,
        run_identity_from_identities,
        source_node_from_identity,
    )

    identities = {
        name: {"pdf_content_hash": fp, "config_hash": "cfg123", "extraction_map_hash": "sch456", "model_id": "gpt-x"}
        for name, fp in (("a.pdf", _FP), ("b.pdf", _FP2))
    }
    run = run_identity_from_identities(identities, created_at="2026-10-08T00:00:00+00:00")
    recorder = ProvenanceRecorder(run)
    for name, identity in identities.items():
        source = source_node_from_identity(identity, source_label=name, source_format="pdf", run_id=run.run_id)
        source_id = source.node_id
        recorder.record_source(source)
        recorder.mark_stage_complete("document_extraction")
        for step in derivations_from_qc_provenance({"branch": "grobid", "decision": "keep"}, source_id=source_id, run_id=run.run_id):
            recorder.record_derivation(step)
        recorder.mark_stage_complete("quality_control")
        items = [
            {"id": "title", "type": "sentence", "page": 1, "coords": [1.0, 1.0, 2.0, 2.0], "section_path": "Metadata",
             "xpath": ".//title", "score": 9},
            *(
                {"id": f"s{n}", "type": "sentence", "page": n, "coords": [1.0, 2.0, 3.0, 4.0],
                 "section_path": "Methods", "xpath": f".//s[@xml:id='s{n}']", "score": 3}
                for n in range(1, 6)
            ),
        ]
        evidence = evidence_nodes_from_items(items, source_id=source_id, run_id=run.run_id, producing_backend="grobid")
        recorder.record_evidence(evidence)
        evidence_ids = [node.node_id for node in evidence]
        recorder.record_derivation(  # in-place filter: kept ids are both input and output
            derivation_from_stage_event("evidence_index", "filter", run_id=run.run_id, inputs=evidence_ids,
                                        outputs=evidence_ids[:-1], removal_reason="below minimum score",
                                        removed_ids=evidence_ids[-1:])
        )
        recorder.mark_stage_complete("evidence_index")
        known = {node.node_id for node in evidence}
        anchors = {node.node_id: node.anchor for node in evidence}

        def _claims(fields: list[dict], origin: str) -> list[ClaimNode]:
            claims, claim_edges, findings = claim_nodes_from_fields(
                fields, source_id=source_id, run_id=run.run_id, known_evidence_ids=known,
                origin=origin, evidence_anchors=anchors,  # type: ignore[arg-type]
            )
            assert findings == []
            for claim in claims:
                recorder.record_claim(claim, [e for e in claim_edges if e.from_id == claim.node_id])
            return claims

        # User decision: prefilled fields cite the TEI metadata (title) evidence.
        prefilled = _claims(
            [{"field_index": 1, "field_name": "author", "extracted_value": "Lorem", "location": ["title"]}], "prefill"
        )
        recorder.record_derivation(
            derivation_from_stage_event("metadata_prefill", "prefill", run_id=run.run_id,
                                        inputs=[format_evidence_id(source_id, "title")],
                                        outputs=[c.node_id for c in prefilled])
        )
        extracted = _claims(
            [
                {"field_index": 7, "field_name": "sample_size", "extracted_value": "42", "location": ["s1", "s2"], "confidence": "high"},
                {"field_index": 8, "field_name": "design", "extracted_value": "RCT", "location": ["s3"], "confidence": "medium"},
            ],
            "model",
        )
        recorder.record_derivation(
            derivation_from_stage_event("extraction_chunk", "extract", run_id=run.run_id,
                                        inputs=[format_evidence_id(source_id, f"s{n}") for n in (1, 2, 3)],
                                        outputs=[c.node_id for c in extracted], model_id="gpt-x")
        )
        base_id = derive_claim_id(source_id, 7)
        synthesized = _claims(
            [{"field_index": 7, "revision": 1, "field_name": "sample_size", "extracted_value": "42",
              "location": ["s1"], "confidence": "high", "contributing_claim_ids": [base_id]}],
            "model",
        )
        recorder.record_derivation(
            derivation_from_stage_event("synthesis", "synthesize", run_id=run.run_id, inputs=[base_id],
                                        outputs=[c.node_id for c in synthesized], model_id="gpt-x")
        )
        for stage in ("metadata_prefill", "extraction_chunk", "synthesis"):
            recorder.mark_stage_complete(stage)
        if fail is not None and name == "a.pdf":
            recorder.mark_stage_failed(*fail)
    return recorder


_REALISTIC_CLAIMS = sorted(
    derive_claim_id(src, index, revision)
    for src in (_SRC, _SRC2)
    for index, revision in ((1, 0), (7, 0), (8, 0), (7, 1))
)


def test_realistic_successful_run_is_complete_at_all_three_levels() -> None:
    """Real adapters + recorder + build_graph; every claim cites anchored evidence (prefill cites the title)."""
    recorded = _realistic_recorder().snapshot()
    graph = build_graph(recorded)

    report = compute_completeness(graph, recorded)

    assert report.run == _COMPLETE
    assert dict(report.documents) == {_SRC: _COMPLETE, _SRC2: _COMPLETE}
    assert sorted(report.claims) == _REALISTIC_CLAIMS
    assert all(state == _COMPLETE for state in report.claims.values())


def test_realistic_run_with_chain_validation_attached_and_marked_complete_is_complete() -> None:
    """The orchestrator path: validate, attach, mark chain_validation complete, compute (6.5)."""
    recorder = _realistic_recorder()
    graph = build_graph(recorder.snapshot())
    validation = validate_chain(graph)
    validated = attach_validation(graph, validation)
    recorder.mark_stage_complete("chain_validation")

    report = compute_completeness(validated, recorder.snapshot(), validation)

    assert report.run == _COMPLETE
    assert all(state == _COMPLETE for state in report.documents.values())


# ---------------------------------------------------------------------------
# Stage-contract diff (2.1, 2.3)
# ---------------------------------------------------------------------------


def test_complete_stage_without_its_declared_records_makes_the_document_incomplete() -> None:
    """evidence_index marked complete but no evidence node exists for the document."""
    claim = _claim(())
    nodes: list[ProvenanceNode] = [_source(), claim, _step((_SRC,), (claim.node_id,))]
    _, report = _compute(_recorded(nodes))

    document = report.documents[_SRC]
    assert document.state == "incomplete"
    assert "missing_records" in (document.reason or "")
    assert "evidence" in document.missing_record_kinds
    assert "evidence_index" in document.responsible_stages
    assert report.run.state == "incomplete"


def test_document_listed_in_the_run_identity_without_a_source_node_is_incomplete() -> None:
    nodes = _minimal_nodes()
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes), fingerprints=(_FP, _FP2)))

    assert report.documents[_SRC] == _COMPLETE
    other = report.documents[_SRC2]
    assert other.state == "incomplete"
    assert other.missing_record_kinds == ("claim", "derivation", "evidence", "source")
    assert other.responsible_stages == ("document_extraction", "evidence_index", "extraction_chunk")
    assert report.run.state == "incomplete"


def test_not_run_stages_are_never_treated_as_missing() -> None:
    """num_chunks=1 yields no extraction_chunk; chain_validation reads not_run before 6.5 marks it."""
    claim = _claim((_E1,))
    nodes: list[ProvenanceNode] = [_source(), _evidence("e1"), claim, _step((_E1,), (claim.node_id,), stage="synthesis", kind="synthesize")]
    status = {"document_extraction": "complete", "evidence_index": "complete", "synthesis": "complete"}
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes), status=status))

    assert report.run == _COMPLETE
    assert report.documents[_SRC] == _COMPLETE


def test_claim_without_a_producing_derivation_step_is_incomplete_for_the_claim_stages() -> None:
    claim = _claim((_E1,))
    other = _claim((_E1,), field_index=8)
    nodes: list[ProvenanceNode] = [_source(), _evidence("e1"), claim, other, _step((_E1,), (claim.node_id,))]
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    assert report.claims[claim.node_id] == _COMPLETE
    orphan = report.claims[other.node_id]
    assert orphan.state == "incomplete"
    assert orphan.missing_record_kinds == ("derivation",)
    assert orphan.responsible_stages == ("extraction_chunk",)
    assert report.documents[_SRC].state == "incomplete"


def test_each_claim_is_tied_only_to_the_stage_that_produced_it() -> None:
    """A synthesized claim need not also be produced by extraction_chunk (and vice versa)."""
    base = _claim((_E1,))
    synth = dataclasses.replace(_claim((_E1,)), node_id=derive_claim_id(_SRC, 7, 1))
    nodes: list[ProvenanceNode] = [
        _source(),
        _evidence("e1"),
        base,
        synth,
        _step((_E1,), (base.node_id,)),
        _step((base.node_id,), (synth.node_id,), stage="synthesis", kind="synthesize"),
    ]
    status = {**_BASE_STATUS, "synthesis": "complete"}
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes), status=status))

    assert report.claims[base.node_id] == _COMPLETE
    assert report.claims[synth.node_id] == _COMPLETE
    assert report.run == _COMPLETE


def test_complete_claim_stage_that_emitted_nothing_for_a_document_is_reported() -> None:
    """metadata_prefill marked complete (run-wide) but it emitted nothing for this document."""
    nodes = _minimal_nodes()
    status = {**_BASE_STATUS, "metadata_prefill": "complete"}
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes), status=status))

    document = report.documents[_SRC]
    assert document.state == "incomplete"
    assert document.missing_record_kinds == ("claim", "derivation")
    assert document.responsible_stages == ("metadata_prefill",)


def test_run_scoped_stage_complete_without_a_validation_record_makes_the_run_incomplete() -> None:
    nodes = _minimal_nodes()
    status = {**_BASE_STATUS, "chain_validation": "complete"}
    recorded = _recorded(nodes, edges=_with_edges(nodes), status=status)
    graph, report = _compute(recorded)

    assert report.documents[_SRC] == _COMPLETE
    assert report.run.state == "incomplete"
    assert report.run.missing_record_kinds == ("validation",)
    assert report.run.responsible_stages == ("chain_validation",)

    validated = attach_validation(graph, validate_chain(graph))
    assert compute_completeness(validated, recorded).run == _COMPLETE


# ---------------------------------------------------------------------------
# Claim support: missing_anchor vs unsupported (2.4)
# ---------------------------------------------------------------------------


def test_anchorless_evidence_is_missing_anchor_and_no_reference_is_unsupported_never_merged() -> None:
    anchorless = _claim((_E2,), field_index=7, support="missing_anchor")
    bare = _claim((), field_index=8, support="unsupported")
    nodes: list[ProvenanceNode] = [
        _source(),
        _evidence("e1"),
        _evidence("e2", precision="absent"),
        anchorless,
        bare,
        _step((_E1, _E2), (anchorless.node_id, bare.node_id)),
    ]
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    missing_anchor = report.claims[anchorless.node_id]
    unsupported = report.claims[bare.node_id]
    assert missing_anchor.state == "incomplete" and missing_anchor.reason == "missing_anchor"
    assert unsupported.state == "incomplete" and unsupported.reason == "unsupported"
    assert missing_anchor.missing_record_kinds == ("anchor",)
    assert missing_anchor.responsible_stages == ("evidence_index",)
    assert unsupported.missing_record_kinds == ("evidence",)
    assert unsupported.responsible_stages == ("extraction_chunk",)

    document = report.documents[_SRC]
    assert document.state == "incomplete"
    assert document.reason == "missing_anchor; unsupported"  # both reasons kept distinct
    assert report.run.state == "incomplete"


@pytest.mark.parametrize("second", ["exact", "approximate"])
def test_mixed_citation_with_any_usable_anchor_is_supported(second: str) -> None:
    claim = _claim((_E1, _E2))
    nodes: list[ProvenanceNode] = [
        _source(),
        _evidence("e1", precision="absent"),
        _evidence("e2", precision=second),
        claim,
        _step((_E1, _E2), (claim.node_id,)),
    ]
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    assert report.claims[claim.node_id] == _COMPLETE


def test_claim_whose_references_all_fail_to_resolve_is_unsupported() -> None:
    ghost = format_evidence_id(_SRC, "zz9")
    claim = _claim((ghost,), support="unsupported")
    nodes: list[ProvenanceNode] = [_source(), _evidence("e1"), claim, _step((_E1,), (claim.node_id,))]
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    assert report.claims[claim.node_id].reason == "unsupported"


def test_resolved_anchorless_plus_unresolved_reference_is_missing_anchor() -> None:
    ghost = format_evidence_id(_SRC, "zz9")
    claim = _claim((_E2, ghost), support="missing_anchor")
    nodes: list[ProvenanceNode] = [_source(), _evidence("e2", precision="absent"), claim, _step((_E2,), (claim.node_id,))]
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    assert report.claims[claim.node_id].reason == "missing_anchor"


def test_support_is_computed_from_graph_anchors_not_from_the_recorded_label() -> None:
    """A claim labelled supported whose only cited evidence is anchorless is still missing_anchor."""
    claim = _claim((_E2,), support="supported")
    nodes: list[ProvenanceNode] = [_source(), _evidence("e2", precision="absent"), claim, _step((_E2,), (claim.node_id,))]
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    assert report.claims[claim.node_id].reason == "missing_anchor"


# ---------------------------------------------------------------------------
# Undetermined: failed stages and missing segments (2.5)
# ---------------------------------------------------------------------------


def test_failed_stage_makes_only_the_claims_it_produced_undetermined() -> None:
    recorder = _realistic_recorder(fail=("extraction_chunk", "model call timed out"))
    recorded = recorder.snapshot()
    graph = build_graph(recorded)

    report = compute_completeness(graph, recorded)

    for src in (_SRC, _SRC2):
        for index in (7, 8):
            claim = report.claims[derive_claim_id(src, index)]
            assert claim.state == "undetermined"
            assert "model call timed out" in (claim.reason or "")
            assert claim.responsible_stages == ("extraction_chunk",)
        # Claims produced by other stages are not blindly marked.
        assert report.claims[derive_claim_id(src, 1)] == _COMPLETE
        assert report.claims[derive_claim_id(src, 7, 1)] == _COMPLETE
        assert report.documents[src].state == "undetermined"
    assert report.run.state == "undetermined"
    assert "model call timed out" in (report.run.reason or "")


def test_failed_document_stage_is_attributed_to_the_document_that_lacks_its_records() -> None:
    """document_extraction failed and only document B is missing its source node."""
    claim_b = _claim((format_evidence_id(_SRC2, "e1"),), source_id=_SRC2)
    nodes = [
        *_minimal_nodes(),
        _evidence("e1", source_id=_SRC2),
        claim_b,
        _step((format_evidence_id(_SRC2, "e1"),), (claim_b.node_id,)),
    ]
    status = {**_BASE_STATUS, "document_extraction": "failed"}
    recorded = _recorded(
        nodes,
        edges=_with_edges(nodes),
        status=status,
        reasons={"document_extraction": "unreadable PDF"},
        fingerprints=(_FP, _FP2),
    )
    _, report = _compute(recorded)

    assert report.documents[_SRC] == _COMPLETE
    document_b = report.documents[_SRC2]
    assert document_b.state == "undetermined"
    assert "unreadable PDF" in (document_b.reason or "")
    assert document_b.responsible_stages == ("document_extraction",)
    assert report.run.state == "undetermined"


def test_failed_stage_in_the_recorded_run_counts_even_without_a_graph_segment() -> None:
    """The recorded status is authoritative even if the graph was built from an earlier snapshot."""
    nodes = _minimal_nodes()
    before = _recorded(nodes, edges=_with_edges(nodes))
    graph = build_graph(before)
    after = dataclasses.replace(
        before,
        stage_status={**before.stage_status, "extraction_chunk": "failed"},
        stage_failure_reasons={"extraction_chunk": "boom"},
    )

    report = compute_completeness(graph, after)

    assert report.claims[derive_claim_id(_SRC, 7)].state == "undetermined"
    assert "boom" in (report.claims[derive_claim_id(_SRC, 7)].reason or "")


def test_claim_citing_unmaterialized_evidence_is_undetermined_with_the_segment_reason() -> None:
    broken = dataclasses.replace(_evidence("e1"), anchor=_anchor("bogus"))
    claim = _claim((_E1,))
    nodes: list[ProvenanceNode] = [_source(), broken, claim, _step((_E1,), (claim.node_id,))]
    graph, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    assert len(graph.missing_segments) == 1
    segment_reason = graph.missing_segments[0].reason
    state = report.claims[claim.node_id]
    assert state.state == "undetermined"
    assert segment_reason in (state.reason or "")
    assert state.missing_record_kinds == ("evidence",)
    assert report.documents[_SRC].state == "undetermined"
    assert report.run.state == "undetermined"


def test_unmaterialized_claim_is_reported_undetermined() -> None:
    nodes = _minimal_nodes()
    broken = dataclasses.replace(_claim((_E1,), field_index=9), support="bogus")
    _, report = _compute(_recorded([*nodes, broken], edges=_with_edges(nodes)))

    state = report.claims[broken.node_id]
    assert state.state == "undetermined"
    assert state.missing_record_kinds == ("claim",)
    assert report.documents[_SRC].state == "undetermined"


def test_unattributed_missing_segment_makes_the_run_undetermined_and_is_not_an_undeclared_stage() -> None:
    nodes = _minimal_nodes()
    recorded = _recorded(nodes, edges=_with_edges(nodes), raw_nodes={"junk": "not a node"})
    graph, report = _compute(recorded, stage_contracts_strict=True)

    assert [segment.stage for segment in graph.missing_segments] == ["unattributed"]
    assert report.documents[_SRC] == _COMPLETE
    assert report.run.state == "undetermined"
    assert report.run.responsible_stages == ("unattributed",)
    assert "not declared" not in (report.run.reason or "")


def test_document_state_is_the_worst_claim_and_undetermined_outranks_incomplete() -> None:
    broken = dataclasses.replace(_evidence("e3"), anchor=_anchor("bogus"))
    incomplete = _claim((), field_index=8, support="unsupported")
    undetermined = _claim((format_evidence_id(_SRC, "e3"),), field_index=9)
    nodes: list[ProvenanceNode] = [
        *_minimal_nodes(),
        broken,
        incomplete,
        undetermined,
        _step((_E1,), (incomplete.node_id, undetermined.node_id)),
    ]
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes)))

    assert report.claims[incomplete.node_id].state == "incomplete"
    assert report.claims[undetermined.node_id].state == "undetermined"
    assert report.documents[_SRC].state == "undetermined"
    assert report.run.state == "undetermined"


def test_run_state_is_the_worst_document() -> None:
    bare = _claim((), field_index=8, source_id=_SRC2, support="unsupported")
    nodes: list[ProvenanceNode] = [
        *_minimal_nodes(),
        _source(_FP2),
        _evidence("e1", source_id=_SRC2),
        bare,
        _step((format_evidence_id(_SRC2, "e1"),), (bare.node_id,)),
    ]
    _, report = _compute(_recorded(nodes, edges=_with_edges(nodes), fingerprints=(_FP, _FP2)))

    assert report.documents[_SRC] == _COMPLETE
    assert report.documents[_SRC2].state == "incomplete"
    assert report.run.state == "incomplete"
    assert report.run.reason == "unsupported"


def test_validation_that_did_not_complete_makes_the_run_undetermined() -> None:
    nodes = _minimal_nodes()
    recorded = _recorded(nodes, edges=_with_edges(nodes))
    graph = build_graph(recorded)
    unvalidated = ChainValidationReport(
        completed=False, incompletion_reason="node index unavailable", findings=(), counts_by_severity={}
    )

    report = compute_completeness(graph, recorded, unvalidated)

    assert report.documents[_SRC] == _COMPLETE
    assert report.run.state == "undetermined"
    assert "node index unavailable" in (report.run.reason or "")


def test_run_with_no_documents_is_undetermined_not_complete() -> None:
    _, report = _compute(_recorded([], status={}, fingerprints=()))

    assert report.run.state == "undetermined"
    assert report.run.reason
    assert dict(report.documents) == {}
    assert dict(report.claims) == {}


def test_malformed_recorded_stage_status_degrades_to_undetermined() -> None:
    nodes = _minimal_nodes()
    recorded = dataclasses.replace(_recorded(nodes, edges=_with_edges(nodes)), stage_status=None)  # type: ignore[arg-type]
    graph = build_graph(dataclasses.replace(recorded, stage_status={}))

    report = compute_completeness(graph, recorded)

    assert report.run.state == "undetermined"
    assert report.run.reason


# ---------------------------------------------------------------------------
# stage_contracts_strict
# ---------------------------------------------------------------------------


def _mystery_stage_recorded() -> RecordedRun:
    """A step recorded against a stage the contract table does not declare (bypassing the recorder)."""
    nodes = _minimal_nodes()
    revised = dataclasses.replace(_claim((_E1,)), node_id=derive_claim_id(_SRC, 7, 1))
    nodes += [revised, _step((derive_claim_id(_SRC, 7),), (revised.node_id,), stage="mystery", kind="merge")]
    status = {**_BASE_STATUS, "mystery": "complete"}
    return _recorded(nodes, edges=_with_edges(nodes), status=status)


def test_strict_mode_reports_an_undeclared_recorded_stage_as_undetermined() -> None:
    graph, report = _compute(_mystery_stage_recorded(), stage_contracts_strict=True)

    assert report.run.state == "undetermined"
    assert "mystery" in report.run.responsible_stages
    assert "mystery" in (report.run.reason or "")
    assert report.claims[derive_claim_id(_SRC, 7, 1)].state == "undetermined"
    assert report.claims[derive_claim_id(_SRC, 7)] == _COMPLETE


def test_non_strict_mode_ignores_an_undeclared_recorded_stage() -> None:
    _, report = _compute(_mystery_stage_recorded(), stage_contracts_strict=False)

    assert report.run == _COMPLETE
    assert all(state == _COMPLETE for state in report.claims.values())


def test_strict_flag_defaults_to_true() -> None:
    recorded = _mystery_stage_recorded()
    graph = build_graph(recorded)
    assert compute_completeness(graph, recorded) == compute_completeness(graph, recorded, stage_contracts_strict=True)


@pytest.mark.parametrize("strict, expected", [(True, "undetermined"), (False, "complete")])
def test_strict_flag_against_a_narrower_contract_table(strict: bool, expected: str) -> None:
    """The realistic run checked against a table that does not declare ``synthesis``."""
    recorded = _realistic_recorder().snapshot()
    graph = build_graph(recorded)
    contracts = {stage: contract for stage, contract in STAGE_CONTRACTS.items() if stage != "synthesis"}

    report = compute_completeness(graph, recorded, contracts=contracts, stage_contracts_strict=strict)

    assert report.run.state == expected
    assert report.claims[derive_claim_id(_SRC, 7, 1)].state == expected
    assert report.claims[derive_claim_id(_SRC, 7)] == _COMPLETE


def test_not_run_stage_absent_from_the_table_is_not_a_recorded_stage() -> None:
    nodes = _minimal_nodes()
    recorded = _recorded(nodes, edges=_with_edges(nodes))  # synthesis, metadata_prefill ... read not_run
    contracts = {stage: STAGE_CONTRACTS[stage] for stage in _BASE_STATUS}

    _, report = _compute(recorded, contracts=contracts, stage_contracts_strict=True)

    assert report.run == _COMPLETE


# ---------------------------------------------------------------------------
# Data only (2.6): no file, no rendering, attachment by the caller
# ---------------------------------------------------------------------------


def test_report_is_data_and_is_attached_by_the_caller_without_mutating_the_graph() -> None:
    nodes = _minimal_nodes()
    recorded = _recorded(nodes, edges=_with_edges(nodes))
    graph = build_graph(recorded)
    snapshot = dataclasses.replace(graph)

    report = compute_completeness(graph, recorded)

    assert graph == snapshot and graph.completeness is None
    attached = attach_completeness(graph, report)
    assert attached.completeness == report
    assert compute_completeness(graph, recorded) == report  # deterministic


def test_computation_performs_no_file_access(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorded = _realistic_recorder(fail=("synthesis", "x")).snapshot()
    graph = build_graph(recorded)
    monkeypatch.chdir(tmp_path)

    def _refuse(*_args: object, **_kwargs: object) -> typing.NoReturn:
        raise AssertionError("compute_completeness touched the filesystem")

    monkeypatch.setattr(builtins, "open", _refuse)
    monkeypatch.setattr(io, "open", _refuse)
    monkeypatch.setattr(os, "open", _refuse)
    monkeypatch.setattr(os, "stat", _refuse)

    report = compute_completeness(graph, recorded)

    monkeypatch.undo()
    assert isinstance(report, CompletenessReport)
    assert list(tmp_path.iterdir()) == []


_ALLOWED_IMPORTS = {
    "__future__",
    "typing",
    "collections.abc",
    "provenance.models",
    "provenance.contracts",
    "provenance.identity",
    "provenance.graph",
    "utils.logging_utils",
}
_FORBIDDEN_CALLS = {"open", "print", "input", "exec", "eval", "compile", "__import__", "breakpoint"}


def _tree() -> ast.Module:
    return ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))


def test_module_imports_nothing_that_could_touch_files_clocks_hashing_or_randomness() -> None:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add(node.module or "")

    assert imported <= _ALLOWED_IMPORTS, f"unexpected imports: {sorted(imported - _ALLOWED_IMPORTS)}"


def test_module_calls_no_io_or_rendering_builtin() -> None:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in _FORBIDDEN_CALLS, f"completeness.py calls {node.func.id}()"
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"write_text", "write_bytes", "read_text", "read_bytes", "mkdir", "open", "dump", "dumps"}


def test_module_declares_no_dataclass_and_exports_only_compute_completeness() -> None:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                assert name != "dataclass", f"{node.name} is a dataclass"
    assert completeness_module.__all__ == ["compute_completeness"]


def test_safety_net_degrades_an_internal_failure_to_an_undetermined_run(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = _minimal_nodes()
    recorded = _recorded(nodes, edges=_with_edges(nodes))
    graph = build_graph(recorded)

    def _boom(self: object) -> typing.NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr(completeness_module._Analysis, "check_claims", lambda self, _u: _boom(self))

    report = compute_completeness(graph, recorded)

    assert report.run.state == "undetermined"
    assert "boom" in (report.run.reason or "")
    assert dict(report.documents) == {} and dict(report.claims) == {}


def test_wrong_argument_types_raise_type_error() -> None:
    nodes = _minimal_nodes()
    recorded = _recorded(nodes, edges=_with_edges(nodes))
    graph = build_graph(recorded)
    with pytest.raises(TypeError):
        compute_completeness(recorded, recorded)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        compute_completeness(graph, graph)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        compute_completeness(graph, recorded, stage_contracts_strict="yes")  # type: ignore[arg-type]
