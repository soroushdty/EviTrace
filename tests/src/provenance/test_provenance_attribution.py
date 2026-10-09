"""Tests for the shared attribution rules owned by ``provenance.graph`` (task 4.9).

Covers (provenance-core design, "Assembly Layer → GraphBuilder → Shared
attribution rules (single owner)"):

- the public document-attribution functions (the source a node identifier
  belongs to, the documents a derivation step touches) and stage-attribution
  functions (own stage; else the earliest declared stage among non-in-place
  producing steps; else the sole contract stage for the node's kind; else
  ``"unattributed"``);
- behavioural agreement: for the same records, the stage a missing segment
  names (GraphBuilder), the stage of the event-log event (Projection) and the
  responsible stage of the completeness entry (CompletenessComputer) are the
  same — for a record produced by an extraction-chunk step and a synthesis
  step, for an in-place step's input, for a node with no producer but a sole
  contract stage, and for an unattributable node (2.3, 2.5, 7.4, 7.6);
- the graph builder's per-document stage segment and the completeness
  computation scope a step to the same documents (2.1, 2.5);
- an import guard: none of graph's consumers (``projection.py``,
  ``completeness.py``, ``validation.py``) imports the identifier-shape
  constants or parsers of ``provenance.identity`` or ``STAGE_ORDER``, nor
  parses ``src:`` / ``claim:`` / ``#`` with literals;
- a generated agreement test over many random runs recorded through the real
  recorder: the stage of every record that failed to materialize equals its
  event stage and its completeness responsible stage, and the completeness
  computation and the chain validator place every node in the document
  ``graph.document_of`` names.

Synthetic identifiers only.
"""

from __future__ import annotations

import ast
import dataclasses
import textwrap
import typing
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from provenance import completeness as completeness_module
from provenance import graph as graph_module
from provenance import identity as identity_module
from provenance import projection as projection_module
from provenance import validation as validation_module
from provenance.completeness import compute_completeness
from provenance.contracts import STAGE_CONTRACTS, STAGE_ORDER
from provenance.graph import build_graph
from provenance.identity import derive_claim_id, derive_source_id, format_evidence_id
from provenance.models import (
    UNKNOWN_PRIVACY,
    ClaimNode,
    DerivationStep,
    EvidenceNode,
    MissingSegment,
    ProvenanceEdge,
    ProvenanceNode,
    RecordedRun,
    RunIdentity,
    SourceAnchor,
    SourceNode,
    ValidationFinding,
    ValidationRecord,
)
from provenance.projection import project_event_log
from provenance.recorder import ProvenanceRecorder
from provenance.validation import validate_chain

_RUN = "run:0123456789abcdef"
_FP = "aaaabbbbccccdddd"
_FP2 = "eeeeffff00001111"
_SRC = derive_source_id(_FP)
_SRC2 = derive_source_id(_FP2)
_E1 = format_evidence_id(_SRC, "e1")
_CLAIM = derive_claim_id(_SRC, 7)

# Hand-picked step identifiers so the synthesis step sorts BEFORE the chunk step:
# the pre-4.9 graph rule ("first producer by node_id") would pick synthesis.
_SYNTHESIS_STEP_ID = "drv:0000000000000000"
_CHUNK_STEP_ID = "drv:ffffffffffffffff"


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


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


def _evidence(local_id: str = "e1", *, source_id: str = _SRC, precision: str = "exact") -> EvidenceNode:
    anchor = (
        SourceAnchor(precision="exact", page=1, coords=(1.0, 2.0, 3.0, 4.0), section_path="Methods")
        if precision == "exact"
        else SourceAnchor(precision="absent", missing=("page", "coords", "section_path", "structural_path"))
    )
    return EvidenceNode(
        node_id=format_evidence_id(source_id, local_id),
        kind="evidence",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        local_id=local_id,
        evidence_kind="sentence",
        source_id=source_id,
        anchor=anchor,
        producing_backend="grobid",
        rank_score=3,
        parser_metadata={},
    )


def _claim(cited: tuple[str, ...] = (), *, field_index: int = 7, source_id: str = _SRC) -> ClaimNode:
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
        support="supported" if cited else "unsupported",
        cited_evidence_ids=cited,
        confidence="high",
    )


def _step(
    node_id: str, inputs: tuple[str, ...], outputs: tuple[str, ...], *, stage: str, kind: str = "extract"
) -> DerivationStep:
    return DerivationStep(
        node_id=node_id,
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


def _cites(claims: typing.Iterable[ClaimNode]) -> tuple[ProvenanceEdge, ...]:
    return tuple(
        ProvenanceEdge(
            edge_id=f"cites_evidence:{c.node_id}->{t}", kind="cites_evidence", from_id=c.node_id, to_id=t, attributes={}
        )
        for c in claims
        for t in c.cited_evidence_ids
    )


def _recorded(
    nodes: typing.Iterable[object],
    *,
    edges: tuple[object, ...] = (),
    status: typing.Mapping[str, str] | None = None,
    doc_status: typing.Mapping[str, typing.Mapping[str, str]] | None = None,
    doc_reasons: typing.Mapping[str, typing.Mapping[str, tuple[str, ...]]] | None = None,
    fingerprints: tuple[str, ...] = (_FP,),
) -> RecordedRun:
    node_map = {getattr(node, "node_id"): node for node in nodes}
    full_status = {stage: "not_run" for stage in STAGE_ORDER}
    full_status.update(status or {"document_extraction": "complete", "evidence_index": "complete"})
    return RecordedRun(
        run=RunIdentity(
            run_id=_RUN,
            config_fingerprint="cfg123",
            schema_fingerprint="sch456",
            model_ids=("gpt-x",),
            document_fingerprints={derive_source_id(fp): fp for fp in fingerprints},
            created_at="2026-10-09T00:00:00+00:00",
        ),
        nodes=node_map,  # type: ignore[arg-type]
        edges=tuple(edges),  # type: ignore[arg-type]
        findings=(),
        stage_status=full_status,  # type: ignore[arg-type]
        stage_failure_reasons={},
        document_stage_status=dict(doc_status or {}),  # type: ignore[arg-type]
        document_stage_failure_reasons=dict(doc_reasons or {}),
    )


# ---------------------------------------------------------------------------
# Scenarios for the agreement tests
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Scenario:
    nodes: tuple[ProvenanceNode, ...]
    target: str  # the record whose stage the three consumers must agree on
    claim: str  # the claim whose completeness entry names the responsible stage
    expected: str


def _chunk_and_synthesis_producers() -> _Scenario:
    """(i) A claim output by an extraction-chunk step and a synthesis step; the synthesis step id sorts first."""
    claim = _claim()
    nodes = (
        _source(),
        _evidence(),
        claim,
        _step(_SYNTHESIS_STEP_ID, (_E1,), (claim.node_id,), stage="synthesis", kind="merge"),
        _step(_CHUNK_STEP_ID, (_E1,), (claim.node_id,), stage="extraction_chunk"),
    )
    return _Scenario(nodes, claim.node_id, claim.node_id, "extraction_chunk")


def _in_place_step_input() -> _Scenario:
    """(ii) A claim produced by a chunk step and then filtered in place by a synthesis step that sorts first."""
    claim = _claim()
    nodes = (
        _source(),
        _evidence(),
        claim,
        _step(_SYNTHESIS_STEP_ID, (claim.node_id,), (claim.node_id,), stage="synthesis", kind="filter"),
        _step(_CHUNK_STEP_ID, (_E1,), (claim.node_id,), stage="extraction_chunk"),
    )
    return _Scenario(nodes, claim.node_id, claim.node_id, "extraction_chunk")


def _only_in_place_step() -> _Scenario:
    """(ii') A claim whose only "producer" is an in-place step: nothing produced it, and no sole contract stage."""
    claim = _claim()
    nodes = (
        _source(),
        _evidence(),
        claim,
        _step(_SYNTHESIS_STEP_ID, (claim.node_id,), (claim.node_id,), stage="synthesis", kind="filter"),
    )
    return _Scenario(nodes, claim.node_id, claim.node_id, "unattributed")


def _sole_contract_stage() -> _Scenario:
    """(iii) Evidence no step produced (an in-place step touches it) takes the sole contract stage for its kind."""
    evidence = _evidence(precision="absent")
    claim = _claim((evidence.node_id,))
    nodes = (
        _source(),
        evidence,
        claim,
        _step(_SYNTHESIS_STEP_ID, (evidence.node_id,), (evidence.node_id,), stage="synthesis", kind="filter"),
        _step(_CHUNK_STEP_ID, (evidence.node_id,), (claim.node_id,), stage="extraction_chunk"),
    )
    return _Scenario(nodes, evidence.node_id, claim.node_id, "evidence_index")


def _unattributable() -> _Scenario:
    """(iv) A claim no step produced: three declared stages emit claims, so it is unattributed."""
    claim = _claim()
    return _Scenario((_source(), _evidence(), claim), claim.node_id, claim.node_id, "unattributed")


_SCENARIOS = {
    "chunk_and_synthesis_producers": _chunk_and_synthesis_producers,
    "in_place_step_input": _in_place_step_input,
    "only_in_place_step": _only_in_place_step,
    "sole_contract_stage": _sole_contract_stage,
    "unattributable": _unattributable,
}


def _unmaterializable(node: ProvenanceNode) -> ProvenanceNode:
    """The same record with one field outside its closed vocabulary, so the builder rejects it."""
    if isinstance(node, ClaimNode):
        return dataclasses.replace(node, support="maybe")  # type: ignore[arg-type]
    if isinstance(node, EvidenceNode):
        return dataclasses.replace(node, evidence_kind="hologram")  # type: ignore[arg-type]
    raise AssertionError(f"no unmaterializable variant for {type(node).__name__}")


def _segment_stage_for_rejected_record(scenario: _Scenario) -> str:
    nodes = [_unmaterializable(n) if n.node_id == scenario.target else n for n in scenario.nodes]
    graph = build_graph(_recorded(nodes))
    segments = [s for s in graph.missing_segments if scenario.target in s.affected_ids]
    assert len(segments) == 1, graph.missing_segments
    return segments[0].stage


def _segment_stage_for_rejected_edge(scenario: _Scenario) -> str:
    bad_edge = ProvenanceEdge(
        edge_id=f"likes:{scenario.target}->{_SRC}", kind="likes", from_id=scenario.target, to_id=_SRC, attributes={}  # type: ignore[arg-type]
    )
    graph = build_graph(_recorded(scenario.nodes, edges=(bad_edge,)))
    segments = [s for s in graph.missing_segments if s.record_kind == "edge"]
    assert len(segments) == 1, graph.missing_segments
    return segments[0].stage


def _event_stage(scenario: _Scenario) -> str:
    events = project_event_log(build_graph(_recorded(scenario.nodes)))
    return next(event.stage for event in events if event.node_id == scenario.target)


def _completeness_stages(scenario: _Scenario) -> tuple[str, ...]:
    claims = [n for n in scenario.nodes if isinstance(n, ClaimNode)]
    recorded = _recorded(scenario.nodes, edges=_cites(claims))
    entry = compute_completeness(build_graph(recorded), recorded).claims[scenario.claim]
    assert entry.state == "incomplete", entry
    return entry.responsible_stages


# ---------------------------------------------------------------------------
# Behavioural agreement: missing segment == event == completeness entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_missing_segment_event_and_completeness_name_the_same_stage(name: str) -> None:
    scenario = _SCENARIOS[name]()

    rejected_record = _segment_stage_for_rejected_record(scenario)
    rejected_edge = _segment_stage_for_rejected_edge(scenario)
    event = _event_stage(scenario)
    responsible = _completeness_stages(scenario)

    assert (rejected_record, rejected_edge, event, responsible) == (
        scenario.expected,
        scenario.expected,
        scenario.expected,
        (scenario.expected,),
    )


def test_the_synthesis_step_id_really_sorts_before_the_chunk_step_id() -> None:
    """Guards the premise of scenario (i): a first-by-id rule would pick synthesis."""
    assert _SYNTHESIS_STEP_ID < _CHUNK_STEP_ID


def test_per_document_segment_and_completeness_scope_a_step_to_the_same_documents() -> None:
    """A step whose output claim is not a node is scoped by the identifier's shape in both consumers."""
    unrecorded_claim = derive_claim_id(_SRC2, 4)
    claim = _claim((_E1,))
    own = _step(_CHUNK_STEP_ID, (_E1,), (claim.node_id,), stage="extraction_chunk")
    stray = _step(_SYNTHESIS_STEP_ID, (), (unrecorded_claim,), stage="extraction_chunk")
    nodes = (_source(), _source(_FP2), _evidence(), claim, own, stray)
    recorded = _recorded(
        nodes,
        edges=_cites([claim]),
        fingerprints=(_FP, _FP2),
        doc_status={_SRC2: {"extraction_chunk": "failed"}},
        doc_reasons={_SRC2: {"extraction_chunk": ("lorem outage",)}},
    )

    graph = build_graph(recorded)
    report = compute_completeness(graph, recorded)

    [segment] = [s for s in graph.missing_segments if s.record_kind == "stage"]
    assert segment.affected_ids == (_SRC2, stray.node_id)
    assert graph_module.step_documents(stray, graph.nodes) == {_SRC2}
    assert graph_module.step_documents(own, graph.nodes) == {_SRC}
    assert report.documents[_SRC2].state == "undetermined"
    assert report.claims[unrecorded_claim].state == "undetermined"
    assert report.documents[_SRC].state == "complete"
    assert report.claims[claim.node_id].state == "complete"


# ---------------------------------------------------------------------------
# The public functions themselves
# ---------------------------------------------------------------------------


def _nodes(*nodes: ProvenanceNode) -> dict[str, ProvenanceNode]:
    return {node.node_id: node for node in nodes}


def test_document_of_follows_the_node_then_the_identifier_shape() -> None:
    validation = ValidationRecord(
        node_id=f"val:{_RUN}:chain_validation",
        kind="validation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        check_name="chain_validation",
        finding_ids=(),
        completed=True,
    )
    step = _step(_CHUNK_STEP_ID, (_E1,), (_CLAIM,), stage="extraction_chunk")
    foreign = dataclasses.replace(_evidence("e9"), source_id=_SRC2)  # node field wins over the id shape
    nodes = _nodes(_source(), _evidence(), _claim(), step, validation, foreign)
    document_of = graph_module.document_of

    assert document_of(_SRC, nodes) == _SRC
    assert document_of(_E1, nodes) == _SRC
    assert document_of(_CLAIM, nodes) == _SRC
    assert document_of(foreign.node_id, nodes) == _SRC2
    assert document_of(step.node_id, nodes) is None
    assert document_of(validation.node_id, nodes) is None
    # Not a node: the src: scope of the identifier.
    assert document_of(format_evidence_id(_SRC2, "x"), nodes) == _SRC2
    assert document_of(derive_claim_id(_SRC2, 3, 2), nodes) == _SRC2
    assert document_of(_SRC2, nodes) == _SRC2
    for unscoped in ("src:", "claim:src:", "drv:abc", "lorem", "", "claim:lorem#f1"):
        assert document_of(unscoped, nodes) is None, unscoped


def test_step_documents_are_the_documents_of_its_inputs_and_outputs() -> None:
    step = _step(_CHUNK_STEP_ID, (_E1, "drv:other", 3), (derive_claim_id(_SRC2, 1),), stage="synthesis")  # type: ignore[arg-type]
    nodes = _nodes(_source(), _evidence(), step)

    assert graph_module.step_documents(step, nodes) == {_SRC, _SRC2}
    assert graph_module.step_documents(dataclasses.replace(step, input_ids=None, output_ids=()), nodes) == set()  # type: ignore[arg-type]


def test_producing_stages_exclude_in_place_steps() -> None:
    claim = _claim()
    nodes = _nodes(
        _step(_SYNTHESIS_STEP_ID, (claim.node_id,), (claim.node_id,), stage="synthesis", kind="filter"),
        _step(_CHUNK_STEP_ID, (_E1,), (claim.node_id, _E1, ""), stage="extraction_chunk"),  # _E1 is in-place here
        _step("drv:1", (), (claim.node_id,), stage="metadata_prefill"),
    )

    producers = graph_module.producing_stages(nodes)

    assert producers == {claim.node_id: frozenset({"extraction_chunk", "metadata_prefill"})}


def test_attribute_stage_applies_the_four_bases_in_order() -> None:
    claim = _claim()
    chunk = _step(_CHUNK_STEP_ID, (_E1,), (claim.node_id,), stage="extraction_chunk")
    synthesis = _step(_SYNTHESIS_STEP_ID, (_E1,), (claim.node_id,), stage="synthesis", kind="merge")
    nodes = _nodes(_source(), _evidence(), claim, chunk, synthesis)
    producers = graph_module.producing_stages(nodes)
    attribute_stage = graph_module.attribute_stage

    assert attribute_stage(synthesis, producers) == ("synthesis", "own")
    assert attribute_stage(claim, producers) == ("extraction_chunk", "producing_step")
    assert attribute_stage(claim.node_id, producers) == ("extraction_chunk", "producing_step")
    assert attribute_stage(nodes[_SRC], producers) == ("document_extraction", "stage_contract")
    assert attribute_stage(nodes[_E1], producers) == ("evidence_index", "stage_contract")
    assert attribute_stage(_claim(field_index=9), producers) == ("unattributed", "none")
    assert attribute_stage("lorem", producers) == ("unattributed", "none")
    # Records that did not materialize: the caller names the identifiers and the intended kind.
    assert attribute_stage({"junk": 1}, producers, identifiers=("x", claim.node_id), kind="node") == (
        "extraction_chunk",
        "producing_step",
    )
    assert attribute_stage(object(), producers, identifiers=(), kind="validation") == (
        "chain_validation",
        "stage_contract",
    )


def test_attribute_stage_honours_a_custom_contract_table() -> None:
    from provenance.models import StageContract

    contracts = {"only": StageContract(stage="only", required_record_kinds=("claim",), scope="claim")}

    assert graph_module.attribute_stage(_claim(), {}, contracts=contracts) == ("only", "stage_contract")
    assert graph_module.sole_contract_stage("claim", contracts) == "only"
    assert graph_module.sole_contract_stage("claim") is None  # three declared stages emit claims
    assert graph_module.sole_contract_stage("source") == "document_extraction"


def test_stage_sort_key_orders_declared_then_undeclared_then_unattributed() -> None:
    stages = ["unattributed", "zeta", "synthesis", "alpha", "document_extraction"]

    assert sorted(stages, key=graph_module.stage_sort_key) == [
        "document_extraction",
        "synthesis",
        "alpha",
        "zeta",
        "unattributed",
    ]


def test_stage_segment_document_recognizes_only_per_document_stage_segments() -> None:
    def segment(record_kind: str, *ids: str) -> MissingSegment:
        return MissingSegment(stage="synthesis", record_kind=record_kind, reason="r", affected_ids=ids)

    recognize = graph_module.stage_segment_document

    assert recognize(segment("stage", _SRC, "drv:1")) == _SRC
    assert recognize(segment("stage", "drv:1", "drv:2")) is None
    assert recognize(segment("stage")) is None
    assert recognize(segment("stage", _E1)) is None
    assert recognize(segment("claim", _SRC)) is None


def test_is_claim_identifier_uses_the_node_then_the_identifier_shape() -> None:
    nodes = _nodes(_source(), _evidence(), _claim())

    assert graph_module.is_claim_identifier(_CLAIM, nodes)
    assert graph_module.is_claim_identifier(derive_claim_id(_SRC2, 1), nodes)
    assert not graph_module.is_claim_identifier(_E1, nodes)
    assert not graph_module.is_claim_identifier(_SRC, nodes)
    assert not graph_module.is_claim_identifier("drv:1", nodes)


def test_the_shared_functions_are_public() -> None:
    for name in (
        "UNATTRIBUTED_STAGE",
        "document_of",
        "step_documents",
        "stage_segment_document",
        "is_claim_identifier",
        "producing_stages",
        "sole_contract_stage",
        "attribute_stage",
        "stage_sort_key",
    ):
        assert name in graph_module.__all__, name
    assert projection_module.UNATTRIBUTED_STAGE == graph_module.UNATTRIBUTED_STAGE == "unattributed"


# ---------------------------------------------------------------------------
# Import guard: graph's consumers cannot interpret identifiers or order stages themselves
# ---------------------------------------------------------------------------
#
# Document attribution needs the identifier shape (the ``src:`` / ``claim:``
# prefixes, the ``#`` scope separator, the ``parse_*`` parsers); stage
# attribution needs the declared stage order. The guard denies both to the
# consumers, so neither rule can be re-implemented there; the generated
# agreement test below catches a copy that would need neither.

_SHAPE_NAMES = frozenset(
    name
    for name in dir(identity_module)
    if name.endswith(("_PREFIX", "_SEPARATOR")) or name.startswith("parse_")
)
_CONTRACT_NAMES = frozenset({"STAGE_ORDER"})
_GUARDED_MODULES = {"provenance.identity": _SHAPE_NAMES, "provenance.contracts": _CONTRACT_NAMES}
_SHAPE_LITERALS = ("src:", "claim:")
_SEPARATOR_METHODS = frozenset(
    {"split", "rsplit", "partition", "rpartition", "startswith", "endswith", "index", "rindex", "find", "rfind", "count"}
)


def _shape_violations(source: str) -> list[str]:
    """Why ``source`` could re-implement document or stage attribution; empty when it cannot."""
    tree = ast.parse(textwrap.dedent(source))
    banned = _SHAPE_NAMES | _CONTRACT_NAMES
    problems: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.ImportFrom) and node.module in _GUARDED_MODULES:
            problems.extend(
                f"line {line}: imports {alias.name} from {node.module}"
                for alias in node.names
                if alias.name == "*" or alias.name in _GUARDED_MODULES[node.module]
            )
        if isinstance(node, ast.ImportFrom) and node.module == "provenance":
            problems.extend(
                f"line {line}: imports the module provenance.{alias.name}"
                for alias in node.names
                if f"provenance.{alias.name}" in _GUARDED_MODULES
            )
        if isinstance(node, ast.Import):
            problems.extend(
                f"line {line}: imports the module {alias.name}" for alias in node.names if alias.name in _GUARDED_MODULES
            )
        if isinstance(node, ast.Name) and node.id in banned:
            problems.append(f"line {line}: reads {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in banned:
            problems.append(f"line {line}: reads .{node.attr}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(_SHAPE_LITERALS):
            problems.append(f"line {line}: identifier-shape literal {node.value!r}")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SEPARATOR_METHODS
            and any(isinstance(arg, ast.Constant) and arg.value == "#" for arg in node.args)
        ):
            problems.append(f"line {line}: splits on the scope separator with .{node.func.attr}('#')")
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and node.left.value == "#"
            and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
        ):
            problems.append(f"line {line}: tests for the scope separator with '#' in ...")
    return problems


_CONSUMERS = {
    "projection": Path(projection_module.__file__),
    "completeness": Path(completeness_module.__file__),
    "validation": Path(validation_module.__file__),
}


def test_the_guard_bans_the_identity_shape_names_that_exist() -> None:
    """The banned set is derived from provenance.identity, so a new prefix or parser is banned automatically."""
    assert {"SOURCE_ID_PREFIX", "CLAIM_ID_PREFIX", "EVIDENCE_SCOPE_SEPARATOR", "parse_evidence_id"} <= _SHAPE_NAMES


@pytest.mark.parametrize("module", sorted(_CONSUMERS))
def test_consumers_cannot_interpret_identifiers_or_order_stages(module: str) -> None:
    problems = _shape_violations(_CONSUMERS[module].read_text(encoding="utf-8"))

    assert problems == [], f"{module}.py bypasses the shared rules owned by provenance.graph: {problems}"


@pytest.mark.parametrize(
    ("module", "required"),
    [
        ("projection", {"attribute_stage", "producing_stages", "stage_sort_key"}),
        (
            "completeness",
            {"attribute_stage", "producing_stages", "document_of", "step_documents", "stage_sort_key"},
        ),
        ("validation", {"document_of", "is_claim_identifier", "evidence_id_parts"}),
    ],
)
def test_consumers_call_the_shared_functions(module: str, required: set[str]) -> None:
    tree = ast.parse(_CONSUMERS[module].read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "provenance.graph"
        for alias in node.names
    }
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}

    assert required <= imported, sorted(required - imported)
    assert required <= used, sorted(required - used)


# The pre-4.9 private copies, with the imports they needed: the guard must flag each one.
_PRIVATE_COPIES = {
    "completeness _doc_from_identifier": """
        from provenance.identity import CLAIM_ID_PREFIX, EVIDENCE_SCOPE_SEPARATOR, SOURCE_ID_PREFIX

        def _doc_from_identifier(identifier):
            text = identifier[len(CLAIM_ID_PREFIX):] if identifier.startswith(CLAIM_ID_PREFIX) else identifier
            if not text.startswith(SOURCE_ID_PREFIX):
                return None
            source = text.partition(EVIDENCE_SCOPE_SEPARATOR)[0]
            return source if len(source) > len(SOURCE_ID_PREFIX) else None
    """,
    "completeness segment_document": """
        from provenance.identity import EVIDENCE_SCOPE_SEPARATOR, SOURCE_ID_PREFIX

        def segment_document(segment):
            ids = segment.affected_ids
            if ids and ids[0].startswith(SOURCE_ID_PREFIX) and EVIDENCE_SCOPE_SEPARATOR not in ids[0]:
                return ids[0]
    """,
    "validation _claim_source": """
        from provenance.identity import CLAIM_ID_PREFIX, EVIDENCE_SCOPE_SEPARATOR

        def _claim_source(node_id):
            if not node_id.startswith(CLAIM_ID_PREFIX):
                return None
            source_id, separator, _ = node_id[len(CLAIM_ID_PREFIX):].partition(EVIDENCE_SCOPE_SEPARATOR)
            return source_id if separator and source_id else None
    """,
    "validation ambiguity parsing": """
        from provenance.identity import parse_evidence_id

        def candidates(reference):
            source_id, local_id = parse_evidence_id(reference)
    """,
    "projection _stage_position": """
        from provenance.contracts import STAGE_CONTRACTS, STAGE_ORDER

        def _stage_position(stage):
            return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else len(STAGE_ORDER)
    """,
    "module import, attribute access": """
        from provenance import identity

        def doc(identifier):
            return identifier.partition(identity.EVIDENCE_SCOPE_SEPARATOR)[0]
    """,
    "dotted module import": """
        import provenance.contracts

        def key(stage):
            return provenance.contracts.STAGE_ORDER.index(stage)
    """,
    "literal prefix": """
        def doc(identifier):
            return identifier.removeprefix("claim:")
    """,
    "literal separator split": """
        def doc(identifier):
            return identifier.partition("#")[0]
    """,
    "literal separator membership": """
        def is_local(reference):
            return "#" not in reference
    """,
}

# Legitimate code the consumers do contain: the guard must not flag it.
_BENIGN = {
    "a function reading both input_ids and output_ids": """
        def touches(step):
            return (*step.input_ids, *step.output_ids)
    """,
    "a .source_id read": """
        def names_no_source(node):
            return not (isinstance(node.source_id, str) and node.source_id)
    """,
    "names containing stage": """
        def check(steps, stage):
            for output_id in steps[0].output_ids:
                stage_ids = {s.node_id for s in steps if s.stage == stage}
    """,
    "identity constructors and run-id prefix-free code": """
        from provenance.identity import derive_source_id, derive_validation_id, format_evidence_id
        from provenance.contracts import STAGE_CONTRACTS

        def ids(fp, local):
            return format_evidence_id(derive_source_id(fp), local)
    """,
    "a local separator constant": """
        _REASON_SEPARATOR = "; "
        def join(reasons):
            return _REASON_SEPARATOR.join(reasons)
    """,
}


@pytest.mark.parametrize("case", sorted(_PRIVATE_COPIES))
def test_guard_flags_a_private_copy_of_either_rule(case: str) -> None:
    assert _shape_violations(_PRIVATE_COPIES[case]), case


@pytest.mark.parametrize("case", sorted(_BENIGN))
def test_guard_does_not_flag_legitimate_consumer_code(case: str) -> None:
    assert _shape_violations(_BENIGN[case]) == [], case


# ---------------------------------------------------------------------------
# Generated agreement: random runs through the real recorder (2.3, 2.5, 7.4, 7.6)
# ---------------------------------------------------------------------------

_GEN_FPS = ("aaaa1111", "bbbb2222", "cccc3333")
_STATUSES = ("not_run", "complete", "complete", "failed")


@dataclasses.dataclass(frozen=True)
class _RunSpec:
    """One generated run: its records, the ids to corrupt and the stage marks."""

    fingerprints: tuple[str, ...]
    nodes: tuple[ProvenanceNode, ...]
    corrupted: frozenset[str]
    run_marks: tuple[tuple[str, str], ...]
    document_marks: tuple[tuple[str, str, str], ...]


@st.composite
def _run_specs(draw: st.DrawFn) -> _RunSpec:
    fingerprints = _GEN_FPS[: draw(st.integers(1, 3))]
    sources = [derive_source_id(fp) for fp in fingerprints]
    nodes: list[ProvenanceNode] = [_source(fp) for fp in fingerprints if draw(st.integers(0, 9)) > 0]

    evidence: list[EvidenceNode] = []
    for source_id in sources:
        for index in range(draw(st.integers(0, 3))):
            node = _evidence(f"e{index}", source_id=source_id, precision=draw(st.sampled_from(("exact", "absent"))))
            if draw(st.integers(0, 9)) == 0:  # the record's own field disagrees with its identifier
                node = dataclasses.replace(node, source_id=draw(st.sampled_from(sources)))
            evidence.append(node)
    citable = [node.node_id for node in evidence] + [format_evidence_id(sources[0], "unrecorded")]
    claims = [
        _claim(tuple(draw(st.lists(st.sampled_from(citable), max_size=3, unique=True))), field_index=index + 3, source_id=source_id)
        for source_id in sources
        for index in range(draw(st.integers(0, 3)))
    ]
    nodes += [*evidence, *claims]

    pool = [node.node_id for node in nodes] + [derive_claim_id(sources[-1], 40), format_evidence_id(sources[-1], "zz")]
    step_count = draw(st.integers(0, 6))
    ranks = draw(st.permutations(range(step_count)))
    for rank in ranks:
        inputs = draw(st.lists(st.sampled_from(pool), max_size=3, unique=True))
        outputs = draw(st.lists(st.sampled_from(pool), max_size=3, unique=True))
        if inputs and draw(st.booleans()):
            outputs = [*dict.fromkeys([*outputs, inputs[0]])]  # an in-place transform of its first input
        nodes.append(
            _step(
                f"drv:{rank:016x}",
                tuple(inputs),
                tuple(outputs),
                stage=draw(st.sampled_from(STAGE_ORDER)),
                kind=draw(st.sampled_from(("extract", "merge", "filter"))),
            )
        )

    corruptible = [node.node_id for node in nodes if isinstance(node, (ClaimNode, EvidenceNode))]
    corrupted = frozenset(draw(st.lists(st.sampled_from(corruptible), max_size=3, unique=True))) if corruptible else frozenset()
    run_marks = tuple((stage, draw(st.sampled_from(_STATUSES))) for stage in STAGE_ORDER)
    document_marks = tuple(
        (source_id, draw(st.sampled_from(STAGE_ORDER[:6])), draw(st.sampled_from(("complete", "failed"))))
        for source_id in sources
        if draw(st.booleans())
    )
    return _RunSpec(fingerprints, tuple(nodes), corrupted, run_marks, document_marks)


def _record_run(spec: _RunSpec, *, corrupt: bool) -> RecordedRun:
    """Feed ``spec`` through the real recorder, corrupting ``spec.corrupted`` when asked."""
    recorder = ProvenanceRecorder(
        RunIdentity(
            run_id=_RUN,
            config_fingerprint="cfg123",
            schema_fingerprint="sch456",
            model_ids=("gpt-x",),
            document_fingerprints={derive_source_id(fp): fp for fp in spec.fingerprints},
            created_at="2026-10-09T00:00:00+00:00",
        )
    )
    for node in spec.nodes:
        if corrupt and node.node_id in spec.corrupted:
            node = _unmaterializable(node)
        if isinstance(node, SourceNode):
            recorder.record_source(node)
        elif isinstance(node, EvidenceNode):
            recorder.record_evidence([node])
        elif isinstance(node, ClaimNode):
            recorder.record_claim(node, list(_cites([node])))
        else:
            recorder.record_derivation(node)  # type: ignore[arg-type]
    for stage, status in spec.run_marks:
        if status == "complete":
            recorder.mark_stage_complete(stage)
        elif status == "failed":
            recorder.mark_stage_failed(stage, f"lorem {stage}")
    for source_id, stage, status in spec.document_marks:
        if status == "complete":
            recorder.mark_stage_complete(stage, source_id=source_id)
        else:
            recorder.mark_stage_failed(stage, f"ipsum {stage}", source_id=source_id)
    return recorder.snapshot()


def _analysis(graph: graph_module.ProvenanceGraph, recorded: RecordedRun) -> typing.Any:
    return completeness_module._Analysis(graph, recorded, None, STAGE_CONTRACTS, True)


def _referenced_ids(graph: graph_module.ProvenanceGraph) -> set[str]:
    ids = set(graph.nodes)
    for node in graph.nodes.values():
        if isinstance(node, DerivationStep):
            ids.update(node.input_ids, node.output_ids)
        elif isinstance(node, ClaimNode):
            ids.update(node.cited_evidence_ids)
    ids.update(edge.to_id for edge in graph.edges)
    return ids


@settings(max_examples=200, derandomize=True, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_run_specs())
def test_generated_runs_agree_on_stage_and_document(spec: _RunSpec) -> None:
    clean_recorded = _record_run(spec, corrupt=False)
    broken_recorded = _record_run(spec, corrupt=True)
    clean = build_graph(clean_recorded)
    broken = build_graph(broken_recorded)

    # Every record that failed to materialize: segment stage == event stage == completeness responsible stage.
    clean_events = {event.node_id: event.stage for event in project_event_log(clean)}
    clean_analysis = _analysis(clean, clean_recorded)
    rejected = {}
    for segment in broken.missing_segments:
        if segment.record_kind in ("claim", "evidence"):
            [node_id] = segment.affected_ids
            rejected[node_id] = segment.stage
    assert set(rejected) == set(spec.corrupted)
    for node_id, stage in rejected.items():
        assert stage == clean_events[node_id] == clean_analysis.responsible(node_id), node_id

    for graph, recorded in ((clean, clean_recorded), (broken, broken_recorded)):
        analysis = _analysis(graph, recorded)
        # The event log and completeness name the same stage for every node of one graph.
        for event in project_event_log(graph):
            assert event.stage == analysis.responsible(event.node_id), event.node_id
        # Completeness places every identifier in the document graph.document_of names.
        for identifier in _referenced_ids(graph):
            assert analysis.doc_of(identifier) == graph_module.document_of(identifier, graph.nodes), identifier

        # The validator places claims, evidence and their references by the same rule.
        findings = validate_chain(graph).findings
        foreign = {
            (f.node_id, f.details["reference"])
            for f in findings
            if f.code == "evidence_out_of_run_context" and "foreign_source" in f.details.get("reasons", ())
        }
        expected_foreign = {
            (claim.node_id, target)
            for claim in graph.nodes_of_kind("claim")
            for target in {*claim.cited_evidence_ids, *(e.to_id for e in graph.edges_from(claim.node_id) if e.kind == "cites_evidence")}  # type: ignore[attr-defined]
            if isinstance(graph.nodes.get(target), EvidenceNode)
            and graph_module.document_of(target, graph.nodes) != graph_module.document_of(claim.node_id, graph.nodes)
        }
        assert foreign == expected_foreign
        unrepresented = {
            (f.node_id, f.details["reference"])
            for f in findings
            if f.code == "source_not_represented" and f.details.get("reference") is not None
        }
        expected_unrepresented = {
            (node.node_id, graph_module.document_of(node.node_id, graph.nodes))
            for node in graph.nodes.values()
            if isinstance(node, (ClaimNode, EvidenceNode))
            and not isinstance(graph.nodes.get(graph_module.document_of(node.node_id, graph.nodes) or ""), SourceNode)
        }
        assert unrepresented == expected_unrepresented


# ---------------------------------------------------------------------------
# The validator takes documents from the shared rule
# ---------------------------------------------------------------------------


def test_evidence_id_parts_is_the_never_raising_evidence_parser() -> None:
    assert graph_module.evidence_id_parts(_E1) == (_SRC, "e1")
    for not_evidence in (_SRC, _CLAIM, "src:#x", "lorem#x", "", None, f"{_SRC}#"):
        assert graph_module.evidence_id_parts(not_evidence) is None, not_evidence  # type: ignore[arg-type]


def test_recorded_finding_merge_scopes_local_references_by_the_shared_document_rule() -> None:
    """A sink finding (local reference) and an adapter finding (full id) about one claim merge into one."""
    claim_id = "claim:src:abc"  # no '#': graph.document_of still names src:abc
    graph = build_graph(_recorded([_source()]))

    def finding(finding_id: str, reference: str) -> ValidationFinding:
        return ValidationFinding(
            finding_id=finding_id,
            code="dangling_reference",
            severity="critical",
            node_id=claim_id,
            edge_id=None,
            message="lorem",
            details={"reference": reference},
        )

    assert graph_module.document_of(claim_id, graph.nodes) == "src:abc"
    report = validate_chain(
        graph, recorded_findings=(finding("f:1", "e1"), finding("f:2", format_evidence_id("src:abc", "e1")))
    )

    merged = [f for f in report.findings if f.node_id == claim_id]
    assert [f.finding_id for f in merged] == ["f:1"]
    assert merged[0].details["merged_finding_ids"] == ["f:2"]
