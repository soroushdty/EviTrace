"""validation — chain validation of a provenance graph into a severity-classified report.

:func:`validate_chain` (provenance-core design, "Analysis Layer →
ChainValidator") runs a fixed set of read-only checks over a
:class:`~provenance.graph.ProvenanceGraph` and returns a populated
:class:`~provenance.models.ChainValidationReport`. This module declares no
dataclass: the finding type, the default severity table and the report
container are all owned by :mod:`provenance.models`.

Reference and structural checks
-------------------------------
``dangling_reference`` (8.1)
    An edge leaves or points at an identifier that is no node, or a claim
    cites (``cites_evidence`` edge or ``cited_evidence_ids`` entry) an
    evidence identifier that is no node and resembles none. A cited entry that
    is not a non-empty string is reported per position (``reason`` is
    ``"malformed"``, finding ID suffix ``[pos<n>]``) and never counts as
    support. Every dangling edge end is its own finding naming its edge. An edge leaving a
    record that failed to materialize is debris of that record — its
    :class:`~provenance.models.MissingSegment` already says why — and is not
    re-reported; a live node pointing at such a record *is* reported, with
    ``details["covered_by_missing_segment"]`` set.
``source_not_represented`` (8.2)
    An evidence or claim node's ``source_id`` (or a ``belongs_to_source``
    edge) does not name a source node of the graph. Reported instead of, not
    in addition to, ``dangling_reference``.
``evidence_out_of_run_context`` (8.3)
    A claim cites a node that exists but is outside its run context:
    ``details["reasons"]`` lists ``not_evidence`` (the target is not an
    evidence node), ``foreign_run`` (its ``run_id`` is not the graph's run) and
    ``foreign_source`` (it belongs to another document than the claim).
``missing_anchor`` (8.4)
    An evidence node whose anchor precision is ``absent``. ``approximate``
    anchors are usable and not reported.
``invalid_identifier`` (8.4)
    A node ID that does not equal the ID the identity service derives from the
    record's own content (source fingerprint; ``<source_id>#<local_id>``; claim
    source, field index and optional ``@r<n>`` revision; the derivation
    content address; ``val:<run_id>:<check_name>``), an edge ID other than
    ``<kind>:<from>-><to>``, or a malformed run ID.
``privacy_carrier_invalid`` (9.6)
    A node whose carrier was rejected, or whose carrier does not conform to
    the declared structure (a ``labeled`` carrier failing
    :func:`~provenance.privacy_carrier.check_carrier_conformance` or lacking
    string supplier metadata; an ``unknown`` carrier holding any value,
    supplier metadata included; a state
    outside the vocabulary). The check is structural only — no label or
    decision *value* is inspected (9.4). The node is retained.
``duplicate_reference`` (5.3, 5.4)
    A claim whose ``cited_evidence_ids`` names one evidence node more than once.
``ambiguous_reference`` (5.3, 5.4)
    A cited evidence identifier that is no node but equals one or more evidence
    nodes of the same source after trimming whitespace and ignoring case of
    the local ID — the same rule as the claim adapter. Reported instead of
    ``dangling_reference``; ``details["candidates"]`` lists the candidates.
``unsupported_claim`` (5.2, 5.3)
    A claim whose support is ``unsupported`` or that cites no evidence.
``prefill_unsupported`` (5.7)
    Reported **instead of** ``unsupported_claim`` for such a claim whose
    ``origin`` is ``prefill`` — a prefilled value that came from a fallback
    and so cites nothing. A dedicated code so its severity is overridable on
    its own; a prefill claim citing only anchorless header metadata is
    signalled by ``missing_anchor`` on the cited node, not by this code.

Every finding names the node and/or edge involved and stamps the default
severity from :data:`~provenance.models.DEFAULT_SEVERITIES`. Graph-derived
finding IDs are ``finding:<code>:chain:<subject>`` where ``<subject>`` is the
edge ID when an edge is involved, else the node ID (or ``run``), suffixed with
``[<reference>]`` when the problem is about one reference of a node — distinct
from recorder (``…:<n>``) and claim-adapter (``…:loc[<n>]``) IDs.

Recorded findings (5.4)
-----------------------
Findings recorded during the run (``RecordedRun.findings`` — the recorder's
``duplicate_identity``, issue-sink findings, claim-adapter reference findings)
cannot be reconstructed from the graph, so the caller passes them as
``recorded_findings``. They are merged with the graph-derived findings:

1. **By ``finding_id``** — a repeated ID is kept once (first occurrence).
2. **By reference problem** — findings that carry ``details["reference"]``
   are keyed by ``(code, node_id, normalized reference, reference kind)``. The
   reference is normalized by scoping a local evidence ID (no ``#``) to the
   source of the claim named by ``node_id``, so the issue sink's full ID and
   the claim adapter's local ID of one ``loc`` meet; the reference kind is
   ``details["reference_kind"]`` (default ``evidence``; a
   ``contributing_claim`` reference is not normalized). Graph-derived
   findings set it explicitly — the edge kind for a dangling edge target
   (``has_input``, ``revises``, …), ``source`` for an unrepresented source —
   and a dangling edge *origin* carries no ``reference`` at all, so distinct
   edges are never merged with one another. Of each group the
   finding with the smallest ``finding_id`` is kept and the others' IDs are
   listed in its ``details["merged_finding_ids"]``.

Merged findings are counted in ``counts_by_severity`` like any other. A
recorded entry that is not a well-formed
:class:`~provenance.models.ValidationFinding` cannot be interpreted, so the run
is reported unvalidated rather than silently dropping it.

Completion (8.6)
----------------
Every check runs inside its own guard. A check that raises, a graph whose node
or edge index is unavailable, an argument that is not a graph, or an
uninterpretable recorded finding makes the report ``completed=False`` with
``incompletion_reason`` naming each problem; the findings of the checks that
did run are still reported. :func:`validate_chain` never raises.

Topology checks
---------------
``graph_cycle`` (8.4)
    The derivation and revision subgraph must be acyclic. It is read in
    dependency direction — a step depends on each ``has_input`` target, each
    ``has_output`` target depends on its step, and a ``derived_from`` or
    ``revises`` edge's origin depends on its target — so a cycle means some
    record is (transitively) derived from itself. An identifier listed as
    both an input and an output of one step is an in-place transform — a
    filter, normalize or annotate step keeping a canonical evidence or claim
    ID — not a cycle: that output gets no "depends on the step" edge, while
    the step still depends on it as an input, so a genuine cycle through
    other steps (step A keeps ``e1`` and writes ``c``; step B reads ``c`` and
    writes ``e1``) is still found. Edges
    leaving a record that failed to materialize are debris and are skipped;
    endpoints that are not nodes still take part (a cycle through a dangling
    identifier is still a cycle). The strongly connected components are found
    by an iterative Tarjan depth-first search with an explicit stack — never
    recursion — so a pathological chain cannot exhaust the interpreter stack.
    Each component with more than one member, or with a self-loop, is one
    finding: ``details["members"]`` lists its identifiers and
    ``details["edge_ids"]`` the derivation/revision edges inside it (both
    sorted); ``node_id`` is its smallest member that is a node.
``orphan_node`` (8.4)
    A node connected by no edge to any *other node* of the graph. Edges to or
    from identifiers that are not nodes, self-loops and ``validates`` edges do
    not connect: a ``validates`` edge only records that a finding was raised,
    so re-validating a graph that carries its validation record yields the
    same findings. Validation records are run-scoped (they belong to the run,
    not to a document) and a clean report's record has no edge by design, so
    they are never orphans. Every other node kind — sources included — can be.
    ``details["incident_edge_ids"]`` lists the node's non-``validates`` edges.
``inconsistent_derivation`` (8.4)
    A derivation step with neither an input nor an output, counting only
    identifiers that are non-empty strings (the data-model invariant; no
    other derivation inconsistency is defined).

Severity policy and ordering (8.5)
----------------------------------
Every finding — graph-derived or recorded — is stamped with the severity of
the effective policy: :data:`~provenance.models.DEFAULT_SEVERITIES` updated by
``severity_overrides`` (``provenance.severity_overrides`` in configuration).
A recorded finding whose code is not in the default table keeps the severity
it was recorded with unless an override names its code; it is reported like
any other finding. Configuration is not validated upstream, so overrides
degrade instead of failing: ``None`` means no override; a value that is not a
mapping is ignored as a whole, and an entry whose code is not a non-empty
string or whose severity is not exactly one of ``critical``, ``error``,
``warning``, ``info`` is ignored on its own — each logged at WARNING, never
raised, and never making the run unvalidated (the checks still ran). An
override naming a code outside the default table is applied (it can only
match recorded findings) and logged at WARNING, so a misspelt code is
visible. Findings are ordered deterministically by ``(severity_rank, code,
node_id)`` — ``critical`` first, a missing ``node_id`` before any other — with
``edge_id`` and ``finding_id`` as tie-breakers.

Validation is read-only with respect to the graph. Imports only the standard
library, ``provenance.models``, ``provenance.identity``,
``provenance.privacy_carrier``, ``provenance.graph`` and ``utils.logging_utils``.
"""

from __future__ import annotations

import dataclasses
import typing
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

from provenance.graph import ProvenanceGraph
from provenance.identity import (
    CLAIM_ID_PREFIX,
    EVIDENCE_SCOPE_SEPARATOR,
    RUN_ID_PREFIX,
    InvalidIdentifierError,
    derive_claim_id,
    derive_derivation_id,
    derive_source_id,
    derive_validation_id,
    format_evidence_id,
    parse_evidence_id,
)
from provenance.models import (
    DEFAULT_SEVERITIES,
    ChainValidationReport,
    ClaimNode,
    DerivationStep,
    EvidenceNode,
    ProvenanceEdge,
    ProvenanceNode,
    Severity,
    SourceNode,
    ValidationFinding,
    ValidationRecord,
)
from provenance.privacy_carrier import PrivacyCarrier, PrivacyState, check_carrier_conformance
from utils.logging_utils import get_logger

__all__ = ["validate_chain"]

logger = get_logger(__name__)

_DANGLING = "dangling_reference"
_SOURCE_NOT_REPRESENTED = "source_not_represented"
_OUT_OF_RUN_CONTEXT = "evidence_out_of_run_context"
_MISSING_ANCHOR = "missing_anchor"
_INVALID_IDENTIFIER = "invalid_identifier"
_PRIVACY_INVALID = "privacy_carrier_invalid"
_DUPLICATE = "duplicate_reference"
_AMBIGUOUS = "ambiguous_reference"
_UNSUPPORTED = "unsupported_claim"
_PREFILL_UNSUPPORTED = "prefill_unsupported"
_PREFILL_ORIGIN = "prefill"
_CYCLE = "graph_cycle"
_ORPHAN = "orphan_node"
_INCONSISTENT = "inconsistent_derivation"

_CITES = "cites_evidence"
_BELONGS = "belongs_to_source"
_DEDICATED_TARGET_KINDS: frozenset[str] = frozenset({_CITES, _BELONGS})
"""Edge kinds whose *target* is checked by a dedicated check, not the generic dangling one."""

_VALIDATES = "validates"
_HAS_INPUT = "has_input"
_DEPENDS_ON_TARGET: frozenset[str] = frozenset({_HAS_INPUT, "derived_from", "revises"})
"""Derivation/revision edge kinds whose origin depends on their target."""
_TARGET_DEPENDS_ON_ORIGIN: frozenset[str] = frozenset({"has_output"})
"""Derivation edge kinds whose target depends on their origin (the step produced it)."""

_SEVERITY_ORDER: tuple[Severity, ...] = typing.get_args(Severity)
_SEVERITY_RANK: Mapping[str, int] = {"critical": 0, "error": 1, "warning": 2, "info": 3}
"""Rank of each severity, most severe first (8.5)."""
_PRIVACY_STATES: frozenset[str] = frozenset(typing.get_args(PrivacyState))

_FINDING_ID_PREFIX = "finding:"
_CHAIN_MARK = "chain"
_RUN_SUBJECT = "run"
_EVIDENCE_REFERENCE = "evidence"
_CONTRIBUTING_CLAIM = "contributing_claim"
_REPR_LIMIT = 80


class _CannotRun(Exception):
    """Validation input is unusable; the message is the incompletion reason."""


def _short_repr(value: object) -> str:
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 — a broken __repr__ must not make validation raise
        text = f"<unrepresentable {type(value).__name__}>"
    return text if len(text) <= _REPR_LIMIT else text[: _REPR_LIMIT - 3] + "..."


def _finding(
    code: str,
    *,
    node_id: str | None,
    edge_id: str | None,
    message: str,
    details: Mapping[str, Any],
    reference: str | None = None,
) -> ValidationFinding:
    subject = edge_id or node_id or _RUN_SUBJECT
    suffix = f"[{reference}]" if reference is not None and edge_id is None else ""
    return ValidationFinding(
        finding_id=f"{_FINDING_ID_PREFIX}{code}:{_CHAIN_MARK}:{subject}{suffix}",
        code=code,
        severity=DEFAULT_SEVERITIES[code],
        node_id=node_id,
        edge_id=edge_id,
        message=message,
        details=details,
    )


# ---------------------------------------------------------------------------
# Read-only view of the graph shared by every check
# ---------------------------------------------------------------------------


class _Context:
    """Indexes over one graph, built once per validation; never mutates the graph."""

    __slots__ = ("nodes", "edges", "edge_ids", "run_id", "segment_ids", "_folded")

    def __init__(self, graph: ProvenanceGraph) -> None:
        if not isinstance(graph.nodes, Mapping):
            raise _CannotRun(f"node index unavailable: graph nodes is {type(graph.nodes).__name__}, not a mapping")
        if not isinstance(graph.edges, (tuple, list)):
            raise _CannotRun(f"edge index unavailable: graph edges is {type(graph.edges).__name__}, not a sequence")
        if not isinstance(graph.missing_segments, (tuple, list)):
            raise _CannotRun(
                f"missing segments unavailable: graph missing_segments is {type(graph.missing_segments).__name__}"
            )
        self.nodes: Mapping[str, ProvenanceNode] = graph.nodes
        self.edges: tuple[ProvenanceEdge, ...] = tuple(graph.edges)
        self.edge_ids: frozenset[str] = frozenset(edge.edge_id for edge in self.edges)
        run_id = getattr(graph.run, "run_id", None)
        self.run_id: str | None = run_id if isinstance(run_id, str) else None
        self.segment_ids: frozenset[str] = frozenset(
            affected for segment in graph.missing_segments for affected in segment.affected_ids
        )
        self._folded: dict[tuple[str, str], list[str]] | None = None

    def nodes_of(self, node_type: type) -> list[Any]:
        return [node for node in self.nodes.values() if isinstance(node, node_type)]

    def edges_of(self, kind: str) -> list[ProvenanceEdge]:
        return [edge for edge in self.edges if edge.kind == kind]

    def edge_id_if_present(self, kind: str, from_id: str, to_id: str) -> str | None:
        edge_id = f"{kind}:{from_id}->{to_id}"
        return edge_id if edge_id in self.edge_ids else None

    def ambiguity_candidates(self, reference: str) -> list[str]:
        """Evidence node IDs of the reference's source equal to it up to whitespace and case of the local ID."""
        try:
            source_id, local_id = parse_evidence_id(reference)
        except InvalidIdentifierError:
            return []
        if self._folded is None:
            folded: dict[tuple[str, str], list[str]] = {}
            for node in self.nodes_of(EvidenceNode):
                try:
                    node_source, node_local = parse_evidence_id(node.node_id)
                except InvalidIdentifierError:
                    continue
                folded.setdefault((node_source, node_local.strip().casefold()), []).append(node.node_id)
            self._folded = folded
        return sorted(c for c in self._folded.get((source_id, local_id.strip().casefold()), []) if c != reference)

    def references(self, kind: str, field_targets: Callable[[ProvenanceNode], Sequence[str]]) -> list[tuple[ProvenanceNode, str, str | None]]:
        """``(from_node, target, edge_id | None)`` for every distinct reference of ``kind``.

        The union of the targets named by each node's own fields and of the
        ``kind`` edges leaving existing nodes, in node then edge order.
        """
        found: dict[tuple[str, str], tuple[ProvenanceNode, str, str | None]] = {}
        for node in self.nodes.values():
            for target in field_targets(node):
                if isinstance(target, str) and target:
                    key = (node.node_id, target)
                    if key not in found:
                        found[key] = (node, target, self.edge_id_if_present(kind, node.node_id, target))
        for edge in self.edges_of(kind):
            node = self.nodes.get(edge.from_id)
            if node is not None and (edge.from_id, edge.to_id) not in found:
                found[(edge.from_id, edge.to_id)] = (node, edge.to_id, edge.edge_id)
        return list(found.values())


# ---------------------------------------------------------------------------
# Checks — each returns its findings or raises (the check then cannot run)
# ---------------------------------------------------------------------------


def _check_dangling_edges(ctx: _Context) -> list[ValidationFinding]:
    """8.1 for every edge end not covered by a dedicated check."""
    findings: list[ValidationFinding] = []
    for edge in ctx.edges:
        if edge.from_id not in ctx.nodes:
            if edge.from_id in ctx.segment_ids:
                continue  # debris of a record that became a missing segment
            findings.append(
                _finding(
                    _DANGLING,
                    node_id=None,
                    edge_id=edge.edge_id,
                    message=f"{edge.kind} edge {edge.edge_id} leaves {edge.from_id}, which is not a node",
                    details={"from_id": edge.from_id, "end": "from", "edge_kind": edge.kind, "covered_by_missing_segment": False},
                )
            )
            continue
        if edge.kind in _DEDICATED_TARGET_KINDS or edge.to_id in ctx.nodes:
            continue
        findings.append(
            _finding(
                _DANGLING,
                node_id=edge.from_id,
                edge_id=edge.edge_id,
                message=f"{edge.kind} edge from {edge.from_id} points at {edge.to_id}, which is not a node",
                details={
                    "reference": edge.to_id,
                    "reference_kind": edge.kind,
                    "end": "to",
                    "edge_kind": edge.kind,
                    "covered_by_missing_segment": edge.to_id in ctx.segment_ids,
                },
            )
        )
    return findings


def _is_reference(target: object) -> bool:
    """Whether a cited entry is usable as a reference at all: a non-empty string."""
    return isinstance(target, str) and bool(target)


def _cited(node: ProvenanceNode) -> Sequence[str]:
    return tuple(node.cited_evidence_ids) if isinstance(node, ClaimNode) else ()


def _check_evidence_references(ctx: _Context) -> list[ValidationFinding]:
    """8.1, 8.3, 5.4: every cited evidence reference resolves to evidence of the claim's run and source."""
    findings: list[ValidationFinding] = []
    for node in ctx.nodes_of(ClaimNode):
        for position, target in enumerate(node.cited_evidence_ids):
            if _is_reference(target):
                continue
            shown = _short_repr(target)
            findings.append(
                _finding(
                    _DANGLING,
                    node_id=node.node_id,
                    edge_id=None,
                    reference=f"pos{position}",
                    message=f"claim {node.node_id} cites {shown} at position {position}, which is not an evidence identifier",
                    details={
                        "reference": shown,
                        "reference_kind": _EVIDENCE_REFERENCE,
                        "reason": "malformed",
                        "position": position,
                    },
                )
            )
    for node, target, edge_id in ctx.references(_CITES, _cited):
        base = {"reference": target, "reference_kind": _EVIDENCE_REFERENCE}
        target_node = ctx.nodes.get(target)
        if target_node is None:
            candidates = ctx.ambiguity_candidates(target)
            if candidates:
                findings.append(
                    _finding(
                        _AMBIGUOUS,
                        node_id=node.node_id,
                        edge_id=edge_id,
                        reference=target,
                        message=(
                            f"{node.node_id} cites {_short_repr(target)}, which is not an evidence node "
                            f"but may mean {len(candidates)} known evidence node(s)"
                        ),
                        details={**base, "candidates": candidates},
                    )
                )
            else:
                findings.append(
                    _finding(
                        _DANGLING,
                        node_id=node.node_id,
                        edge_id=edge_id,
                        reference=target,
                        message=f"{node.node_id} cites {_short_repr(target)}, which is not a node",
                        details={**base, "covered_by_missing_segment": target in ctx.segment_ids},
                    )
                )
            continue

        reasons: list[str] = []
        if not isinstance(target_node, EvidenceNode):
            reasons.append("not_evidence")
        else:
            if target_node.run_id != ctx.run_id:
                reasons.append("foreign_run")
            citing_source = getattr(node, "source_id", None)
            if isinstance(node, (ClaimNode, EvidenceNode)) and target_node.source_id != citing_source:
                reasons.append("foreign_source")
        if reasons:
            findings.append(
                _finding(
                    _OUT_OF_RUN_CONTEXT,
                    node_id=node.node_id,
                    edge_id=edge_id,
                    reference=target,
                    message=f"{node.node_id} cites {target}, which is outside its run context ({', '.join(reasons)})",
                    details={**base, "reasons": reasons},
                )
            )
    return findings


def _source_of(node: ProvenanceNode) -> Sequence[str]:
    if isinstance(node, (EvidenceNode, ClaimNode)):
        return (node.source_id,) if isinstance(node.source_id, str) and node.source_id else ()
    return ()


def _check_sources(ctx: _Context) -> list[ValidationFinding]:
    """8.2: every evidence and claim node belongs to a source node of the graph."""
    findings: list[ValidationFinding] = []
    for node, target, edge_id in ctx.references(_BELONGS, _source_of):
        target_node = ctx.nodes.get(target)
        if isinstance(target_node, SourceNode):
            continue
        reason = "absent" if target_node is None else "not_a_source"
        findings.append(
            _finding(
                _SOURCE_NOT_REPRESENTED,
                node_id=node.node_id,
                edge_id=edge_id,
                reference=target,
                message=f"{node.kind} {node.node_id} belongs to {target}, which is not a source node of the graph",
                details={"reference": target, "reference_kind": "source", "reason": reason},
            )
        )
    # A source_id that is not a usable string names no source at all.
    for node in ctx.nodes.values():
        if isinstance(node, (EvidenceNode, ClaimNode)) and not (isinstance(node.source_id, str) and node.source_id):
            findings.append(
                _finding(
                    _SOURCE_NOT_REPRESENTED,
                    node_id=node.node_id,
                    edge_id=None,
                    message=f"{node.kind} {node.node_id} names no source ({_short_repr(node.source_id)})",
                    details={"reference": None, "reason": "absent"},
                )
            )
    return findings


def _check_missing_anchors(ctx: _Context) -> list[ValidationFinding]:
    """8.4: evidence whose anchor is absent."""
    return [
        _finding(
            _MISSING_ANCHOR,
            node_id=node.node_id,
            edge_id=None,
            message=f"evidence {node.node_id} has no usable anchor",
            details={"missing": list(node.anchor.missing)},
        )
        for node in ctx.nodes_of(EvidenceNode)
        if node.anchor.precision == "absent"
    ]


def _identifier_problem(node: ProvenanceNode) -> str | None:
    """Why ``node.node_id`` is not the ID derived from the record's content, or ``None``."""
    try:
        if isinstance(node, SourceNode):
            expected = derive_source_id(node.document_fingerprint)
        elif isinstance(node, EvidenceNode):
            expected = format_evidence_id(node.source_id, node.local_id)
        elif isinstance(node, ClaimNode):
            return _claim_identifier_problem(node)
        elif isinstance(node, DerivationStep):
            expected = derive_derivation_id(node.stage, node.derivation_kind, node.input_ids, node.output_ids)
        elif isinstance(node, ValidationRecord):
            expected = derive_validation_id(node.run_id, node.check_name)
        else:
            return f"{type(node).__name__} is not a provenance node type with a defined identifier"
    except (InvalidIdentifierError, TypeError, ValueError) as exc:
        return f"identifier cannot be derived from the record: {exc}"
    if node.node_id != expected:
        return f"node_id does not match the derived identifier {expected!r}"
    return None


def _claim_identifier_problem(node: ClaimNode) -> str | None:
    base = derive_claim_id(node.source_id, node.field_index)
    if node.node_id == base:
        return None
    suffix = node.node_id[len(base) :] if node.node_id.startswith(base) else ""
    digits = suffix[2:] if suffix.startswith("@r") else ""
    if digits.isascii() and digits.isdigit() and derive_claim_id(node.source_id, node.field_index, int(digits)) == node.node_id:
        return None
    return f"node_id does not match {base!r} or a revision of it ({base}@r<n>)"


def _check_identifiers(ctx: _Context) -> list[ValidationFinding]:
    """8.4: node, edge and run identifiers follow the identity service's shapes."""
    findings: list[ValidationFinding] = []
    if not (
        isinstance(ctx.run_id, str) and ctx.run_id.startswith(RUN_ID_PREFIX) and len(ctx.run_id) > len(RUN_ID_PREFIX)
    ):
        findings.append(
            _finding(
                _INVALID_IDENTIFIER,
                node_id=None,
                edge_id=None,
                message=f"run identifier {_short_repr(ctx.run_id)} is not {RUN_ID_PREFIX!r} plus a digest",
                details={"reference": ctx.run_id, "reason": "malformed run identifier"},
            )
        )
    for node in ctx.nodes.values():
        problem = _identifier_problem(node)
        if problem is not None:
            findings.append(
                _finding(
                    _INVALID_IDENTIFIER,
                    node_id=node.node_id,
                    edge_id=None,
                    message=f"{node.kind} identifier {_short_repr(node.node_id)} is invalid: {problem}",
                    details={"reason": problem},
                )
            )
    for edge in ctx.edges:
        expected = f"{edge.kind}:{edge.from_id}->{edge.to_id}"
        if edge.edge_id != expected:
            reason = f"edge_id does not match the derived identifier {expected!r}"
            findings.append(
                _finding(
                    _INVALID_IDENTIFIER,
                    node_id=None,
                    edge_id=edge.edge_id,
                    message=f"edge identifier {_short_repr(edge.edge_id)} is invalid: {reason}",
                    details={"reason": reason},
                )
            )
    return findings


def _carrier_problem(carrier: object) -> str | None:
    """Why *carrier* does not conform to the declared carrier structure, or ``None`` (9.6).

    Structural only: types and the carrier's own state are inspected, never
    the value of a label or decision (9.4).
    """
    if not isinstance(carrier, PrivacyCarrier):
        return f"privacy is {type(carrier).__name__}, not a PrivacyCarrier"
    state = carrier.state
    if not isinstance(state, str) or state not in _PRIVACY_STATES:
        return f"carrier state {_short_repr(state)} is not one of {sorted(_PRIVACY_STATES)}"
    if state == "rejected":
        reason = carrier.rejection_reason
        return f"carrier was rejected: {reason}" if isinstance(reason, str) and reason else "carrier was rejected"
    if state == "unknown":
        held = [
            name
            for name, value in (
                ("label", carrier.label),
                ("decision", carrier.decision),
                ("rejection_reason", carrier.rejection_reason),
                ("supplied_by", carrier.supplied_by),
                ("supplied_at", carrier.supplied_at),
            )
            if value is not None
        ]
        return f"unknown carrier holds {', '.join(held)}" if held else None
    conforms, reason = check_carrier_conformance({"label": carrier.label, "decision": carrier.decision})
    if not conforms:
        return reason or "labeled carrier does not conform"
    if carrier.rejection_reason is not None:
        return "labeled carrier holds a rejection_reason"
    for name, value in (("supplied_by", carrier.supplied_by), ("supplied_at", carrier.supplied_at)):
        if not isinstance(value, str):
            return f"labeled carrier '{name}' must be a str, got {type(value).__name__}"
    return None


def _check_privacy_carriers(ctx: _Context) -> list[ValidationFinding]:
    """9.6: every node's privacy carrier conforms to the declared structure; nodes are retained."""
    findings: list[ValidationFinding] = []
    for node in ctx.nodes.values():
        problem = _carrier_problem(node.privacy)
        if problem is not None:
            findings.append(
                _finding(
                    _PRIVACY_INVALID,
                    node_id=node.node_id,
                    edge_id=None,
                    message=f"privacy carrier of {node.node_id} does not conform: {problem}",
                    details={"reason": problem},
                )
            )
    return findings


def _check_duplicate_references(ctx: _Context) -> list[ValidationFinding]:
    """5.3, 5.4: a claim cites one evidence node at most once."""
    findings: list[ValidationFinding] = []
    for node in ctx.nodes_of(ClaimNode):
        positions: dict[str, list[int]] = {}
        for position, target in enumerate(node.cited_evidence_ids):
            if not _is_reference(target):
                continue  # reported as a malformed dangling reference
            positions.setdefault(target, []).append(position)
        for target, seen in positions.items():
            if len(seen) > 1:
                findings.append(
                    _finding(
                        _DUPLICATE,
                        node_id=node.node_id,
                        edge_id=None,
                        reference=target,
                        message=f"claim {node.node_id} cites {_short_repr(target)} {len(seen)} times",
                        details={"reference": target, "positions": seen, "reference_kind": _EVIDENCE_REFERENCE},
                    )
                )
    return findings


def _check_unsupported_claims(ctx: _Context) -> list[ValidationFinding]:
    """5.2, 5.3, 5.7: a claim with no evidence reference is unsupported; a prefilled one gets its own code."""
    findings: list[ValidationFinding] = []
    edge_targets: dict[str, set[str]] = {}
    for edge in ctx.edges_of(_CITES):
        edge_targets.setdefault(edge.from_id, set()).add(edge.to_id)
    for node in ctx.nodes_of(ClaimNode):
        cited = {target for target in node.cited_evidence_ids if _is_reference(target)}
        cited |= edge_targets.get(node.node_id, set())
        if node.support == "unsupported" or not cited:
            prefilled = node.origin == _PREFILL_ORIGIN
            findings.append(
                _finding(
                    _PREFILL_UNSUPPORTED if prefilled else _UNSUPPORTED,
                    node_id=node.node_id,
                    edge_id=None,
                    message=(
                        f"{'prefilled ' if prefilled else ''}claim {node.node_id} "
                        "is not supported by any evidence reference"
                    ),
                    details={"support": node.support, "cited_count": len(cited), "origin": node.origin},
                )
            )
    return findings


def _dependency_graph(ctx: _Context) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """``(successors, vertices)`` of the derivation/revision subgraph in dependency direction.

    A step's output that is also one of its own inputs (``input_ids`` or a
    ``has_input`` edge) is an in-place transform and gets no "output depends
    on step" edge; the step still depends on it as an input. ``successors[a]`` lists ``(b, edge_id)`` for each edge making ``a`` depend
    on ``b``; both lists are sorted so the search order is deterministic.
    """
    inputs_of: dict[str, set[str]] = {}
    for node in ctx.nodes_of(DerivationStep):
        inputs_of.setdefault(node.node_id, set()).update(_usable_ids(node.input_ids))
    for edge in ctx.edges:
        if edge.kind == _HAS_INPUT:
            inputs_of.setdefault(edge.from_id, set()).add(edge.to_id)

    successors: dict[str, list[tuple[str, str]]] = {}
    vertices: set[str] = set()
    for edge in ctx.edges:
        if edge.from_id not in ctx.nodes:
            continue  # debris of a record that failed to materialize
        if edge.kind in _DEPENDS_ON_TARGET:
            dependent, dependency = edge.from_id, edge.to_id
        elif edge.kind in _TARGET_DEPENDS_ON_ORIGIN:
            if edge.to_id in inputs_of.get(edge.from_id, ()):
                continue  # in-place transform: the step kept (filtered, annotated, ...) one of its inputs
            dependent, dependency = edge.to_id, edge.from_id
        else:
            continue
        successors.setdefault(dependent, []).append((dependency, edge.edge_id))
        vertices.update((dependent, dependency))
    for targets in successors.values():
        targets.sort()
    return successors, sorted(vertices)


def _strongly_connected(successors: Mapping[str, Sequence[tuple[str, str]]], vertices: Sequence[str]) -> list[list[str]]:
    """Tarjan's strongly connected components, iteratively (explicit work stack, no recursion)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0
    for root in vertices:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work: list[tuple[str, Iterator[tuple[str, str]]]] = [(root, iter(successors.get(root, ())))]
        while work:
            vertex, pending = work[-1]
            descended = False
            for successor, _edge_id in pending:
                if successor not in index:
                    index[successor] = low[successor] = counter
                    counter += 1
                    stack.append(successor)
                    on_stack.add(successor)
                    work.append((successor, iter(successors.get(successor, ()))))
                    descended = True
                    break
                if successor in on_stack:
                    low[vertex] = min(low[vertex], index[successor])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[vertex])
            if low[vertex] == index[vertex]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == vertex:
                        break
                components.append(component)
    return components


def _check_cycles(ctx: _Context) -> list[ValidationFinding]:
    """8.4: the derivation and revision subgraph is acyclic; one finding per cyclic component."""
    successors, vertices = _dependency_graph(ctx)
    findings: list[ValidationFinding] = []
    for component in _strongly_connected(successors, vertices):
        members = set(component)
        edge_ids = sorted(
            edge_id
            for member in component
            for successor, edge_id in successors.get(member, ())
            if successor in members
        )
        if not edge_ids:
            continue  # a single vertex without a self-loop is not a cycle
        ordered = sorted(members)
        subject = next((member for member in ordered if member in ctx.nodes), ordered[0])
        findings.append(
            _finding(
                _CYCLE,
                node_id=subject,
                edge_id=None,
                message=(
                    f"derivation/revision cycle of {len(ordered)} record(s) through {subject}: "
                    "a record is derived from itself"
                ),
                details={"members": ordered, "edge_ids": edge_ids},
            )
        )
    return findings


def _check_orphans(ctx: _Context) -> list[ValidationFinding]:
    """8.4: a node connected by no edge to any other node of the graph (validation records exempt)."""
    connected: set[str] = set()
    incident: dict[str, list[str]] = {}
    for edge in ctx.edges:
        if edge.kind == _VALIDATES:
            continue
        for end in {edge.from_id, edge.to_id}:
            incident.setdefault(end, []).append(edge.edge_id)
        if edge.from_id != edge.to_id and edge.from_id in ctx.nodes and edge.to_id in ctx.nodes:
            connected.update((edge.from_id, edge.to_id))
    return [
        _finding(
            _ORPHAN,
            node_id=node.node_id,
            edge_id=None,
            message=f"{node.kind} {node.node_id} is connected to no other node of the graph",
            details={"node_kind": node.kind, "incident_edge_ids": sorted(incident.get(node.node_id, ()))},
        )
        for node in ctx.nodes.values()
        if node.node_id not in connected and not isinstance(node, ValidationRecord)
    ]


def _usable_ids(ids: object) -> list[str]:
    if not isinstance(ids, (tuple, list)):
        return []
    return [identifier for identifier in ids if isinstance(identifier, str) and identifier]


def _check_inconsistent_derivations(ctx: _Context) -> list[ValidationFinding]:
    """8.4: a derivation step has at least one input or at least one output."""
    return [
        _finding(
            _INCONSISTENT,
            node_id=node.node_id,
            edge_id=None,
            message=f"derivation step {node.node_id} has neither an input nor an output",
            details={
                "stage": node.stage,
                "derivation_kind": node.derivation_kind,
                "input_ids": [_short_repr(i) for i in node.input_ids] if isinstance(node.input_ids, (tuple, list)) else [],
                "output_ids": [_short_repr(o) for o in node.output_ids] if isinstance(node.output_ids, (tuple, list)) else [],
            },
        )
        for node in ctx.nodes_of(DerivationStep)
        if not _usable_ids(node.input_ids) and not _usable_ids(node.output_ids)
    ]


_Check = Callable[[_Context], Iterable[ValidationFinding]]

_CHECKS: tuple[tuple[str, _Check], ...] = (
    (_DANGLING, _check_dangling_edges),
    ("evidence_references", _check_evidence_references),
    (_SOURCE_NOT_REPRESENTED, _check_sources),
    (_MISSING_ANCHOR, _check_missing_anchors),
    (_INVALID_IDENTIFIER, _check_identifiers),
    (_PRIVACY_INVALID, _check_privacy_carriers),
    (_DUPLICATE, _check_duplicate_references),
    (_UNSUPPORTED, _check_unsupported_claims),
    (_CYCLE, _check_cycles),
    (_ORPHAN, _check_orphans),
    (_INCONSISTENT, _check_inconsistent_derivations),
)
"""``(check name, check)`` in run order; each check runs inside its own guard."""


# ---------------------------------------------------------------------------
# Merging recorded findings
# ---------------------------------------------------------------------------


def _well_formed(finding: object) -> bool:
    return (
        isinstance(finding, ValidationFinding)
        and isinstance(finding.finding_id, str)
        and bool(finding.finding_id)
        and isinstance(finding.code, str)
        and finding.severity in _SEVERITY_ORDER
        and isinstance(finding.details, Mapping)
    )


def _claim_source(node_id: str) -> str | None:
    """The source ID inside a claim ID ``claim:<source_id>#f<n>[@r<k>]``, if *node_id* is one."""
    if not node_id.startswith(CLAIM_ID_PREFIX):
        return None
    source_id, separator, _ = node_id[len(CLAIM_ID_PREFIX) :].partition(EVIDENCE_SCOPE_SEPARATOR)
    return source_id if separator and source_id else None


def _problem_key(finding: ValidationFinding) -> tuple[Any, ...]:
    """The de-duplication key of *finding*: its reference problem, else its ``finding_id``."""
    reference = finding.details.get("reference")
    if not isinstance(reference, str) or not reference:
        return ("id", finding.finding_id)
    kind = finding.details.get("reference_kind", _EVIDENCE_REFERENCE)
    kind = kind if isinstance(kind, str) else _EVIDENCE_REFERENCE
    normalized = reference
    if kind == _EVIDENCE_REFERENCE and EVIDENCE_SCOPE_SEPARATOR not in reference and finding.node_id:
        source_id = _claim_source(finding.node_id)
        if source_id is not None:
            try:
                normalized = format_evidence_id(source_id, reference)
            except InvalidIdentifierError:
                normalized = reference
    return ("reference", finding.code, finding.node_id, normalized, kind)


def _merge(findings: Iterable[ValidationFinding]) -> list[ValidationFinding]:
    """De-duplicate by ``finding_id`` (first kept), then by reference problem (smallest ID kept)."""
    by_id: dict[str, ValidationFinding] = {}
    for finding in findings:
        by_id.setdefault(finding.finding_id, finding)
    groups: dict[tuple[Any, ...], list[ValidationFinding]] = {}
    for finding in by_id.values():
        groups.setdefault(_problem_key(finding), []).append(finding)
    merged: list[ValidationFinding] = []
    for group in groups.values():
        group.sort(key=lambda f: f.finding_id)
        kept = group[0]
        if len(group) > 1:
            others = [f.finding_id for f in group[1:]]
            kept = ValidationFinding(
                finding_id=kept.finding_id,
                code=kept.code,
                severity=kept.severity,
                node_id=kept.node_id,
                edge_id=kept.edge_id,
                message=kept.message,
                details={**kept.details, "merged_finding_ids": others},
            )
        merged.append(kept)
    return merged


# ---------------------------------------------------------------------------
# Severity policy and ordering (8.5)
# ---------------------------------------------------------------------------


def _severity_policy(overrides: object) -> dict[str, Severity]:
    """The default severity table updated by every usable override entry; never raises."""
    policy: dict[str, Severity] = dict(DEFAULT_SEVERITIES)
    if overrides is None:
        return policy
    if not isinstance(overrides, Mapping):
        logger.warning(
            "provenance severity overrides ignored: expected a mapping of finding code to severity, got %s",
            type(overrides).__name__,
        )
        return policy
    try:
        entries = list(overrides.items())
    except Exception as exc:  # noqa: BLE001 — an unreadable mapping must not abort validation
        logger.warning("provenance severity overrides ignored: mapping cannot be read: %s: %s", type(exc).__name__, exc)
        return policy
    for code, severity in entries:
        if not isinstance(code, str) or not code:
            logger.warning(
                "provenance severity override %s -> %s ignored: the finding code is not a non-empty string",
                _short_repr(code),
                _short_repr(severity),
            )
            continue
        if not isinstance(severity, str) or severity not in _SEVERITY_RANK:
            logger.warning(
                "provenance severity override %r -> %s ignored: severity must be one of %s",
                code,
                _short_repr(severity),
                ", ".join(_SEVERITY_ORDER),
            )
            continue
        if code not in DEFAULT_SEVERITIES:
            logger.warning(
                "provenance severity override names unknown finding code %r; it applies only to recorded "
                "findings with that code",
                code,
            )
        policy[code] = severity  # type: ignore[assignment]
    return policy


def _classify(finding: ValidationFinding, policy: Mapping[str, Severity]) -> ValidationFinding:
    """``finding`` stamped with the policy severity of its code (its own severity for an unknown code)."""
    severity = policy.get(finding.code, finding.severity)
    return finding if severity == finding.severity else dataclasses.replace(finding, severity=severity)


def _order_key(finding: ValidationFinding) -> tuple[int, str, str, str, str]:
    return (
        _SEVERITY_RANK.get(finding.severity, len(_SEVERITY_RANK)),
        finding.code,
        finding.node_id or "",
        finding.edge_id or "",
        finding.finding_id,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _report(
    findings: Iterable[ValidationFinding],
    problems: Sequence[str],
    policy: Mapping[str, Severity],
) -> ChainValidationReport:
    ordered = tuple(sorted((_classify(finding, policy) for finding in _merge(findings)), key=_order_key))
    counts = Counter(finding.severity for finding in ordered)
    report = ChainValidationReport(
        completed=not problems,
        incompletion_reason="; ".join(problems) if problems else None,
        findings=ordered,
        counts_by_severity={severity: counts[severity] for severity in _SEVERITY_ORDER if counts[severity]},
    )
    for finding in report.findings:
        logger.debug("chain validation %s [%s] %s: %s", finding.finding_id, finding.severity, finding.code, finding.message)
    if not report.completed:
        logger.warning("provenance chain validation not completed; run is unvalidated: %s", report.incompletion_reason)
    return report


def _recorded(recorded_findings: object, problems: list[str]) -> list[ValidationFinding]:
    if isinstance(recorded_findings, (str, bytes)) or not isinstance(recorded_findings, Sequence):
        problems.append(
            f"recorded findings cannot be read: expected a sequence of findings, got {type(recorded_findings).__name__}"
        )
        return []
    usable = [finding for finding in recorded_findings if _well_formed(finding)]
    rejected = len(recorded_findings) - len(usable)
    if rejected:
        problems.append(f"{rejected} recorded finding(s) are not well-formed validation findings and cannot be interpreted")
    return usable


def validate_chain(
    graph: ProvenanceGraph,
    *,
    severity_overrides: Mapping[str, Severity] | None = None,
    recorded_findings: Sequence[ValidationFinding] = (),
) -> ChainValidationReport:
    """Validate the provenance chain of ``graph`` into a populated report (5.3, 5.4, 8.1–8.6, 9.6).

    ``severity_overrides`` maps a finding code to the severity that replaces
    its default (``provenance.severity_overrides``); unusable entries are
    ignored with a WARNING. ``recorded_findings`` are the findings recorded
    during the run (``RecordedRun.findings``); they are merged with the
    graph-derived findings as described in the module docstring. Every finding
    is classified by the effective severity policy, counted per severity and
    ordered by ``(severity_rank, code, node_id)``.

    Never raises. ``completed`` is ``False`` — the run is unvalidated, never
    valid — whenever a check could not run or an input could not be
    interpreted, and ``incompletion_reason`` then names each problem. The graph
    is not modified.
    """
    problems: list[str] = []
    findings: list[ValidationFinding] = []
    try:
        policy = _severity_policy(severity_overrides)
        findings.extend(_recorded(recorded_findings, problems))
        if not isinstance(graph, ProvenanceGraph):
            problems.append(f"validation cannot run: expected a ProvenanceGraph, got {type(graph).__name__}")
            return _report(findings, problems, policy)
        try:
            ctx = _Context(graph)
        except _CannotRun as exc:
            problems.append(f"validation cannot run: {exc}")
            return _report(findings, problems, policy)
        except Exception as exc:  # noqa: BLE001 — an unreadable graph leaves the run unvalidated
            problems.append(f"validation cannot run: graph indexes unavailable: {type(exc).__name__}: {exc}")
            return _report(findings, problems, policy)
        graph_findings: list[ValidationFinding] = []
        for name, check in _CHECKS:
            try:
                produced = list(check(ctx))
            except Exception as exc:  # noqa: BLE001 — a check that cannot run leaves the run unvalidated
                problems.append(f"check {name!r} could not run: {type(exc).__name__}: {exc}")
                continue
            graph_findings.extend(produced)
        # Graph-derived findings first, so a recorded finding never displaces one with the same ID.
        return _report([*graph_findings, *findings], problems, policy)
    except Exception as exc:  # noqa: BLE001 — validate_chain never raises
        logger.warning("provenance chain validation failed unexpectedly: %s", exc)
        return ChainValidationReport(
            completed=False,
            incompletion_reason=f"validation failed unexpectedly: {type(exc).__name__}: {exc}",
            findings=(),
            counts_by_severity={},
        )
