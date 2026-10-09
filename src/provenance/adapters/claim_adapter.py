"""claim_adapter — reconstructed field mappings into ``ClaimNode`` records.

Consumes the plain field mappings built by ``reconstruct_fields`` in
``src/pipeline/validator.py`` (and the prefilled fields built the same way in
``src/pipeline/pdf_processor.py``) without importing ``pipeline``:

========================== ====================================================
Field key                  Use
========================== ====================================================
``field_index``            ``ClaimNode.field_index``; with ``revision`` it
                           forms ``node_id`` via
                           :func:`provenance.identity.derive_claim_id`
``field_name``             ``ClaimNode.field_name`` (``""`` when not a string)
``extracted_value``        digested into ``ClaimNode.value_digest``; the value
                           itself is never stored
``location``               the evidence references (pipeline-local evidence
                           IDs, or fully-qualified evidence node IDs)
``confidence``             ``ClaimNode.confidence`` (``None`` when not a string)
``revision``               optional, default ``0``: the claim's revision number
``contributing_claim_ids`` optional: claim node IDs this claim was revised,
                           synthesized or merged from (5.5)
any other key              ignored — notably ``evidence`` (text, never stored on
                           a claim) and ``location_metadata`` (the evidence
                           nodes carry the anchors)
========================== ====================================================

Reference resolution (5.1, 5.4). A reference without ``#`` is a local ID and is
scoped to the claim's source by :func:`~provenance.identity.format_evidence_id`;
a reference with ``#`` is taken as an evidence node ID. A resolved ID that is
in ``known_evidence_ids`` is cited: it lands in ``cited_evidence_ids`` and gets
one ``cites_evidence`` edge. Every other reference is reported as a finding
naming the claim (``node_id``), the offending reference (``details``) and the
issue kind (``code``); the claim itself is always retained:

- ``dangling_reference`` — unknown: not a usable identifier (``reason`` is
  ``"malformed"``) or no known evidence node has that ID (``"unknown"``);
- ``ambiguous_reference`` — not an exact evidence ID, but equal to one or more
  known IDs of the claim's source after trimming whitespace and ignoring case.
  The reference does not identify a node unambiguously, so nothing is cited;
  ``details["candidates"]`` lists the nodes it might mean;
- ``duplicate_reference`` — the same evidence node (or the same unresolved
  reference) cited more than once; it is cited once and one finding lists
  every position.

Support classification (5.2, 5.6), per the claim-support state machine:

- ``unsupported`` — no reference resolves to known evidence (no reference at
  all, or only references reported as findings above);
- ``missing_anchor`` — no cited evidence node has a usable anchor;
- ``supported`` — at least one cited evidence node has a usable anchor.

A cited node's anchor is usable when ``evidence_anchors`` (required) maps its
node ID to a :class:`~provenance.models.SourceAnchor` whose ``precision`` is
not ``"absent"``; an ``approximate`` anchor is usable even though its
``missing`` tuple is non-empty. The check fails closed: a cited node with no
entry in ``evidence_anchors``, or whose entry is not a ``SourceAnchor``, counts
as anchorless, so a missing anchor is never reported as support.

Origin (5.7). Every claim of one call carries the caller-supplied ``origin``;
fields produced without a model call (the TEI-prefilled fields) are converted
with ``origin="prefill"``. Such a field has ``location=[]`` and therefore
classifies ``unsupported`` like any other claim with no reference; its
``origin`` is what distinguishes it.

Revisions (5.5). Each entry of ``contributing_claim_ids`` gets one ``revises``
edge from the resulting claim to the contributing claim. An entry that is not
a string starting with ``claim:`` is an ``invalid_identifier`` finding; a repeated one a ``duplicate_reference``
finding with ``reference_kind="contributing_claim"``.

Totality. A field that cannot be identified (not a mapping; ``field_index`` not
a non-negative ``int``; ``revision`` present but not a non-negative ``int``)
cannot become a claim; it is reported as an ``invalid_identifier`` finding
(``node_id=None``, ``details`` naming the source and position) instead of being
dropped silently. Each finding is logged at DEBUG (identifiers and positions
only — never values or text), and a call that produced findings logs one
WARNING with the counts and the ``source_id``.

Finding IDs are deterministic and distinct from recorder-generated ones:
``finding:<code>:<claim_id>:loc[<pos>]`` for an evidence reference,
``finding:<code>:<claim_id>:rev[<pos>]`` for a contributing claim, and
``finding:invalid_identifier:<source_id>:field[<pos>]`` for an unidentifiable
field.

The value digest is :func:`provenance.identity.derive_value_digest` (the
identity module is the only provenance module that hashes); this module
imports no hashing library. Every
node carries :data:`~provenance.privacy_carrier.UNKNOWN_PRIVACY` and no
extensions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from typing import Any, get_args

from provenance.identity import (
    CLAIM_ID_PREFIX,
    EVIDENCE_SCOPE_SEPARATOR,
    InvalidIdentifierError,
    derive_claim_id,
    derive_value_digest,
    format_evidence_id,
    parse_evidence_id,
)
from provenance.models import (
    DEFAULT_SEVERITIES,
    ClaimNode,
    ClaimOrigin,
    ClaimSupport,
    EdgeKind,
    ProvenanceEdge,
    SourceAnchor,
    ValidationFinding,
)
from provenance.privacy_carrier import UNKNOWN_PRIVACY
from utils.logging_utils import get_logger

logger = get_logger(__name__)

__all__ = ["claim_nodes_from_fields"]

# Key names emitted by reconstruct_fields (src/pipeline/validator.py).
_FIELD_INDEX_KEY = "field_index"
_FIELD_NAME_KEY = "field_name"
_VALUE_KEY = "extracted_value"
_LOCATION_KEY = "location"
_CONFIDENCE_KEY = "confidence"
# Optional revision keys defined by this adapter (5.5).
_REVISION_KEY = "revision"
_CONTRIBUTORS_KEY = "contributing_claim_ids"

_CLAIM_ORIGINS: frozenset[str] = frozenset(get_args(ClaimOrigin))

_DANGLING = "dangling_reference"
_AMBIGUOUS = "ambiguous_reference"
_DUPLICATE = "duplicate_reference"
_INVALID_IDENTIFIER = "invalid_identifier"

_FINDING_ID_PREFIX = "finding:"

_REPR_LIMIT = 80
"""Longest ``repr`` of an identifier written to a finding message or the log."""

_UNRESOLVED = object()
"""Sentinel: a reference that is not a usable evidence identifier."""


def _short_repr(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _REPR_LIMIT else text[: _REPR_LIMIT - 3] + "..."


def _is_index(value: object) -> bool:
    # ``bool`` is an ``int`` subclass; a flag is never an index or a revision.
    return type(value) is int and value >= 0  # type: ignore[operator]


def _as_list(value: object) -> list[Any]:
    """Return the references in *value*: ``None`` → none; a string → one; a list/tuple → its items."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return list(value)
    return [value]  # one malformed reference, reported rather than dropped


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _finding(code: str, finding_id: str, *, node_id: str | None, message: str, details: dict[str, Any]) -> ValidationFinding:
    finding = ValidationFinding(
        finding_id=finding_id,
        code=code,
        severity=DEFAULT_SEVERITIES[code],
        node_id=node_id,
        edge_id=None,
        message=message,
        details=details,
    )
    logger.debug("claim adapter finding %s [%s]: %s", finding_id, finding.severity, message)
    return finding


def _edge(kind: EdgeKind, from_id: str, to_id: str) -> ProvenanceEdge:
    return ProvenanceEdge(edge_id=f"{kind}:{from_id}->{to_id}", kind=kind, from_id=from_id, to_id=to_id, attributes={})


# ---------------------------------------------------------------------------
# Evidence references
# ---------------------------------------------------------------------------


class _EvidenceIndex:
    """Exact and tolerant lookup over the known evidence IDs of one source."""

    def __init__(self, source_id: str, known: AbstractSet[str]) -> None:
        self._source_id = source_id
        self._known = known
        self._folded: dict[str, list[str]] = {}
        for node_id in known:
            if not isinstance(node_id, str):
                continue
            try:
                node_source, local_id = parse_evidence_id(node_id)
            except InvalidIdentifierError:
                continue
            if node_source == source_id:
                self._folded.setdefault(local_id.strip().casefold(), []).append(node_id)

    def resolve(self, reference: object) -> object:
        """Return the evidence node ID *reference* denotes, or ``_UNRESOLVED`` when it is malformed."""
        if not isinstance(reference, str):
            return _UNRESOLVED
        try:
            if EVIDENCE_SCOPE_SEPARATOR in reference:
                parse_evidence_id(reference)
                return reference
            return format_evidence_id(self._source_id, reference)
        except InvalidIdentifierError:
            return _UNRESOLVED

    def is_known(self, node_id: str) -> bool:
        return node_id in self._known

    def candidates(self, reference: str) -> list[str]:
        """Known IDs of this source equal to *reference*'s local part up to whitespace and case."""
        local_id = reference
        if EVIDENCE_SCOPE_SEPARATOR in reference:
            node_source, local_id = parse_evidence_id(reference)
            if node_source != self._source_id:
                return []
        return sorted(self._folded.get(local_id.strip().casefold(), []))


def _cite(
    claim_id: str,
    field_index: int,
    references: list[Any],
    index: _EvidenceIndex,
    findings: list[ValidationFinding],
) -> list[str]:
    """Return the cited evidence node IDs, in first-reference order; append reference findings."""
    cited: list[str] = []
    positions: dict[object, list[int]] = {}
    first_reference: dict[object, Any] = {}
    for position, reference in enumerate(references):
        resolved = index.resolve(reference)
        key: object = resolved if resolved is not _UNRESOLVED else ("unresolved", _short_repr(reference))
        if key in positions:
            positions[key].append(position)
            continue
        positions[key] = [position]
        first_reference[key] = reference
        details: dict[str, Any] = {"reference": reference, "position": position, "field_index": field_index}
        finding_id = f"{_FINDING_ID_PREFIX}{{code}}:{claim_id}:loc[{position}]"

        if resolved is _UNRESOLVED:
            findings.append(
                _finding(
                    _DANGLING,
                    finding_id.format(code=_DANGLING),
                    node_id=claim_id,
                    message=f"claim {claim_id} cites {_short_repr(reference)}, which is not an evidence identifier",
                    details={**details, "reason": "malformed"},
                )
            )
        elif index.is_known(resolved):  # type: ignore[arg-type]
            cited.append(resolved)  # type: ignore[arg-type]
        elif candidates := index.candidates(reference):
            findings.append(
                _finding(
                    _AMBIGUOUS,
                    finding_id.format(code=_AMBIGUOUS),
                    node_id=claim_id,
                    message=(
                        f"claim {claim_id} cites {_short_repr(reference)}, which is not an exact evidence "
                        f"identifier but may mean {len(candidates)} known evidence node(s)"
                    ),
                    details={**details, "candidates": candidates},
                )
            )
        else:
            findings.append(
                _finding(
                    _DANGLING,
                    finding_id.format(code=_DANGLING),
                    node_id=claim_id,
                    message=f"claim {claim_id} cites {_short_repr(reference)}, which is not a known evidence node",
                    details={**details, "reason": "unknown"},
                )
            )

    for key, seen in positions.items():
        if len(seen) > 1:
            reference = first_reference[key]
            findings.append(
                _finding(
                    _DUPLICATE,
                    f"{_FINDING_ID_PREFIX}{_DUPLICATE}:{claim_id}:loc[{seen[1]}]",
                    node_id=claim_id,
                    message=f"claim {claim_id} cites {_short_repr(reference)} {len(seen)} times; cited once",
                    details={
                        "reference": reference,
                        "positions": seen,
                        "field_index": field_index,
                        "reference_kind": "evidence",
                    },
                )
            )
    return cited


def _classify(cited: list[str], evidence_anchors: Mapping[str, SourceAnchor]) -> ClaimSupport:
    """Classify per the claim-support state machine; an unmapped or non-anchor entry is anchorless."""
    if not cited:
        return "unsupported"
    for node_id in cited:
        anchor = evidence_anchors.get(node_id)
        if isinstance(anchor, SourceAnchor) and anchor.precision != "absent":
            return "supported"
    return "missing_anchor"


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


def _revise(
    claim_id: str,
    field_index: int,
    contributors: list[Any],
    findings: list[ValidationFinding],
) -> list[ProvenanceEdge]:
    edges: list[ProvenanceEdge] = []
    positions: dict[str, list[int]] = {}
    for position, contributor in enumerate(contributors):
        if (
            not isinstance(contributor, str)
            or not contributor.startswith(CLAIM_ID_PREFIX)
            or len(contributor) == len(CLAIM_ID_PREFIX)
        ):
            findings.append(
                _finding(
                    _INVALID_IDENTIFIER,
                    f"{_FINDING_ID_PREFIX}{_INVALID_IDENTIFIER}:{claim_id}:rev[{position}]",
                    node_id=claim_id,
                    message=(
                        f"claim {claim_id} names contributing claim {_short_repr(contributor)}, "
                        "which is not a claim identifier"
                    ),
                    details={
                        "reference": contributor,
                        "position": position,
                        "field_index": field_index,
                        "reference_kind": "contributing_claim",
                    },
                )
            )
            continue
        if contributor in positions:
            positions[contributor].append(position)
            continue
        positions[contributor] = [position]
        edges.append(_edge("revises", claim_id, contributor))

    for contributor, seen in positions.items():
        if len(seen) > 1:
            findings.append(
                _finding(
                    _DUPLICATE,
                    f"{_FINDING_ID_PREFIX}{_DUPLICATE}:{claim_id}:rev[{seen[1]}]",
                    node_id=claim_id,
                    message=f"claim {claim_id} names contributing claim {contributor!r} {len(seen)} times; linked once",
                    details={
                        "reference": contributor,
                        "positions": seen,
                        "field_index": field_index,
                        "reference_kind": "contributing_claim",
                    },
                )
            )
    return edges


# ---------------------------------------------------------------------------
# Argument checks (caller errors raise; field defects do not)
# ---------------------------------------------------------------------------


def _check_context(
    fields: object,
    source_id: object,
    run_id: object,
    known_evidence_ids: object,
    origin: object,
    evidence_anchors: object,
) -> None:
    if isinstance(fields, (str, bytes)) or not isinstance(fields, Sequence):
        raise TypeError(f"fields must be a sequence of mappings, got {type(fields).__name__}")
    derive_claim_id(source_id, 0)  # type: ignore[arg-type]  # raises on a malformed source_id
    if not isinstance(run_id, str) or not run_id:
        raise InvalidIdentifierError(f"run_id must be a non-empty string, got {run_id!r}")
    if not isinstance(known_evidence_ids, AbstractSet):
        raise TypeError(f"known_evidence_ids must be a set, got {type(known_evidence_ids).__name__}")
    if not isinstance(origin, str) or origin not in _CLAIM_ORIGINS:
        raise ValueError(f"origin must be one of {sorted(_CLAIM_ORIGINS)}, got {origin!r}")
    if not isinstance(evidence_anchors, Mapping):
        raise TypeError(f"evidence_anchors must be a mapping, got {type(evidence_anchors).__name__}")


def _claim_id(field: object, source_id: str) -> tuple[str, int] | str:
    """Return ``(claim_id, field_index)``, or the reason the field cannot be identified."""
    if not isinstance(field, Mapping):
        return f"field is not a mapping ({type(field).__name__})"
    field_index = field.get(_FIELD_INDEX_KEY)
    if not _is_index(field_index):
        return f"{_FIELD_INDEX_KEY} {_short_repr(field_index)} is not a non-negative int"
    revision = field.get(_REVISION_KEY, 0)
    if not _is_index(revision):
        return f"{_REVISION_KEY} {_short_repr(revision)} is not a non-negative int"
    return derive_claim_id(source_id, field_index, revision), field_index  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Public adapter
# ---------------------------------------------------------------------------


def claim_nodes_from_fields(
    fields: Sequence[Mapping[str, Any]],
    *,
    source_id: str,
    run_id: str,
    known_evidence_ids: AbstractSet[str],
    origin: ClaimOrigin,
    evidence_anchors: Mapping[str, SourceAnchor],
) -> tuple[list[ClaimNode], list[ProvenanceEdge], list[ValidationFinding]]:
    """Convert reconstructed fields into claim nodes, edges and findings (5.1, 5.2, 5.5, 5.6, 5.7).

    Returns ``(claims, edges, findings)``: claims in field order; for each
    claim its ``cites_evidence`` edges in reference order, then its
    ``revises`` edges; findings in the order found. ``known_evidence_ids``
    holds evidence node IDs (``<source_id>#<local_id>``) that exist in the
    run; ``evidence_anchors`` (required) maps evidence node IDs to their
    anchors. A cited node absent from ``evidence_anchors`` counts as
    anchorless, so a claim citing only such nodes is ``missing_anchor``.

    Never raises on a field. Raises only on caller errors: ``fields`` not a
    sequence, ``known_evidence_ids`` not a set, ``evidence_anchors`` not a
    mapping, or ``evidence_anchors`` omitted (``TypeError``); a malformed ``source_id``
    or ``run_id`` (``InvalidIdentifierError``); an ``origin`` outside
    ``ClaimOrigin`` (``ValueError``). Fields sharing one claim ID are all
    converted; the recorder decides how to treat a repeated ``node_id``.
    """
    _check_context(fields, source_id, run_id, known_evidence_ids, origin, evidence_anchors)
    index = _EvidenceIndex(source_id, known_evidence_ids)

    claims: list[ClaimNode] = []
    edges: list[ProvenanceEdge] = []
    findings: list[ValidationFinding] = []
    skipped = 0
    for position, field in enumerate(fields):
        identified = _claim_id(field, source_id)
        if isinstance(identified, str):
            skipped += 1
            findings.append(
                _finding(
                    _INVALID_IDENTIFIER,
                    f"{_FINDING_ID_PREFIX}{_INVALID_IDENTIFIER}:{source_id}:field[{position}]",
                    node_id=None,
                    message=f"field at position {position} of {source_id} cannot become a claim: {identified}",
                    details={"source_id": source_id, "position": position, "reason": identified},
                )
            )
            continue
        claim_id, field_index = identified
        assert isinstance(field, Mapping)

        cited = _cite(claim_id, field_index, _as_list(field.get(_LOCATION_KEY)), index, findings)
        revision_edges = _revise(claim_id, field_index, _as_list(field.get(_CONTRIBUTORS_KEY)), findings)

        field_name = field.get(_FIELD_NAME_KEY)
        if not isinstance(field_name, str):
            logger.debug("claim %s: %s %s is not a string; recorded as ''", claim_id, _FIELD_NAME_KEY, _short_repr(field_name))
            field_name = ""
        confidence = field.get(_CONFIDENCE_KEY)
        if confidence is not None and not isinstance(confidence, str):
            logger.debug("claim %s: %s %s is not a string; recorded as None", claim_id, _CONFIDENCE_KEY, _short_repr(confidence))
            confidence = None

        claims.append(
            ClaimNode(
                node_id=claim_id,
                kind="claim",
                run_id=run_id,
                privacy=UNKNOWN_PRIVACY,
                extensions={},
                source_id=source_id,
                field_index=field_index,
                field_name=field_name,
                value_digest=derive_value_digest(field.get(_VALUE_KEY)),
                origin=origin,
                support=_classify(cited, evidence_anchors),
                cited_evidence_ids=tuple(cited),
                confidence=confidence,
            )
        )
        edges.extend(_edge("cites_evidence", claim_id, node_id) for node_id in cited)
        edges.extend(revision_edges)

    if findings:
        logger.warning(
            "claim adapter for %s: %d field(s) converted, %d unidentifiable field(s) skipped, "
            "%d finding(s) recorded",
            source_id,
            len(claims),
            skipped,
            len(findings),
        )
    return claims, edges, findings
