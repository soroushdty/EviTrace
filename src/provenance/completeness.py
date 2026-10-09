"""completeness — provenance completeness at run, document and claim level, as data.

:func:`compute_completeness` (provenance-core design, "Analysis Layer →
CompletenessComputer") answers "is this artifact fully traceable?" for every
claim, every document and the run, returning a
:class:`~provenance.models.CompletenessReport`. It returns data only: it
formats nothing, renders nothing and writes no file (2.6). The caller attaches
the report with :func:`provenance.graph.attach_completeness`. This module
declares no dataclass; both report types live in :mod:`provenance.models`.

States are ``complete``, ``incomplete`` (a declared record is absent) and
``undetermined`` (completeness cannot be decided). Each
:class:`~provenance.models.ArtifactCompleteness` names the absent record kinds
and the responsible stages (2.3).

Effective stage status
    Stage status is evaluated per (document, stage): the effective status is
    the worse (``not_run < complete < failed``) of the run-wide
    ``RecordedRun.stage_status`` and that document's
    ``RecordedRun.document_stage_status``. Run-scoped contracts read the
    run-wide status only.

Stage-contract diff (2.1)
    Only stages whose effective status is ``complete`` are checked. A
    ``not_run`` stage is never treated as missing (``num_chunks=1`` runs no
    ``extraction_chunk``; ``chain_validation`` reads ``not_run`` until the
    orchestrator marks it). For a complete stage *S*, each record kind *K* its
    contract requires must be present:

    - ``run`` scope: some node of kind *K* counts toward *S*, else the run is
      ``incomplete``;
    - ``document`` and ``claim`` scope: each document for which *S* is
      effectively complete has a node of kind *K* that counts toward *S*,
      else that document is ``incomplete``.

    Document and stage attribution are the shared rules owned by
    :mod:`provenance.graph` (``document_of``, ``step_documents``,
    ``producing_stages``, ``sole_contract_stage``, ``attribute_stage``); this
    module keeps no copy of either. A derivation step counts toward its own
    ``stage`` and toward each document its input and output identifiers
    belong to. Any other node counts toward
    every stage of a derivation step that *produces* it (lists it in
    ``output_ids`` but not in ``input_ids`` — an in-place filter or annotate
    step produces nothing), and, when no step produces it, toward the one
    declared stage whose contract requires its kind (source →
    ``document_extraction``, evidence → ``evidence_index``, validation →
    ``chain_validation``).

    Claims carry no ``stage``. A claim is tied to the stages of the steps that
    produce it; not every claim-emitting stage is required for every claim. A
    claim no step produces is ``incomplete`` (missing ``derivation``) when a
    stage whose contract requires both ``claim`` and ``derivation`` is
    effectively complete for the claim's document; those stages are named
    responsible.

Claim support (2.4)
    Computed from the graph — the anchors of the evidence nodes a claim cites
    (``cited_evidence_ids`` and ``cites_evidence`` edges) — with the same rule
    as the claim adapter: ``supported`` when any cited evidence node has an
    ``exact`` or ``approximate`` anchor; otherwise ``missing_anchor`` when at
    least one cited identifier is an evidence node (all anchorless); otherwise
    ``unsupported`` (no reference, or none resolves to an evidence node). The
    two failures are reported with the distinct reasons ``"missing_anchor"``
    (missing kind ``anchor``, responsible: the stage of the anchorless
    evidence) and ``"unsupported"`` (missing kind ``evidence``, responsible:
    the stage that produced the claim), never merged. The responsible stage is
    :func:`provenance.graph.attribute_stage` — the rule the event-log
    projection and the graph builder's missing segments use too: the earliest
    declared stage among non-in-place producing steps, else the sole contract
    stage for the kind, else ``"unattributed"``.

Prefill policy (5.7)
    A claim of ``origin="prefill"`` is classified like any other claim, and
    its ``unsupported`` (a fallback value citing nothing) or
    ``missing_anchor`` (a TEI-header value citing a header metadata node that
    GROBID gives no coordinates or ``xml:id``) gap is reported on the claim.
    Those two gaps are reported **at claim level only**: they do not by
    themselves make the claim's document or the run incomplete (the signal is
    the ``prefill_unsupported`` and ``missing_anchor`` validation findings).
    Every other contribution of a prefill claim — ``missing_records``,
    ``undetermined`` — and every claim of another origin aggregates normally.

Undetermined (2.5)
    - *Run-wide failed stage* (``source_id=None``). The failure covers every
      derivation step of that stage (the run-wide ``record_kind="stage"``
      missing segment lists them, and the recorded status is honoured even
      without a segment): the claims those steps output and the documents
      their identifiers belong to become ``undetermined`` with the recorded
      reason. A document for which the failed stage recorded nothing at all is
      ``undetermined`` too, with a reason suffix that marks it as the likely
      failing one. A run-wide failure **always** makes the run
      ``undetermined`` with the recorded reason — whatever the stage's scope,
      and including a stage that recorded no derivation steps (e.g.
      ``document_extraction``, or ``evidence_index`` with only evidence
      nodes), whose records may cover every document.
    - *Per-document failed stage.* A failure marked for one document (its
      per-document missing segment names the source ID first, then that
      document's steps of the stage; the recorded per-document status is
      honoured even without a segment) makes only that document — and the
      claims of that document its steps output — ``undetermined``, with every
      recorded reason. Other documents stay determinable.
    - *Per-document mark without a source.* A per-document status for a
      source ID that has no source node makes the run ``undetermined``,
      naming the ID.
    - *Missing segment.* A record that could not be materialized covers the
      claim it names, the claim an unmaterialized edge leaves, or the document
      an identifier belongs to; a claim citing unmaterialized evidence with no
      usable alternative is ``undetermined``. A segment that names no placeable
      identifier (stage ``"unattributed"`` included) makes the run
      ``undetermined``.
    - *Validation not completed.* A chain-validation report (the ``report``
      argument, else the one attached to the graph) with ``completed=False``
      makes the run ``undetermined``.
    - A run with no document, or a document with no record and no completed
      stage, is ``undetermined`` rather than vacuously complete.
    - Malformed inputs (a non-mapping run-wide or per-document stage status,
      a malformed contract entry, an unusable document fingerprint) degrade to
      ``undetermined`` with a reason. Nothing here raises for well-typed
      arguments.

``stage_contracts_strict``
    A stage is *recorded* when its status is ``complete`` or ``failed``, it has
    a failure reason, a derivation step names it, or a missing segment is
    attributed to it (``"unattributed"`` excluded). When strict, a recorded
    stage absent from the contract table makes the run — and the claims and
    documents its steps cover — ``undetermined``; when not strict it is
    ignored (its steps still count as producing derivations). A recorded
    failure is honoured in both modes.

Aggregation (2.2)
    A document's state is the most severe of its own findings and its claims
    (subject to the prefill policy); the run's is the most severe of its own
    findings and its documents; ``undetermined`` outranks ``incomplete``. An aggregated artifact carries
    the reasons, missing kinds and responsible stages of the contributions at
    its own (most severe) state; reasons are joined with ``"; "`` in sorted
    order, kinds are sorted, stages follow the declared stage order.

Imports only the standard library (no I/O, clock, hashing or randomness
module), ``provenance.models``, ``provenance.identity``,
``provenance.contracts``, ``provenance.graph`` and ``utils.logging_utils``.
"""

from __future__ import annotations

import typing
from collections.abc import Iterable, Mapping

from provenance.contracts import STAGE_CONTRACTS
from provenance.graph import (
    UNATTRIBUTED_STAGE,
    VALIDATION_CHECK_NAME,
    ProvenanceGraph,
    attribute_stage,
    document_of,
    is_claim_identifier,
    producing_stages,
    sole_contract_stage,
    stage_segment_document,
    stage_sort_key,
    step_documents,
)
from provenance.identity import derive_source_id
from provenance.models import (
    ArtifactCompleteness,
    ChainValidationReport,
    ClaimNode,
    CompletenessReport,
    DerivationStep,
    EdgeKind,
    EvidenceNode,
    MissingSegment,
    ProvenanceNode,
    RecordedRun,
    SourceAnchor,
    SourceNode,
    StageContract,
)
from utils.logging_utils import get_logger

__all__ = ["compute_completeness"]

logger = get_logger(__name__)

_COMPLETE = "complete"
_INCOMPLETE = "incomplete"
_UNDETERMINED = "undetermined"
_STATE_RANK: Mapping[str, int] = {_COMPLETE: 0, _INCOMPLETE: 1, _UNDETERMINED: 2}

_FAILED = "failed"
_NOT_RUN = "not_run"
_STATUS_RANK: Mapping[str, int] = {_NOT_RUN: 0, _COMPLETE: 1, _FAILED: 2}
_STAGE_SEGMENT_KIND = "stage"
_SCOPES = frozenset({"run", "document", "claim"})

_REASON_UNSUPPORTED = "unsupported"
_REASON_MISSING_ANCHOR = "missing_anchor"
_REASON_MISSING_RECORDS = "missing_records"
_PREFILL_ORIGIN = "prefill"
_CLAIM_LEVEL_ONLY_REASONS = frozenset({_REASON_UNSUPPORTED, _REASON_MISSING_ANCHOR})
"""Support gaps of a prefill-origin claim that are reported on the claim but not aggregated (5.7)."""
_ANCHOR_KIND = "anchor"
_REASON_SEPARATOR = "; "

_USABLE_PRECISIONS = frozenset({"exact", "approximate"})
_EDGE_KINDS: tuple[str, ...] = typing.get_args(EdgeKind)

# A contribution: (state, reasons, missing record kinds, responsible stages).
_Contribution = tuple[str, frozenset[str], frozenset[str], frozenset[str]]


def _contribution(
    state: str, reason: str, kinds: Iterable[str] = (), stages: Iterable[str] = ()
) -> _Contribution:
    return (state, frozenset((reason,)), frozenset(kinds), frozenset(stages))


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(item for item in value if _text(item))


def _edge_ends(identifier: str) -> tuple[str, str] | None:
    """``(from_id, to_id)`` of an ``<kind>:<from>-><to>`` edge identifier."""
    for kind in _EDGE_KINDS:
        prefix = f"{kind}:"
        if identifier.startswith(prefix) and "->" in identifier:
            from_id, _, to_id = identifier[len(prefix):].partition("->")
            if from_id and to_id:
                return from_id, to_id
    return None


def _combine(contributions: Iterable[_Contribution]) -> ArtifactCompleteness:
    chosen = [c for c in contributions if c[0] != _COMPLETE]
    if not chosen:
        return ArtifactCompleteness(state=_COMPLETE, reason=None, missing_record_kinds=(), responsible_stages=())
    worst = max(chosen, key=lambda c: _STATE_RANK[c[0]])[0]
    chosen = [c for c in chosen if c[0] == worst]
    reasons = sorted(set().union(*(c[1] for c in chosen)))
    kinds = sorted(set().union(*(c[2] for c in chosen)))
    stages = sorted(set().union(*(c[3] for c in chosen)), key=stage_sort_key)
    return ArtifactCompleteness(
        state=worst,  # type: ignore[arg-type]
        reason=_REASON_SEPARATOR.join(reasons),
        missing_record_kinds=tuple(kinds),
        responsible_stages=tuple(stages),
    )


def _lift(result: ArtifactCompleteness) -> _Contribution:
    """An aggregated child result as a contribution to its parent (its reason kept whole)."""
    reasons = frozenset((result.reason,)) if result.reason else frozenset()
    return (result.state, reasons, frozenset(result.missing_record_kinds), frozenset(result.responsible_stages))


class _Analysis:
    """One computation's working state; built and discarded inside :func:`compute_completeness`."""

    def __init__(
        self,
        graph: ProvenanceGraph,
        recorded: RecordedRun,
        report: ChainValidationReport | None,
        contracts: object,
        strict: bool,
    ) -> None:
        self.graph = graph
        self.recorded = recorded
        self.report = report if report is not None else graph.validation
        self.strict = strict
        self.run: list[_Contribution] = []
        self.docs: dict[str, list[_Contribution]] = {}
        self.claims: dict[str, list[_Contribution]] = {}
        self.claim_doc: dict[str, str | None] = {}

        self.nodes: dict[str, ProvenanceNode] = {
            node_id: node
            for node_id, node in graph.nodes.items()
            if _text(node_id) and isinstance(node, ProvenanceNode)
        }
        self.steps: list[DerivationStep] = [
            node for node in self.nodes.values() if isinstance(node, DerivationStep) and _text(node.stage)
        ]
        self.segments: list[MissingSegment] = [
            segment for segment in graph.missing_segments if isinstance(segment, MissingSegment)
        ]
        self.contracts = self._normalize_contracts(contracts)
        self.status, self.failure_reasons = self._normalize_status()
        self.doc_status, self.doc_failure_reasons = self._normalize_document_status()

        # Producing stages (in-place steps excluded) and per-step document coverage: shared rules.
        self.producers: dict[str, frozenset[str]] = producing_stages(self.nodes)
        self.step_docs: dict[str, frozenset[str]] = {
            step.node_id: step_documents(step, self.nodes) for step in self.steps
        }

        # Which documents (and whether anything, run-wide) carry records of each (stage, kind).
        self.present_docs: dict[tuple[str, str], set[str]] = {}
        self.present_any: set[tuple[str, str]] = set()
        self.stage_docs: dict[str, set[str]] = {}
        for node in self.nodes.values():
            if isinstance(node, DerivationStep):
                stages = {node.stage} if _text(node.stage) else set()
                docs = self.step_docs.get(node.node_id, frozenset())
            else:
                stages = self.stages_of(node)
                doc = self.doc_of(node.node_id)
                docs = {doc} if doc else set()
            for stage in stages:
                self.present_any.add((stage, node.kind))
                self.present_docs.setdefault((stage, node.kind), set()).update(docs)
                self.stage_docs.setdefault(stage, set()).update(docs)

        self.documents = self._collect_documents()

    # ------------------------------------------------------------------
    # Normalization (malformed input degrades to an undetermined run)
    # ------------------------------------------------------------------

    def _normalize_contracts(self, contracts: object) -> dict[str, StageContract]:
        if not isinstance(contracts, Mapping):
            self.run.append(_contribution(_UNDETERMINED, "stage-contract table is not a mapping"))
            return {}
        accepted: dict[str, StageContract] = {}
        for stage, contract in contracts.items():
            if (
                _text(stage)
                and isinstance(contract, StageContract)
                and contract.scope in _SCOPES
                and isinstance(contract.required_record_kinds, tuple)
                and all(_text(kind) for kind in contract.required_record_kinds)
            ):
                accepted[stage] = contract
            else:
                name = stage if _text(stage) else UNATTRIBUTED_STAGE
                self.run.append(
                    _contribution(_UNDETERMINED, f"stage contract for {stage!r} is malformed", stages=(name,))
                )
        return accepted

    def _normalize_status(self) -> tuple[dict[str, str], dict[str, str]]:
        raw_status = self.recorded.stage_status
        if not isinstance(raw_status, Mapping):
            self.run.append(_contribution(_UNDETERMINED, "recorded stage status is unavailable"))
            raw_status = {}
        raw_reasons = self.recorded.stage_failure_reasons
        if not isinstance(raw_reasons, Mapping):
            raw_reasons = {}
        status = {stage: value for stage, value in raw_status.items() if _text(stage) and isinstance(value, str)}
        reasons = {stage: value for stage, value in raw_reasons.items() if _text(stage) and isinstance(value, str)}
        return status, reasons

    def _normalize_document_status(self) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, tuple[str, ...]]]]:
        """``source_id -> stage -> status`` and ``source_id -> stage -> distinct reasons`` (order kept)."""
        raw_status = self.recorded.document_stage_status
        if not isinstance(raw_status, Mapping):
            self.run.append(_contribution(_UNDETERMINED, "recorded per-document stage status is unavailable"))
            raw_status = {}
        raw_reasons = self.recorded.document_stage_failure_reasons
        if not isinstance(raw_reasons, Mapping):
            raw_reasons = {}
        status: dict[str, dict[str, str]] = {}
        for doc, by_stage in raw_status.items():
            if _text(doc) and isinstance(by_stage, Mapping):
                status[doc] = {
                    stage: value for stage, value in by_stage.items() if _text(stage) and isinstance(value, str)
                }
        reasons: dict[str, dict[str, tuple[str, ...]]] = {}
        for doc, by_stage in raw_reasons.items():
            if not (_text(doc) and isinstance(by_stage, Mapping)):
                continue
            for stage, values in by_stage.items():
                values = (values,) if isinstance(values, str) else values
                if _text(stage) and isinstance(values, (tuple, list)):
                    kept = tuple(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))
                    reasons.setdefault(doc, {})[stage] = kept
        return status, reasons

    def effective(self, doc: str | None, stage: str) -> str:
        """The worse of the run-wide and the per-document status of ``stage`` (2.5)."""
        run_wide = self.status.get(stage, _NOT_RUN)
        per_document = self.doc_status.get(doc, {}).get(stage, _NOT_RUN) if doc else _NOT_RUN
        return max((run_wide, per_document), key=lambda value: _STATUS_RANK.get(value, -1))

    # ------------------------------------------------------------------
    # Attribution
    # ------------------------------------------------------------------

    def doc_of(self, identifier: str) -> str | None:
        """The shared document-attribution rule (:func:`provenance.graph.document_of`)."""
        return document_of(identifier, self.nodes)

    def stages_of(self, node: ProvenanceNode) -> set[str]:
        """Every stage a non-derivation node's presence counts toward.

        Every stage that produces it (shared :func:`~provenance.graph.producing_stages`),
        else the sole contract stage for its kind; unlike the responsible
        stage, presence counts toward *each* producing stage.
        """
        produced = self.producers.get(node.node_id)
        if produced:
            return set(produced)
        sole = sole_contract_stage(node.kind, self.contracts)
        return {sole} if sole else set()

    def responsible(self, identifier: str) -> str:
        """The shared stage-attribution rule (:func:`provenance.graph.attribute_stage`)."""
        return attribute_stage(self.nodes.get(identifier, identifier), self.producers, contracts=self.contracts)[0]

    def is_claim(self, identifier: str) -> bool:
        return is_claim_identifier(identifier, self.nodes)

    def _collect_documents(self) -> set[str]:
        documents: set[str] = set()
        for node in self.nodes.values():
            if isinstance(node, (SourceNode, EvidenceNode, ClaimNode)):
                doc = self.doc_of(node.node_id)
                if doc:
                    documents.add(doc)
        for docs in self.step_docs.values():
            documents.update(docs)
        fingerprints = self.graph.run.document_fingerprints
        if isinstance(fingerprints, Mapping):
            for key, fingerprint in fingerprints.items():
                try:
                    documents.add(derive_source_id(fingerprint))
                except Exception as exc:  # noqa: BLE001 — an unusable fingerprint degrades, never raises
                    self.run.append(
                        _contribution(_UNDETERMINED, f"document fingerprint of {key!r} is not usable: {exc}")
                    )
        return documents

    # ------------------------------------------------------------------
    # Contribution helpers
    # ------------------------------------------------------------------

    def add_claim(self, claim_id: str, contribution: _Contribution) -> None:
        self.claims.setdefault(claim_id, []).append(contribution)
        if claim_id not in self.claim_doc:
            self.claim_doc[claim_id] = self.doc_of(claim_id)

    def add_doc(self, doc: str, contribution: _Contribution) -> None:
        self.documents.add(doc)
        self.docs.setdefault(doc, []).append(contribution)

    def cover(self, identifiers: Iterable[str], contribution: _Contribution) -> bool:
        """Mark the claims and documents ``identifiers`` cover; return whether any was placed."""
        placed = False
        for identifier in identifiers:
            if self.is_claim(identifier):
                self.add_claim(identifier, contribution)
                placed = True
                continue
            doc = self.doc_of(identifier)
            if doc:
                self.add_doc(doc, contribution)
                placed = True
        return placed

    def cover_steps(self, step_ids: Iterable[str], contribution: _Contribution) -> None:
        """Claims a step outputs and documents its identifiers belong to."""
        for step_id in step_ids:
            step = self.nodes.get(step_id)
            if not isinstance(step, DerivationStep):
                continue
            for output_id in _ids(step.output_ids):
                if self.is_claim(output_id):
                    self.add_claim(output_id, contribution)
            for doc in self.step_docs.get(step_id, ()):
                self.add_doc(doc, contribution)

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def recorded_stages(self) -> set[str]:
        stages = {stage for stage, value in self.status.items() if value in (_COMPLETE, _FAILED)}
        stages.update(self.failure_reasons)
        for by_stage in self.doc_status.values():
            stages.update(stage for stage, value in by_stage.items() if value in (_COMPLETE, _FAILED))
        for by_stage in self.doc_failure_reasons.values():
            stages.update(by_stage)
        stages.update(step.stage for step in self.steps)
        stages.update(
            segment.stage for segment in self.segments if _text(segment.stage) and segment.stage != UNATTRIBUTED_STAGE
        )
        return stages

    def check_undeclared_stages(self) -> None:
        for stage in sorted(self.recorded_stages() - set(self.contracts), key=stage_sort_key):
            if not self.strict:
                logger.debug("completeness: ignoring recorded stage %r absent from the contract table", stage)
                continue
            contribution = _contribution(
                _UNDETERMINED,
                f"stage {stage!r} is recorded but not declared in the stage-contract table "
                "(stage_contracts_strict)",
                stages=(stage,),
            )
            self.run.append(contribution)
            self.cover_steps((step.node_id for step in self.steps if step.stage == stage), contribution)

    def failed_stages(self) -> dict[str, tuple[str, set[str]]]:
        """Run-wide failures: ``stage -> (reason, affected ids)``."""
        failed: dict[str, tuple[str, set[str]]] = {}
        for segment in self.segments:
            if (
                segment.record_kind == _STAGE_SEGMENT_KIND
                and _text(segment.stage)
                and stage_segment_document(segment) is None
            ):
                reason = segment.reason if _text(segment.reason) else f"stage {segment.stage!r} failed"
                failed.setdefault(segment.stage, (reason, set(_ids(segment.affected_ids))))
        for stage, value in self.status.items():
            if value == _FAILED and stage not in failed:
                detail = self.failure_reasons.get(stage, "").strip()
                reason = f"stage {stage!r} failed: {detail}" if detail else f"stage {stage!r} failed (no reason recorded)"
                failed[stage] = (reason, set())
        return failed

    def document_failures(self) -> dict[tuple[str, str], tuple[str, set[str]]]:
        """Per-document failures: ``(source_id, stage) -> (reason, affected step ids)``."""
        failed: dict[tuple[str, str], tuple[str, set[str]]] = {}
        for segment in self.segments:
            doc = stage_segment_document(segment)
            if segment.record_kind != _STAGE_SEGMENT_KIND or not _text(segment.stage) or doc is None:
                continue
            reason = segment.reason if _text(segment.reason) else f"stage {segment.stage!r} failed for document {doc!r}"
            failed.setdefault((doc, segment.stage), (reason, set(_ids(segment.affected_ids)[1:])))
        for doc, by_stage in self.doc_status.items():
            for stage, value in by_stage.items():
                if value != _FAILED or (doc, stage) in failed:
                    continue
                details = self.doc_failure_reasons.get(doc, {}).get(stage, ())
                reason = (
                    f"stage {stage!r} failed for document {doc!r}: {_REASON_SEPARATOR.join(details)}"
                    if details
                    else f"stage {stage!r} failed for document {doc!r} (no reason recorded)"
                )
                failed[(doc, stage)] = (reason, set())
        return failed

    def check_document_failures(self) -> None:
        """A per-document failure makes only that document and the claims its steps output undetermined."""
        failures = sorted(self.document_failures().items(), key=lambda item: (item[0][0], stage_sort_key(item[0][1])))
        for (doc, stage), (reason, affected) in failures:
            contribution = _contribution(_UNDETERMINED, reason, stages=(stage,))
            self.add_doc(doc, contribution)
            step_ids = {
                step.node_id for step in self.steps if step.stage == stage and doc in self.step_docs.get(step.node_id, ())
            } | affected
            for step_id in sorted(step_ids):
                step = self.nodes.get(step_id)
                if not isinstance(step, DerivationStep):
                    continue
                for output_id in _ids(step.output_ids):
                    if self.is_claim(output_id) and self.doc_of(output_id) == doc:
                        self.add_claim(output_id, contribution)

    def check_document_marks_have_sources(self) -> None:
        sources = {node_id for node_id, node in self.nodes.items() if isinstance(node, SourceNode)}
        for doc in sorted((set(self.doc_status) | set(self.doc_failure_reasons)) - sources):
            stages = (*self.doc_status.get(doc, {}), *self.doc_failure_reasons.get(doc, {}))
            self.run.append(
                _contribution(
                    _UNDETERMINED,
                    f"stage status is recorded for document {doc!r}, which has no source node",
                    stages=stages,
                )
            )

    def check_failed_stages(self) -> None:
        self.check_document_failures()
        self.check_document_marks_have_sources()
        for stage, (reason, affected) in sorted(self.failed_stages().items(), key=lambda item: stage_sort_key(item[0])):
            contribution = _contribution(_UNDETERMINED, reason, stages=(stage,))
            step_ids = {step.node_id for step in self.steps if step.stage == stage} | affected
            self.cover_steps(sorted(step_ids), contribution)
            self.cover((i for i in sorted(affected) if not isinstance(self.nodes.get(i), DerivationStep)), contribution)
            # A run-wide failure always reaches the run, even when the stage recorded no steps (4.9).
            self.run.append(contribution)
            contract = self.contracts.get(stage)
            if contract is None or contract.scope == "run":
                continue
            covered = self.stage_docs.get(stage, set())
            silent = _contribution(
                _UNDETERMINED, f"{reason} (the stage recorded nothing for this document)", stages=(stage,)
            )
            for doc in sorted(self.documents - covered):
                self.add_doc(doc, silent)

    def check_missing_segments(self) -> dict[str, list[MissingSegment]]:
        """Cover what each unmaterialized record touches; return ``identifier -> segments``."""
        unmaterialized: dict[str, list[MissingSegment]] = {}
        for segment in self.segments:
            if segment.record_kind == _STAGE_SEGMENT_KIND:
                continue
            stage = segment.stage if _text(segment.stage) else UNATTRIBUTED_STAGE
            kind = segment.record_kind if _text(segment.record_kind) else "node"
            contribution = _contribution(_UNDETERMINED, self.segment_reason(segment), kinds=(kind,), stages=(stage,))
            placed = False
            for identifier in _ids(segment.affected_ids):
                unmaterialized.setdefault(identifier, []).append(segment)
                ends = _edge_ends(identifier) if kind == "edge" else None
                targets: tuple[str, ...] = ends if ends is not None else (identifier,)
                if ends is not None and self.is_claim(ends[0]):
                    targets = (ends[0],)
                placed = self.cover(targets, contribution) or placed
            if not placed:
                self.run.append(contribution)
        return unmaterialized

    @staticmethod
    def segment_reason(segment: MissingSegment) -> str:
        return f"missing segment ({segment.record_kind}, stage {segment.stage!r}): {segment.reason}"

    def check_contracts(self) -> None:
        # not_run is never missing; failed is handled as undetermined.
        for stage in sorted(self.contracts, key=stage_sort_key):
            contract = self.contracts[stage]
            if contract.scope == "run":
                if self.status.get(stage) != _COMPLETE:
                    continue
                missing = [kind for kind in contract.required_record_kinds if (stage, kind) not in self.present_any]
                if missing:
                    self.run.append(_contribution(_INCOMPLETE, _REASON_MISSING_RECORDS, missing, (stage,)))
                continue
            for doc in sorted(self.documents):
                if self.effective(doc, stage) != _COMPLETE:
                    continue
                missing = [
                    kind
                    for kind in contract.required_record_kinds
                    if doc not in self.present_docs.get((stage, kind), set())
                ]
                if missing:
                    self.add_doc(doc, _contribution(_INCOMPLETE, _REASON_MISSING_RECORDS, missing, (stage,)))

    def claim_stages(self, doc: str | None) -> list[str]:
        """Claim-emitting stages effectively complete for ``doc`` (run-wide status when ``doc`` is unknown)."""
        return [
            stage
            for stage in sorted(self.contracts, key=stage_sort_key)
            if {"claim", "derivation"} <= set(self.contracts[stage].required_record_kinds)
            and self.effective(doc, stage) == _COMPLETE
        ]

    def check_claims(self, unmaterialized: Mapping[str, list[MissingSegment]]) -> None:
        cited_by_edge: dict[str, list[str]] = {}
        for edge in self.graph.edges:
            if getattr(edge, "kind", None) == "cites_evidence" and _text(edge.from_id) and _text(edge.to_id):
                cited_by_edge.setdefault(edge.from_id, []).append(edge.to_id)
        for claim in (node for node in self.nodes.values() if isinstance(node, ClaimNode)):
            claim_id = claim.node_id
            self.claims.setdefault(claim_id, [])
            self.claim_doc[claim_id] = self.doc_of(claim_id)
            claim_stages = self.claim_stages(self.claim_doc[claim_id])

            if claim_id not in self.producers and claim_stages:
                self.add_claim(
                    claim_id, _contribution(_INCOMPLETE, _REASON_MISSING_RECORDS, ("derivation",), claim_stages)
                )

            cited = list(dict.fromkeys((*_ids(claim.cited_evidence_ids), *cited_by_edge.get(claim_id, ()))))
            evidence = [i for i in cited if isinstance(self.nodes.get(i), EvidenceNode)]
            if any(self._usable(self.nodes[i]) for i in evidence):  # type: ignore[arg-type]
                continue
            lost = [i for i in cited if i in unmaterialized]
            if lost:
                segments = [segment for i in lost for segment in unmaterialized[i]]
                reason = _REASON_SEPARATOR.join(sorted({self.segment_reason(s) for s in segments}))
                stages = {s.stage if _text(s.stage) else UNATTRIBUTED_STAGE for s in segments}
                self.add_claim(claim_id, _contribution(_UNDETERMINED, reason, ("evidence",), stages))
            elif evidence:
                stages = {self.responsible(i) for i in evidence}
                self.add_claim(claim_id, _contribution(_INCOMPLETE, _REASON_MISSING_ANCHOR, (_ANCHOR_KIND,), stages))
            else:
                self.add_claim(
                    claim_id,
                    _contribution(_INCOMPLETE, _REASON_UNSUPPORTED, ("evidence",), (self.responsible(claim_id),)),
                )

    @staticmethod
    def _usable(node: EvidenceNode) -> bool:
        anchor = node.anchor
        return isinstance(anchor, SourceAnchor) and anchor.precision in _USABLE_PRECISIONS

    def check_validation(self) -> None:
        if isinstance(self.report, ChainValidationReport) and self.report.completed is False:
            detail = self.report.incompletion_reason
            reason = "chain validation did not complete" + (f": {detail}" if _text(detail) else "")
            self.run.append(_contribution(_UNDETERMINED, reason, stages=(VALIDATION_CHECK_NAME,)))

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def aggregated_claim_state(self, claim_id: str, state: ArtifactCompleteness) -> ArtifactCompleteness:
        """The claim state its document (or the run) aggregates: ``state``, except under the prefill policy.

        For a prefill-origin claim the ``incomplete`` support gaps
        (``unsupported``, ``missing_anchor``) are dropped before combining, so
        they stay on the claim alone; its other contributions still count.
        """
        node = self.nodes.get(claim_id)
        if not isinstance(node, ClaimNode) or node.origin != _PREFILL_ORIGIN:
            return state
        kept = [
            contribution
            for contribution in self.claims[claim_id]
            if not (contribution[0] == _INCOMPLETE and contribution[1] and contribution[1] <= _CLAIM_LEVEL_ONLY_REASONS)
        ]
        return _combine(kept)

    def result(self) -> CompletenessReport:
        claims = {claim_id: _combine(self.claims[claim_id]) for claim_id in sorted(self.claims)}

        per_doc: dict[str, list[_Contribution]] = {doc: list(self.docs.get(doc, ())) for doc in self.documents}
        for claim_id, state in claims.items():
            lifted = _lift(self.aggregated_claim_state(claim_id, state))
            doc = self.claim_doc.get(claim_id)
            if doc:
                per_doc.setdefault(doc, []).append(lifted)
            else:
                self.run.append(lifted)

        has_records = {doc for docs in self.stage_docs.values() for doc in docs}
        has_records.update(doc for doc in self.claim_doc.values() if doc)
        for doc, contributions in per_doc.items():
            if doc not in has_records and not contributions:
                contributions.append(
                    _contribution(_UNDETERMINED, "no provenance records and no completed stage cover this document")
                )

        documents = {doc: _combine(per_doc[doc]) for doc in sorted(per_doc)}
        if not documents:
            self.run.append(_contribution(_UNDETERMINED, "no documents are recorded in the run"))
        run = _combine([*self.run, *(_lift(state) for state in documents.values())])
        return CompletenessReport(run=run, documents=documents, claims=claims)


def compute_completeness(
    graph: ProvenanceGraph,
    recorded: RecordedRun,
    report: ChainValidationReport | None = None,
    *,
    contracts: Mapping[str, StageContract] = STAGE_CONTRACTS,
    stage_contracts_strict: bool = True,
) -> CompletenessReport:
    """Compute provenance completeness at run, document and claim level (2.1–2.6).

    ``graph`` supplies the content (nodes, edges, missing segments, and the
    attached validation report when ``report`` is ``None``); ``recorded``
    supplies the run-wide and per-document stage status and failure reasons;
    ``contracts`` is the stage-contract table diffed against both;
    ``stage_contracts_strict`` is the ``provenance.stage_contracts_strict``
    setting. See the module docstring for the rules.

    Returns data only and never modifies ``graph``; attach the result with
    :func:`provenance.graph.attach_completeness`. Raises ``TypeError`` only
    when an argument is not of its declared type; any other failure degrades
    to an ``undetermined`` run carrying the reason.
    """
    if not isinstance(graph, ProvenanceGraph):
        raise TypeError(f"graph must be a ProvenanceGraph, got {type(graph).__name__}")
    if not isinstance(recorded, RecordedRun):
        raise TypeError(f"recorded must be a RecordedRun, got {type(recorded).__name__}")
    if report is not None and not isinstance(report, ChainValidationReport):
        raise TypeError(f"report must be a ChainValidationReport or None, got {type(report).__name__}")
    if not isinstance(stage_contracts_strict, bool):
        raise TypeError(f"stage_contracts_strict must be a bool, got {type(stage_contracts_strict).__name__}")

    try:
        analysis = _Analysis(graph, recorded, report, contracts, stage_contracts_strict)
        analysis.check_undeclared_stages()
        analysis.check_failed_stages()
        unmaterialized = analysis.check_missing_segments()
        analysis.check_contracts()
        analysis.check_claims(unmaterialized)
        analysis.check_validation()
        result = analysis.result()
    except Exception as exc:  # noqa: BLE001 — completeness degrades, it never aborts the run
        logger.warning("provenance completeness could not be computed: %s", exc)
        return CompletenessReport(
            run=ArtifactCompleteness(
                state=_UNDETERMINED,
                reason=f"completeness computation failed: {type(exc).__name__}: {exc}",
                missing_record_kinds=(),
                responsible_stages=(),
            ),
            documents={},
            claims={},
        )
    logger.debug(
        "provenance completeness: run %s, %d document(s), %d claim(s)",
        result.run.state,
        len(result.documents),
        len(result.claims),
    )
    return result
