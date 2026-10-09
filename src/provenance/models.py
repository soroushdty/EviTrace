"""models — single owner of every provenance record type, vocabulary and cross-layer type.

``models.py`` owns (provenance-core design, "ProvenanceModels"):

(a) every provenance *record* type — :class:`SourceAnchor`, the node base
    :class:`ProvenanceNode` and its five node types, :class:`ProvenanceEdge`,
    and :class:`RunIdentity`;
(b) every closed vocabulary — ``Literal`` unions, closed by design (7.1);
    adding a member is a Revalidation Trigger;
(c) every type consumed by a layer other than the one that produces it —
    :class:`ValidationFinding`, :data:`DEFAULT_SEVERITIES`,
    :class:`MissingSegment`, :class:`StageContract`,
    :class:`ArtifactCompleteness`, :class:`RecordedRun`,
    :class:`ChainValidationReport` and :class:`CompletenessReport`. They live
    here so that every import points leftward in the dependency order: the
    recorder and the claim adapter build findings and snapshots before the
    validation layer exists, and the graph container carries both reports
    before the modules that compute them.

Outside this module only ``privacy_carrier.py`` (``PrivacyCarrier``),
``graph.py`` (``ProvenanceGraph``) and ``projection.py`` (``ProvenanceEvent``)
declare a provenance dataclass.

Every record is ``@dataclass(frozen=True)``, hashable, and equal by value.
Mapping-valued fields are copied at construction into read-only mappings
(nested mappings and lists frozen likewise), so a record cannot be changed
after construction — through attribute assignment *or* through a mapping it
holds — and does not alias the caller's dict. The read-only containers
subclass ``dict``/``list``, so they compare equal to plain values and remain
JSON-serializable, picklable and ``dataclasses.asdict``-compatible.

Every node embeds one :class:`~provenance.privacy_carrier.PrivacyCarrier`
(9.1) and carries ``extensions`` whose keys must follow the ``x-<namespace>-<field>``
convention; a non-conforming key is rejected at construction (10.4), which
keeps extension metadata visibly distinct from core fields.

A leaf module: it imports only the standard library and
``provenance.privacy_carrier``.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NoReturn

from provenance.privacy_carrier import UNKNOWN_PRIVACY, PrivacyCarrier

__all__ = [
    # vocabularies
    "NodeKind",
    "EdgeKind",
    "EvidenceKind",
    "AnchorPrecision",
    "DerivationKind",
    "Determinism",
    "Severity",
    "CompletenessState",
    "ClaimSupport",
    "ClaimOrigin",
    # record types
    "SourceAnchor",
    "ProvenanceNode",
    "SourceNode",
    "EvidenceNode",
    "ClaimNode",
    "DerivationStep",
    "ValidationRecord",
    "ProvenanceEdge",
    "RunIdentity",
    # cross-layer types
    "ValidationFinding",
    "DEFAULT_SEVERITIES",
    "MissingSegment",
    "StageContract",
    "ArtifactCompleteness",
    "RecordedRun",
    "ChainValidationReport",
    "CompletenessReport",
    # extension-key convention
    "EXTENSION_KEY_PATTERN",
    "is_extension_key",
    # re-exported carrier (owned by privacy_carrier.py)
    "PrivacyCarrier",
    "UNKNOWN_PRIVACY",
]

# ---------------------------------------------------------------------------
# Closed vocabularies (7.1)
# ---------------------------------------------------------------------------

NodeKind = Literal["source", "evidence", "claim", "derivation", "validation"]
EdgeKind = Literal[
    "cites_evidence",
    "anchored_in",
    "derived_from",
    "has_input",
    "has_output",
    "revises",
    "validates",
    "belongs_to_source",
]
EvidenceKind = Literal["sentence", "figure_caption", "table", "metadata"]
AnchorPrecision = Literal["exact", "approximate", "absent"]
DerivationKind = Literal[
    "extract",
    "normalize",
    "chunk",
    "filter",
    "merge",
    "synthesize",
    "reconcile",
    "remove",
    "prefill",
    "annotate",
]
Determinism = Literal["deterministic", "probabilistic"]
Severity = Literal["critical", "error", "warning", "info"]
CompletenessState = Literal["complete", "incomplete", "undetermined"]
ClaimSupport = Literal["supported", "unsupported", "missing_anchor"]
ClaimOrigin = Literal["model", "prefill", "human", "import"]

# ---------------------------------------------------------------------------
# Read-only containers for mapping-valued record fields
# ---------------------------------------------------------------------------


def _reject_mutation(self: object, *args: object, **kwargs: object) -> NoReturn:
    raise TypeError(f"{type(self).__name__} is immutable: provenance records cannot be modified")


class _FrozenDict(dict):  # type: ignore[type-arg]
    """A ``dict`` whose mutators raise ``TypeError``; hashable and equal by value."""

    __slots__ = ()

    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    __ior__ = _reject_mutation
    clear = _reject_mutation
    pop = _reject_mutation
    popitem = _reject_mutation
    setdefault = _reject_mutation
    update = _reject_mutation

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(frozenset(self.items()))

    def __reduce__(self) -> tuple[type, tuple[dict[Any, Any]]]:
        return (type(self), (dict(self),))


class _FrozenList(list):  # type: ignore[type-arg]
    """A ``list`` whose mutators raise ``TypeError``; hashable and equal by value."""

    __slots__ = ()

    __setitem__ = _reject_mutation
    __delitem__ = _reject_mutation
    __iadd__ = _reject_mutation
    __imul__ = _reject_mutation
    append = _reject_mutation
    extend = _reject_mutation
    insert = _reject_mutation
    pop = _reject_mutation
    remove = _reject_mutation
    clear = _reject_mutation
    sort = _reject_mutation
    reverse = _reject_mutation

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(self))

    def __reduce__(self) -> tuple[type, tuple[list[Any]]]:
        return (type(self), (list(self),))


def _freeze(value: Any) -> Any:
    """Return a read-only, hashable equivalent of *value* (mappings, lists, sets)."""
    if isinstance(value, (_FrozenDict, _FrozenList)):
        return value
    if isinstance(value, Mapping):
        return _FrozenDict((key, _freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    if type(value) is tuple:
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


class _Record:
    """Mixin for every record: freezes mapping-valued fields at construction."""

    __slots__ = ()

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):  # type: ignore[arg-type]
            value = getattr(self, field.name)
            if isinstance(value, Mapping):
                object.__setattr__(self, field.name, _freeze(value))


# ---------------------------------------------------------------------------
# Namespaced extension keys (10.4)
# ---------------------------------------------------------------------------

EXTENSION_KEY_PATTERN: re.Pattern[str] = re.compile(r"x-[a-z][a-z0-9]*-[A-Za-z0-9_][A-Za-z0-9_-]*")
"""``x-<namespace>-<field>``: a lower-case alphanumeric namespace, then a non-empty field name."""


def is_extension_key(key: object) -> bool:
    """Return whether *key* follows the ``x-<namespace>-<field>`` convention."""
    return isinstance(key, str) and EXTENSION_KEY_PATTERN.fullmatch(key) is not None


def _check_extension_keys(extensions: object) -> None:
    if not isinstance(extensions, Mapping):
        raise TypeError(f"extensions must be a mapping, got {type(extensions).__name__}")
    bad = [key for key in extensions if not is_extension_key(key)]
    if bad:
        listed = ", ".join(repr(key) for key in bad)
        raise ValueError(
            f"extension keys must match 'x-<namespace>-<field>' to stay distinct from core fields; "
            f"rejected: {listed}"
        )


# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceAnchor(_Record):
    """Every available location of an evidence item in its source (4.3–4.5).

    ``missing`` names the anchor kinds explicitly absent (4.4).
    """

    precision: AnchorPrecision
    page: int | None = None
    coords: tuple[float, float, float, float] | None = None
    section_path: str | None = None
    char_span: tuple[int, int] | None = None
    structural_path: str | None = None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProvenanceNode(_Record):
    """Base of every provenance node: identity, type, run, privacy carrier, extensions."""

    node_id: str
    kind: NodeKind
    run_id: str
    privacy: PrivacyCarrier
    extensions: Mapping[str, Any]

    def __post_init__(self) -> None:
        _check_extension_keys(self.extensions)
        super().__post_init__()


@dataclass(frozen=True)
class SourceNode(ProvenanceNode):
    """One source document in one run (1.1, 3.1, 3.2, 3.4)."""

    document_fingerprint: str
    source_format: str
    source_label: str
    config_fingerprint: str
    schema_fingerprint: str
    model_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceNode(ProvenanceNode):
    """One evidence item, carrying its kind, anchor and producing backend (4.1–4.7, 10.2).

    ``local_id`` is the pipeline-assigned identifier, adopted verbatim (4.7).
    """

    local_id: str
    evidence_kind: EvidenceKind
    source_id: str
    anchor: SourceAnchor
    producing_backend: str | None
    rank_score: int | None
    parser_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ClaimNode(ProvenanceNode):
    """One extracted field value and the evidence it cites (5.1, 5.2, 5.6, 5.7)."""

    source_id: str
    field_index: int
    field_name: str
    value_digest: str
    origin: ClaimOrigin
    support: ClaimSupport
    cited_evidence_ids: tuple[str, ...]
    confidence: str | None


@dataclass(frozen=True)
class DerivationStep(ProvenanceNode):
    """One transformation: inputs, outputs, kind, stage and determinism (6.1–6.7)."""

    stage: str
    derivation_kind: DerivationKind
    determinism: Determinism
    model_id: str | None
    input_ids: tuple[str, ...]
    output_ids: tuple[str, ...]
    removal_reason: str | None = None
    removed_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationRecord(ProvenanceNode):
    """The attachable result of one validation check (8.7, 10.3)."""

    check_name: str
    finding_ids: tuple[str, ...]
    completed: bool


@dataclass(frozen=True)
class ProvenanceEdge(_Record):
    """A typed, directed relationship between two nodes (7.1)."""

    edge_id: str
    kind: EdgeKind
    from_id: str
    to_id: str
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class RunIdentity(_Record):
    """The run's identity and the single set of fingerprints in effect (3.5, 3.6)."""

    run_id: str
    config_fingerprint: str
    schema_fingerprint: str
    model_ids: tuple[str, ...]
    document_fingerprints: Mapping[str, str]
    created_at: str


# ---------------------------------------------------------------------------
# Cross-layer types (owned here, not by the layer that analyses them)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationFinding(_Record):
    """One provenance validation issue, naming the node or edge involved (5.4, 8.4, 8.5)."""

    finding_id: str
    code: str
    severity: Severity
    node_id: str | None
    edge_id: str | None
    message: str
    details: Mapping[str, Any]


DEFAULT_SEVERITIES: Mapping[str, Severity] = _FrozenDict(
    {
        # critical
        "dangling_reference": "critical",
        "graph_cycle": "critical",
        "invalid_identifier": "critical",
        # error
        "source_not_represented": "error",
        "evidence_out_of_run_context": "error",
        "inconsistent_derivation": "error",
        "privacy_carrier_invalid": "error",
        # warning
        "unsupported_claim": "warning",
        "missing_anchor": "warning",
        "duplicate_reference": "warning",
        "ambiguous_reference": "warning",
        # Recorded by ProvenanceRecorder when one node_id is recorded twice with
        # differing content; the first record is kept. Not in the ChainValidator
        # table of design.md, so its severity is set here.
        "duplicate_identity": "warning",
        # info
        "orphan_node": "info",
    }
)
"""Default severity per finding code (8.5); overridable via ``provenance.severity_overrides``."""


@dataclass(frozen=True)
class MissingSegment(_Record):
    """A part of the run that could not be materialized, and why (1.6, 7.4)."""

    stage: str
    record_kind: str
    reason: str
    affected_ids: tuple[str, ...]


@dataclass(frozen=True)
class StageContract(_Record):
    """The record kinds one pipeline stage must emit, and the artifact scope (2.1, 2.3)."""

    stage: str
    required_record_kinds: tuple[str, ...]
    scope: Literal["run", "document", "claim"]


@dataclass(frozen=True)
class ArtifactCompleteness(_Record):
    """Completeness state of one artifact with the absent kinds and responsible stages (2.1–2.5)."""

    state: CompletenessState
    reason: str | None
    missing_record_kinds: tuple[str, ...]
    responsible_stages: tuple[str, ...]


@dataclass(frozen=True)
class RecordedRun(_Record):
    """Immutable snapshot of everything a recorder accepted during one run (1.2, 1.6)."""

    run: RunIdentity
    nodes: Mapping[str, ProvenanceNode]
    edges: tuple[ProvenanceEdge, ...]
    findings: tuple[ValidationFinding, ...]
    stage_status: Mapping[str, Literal["complete", "failed", "not_run"]]
    stage_failure_reasons: Mapping[str, str]


@dataclass(frozen=True)
class ChainValidationReport(_Record):
    """Chain-validation outcome; ``completed=False`` means the run is unvalidated (8.4–8.6)."""

    completed: bool
    incompletion_reason: str | None
    findings: tuple[ValidationFinding, ...]
    counts_by_severity: Mapping[Severity, int]


@dataclass(frozen=True)
class CompletenessReport(_Record):
    """Completeness at run, document and claim level, as queryable data (2.2, 2.6)."""

    run: ArtifactCompleteness
    documents: Mapping[str, ArtifactCompleteness]
    claims: Mapping[str, ArtifactCompleteness]
