"""projection — the ordered, append-only event-log view of a provenance graph (7.6).

:func:`project_event_log` (provenance-core design, "Analysis Layer →
Projection") derives one :class:`ProvenanceEvent` per graph node, ordered by a
total key that needs no clock and no randomness. The event log is a
*projection*: it is never persisted on its own, no decision store exists
beside the graph, and regenerating it from an equal graph yields an equal log.
This module reads nothing and writes nothing.

Stage attribution
    Only :class:`~provenance.models.DerivationStep` carries a ``stage``. Every
    other node takes a stage derived from the graph alone, by the shared rule
    :func:`provenance.graph.attribute_stage` — the same rule the graph builder
    uses for missing segments and the completeness computation for
    responsible stages, so the three always agree:

    1. ``own`` — a derivation step's own ``stage``;
    2. ``producing_step`` — the earliest stage (in the ordering below) of a
       derivation step listing the node among its ``output_ids``. A step that
       lists the node among its inputs *and* outputs is an in-place transform
       (filter, annotate, ...) and does not count as producing it;
    3. ``stage_contract`` — the one declared stage whose contract requires
       the node's kind (source → ``document_extraction``, evidence →
       ``evidence_index``, validation → ``chain_validation``);
    4. ``none`` — otherwise the node's stage is ``"unattributed"`` (e.g. a
       claim no step produced, since three declared stages emit claims).

    The basis used is recorded as ``summary["stage_basis"]``.

Ordering
    Events sort on ``(stage position, stage, node-kind rank, node_id)`` and are
    numbered ``1..n`` in that order; ``(stage position, stage)`` is
    :func:`provenance.graph.stage_sort_key`. A declared stage's position is its index
    in ``STAGE_ORDER``; a stage name a derivation step carries but
    ``STAGE_ORDER`` does not declare sorts after every declared stage (by
    name), and ``"unattributed"`` sorts last. For declared stages this is
    exactly the design key ``(STAGE_ORDER.index(stage), node_kind_rank,
    node_id)``. The node-kind rank is the declaration order of the closed
    ``NodeKind`` vocabulary (source, evidence, claim, derivation, validation).
    Node identifiers are unique in a graph, so the order is total.

Event fields
    ``event_kind`` is ``derivation_<derivation_kind>`` for a derivation step
    and ``<node_kind>_recorded`` otherwise. ``summary`` copies a fixed set of
    the node's own scalar and identifier fields — never evidence text
    (``parser_metadata``), privacy carriers or extensions — plus
    ``stage_basis`` and, for evidence, ``anchor_precision``. Missing segments
    and attached reports are not nodes and produce no event; an attached
    validation report appears through its ``ValidationRecord`` node.

Imports only the standard library (no I/O, clock or randomness module), ``provenance.models``
and ``provenance.graph``.
"""

from __future__ import annotations

import typing
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from provenance.graph import (
    UNATTRIBUTED_STAGE,
    ProvenanceGraph,
    attribute_stage,
    producing_stages,
    stage_sort_key,
)
from provenance.models import (
    ClaimNode,
    DerivationStep,
    EvidenceNode,
    NodeKind,
    ProvenanceNode,
    SourceNode,
    ValidationRecord,
    _Record,  # the shared mapping-freezing mixin; ProvenanceEvent is a provenance dataclass too
)

__all__ = [
    "UNATTRIBUTED_STAGE",
    "ProvenanceEvent",
    "project_event_log",
]

_NODE_KIND_RANK: Mapping[str, int] = MappingProxyType(
    {kind: rank for rank, kind in enumerate(typing.get_args(NodeKind))}
)
"""Node-kind rank: declaration order of the closed ``NodeKind`` vocabulary."""

_SUMMARY_FIELDS: Mapping[type, tuple[str, ...]] = MappingProxyType({
    SourceNode: ("document_fingerprint", "source_format", "source_label"),
    EvidenceNode: ("source_id", "local_id", "evidence_kind", "producing_backend", "rank_score"),
    ClaimNode: (
        "source_id",
        "field_index",
        "field_name",
        "value_digest",
        "origin",
        "support",
        "confidence",
        "cited_evidence_ids",
    ),
    DerivationStep: (
        "derivation_kind",
        "determinism",
        "model_id",
        "input_ids",
        "output_ids",
        "removal_reason",
        "removed_ids",
    ),
    ValidationRecord: ("check_name", "completed", "finding_ids"),
})
"""The node fields each event summary copies, per concrete node type."""


@dataclass(frozen=True)
class ProvenanceEvent(_Record):
    """One entry of the event-log projection; every field is derived from the graph (7.6)."""

    sequence: int
    stage: str
    event_kind: str
    node_id: str
    node_kind: NodeKind
    summary: Mapping[str, Any]


def _event_kind(node: ProvenanceNode) -> str:
    if isinstance(node, DerivationStep):
        return f"derivation_{node.derivation_kind}"
    return f"{node.kind}_recorded"


def _summary(node: ProvenanceNode, stage_basis: str) -> dict[str, Any]:
    summary: dict[str, Any] = {"stage_basis": stage_basis}
    for name in _SUMMARY_FIELDS.get(type(node), ()):
        summary[name] = getattr(node, name, None)
    if isinstance(node, EvidenceNode):
        summary["anchor_precision"] = getattr(node.anchor, "precision", None)
    return summary


def project_event_log(graph: ProvenanceGraph) -> tuple[ProvenanceEvent, ...]:
    """Return the ordered event log of ``graph``: one event per node, numbered from 1 (7.6).

    A pure function of the graph: no clock, no randomness, no file access, and
    no state kept between calls, so two projections of equal graphs are equal.
    ``graph`` is not modified. Raises ``TypeError`` only when ``graph`` is not
    a :class:`~provenance.graph.ProvenanceGraph`.
    """
    if not isinstance(graph, ProvenanceGraph):
        raise TypeError(f"graph must be a ProvenanceGraph, got {type(graph).__name__}")
    nodes = {
        node_id: node
        for node_id, node in graph.nodes.items()
        if isinstance(node_id, str) and isinstance(node, ProvenanceNode)
    }
    producers = producing_stages(nodes)

    unordered = []
    for node_id, node in nodes.items():
        stage, basis = attribute_stage(node, producers)
        rank = _NODE_KIND_RANK.get(node.kind, len(_NODE_KIND_RANK))
        unordered.append(((*stage_sort_key(stage), rank, node_id), node_id, stage, node, basis))
    unordered.sort(key=lambda entry: entry[0])

    return tuple(
        ProvenanceEvent(
            sequence=sequence,
            stage=stage,
            event_kind=_event_kind(node),
            node_id=node_id,
            node_kind=node.kind,
            summary=_summary(node, basis),
        )
        for sequence, (_key, node_id, stage, node, basis) in enumerate(unordered, start=1)
    )
