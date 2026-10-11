"""Property-based tests for identity, graph assembly and projection (task 7.2).

Covers the first three Hypothesis properties of provenance-core's design
("Testing Strategy -> Property Tests"), read-only over ``IdentityService``,
``GraphBuilder`` and ``Projection``:

1. **Evidence identifier round-trip totality (3.3, 4.2).** For every valid
   ``(source_id, local_id)`` pair -- a source identifier from
   ``derive_source_id(fingerprint)`` with a fingerprint that is non-empty and
   free of ``#`` and whitespace, a local identifier that is non-empty and free
   of ``#`` -- ``parse_evidence_id(format_evidence_id(s, l)) == (s, l)``, the
   formatted identifier is injective in the pair, and ``format`` inverts
   ``parse``. Every invalid input raises ``InvalidIdentifierError`` and nothing
   else; ``parse_evidence_id`` on arbitrary text either returns a pair that
   re-formats to the same text or raises ``InvalidIdentifierError``. Claim
   identifiers are injective, never parse as evidence identifiers, and carry
   the source scope of the source they were derived from. Derivation-step
   identifiers are invariant under permutation of inputs and outputs.
2. **Graph assembly never raises (7.4).** ``build_graph`` is fed generated
   ``RecordedRun`` snapshots built directly (not through the recorder, so the
   data can be hostile): dangling references, ids whose source scope matches no
   recorded source, self-referencing and cyclic steps, undeclared stage names,
   odd extension values, failed stages and documents, duplicate edges.

   *How "unmaterialized" is induced, and why that is faithful.* The record
   classes in ``provenance.models`` do not check vocabulary membership, node
   kind/class agreement, ``node_id`` or ``privacy`` type at construction
   (``graph.py``, "Partial preservation"), so such a record is exactly the
   shape the builder is documented to reject and a recorder snapshot can
   legitimately carry. The random strategy picks a subset of the generated
   nodes and edges and corrupts each with ``dataclasses.replace``: an
   out-of-vocabulary ``evidence_kind``/``anchor.precision``/``support``/
   ``origin``/``determinism``/``derivation_kind``/edge ``kind``, a node ``kind``
   that disagrees with its class or the vocabulary, a non-``PrivacyCarrier``
   ``privacy``, an empty ``stage``; a record stored under a snapshot key other
   than its ``node_id``; an empty ``node_id``; an edge with an empty ``edge_id``
   or endpoint; or a later edge reusing an assembled id with differing content
   (first wins). The rejected set is known by construction, independently of
   the implementation; no guard is monkeypatched. Because the fixed
   (derandomized) examples draw some variants rarely, every variant is also
   covered by a deterministic parametrized test. The guards for ill-typed
   input (``run_id`` not a string, ``extensions``/edge ``attributes`` not a
   mapping, ``anchor`` not a ``SourceAnchor``, a non-concrete node class, a
   blank source id in a status map) are out of scope: the task says
   "well-typed record sets".

   *Accounting.* Each rejected node is in exactly one node segment (record kind
   of its class, ``affected_ids`` its snapshot key and/or ``node_id``), no
   materialized node is named by a node segment, and the materialized nodes are
   exactly the unrejected ones. Edges mirror this: a rejected edge is absent
   from ``graph.edges`` (for a conflict, the first assembled edge stays), every
   materialized edge is present and unreported, and each rejected edge has one
   edge segment. ``record_kind="stage"`` segments are not unmaterialized
   records: they report a stage marked failed and may legitimately name a
   source identifier (and a step) already named elsewhere, so they are excluded
   from the exactly-once count and accounted for separately -- one segment per
   failed run-wide stage and per failed ``(document, stage)`` pair, each naming
   only materialized steps and the failed document.
3. **Projection is a total order (7.6).** Over the same graphs, the event log
   numbers its events exactly ``1..n`` with no shared sequence number, one event
   per graph node, strictly increasing under the documented key
   ``(stage position, stage, node-kind rank, node_id)`` (so the key is
   injective on events and the comparison is a strict total order:
   irreflexive, asymmetric, transitive and total), and is a pure function of the
   graph: two projections are equal, and a snapshot recorded in a different
   insertion order yields an equal graph and an equal log (``graph.py``,
   "Determinism").

Synthetic identifiers only. Examples are derandomized (``derandomize=True``, no
example database), as in the 4.9 attribution test (which uses 200 examples for
its properties): 200 here for the cheap identity properties and 100 for the
graph-level ones, each of which builds, validates and projects a whole graph.
"""

from __future__ import annotations

import dataclasses
import typing
import unicodedata
from collections import Counter
from itertools import combinations

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from provenance.completeness import compute_completeness
from provenance.contracts import STAGE_ORDER
from provenance.graph import (
    PROVENANCE_SCHEMA_ID,
    ProvenanceGraph,
    attach_completeness,
    attach_validation,
    build_graph,
    document_of,
    evidence_id_parts,
    is_claim_identifier,
)
from provenance.identity import (
    DERIVATION_ID_PREFIX,
    EVIDENCE_SCOPE_SEPARATOR,
    SOURCE_ID_PREFIX,
    InvalidIdentifierError,
    derive_claim_id,
    derive_derivation_id,
    derive_source_id,
    format_evidence_id,
    parse_evidence_id,
)
from provenance.models import (
    UNKNOWN_PRIVACY,
    ClaimNode,
    DerivationStep,
    EdgeKind,
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
from provenance.projection import project_event_log
from provenance.validation import validate_chain

_IDENTITY_SETTINGS = settings(max_examples=200, derandomize=True, deadline=None, database=None)
_GRAPH_SETTINGS = settings(
    max_examples=100,
    derandomize=True,
    deadline=None,
    database=None,
    # Generating a hostile snapshot plus building, validating and projecting it is
    # legitimately slower than Hypothesis' default data-generation budget.
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

_RUN = "run:0123456789abcdef"
_NODE_KIND_ORDER: tuple[str, ...] = typing.get_args(NodeKind)
_EDGE_KINDS: tuple[str, ...] = typing.get_args(EdgeKind)


# ---------------------------------------------------------------------------
# Identifier strategies
# ---------------------------------------------------------------------------

# Every character str.isspace() accepts is in category Zs/Zl/Zp or is one of
# these Cc controls; excluding both makes a fingerprint whitespace-free without a filter.
_CONTROL_WHITESPACE = "\t\n\v\f\r\x1c\x1d\x1e\x1f\x85"
_FINGERPRINT_CHARS = st.characters(
    blacklist_categories=("Cs", "Zs", "Zl", "Zp"),
    blacklist_characters=EVIDENCE_SCOPE_SEPARATOR + _CONTROL_WHITESPACE,
)
_TRICKY_FINGERPRINTS = (
    "0",
    "src:",
    "src:abc",
    "claim:src:abc",
    "drv:0123456789abcdef",
    "a@r1",
    "a:b/c\\d",
    "f1",
    "@",
    ":",
    "/",
    ".",
    "é中\U0001f9ea",
    "‮",
    "\x00",
    "x" * 4096,
)
_fingerprints = st.one_of(
    st.text(_FINGERPRINT_CHARS, min_size=1, max_size=40),
    st.text("0123456789abcdef", min_size=1, max_size=64),
    st.sampled_from(_TRICKY_FINGERPRINTS),
    st.builds(
        "{}{}".format,
        st.sampled_from(("src:", "claim:", "@", ":")),
        st.text(_FINGERPRINT_CHARS, min_size=1, max_size=8),
    ),
    st.builds(lambda text, n: text * n, st.text(_FINGERPRINT_CHARS, min_size=1, max_size=4), st.integers(50, 300)),
)
_source_ids = _fingerprints.map(derive_source_id)

# A local identifier is adopted verbatim: only non-empty and '#'-free is required.
_LOCAL_CHARS = st.characters(blacklist_categories=("Cs",), blacklist_characters=EVIDENCE_SCOPE_SEPARATOR)
_TRICKY_LOCALS = (
    "0",
    " ",
    " lead and trail ",
    "a b\tc\n",
    "src:abc",
    "claim:src:abc",
    "f1@r2",
    "é中\U0001f9ea‮",
    "\x00",
    ":",
    "y" * 4096,
)
_local_ids = st.one_of(
    st.text(_LOCAL_CHARS, min_size=1, max_size=40),
    st.sampled_from(_TRICKY_LOCALS),
    st.builds("e{}".format, st.integers(0, 10_000)),
    st.builds(lambda text, n: text * n, st.text(_LOCAL_CHARS, min_size=1, max_size=4), st.integers(50, 300)),
)
_pairs = st.tuples(_source_ids, _local_ids)

_NON_STR = st.one_of(
    st.none(), st.integers(), st.binary(max_size=4), st.lists(st.text(max_size=2), max_size=2), st.booleans()
)


def _insert_separator(text: str, position: int) -> str:
    at = position % (len(text) + 1)
    return text[:at] + EVIDENCE_SCOPE_SEPARATOR + text[at:]


def _whitespace_inside(text: str, position: int, space: str) -> str:
    at = position % (len(text) + 1)
    return text[:at] + space + text[at:]


def test_fingerprint_alphabet_excludes_every_whitespace_character() -> None:
    """The cheap alphabet really is whitespace-free (guards the filter-free strategy)."""
    allowed = {
        char
        for char in map(chr, range(0x110000))
        if unicodedata.category(char) not in ("Cs", "Zs", "Zl", "Zp") and char not in _CONTROL_WHITESPACE
    }
    assert not any(char.isspace() for char in allowed)


# ---------------------------------------------------------------------------
# 1. Identity: round-trip totality (3.3, 4.2)
# ---------------------------------------------------------------------------


@_IDENTITY_SETTINGS
@given(_pairs)
@example((derive_source_id("0"), " "))
@example((derive_source_id("src:abc"), "claim:src:abc"))
@example((derive_source_id("x" * 4096), "é中\U0001f9ea‮"))
def test_evidence_id_round_trip_is_total_over_valid_pairs(pair: tuple[str, str]) -> None:
    source_id, local_id = pair
    node_id = format_evidence_id(source_id, local_id)
    assert parse_evidence_id(node_id) == (source_id, local_id)
    assert evidence_id_parts(node_id) == (source_id, local_id)
    assert node_id == f"{source_id}{EVIDENCE_SCOPE_SEPARATOR}{local_id}"
    assert node_id.count(EVIDENCE_SCOPE_SEPARATOR) == 1
    # The reverse direction: formatting what was parsed reproduces the identifier.
    assert format_evidence_id(*parse_evidence_id(node_id)) == node_id
    # The identifier is attributed to its source scope by the shared document rule.
    assert document_of(node_id, {}) == source_id


@_IDENTITY_SETTINGS
@given(_pairs, _pairs)
def test_formatted_evidence_id_is_injective_in_the_pair(first: tuple[str, str], second: tuple[str, str]) -> None:
    same_id = format_evidence_id(*first) == format_evidence_id(*second)
    assert same_id == (first == second)


@_IDENTITY_SETTINGS
@given(_fingerprints, _fingerprints)
def test_source_ids_follow_fingerprints(first: str, second: str) -> None:
    assert derive_source_id(first) == f"{SOURCE_ID_PREFIX}{first}"
    assert (derive_source_id(first) == derive_source_id(second)) == (first == second)


@_IDENTITY_SETTINGS
@given(_pairs, st.integers(min_value=0, max_value=1000))
@example((derive_source_id("a"), "b"), 0)
def test_invalid_local_ids_raise_only_invalid_identifier_error(pair: tuple[str, str], position: int) -> None:
    source_id, local_id = pair
    for bad_local in (
        _insert_separator(local_id, position),  # contains the scope separator
        EVIDENCE_SCOPE_SEPARATOR,
        "",
    ):
        with pytest.raises(InvalidIdentifierError):
            format_evidence_id(source_id, bad_local)


@_IDENTITY_SETTINGS
@given(
    _fingerprints,
    _local_ids,
    st.integers(min_value=0, max_value=1000),
    st.sampled_from((" ", "\t", "\n", " ", " ", "\x85"))
)
def test_invalid_source_ids_raise_only_invalid_identifier_error(
    fingerprint: str, local_id: str, position: int, space: str
) -> None:
    source_id = derive_source_id(fingerprint)
    bad_source_ids = (
        fingerprint,  # lacks the prefix, unless the fingerprint is itself a well-formed source id (skipped below)
        SOURCE_ID_PREFIX,  # empty fingerprint
        "",
        _insert_separator(source_id, max(position, len(SOURCE_ID_PREFIX))),  # separator inside the scope
        _whitespace_inside(source_id, position, space),  # whitespace inside the scope
    )
    for bad in bad_source_ids:
        if bad == fingerprint and fingerprint.startswith(SOURCE_ID_PREFIX) and len(fingerprint) > len(SOURCE_ID_PREFIX):
            continue  # a fingerprint that is itself a well-formed source id is valid input
        with pytest.raises(InvalidIdentifierError):
            format_evidence_id(bad, local_id)
    with pytest.raises(InvalidIdentifierError):
        derive_source_id("")
    with pytest.raises(InvalidIdentifierError):
        derive_source_id(_insert_separator(fingerprint, position))
    with pytest.raises(InvalidIdentifierError):
        derive_source_id(_whitespace_inside(fingerprint, position, space))
    with pytest.raises(InvalidIdentifierError):
        derive_claim_id(_whitespace_inside(source_id, position, space), 0)


@_IDENTITY_SETTINGS
@given(_NON_STR, _NON_STR)
def test_non_string_arguments_raise_only_invalid_identifier_error(first: object, second: object) -> None:
    valid = derive_source_id("fp")
    with pytest.raises(InvalidIdentifierError):
        format_evidence_id(first, "local")  # type: ignore[arg-type]
    with pytest.raises(InvalidIdentifierError):
        format_evidence_id(valid, second)  # type: ignore[arg-type]
    with pytest.raises(InvalidIdentifierError):
        parse_evidence_id(first)  # type: ignore[arg-type]
    with pytest.raises(InvalidIdentifierError):
        derive_source_id(second)  # type: ignore[arg-type]
    with pytest.raises(InvalidIdentifierError):
        derive_claim_id(first, 0)  # type: ignore[arg-type]
    assert evidence_id_parts(first) is None  # type: ignore[arg-type]


@_IDENTITY_SETTINGS
@given(
    st.one_of(
        st.text(max_size=60),
        st.text("#:@srcl aim\t0123456789abcdef", max_size=30),
        st.builds(format_evidence_id, _source_ids, _local_ids),
        st.builds("{}{}{}".format, _source_ids, st.sampled_from(("#", "##", " #", "# ")), _local_ids),
        st.builds(derive_claim_id, _source_ids, st.integers(0, 50)),
    )
)
@example("")
@example("#")
@example("src:#x")
@example("src:a#")
@example("src:a#b#c")
@example("src:a #b")
@example("lorem#x")
def test_parse_evidence_id_on_arbitrary_text_is_total(text: str) -> None:
    try:
        source_id, local_id = parse_evidence_id(text)
    except InvalidIdentifierError:
        assert evidence_id_parts(text) is None
        return
    assert format_evidence_id(source_id, local_id) == text
    assert evidence_id_parts(text) == (source_id, local_id)


@_IDENTITY_SETTINGS
@given(_source_ids, st.integers(0, 10_000), st.integers(0, 50), _source_ids, st.integers(0, 10_000), st.integers(0, 50))
def test_claim_ids_are_injective_and_disjoint_from_evidence_ids(
    source_id: str, field_index: int, revision: int, other_source: str, other_index: int, other_revision: int
) -> None:
    claim_id = derive_claim_id(source_id, field_index, revision)
    suffix = f"@r{revision}" if revision else ""
    assert claim_id == f"claim:{source_id}#f{field_index}{suffix}"
    assert (claim_id == derive_claim_id(other_source, other_index, other_revision)) == (
        (source_id, field_index, revision) == (other_source, other_index, other_revision)
    )
    # A claim identifier is never an evidence identifier, and the shared rules recognise it as a claim.
    with pytest.raises(InvalidIdentifierError):
        parse_evidence_id(claim_id)
    assert evidence_id_parts(claim_id) is None
    assert is_claim_identifier(claim_id, {})
    assert not is_claim_identifier(format_evidence_id(source_id, f"f{field_index}"), {})
    # It carries the source scope it was derived from.
    assert document_of(claim_id, {}) == source_id


_STEP_IDS = st.lists(st.text(min_size=1, max_size=12), max_size=6)


@_IDENTITY_SETTINGS
@given(
    st.sampled_from(STAGE_ORDER + ("custom_stage",)),
    st.sampled_from(("extract", "merge", "filter")),
    _STEP_IDS,
    _STEP_IDS,
    st.randoms(use_true_random=False),
)
def test_derivation_id_is_invariant_under_input_and_output_permutation(
    stage: str, kind: str, inputs: list[str], outputs: list[str], rnd: typing.Any
) -> None:
    expected = derive_derivation_id(stage, kind, inputs, outputs)
    shuffled_inputs, shuffled_outputs = list(inputs), list(outputs)
    rnd.shuffle(shuffled_inputs)
    rnd.shuffle(shuffled_outputs)
    assert derive_derivation_id(stage, kind, shuffled_inputs, shuffled_outputs) == expected
    assert derive_derivation_id(stage, kind, tuple(shuffled_inputs), tuple(shuffled_outputs)) == expected
    assert expected.startswith(DERIVATION_ID_PREFIX)
    assert len(expected) == len(DERIVATION_ID_PREFIX) + 16
    # Moving an identifier from the inputs to the outputs is a different step.
    if inputs:
        assert derive_derivation_id(stage, kind, inputs[1:], [*outputs, inputs[0]]) != expected or inputs[0] in outputs


# ---------------------------------------------------------------------------
# Hostile-but-well-typed snapshots
# ---------------------------------------------------------------------------

_STATUSES = ("complete", "failed", "not_run")
_STAGE_NAMES = (*STAGE_ORDER, "custom_stage", "unattributed", "ünicode stage")
_EXTENSION_VALUES = st.one_of(
    st.none(),
    st.integers(),
    st.text(max_size=5),
    st.lists(st.integers(), max_size=2),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)
_extensions = st.dictionaries(
    st.sampled_from(("x-lorem-field", "x-ns-a_1", "x-other-b")), _EXTENSION_VALUES, max_size=2
)
_RUN_IDENTITY_FIELDS = dict(
    run_id=_RUN,
    config_fingerprint="cfg",
    schema_fingerprint="sch",
    model_ids=("model-x",),
    created_at="2026-10-09T00:00:00+00:00",
)


@dataclasses.dataclass(frozen=True, repr=False)
class _Spec:
    """A generated snapshot plus what is known by construction about its corruption.

    ``items`` are the ``(snapshot key, node)`` pairs of ``RecordedRun.nodes``; the key differs from the node's
    ``node_id`` only for a deliberately mismatched record.
    """

    items: tuple[tuple[str, ProvenanceNode], ...]
    edges: tuple[ProvenanceEdge, ...]
    # Expected node segments, as (record_kind, affected_ids), and the snapshot keys they correspond to.
    rejected_nodes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    rejected_keys: frozenset[str] = frozenset()
    # Edges the builder must keep, the ids of edges it must drop, the ids whose *later* duplicate it must
    # drop (the first assembled edge wins), and the expected edge segments (deduplicated affected_ids).
    materialized_edges: tuple[ProvenanceEdge, ...] = ()
    rejected_edge_ids: frozenset[str] = frozenset()
    conflict_edge_ids: frozenset[str] = frozenset()
    expected_edge_segments: tuple[tuple[str, ...], ...] = ()
    source_ids: tuple[str, ...] = ()
    run_status: tuple[tuple[str, str], ...] = tuple((stage, "not_run") for stage in STAGE_ORDER)
    run_reasons: tuple[tuple[str, str], ...] = ()
    document_status: tuple[tuple[str, str, str], ...] = ()  # (source_id, stage, status)
    document_reasons: tuple[tuple[str, str, tuple[str, ...]], ...] = ()

    @property
    def nodes(self) -> tuple[ProvenanceNode, ...]:
        return tuple(node for _key, node in self.items)

    def __repr__(self) -> str:  # compact falsifying examples
        return (
            f"_Spec(items={[(key, node.node_id) for key, node in self.items]}, "
            f"edges={[edge.edge_id for edge in self.edges]}, rejected_nodes={list(self.rejected_nodes)}, "
            f"rejected_edge_ids={sorted(self.rejected_edge_ids)}, conflict_edge_ids={sorted(self.conflict_edge_ids)}, "
            f"sources={list(self.source_ids)}, failed_run_stages={sorted(self.failed_run_stages())}, "
            f"failed_documents={sorted(self.failed_document_pairs())})"
        )

    def failed_run_stages(self) -> set[str]:
        return {stage for stage, status in self.run_status if status == "failed"}

    def failed_document_pairs(self) -> set[tuple[str, str]]:
        return {(source_id, stage) for source_id, stage, status in self.document_status if status == "failed"}

    def without_conflicting_edges(self) -> _Spec:
        """Drop the later duplicate of every conflicting edge id (first-assembled wins, so order would matter)."""
        seen: set[str] = set()
        kept = []
        for edge in self.edges:
            if edge.edge_id in self.conflict_edge_ids and edge.edge_id in seen:
                continue
            seen.add(edge.edge_id)
            kept.append(edge)
        return dataclasses.replace(self, edges=tuple(kept))

    def recorded(
        self,
        *,
        node_order: typing.Sequence[int] | None = None,
        edge_order: typing.Sequence[int] | None = None,
        status_order: typing.Sequence[int] | None = None,
    ) -> RecordedRun:
        items = list(self.items)
        edges = list(self.edges)
        run_status = list(self.run_status)
        if node_order is not None:
            items = [items[i] for i in node_order]
        if edge_order is not None:
            edges = [edges[i] for i in edge_order]
        if status_order is not None:
            run_status = [run_status[i] for i in status_order]
        document_status: dict[str, dict[str, str]] = {}
        for source_id, stage, status in self.document_status:
            document_status.setdefault(source_id, {})[stage] = status
        document_reasons: dict[str, dict[str, tuple[str, ...]]] = {}
        for source_id, stage, reasons in self.document_reasons:
            document_reasons.setdefault(source_id, {})[stage] = reasons
        fingerprints = {sid: sid[len(SOURCE_ID_PREFIX) :] for sid in self.source_ids}
        return RecordedRun(
            run=RunIdentity(document_fingerprints=fingerprints, **_RUN_IDENTITY_FIELDS),
            nodes=dict(items),
            edges=tuple(edges),
            findings=(),
            stage_status=dict(run_status),  # type: ignore[arg-type]
            stage_failure_reasons=dict(self.run_reasons),
            document_stage_status=document_status,  # type: ignore[arg-type]
            document_stage_failure_reasons=document_reasons,
        )


def _node_common(node_id: str, kind: str, extensions: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return dict(node_id=node_id, kind=kind, run_id=_RUN, privacy=UNKNOWN_PRIVACY, extensions=extensions)


# Each corruption keeps the record constructible (the models do not check) but outside what the builder
# materializes. Every entry is exercised deterministically by ``test_each_corruption_*`` below, so none
# depends on the random generator happening to draw it.
_Corruption = tuple[str, typing.Callable[[typing.Any], typing.Any]]
_CORRUPTIONS: typing.Mapping[type, tuple[_Corruption, ...]] = {
    SourceNode: (
        ("kind_disagrees_with_class", lambda n: dataclasses.replace(n, kind="claim")),
        ("kind_outside_vocabulary", lambda n: dataclasses.replace(n, kind="bogus")),
        ("privacy_none", lambda n: dataclasses.replace(n, privacy=None)),
    ),
    EvidenceNode: (
        ("evidence_kind", lambda n: dataclasses.replace(n, evidence_kind="hologram")),
        (
            "anchor_precision",
            lambda n: dataclasses.replace(n, anchor=SourceAnchor(precision="fuzzy")),  # type: ignore[arg-type]
        ),
        ("kind_disagrees_with_class", lambda n: dataclasses.replace(n, kind="validation")),
        ("privacy_none", lambda n: dataclasses.replace(n, privacy=None)),
    ),
    ClaimNode: (
        ("support", lambda n: dataclasses.replace(n, support="maybe")),
        ("origin", lambda n: dataclasses.replace(n, origin="oracle")),
        ("kind_disagrees_with_class", lambda n: dataclasses.replace(n, kind="derivation")),
        ("privacy_none", lambda n: dataclasses.replace(n, privacy=None)),
    ),
    DerivationStep: (
        ("determinism", lambda n: dataclasses.replace(n, determinism="random")),
        ("derivation_kind", lambda n: dataclasses.replace(n, derivation_kind="invent")),
        ("empty_stage", lambda n: dataclasses.replace(n, stage="")),
        ("kind_disagrees_with_class", lambda n: dataclasses.replace(n, kind="claim")),
        ("privacy_none", lambda n: dataclasses.replace(n, privacy=None)),
    ),
    ValidationRecord: (
        ("kind_disagrees_with_class", lambda n: dataclasses.replace(n, kind="source")),
        ("privacy_string", lambda n: dataclasses.replace(n, privacy="public")),
    ),
}
_CLASS_NODE_KIND = {
    SourceNode: "source",
    EvidenceNode: "evidence",
    ClaimNode: "claim",
    DerivationStep: "derivation",
    ValidationRecord: "validation",
}
_STEP_KINDS = (
    "extract", "normalize", "chunk", "filter", "merge", "synthesize", "reconcile", "remove", "prefill", "annotate",
)


def _mismatched_key(node: ProvenanceNode) -> tuple[str, tuple[str, ...]]:
    """(snapshot key, expected affected_ids) when the record sits under a key other than its node_id."""
    key = f"key-mismatch:{node.node_id}"
    return key, (key, node.node_id)


@st.composite
def _specs(draw: st.DrawFn) -> _Spec:
    fingerprints = draw(st.lists(_fingerprints, min_size=0, max_size=3, unique=True))
    source_ids = tuple(derive_source_id(fp) for fp in fingerprints)
    # Evidence/claims may also sit in a scope no recorded source matches.
    scopes = [*source_ids, derive_source_id("unrecorded-scope")]

    nodes: dict[str, ProvenanceNode] = {}

    def add(node: ProvenanceNode) -> None:
        nodes.setdefault(node.node_id, node)

    for source_id in source_ids:
        if draw(st.integers(0, 6)) > 0:
            add(
                SourceNode(
                    **_node_common(source_id, "source", draw(_extensions)),
                    document_fingerprint=source_id[len(SOURCE_ID_PREFIX) :],
                    source_format="pdf",
                    source_label="lorem.pdf",
                    config_fingerprint="cfg",
                    schema_fingerprint="sch",
                    model_ids=("model-x",),
                )
            )
    for scope in scopes:
        for local_id in draw(st.lists(_local_ids, max_size=3, unique=True)):
            precision = draw(st.sampled_from(("exact", "approximate", "absent")))
            add(
                EvidenceNode(
                    **_node_common(format_evidence_id(scope, local_id), "evidence", draw(_extensions)),
                    local_id=local_id,
                    evidence_kind=draw(st.sampled_from(("sentence", "figure_caption", "table", "metadata"))),
                    source_id=draw(st.sampled_from((scope, *scopes))),  # the field may disagree with the identifier
                    anchor=SourceAnchor(
                        precision=precision,  # type: ignore[arg-type]
                        page=draw(st.one_of(st.none(), st.integers(0, 9))),
                    ),
                    producing_backend=draw(st.sampled_from((None, "grobid", "pdfplumber"))),
                    rank_score=draw(st.one_of(st.none(), st.integers(-3, 9))),
                    parser_metadata={},
                )
            )
    evidence_ids = [node_id for node_id, node in nodes.items() if isinstance(node, EvidenceNode)]
    dangling = [
        format_evidence_id(scopes[0] if scopes else "src:none", "dangling"),
        derive_claim_id(derive_source_id("elsewhere"), 99),
        "opaque id with spaces",
        derive_source_id("not-recorded"),
    ]
    for scope in scopes:
        for field_index in draw(st.lists(st.integers(0, 4), max_size=3, unique=True)):
            cited = tuple(draw(st.lists(st.sampled_from([*evidence_ids, *dangling]), max_size=3, unique=True)))
            claim_id = derive_claim_id(scope, field_index, draw(st.integers(0, 1)))
            add(
                ClaimNode(
                    **_node_common(claim_id, "claim", draw(_extensions)),
                    source_id=draw(st.sampled_from((scope, *scopes))),
                    field_index=field_index,
                    field_name=draw(st.sampled_from(("sample_size", "", "ünicode"))),
                    value_digest="0123456789abcdef",
                    origin=draw(st.sampled_from(("model", "prefill", "human", "import"))),
                    support=draw(st.sampled_from(("supported", "unsupported", "missing_anchor"))),
                    cited_evidence_ids=cited,
                    confidence=draw(st.sampled_from((None, "high", "low"))),
                )
            )
    pool = [*nodes, *dangling]
    for _ in range(draw(st.integers(0, 6))):
        inputs = tuple(draw(st.lists(st.sampled_from(pool), max_size=3))) if pool else ()
        outputs = tuple(draw(st.lists(st.sampled_from(pool), max_size=3))) if pool else ()
        stage = draw(st.sampled_from(_STAGE_NAMES))
        kind = draw(st.sampled_from(_STEP_KINDS))
        step_id = derive_derivation_id(stage, kind, inputs, outputs)
        add(
            DerivationStep(
                **_node_common(step_id, "derivation", draw(_extensions)),
                stage=stage,
                derivation_kind=kind,  # type: ignore[arg-type]
                determinism=draw(st.sampled_from(("deterministic", "probabilistic"))),
                model_id=draw(st.sampled_from((None, "model-x"))),
                input_ids=inputs,
                output_ids=outputs,
                removal_reason=draw(st.sampled_from((None, "lorem"))),
                removed_ids=tuple(draw(st.lists(st.sampled_from(pool), max_size=2))) if pool else (),
            )
        )
    # Cyclic and self-referencing steps: the output of one is the input of the next, and back.
    step_ids = [node_id for node_id, node in nodes.items() if isinstance(node, DerivationStep)]
    if len(step_ids) >= 2 and draw(st.booleans()):
        first, second = step_ids[0], step_ids[1]
        for this, other in ((first, second), (second, first)):
            node = nodes[this]
            assert isinstance(node, DerivationStep)
            nodes[this] = dataclasses.replace(
                node, input_ids=(*node.input_ids, other), output_ids=(*node.output_ids, this)
            )
    for index in range(draw(st.integers(0, 2))):
        add(
            ValidationRecord(
                **_node_common(f"val:{_RUN}:check_{index}", "validation", draw(_extensions)),
                check_name=f"check_{index}",
                finding_ids=tuple(draw(st.lists(st.text(min_size=1, max_size=4), max_size=2))),
                completed=draw(st.booleans()),
            )
        )

    all_ids = [*nodes, *dangling]
    edges: list[ProvenanceEdge] = []
    for index in range(draw(st.integers(0, 6)) if all_ids else 0):
        edges.append(
            ProvenanceEdge(
                edge_id=f"e:{index}",
                kind=draw(st.sampled_from(_EDGE_KINDS)),  # type: ignore[arg-type]
                from_id=draw(st.sampled_from(all_ids)),
                to_id=draw(st.sampled_from(all_ids)),
                attributes=draw(st.dictionaries(st.text(max_size=3), st.integers(), max_size=2)),
            )
        )

    # Corrupt a random subset of nodes and edges; remember exactly which and what the builder must report.
    items: dict[str, tuple[str, ProvenanceNode]] = {node_id: (node_id, node) for node_id, node in nodes.items()}
    rejected_nodes: list[tuple[str, tuple[str, ...]]] = []
    rejected_keys: set[str] = set()
    if nodes:
        for node_id in draw(st.lists(st.sampled_from(list(nodes)), max_size=4, unique=True)):
            node = nodes[node_id]
            record_kind = _CLASS_NODE_KIND[type(node)]
            variant = draw(st.sampled_from(("field", "field", "field", "key_mismatch", "empty_node_id")))
            if variant == "field":
                _label, corrupt = draw(st.sampled_from(_CORRUPTIONS[type(node)]))
                items[node_id] = (node_id, corrupt(node))
                rejected_keys.add(node_id)
                rejected_nodes.append((record_kind, (node_id,)))
            elif variant == "key_mismatch":
                key, affected = _mismatched_key(node)
                items[node_id] = (key, node)
                rejected_keys.add(key)
                rejected_nodes.append((record_kind, affected))
            else:
                key = f"empty-id:{node_id}"
                items[node_id] = (key, dataclasses.replace(node, node_id=""))
                rejected_keys.add(key)
                rejected_nodes.append((record_kind, (key,)))

    base_count = len(edges)
    rejected_indexes: set[int] = set()
    rejected_edge_ids: set[str] = set()
    conflict_edge_ids: set[str] = set()
    expected_edge_segments: set[tuple[str, ...]] = set()
    for index in draw(st.lists(st.sampled_from(range(base_count)), max_size=3, unique=True)) if edges else ():
        edge = edges[index]
        variant = draw(st.sampled_from(("kind", "from", "to", "empty_edge_id", "conflict")))
        if variant == "conflict":
            # A later edge reusing this id with other content: the first assembled wins, the later is rejected.
            edges.append(dataclasses.replace(edge, attributes={"conflicting": 1, **edge.attributes}))
            conflict_edge_ids.add(edge.edge_id)
            expected_edge_segments.add((edge.edge_id,))
            continue
        rejected_indexes.add(index)
        if variant == "empty_edge_id":
            # Named by its endpoints in the segment, since it has no usable id.
            edges[index] = dataclasses.replace(edge, edge_id="")
            expected_edge_segments.add(tuple(dict.fromkeys((edge.from_id, edge.to_id))))
            continue
        if variant == "kind":
            edges[index] = dataclasses.replace(edge, kind="likes")  # type: ignore[arg-type]
        elif variant == "from":
            edges[index] = dataclasses.replace(edge, from_id="")
        else:
            edges[index] = dataclasses.replace(edge, to_id="")
        rejected_edge_ids.add(edge.edge_id)
        expected_edge_segments.add((edge.edge_id,))
    materialized_edges = tuple(edges[i] for i in range(base_count) if i not in rejected_indexes)

    stages_for_status = (
        *STAGE_ORDER,
        *draw(st.lists(st.sampled_from(("custom_stage", "ünicode stage")), max_size=1)),
    )
    run_status = tuple((stage, draw(st.sampled_from(_STATUSES))) for stage in stages_for_status)
    run_reasons = tuple(
        (stage, draw(st.sampled_from(("", "  ", "lorem failure", "é中"))))
        for stage, status in run_status
        if status == "failed" and draw(st.booleans())
    )
    document_status = tuple(
        (source_id, stage, draw(st.sampled_from(("complete", "failed"))))
        for source_id in source_ids
        for stage in draw(st.lists(st.sampled_from(_STAGE_NAMES), max_size=2, unique=True))
    )
    document_reasons = tuple(
        (source_id, stage, tuple(draw(st.lists(st.sampled_from(("", "ipsum", "dolor", " ipsum ")), max_size=2))))
        for source_id, stage, status in document_status
        if status == "failed"
    )
    return _Spec(
        items=tuple(items.values()),
        edges=tuple(edges),
        rejected_nodes=tuple(rejected_nodes),
        rejected_keys=frozenset(rejected_keys),
        materialized_edges=materialized_edges,
        rejected_edge_ids=frozenset(rejected_edge_ids),
        conflict_edge_ids=frozenset(conflict_edge_ids),
        expected_edge_segments=tuple(sorted(expected_edge_segments)),
        source_ids=source_ids,
        run_status=run_status,
        run_reasons=run_reasons,
        document_status=document_status,
        document_reasons=document_reasons,
    )


def _empty_spec() -> _Spec:
    return _Spec(items=(), edges=())


def _stage_failures_only_spec() -> _Spec:
    return _Spec(
        items=(),
        edges=(),
        run_status=tuple((stage, "failed") for stage in STAGE_ORDER),
        run_reasons=(("quality_control", "lorem failure"),),
    )


# ---------------------------------------------------------------------------
# 2. Graph assembly never raises; every unmaterialized record is in exactly one segment (7.4)
# ---------------------------------------------------------------------------


def _non_stage_segments(graph: ProvenanceGraph) -> list[typing.Any]:
    return [segment for segment in graph.missing_segments if segment.record_kind != "stage"]


def _assert_graph_accounting(spec: _Spec, graph: ProvenanceGraph) -> None:
    # Never raises (the call returned) and returns a graph carrying the schema identity.
    assert isinstance(graph, ProvenanceGraph)
    assert graph.schema_id == PROVENANCE_SCHEMA_ID

    # Materialized nodes are exactly the unrejected ones; nothing is invented, nothing rejected survives.
    kept_ids = {node.node_id for key, node in spec.items if key not in spec.rejected_keys}
    assert set(graph.nodes) == kept_ids
    assert list(graph.nodes) == sorted(graph.nodes)

    # Every rejected node is in exactly one node segment (kind and identifiers as expected); no other
    # node segment exists and no materialized node is reported as missing.
    non_stage = _non_stage_segments(graph)
    node_segments = [segment for segment in non_stage if segment.record_kind != "edge"]
    assert sorted((s.record_kind, s.affected_ids) for s in node_segments) == sorted(spec.rejected_nodes)
    assert all(segment.reason.strip() and segment.stage.strip() for segment in node_segments)
    assert not (set(graph.nodes) & {i for s in node_segments for i in s.affected_ids})

    # Edges mirror the node side. Rejected edges are absent from the graph (a conflicting later duplicate
    # leaves the first-assembled edge in place), materialized edges are present and unreported, and every
    # rejected edge has exactly one edge segment.
    graph_edges = {edge.edge_id: edge for edge in graph.edges}
    assert len(graph_edges) == len(graph.edges)
    for edge in spec.materialized_edges:
        assert graph_edges.get(edge.edge_id) == edge, edge.edge_id
    for edge_id in spec.rejected_edge_ids:
        assert edge_id not in graph_edges, edge_id
    assert "" not in graph_edges
    edge_segments = [segment for segment in non_stage if segment.record_kind == "edge"]
    assert sorted(s.affected_ids for s in edge_segments) == sorted(spec.expected_edge_segments)
    assert all(segment.reason.strip() and segment.stage.strip() for segment in edge_segments)
    reported_edge_ids = {i for s in edge_segments for i in s.affected_ids}
    kept_edge_ids = {edge.edge_id for edge in spec.materialized_edges} - spec.conflict_edge_ids
    assert not (kept_edge_ids & reported_edge_ids)
    # Anything else in the graph is a structural edge of a materialized node.
    recorded = set(spec.materialized_edges)
    structural_kinds = {"belongs_to_source", "has_input", "has_output"}
    for edge in graph.edges:
        if edge in recorded:
            continue
        assert edge.kind in structural_kinds, edge
        assert edge.from_id in graph.nodes
        assert edge.edge_id == f"{edge.kind}:{edge.from_id}->{edge.to_id}"
    assert [e.edge_id for e in graph.edges] == sorted(e.edge_id for e in graph.edges)

    # Stage segments are not unmaterialized records: one per failed stage / failed (document, stage).
    stage_segments = [segment for segment in graph.missing_segments if segment.record_kind == "stage"]
    failed_runs = {stage for stage in spec.failed_run_stages() if stage}
    failed_pairs = spec.failed_document_pairs()
    assert len(stage_segments) == len(failed_runs) + len(failed_pairs)
    step_ids = {node_id for node_id, node in graph.nodes.items() if isinstance(node, DerivationStep)}
    for segment in stage_segments:
        assert segment.reason.strip()
        named = list(segment.affected_ids)
        if named and named[0] in spec.source_ids and named[0] not in step_ids:
            named = named[1:]  # a per-document segment names its document first
        assert set(named) <= step_ids

    # Segments are returned in their documented order and without duplicates.
    keys = [(s.stage, s.record_kind, s.affected_ids, s.reason) for s in graph.missing_segments]
    assert keys == sorted(keys)
    assert len(set(graph.missing_segments)) == len(graph.missing_segments)


@_GRAPH_SETTINGS
@given(_specs())
@example(_empty_spec())
@example(_stage_failures_only_spec())
def test_build_graph_never_raises_and_accounts_for_every_unmaterialized_record(spec: _Spec) -> None:
    recorded = spec.recorded()
    graph = build_graph(recorded)
    _assert_graph_accounting(spec, graph)
    # Determinism: building again yields an equal graph, with equal key order.
    again = build_graph(recorded)
    assert again == graph
    assert list(again.nodes) == list(graph.nodes)


# Deterministic coverage of every rejection path: the random property above draws each corruption only
# occasionally, so with its fixed (derandomized) examples a guard could be deleted unnoticed. These cases
# make each one certain.


def _minimal_node(node_class: type, name: str) -> ProvenanceNode:
    source_id = derive_source_id("fp")
    if node_class is SourceNode:
        return SourceNode(
            **_node_common(derive_source_id(name), "source", {}),
            document_fingerprint=name,
            source_format="pdf",
            source_label="lorem.pdf",
            config_fingerprint="cfg",
            schema_fingerprint="sch",
            model_ids=("model-x",),
        )
    if node_class is EvidenceNode:
        return EvidenceNode(
            **_node_common(format_evidence_id(source_id, name), "evidence", {}),
            local_id=name,
            evidence_kind="sentence",
            source_id=source_id,
            anchor=SourceAnchor(precision="exact", page=1),
            producing_backend="grobid",
            rank_score=1,
            parser_metadata={},
        )
    if node_class is ClaimNode:
        return ClaimNode(
            **_node_common(derive_claim_id(source_id, len(name)), "claim", {}),
            source_id=source_id,
            field_index=len(name),
            field_name="sample_size",
            value_digest="0123456789abcdef",
            origin="model",
            support="unsupported",
            cited_evidence_ids=(),
            confidence=None,
        )
    if node_class is DerivationStep:
        return DerivationStep(
            **_node_common(derive_derivation_id("extraction_chunk", "extract", [name], []), "derivation", {}),
            stage="extraction_chunk",
            derivation_kind="extract",
            determinism="deterministic",
            model_id=None,
            input_ids=(name,),
            output_ids=(),
        )
    assert node_class is ValidationRecord
    return ValidationRecord(
        **_node_common(f"val:{_RUN}:{name}", "validation", {}),
        check_name=name,
        finding_ids=(),
        completed=True,
    )


_CORRUPTION_CASES = [
    pytest.param(node_class, label, corrupt, id=f"{node_class.__name__}-{label}")
    for node_class, corruptions in _CORRUPTIONS.items()
    for label, corrupt in corruptions
]


@pytest.mark.parametrize(("node_class", "label", "corrupt"), _CORRUPTION_CASES)
def test_each_corruption_is_rejected_exactly_once_and_spares_a_valid_sibling(
    node_class: type, label: str, corrupt: typing.Callable[[typing.Any], typing.Any]
) -> None:
    del label  # carried only for the test id
    broken = corrupt(_minimal_node(node_class, "bad"))
    sibling = _minimal_node(node_class, "good")
    spec = _Spec(items=((broken.node_id, broken), (sibling.node_id, sibling)), edges=())

    graph = build_graph(spec.recorded())

    assert set(graph.nodes) == {sibling.node_id}
    assert graph.nodes[sibling.node_id] == sibling
    [segment] = _non_stage_segments(graph)
    assert segment.affected_ids == (broken.node_id,)
    assert segment.record_kind == _CLASS_NODE_KIND[node_class]
    assert segment.reason.strip()
    assert segment.stage.strip()


_IDENTIFIER_CASES = ("key_mismatch", "empty_node_id", "empty_node_id_and_key")


@pytest.mark.parametrize("node_class", list(_CORRUPTIONS), ids=lambda cls: cls.__name__)
@pytest.mark.parametrize("case", _IDENTIFIER_CASES)
def test_identifier_level_rejections_are_reported_once(node_class: type, case: str) -> None:
    node = _minimal_node(node_class, "bad")
    sibling = _minimal_node(node_class, "good")
    if case == "key_mismatch":
        key, affected = _mismatched_key(node)
        broken = node
    elif case == "empty_node_id":
        key, affected = "empty-id-key", ("empty-id-key",)
        broken = dataclasses.replace(node, node_id="")
    else:
        key, affected = "", ()
        broken = dataclasses.replace(node, node_id="")
    spec = _Spec(items=((key, broken), (sibling.node_id, sibling)), edges=())

    graph = build_graph(spec.recorded())

    assert set(graph.nodes) == {sibling.node_id}
    [segment] = _non_stage_segments(graph)
    assert segment.affected_ids == affected
    assert segment.record_kind == _CLASS_NODE_KIND[node_class]
    assert segment.reason.strip()
    assert segment.stage.strip()


@pytest.mark.parametrize("variant", ("bad_kind", "empty_from", "empty_to", "empty_edge_id", "conflict"))
def test_each_edge_rejection_is_reported_once_and_spares_a_valid_sibling(variant: str) -> None:
    claim = _minimal_node(ClaimNode, "ab")
    evidence = _minimal_node(EvidenceNode, "e1")
    good = ProvenanceEdge(
        edge_id="e:good", kind="cites_evidence", from_id=claim.node_id, to_id=evidence.node_id, attributes={}
    )
    base = dataclasses.replace(good, edge_id="e:bad")
    broken_by_variant = {
        "bad_kind": (dataclasses.replace(base, kind="likes"), ("e:bad",)),  # type: ignore[arg-type]
        "empty_from": (dataclasses.replace(base, from_id=""), ("e:bad",)),
        "empty_to": (dataclasses.replace(base, to_id=""), ("e:bad",)),
        "empty_edge_id": (dataclasses.replace(base, edge_id=""), (claim.node_id, evidence.node_id)),
        "conflict": (dataclasses.replace(base, attributes={"conflicting": 1}), ("e:bad",)),
    }
    broken, affected = broken_by_variant[variant]
    edges = (good, base, broken) if variant == "conflict" else (good, broken)
    spec = _Spec(items=((claim.node_id, claim), (evidence.node_id, evidence)), edges=edges)

    graph = build_graph(spec.recorded())

    by_id = {edge.edge_id: edge for edge in graph.edges}
    assert by_id["e:good"] == good
    assert by_id.get("e:bad") == (base if variant == "conflict" else None)
    assert "" not in by_id
    [segment] = _non_stage_segments(graph)
    assert segment.record_kind == "edge"
    assert segment.affected_ids == affected
    assert segment.reason.strip()
    assert segment.stage.strip()


@_GRAPH_SETTINGS
@given(_specs(), st.randoms(use_true_random=False))
def test_graph_is_independent_of_record_insertion_order(spec: _Spec, rnd: typing.Any) -> None:
    # Conflicting duplicate edge ids are first-wins by design, so shuffle only the unambiguous edges.
    unambiguous = spec.without_conflicting_edges()
    node_order = list(range(len(unambiguous.items)))
    edge_order = list(range(len(unambiguous.edges)))
    status_order = list(range(len(unambiguous.run_status)))
    for order in (node_order, edge_order, status_order):
        rnd.shuffle(order)
    reference = build_graph(unambiguous.recorded())
    shuffled = build_graph(
        unambiguous.recorded(node_order=node_order, edge_order=edge_order, status_order=status_order)
    )
    assert shuffled == reference
    assert list(shuffled.nodes) == list(reference.nodes)
    assert project_event_log(shuffled) == project_event_log(reference)


@_GRAPH_SETTINGS
@given(_specs())
def test_attachments_are_total_non_mutating_and_idempotent(spec: _Spec) -> None:
    recorded = spec.recorded()
    graph = build_graph(recorded)
    pristine = build_graph(recorded)

    report = validate_chain(graph)
    with_validation = attach_validation(graph, report)
    assert graph == pristine  # the input graph is not modified
    assert attach_validation(with_validation, report) == with_validation
    assert attach_validation(graph, report) == with_validation
    assert with_validation.validation == report
    assert set(graph.nodes) <= set(with_validation.nodes)
    assert [i for i in with_validation.nodes if i not in graph.nodes] in ([], [f"val:{_RUN}:chain_validation"])

    completeness = compute_completeness(with_validation, recorded, report)
    with_both = attach_completeness(with_validation, completeness)
    assert with_validation.completeness is None
    assert attach_completeness(with_both, completeness) == with_both
    assert with_both.nodes == with_validation.nodes
    assert with_both.edges == with_validation.edges
    assert with_both.completeness == completeness
    assert attach_validation(with_both, report).completeness == completeness


# ---------------------------------------------------------------------------
# 3. Projection ordering is a total order (7.6)
# ---------------------------------------------------------------------------


def _stage_rank(stage: str) -> tuple[int, str]:
    """The documented stage order, written independently of ``graph.stage_sort_key``.

    Declared stages in ``STAGE_ORDER`` order, then undeclared stages by name, then ``"unattributed"`` last.
    """
    if stage in STAGE_ORDER:
        return (STAGE_ORDER.index(stage), "")
    if stage == "unattributed":
        return (len(STAGE_ORDER) + 1, "")
    return (len(STAGE_ORDER), stage)


def _key(event: typing.Any) -> tuple[typing.Any, ...]:
    """The documented ordering key ``(stage position, stage, node-kind rank, node_id)``, rebuilt in the test."""
    return (*_stage_rank(event.stage), _NODE_KIND_ORDER.index(event.node_kind), event.node_id)


def _assert_total_order(graph: ProvenanceGraph) -> None:
    events = project_event_log(graph)
    count = len(graph.nodes)
    # Sequence numbers are exactly 1..n: unique, consecutive, strictly increasing along the list.
    assert [event.sequence for event in events] == list(range(1, count + 1))
    assert len({event.sequence for event in events}) == len(events)
    # One event per node, no event without a node.
    assert sorted(event.node_id for event in events) == sorted(graph.nodes)
    for event in events:
        node = graph.nodes[event.node_id]
        assert event.node_kind == node.kind
        if isinstance(node, DerivationStep):
            assert event.stage == node.stage

    keys = [_key(event) for event in events]
    # The key is injective on events and the log is sorted by it (strict total order). Because the key
    # embeds the independently written stage order, this also asserts declared stages come first in
    # STAGE_ORDER order, undeclared stages follow by name, and "unattributed" is last.
    assert all(first < second for first, second in zip(keys, keys[1:], strict=False)), keys
    assert len(set(keys)) == len(keys)
    if len(events) <= 25:
        for (i, left), (j, right) in combinations(list(enumerate(keys)), 2):
            assert (left < right) != (right < left)  # asymmetric and total
            assert (left == right) is False  # distinct events never tie
            assert (left < right) == (i < j)  # the log order is the key order
        for a, b, c in combinations(keys, 3):
            assert a < b < c and a < c  # transitive along the sorted list

    # A pure function of the graph: no state, clock or randomness leaks between calls.
    assert project_event_log(graph) == events


@_GRAPH_SETTINGS
@given(_specs())
@example(_empty_spec())
@example(_stage_failures_only_spec())
def test_event_log_is_a_total_order_without_shared_sequence_numbers(spec: _Spec) -> None:
    graph = build_graph(spec.recorded())
    _assert_total_order(graph)
    # An attached validation report adds its ValidationRecord node and the order stays total.
    attached = attach_validation(graph, validate_chain(graph))
    _assert_total_order(attached)
    # Missing segments and attached reports are not nodes: the log has one event per node only.
    assert len(project_event_log(attached)) == len(attached.nodes)
