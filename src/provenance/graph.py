"""graph — the typed provenance graph and its assembly from a recorder snapshot.

:func:`build_graph` (provenance-core design, "Assembly Layer → GraphBuilder")
turns an immutable :class:`~provenance.models.RecordedRun` into a
:class:`ProvenanceGraph` carrying the schema identity and version (7.5) and the
run identity. Every node and every edge carries an explicit kind from the
closed vocabularies in :mod:`provenance.models` (7.1), and the five node types
stay distinct classes (7.2).

Partial preservation (1.6, 7.4)
    Records are materialized one at a time, each inside its own guard. Model
    construction does not check vocabulary membership, node kind/class
    agreement or a non-empty ``node_id``, so this module does: a record that
    fails any check — or is not a record at all — becomes one
    :class:`~provenance.models.MissingSegment` naming the stage, the record
    kind, a non-empty reason and the affected identifiers, and the rest of the
    graph is still produced. The segment's stage is :func:`attribute_stage`
    of the record (an edge takes the stage of the materialized node it
    leaves), so it matches the event log and the completeness entry. A stage
    the snapshot marks ``failed`` is reported as a further segment
    (``record_kind="stage"``) carrying the recorded failure reason (2.5): a
    run-wide failure yields one segment whose ``affected_ids`` are all of
    that stage's derivation-step IDs; a failure marked for one document
    yields one segment per ``(stage, source_id)`` whose ``affected_ids`` are
    the source ID followed by that document's steps of the stage (a step belongs to the documents its input and output
    identifiers belong to) and whose reason lists every recorded reason. A
    stage that emitted nothing simply contributes no nodes.
    :func:`build_graph` never raises for a ``RecordedRun``.

Edges
    The graph holds the edges recorded with claims (``cites_evidence``,
    ``revises``) plus the structural edges implied by the records' own
    references: ``belongs_to_source`` from each evidence and claim node to its
    ``source_id``, and ``has_input`` / ``has_output`` from each derivation step
    to its input and output identifiers. Edge IDs follow ``<kind>:<from>-><to>``
    and are de-duplicated by key. An edge may point at an identifier that is
    not a node; the chain validator reports it as a dangling reference.

Determinism
    Snapshot order is insertion order across concurrent tasks, so nodes are
    keyed in ``node_id`` order, edges in ``edge_id`` order and missing segments
    in ``(stage, record_kind, affected_ids, reason)`` order. Equal snapshots
    yield equal graphs whatever their insertion order.

Attachments (8.7)
    :func:`attach_validation` and :func:`attach_completeness` — owned here, not
    by serialization — return a new graph carrying the report, so the
    serialized artifact is a pure function of the graph alone.
    ``attach_validation`` also adds one :class:`~provenance.models.ValidationRecord`
    (``val:<run_id>:chain_validation``) and a ``validates`` edge to every
    finding subject present in the graph. Both are non-mutating and
    idempotent: attaching the same report twice yields an equal graph, and
    attaching a different report replaces the earlier one.

Shared attribution rules (single owner)
    This module owns the only implementations of the two attribution rules;
    :func:`build_graph`, :mod:`provenance.projection`,
    :mod:`provenance.completeness` and :mod:`provenance.validation` call
    these public functions and keep no copy. An import guard (no identifier
    shape constant or parser, no ``STAGE_ORDER`` in those modules) and a
    generated agreement test enforce it:

    - **document attribution** — :func:`document_of` (the source ID an
      identifier belongs to: a source node is its own document; an evidence or
      claim node's ``source_id``, else the ``src:`` scope of its identifier;
      derivation steps and validation records belong to no single document),
      :func:`step_documents` (the documents a derivation step's input and
      output identifiers belong to), :func:`stage_segment_document` (the
      document a per-document ``record_kind="stage"`` segment names first),
      :func:`is_claim_identifier` and :func:`evidence_id_parts` (the source
      and local part of an evidence identifier). :mod:`provenance.projection`,
      :mod:`provenance.completeness` and :mod:`provenance.validation` never
      interpret an identifier's shape themselves;
    - **stage attribution** — :func:`attribute_stage` returns ``(stage,
      basis)``: ``own`` (a derivation step's own ``stage``); else
      ``producing_step`` (the earliest stage, by :func:`stage_sort_key`, among
      the derivation steps that list the node in ``output_ids`` — a step that
      also lists it in ``input_ids`` is an in-place transform and does not
      produce it; see :func:`producing_stages`); else ``stage_contract`` (the
      sole declared stage whose contract requires the node's kind, see
      :func:`sole_contract_stage`); else ``none`` with stage
      :data:`UNATTRIBUTED_STAGE`.

Evidence text is never copied onto claim nodes; the graph holds references
only (1.3). Imports only the standard library, ``provenance.models``,
``provenance.identity``, ``provenance.contracts`` and ``utils.logging_utils``.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from provenance.contracts import STAGE_CONTRACTS, STAGE_ORDER
from provenance.identity import (
    CLAIM_ID_PREFIX,
    EVIDENCE_SCOPE_SEPARATOR,
    SOURCE_ID_PREFIX,
    InvalidIdentifierError,
    derive_validation_id,
    parse_evidence_id,
)
from provenance.models import (
    UNKNOWN_PRIVACY,
    AnchorPrecision,
    ChainValidationReport,
    ClaimNode,
    ClaimOrigin,
    ClaimSupport,
    CompletenessReport,
    DerivationKind,
    DerivationStep,
    Determinism,
    EdgeKind,
    EvidenceKind,
    EvidenceNode,
    MissingSegment,
    NodeKind,
    PrivacyCarrier,
    ProvenanceEdge,
    ProvenanceNode,
    RecordedRun,
    RunIdentity,
    SourceAnchor,
    SourceNode,
    StageContract,
    ValidationRecord,
    _Record,  # the shared mapping-freezing mixin; ProvenanceGraph is a provenance dataclass too
)
from utils.logging_utils import get_logger

__all__ = [
    "PROVENANCE_SCHEMA_ID",
    "PROVENANCE_SCHEMA_VERSION",
    "VALIDATION_CHECK_NAME",
    "UNATTRIBUTED_STAGE",
    "ProvenanceGraph",
    "build_graph",
    "attach_validation",
    "attach_completeness",
    "document_of",
    "step_documents",
    "stage_segment_document",
    "is_claim_identifier",
    "evidence_id_parts",
    "stage_sort_key",
    "producing_stages",
    "sole_contract_stage",
    "attribute_stage",
]

logger = get_logger(__name__)

PROVENANCE_SCHEMA_ID = "evitrace/provenance-graph"
"""Schema identity carried by every graph and its serialized artifact (7.5)."""

PROVENANCE_SCHEMA_VERSION = "1.0.0"
"""SemVer schema version; readers accept a matching major version (7.5, 10.5, 10.6)."""

VALIDATION_CHECK_NAME = "chain_validation"
"""``check_name`` of the validation record :func:`attach_validation` adds."""

UNATTRIBUTED_STAGE = "unattributed"
"""Stage of a record no derivation step produced and no single stage contract claims.

Not a declared stage."""

_STAGE_RECORD_KIND = "stage"
_EDGE_RECORD_KIND = "edge"
_NODE_RECORD_KIND = "node"
_REASON_LIMIT = 200

_NODE_KINDS: frozenset[str] = frozenset(typing.get_args(NodeKind))
_EDGE_KINDS: frozenset[str] = frozenset(typing.get_args(EdgeKind))
_EVIDENCE_KINDS: frozenset[str] = frozenset(typing.get_args(EvidenceKind))
_PRECISIONS: frozenset[str] = frozenset(typing.get_args(AnchorPrecision))
_CLAIM_SUPPORTS: frozenset[str] = frozenset(typing.get_args(ClaimSupport))
_CLAIM_ORIGINS: frozenset[str] = frozenset(typing.get_args(ClaimOrigin))
_DERIVATION_KINDS: frozenset[str] = frozenset(typing.get_args(DerivationKind))
_DETERMINISMS: frozenset[str] = frozenset(typing.get_args(Determinism))

_CLASS_KINDS: Mapping[type, str] = {
    SourceNode: "source",
    EvidenceNode: "evidence",
    ClaimNode: "claim",
    DerivationStep: "derivation",
    ValidationRecord: "validation",
}
"""The node kind each concrete node class must declare; nothing else materializes."""


class _Unmaterializable(Exception):
    """A record failed a materialization check; the message is the segment reason."""


# ---------------------------------------------------------------------------
# The graph container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceGraph(_Record):
    """A typed, schema-versioned provenance graph for one run (7.1–7.5).

    ``nodes`` is keyed by ``node_id``. ``validation`` and ``completeness`` hold
    the reports attached by :func:`attach_validation` and
    :func:`attach_completeness`, so serializing the graph needs nothing else.
    The lookups below never raise; an unknown or malformed argument yields an
    empty tuple.
    """

    schema_id: str
    schema_version: str
    run: RunIdentity
    nodes: Mapping[str, ProvenanceNode]
    edges: tuple[ProvenanceEdge, ...]
    missing_segments: tuple[MissingSegment, ...]
    validation: ChainValidationReport | None = None
    completeness: CompletenessReport | None = None

    def nodes_of_kind(self, kind: NodeKind) -> tuple[ProvenanceNode, ...]:
        """Every node of ``kind``, in ``node_id`` order."""
        if not isinstance(kind, str):
            return ()
        return tuple(node for node in self.nodes.values() if node.kind == kind)

    def edges_from(self, node_id: str) -> tuple[ProvenanceEdge, ...]:
        """Every edge leaving ``node_id``, in ``edge_id`` order."""
        if not isinstance(node_id, str):
            return ()
        return tuple(edge for edge in self.edges if edge.from_id == node_id)

    def path_from_claim(self, claim_id: str) -> tuple[ProvenanceNode, ...]:
        """The claim → evidence → source chain of ``claim_id`` (1.4).

        Returns the claim, then each evidence node it cites (``cites_evidence``
        edges, ``node_id`` order), then each distinct source those evidence
        nodes belong to (``belongs_to_source`` edges, ``node_id`` order).
        Targets that are not nodes of the expected kind are skipped. A
        ``claim_id`` that is not a claim node yields ``()``.
        """
        if not isinstance(claim_id, str):
            return ()
        claim = self.nodes.get(claim_id)
        if claim is None or claim.kind != "claim":
            return ()
        evidence = self._targets(claim_id, "cites_evidence", "evidence")
        source_ids = {
            source.node_id
            for node in evidence
            for source in self._targets(node.node_id, "belongs_to_source", "source")
        }
        sources = tuple(self.nodes[source_id] for source_id in sorted(source_ids))
        return (claim, *evidence, *sources)

    def _targets(self, from_id: str, edge_kind: str, node_kind: str) -> tuple[ProvenanceNode, ...]:
        target_ids = sorted({edge.to_id for edge in self.edges_from(from_id) if edge.kind == edge_kind})
        found = (self.nodes.get(target_id) for target_id in target_ids)
        return tuple(node for node in found if node is not None and node.kind == node_kind)


# ---------------------------------------------------------------------------
# Materialization checks (vocabulary, kind/class agreement, non-empty IDs)
# ---------------------------------------------------------------------------


def _short_repr(value: object) -> str:
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 — a broken __repr__ must not make assembly raise
        text = f"<unrepresentable {type(value).__name__}>"
    return text if len(text) <= _REASON_LIMIT else text[: _REASON_LIMIT - 3] + "..."


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _require_member(value: object, allowed: frozenset[str], what: str) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise _Unmaterializable(f"{what} {_short_repr(value)} is not one of {sorted(allowed)}")


def _require_text(value: object, what: str) -> None:
    if not _non_empty_str(value):
        raise _Unmaterializable(f"{what} must be a non-empty string, got {_short_repr(value)}")


def _check_node(key: object, node: object) -> ProvenanceNode:
    """Return *node* when it can be materialized under *key*, else raise ``_Unmaterializable``."""
    if not isinstance(node, ProvenanceNode):
        raise _Unmaterializable(f"not a provenance node: {type(node).__name__}")
    expected_kind = _CLASS_KINDS.get(type(node))
    if expected_kind is None:
        raise _Unmaterializable(f"{type(node).__name__} is not a concrete provenance node type")
    _require_text(node.node_id, "node_id")
    if key != node.node_id:
        raise _Unmaterializable(f"snapshot key {_short_repr(key)} does not match node_id {node.node_id!r}")
    _require_member(node.kind, _NODE_KINDS, "node kind")
    if node.kind != expected_kind:
        raise _Unmaterializable(
            f"node kind {node.kind!r} disagrees with its type {type(node).__name__} (expected {expected_kind!r})"
        )
    if not isinstance(node.run_id, str):
        raise _Unmaterializable(f"run_id must be a string, got {_short_repr(node.run_id)}")
    if not isinstance(node.privacy, PrivacyCarrier):
        raise _Unmaterializable(f"privacy must be a PrivacyCarrier, got {type(node.privacy).__name__}")
    if not isinstance(node.extensions, Mapping):
        raise _Unmaterializable(f"extensions must be a mapping, got {type(node.extensions).__name__}")
    if isinstance(node, EvidenceNode):
        _require_member(node.evidence_kind, _EVIDENCE_KINDS, "evidence kind")
        if not isinstance(node.anchor, SourceAnchor):
            raise _Unmaterializable(f"anchor must be a SourceAnchor, got {type(node.anchor).__name__}")
        _require_member(node.anchor.precision, _PRECISIONS, "anchor precision")
    elif isinstance(node, ClaimNode):
        _require_member(node.support, _CLAIM_SUPPORTS, "claim support")
        _require_member(node.origin, _CLAIM_ORIGINS, "claim origin")
    elif isinstance(node, DerivationStep):
        _require_text(node.stage, "stage")
        _require_member(node.derivation_kind, _DERIVATION_KINDS, "derivation kind")
        _require_member(node.determinism, _DETERMINISMS, "determinism")
    return node


def _check_edge(edge: object) -> ProvenanceEdge:
    """Return *edge* when it can be materialized, else raise ``_Unmaterializable``."""
    if not isinstance(edge, ProvenanceEdge):
        raise _Unmaterializable(f"not a provenance edge: {type(edge).__name__}")
    _require_text(edge.edge_id, "edge_id")
    _require_member(edge.kind, _EDGE_KINDS, "edge kind")
    _require_text(edge.from_id, "from_id")
    _require_text(edge.to_id, "to_id")
    if not isinstance(edge.attributes, Mapping):
        raise _Unmaterializable(f"attributes must be a mapping, got {type(edge.attributes).__name__}")
    return edge


def _reason(exc: BaseException) -> str:
    text = str(exc).strip()
    return text if text else type(exc).__name__


# ---------------------------------------------------------------------------
# Shared attribution rules (single owner; see the module docstring)
# ---------------------------------------------------------------------------


def _identifiers(value: object) -> tuple[str, ...]:
    """The non-empty string identifiers of an ``input_ids`` / ``output_ids`` value."""
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(item for item in value if _non_empty_str(item))


def _scope_of(identifier: str) -> str | None:
    """The ``src:`` scope of a source, evidence or claim identifier, if it has one."""
    text = identifier[len(CLAIM_ID_PREFIX) :] if identifier.startswith(CLAIM_ID_PREFIX) else identifier
    if not text.startswith(SOURCE_ID_PREFIX):
        return None
    source = text.partition(EVIDENCE_SCOPE_SEPARATOR)[0]
    return source if len(source) > len(SOURCE_ID_PREFIX) else None


def document_of(identifier: str, nodes: Mapping[str, ProvenanceNode]) -> str | None:
    """The source ID ``identifier`` belongs to, or ``None`` when it belongs to no single document.

    A source node is its own document; an evidence or claim node belongs to
    its ``source_id``, else to the ``src:`` scope of its identifier;
    derivation steps and validation records belong to no single document. An
    identifier that is not a node is attributed by its shape (``src:<fp>``,
    ``src:<fp>#<local>``, ``claim:src:<fp>#f<n>``). Never raises.
    """
    if not _non_empty_str(identifier):
        return None
    node = nodes.get(identifier)
    if isinstance(node, SourceNode):
        return node.node_id
    if isinstance(node, (EvidenceNode, ClaimNode)) and _non_empty_str(node.source_id):
        return node.source_id
    if node is not None and not isinstance(node, (EvidenceNode, ClaimNode)):
        return None  # derivation steps and validation records belong to no single document
    return _scope_of(identifier)


def step_documents(step: DerivationStep, nodes: Mapping[str, ProvenanceNode]) -> frozenset[str]:
    """The documents a derivation step touches: those of its input and output identifiers."""
    identifiers = (*_identifiers(getattr(step, "input_ids", ())), *_identifiers(getattr(step, "output_ids", ())))
    return frozenset(document for document in (document_of(i, nodes) for i in identifiers) if document)


def stage_segment_document(segment: MissingSegment) -> str | None:
    """The source ID a per-document ``record_kind="stage"`` segment names first; ``None`` otherwise.

    :func:`build_graph` lists the source ID first in a per-document stage
    segment and only step IDs in a run-wide one, so a run-wide segment (or any
    other segment) yields ``None``.
    """
    if not isinstance(segment, MissingSegment) or segment.record_kind != _STAGE_RECORD_KIND:
        return None
    ids = _identifiers(segment.affected_ids)
    if ids and EVIDENCE_SCOPE_SEPARATOR not in ids[0] and _scope_of(ids[0]) == ids[0]:
        return ids[0]
    return None


def is_claim_identifier(identifier: str, nodes: Mapping[str, ProvenanceNode]) -> bool:
    """Whether ``identifier`` is a claim: a claim node, or (not being a node) a ``claim:`` identifier."""
    if not _non_empty_str(identifier):
        return False
    node = nodes.get(identifier)
    return isinstance(node, ClaimNode) or (node is None and identifier.startswith(CLAIM_ID_PREFIX))


def evidence_id_parts(identifier: str) -> tuple[str, str] | None:
    """``(source_id, local_id)`` of an evidence identifier ``<source_id>#<local_id>``; ``None`` otherwise.

    The never-raising form of :func:`provenance.identity.parse_evidence_id`.
    """
    if not _non_empty_str(identifier):
        return None
    try:
        return parse_evidence_id(identifier)
    except InvalidIdentifierError:
        return None


def stage_sort_key(stage: str) -> tuple[int, str]:
    """Declared stages in ``STAGE_ORDER`` first, then undeclared stages by name, ``"unattributed"`` last."""
    if stage in STAGE_ORDER:
        return (STAGE_ORDER.index(stage), stage)
    return (len(STAGE_ORDER) + (1 if stage == UNATTRIBUTED_STAGE else 0), stage)


def producing_stages(nodes: Mapping[str, ProvenanceNode]) -> dict[str, frozenset[str]]:
    """``node_id -> stages`` of the derivation steps that produce it.

    A step produces each identifier in its ``output_ids`` that is not also in
    its ``input_ids``; a step listing an identifier on both sides is an
    in-place transform (filter, annotate, ...) and produces nothing. Steps
    without a non-empty ``stage`` are ignored. Pass the result to
    :func:`attribute_stage`.
    """
    produced: dict[str, set[str]] = {}
    for node in nodes.values():
        if not isinstance(node, DerivationStep) or not _non_empty_str(node.stage):
            continue
        inputs = set(_identifiers(node.input_ids))
        for output_id in _identifiers(node.output_ids):
            if output_id not in inputs:
                produced.setdefault(output_id, set()).add(node.stage)
    return {node_id: frozenset(stages) for node_id, stages in produced.items()}


def sole_contract_stage(
    kind: str, contracts: Mapping[str, StageContract] = STAGE_CONTRACTS
) -> str | None:
    """The one stage in ``contracts`` whose contract requires ``kind``, if exactly one does."""
    if not isinstance(kind, str) or not isinstance(contracts, Mapping):
        return None
    stages = [
        stage
        for stage, contract in contracts.items()
        if isinstance(getattr(contract, "required_record_kinds", None), tuple) and kind in contract.required_record_kinds
    ]
    return stages[0] if len(stages) == 1 else None


def attribute_stage(
    record: object,
    producers: Mapping[str, Collection[str]],
    *,
    identifiers: Iterable[str] | None = None,
    kind: str | None = None,
    contracts: Mapping[str, StageContract] = STAGE_CONTRACTS,
) -> tuple[str, str]:
    """``(stage, basis)`` of a record — the shared stage-attribution rule.

    ``record`` is a node, a node identifier, or a value that failed to
    materialize. ``producers`` is :func:`producing_stages` of the graph's
    nodes. ``identifiers`` defaults to the node's ``node_id`` (or the
    identifier itself) and ``kind`` to the node's ``kind``; pass them for a
    value that is not a well-formed node. The first rule that applies wins:

    1. ``own`` — a derivation step's own non-empty ``stage``;
    2. ``producing_step`` — the earliest stage (:func:`stage_sort_key`) among
       the producing steps of any of ``identifiers`` (in-place steps excluded);
    3. ``stage_contract`` — :func:`sole_contract_stage` of ``kind``;
    4. ``none`` — :data:`UNATTRIBUTED_STAGE`.
    """
    if isinstance(record, DerivationStep) and _non_empty_str(record.stage):
        return record.stage, "own"
    if identifiers is None:
        if isinstance(record, str):
            identifiers = (record,)
        elif isinstance(record, ProvenanceNode):
            identifiers = (record.node_id,)
        else:
            identifiers = ()
    if kind is None and isinstance(record, ProvenanceNode):
        kind = record.kind
    stages = {
        stage
        for identifier in identifiers
        if _non_empty_str(identifier)
        for stage in producers.get(identifier, ())
        if _non_empty_str(stage)
    }
    if stages:
        return min(stages, key=stage_sort_key), "producing_step"
    contract = sole_contract_stage(kind, contracts) if isinstance(kind, str) else None
    if contract is not None:
        return contract, "stage_contract"
    return UNATTRIBUTED_STAGE, "none"


# ---------------------------------------------------------------------------
# Stage and kind of a record that failed to materialize
# ---------------------------------------------------------------------------


def _record_kind(value: object) -> str:
    """The node kind a value was meant to be: from its class, else its declared kind."""
    kind = _CLASS_KINDS.get(type(value))
    if kind is not None:
        return kind
    declared = getattr(value, "kind", None) if isinstance(value, ProvenanceNode) else None
    if isinstance(declared, str) and declared in _NODE_KINDS:
        return declared
    return _NODE_RECORD_KIND


def _edge_stage(
    edge: object, nodes: Mapping[str, ProvenanceNode], producers: Mapping[str, Collection[str]]
) -> str:
    """An edge belongs to the stage of the node it leaves, when that node materialized."""
    from_id = getattr(edge, "from_id", None) if isinstance(edge, ProvenanceEdge) else None
    from_node = nodes.get(from_id) if _non_empty_str(from_id) else None  # type: ignore[arg-type]
    if from_node is None:
        return UNATTRIBUTED_STAGE
    return attribute_stage(from_node, producers)[0]


def _ids_of(*candidates: object) -> tuple[str, ...]:
    seen: list[str] = []
    for candidate in candidates:
        if _non_empty_str(candidate) and candidate not in seen:
            seen.append(candidate)  # type: ignore[arg-type]
    return tuple(seen)


def _safe(compute: Callable[[], str], fallback: str) -> str:
    try:
        return compute()
    except Exception:  # noqa: BLE001 — attribution is best effort and must never raise
        return fallback


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _edge(kind: EdgeKind, from_id: str, to_id: str) -> ProvenanceEdge:
    return ProvenanceEdge(edge_id=f"{kind}:{from_id}->{to_id}", kind=kind, from_id=from_id, to_id=to_id, attributes={})


def _structural_edges(node: ProvenanceNode) -> list[ProvenanceEdge]:
    """Edges implied by a node's own reference fields."""
    edges: list[ProvenanceEdge] = []
    if isinstance(node, (EvidenceNode, ClaimNode)) and _non_empty_str(node.source_id):
        edges.append(_edge("belongs_to_source", node.node_id, node.source_id))
    elif isinstance(node, DerivationStep):
        for ids, kind in ((node.input_ids, "has_input"), (node.output_ids, "has_output")):
            if isinstance(ids, (tuple, list)):
                edges.extend(_edge(kind, node.node_id, target) for target in ids if _non_empty_str(target))  # type: ignore[arg-type]
    return edges


def _items(container: object) -> list[tuple[Any, Any]] | None:
    if not isinstance(container, Mapping):
        return None
    try:
        return list(container.items())
    except Exception:  # noqa: BLE001
        return None


def _sequence(container: object) -> list[Any] | None:
    if isinstance(container, (tuple, list)):
        return list(container)
    return None


def _sort_segments(segments: Iterable[MissingSegment]) -> tuple[MissingSegment, ...]:
    unique: dict[MissingSegment, None] = dict.fromkeys(segments)
    return tuple(sorted(unique, key=lambda s: (s.stage, s.record_kind, s.affected_ids, s.reason)))


def _sort_edges(edges: Iterable[ProvenanceEdge]) -> tuple[ProvenanceEdge, ...]:
    return tuple(sorted(edges, key=lambda edge: edge.edge_id))


def _materialize_nodes(
    recorded: RecordedRun, segments: list[MissingSegment]
) -> dict[str, ProvenanceNode]:
    items = _items(recorded.nodes)
    if items is None:
        segments.append(
            MissingSegment(
                stage=UNATTRIBUTED_STAGE,
                record_kind=_NODE_RECORD_KIND,
                reason=f"snapshot nodes is not a mapping: {type(recorded.nodes).__name__}",
                affected_ids=(),
            )
        )
        return {}

    nodes: dict[str, ProvenanceNode] = {}
    rejected: list[tuple[object, object, str]] = []
    for key, value in items:
        try:
            node = _check_node(key, value)
        except Exception as exc:  # noqa: BLE001 — every failure becomes a missing segment
            rejected.append((key, value, _reason(exc)))
            continue
        nodes[node.node_id] = node

    producers = producing_stages(nodes)
    for key, value, reason in rejected:
        record_kind = _record_kind(value)
        affected = _ids_of(key, getattr(value, "node_id", None) if isinstance(value, ProvenanceNode) else None)
        stage = _safe(
            lambda: attribute_stage(value, producers, identifiers=affected, kind=record_kind)[0], UNATTRIBUTED_STAGE
        )
        segments.append(MissingSegment(stage=stage, record_kind=record_kind, reason=reason, affected_ids=affected))
    return nodes


def _materialize_edges(
    recorded: RecordedRun,
    nodes: Mapping[str, ProvenanceNode],
    segments: list[MissingSegment],
) -> dict[str, ProvenanceEdge]:
    edges: dict[str, ProvenanceEdge] = {}
    producers = producing_stages(nodes)
    recorded_edges = _sequence(recorded.edges)
    if recorded_edges is None:
        segments.append(
            MissingSegment(
                stage=UNATTRIBUTED_STAGE,
                record_kind=_EDGE_RECORD_KIND,
                reason=f"snapshot edges is not a sequence: {type(recorded.edges).__name__}",
                affected_ids=(),
            )
        )
        recorded_edges = []

    for value in recorded_edges:
        try:
            edge = _check_edge(value)
            existing = edges.get(edge.edge_id)
            if existing is not None and existing != edge:
                raise _Unmaterializable(f"edge_id {edge.edge_id!r} already assembled with differing content")
        except Exception as exc:  # noqa: BLE001 — every failure becomes a missing segment
            affected = _ids_of(getattr(value, "edge_id", None) if isinstance(value, ProvenanceEdge) else None)
            if not affected and isinstance(value, ProvenanceEdge):
                affected = _ids_of(value.from_id, value.to_id)
            stage = _safe(lambda: _edge_stage(value, nodes, producers), UNATTRIBUTED_STAGE)
            segments.append(
                MissingSegment(stage=stage, record_kind=_EDGE_RECORD_KIND, reason=_reason(exc), affected_ids=affected)
            )
            continue
        edges[edge.edge_id] = edge

    for node in nodes.values():
        for edge in _structural_edges(node):
            edges.setdefault(edge.edge_id, edge)
    return edges


def _failure_details(raw: object) -> list[str]:
    """Distinct non-blank recorded reasons, in recording order."""
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (tuple, list)):
        return []
    return list(dict.fromkeys(item.strip() for item in raw if isinstance(item, str) and item.strip()))


def _failed_stage_segments(
    recorded: RecordedRun, nodes: Mapping[str, ProvenanceNode]
) -> list[MissingSegment]:
    steps = [node for node in nodes.values() if isinstance(node, DerivationStep)]
    segments: list[MissingSegment] = []

    # Run-wide failures: one segment listing every step of the stage.
    statuses = _items(recorded.stage_status) or []
    reasons = recorded.stage_failure_reasons if isinstance(recorded.stage_failure_reasons, Mapping) else {}
    for stage, status in statuses:
        if not _non_empty_str(stage) or status != "failed":
            continue
        recorded_reason = reasons.get(stage)
        detail = recorded_reason.strip() if isinstance(recorded_reason, str) else ""
        reason = f"stage {stage!r} failed: {detail}" if detail else f"stage {stage!r} failed (no reason recorded)"
        affected = tuple(sorted(step.node_id for step in steps if step.stage == stage))
        segments.append(MissingSegment(stage=stage, record_kind=_STAGE_RECORD_KIND, reason=reason, affected_ids=affected))

    # Per-document failures: one segment per (stage, source_id), naming the document first.
    document_statuses = _items(recorded.document_stage_status) or []
    raw_document_reasons = recorded.document_stage_failure_reasons
    document_reasons = raw_document_reasons if isinstance(raw_document_reasons, Mapping) else {}
    documents_of_step = {step.node_id: step_documents(step, nodes) for step in steps}
    for source_id, by_stage in document_statuses:
        if not _non_empty_str(source_id):
            continue
        stage_reasons = document_reasons.get(source_id)
        stage_reasons = stage_reasons if isinstance(stage_reasons, Mapping) else {}
        for stage, status in _items(by_stage) or []:
            if not _non_empty_str(stage) or status != "failed":
                continue
            details = _failure_details(stage_reasons.get(stage))
            reason = (
                f"stage {stage!r} failed for document {source_id!r}: {'; '.join(details)}"
                if details
                else f"stage {stage!r} failed for document {source_id!r} (no reason recorded)"
            )
            affected = tuple(
                sorted(step.node_id for step in steps if step.stage == stage and source_id in documents_of_step[step.node_id])
            )
            segments.append(
                MissingSegment(
                    stage=stage, record_kind=_STAGE_RECORD_KIND, reason=reason, affected_ids=(source_id, *affected)
                )
            )
    return segments


def build_graph(recorded: RecordedRun) -> ProvenanceGraph:
    """Assemble the typed provenance graph of one run from a recorder snapshot (1.6, 7.1–7.5).

    Never raises for a :class:`~provenance.models.RecordedRun`: every record
    that cannot be materialized, and every stage marked failed, becomes a
    :class:`~provenance.models.MissingSegment` while the rest of the graph is
    still produced. Raises ``TypeError`` only when ``recorded`` is not a
    ``RecordedRun`` (a caller error, as no run identity exists to build on).
    """
    if not isinstance(recorded, RecordedRun) or not isinstance(recorded.run, RunIdentity):
        raise TypeError(f"recorded must be a RecordedRun with a RunIdentity, got {type(recorded).__name__}")

    segments: list[MissingSegment] = []
    nodes = _materialize_nodes(recorded, segments)
    edges = _materialize_edges(recorded, nodes, segments)
    segments.extend(_failed_stage_segments(recorded, nodes))

    graph = ProvenanceGraph(
        schema_id=PROVENANCE_SCHEMA_ID,
        schema_version=PROVENANCE_SCHEMA_VERSION,
        run=recorded.run,
        nodes={node_id: nodes[node_id] for node_id in sorted(nodes)},
        edges=_sort_edges(edges.values()),
        missing_segments=_sort_segments(segments),
    )
    for segment in graph.missing_segments:
        logger.debug(
            "provenance missing segment [%s/%s] %s: %s",
            segment.stage,
            segment.record_kind,
            ", ".join(segment.affected_ids) or "-",
            segment.reason,
        )
    if graph.missing_segments:
        logger.warning(
            "provenance graph for %s assembled with %d missing segment(s); %d node(s), %d edge(s) kept",
            recorded.run.run_id,
            len(graph.missing_segments),
            len(graph.nodes),
            len(graph.edges),
        )
    return graph


# ---------------------------------------------------------------------------
# Attachments (8.7)
# ---------------------------------------------------------------------------


def _require_graph(graph: object) -> ProvenanceGraph:
    if not isinstance(graph, ProvenanceGraph):
        raise TypeError(f"graph must be a ProvenanceGraph, got {type(graph).__name__}")
    return graph


def attach_validation(graph: ProvenanceGraph, report: ChainValidationReport) -> ProvenanceGraph:
    """Return a new graph carrying ``report`` plus its validation record and ``validates`` edges (8.7).

    Adds one :class:`~provenance.models.ValidationRecord` with ID
    ``val:<run_id>:chain_validation`` whose ``finding_ids`` are the report's
    findings in report order and whose ``completed`` mirrors the report, plus
    one ``validates`` edge to each finding subject (``node_id``) present in the
    graph — so no dangling edge is introduced. A previously attached record and
    its edges are replaced. When no validation ID can be derived from the
    run's identity, the report is still attached and one ``validation``
    missing segment records why. ``graph`` is never modified; attaching the
    same report twice yields an equal graph. Raises ``TypeError`` only when an
    argument is not of the declared type.
    """
    graph = _require_graph(graph)
    if not isinstance(report, ChainValidationReport):
        raise TypeError(f"report must be a ChainValidationReport, got {type(report).__name__}")

    findings = report.findings if isinstance(report.findings, (tuple, list)) else ()
    try:
        validation_id = derive_validation_id(graph.run.run_id, VALIDATION_CHECK_NAME)
    except Exception as exc:  # noqa: BLE001 — degrade to a missing segment, never raise
        segment = MissingSegment(
            stage=VALIDATION_CHECK_NAME,
            record_kind="validation",
            reason=f"validation record cannot be identified: {_reason(exc)}",
            affected_ids=(),
        )
        return dataclasses.replace(
            graph,
            validation=report,
            missing_segments=_sort_segments((*graph.missing_segments, segment)),
        )

    record = ValidationRecord(
        node_id=validation_id,
        kind="validation",
        run_id=graph.run.run_id,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        check_name=VALIDATION_CHECK_NAME,
        finding_ids=tuple(getattr(finding, "finding_id", "") for finding in findings),
        completed=report.completed,
    )
    subjects = sorted(
        {
            finding.node_id
            for finding in findings
            if _non_empty_str(getattr(finding, "node_id", None))
            and finding.node_id in graph.nodes
            and finding.node_id != validation_id
        }
    )
    kept = [
        edge for edge in graph.edges if not (edge.kind == "validates" and edge.from_id == validation_id)
    ]
    validates = [_edge("validates", validation_id, subject) for subject in subjects]
    nodes = {**graph.nodes, validation_id: record}
    return dataclasses.replace(
        graph,
        nodes={node_id: nodes[node_id] for node_id in sorted(nodes)},
        edges=_sort_edges({edge.edge_id: edge for edge in (*kept, *validates)}.values()),
        validation=report,
    )


def attach_completeness(graph: ProvenanceGraph, report: CompletenessReport) -> ProvenanceGraph:
    """Return a new graph carrying ``report`` (8.7 counterpart for completeness, 2.6).

    Completeness is a computed view, not a node kind, so no node or edge is
    added. ``graph`` is never modified; attaching the same report twice yields
    an equal graph. Raises ``TypeError`` only when an argument is not of the
    declared type.
    """
    graph = _require_graph(graph)
    if not isinstance(report, CompletenessReport):
        raise TypeError(f"report must be a CompletenessReport, got {type(report).__name__}")
    return dataclasses.replace(graph, completeness=report)
