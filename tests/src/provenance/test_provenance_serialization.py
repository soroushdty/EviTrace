"""Tests for ``provenance.serialization`` — graph ⇄ plain mapping with the schema version guard.

Covers task 5.1 of provenance-core (design "Persistence Layer → Serialization",
"Data Contracts & Integration → Artifact schema" and its compatibility rule):

- ``graph_to_dict`` takes the graph as its only data argument and emits the
  artifact keys of the design schema (schema identity and version, run,
  nodes, edges, missing segments, validation, completeness) in a fixed order,
  with deterministic key order inside free-form mappings (7.3, 7.5);
- ``graph_from_dict(graph_to_dict(g)) == g`` — also through ``json.dumps`` /
  ``json.loads`` — for a realistic graph built from the real adapters,
  recorder, ``build_graph``, ``attach_validation(validate_chain(...))`` and
  ``attach_completeness(compute_completeness(...))``; typed tuple fields are
  re-tupled and edge order is preserved;
- namespaced extension fields round-trip unchanged (10.4); validation records
  of any check name round-trip without a core type change (10.3);
- a compatible-minor artifact with unknown keys loads, with those keys
  preserved into the node's ``extensions`` (edges: ``attributes``; findings:
  ``details``) under the reserved ``x-compat-unknown`` key (10.5);
- any other major — or an unparseable version — raises
  ``UnrecognizedSchemaVersionError`` naming the version found and the versions
  supported, before anything is materialized (10.6);
- a compatible artifact that violates a closed vocabulary or misses a required
  field raises ``MalformedProvenanceArtifactError`` (readers validate the
  artifact against the closed unions).

Synthetic identifiers and lorem text only.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import logging
import typing
from collections.abc import Iterator
from typing import Any

import pytest

from provenance import serialization
from provenance.adapters import (
    claim_nodes_from_fields,
    derivation_from_stage_event,
    derivations_from_qc_provenance,
    evidence_nodes_from_items,
    run_identity_from_identities,
    source_node_from_identity,
)
from provenance.completeness import compute_completeness
from provenance.graph import (
    PROVENANCE_SCHEMA_ID,
    PROVENANCE_SCHEMA_VERSION,
    ProvenanceGraph,
    attach_completeness,
    attach_validation,
    build_graph,
)
from provenance.identity import derive_claim_id, derive_source_id, format_evidence_id
from provenance.models import (
    UNKNOWN_PRIVACY,
    AnchorPrecision,
    ArtifactCompleteness,
    ChainValidationReport,
    ClaimNode,
    ClaimOrigin,
    ClaimSupport,
    CompletenessReport,
    CompletenessState,
    DerivationKind,
    DerivationStep,
    Determinism,
    EdgeKind,
    EvidenceKind,
    EvidenceNode,
    MissingSegment,
    ProvenanceEdge,
    RunIdentity,
    Severity,
    SourceAnchor,
    SourceNode,
    ValidationFinding,
    ValidationRecord,
)
from provenance.privacy_carrier import PrivacyState, attach_privacy
from provenance.recorder import ProvenanceRecorder
from provenance.serialization import (
    COMPAT_UNKNOWN_KEY,
    MalformedProvenanceArtifactError,
    UnrecognizedSchemaVersionError,
    graph_from_dict,
    graph_to_dict,
)
from provenance.validation import validate_chain

_RUN = "run:0123456789abcdef"
_FP = "aaaabbbbccccdddd"
_FP2 = "eeeeffff00001111"
_SRC = derive_source_id(_FP)
_SRC2 = derive_source_id(_FP2)
_E1 = format_evidence_id(_SRC, "e1")
_TOP_LEVEL_KEYS = [
    "schema_id",
    "schema_version",
    "run",
    "nodes",
    "edges",
    "missing_segments",
    "validation",
    "completeness",
]


def _json_round_trip(payload: dict[str, Any]) -> Any:
    return json.loads(json.dumps(payload))


# ---------------------------------------------------------------------------
# A realistic graph: real adapters + recorder + build_graph + attachments
# ---------------------------------------------------------------------------


def _realistic_recorder(*, fail: tuple[str, str] | None = None) -> ProvenanceRecorder:
    identities = {
        name: {"pdf_content_hash": fp, "config_hash": "cfg123", "extraction_map_hash": "sch456", "model_id": "gpt-x"}
        for name, fp in (("a.pdf", _FP), ("b.pdf", _FP2))
    }
    run = run_identity_from_identities(identities, created_at="2026-10-08T00:00:00+00:00")
    recorder = ProvenanceRecorder(run)
    for name, identity in identities.items():
        source = source_node_from_identity(identity, source_label=name, source_format="pdf", run_id=run.run_id)
        if name == "a.pdf":  # one carrier populated by a (simulated) privacy component, stored verbatim
            source = dataclasses.replace(
                source,
                privacy=attach_privacy(
                    {"label": "lorem-label", "decision": "lorem-decision"},
                    supplied_by="privacy-test",
                    supplied_at="2026-10-08T00:00:00+00:00",
                ),
            )
        source_id = source.node_id
        recorder.record_source(source)
        recorder.mark_stage_complete("document_extraction")
        qc = {"branch": "grobid", "decision": "keep", "scores": {"fidelity": 0.97, "sections": [1, 2]}}
        for step in derivations_from_qc_provenance(qc, source_id=source_id, run_id=run.run_id):
            recorder.record_derivation(step)
        recorder.mark_stage_complete("quality_control")
        items = [
            {"id": "title", "type": "sentence", "page": 1, "coords": [1.0, 1.0, 2.0, 2.0], "section_path": "Metadata",
             "xpath": ".//title", "score": 9, "text": "Lorem ipsum"},
            *(
                {"id": f"s{n}", "type": "sentence", "page": n, "coords": [1.0, 2.5, 3.0, 4.25],
                 "section_path": "Methods", "xpath": f".//s[@xml:id='s{n}']", "score": 3, "text": f"dolor {n}"}
                for n in range(1, 6)
            ),
            {"id": "f1", "type": "figure_caption", "section_path": "Results", "score": 2},  # approximate anchor
        ]
        evidence = evidence_nodes_from_items(items, source_id=source_id, run_id=run.run_id, producing_backend="grobid")
        recorder.record_evidence(evidence)
        evidence_ids = [node.node_id for node in evidence]
        recorder.record_derivation(
            derivation_from_stage_event(
                "evidence_index", "filter", run_id=run.run_id, inputs=evidence_ids, outputs=evidence_ids[:-1],
                removal_reason="below minimum score", removed_ids=evidence_ids[-1:],
            )
        )
        recorder.mark_stage_complete("evidence_index")
        known = {node.node_id for node in evidence}
        anchors = {node.node_id: node.anchor for node in evidence}

        def _claims(fields: list[dict[str, Any]], origin: ClaimOrigin) -> list[ClaimNode]:
            claims, claim_edges, findings = claim_nodes_from_fields(
                fields, source_id=source_id, run_id=run.run_id, known_evidence_ids=known,
                origin=origin, evidence_anchors=anchors,
            )
            for finding in findings:
                recorder.record_issue(finding)
            for claim in claims:
                recorder.record_claim(claim, [e for e in claim_edges if e.from_id == claim.node_id])
            return claims

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
                {"field_index": 7, "field_name": "sample_size", "extracted_value": "42", "location": ["s1", "s2"],
                 "confidence": "high"},
                # one unknown loc id: a dangling-reference finding, claim retained (5.4)
                {"field_index": 8, "field_name": "design", "extracted_value": "RCT", "location": ["s3", "s99"],
                 "confidence": "medium"},
            ],
            "model",
        )
        recorder.record_derivation(
            derivation_from_stage_event("extraction_chunk", "extract", run_id=run.run_id,
                                        inputs=[format_evidence_id(source_id, f"s{n}") for n in (1, 2, 3)],
                                        outputs=[c.node_id for c in extracted], model_id="gpt-x",
                                        extensions={"x-multiagent-prompt_version": "v3",
                                                    "x-multiagent-replaced_field_indices": [7, 8]})
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


def _realistic_graph(*, fail: tuple[str, str] | None = None) -> ProvenanceGraph:
    """The orchestrator path: build, validate, attach, mark validation complete, compute, attach."""
    recorder = _realistic_recorder(fail=fail)
    recorded = recorder.snapshot()
    graph = build_graph(recorded)
    report = validate_chain(graph, recorded_findings=recorded.findings)
    validated = attach_validation(graph, report)
    recorder.mark_stage_complete("chain_validation")
    completeness = compute_completeness(validated, recorder.snapshot(), report)
    return attach_completeness(validated, completeness)


@pytest.fixture(scope="module")
def realistic_graph() -> ProvenanceGraph:
    return _realistic_graph()


# ---------------------------------------------------------------------------
# Minimal record builders
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


def _source(**extensions: Any) -> SourceNode:
    return SourceNode(
        node_id=_SRC,
        kind="source",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions=extensions,
        document_fingerprint=_FP,
        source_format="pdf",
        source_label="lorem.pdf",
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
    )


def _evidence(
    local_id: str = "e1",
    *,
    evidence_kind: EvidenceKind = "sentence",
    precision: AnchorPrecision = "exact",
    extensions: dict[str, Any] | None = None,
) -> EvidenceNode:
    return EvidenceNode(
        node_id=format_evidence_id(_SRC, local_id),
        kind="evidence",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions=extensions or {},
        local_id=local_id,
        evidence_kind=evidence_kind,
        source_id=_SRC,
        anchor=SourceAnchor(
            precision=precision,
            page=1,
            coords=(1.0, 2.0, 3.0, 4.0),
            section_path="Methods",
            char_span=(3, 17),
            structural_path=".//s[1]",
            missing=("char_span",) if precision != "exact" else (),
        ),
        producing_backend="grobid",
        rank_score=3,
        parser_metadata={"text": "lorem ipsum", "nested": {"b": [1, 2], "a": None}},
    )


def _claim(
    field_index: int = 7,
    *,
    origin: ClaimOrigin = "model",
    support: ClaimSupport = "supported",
    extensions: dict[str, Any] | None = None,
) -> ClaimNode:
    return ClaimNode(
        node_id=derive_claim_id(_SRC, field_index),
        kind="claim",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions=extensions or {},
        source_id=_SRC,
        field_index=field_index,
        field_name=f"field_{field_index}",
        value_digest="0123456789abcdef",
        origin=origin,
        support=support,
        cited_evidence_ids=(_E1,),
        confidence="high",
    )


def _step(
    *,
    kind: DerivationKind = "extract",
    determinism: Determinism = "probabilistic",
    extensions: dict[str, Any] | None = None,
) -> DerivationStep:
    return DerivationStep(
        node_id=f"drv:{kind}{determinism[:4]}",
        kind="derivation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions=extensions or {},
        stage="extraction_chunk",
        derivation_kind=kind,
        determinism=determinism,
        model_id="gpt-x" if determinism == "probabilistic" else None,
        input_ids=(_E1,),
        output_ids=(derive_claim_id(_SRC, 7),),
        removal_reason="lorem" if kind == "remove" else None,
        removed_ids=(_E1,) if kind == "remove" else (),
    )


def _finding(severity: Severity = "warning", **details: Any) -> ValidationFinding:
    return ValidationFinding(
        finding_id=f"f:{severity}",
        code="missing_anchor",
        severity=severity,
        node_id=_E1,
        edge_id=None,
        message="lorem",
        details=details or {"missing": ["page"]},
    )


def _edge(kind: EdgeKind = "cites_evidence", from_id: str | None = None, to_id: str = _E1, **attributes: Any) -> ProvenanceEdge:
    from_id = from_id or derive_claim_id(_SRC, 7)
    return ProvenanceEdge(edge_id=f"{kind}:{from_id}->{to_id}", kind=kind, from_id=from_id, to_id=to_id,
                          attributes=attributes)


def _graph(*nodes: Any, edges: tuple[ProvenanceEdge, ...] = (), **kwargs: Any) -> ProvenanceGraph:
    return ProvenanceGraph(
        schema_id=PROVENANCE_SCHEMA_ID,
        schema_version=PROVENANCE_SCHEMA_VERSION,
        run=_run_identity(),
        nodes={node.node_id: node for node in nodes},
        edges=edges,
        missing_segments=kwargs.pop("missing_segments", ()),
        **kwargs,
    )


def _small_graph() -> ProvenanceGraph:
    return _graph(_source(), _evidence(), _claim(), edges=(_edge(),))


def _node_entry(payload: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(entry for entry in payload["nodes"] if entry["node_id"] == node_id)


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def serialization_log() -> Iterator[_ListHandler]:
    """Capture records on the module's logger (``caplog`` misses ``evi_trace`` loggers once configured)."""
    handler = _ListHandler()
    module_logger = serialization.logger
    previous_level = module_logger.level
    module_logger.addHandler(handler)
    module_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        module_logger.removeHandler(handler)
        module_logger.setLevel(previous_level)


# ---------------------------------------------------------------------------
# The write direction: one data argument, the design's artifact keys (7.3, 7.5)
# ---------------------------------------------------------------------------


def test_graph_to_dict_takes_the_graph_as_its_only_argument() -> None:
    assert list(inspect.signature(graph_to_dict).parameters) == ["graph"]
    assert list(inspect.signature(graph_from_dict).parameters) == ["payload"]


def test_graph_to_dict_rejects_a_non_graph() -> None:
    with pytest.raises(TypeError, match="ProvenanceGraph"):
        graph_to_dict({"schema_id": PROVENANCE_SCHEMA_ID})  # type: ignore[arg-type]


def test_artifact_carries_schema_identity_version_and_the_design_keys_in_order(realistic_graph: ProvenanceGraph) -> None:
    payload = graph_to_dict(realistic_graph)

    assert list(payload) == _TOP_LEVEL_KEYS
    assert payload["schema_id"] == PROVENANCE_SCHEMA_ID == "evitrace/provenance-graph"
    assert payload["schema_version"] == PROVENANCE_SCHEMA_VERSION
    assert list(payload["run"]) == [
        "run_id", "config_fingerprint", "schema_fingerprint", "model_ids", "document_fingerprints", "created_at",
    ]
    assert isinstance(payload["nodes"], list) and isinstance(payload["edges"], list)
    assert [entry["node_id"] for entry in payload["nodes"]] == sorted(realistic_graph.nodes)
    assert [entry["edge_id"] for entry in payload["edges"]] == [edge.edge_id for edge in realistic_graph.edges]
    assert set(payload["validation"]) == {"completed", "incompletion_reason", "findings", "counts_by_severity"}
    assert set(payload["completeness"]) == {"run", "documents", "claims"}


def test_every_node_and_edge_is_self_describing_by_kind(realistic_graph: ProvenanceGraph) -> None:
    """7.3: the artifact names every node's and edge's kind; the node shape follows the design schema."""
    payload = graph_to_dict(realistic_graph)

    kinds = {entry["kind"] for entry in payload["nodes"]}
    assert kinds == {"source", "evidence", "claim", "derivation", "validation"}
    for entry in payload["nodes"]:
        assert list(entry)[:5] == ["node_id", "kind", "run_id", "privacy", "extensions"]
        assert set(entry["privacy"]) == {f.name for f in dataclasses.fields(UNKNOWN_PRIVACY)}
    for entry in payload["edges"]:
        assert list(entry) == ["edge_id", "kind", "from_id", "to_id", "attributes"]


def test_artifact_is_plain_json_data(realistic_graph: ProvenanceGraph) -> None:
    payload = graph_to_dict(realistic_graph)

    def _plain(value: Any) -> None:
        assert type(value) in (dict, list, str, int, float, bool, type(None)), type(value)
        if isinstance(value, dict):
            assert all(type(key) is str for key in value)
            for item in value.values():
                _plain(item)
        elif isinstance(value, list):
            for item in value:
                _plain(item)

    _plain(payload)
    assert _json_round_trip(payload) == payload


def test_output_is_deterministic_for_equal_graphs_whatever_the_mapping_insertion_order() -> None:
    first = _graph(_source(**{"x-a-one": 1, "x-b-two": {"q": 1, "p": 2}}), _evidence())
    second = _graph(_evidence(), _source(**{"x-b-two": {"p": 2, "q": 1}, "x-a-one": 1}))
    assert first == second

    assert json.dumps(graph_to_dict(first)) == json.dumps(graph_to_dict(second))


def test_a_value_json_cannot_represent_is_rejected_on_write() -> None:
    graph = _graph(_source(**{"x-test-tags": {"a", "b"}}))  # frozen into a frozenset

    with pytest.raises(TypeError, match="x-test-tags"):
        graph_to_dict(graph)


def test_a_non_string_mapping_key_is_rejected_on_write() -> None:
    graph = _graph(_evidence(extensions={"x-test-map": {1: "one"}}))

    with pytest.raises(TypeError, match="x-test-map"):
        graph_to_dict(graph)


# ---------------------------------------------------------------------------
# Round trip (design Unit Test 6; 10.3, 10.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fail", [None, ("synthesis", "lorem timeout")], ids=["clean", "failed-stage"])
def test_realistic_graph_round_trips_through_json(fail: tuple[str, str] | None) -> None:
    graph = _realistic_graph(fail=fail)
    assert graph.validation is not None and graph.completeness is not None
    assert graph.validation.findings, "the realistic run carries findings (unknown loc id)"
    if fail is not None:
        assert graph.missing_segments

    restored = graph_from_dict(_json_round_trip(graph_to_dict(graph)))

    assert restored == graph
    assert restored.edges == graph.edges  # order preserved, not just set-equal
    assert restored.missing_segments == graph.missing_segments
    assert restored.validation == graph.validation
    assert restored.completeness == graph.completeness
    assert graph_to_dict(restored) == graph_to_dict(graph)


def test_round_trip_restores_tuple_typed_fields_as_tuples(realistic_graph: ProvenanceGraph) -> None:
    restored = graph_from_dict(_json_round_trip(graph_to_dict(realistic_graph)))

    assert type(restored.edges) is tuple and type(restored.missing_segments) is tuple
    assert type(restored.run.model_ids) is tuple
    for node in restored.nodes.values():
        for field in dataclasses.fields(node):
            if typing.get_origin(typing.get_type_hints(type(node))[field.name]) is tuple:
                assert type(getattr(node, field.name)) is tuple, (node.node_id, field.name)
        if isinstance(node, EvidenceNode):
            assert node.anchor.coords is None or type(node.anchor.coords) is tuple
            assert type(node.anchor.missing) is tuple
    assert restored.validation is not None and type(restored.validation.findings) is tuple
    assert restored.completeness is not None
    for completeness in (restored.completeness.run, *restored.completeness.claims.values()):
        assert type(completeness.missing_record_kinds) is tuple
        assert type(completeness.responsible_stages) is tuple


def test_round_trip_keeps_node_classes_and_privacy_carriers(realistic_graph: ProvenanceGraph) -> None:
    restored = graph_from_dict(_json_round_trip(graph_to_dict(realistic_graph)))

    for node_id, node in realistic_graph.nodes.items():
        assert type(restored.nodes[node_id]) is type(node)
        assert restored.nodes[node_id].privacy == node.privacy
    labeled = restored.nodes[_SRC]
    assert labeled.privacy.state == "labeled"
    assert (labeled.privacy.label, labeled.privacy.decision) == ("lorem-label", "lorem-decision")


def test_namespaced_extension_fields_round_trip_unchanged_and_apart_from_core_fields(
    realistic_graph: ProvenanceGraph,
) -> None:
    payload = graph_to_dict(realistic_graph)
    steps = [entry for entry in payload["nodes"] if entry["kind"] == "derivation" and entry["extensions"]]
    chunk = next(entry for entry in steps if entry["stage"] == "extraction_chunk")
    qc = next(entry for entry in steps if entry["stage"] == "quality_control")

    assert chunk["extensions"] == {"x-multiagent-prompt_version": "v3", "x-multiagent-replaced_field_indices": [7, 8]}
    assert "prompt_version" not in chunk  # visibly distinct from core fields (10.4)
    assert qc["extensions"]["x-qc-scores"] == {"fidelity": 0.97, "sections": [1, 2]}

    restored = graph_from_dict(_json_round_trip(payload))
    for node_id, node in realistic_graph.nodes.items():
        assert restored.nodes[node_id].extensions == node.extensions


def test_any_validation_record_the_graph_carries_round_trips_without_core_change(
    realistic_graph: ProvenanceGraph,
) -> None:
    """10.3: a record from a new validation method is serialized like the builder's own."""
    new_method = ValidationRecord(
        node_id=f"val:{realistic_graph.run.run_id}:lorem_consistency_check",
        kind="validation",
        run_id=realistic_graph.run.run_id,
        privacy=UNKNOWN_PRIVACY,
        extensions={"x-lorem-threshold": 0.5},
        check_name="lorem_consistency_check",
        finding_ids=("f:1", "f:2"),
        completed=False,
    )
    graph = dataclasses.replace(realistic_graph, nodes={**realistic_graph.nodes, new_method.node_id: new_method})

    restored = graph_from_dict(_json_round_trip(graph_to_dict(graph)))

    assert restored == graph
    assert restored.nodes[new_method.node_id] == new_method
    assert {node.check_name for node in restored.nodes_of_kind("validation")} == {  # type: ignore[attr-defined]
        "chain_validation",
        "lorem_consistency_check",
    }


def test_edge_order_is_preserved_even_when_not_sorted() -> None:
    edges = (_edge("cites_evidence"), _edge("belongs_to_source", from_id=_E1, to_id=_SRC), _edge("anchored_in", to_id=_SRC))
    graph = _graph(_source(), _evidence(), _claim(), edges=edges)

    restored = graph_from_dict(_json_round_trip(graph_to_dict(graph)))

    assert [edge.edge_id for edge in restored.edges] == [edge.edge_id for edge in edges]
    assert restored == graph


def test_a_graph_without_reports_round_trips_with_null_reports() -> None:
    graph = _small_graph()
    payload = graph_to_dict(graph)

    assert payload["validation"] is None and payload["completeness"] is None
    assert graph_from_dict(_json_round_trip(payload)) == graph


def test_graph_from_dict_does_not_modify_the_payload(realistic_graph: ProvenanceGraph) -> None:
    payload = _json_round_trip(graph_to_dict(realistic_graph))
    payload["schema_version"] = "1.7.0"
    payload["nodes"][0]["lorem_future_field"] = {"a": 1}
    snapshot = copy.deepcopy(payload)

    graph_from_dict(payload)

    assert payload == snapshot


# ---------------------------------------------------------------------------
# Every closed-vocabulary member round-trips (design ProvenanceModels → Validation)
# ---------------------------------------------------------------------------


def _vocabulary_graph(
    *,
    evidence_kind: EvidenceKind = "sentence",
    precision: AnchorPrecision = "exact",
    derivation_kind: DerivationKind = "extract",
    determinism: Determinism = "probabilistic",
    support: ClaimSupport = "supported",
    origin: ClaimOrigin = "model",
    edge_kind: EdgeKind = "cites_evidence",
    severity: Severity = "warning",
    state: CompletenessState = "complete",
    privacy_state: PrivacyState = "unknown",
) -> ProvenanceGraph:
    source = dataclasses.replace(_source(), privacy=dataclasses.replace(UNKNOWN_PRIVACY, state=privacy_state))
    completeness = ArtifactCompleteness(state=state, reason=None if state == "complete" else "lorem",
                                        missing_record_kinds=(), responsible_stages=())
    return _graph(
        source,
        _evidence(evidence_kind=evidence_kind, precision=precision),
        _claim(support=support, origin=origin),
        _step(kind=derivation_kind, determinism=determinism),
        edges=(_edge(edge_kind),),
        missing_segments=(MissingSegment(stage="synthesis", record_kind="claim", reason="lorem", affected_ids=("a", "b")),),
        validation=ChainValidationReport(completed=True, incompletion_reason=None, findings=(_finding(severity),),
                                         counts_by_severity={severity: 1}),
        completeness=CompletenessReport(run=completeness, documents={_SRC: completeness},
                                        claims={derive_claim_id(_SRC, 7): completeness}),
    )


_VOCABULARY_CASES = [
    *(("evidence_kind", member) for member in typing.get_args(EvidenceKind)),
    *(("precision", member) for member in typing.get_args(AnchorPrecision)),
    *(("derivation_kind", member) for member in typing.get_args(DerivationKind)),
    *(("determinism", member) for member in typing.get_args(Determinism)),
    *(("support", member) for member in typing.get_args(ClaimSupport)),
    *(("origin", member) for member in typing.get_args(ClaimOrigin)),
    *(("edge_kind", member) for member in typing.get_args(EdgeKind)),
    *(("severity", member) for member in typing.get_args(Severity)),
    *(("state", member) for member in typing.get_args(CompletenessState)),
    *(("privacy_state", member) for member in typing.get_args(PrivacyState)),
]


@pytest.mark.parametrize(("vocabulary", "member"), _VOCABULARY_CASES, ids=[f"{v}={m}" for v, m in _VOCABULARY_CASES])
def test_every_vocabulary_member_round_trips(vocabulary: str, member: str) -> None:
    graph = _vocabulary_graph(**{vocabulary: member})

    assert graph_from_dict(_json_round_trip(graph_to_dict(graph))) == graph


def test_every_node_kind_round_trips() -> None:
    record = ValidationRecord(node_id=f"val:{_RUN}:chain_validation", kind="validation", run_id=_RUN,
                              privacy=UNKNOWN_PRIVACY, extensions={}, check_name="chain_validation",
                              finding_ids=("f:warning",), completed=True)
    graph = _graph(_source(), _evidence(), _claim(), _step(), record)

    restored = graph_from_dict(_json_round_trip(graph_to_dict(graph)))

    assert {node.kind for node in restored.nodes.values()} == set(typing.get_args(typing.get_type_hints(SourceNode)["kind"]))
    assert restored == graph


# ---------------------------------------------------------------------------
# Compatible minor versions: unknown keys are preserved, never dropped (10.5)
# ---------------------------------------------------------------------------


def _future_minor_payload() -> dict[str, Any]:
    finding = _finding("warning")
    graph = _graph(
        _source(),
        _evidence(extensions={"x-lorem-kept": [1, 2]}),
        _claim(),
        edges=(_edge(weight_hint="lorem"),),
        validation=ChainValidationReport(completed=True, incompletion_reason=None, findings=(finding,),
                                         counts_by_severity={"warning": 1}),
    )
    payload = _json_round_trip(graph_to_dict(graph))
    payload["schema_version"] = "1.4.2"
    evidence = _node_entry(payload, _E1)
    evidence["sentence_hash_v2"] = "lorem"                  # unknown core-style key
    evidence["x-future-flag"] = True                         # unknown key that already looks namespaced
    evidence["anchor"]["page_label"] = "iv"                  # unknown key inside the anchor
    evidence["privacy"]["policy_ref"] = "lorem-policy"       # unknown key inside the carrier
    evidence["extensions"]["Not A Namespaced Key"] = {"a": 1}  # extension key outside the charset
    payload["edges"][0]["confidence_weight"] = 0.5           # unknown edge key
    payload["validation"]["findings"][0]["rule_version"] = 3  # unknown finding key
    return payload


def test_compatible_minor_artifact_with_unknown_node_keys_loads_with_them_preserved() -> None:
    graph = graph_from_dict(_future_minor_payload())

    evidence = graph.nodes[_E1]
    assert isinstance(evidence, EvidenceNode)
    assert evidence.extensions == {
        "x-lorem-kept": [1, 2],
        COMPAT_UNKNOWN_KEY: {
            "sentence_hash_v2": "lorem",
            "x-future-flag": True,
            "anchor": {"page_label": "iv"},
            "privacy": {"policy_ref": "lorem-policy"},
            "extensions": {"Not A Namespaced Key": {"a": 1}},
        },
    }
    assert COMPAT_UNKNOWN_KEY == "x-compat-unknown"
    # core fields are interpreted normally
    assert evidence.anchor == _evidence().anchor
    assert evidence.privacy == UNKNOWN_PRIVACY
    assert evidence.local_id == "e1"
    # the version found is recorded on the graph (10.5)
    assert graph.schema_version == "1.4.2"


def test_compatible_minor_unknown_edge_and_finding_keys_are_preserved_in_their_free_mappings() -> None:
    graph = graph_from_dict(_future_minor_payload())

    assert graph.edges[0].attributes == {"weight_hint": "lorem", COMPAT_UNKNOWN_KEY: {"confidence_weight": 0.5}}
    assert graph.validation is not None
    assert graph.validation.findings[0].details == {"missing": ["page"], COMPAT_UNKNOWN_KEY: {"rule_version": 3}}


def test_preserved_unknown_keys_survive_a_further_round_trip() -> None:
    loaded = graph_from_dict(_future_minor_payload())

    again = graph_from_dict(_json_round_trip(graph_to_dict(loaded)))

    assert again == loaded


def test_reserved_key_collision_uses_the_next_free_reserved_key() -> None:
    payload = _json_round_trip(graph_to_dict(_graph(_source(**{COMPAT_UNKNOWN_KEY: {"earlier": 1}}))))
    payload["schema_version"] = "1.1.0"
    _node_entry(payload, _SRC)["later_field"] = 2

    source = graph_from_dict(payload).nodes[_SRC]

    assert source.extensions == {COMPAT_UNKNOWN_KEY: {"earlier": 1}, f"{COMPAT_UNKNOWN_KEY}_2": {"later_field": 2}}


def test_unknown_keys_without_an_extension_slot_are_logged_and_the_artifact_still_loads(
    serialization_log: _ListHandler,
) -> None:
    payload = _json_round_trip(graph_to_dict(_vocabulary_graph()))
    payload["schema_version"] = "1.2.0"
    payload["lorem_section"] = {"a": 1}
    payload["run"]["run_label"] = "lorem"
    payload["missing_segments"][0]["retry_count"] = 2
    payload["completeness"]["documents"][_SRC]["confidence"] = 0.9

    graph = graph_from_dict(payload)

    assert graph.run == _run_identity()
    warnings = [r.getMessage() for r in serialization_log.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    for path in ("lorem_section", "run.run_label", "missing_segments[0].retry_count",
                 f"completeness.documents[{_SRC!r}].confidence"):
        assert path in warnings[0], path


def test_a_clean_round_trip_logs_no_warning(realistic_graph: ProvenanceGraph, serialization_log: _ListHandler) -> None:
    graph_from_dict(_json_round_trip(graph_to_dict(realistic_graph)))

    assert not [r for r in serialization_log.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("version", ["1.0.0", "1.0.1", "1.9.0", "1.12.3-rc.1", "1.0.0+build.7"])
def test_any_matching_major_is_accepted(version: str) -> None:
    payload = _json_round_trip(graph_to_dict(_small_graph()))
    payload["schema_version"] = version

    graph = graph_from_dict(payload)

    assert graph.schema_version == version
    assert graph == dataclasses.replace(_small_graph(), schema_version=version)


def test_optional_fields_absent_from_an_artifact_take_their_defaults() -> None:
    payload = _json_round_trip(graph_to_dict(_graph(_source(), _step())))
    del payload["validation"], payload["completeness"]
    step_entry = next(entry for entry in payload["nodes"] if entry["kind"] == "derivation")
    del step_entry["removal_reason"], step_entry["removed_ids"]
    for entry in payload["nodes"]:
        del entry["privacy"]["supplied_by"]

    graph = graph_from_dict(payload)

    assert graph == _graph(_source(), _step())


# ---------------------------------------------------------------------------
# Incompatible majors: report, never materialize (10.6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["2.0.0", "0.9.0", "10.0.0"])
def test_an_incompatible_major_raises_naming_found_and_supported_versions(version: str) -> None:
    payload = _json_round_trip(graph_to_dict(_small_graph()))
    payload["schema_version"] = version

    with pytest.raises(UnrecognizedSchemaVersionError) as caught:
        graph_from_dict(payload)

    error = caught.value
    assert error.found_version == version
    assert error.supported_versions == ("1.x.x",)
    assert version in str(error) and "1.x.x" in str(error)
    assert isinstance(error, Exception)


@pytest.mark.parametrize("version", [None, 1, 1.0, "", "1", "1.0", "v1.0.0", "01.0.0", "one.zero.zero", "1.0.0.0"])
def test_an_unparseable_or_absent_version_is_unrecognized(version: object) -> None:
    payload = _json_round_trip(graph_to_dict(_small_graph()))
    payload["schema_version"] = version

    with pytest.raises(UnrecognizedSchemaVersionError) as caught:
        graph_from_dict(payload)

    assert caught.value.found_version == version


def test_a_missing_version_key_is_unrecognized() -> None:
    payload = _json_round_trip(graph_to_dict(_small_graph()))
    del payload["schema_version"]

    with pytest.raises(UnrecognizedSchemaVersionError) as caught:
        graph_from_dict(payload)

    assert caught.value.found_version is None


def test_the_version_guard_runs_before_anything_is_materialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """An incompatible major is reported even when its content is unreadable to this reader."""
    constructed: list[type] = []
    original = serialization._construct

    def _spy(cls: type, kwargs: dict[str, Any], path: str) -> Any:
        constructed.append(cls)
        return original(cls, kwargs, path)

    monkeypatch.setattr(serialization, "_construct", _spy)
    payload = {"schema_id": PROVENANCE_SCHEMA_ID, "schema_version": "2.0.0", "run": "lorem", "nodes": {"a": 1}}

    with pytest.raises(UnrecognizedSchemaVersionError):
        graph_from_dict(payload)

    assert constructed == []


# ---------------------------------------------------------------------------
# Compatible but malformed artifacts: validated against the closed unions
# ---------------------------------------------------------------------------


def _compatible_payload() -> dict[str, Any]:
    return _json_round_trip(graph_to_dict(_vocabulary_graph()))


def _set_node_field(node_kind: str, key: str, value: Any) -> Any:
    def mutate(payload: dict[str, Any]) -> None:
        entry = next(e for e in payload["nodes"] if e["kind"] == node_kind)
        entry[key] = value

    return mutate


_MALFORMED_CASES: list[tuple[str, Any]] = [
    ("not-a-mapping", None),
    ("wrong-schema-id", lambda p: p.__setitem__("schema_id", "lorem/other-graph")),
    ("unknown-node-kind", _set_node_field("source", "kind", "lorem")),
    ("evidence-kind", _set_node_field("evidence", "evidence_kind", "paragraph")),
    ("derivation-kind", _set_node_field("derivation", "derivation_kind", "translate")),
    ("determinism-bool", _set_node_field("derivation", "determinism", True)),
    ("claim-support", _set_node_field("claim", "support", "maybe")),
    ("claim-origin", _set_node_field("claim", "origin", "oracle")),
    ("field-index-bool", _set_node_field("claim", "field_index", True)),
    ("field-index-str", _set_node_field("claim", "field_index", "7")),
    ("empty-node-id", _set_node_field("claim", "node_id", "")),
    ("missing-required", lambda p: next(e for e in p["nodes"] if e["kind"] == "claim").pop("value_digest")),
    ("precision", lambda p: next(e for e in p["nodes"] if e["kind"] == "evidence")["anchor"].__setitem__("precision", "rough")),
    ("coords-length", lambda p: next(e for e in p["nodes"] if e["kind"] == "evidence")["anchor"].__setitem__("coords", [1, 2])),
    ("privacy-state", lambda p: p["nodes"][0]["privacy"].__setitem__("state", "secret")),
    ("edge-kind", lambda p: p["edges"][0].__setitem__("kind", "mentions")),
    ("edge-empty-to", lambda p: p["edges"][0].__setitem__("to_id", "")),
    ("severity", lambda p: p["validation"]["findings"][0].__setitem__("severity", "fatal")),
    ("counts-key", lambda p: p["validation"]["counts_by_severity"].__setitem__("fatal", 1)),
    ("completeness-state", lambda p: p["completeness"]["run"].__setitem__("state", "partial")),
    ("nodes-not-list", lambda p: p.__setitem__("nodes", {})),
    ("node-not-object", lambda p: p["nodes"].append("lorem")),
    ("duplicate-node-id", lambda p: p["nodes"].append(copy.deepcopy(p["nodes"][0]))),
    ("run-missing", lambda p: p.pop("run")),
    ("model-ids-not-list", lambda p: p["run"].__setitem__("model_ids", "gpt-x")),
    ("document-fingerprint-int", lambda p: p["run"]["document_fingerprints"].__setitem__(_SRC, 5)),
    ("extensions-not-object", _set_node_field("source", "extensions", [])),
    ("details-not-object", lambda p: p["validation"]["findings"][0].__setitem__("details", "lorem")),
]


@pytest.mark.parametrize(("case", "mutate"), _MALFORMED_CASES, ids=[case for case, _ in _MALFORMED_CASES])
def test_a_compatible_artifact_outside_the_closed_unions_is_malformed(case: str, mutate: Any) -> None:
    payload: Any = _compatible_payload() if mutate is not None else ["lorem"]
    if mutate is not None:
        mutate(payload)

    with pytest.raises(MalformedProvenanceArtifactError):
        graph_from_dict(payload)


def test_malformed_artifact_error_is_a_value_error_naming_the_path() -> None:
    payload = _compatible_payload()
    next(e for e in payload["nodes"] if e["kind"] == "claim")["origin"] = "oracle"

    with pytest.raises(ValueError, match=r"nodes\[\d+\]\.origin"):
        graph_from_dict(payload)
