"""Tests for ``provenance.graph`` — typed graph assembly with partial preservation.

Covers (provenance-core design, "Assembly Layer → GraphBuilder"):

- assembly of a recorder snapshot into a :class:`ProvenanceGraph` carrying the
  schema identity, schema version and run identity (7.5), in which every node
  and every edge carries an explicit kind from the closed vocabulary and the
  five node types stay distinct (7.1, 7.2);
- per-record guarding: a record that cannot be materialized becomes exactly one
  :class:`MissingSegment` naming stage, record kind, a non-empty reason and the
  affected identifiers, while the rest of the graph is still produced (7.4);
- a stage that emitted nothing does not abort assembly (1.6);
- a failed stage becomes a ``record_kind="stage"`` missing segment: one for a
  run-wide failure, one per ``(stage, document)`` for a per-document failure,
  naming the document and that document's steps of the stage (task 4.6; 2.5);
- lookup by node kind, outgoing-edge lookup and the claim → evidence → source
  traversal (1.4), none of which raises;
- ``attach_validation`` / ``attach_completeness``: new graphs carrying the
  reports, validation records plus ``validates`` edges for the former,
  without mutating the original, and idempotent (8.7).

Synthetic identifiers and lorem text only.
"""

from __future__ import annotations

import ast
import dataclasses
import typing
from pathlib import Path

import pytest

import provenance
from provenance import graph as graph_module
from provenance.adapters import (
    claim_nodes_from_fields,
    derivation_from_stage_event,
    evidence_node_from_item,
    source_node_from_identity,
)
from provenance.graph import (
    PROVENANCE_SCHEMA_ID,
    PROVENANCE_SCHEMA_VERSION,
    ProvenanceGraph,
    attach_completeness,
    attach_validation,
    build_graph,
)
from provenance.identity import derive_source_id, derive_validation_id
from provenance.models import (
    UNKNOWN_PRIVACY,
    ArtifactCompleteness,
    ChainValidationReport,
    ClaimNode,
    CompletenessReport,
    DerivationStep,
    EdgeKind,
    EvidenceNode,
    MissingSegment,
    NodeKind,
    ProvenanceEdge,
    ProvenanceNode,
    RecordedRun,
    RunIdentity,
    SourceAnchor,
    SourceNode,
    ValidationFinding,
    ValidationRecord,
)
from provenance.recorder import ProvenanceRecorder

_RUN = "run:0123456789abcdef"
_FP = "aaaabbbbccccdddd"
_SRC = derive_source_id(_FP)
_NODE_KINDS = set(typing.get_args(NodeKind))
_EDGE_KINDS = set(typing.get_args(EdgeKind))
_MODULE_PATH = Path(graph_module.__file__)


# ---------------------------------------------------------------------------
# Fixtures: a small, realistic run built through the real adapters and recorder
# ---------------------------------------------------------------------------


def _run_identity() -> RunIdentity:
    return RunIdentity(
        run_id=_RUN,
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
        document_fingerprints={_SRC: _FP},
        created_at="2026-10-08T00:00:00+00:00",
    )


def _source() -> SourceNode:
    return source_node_from_identity(
        {"pdf_content_hash": _FP, "config_hash": "cfg123", "extraction_map_hash": "sch456", "model_id": "gpt-x"},
        source_label="lorem.pdf",
        source_format="pdf",
        run_id=_RUN,
    )


def _evidence(local_id: str, page: int) -> EvidenceNode:
    return evidence_node_from_item(
        {
            "id": local_id,
            "type": "sentence",
            "page": page,
            "coords": [1.0, 2.0, 3.0, 4.0],
            "section_path": "Methods",
            "xpath": f".//s[@xml:id='{local_id}']",
            "score": 3,
        },
        source_id=_SRC,
        run_id=_RUN,
        producing_backend="grobid",
    )


def _claim(evidence: list[EvidenceNode]) -> tuple[ClaimNode, list[ProvenanceEdge]]:
    claims, edges, findings = claim_nodes_from_fields(
        [
            {
                "field_index": 7,
                "field_name": "sample_size",
                "extracted_value": "42",
                "location": [node.local_id for node in evidence],
                "confidence": "high",
            }
        ],
        source_id=_SRC,
        run_id=_RUN,
        known_evidence_ids={node.node_id for node in evidence},
        origin="model",
        evidence_anchors={node.node_id: node.anchor for node in evidence},
    )
    assert findings == []
    return claims[0], edges


def _bad_evidence() -> EvidenceNode:
    """Well-typed but unmaterializable: its evidence kind is outside the closed vocabulary."""
    good = _evidence("s9", 9)
    return dataclasses.replace(good, node_id=f"{_SRC}#s9", evidence_kind="hologram")  # type: ignore[arg-type]


def _recorded_run(*, with_bad_record: bool = True, fail_stage: str | None = None) -> RecordedRun:
    recorder = ProvenanceRecorder(_run_identity())
    recorder.record_source(_source())
    recorder.mark_stage_complete("document_extraction")
    evidence = [_evidence("s1", 1), _evidence("s2", 2)]
    batch: list[EvidenceNode] = [*evidence]
    if with_bad_record:
        batch.append(_bad_evidence())
    recorder.record_evidence(batch)
    recorder.mark_stage_complete("evidence_index")
    claim, edges = _claim(evidence)
    recorder.record_claim(claim, edges)
    recorder.record_derivation(
        derivation_from_stage_event(
            "extraction_chunk",
            "extract",
            run_id=_RUN,
            inputs=[node.node_id for node in evidence],
            outputs=[claim.node_id],
            model_id="gpt-x",
        )
    )
    if fail_stage is None:
        recorder.mark_stage_complete("extraction_chunk")
    else:
        recorder.mark_stage_failed(fail_stage, "lorem ipsum timeout")
    return recorder.snapshot()


def _claim_id() -> str:
    return _claim([_evidence("s1", 1), _evidence("s2", 2)])[0].node_id


def _base_run(**overrides: typing.Any) -> RecordedRun:
    """A hand-built snapshot (source + one evidence node) for targeted bad-record cases."""
    source = _source()
    evidence = _evidence("s1", 1)
    fields: dict[str, typing.Any] = {
        "run": _run_identity(),
        "nodes": {source.node_id: source, evidence.node_id: evidence},
        "edges": (),
        "findings": (),
        "stage_status": {},
        "stage_failure_reasons": {},
    }
    fields.update(overrides)
    return RecordedRun(**fields)


def _validation_report() -> ChainValidationReport:
    finding = ValidationFinding(
        finding_id="finding:missing_anchor:x:1",
        code="missing_anchor",
        severity="warning",
        node_id=f"{_SRC}#s1",
        edge_id=None,
        message="lorem",
        details={},
    )
    orphan = ValidationFinding(
        finding_id="finding:orphan_node:y:1",
        code="orphan_node",
        severity="info",
        node_id=_SRC,
        edge_id=None,
        message="ipsum",
        details={},
    )
    ghost = ValidationFinding(
        finding_id="finding:dangling_reference:z:1",
        code="dangling_reference",
        severity="critical",
        node_id=f"{_SRC}#does-not-exist",
        edge_id=None,
        message="dolor",
        details={},
    )
    return ChainValidationReport(
        completed=True,
        incompletion_reason=None,
        findings=(finding, orphan, ghost),
        counts_by_severity={"critical": 1, "warning": 1, "info": 1},
    )


def _completeness_report() -> CompletenessReport:
    complete = ArtifactCompleteness(state="complete", reason=None, missing_record_kinds=(), responsible_stages=())
    return CompletenessReport(run=complete, documents={_SRC: complete}, claims={_claim_id(): complete})


# ---------------------------------------------------------------------------
# Integration: partial preservation and traversal (7.4, 1.4)
# ---------------------------------------------------------------------------


def test_build_graph_preserves_everything_but_the_one_unmaterializable_record() -> None:
    recorded = _recorded_run()
    assert f"{_SRC}#s9" in recorded.nodes  # the recorder accepted it

    graph = build_graph(recorded)

    expected = set(recorded.nodes) - {f"{_SRC}#s9"}
    assert set(graph.nodes) == expected
    assert len(graph.missing_segments) == 1
    segment = graph.missing_segments[0]
    assert isinstance(segment, MissingSegment)
    assert segment.reason.strip()
    assert "hologram" in segment.reason
    assert segment.affected_ids == (f"{_SRC}#s9",)
    assert segment.record_kind == "evidence"
    assert segment.stage == "evidence_index"


def test_path_from_claim_returns_claim_then_evidence_then_source() -> None:
    graph = build_graph(_recorded_run())

    path = graph.path_from_claim(_claim_id())

    assert [node.kind for node in path] == ["claim", "evidence", "evidence", "source"]
    assert [node.node_id for node in path] == [_claim_id(), f"{_SRC}#s1", f"{_SRC}#s2", _SRC]


def test_graph_carries_schema_identity_version_and_run() -> None:
    graph = build_graph(_recorded_run())

    assert graph.schema_id == PROVENANCE_SCHEMA_ID == "evitrace/provenance-graph"
    assert graph.schema_version == PROVENANCE_SCHEMA_VERSION == "1.0.0"
    assert graph.run == _run_identity()
    assert graph.validation is None
    assert graph.completeness is None


def test_every_node_and_edge_carries_a_kind_from_the_closed_vocabulary() -> None:
    graph = build_graph(_recorded_run())

    assert graph.nodes and graph.edges
    for node_id, node in graph.nodes.items():
        assert node.node_id == node_id
        assert node.kind in _NODE_KINDS
    for edge in graph.edges:
        assert edge.kind in _EDGE_KINDS


def test_node_types_stay_distinct() -> None:
    graph = build_graph(_recorded_run())

    assert {type(node) for node in graph.nodes_of_kind("source")} == {SourceNode}
    assert {type(node) for node in graph.nodes_of_kind("evidence")} == {EvidenceNode}
    assert {type(node) for node in graph.nodes_of_kind("claim")} == {ClaimNode}
    assert {type(node) for node in graph.nodes_of_kind("derivation")} == {DerivationStep}
    assert graph.nodes_of_kind("validation") == ()


def test_structural_edges_are_assembled_from_record_references() -> None:
    graph = build_graph(_recorded_run())
    claim_id = _claim_id()
    step = graph.nodes_of_kind("derivation")[0]
    assert isinstance(step, DerivationStep)

    edge_keys = {(edge.kind, edge.from_id, edge.to_id) for edge in graph.edges}

    # recorded with the claim
    assert ("cites_evidence", claim_id, f"{_SRC}#s1") in edge_keys
    assert ("cites_evidence", claim_id, f"{_SRC}#s2") in edge_keys
    # assembled from the records' own references
    assert ("belongs_to_source", f"{_SRC}#s1", _SRC) in edge_keys
    assert ("belongs_to_source", claim_id, _SRC) in edge_keys
    assert ("has_input", step.node_id, f"{_SRC}#s1") in edge_keys
    assert ("has_output", step.node_id, claim_id) in edge_keys
    edge_ids = [edge.edge_id for edge in graph.edges]
    assert len(edge_ids) == len(set(edge_ids))
    assert all(edge.edge_id == f"{edge.kind}:{edge.from_id}->{edge.to_id}" for edge in graph.edges)


def test_nodes_of_kind_and_edges_from() -> None:
    graph = build_graph(_recorded_run())

    assert [node.node_id for node in graph.nodes_of_kind("evidence")] == [f"{_SRC}#s1", f"{_SRC}#s2"]
    outgoing = graph.edges_from(_claim_id())
    assert outgoing
    assert all(edge.from_id == _claim_id() for edge in outgoing)
    assert {edge.kind for edge in outgoing} == {"cites_evidence", "belongs_to_source"}
    assert graph.edges_from("nope") == ()


# ---------------------------------------------------------------------------
# Silent stages and failed stages (1.6, 7.4)
# ---------------------------------------------------------------------------


def test_a_stage_that_emitted_nothing_does_not_abort_assembly() -> None:
    recorder = ProvenanceRecorder(_run_identity())
    recorder.record_source(_source())
    recorder.mark_stage_complete("document_extraction")
    # evidence_index, extraction stages etc. emit nothing at all

    graph = build_graph(recorder.snapshot())

    assert list(graph.nodes) == [_SRC]
    assert graph.missing_segments == ()


def test_empty_snapshot_yields_an_empty_graph() -> None:
    graph = build_graph(ProvenanceRecorder(_run_identity()).snapshot())

    assert graph.nodes == {}
    assert graph.missing_segments == ()
    assert graph.path_from_claim(_claim_id()) == ()


def test_a_failed_stage_is_reported_as_a_missing_segment_and_other_stages_survive() -> None:
    recorded = _recorded_run(with_bad_record=False, fail_stage="synthesis")

    graph = build_graph(recorded)

    assert set(graph.nodes) == set(recorded.nodes)
    segments = [segment for segment in graph.missing_segments if segment.stage == "synthesis"]
    assert len(segments) == 1
    assert segments[0].record_kind == "stage"
    assert "lorem ipsum timeout" in segments[0].reason


# ---------------------------------------------------------------------------
# Failed stages scoped to one document (task 4.6; 2.5, 7.4)
# ---------------------------------------------------------------------------

_FP2 = "eeeeffff00001111"
_SRC2 = derive_source_id(_FP2)


def _two_document_recorder() -> tuple[ProvenanceRecorder, dict[str, list[str]]]:
    """Two documents, each with a source, two evidence nodes, a claim and one extraction_chunk step.

    Returns the recorder and ``source_id -> [extraction_chunk step ids]``.
    """
    identity = dataclasses.replace(_run_identity(), document_fingerprints={_SRC: _FP, _SRC2: _FP2})
    recorder = ProvenanceRecorder(identity)
    steps: dict[str, list[str]] = {}
    for fp in (_FP, _FP2):
        source = source_node_from_identity(
            {"pdf_content_hash": fp, "config_hash": "cfg123", "extraction_map_hash": "sch456", "model_id": "gpt-x"},
            source_label=f"{fp}.pdf",
            source_format="pdf",
            run_id=_RUN,
        )
        source_id = source.node_id
        recorder.record_source(source)
        evidence = [
            evidence_node_from_item(
                {"id": f"s{n}", "type": "sentence", "page": n, "coords": [1.0, 2.0, 3.0, 4.0],
                 "xpath": f".//s[@xml:id='s{n}']", "score": 3},
                source_id=source_id,
                run_id=_RUN,
                producing_backend="grobid",
            )
            for n in (1, 2)
        ]
        recorder.record_evidence(evidence)
        claims, edges, _ = claim_nodes_from_fields(
            [{"field_index": 7, "field_name": "sample_size", "extracted_value": "42", "location": ["s1"]}],
            source_id=source_id,
            run_id=_RUN,
            known_evidence_ids={node.node_id for node in evidence},
            origin="model",
            evidence_anchors={node.node_id: node.anchor for node in evidence},
        )
        recorder.record_claim(claims[0], edges)
        step = derivation_from_stage_event(
            "extraction_chunk", "extract", run_id=_RUN,
            inputs=[evidence[0].node_id], outputs=[claims[0].node_id], model_id="gpt-x",
        )
        steps[source_id] = [recorder.record_derivation(step)]
        recorder.mark_stage_complete("extraction_chunk", source_id=source_id)
    return recorder, steps


def _stage_segments(graph: ProvenanceGraph) -> list[MissingSegment]:
    return [segment for segment in graph.missing_segments if segment.record_kind == "stage"]


def test_a_per_document_failure_yields_one_segment_naming_the_document_and_its_steps() -> None:
    recorder, steps = _two_document_recorder()
    recorder.mark_stage_failed("extraction_chunk", "model call timed out", source_id=_SRC)
    recorded = recorder.snapshot()

    graph = build_graph(recorded)

    assert set(graph.nodes) == set(recorded.nodes)
    segments = _stage_segments(graph)
    assert len(segments) == 1
    segment = segments[0]
    assert segment.stage == "extraction_chunk"
    assert segment.affected_ids == (_SRC, *sorted(steps[_SRC]))
    assert not set(steps[_SRC2]) & set(segment.affected_ids)
    assert _SRC in segment.reason
    assert "model call timed out" in segment.reason


def test_one_segment_per_failed_document_and_stage_carrying_every_reason() -> None:
    recorder, steps = _two_document_recorder()
    recorder.mark_stage_failed("extraction_chunk", "chunk 2 timed out", source_id=_SRC)
    recorder.mark_stage_failed("extraction_chunk", "invalid JSON", source_id=_SRC)
    recorder.mark_stage_failed("extraction_chunk", "rate limited", source_id=_SRC2)
    recorder.mark_stage_failed("synthesis", "never ran", source_id=_SRC2)

    graph = build_graph(recorder.snapshot())

    by_key = {(segment.stage, segment.affected_ids[0]): segment for segment in _stage_segments(graph)}
    assert sorted(by_key) == [("extraction_chunk", _SRC), ("extraction_chunk", _SRC2), ("synthesis", _SRC2)]
    first = by_key[("extraction_chunk", _SRC)]
    assert "chunk 2 timed out" in first.reason and "invalid JSON" in first.reason
    assert first.reason.index("chunk 2 timed out") < first.reason.index("invalid JSON")
    assert by_key[("extraction_chunk", _SRC2)].affected_ids == (_SRC2, *steps[_SRC2])
    assert "rate limited" in by_key[("extraction_chunk", _SRC2)].reason
    # A failed stage that recorded no step for the document still names the document.
    assert by_key[("synthesis", _SRC2)].affected_ids == (_SRC2,)


def test_a_run_wide_failure_keeps_its_single_segment_listing_every_step_of_the_stage() -> None:
    recorder, steps = _two_document_recorder()
    recorder.mark_stage_failed("extraction_chunk", "provider outage")

    graph = build_graph(recorder.snapshot())

    segments = _stage_segments(graph)
    assert len(segments) == 1
    assert segments[0].affected_ids == tuple(sorted([*steps[_SRC], *steps[_SRC2]]))
    assert "provider outage" in segments[0].reason


def test_run_wide_and_per_document_failures_of_one_stage_both_yield_segments() -> None:
    recorder, _ = _two_document_recorder()
    recorder.mark_stage_failed("extraction_chunk", "provider outage")
    recorder.mark_stage_failed("extraction_chunk", "bad JSON", source_id=_SRC2)

    segments = _stage_segments(build_graph(recorder.snapshot()))

    assert len(segments) == 2
    assert sum(1 for segment in segments if segment.affected_ids[:1] == (_SRC2,)) == 1


def test_per_document_completion_alone_yields_no_segment() -> None:
    recorder, _ = _two_document_recorder()

    assert build_graph(recorder.snapshot()).missing_segments == ()


def test_build_graph_never_raises_on_malformed_per_document_status() -> None:
    for status, reasons in (
        (None, None),
        ({_SRC: None, 5: {"synthesis": "failed"}}, {_SRC: "x"}),
        ({_SRC: {"synthesis": "failed", None: "failed"}}, {_SRC: {"synthesis": ("a", 3, None)}}),
        ({"": {"synthesis": "failed"}}, {}),
    ):
        malformed = dataclasses.replace(
            _base_run(), document_stage_status=status, document_stage_failure_reasons=reasons  # type: ignore[arg-type]
        )
        graph = build_graph(malformed)
        assert all(segment.reason.strip() for segment in graph.missing_segments)
    graph = build_graph(
        dataclasses.replace(
            _base_run(),
            document_stage_status={_SRC: {"synthesis": "failed", None: "failed"}},  # type: ignore[dict-item]
            document_stage_failure_reasons={_SRC: {"synthesis": ("a", 3, None)}},  # type: ignore[dict-item]
        )
    )
    assert [(segment.stage, segment.affected_ids) for segment in _stage_segments(graph)] == [("synthesis", (_SRC,))]
    assert graph.missing_segments[0].reason.endswith(": a")


# ---------------------------------------------------------------------------
# Per-record guarding: each unmaterializable shape is one segment (7.4)
# ---------------------------------------------------------------------------


def _bad_node_cases() -> list[tuple[str, typing.Any, typing.Any, str]]:
    """(case, key, value, expected record_kind)."""
    evidence = _evidence("s5", 5)
    step = derivation_from_stage_event("quality_control", "reconcile", run_id=_RUN, inputs=[_SRC], outputs=[])
    claim = _claim([_evidence("s1", 1)])[0]
    return [
        ("kind outside vocabulary", evidence.node_id, dataclasses.replace(evidence, kind="blob"), "evidence"),
        ("kind disagrees with class", evidence.node_id, dataclasses.replace(evidence, kind="claim"), "evidence"),
        ("empty node_id", "", dataclasses.replace(evidence, node_id=""), "evidence"),
        ("key differs from node_id", f"{_SRC}#other", evidence, "evidence"),
        ("not a node", f"{_SRC}#junk", {"node_id": "junk"}, "node"),
        ("bare base node", "base", ProvenanceNode("base", "source", _RUN, UNKNOWN_PRIVACY, {}), "source"),
        ("bad anchor precision", evidence.node_id,
         dataclasses.replace(evidence, anchor=SourceAnchor(precision="fuzzy")), "evidence"),  # type: ignore[arg-type]
        ("anchor not an anchor", evidence.node_id, dataclasses.replace(evidence, anchor=None), "evidence"),
        ("bad claim support", claim.node_id, dataclasses.replace(claim, support="maybe"), "claim"),
        ("bad claim origin", claim.node_id, dataclasses.replace(claim, origin="oracle"), "claim"),
        ("bad derivation kind", step.node_id, dataclasses.replace(step, derivation_kind="teleport"), "derivation"),
        ("bad determinism", step.node_id, dataclasses.replace(step, determinism=True), "derivation"),
        ("bad privacy carrier", evidence.node_id, dataclasses.replace(evidence, privacy={"label": "x"}), "evidence"),
        ("bad run_id", evidence.node_id, dataclasses.replace(evidence, run_id=None), "evidence"),
    ]


@pytest.mark.parametrize(("case", "key", "value", "record_kind"), _bad_node_cases(), ids=lambda v: str(v)[:40])
def test_each_unmaterializable_node_becomes_exactly_one_segment(
    case: str, key: typing.Any, value: typing.Any, record_kind: str
) -> None:
    base = _base_run()
    nodes = dict(base.nodes)
    nodes[key] = value
    recorded = dataclasses.replace(base, nodes=nodes)

    graph = build_graph(recorded)

    assert set(graph.nodes) == set(base.nodes), case
    assert len(graph.missing_segments) == 1, case
    segment = graph.missing_segments[0]
    assert segment.reason.strip(), case
    assert segment.record_kind == record_kind, case
    assert segment.stage, case
    if key:
        assert key in segment.affected_ids, case


def _bad_edges() -> list[tuple[str, typing.Any]]:
    good = ProvenanceEdge(edge_id="e", kind="cites_evidence", from_id="a", to_id="b", attributes={})
    return [
        ("edge kind outside vocabulary", dataclasses.replace(good, edge_id="x:a->b", kind="likes")),
        ("empty edge_id", dataclasses.replace(good, edge_id="")),
        ("empty endpoint", dataclasses.replace(good, edge_id="cites_evidence:->b", from_id="")),
        ("not an edge", ("cites_evidence", "a", "b")),
    ]


@pytest.mark.parametrize(("case", "edge"), _bad_edges(), ids=lambda v: str(v)[:40])
def test_each_unmaterializable_edge_becomes_exactly_one_segment(case: str, edge: typing.Any) -> None:
    graph = build_graph(_base_run(edges=(edge,)))

    assert len(graph.nodes) == 2, case
    assert len(graph.missing_segments) == 1, case
    segment = graph.missing_segments[0]
    assert segment.record_kind == "edge", case
    assert segment.reason.strip(), case
    assert all(e.edge_id != getattr(edge, "edge_id", None) for e in graph.edges if e.kind == "cites_evidence")


def test_conflicting_duplicate_edge_ids_keep_the_first_and_report_the_second() -> None:
    first = ProvenanceEdge(edge_id="e1", kind="cites_evidence", from_id="a", to_id="b", attributes={})
    second = dataclasses.replace(first, to_id="c")

    graph = build_graph(_base_run(edges=(first, first, second)))

    assert [edge for edge in graph.edges if edge.edge_id == "e1"] == [first]
    assert len(graph.missing_segments) == 1
    assert graph.missing_segments[0].affected_ids == ("e1",)


def test_build_graph_never_raises_on_garbage_inside_a_snapshot() -> None:
    garbage = dataclasses.replace(
        _base_run(),
        nodes={None: None, 3: object(), "x": "y"},  # type: ignore[dict-item]
        edges=(None, 5, "z"),
        stage_status={"evidence_index": "failed", 9: None},  # type: ignore[dict-item]
        stage_failure_reasons={},
    )

    graph = build_graph(garbage)

    assert graph.nodes == {}
    assert len(graph.missing_segments) == 3 + 3 + 1
    assert all(segment.reason.strip() for segment in graph.missing_segments)


def test_build_graph_never_raises_when_snapshot_containers_are_malformed() -> None:
    malformed = dataclasses.replace(_base_run(), nodes=None, edges=None, stage_status=None)  # type: ignore[arg-type]

    graph = build_graph(malformed)

    assert graph.nodes == {}
    assert graph.missing_segments
    assert all(segment.reason.strip() for segment in graph.missing_segments)


def test_build_graph_is_deterministic_regardless_of_insertion_order() -> None:
    recorded = _recorded_run()
    reversed_run = dataclasses.replace(
        recorded,
        nodes=dict(reversed(list(recorded.nodes.items()))),
        edges=tuple(reversed(recorded.edges)),
    )

    first, second = build_graph(recorded), build_graph(reversed_run)

    assert first == second
    assert list(first.nodes) == list(second.nodes) == sorted(first.nodes)
    assert [edge.edge_id for edge in first.edges] == sorted(edge.edge_id for edge in first.edges)


def test_graph_is_immutable() -> None:
    graph = build_graph(_recorded_run())

    with pytest.raises(dataclasses.FrozenInstanceError):
        graph.nodes = {}  # type: ignore[misc]
    with pytest.raises(TypeError):
        graph.nodes[_SRC] = None  # type: ignore[index]
    assert isinstance(graph.edges, tuple)
    assert isinstance(graph.missing_segments, tuple)


# ---------------------------------------------------------------------------
# Lookups never raise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, 3, "", "src:unknown", ["list"], {"a": 1}])
def test_lookups_are_total(bad: typing.Any) -> None:
    graph = build_graph(_recorded_run())

    assert graph.nodes_of_kind(bad) == ()
    assert graph.edges_from(bad) == ()
    assert graph.path_from_claim(bad) == ()


def test_path_from_claim_on_a_non_claim_node_is_empty() -> None:
    graph = build_graph(_recorded_run())

    assert graph.path_from_claim(_SRC) == ()
    assert graph.path_from_claim(f"{_SRC}#s1") == ()


def test_path_from_claim_skips_evidence_that_did_not_materialize() -> None:
    recorded = _recorded_run()
    nodes = dict(recorded.nodes)
    nodes[f"{_SRC}#s2"] = dataclasses.replace(nodes[f"{_SRC}#s2"], evidence_kind="hologram")

    graph = build_graph(dataclasses.replace(recorded, nodes=nodes))

    assert [node.node_id for node in graph.path_from_claim(_claim_id())] == [_claim_id(), f"{_SRC}#s1", _SRC]


# ---------------------------------------------------------------------------
# Attachments (8.7)
# ---------------------------------------------------------------------------


def test_attach_validation_returns_a_new_graph_with_report_records_and_edges() -> None:
    original = build_graph(_recorded_run())
    before = dataclasses.replace(original)
    report = _validation_report()

    attached = attach_validation(original, report)

    assert attached is not original
    assert attached.validation == report
    assert original == before
    assert original.validation is None
    validation_id = derive_validation_id(_RUN, "chain_validation")
    assert validation_id not in original.nodes
    record = attached.nodes[validation_id]
    assert isinstance(record, ValidationRecord)
    assert record.kind == "validation"
    assert record.run_id == _RUN
    assert record.completed is True
    assert record.finding_ids == tuple(finding.finding_id for finding in report.findings)
    assert attached.nodes_of_kind("validation") == (record,)
    validates = {(edge.from_id, edge.to_id) for edge in attached.edges if edge.kind == "validates"}
    # one edge per finding subject that exists in the graph; no dangling edge is introduced
    assert validates == {(validation_id, f"{_SRC}#s1"), (validation_id, _SRC)}
    assert set(original.nodes) <= set(attached.nodes)
    assert set(original.edges) <= set(attached.edges)


def test_attach_validation_records_an_incomplete_run() -> None:
    report = ChainValidationReport(
        completed=False, incompletion_reason="node index unavailable", findings=(), counts_by_severity={}
    )

    attached = attach_validation(build_graph(_recorded_run()), report)

    record = attached.nodes[derive_validation_id(_RUN, "chain_validation")]
    assert isinstance(record, ValidationRecord)
    assert record.completed is False
    assert record.finding_ids == ()
    assert attached.validation == report


def test_attach_validation_is_idempotent() -> None:
    graph = build_graph(_recorded_run())
    report = _validation_report()

    once = attach_validation(graph, report)
    twice = attach_validation(once, report)

    assert once == twice
    assert attach_validation(graph, report) == once


def test_attach_validation_replaces_a_previously_attached_report() -> None:
    graph = build_graph(_recorded_run())
    first = attach_validation(graph, _validation_report())
    empty = ChainValidationReport(completed=True, incompletion_reason=None, findings=(), counts_by_severity={})

    second = attach_validation(first, empty)

    assert second.validation == empty
    assert [edge for edge in second.edges if edge.kind == "validates"] == []
    assert second == attach_validation(graph, empty)


def test_attach_validation_on_a_malformed_run_id_degrades_to_a_segment() -> None:
    graph = dataclasses.replace(build_graph(_recorded_run()), run=dataclasses.replace(_run_identity(), run_id="bogus"))

    attached = attach_validation(graph, _validation_report())

    assert attached.validation == _validation_report()
    assert attached.nodes_of_kind("validation") == ()
    segments = [s for s in attached.missing_segments if s.record_kind == "validation"]
    assert len(segments) == 1 and segments[0].reason.strip()
    assert attach_validation(attached, _validation_report()) == attached


def test_attach_completeness_returns_a_new_graph_without_new_nodes() -> None:
    original = build_graph(_recorded_run())
    before = dataclasses.replace(original)
    report = _completeness_report()

    attached = attach_completeness(original, report)

    assert attached is not original
    assert attached.completeness == report
    assert original == before
    assert original.completeness is None
    assert attached.nodes == original.nodes
    assert attached.edges == original.edges
    assert attach_completeness(attached, report) == attached


def test_attaching_both_reports_yields_a_graph_carrying_both() -> None:
    original = build_graph(_recorded_run())
    before = dataclasses.replace(original)

    attached = attach_completeness(attach_validation(original, _validation_report()), _completeness_report())

    assert attached.validation == _validation_report()
    assert attached.completeness == _completeness_report()
    assert attached.nodes_of_kind("validation")
    assert original == before
    assert original.validation is None and original.completeness is None
    assert attached == attach_validation(attach_completeness(original, _completeness_report()), _validation_report())


@pytest.mark.parametrize("bad", [None, "report", {"completed": True}])
def test_attachments_reject_a_non_report(bad: typing.Any) -> None:
    graph = build_graph(_recorded_run())

    with pytest.raises(TypeError):
        attach_validation(graph, bad)
    with pytest.raises(TypeError):
        attach_completeness(graph, bad)


# ---------------------------------------------------------------------------
# Module boundary
# ---------------------------------------------------------------------------


def test_graph_module_declares_only_the_graph_dataclass_and_no_io() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    dataclass_names = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any("dataclass" in ast.unparse(decorator) for decorator in node.decorator_list)
    ]
    assert dataclass_names == ["ProvenanceGraph"]
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    assert imported <= {"__future__", "collections", "dataclasses", "typing", "provenance", "utils"}
    assert "open" not in {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def test_package_re_exports_the_graph_surface() -> None:
    for name in (
        "ProvenanceGraph",
        "build_graph",
        "attach_validation",
        "attach_completeness",
        "PROVENANCE_SCHEMA_ID",
        "PROVENANCE_SCHEMA_VERSION",
    ):
        assert name in provenance.__all__
        assert getattr(provenance, name) is getattr(graph_module, name)
