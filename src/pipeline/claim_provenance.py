"""claim_provenance -- claim nodes and extraction derivation steps for ``process_pdf``.

Pipeline-side half of the provenance integration for the claim-scoped stages
``metadata_prefill``, ``extraction_chunk`` and ``synthesis`` (provenance-core
design, "PipelineIntegration"; Requirements 2.5, 5.1, 5.4, 5.5, 5.7, 6.1, 6.7).
It lives beside ``pdf_processor`` rather than inside it because that module is
already the largest in the package and the emission is a self-contained
conversion of values ``process_pdf`` already holds; ``pipeline`` is the sole
integrator, so ``provenance`` never imports from here.

What is emitted
---------------
One claim node per final field, converted by ``claim_nodes_from_fields`` from
the field mappings ``process_pdf`` returns (so each claim's value digest is the
digest of the persisted value), with ``cites_evidence`` edges to the evidence
nodes recorded by ``evidence_index`` -- ``known_evidence_ids`` and
``evidence_anchors`` come from those same nodes -- and these derivation steps,
which reference node identifiers only:

``metadata_prefill``
    one deterministic ``prefill`` step whose outputs are the prefilled claims
    (origin ``prefill``, Requirement 5.7) and whose inputs are the header
    metadata nodes they cite. The author cites ``M-first-author`` only when the
    prefilled value is the TEI header's first author; the year cites
    ``M-publication-date`` only when its provenance is ``tei_header``. A
    fallback value (paper id, PDF-metadata or first-page year, ``"nr"``) cites
    nothing.
``extraction_chunk``
    one probabilistic ``extract`` step per chunk that ran, carrying the chunk
    model, with the claims it produced as outputs and the evidence nodes they
    cite as inputs; plus one deterministic ``merge`` step for a field several
    chunks agreed on.
``synthesis``
    one probabilistic ``synthesize`` step carrying the synthesis model; plus a
    ``remove`` step naming the conflict candidates the synthesis input cap
    left out, with the reason.

Revisions (Requirement 5.5). A field with several chunk candidates -- a
conflict resolved by synthesis, or an agreement merged deterministically -- has
each candidate emitted as an earlier revision ``claim:<src>#f<n>[@r<k>]`` (k =
0, 1, ... in chunk order) and the final claim as the next revision, whose
``contributing_claim_ids`` are the contributing candidates, so ``revises``
edges exist on the real path. Today chunks own disjoint fields, so this path is
reached only when a configuration assigns a field to several chunks; a field
with a single contributor has one claim, ``@r0``.

Reference problems. ``claim_issue_sink`` wraps the recorder's issue sink for one
document: it injects the claim identifier the validator cannot know, leaves
scoped (``#``) references to the claim adapter, forwards only the three
reference codes, and never raises.

Prompt safety. Nothing here is called before the prompts are assembled, and no
value built here is passed to a prompt or to ``extract_chunk``; the issue sink
only receives findings after a response is parsed.

Nothing here raises into extraction: every public entry point catches
``Exception`` (``BaseException`` passes), logs a WARNING and marks the affected
stage failed for the document.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Literal

from provenance.adapters.claim_adapter import claim_nodes_from_fields
from provenance.adapters.derivation_adapter import derivation_from_stage_event
from provenance.identity import (
    EVIDENCE_SCOPE_SEPARATOR,
    InvalidIdentifierError,
    derive_claim_id,
    derive_source_id,
    format_evidence_id,
)
from provenance.models import ClaimNode, ClaimOrigin, EvidenceNode, SourceAnchor
from utils.logging_utils import get_logger

from .evidence_index import (
    HEADER_AUTHOR_EVIDENCE_ID,
    HEADER_DATE_EVIDENCE_ID,
    EvidenceBundle,
    extract_header_metadata,
)
from .validator import reconstruct_fields

if TYPE_CHECKING:
    from provenance.recorder import ProvenanceRecorder

    from .document_provenance import DocumentProvenance

logger = get_logger(__name__)

__all__ = [
    "DocumentClaims",
    "FieldCandidates",
    "claim_issue_sink",
    "claim_stages_that_ran",
    "header_citations",
    "plan_field_candidates",
    "revisions_of",
]

IssueSink = Callable[[str, str, Mapping[str, Any]], None]

PREFILL_STAGE = "metadata_prefill"
CHUNK_STAGE = "extraction_chunk"
SYNTHESIS_STAGE = "synthesis"

_REFERENCE_CODES = frozenset({"dangling_reference", "duplicate_reference", "ambiguous_reference"})
_DUPLICATE = "duplicate_reference"
_TEI_HEADER_YEAR = "tei_header"
_AUTHOR_FIELD = 1
_YEAR_FIELD = 2
_EXTENSION_CHUNK = "x-pipeline-chunk"
_REASON_LIMIT = 200


def _short(value: object, limit: int = 80) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _is_index(value: object) -> bool:
    # ``bool`` is an ``int`` subclass; a flag is never an index.
    return type(value) is int and value >= 0  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Issue sink (Requirement 5.4)
# ---------------------------------------------------------------------------


def _claim_id_for(source_id: str, field_index: object, revisions: Mapping[int, int] | None) -> str | None:
    """The claim identifier of ``field_index`` in ``source_id``, or ``None`` when it cannot be formed."""
    if not _is_index(field_index):
        return None
    revision = revisions.get(field_index, 0) if revisions is not None else 0  # type: ignore[arg-type]
    try:
        return derive_claim_id(source_id, field_index, revision)  # type: ignore[arg-type]
    except InvalidIdentifierError as exc:
        logger.debug("no claim identifier for field %s in %s: %s", field_index, _short(source_id), exc)
        return None


def claim_issue_sink(
    sink: IssueSink,
    source_id: str,
    *,
    revisions: Mapping[int, int] | None = None,
) -> IssueSink:
    """Wrap the recorder's issue sink for one document (``pipeline.validator``'s ``issue_sink``).

    The validator does not know the document, so the wrapper adds
    ``details["node_id"]`` -- the claim identifier of ``details["field_index"]``
    -- which is what makes a recorded finding name its claim. ``revisions`` maps
    a field index to the revision of its final claim (see
    :func:`plan_field_candidates`); it is read at call time, so a mapping filled
    in after the wrapper was built still applies. A call is forwarded only for
    the three reference codes. A ``dangling_reference`` or ``ambiguous_reference``
    about a scoped (``#``) reference is dropped: the validator judges references
    against local ids only, so it cannot tell a scoped id of this document from
    an unknown one, and the claim adapter, which knows the source, judges it. An
    item whose field index cannot name a claim is forwarded without ``node_id``
    rather than lost. The wrapper never raises.
    """

    def wrapped(code: str, message: str, details: Mapping[str, Any]) -> None:
        try:
            if code not in _REFERENCE_CODES:
                logger.debug("provenance issue sink: code %s is not a reference code; dropped", _short(code))
                return
            if not isinstance(details, Mapping):
                logger.debug("provenance issue sink: details are not a mapping; dropped")
                return
            reference = details.get("reference")
            if code != _DUPLICATE and isinstance(reference, str) and EVIDENCE_SCOPE_SEPARATOR in reference:
                return
            forwarded = dict(details)
            node_id = _claim_id_for(source_id, details.get("field_index"), revisions)
            if node_id is not None:
                forwarded["node_id"] = node_id
            sink(code, message, forwarded)
        except Exception as exc:  # noqa: BLE001 -- provenance must not change extraction
            logger.warning("provenance issue sink failed for a %s finding: %s", _short(code), str(exc)[:_REASON_LIMIT])

    return wrapped


# ---------------------------------------------------------------------------
# Header citations of the prefilled fields (decision B4)
# ---------------------------------------------------------------------------


def header_citations(
    bundle: EvidenceBundle,
    source_id: str,
    known_evidence_ids: AbstractSet[str],
) -> dict[int, str]:
    """Map a prefilled field index to the local id of the header metadata node its value came from.

    Field 1 (author) cites ``M-first-author`` only when the prefilled value is
    the TEI header's first author; field 2 (year) cites ``M-publication-date``
    only when its provenance is ``tei_header`` and equals the header's year. A
    value from a fallback cites nothing, and so does a header value whose node
    is not among ``known_evidence_ids`` (a citation to a node that does not
    exist would be dangling). Both checks read the *current* TEI through
    ``extract_header_metadata``, so a stale evidence cache cannot make a
    fallback value cite a header node.
    """
    citations: dict[int, str] = {}
    tei_xml = bundle.tei_xml
    if not isinstance(tei_xml, str) or not tei_xml.strip():
        return citations
    header = extract_header_metadata(tei_xml)
    prefilled = bundle.prefilled_fields

    def known(local_id: str) -> bool:
        return format_evidence_id(source_id, local_id) in known_evidence_ids

    author = prefilled.get(_AUTHOR_FIELD)
    if header.first_author and author == header.first_author and known(HEADER_AUTHOR_EVIDENCE_ID):
        citations[_AUTHOR_FIELD] = HEADER_AUTHOR_EVIDENCE_ID
    year = prefilled.get(_YEAR_FIELD)
    if (
        bundle.year_provenance == _TEI_HEADER_YEAR
        and header.publication_year
        and year == header.publication_year
        and known(HEADER_DATE_EVIDENCE_ID)
    ):
        citations[_YEAR_FIELD] = HEADER_DATE_EVIDENCE_ID
    return citations


# ---------------------------------------------------------------------------
# Candidates and revisions (Requirement 5.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldCandidates:
    """The chunk candidates behind one final claim, in chunk order.

    ``kind`` is ``"conflict"`` (synthesis resolved disagreeing candidates) or
    ``"merge"`` (several chunks agreed and the deterministic merge combined
    them). ``candidates`` pairs each chunk number with its compact entry; the
    candidate at position ``k`` is revision ``k`` of the field and the final
    claim is revision ``final_revision``. ``contributor_positions`` are the
    candidates the final claim was built from -- for a conflict, those the
    synthesis input showed; the rest (``dropped_positions``) were cut by the
    candidate cap.
    """

    field_index: int
    kind: Literal["conflict", "merge"]
    candidates: tuple[tuple[int, Mapping[str, Any]], ...]
    contributor_positions: tuple[int, ...]

    @property
    def final_revision(self) -> int:
        return len(self.candidates)

    @property
    def dropped_positions(self) -> tuple[int, ...]:
        kept = set(self.contributor_positions)
        return tuple(position for position in range(len(self.candidates)) if position not in kept)


def _has_value(entry: Mapping[str, Any]) -> bool:
    value = entry.get("v")
    return isinstance(value, str) and bool(value.strip())


def plan_field_candidates(
    chunk_results: Sequence[tuple[int, Sequence[Mapping[str, Any]]]],
    conflicts: Sequence[int],
    shown_by_field: Mapping[int, Sequence[Mapping[str, Any]]],
) -> dict[int, FieldCandidates]:
    """Decide which fields have candidates that become earlier revisions of the final claim.

    ``chunk_results`` pairs each extraction chunk number with its validated
    compact entries; ``conflicts`` are the field indices the deterministic merge
    left to synthesis; ``shown_by_field`` gives, for a conflict, the candidate
    entries the synthesis input showed (the same objects as in
    ``chunk_results``; a conflict absent from it counts every candidate as
    shown). A conflict with at least one entry is planned. A non-conflict field
    is planned as a ``merge`` when two or more chunks gave it a non-empty value;
    a field with one contributor, or none, has a single claim and is not planned.
    """
    by_field: dict[int, list[tuple[int, Mapping[str, Any]]]] = {}
    for chunk_num, entries in chunk_results:
        for entry in entries:
            index = entry.get("i") if isinstance(entry, Mapping) else None
            if _is_index(index):
                by_field.setdefault(index, []).append((chunk_num, entry))  # type: ignore[arg-type]

    plan: dict[int, FieldCandidates] = {}
    conflict_set = set(conflicts)
    for index in sorted(conflict_set):
        entries = by_field.get(index, [])
        if not entries:
            continue
        shown = shown_by_field.get(index)
        if shown is None:
            positions = tuple(range(len(entries)))
        else:
            positions = tuple(
                position for position, (_, entry) in enumerate(entries) if any(entry is seen for seen in shown)
            )
        plan[index] = FieldCandidates(index, "conflict", tuple(entries), positions)
    for index, entries in sorted(by_field.items()):
        if index in conflict_set:
            continue
        contributing = [(chunk_num, entry) for chunk_num, entry in entries if _has_value(entry)]
        if len(contributing) >= 2:
            plan[index] = FieldCandidates(index, "merge", tuple(contributing), tuple(range(len(contributing))))
    return plan


def revisions_of(plan: Mapping[int, FieldCandidates]) -> dict[int, int]:
    """Field index -> revision of its final claim, for every planned field."""
    return {index: candidates.final_revision for index, candidates in plan.items()}


# ---------------------------------------------------------------------------
# Per-document emission
# ---------------------------------------------------------------------------


def _reason(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= _REASON_LIMIT else text[: _REASON_LIMIT - 3] + "..."


def claim_stages_that_ran(
    prefilled: Sequence[Any],
    chunk_results: Sequence[Any],
    synthesis_ran: bool,
) -> tuple[str, ...]:
    """The claim stages a document went through, in stage order.

    For marking them failed when their records could not be written or the
    output was not persisted; a stage that did not run is never marked.
    """
    return tuple(
        stage
        for stage, ran in (
            (PREFILL_STAGE, bool(prefilled)),
            (CHUNK_STAGE, bool(chunk_results)),
            (SYNTHESIS_STAGE, synthesis_ran),
        )
        if ran
    )


class DocumentClaims:
    """Claim emission for one document, built once per ``process_pdf`` call.

    Holds the recorder, the document's ``source_id`` and the mutable
    field-to-final-revision map the issue sink reads. Every public method
    swallows ``Exception`` (see the module docstring).
    """

    def __init__(self, recorder: "ProvenanceRecorder", run_id: str, source_id: str) -> None:
        self.recorder = recorder
        self.run_id = run_id
        self.source_id = source_id
        self.revisions: dict[int, int] = {}
        self.plan: dict[int, FieldCandidates] = {}

    @classmethod
    def for_document(cls, provenance: "DocumentProvenance | None", pdf_name: str) -> "DocumentClaims | None":
        """The emitter for ``provenance``, or ``None`` when absent or its identity is unusable."""
        if provenance is None:
            return None
        try:
            source_id = derive_source_id(provenance.identity.pdf_content_hash)
            derive_claim_id(source_id, 0)  # the same identifier check the claim adapter applies
            return cls(provenance.recorder, provenance.run_id, source_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("claim provenance skipped for %s: %s", pdf_name, _reason(exc))
            return None

    # -- construction helpers -------------------------------------------------

    def evidence_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``build_or_load_evidence_bundle`` so evidence nodes exist before claims."""
        return {"recorder": self.recorder, "source_id": self.source_id}

    def issue_sink(self) -> IssueSink:
        """The per-document sink for ``validate_chunk_output`` / ``reconstruct_fields`` (via the repair loop)."""
        return claim_issue_sink(self.recorder.issue_sink(), self.source_id, revisions=self.revisions)

    def plan_candidates(
        self,
        chunk_results: Sequence[tuple[int, Sequence[Mapping[str, Any]]]],
        conflicts: Sequence[int],
        shown_by_field: Mapping[int, Sequence[Mapping[str, Any]]],
    ) -> None:
        """Compute the candidate plan and publish the final revisions to the issue sink."""
        try:
            self.plan = plan_field_candidates(chunk_results, conflicts, shown_by_field)
            self.revisions.clear()
            self.revisions.update(revisions_of(self.plan))
        except Exception as exc:  # noqa: BLE001
            logger.warning("candidate planning failed for %s: %s", self.source_id, _reason(exc))
            self.plan = {}
            self.revisions.clear()

    # -- stage marks ----------------------------------------------------------

    def mark_failed(self, stage: str, reason: str) -> None:
        """Mark ``stage`` failed for this document; never raises."""
        try:
            self.recorder.mark_stage_failed(stage, reason[:_REASON_LIMIT], source_id=self.source_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not mark %s failed for %s: %s", stage, self.source_id, _reason(exc))

    def mark_failed_all(self, stages: Sequence[str], reason: str) -> None:
        for stage in stages:
            self.mark_failed(stage, reason)

    # -- emission ---------------------------------------------------------------

    def emit(
        self,
        *,
        bundle: EvidenceBundle,
        field_lookup: Mapping[int, Mapping[str, Any]],
        chunk_model: str,
        synthesis_model: str,
        candidate_cap: int,
        prefilled: Sequence[Mapping[str, Any]],
        chunk_results: Sequence[tuple[int, Sequence[Mapping[str, Any]]]],
        merged_fields: Sequence[Mapping[str, Any]],
        synthesized_fields: Sequence[Mapping[str, Any]] | None,
    ) -> None:
        """Record the claims and steps of every claim stage that ran, then mark each complete.

        ``prefilled``, ``merged_fields`` and ``synthesized_fields`` are the field
        mappings exactly as ``process_pdf`` persisted them (``synthesized_fields``
        is ``None`` when synthesis did not run); ``chunk_results`` pairs each
        extraction chunk number with its validated compact entries. A stage that
        raises is marked failed; the others still run. Never raises.
        """
        try:
            nodes: Sequence[EvidenceNode] = bundle.provenance_evidence_nodes
            run = _Run(
                self,
                known={node.node_id for node in nodes},
                anchors={node.node_id: node.anchor for node in nodes},
                bundle=bundle,
                field_lookup=field_lookup,
                chunk_model=chunk_model,
                synthesis_model=synthesis_model,
                candidate_cap=candidate_cap,
            )
        except Exception as exc:  # noqa: BLE001
            self.mark_failed_all(
                claim_stages_that_ran(prefilled, chunk_results, synthesized_fields is not None), _reason(exc)
            )
            return
        for stage, emit in (
            (PREFILL_STAGE, lambda: run.prefill(prefilled)),
            (CHUNK_STAGE, lambda: run.extraction(chunk_results, merged_fields)),
            (SYNTHESIS_STAGE, lambda: run.synthesis(synthesized_fields)),
        ):
            try:
                emit()
            except Exception as exc:  # noqa: BLE001 -- provenance never aborts extraction
                logger.warning("claim provenance for %s failed in %s: %s", self.source_id, stage, _reason(exc))
                self.mark_failed(stage, _reason(exc))


class _Run:
    """The state shared by the three stage emitters of one ``DocumentClaims.emit`` call."""

    def __init__(
        self,
        owner: DocumentClaims,
        *,
        known: set[str],
        anchors: Mapping[str, SourceAnchor],
        bundle: EvidenceBundle,
        field_lookup: Mapping[int, Mapping[str, Any]],
        chunk_model: str,
        synthesis_model: str,
        candidate_cap: int,
    ) -> None:
        self.owner = owner
        self.recorder = owner.recorder
        self.source_id = owner.source_id
        self.run_id = owner.run_id
        self.plan = owner.plan
        self.known = known
        self.anchors = anchors
        self.bundle = bundle
        self.field_lookup = field_lookup
        self.chunk_model = chunk_model
        self.synthesis_model = synthesis_model
        self.candidate_cap = candidate_cap

    # -- shared -----------------------------------------------------------------

    def _convert(self, fields: Sequence[Mapping[str, Any]], origin: ClaimOrigin) -> list[ClaimNode]:
        """Convert ``fields`` to claims, record the findings, then each claim with its own edges."""
        claims, edges, findings = claim_nodes_from_fields(
            fields,
            source_id=self.source_id,
            run_id=self.run_id,
            known_evidence_ids=self.known,
            origin=origin,
            evidence_anchors=self.anchors,
        )
        for finding in findings:
            self.recorder.record_issue(finding)
        edges_by_claim: dict[str, list[Any]] = {}
        for edge in edges:
            edges_by_claim.setdefault(edge.from_id, []).append(edge)
        for claim in claims:
            self.recorder.record_claim(claim, edges_by_claim.get(claim.node_id, []))
        return claims

    def _claim_id(self, index: int, revision: int = 0) -> str:
        return derive_claim_id(self.source_id, index, revision)

    def _contributor_ids(self, candidates: FieldCandidates) -> list[str]:
        return [self._claim_id(candidates.field_index, position) for position in candidates.contributor_positions]

    def _step(self, stage: str, kind: Any, **kwargs: Any) -> None:
        self.recorder.record_derivation(
            derivation_from_stage_event(stage, kind, run_id=self.run_id, **kwargs)
        )

    @staticmethod
    def _cited(claims: Sequence[ClaimNode]) -> list[str]:
        cited: dict[str, None] = {}
        for claim in claims:
            for evidence_id in claim.cited_evidence_ids:
                cited.setdefault(evidence_id, None)
        return list(cited)

    # -- metadata_prefill ---------------------------------------------------------

    def prefill(self, prefilled: Sequence[Mapping[str, Any]]) -> None:
        if not prefilled:
            return
        citations = header_citations(self.bundle, self.source_id, self.known)
        fields = []
        for field in prefilled:
            local_id = citations.get(field.get("field_index"))  # type: ignore[arg-type]
            # A copy: the persisted field keeps its empty ``location``.
            fields.append({**field, "location": [local_id] if local_id else []})
        claims = self._convert(fields, "prefill")
        if not claims:
            raise ValueError("no prefilled field could become a claim")
        self._step(
            PREFILL_STAGE,
            "prefill",
            inputs=self._cited(claims),
            outputs=[claim.node_id for claim in claims],
        )
        self.recorder.mark_stage_complete(PREFILL_STAGE, source_id=self.source_id)

    # -- extraction_chunk ----------------------------------------------------------

    def extraction(
        self,
        chunk_results: Sequence[tuple[int, Sequence[Mapping[str, Any]]]],
        merged_fields: Sequence[Mapping[str, Any]],
    ) -> None:
        if not chunk_results:
            return
        producers = self._single_producers(chunk_results)
        produced: dict[int, list[ClaimNode]] = {chunk_num: [] for chunk_num, _ in chunk_results}

        candidate_fields: list[tuple[int, dict[str, Any]]] = []
        for index, planned in self.plan.items():
            for position, (chunk_num, entry) in enumerate(planned.candidates):
                (field,) = reconstruct_fields([dict(entry)], self.field_lookup, self.bundle.evidence_map)
                candidate_fields.append((chunk_num, {**field, "revision": position}))
        candidate_claims = self._convert([field for _, field in candidate_fields], "model") if candidate_fields else []
        chunk_of_candidate = {
            self._claim_id(field["field_index"], field["revision"]): chunk_num for chunk_num, field in candidate_fields
        }
        for claim in candidate_claims:
            produced[chunk_of_candidate[claim.node_id]].append(claim)

        final_fields: list[Mapping[str, Any]] = []
        for field in merged_fields:
            planned = self.plan.get(field.get("field_index"))  # type: ignore[arg-type]
            if planned is not None and planned.kind == "merge":
                final_fields.append(
                    {
                        **field,
                        "revision": planned.final_revision,
                        "contributing_claim_ids": self._contributor_ids(planned),
                    }
                )
            else:
                final_fields.append(field)
        final_claims = self._convert(final_fields, "model") if final_fields else []
        first_chunk = chunk_results[0][0]
        merged_claims: list[ClaimNode] = []
        for claim in final_claims:
            planned = self.plan.get(claim.field_index)
            if planned is not None and planned.kind == "merge":
                merged_claims.append(claim)
            else:
                produced[producers.get(claim.field_index, first_chunk)].append(claim)

        steps = 0
        for chunk_num, _ in chunk_results:
            claims = produced[chunk_num]
            if not claims:
                continue
            self._step(
                CHUNK_STAGE,
                "extract",
                inputs=self._cited(claims),
                outputs=[claim.node_id for claim in claims],
                model_id=self.chunk_model,
                extensions={_EXTENSION_CHUNK: chunk_num},
            )
            steps += 1
        for claim in merged_claims:
            planned = self.plan[claim.field_index]
            self._step(
                CHUNK_STAGE,
                "merge",
                inputs=self._contributor_ids(planned),
                outputs=[claim.node_id],
            )
            steps += 1
        if not steps:
            raise ValueError("no extraction chunk produced a claim")
        self.recorder.mark_stage_complete(CHUNK_STAGE, source_id=self.source_id)

    @staticmethod
    def _single_producers(chunk_results: Sequence[tuple[int, Sequence[Mapping[str, Any]]]]) -> dict[int, int]:
        """Field index -> the one chunk credited with a field that has a single claim.

        The first chunk with a non-empty value; for a field every chunk left
        empty, the first chunk that returned it. Exactly one chunk, so no
        output belongs to two steps.
        """
        first_valued: dict[int, int] = {}
        first_any: dict[int, int] = {}
        for chunk_num, entries in chunk_results:
            for entry in entries:
                index = entry.get("i")
                if not _is_index(index):
                    continue
                first_any.setdefault(index, chunk_num)  # type: ignore[arg-type]
                if _has_value(entry):
                    first_valued.setdefault(index, chunk_num)  # type: ignore[arg-type]
        return {**first_any, **first_valued}

    # -- synthesis -------------------------------------------------------------------

    def synthesis(self, synthesized_fields: Sequence[Mapping[str, Any]] | None) -> None:
        if synthesized_fields is None:
            return
        fields: list[Mapping[str, Any]] = []
        contributors: dict[str, None] = {}
        for field in synthesized_fields:
            planned = self.plan.get(field.get("field_index"))  # type: ignore[arg-type]
            if planned is not None and planned.kind == "conflict":
                ids = self._contributor_ids(planned)
                contributors.update(dict.fromkeys(ids))
                fields.append({**field, "revision": planned.final_revision, "contributing_claim_ids": ids})
            else:
                fields.append(field)
        claims = self._convert(fields, "model")
        if not claims:
            raise ValueError("synthesis produced no claim")
        self._step(
            SYNTHESIS_STAGE,
            "synthesize",
            inputs=[*contributors, *self._cited(claims)],
            outputs=[claim.node_id for claim in claims],
            model_id=self.synthesis_model,
        )
        dropped = [
            self._claim_id(planned.field_index, position)
            for planned in self.plan.values()
            if planned.kind == "conflict"
            for position in planned.dropped_positions
        ]
        if dropped:
            self._step(
                SYNTHESIS_STAGE,
                "remove",
                inputs=dropped,
                outputs=[],
                removal_reason=(
                    f"candidate not shown to synthesis: only the {self.candidate_cap} "
                    "highest-confidence candidates of a conflicting field are sent"
                ),
                removed_ids=dropped,
            )
        self.recorder.mark_stage_complete(SYNTHESIS_STAGE, source_id=self.source_id)
