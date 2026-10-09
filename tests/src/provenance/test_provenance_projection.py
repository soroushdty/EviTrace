"""Tests for ``provenance.projection`` — the ordered event-log projection (7.6).

Covers task 4.4 of provenance-core (design "Analysis Layer → Projection"):

- the projection is a pure function of the graph: two projections of one
  graph are equal, and graphs equal by value (whatever their record insertion
  order) project to equal logs;
- the order is total and needs no clock: sequence numbers are unique and
  contiguous, and events follow ``(STAGE_ORDER position, node-kind rank,
  node_id)``;
- every event field is derivable from the graph alone (no field absent from
  the graph — the "ledger is a projection" assertion of Integration Test 5);
- non-derivation nodes take the stage of the step that produces them, else the
  sole declared stage for their kind, else ``"unattributed"``, which — like any
  undeclared stage — sorts after every declared stage;
- the module performs no file access, no clock or randomness, holds no
  parallel store, and ``ProvenanceEvent`` is the only dataclass it declares.

Synthetic identifiers only.
"""

from __future__ import annotations

import ast
import builtins
import copy
import dataclasses
import io
import os
import typing
from pathlib import Path

import pytest

from provenance import projection as projection_module
from provenance.contracts import STAGE_ORDER
from provenance.graph import ProvenanceGraph, attach_validation, build_graph
from provenance.identity import (
    derive_claim_id,
    derive_derivation_id,
    derive_source_id,
    format_evidence_id,
)
from provenance.models import (
    UNKNOWN_PRIVACY,
    ChainValidationReport,
    ClaimNode,
    DerivationStep,
    EvidenceNode,
    NodeKind,
    ProvenanceEdge,
    ProvenanceNode,
    RecordedRun,
    RunIdentity,
    SourceAnchor,
    SourceNode,
    ValidationRecord,
)
from provenance.projection import ProvenanceEvent, project_event_log

_RUN = "run:0123456789abcdef"
_FP = "aaaabbbbccccdddd"
_SRC = derive_source_id(_FP)
_FP2 = "eeeeffff00001111"
_SRC2 = derive_source_id(_FP2)
_E1 = format_evidence_id(_SRC, "e1")
_E2 = format_evidence_id(_SRC, "e2")
_MODULE_PATH = Path(projection_module.__file__)
_KIND_ORDER = typing.get_args(NodeKind)


# ---------------------------------------------------------------------------
# Minimal record builders
# ---------------------------------------------------------------------------


def _run_identity() -> RunIdentity:
    return RunIdentity(
        run_id=_RUN,
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
        document_fingerprints={_SRC: _FP, _SRC2: _FP2},
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


def _evidence(local_id: str, *, source_id: str = _SRC) -> EvidenceNode:
    return EvidenceNode(
        node_id=format_evidence_id(source_id, local_id),
        kind="evidence",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        local_id=local_id,
        evidence_kind="sentence",
        source_id=source_id,
        anchor=SourceAnchor(precision="exact", page=1, coords=(1.0, 2.0, 3.0, 4.0), section_path="Methods"),
        producing_backend="grobid",
        rank_score=3,
        parser_metadata={"text": "lorem ipsum"},
    )


def _claim(field_index: int, cited: tuple[str, ...] = (_E1,), *, source_id: str = _SRC, revision: int = 0) -> ClaimNode:
    return ClaimNode(
        node_id=derive_claim_id(source_id, field_index, revision),
        kind="claim",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={"x-test-note": "lorem"},
        source_id=source_id,
        field_index=field_index,
        field_name=f"field_{field_index}",
        value_digest="0123456789abcdef",
        origin="model",
        support="supported" if cited else "unsupported",
        cited_evidence_ids=cited,
        confidence="high",
    )


def _step(inputs: tuple[str, ...], outputs: tuple[str, ...], *, stage: str, kind: str = "extract") -> DerivationStep:
    return DerivationStep(
        node_id=derive_derivation_id(stage, kind, inputs, outputs),
        kind="derivation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        stage=stage,
        derivation_kind=kind,
        determinism="probabilistic" if kind == "extract" else "deterministic",
        model_id="gpt-x" if kind == "extract" else None,
        input_ids=inputs,
        output_ids=outputs,
    )


def _edge(kind: str, from_id: str, to_id: str) -> ProvenanceEdge:
    return ProvenanceEdge(edge_id=f"{kind}:{from_id}->{to_id}", kind=kind, from_id=from_id, to_id=to_id, attributes={})


def _graph(nodes: list[ProvenanceNode], edges: list[ProvenanceEdge] | None = None) -> ProvenanceGraph:
    recorded = RecordedRun(
        run=_run_identity(),
        nodes={node.node_id: node for node in nodes},
        edges=tuple(edges or ()),
        findings=(),
        stage_status={},
        stage_failure_reasons={},
    )
    return build_graph(recorded)


def _run_nodes() -> tuple[list[ProvenanceNode], list[ProvenanceEdge]]:
    """A realistic single-document run touching every declared stage."""
    claim_a = _claim(3, (_E1,))
    claim_b = _claim(4, (_E1, _E2))
    synthesized = _claim(3, (_E1,), revision=1)
    prefill = _claim(1, (_E2,))
    nodes: list[ProvenanceNode] = [
        _source(),
        _step((_SRC,), (), stage="quality_control", kind="reconcile"),
        _evidence("e1"),
        _evidence("e2"),
        prefill,
        _step((_E2,), (prefill.node_id,), stage="metadata_prefill", kind="prefill"),
        claim_a,
        claim_b,
        _step((_E1, _E2), (claim_a.node_id, claim_b.node_id), stage="extraction_chunk"),
        synthesized,
        _step((claim_a.node_id, claim_b.node_id), (synthesized.node_id,), stage="synthesis", kind="synthesize"),
    ]
    edges = [_edge("cites_evidence", c.node_id, e) for c in (claim_a, claim_b, synthesized, prefill) for e in c.cited_evidence_ids]
    edges.append(_edge("revises", synthesized.node_id, claim_a.node_id))
    return nodes, edges


def _run_graph() -> ProvenanceGraph:
    nodes, edges = _run_nodes()
    return _graph(nodes, edges)


def _validated(graph: ProvenanceGraph) -> ProvenanceGraph:
    report = ChainValidationReport(completed=True, incompletion_reason=None, findings=(), counts_by_severity={})
    return attach_validation(graph, report)


def _by_id(events: tuple[ProvenanceEvent, ...]) -> dict[str, ProvenanceEvent]:
    return {event.node_id: event for event in events}


def _position(stage: str) -> int:
    if stage in STAGE_ORDER:
        return STAGE_ORDER.index(stage)
    return len(STAGE_ORDER) + (1 if stage == "unattributed" else 0)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_one_event_per_node_with_design_fields() -> None:
    graph = _validated(_run_graph())

    events = project_event_log(graph)

    assert isinstance(events, tuple)
    assert all(isinstance(event, ProvenanceEvent) for event in events)
    assert sorted(event.node_id for event in events) == sorted(graph.nodes)
    assert [f.name for f in dataclasses.fields(ProvenanceEvent)] == [
        "sequence",
        "stage",
        "event_kind",
        "node_id",
        "node_kind",
        "summary",
    ]


def test_event_is_frozen_and_its_summary_read_only() -> None:
    event = project_event_log(_run_graph())[0]

    with pytest.raises(dataclasses.FrozenInstanceError):
        event.sequence = 99  # type: ignore[misc]
    with pytest.raises(TypeError):
        event.summary["injected"] = 1  # type: ignore[index]
    hash(event)


def test_empty_graph_projects_to_empty_log() -> None:
    assert project_event_log(_graph([])) == ()


def test_non_graph_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        project_event_log({"nodes": {}})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Purity and determinism
# ---------------------------------------------------------------------------


def test_two_projections_of_one_graph_are_equal() -> None:
    graph = _validated(_run_graph())

    assert project_event_log(graph) == project_event_log(graph)


def test_projection_is_independent_of_record_insertion_order() -> None:
    nodes, edges = _run_nodes()
    forward = _graph(nodes, edges)
    backward = _graph(list(reversed(nodes)), list(reversed(edges)))

    assert project_event_log(forward) == project_event_log(backward)


def test_projection_is_independent_of_node_mapping_order() -> None:
    graph = _run_graph()
    shuffled = dataclasses.replace(graph, nodes=dict(reversed(list(graph.nodes.items()))))

    assert list(shuffled.nodes) != list(graph.nodes)
    assert project_event_log(shuffled) == project_event_log(graph)


def test_projection_does_not_modify_the_graph() -> None:
    graph = _validated(_run_graph())
    before = copy.deepcopy(graph)

    project_event_log(graph)

    assert graph == before


# ---------------------------------------------------------------------------
# Total order
# ---------------------------------------------------------------------------


def test_sequence_numbers_are_unique_and_contiguous() -> None:
    events = project_event_log(_validated(_run_graph()))

    sequences = [event.sequence for event in events]
    assert len(set(sequences)) == len(sequences)
    assert sequences == list(range(1, len(events) + 1))


def test_events_follow_stage_kind_rank_and_node_id() -> None:
    events = project_event_log(_validated(_run_graph()))

    keys = [(_position(e.stage), e.stage, _KIND_ORDER.index(e.node_kind), e.node_id) for e in events]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_declared_stages_appear_in_stage_order() -> None:
    events = project_event_log(_validated(_run_graph()))

    stages = list(dict.fromkeys(event.stage for event in events))
    assert stages == list(STAGE_ORDER)


def test_kind_rank_orders_nodes_within_one_stage() -> None:
    events = project_event_log(_run_graph())

    chunk = [event for event in events if event.stage == "extraction_chunk"]
    assert [event.node_kind for event in chunk] == ["claim", "claim", "derivation"]
    assert [event.node_id for event in chunk[:2]] == sorted(event.node_id for event in chunk[:2])


def test_node_id_breaks_ties_within_a_stage_and_kind() -> None:
    graph = _graph([_source(_FP2), _source(_FP)])

    events = project_event_log(graph)

    assert [event.node_id for event in events] == sorted([_SRC, _SRC2])
    assert {event.stage for event in events} == {"document_extraction"}


# ---------------------------------------------------------------------------
# Stage attribution (only DerivationStep carries a stage)
# ---------------------------------------------------------------------------


def test_derivation_step_uses_its_own_stage() -> None:
    graph = _run_graph()
    events = _by_id(project_event_log(graph))

    for node in graph.nodes_of_kind("derivation"):
        assert events[node.node_id].stage == node.stage  # type: ignore[attr-defined]
        assert events[node.node_id].summary["stage_basis"] == "own"


def test_produced_node_takes_its_producing_steps_stage() -> None:
    events = _by_id(project_event_log(_run_graph()))

    assert events[derive_claim_id(_SRC, 1)].stage == "metadata_prefill"
    assert events[derive_claim_id(_SRC, 3)].stage == "extraction_chunk"
    assert events[derive_claim_id(_SRC, 3, 1)].stage == "synthesis"
    assert events[derive_claim_id(_SRC, 3)].summary["stage_basis"] == "producing_step"


def test_unproduced_node_takes_the_sole_contract_stage_for_its_kind() -> None:
    events = _by_id(project_event_log(_validated(_run_graph())))

    assert events[_SRC].stage == "document_extraction"
    assert events[_E1].stage == "evidence_index"
    validation = next(event for event in events.values() if event.node_kind == "validation")
    assert validation.stage == "chain_validation"
    assert events[_SRC].summary["stage_basis"] == "stage_contract"


def test_node_with_several_producers_takes_the_earliest_stage() -> None:
    claim = _claim(5)
    early = _step((_E1,), (claim.node_id,), stage="extraction_chunk")
    late = _step((_E2,), (claim.node_id,), stage="synthesis", kind="merge")
    graph = _graph([_source(), _evidence("e1"), _evidence("e2"), claim, late, early])

    assert _by_id(project_event_log(graph))[claim.node_id].stage == "extraction_chunk"


def test_in_place_transform_does_not_count_as_producing() -> None:
    evidence = _evidence("e1")
    in_place = _step((evidence.node_id,), (evidence.node_id,), stage="synthesis", kind="filter")
    graph = _graph([_source(), evidence, in_place])

    assert _by_id(project_event_log(graph))[evidence.node_id].stage == "evidence_index"


def test_claim_without_producing_step_is_unattributed_and_sorts_last() -> None:
    orphan_claim = _claim(9)
    nodes, edges = _run_nodes()
    graph = _validated(_graph([*nodes, orphan_claim], edges))

    events = project_event_log(graph)

    assert events[-1].node_id == orphan_claim.node_id
    assert events[-1].stage == "unattributed"
    assert events[-1].summary["stage_basis"] == "none"


def test_undeclared_stage_sorts_after_declared_and_before_unattributed() -> None:
    claim = _claim(9)
    routed = _step((_E1,), (), stage="zz_downstream_stage", kind="filter")
    graph = _validated(_graph([_source(), _evidence("e1"), claim, routed]))

    events = project_event_log(graph)
    stages = [event.stage for event in events]

    assert stages[-2:] == ["zz_downstream_stage", "unattributed"]
    assert stages.index("chain_validation") < stages.index("zz_downstream_stage")


# ---------------------------------------------------------------------------
# Every event field is derivable from the graph alone (ledger is a projection)
# ---------------------------------------------------------------------------


_DERIVED_SUMMARY_KEYS = {"stage_basis", "anchor_precision"}


def test_every_event_field_is_derivable_from_the_graph() -> None:
    graph = _validated(_run_graph())

    for event in project_event_log(graph):
        node = graph.nodes[event.node_id]
        assert event.node_kind == node.kind
        assert event.stage in STAGE_ORDER or event.stage == "unattributed" or event.stage in {
            step.stage for step in graph.nodes_of_kind("derivation")  # type: ignore[attr-defined]
        }
        for key, value in event.summary.items():
            if key == "anchor_precision":
                assert value == node.anchor.precision  # type: ignore[attr-defined]
            elif key not in _DERIVED_SUMMARY_KEYS:
                assert value == getattr(node, key), f"{event.node_id}: summary[{key!r}] not from the node"


def test_event_kind_is_derived_from_the_node() -> None:
    graph = _validated(_run_graph())

    for event in project_event_log(graph):
        node = graph.nodes[event.node_id]
        if isinstance(node, DerivationStep):
            assert event.event_kind == f"derivation_{node.derivation_kind}"
        else:
            assert event.event_kind == f"{node.kind}_recorded"


def test_summary_never_copies_evidence_text_or_extensions() -> None:
    for event in project_event_log(_run_graph()):
        assert "parser_metadata" not in event.summary
        assert "extensions" not in event.summary
        assert "privacy" not in event.summary
        assert "lorem ipsum" not in repr(event.summary)


def test_projection_ignores_missing_segments_and_reports() -> None:
    graph = _run_graph()
    with_report = dataclasses.replace(
        graph,
        validation=ChainValidationReport(completed=False, incompletion_reason="x", findings=(), counts_by_severity={}),
    )

    assert project_event_log(with_report) == project_event_log(graph)


def test_validation_record_summary() -> None:
    graph = _validated(_run_graph())
    record = graph.nodes_of_kind("validation")[0]
    assert isinstance(record, ValidationRecord)

    event = _by_id(project_event_log(graph))[record.node_id]

    assert event.summary["check_name"] == "chain_validation"
    assert event.summary["completed"] is True


# ---------------------------------------------------------------------------
# No I/O, no clock, no randomness, no parallel store
# ---------------------------------------------------------------------------


_ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "types",
    "typing",
    "collections.abc",
    "provenance.models",
    "provenance.contracts",
    "provenance.graph",
}
_FORBIDDEN_CALLS = {"open", "print", "input", "exec", "eval", "compile", "__import__", "globals", "breakpoint"}


def _tree() -> ast.Module:
    return ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))


def test_module_imports_nothing_that_could_touch_files_clocks_or_randomness() -> None:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are not used in provenance"
            imported.add(node.module or "")

    assert imported <= _ALLOWED_IMPORTS, f"unexpected imports: {sorted(imported - _ALLOWED_IMPORTS)}"


def test_module_calls_no_io_or_dynamic_builtin() -> None:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in _FORBIDDEN_CALLS, f"projection.py calls {node.func.id}()"
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"write_text", "write_bytes", "read_text", "read_bytes", "mkdir", "open"}


def test_projection_performs_no_file_access_at_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _validated(_run_graph())

    def _refuse(*_args: object, **_kwargs: object) -> typing.NoReturn:
        raise AssertionError("project_event_log touched the filesystem")

    monkeypatch.setattr(builtins, "open", _refuse)
    monkeypatch.setattr(io, "open", _refuse)
    monkeypatch.setattr(os, "open", _refuse)
    monkeypatch.setattr(os, "stat", _refuse)

    assert project_event_log(graph)


def test_module_keeps_no_parallel_store() -> None:
    tree = _tree()
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.Global, ast.Nonlocal)), "projection keeps no shared state"
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                assert name not in {"cache", "lru_cache", "cached_property"}, f"{node.name} caches results"
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None:
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if [getattr(target, "id", None) for target in targets] == ["__all__"]:
                continue  # the export list, not state
            value = statement.value
            assert not isinstance(value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)), (
                f"module-level mutable container at line {statement.lineno}"
            )
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                assert value.func.id not in {"list", "dict", "set", "defaultdict", "deque", "OrderedDict"}


def test_provenance_event_is_the_only_dataclass_declared() -> None:
    declared: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                if name == "dataclass":
                    declared.append(node.name)

    assert declared == ["ProvenanceEvent"]
    assert ProvenanceEvent.__dataclass_params__.frozen  # type: ignore[attr-defined]
