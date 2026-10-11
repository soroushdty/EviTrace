"""run_provenance -- the run-scoped provenance lifecycle driven by the orchestrator.

Pipeline-side half of provenance-core task 6.5 (design, "PipelineIntegration":
"orchestrator.py constructs one ProvenanceRecorder per run ... passes it
explicitly", "Run end (orchestrator)", "Artifact directory"; Requirements 1.3,
1.4, 2.2, 8.5, 8.6, 8.7). It exists so ``orchestrator.run_pipeline`` gains three
short calls instead of ~250 lines of lifecycle code, and so every rule here is
testable without running a pipeline.

Lifecycle
---------
:func:`resolve_provenance_settings` / :func:`load_run_provenance_block`
    The ``provenance`` YAML block, validated. The block is merged with the
    documented defaults by ``config_utils.load_provenance_config``; this module
    adds the *value* validation that loader deliberately omits. An invalid value
    never aborts the run: it is reported with a WARNING naming the key and the
    value, and the documented default is used.
:func:`start_run_provenance`
    Before any document is processed: plan the documents (``document_provenance``
    owns identity computation), build the run identity and construct the ONE
    :class:`~provenance.recorder.ProvenanceRecorder` of the run. Returns ``None``
    -- "run without provenance" -- when provenance is disabled, when there is no
    document to record (every document resumed or without a usable fingerprint),
    or when anything fails. The returned :class:`RunProvenance` is passed
    explicitly; there is no module-level state.
:meth:`RunProvenance.document`, :meth:`RunProvenance.record_document_failure`,
:meth:`RunProvenance.record_extraction_failure`
    The per-document hooks ``_bounded`` calls.
:func:`finish_run_provenance`
    After processing: snapshot, build the graph, validate it, attach the
    validation, mark ``chain_validation`` run-wide, take a POST-validation
    snapshot, compute completeness from it, attach it, resolve the artifact
    directory and write the artifact. Never raises ``Exception``.

Degradation (provenance never aborts extraction)
------------------------------------------------
Every sub-step of :func:`finish_run_provenance` has its own guard, so a failure
degrades the artifact instead of discarding it where an honest artifact is still
possible:

* graph assembly fails -> nothing to write; no artifact;
* validation (or attaching it) fails -> ``chain_validation`` is marked FAILED with
  the reason, the run reads unvalidated (8.6), and the artifact is still written;
* completeness fails -> the graph carries an ``undetermined`` run entry with the
  reason (2.5) rather than none;
* writing fails (``ValueError`` / ``TypeError`` / ``OSError`` -- the writer encodes
  everything before touching the filesystem and removes its temporary file, so no
  partial artifact exists) -> no artifact.

Each failure is logged as a WARNING (traceback at DEBUG) and listed in
:attr:`ProvenanceRunSummary.errors`.

Run summary
-----------
This codebase has no run-summary artifact other than the run log and
``token_report.json``, and ``run_pipeline`` must keep returning the extracted
results, so "the run summary" is the INFO line logged by
:func:`finish_run_provenance` plus the :class:`ProvenanceRunSummary` it returns
(the structure tests assert on). ``provenance.fail_run_on_severity`` sets
:attr:`ProvenanceRunSummary.failed` and logs an ERROR when the findings reach
that severity or worse; extraction output has already been written by then.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from provenance.completeness import compute_completeness
from provenance.contracts import STAGE_CONTRACTS
from provenance.graph import attach_completeness, attach_validation, build_graph
from provenance.models import ArtifactCompleteness, CompletenessReport
from provenance.recorder import ProvenanceRecorder
from provenance.serialization import write_graph
from provenance.validation import validate_chain
from utils import path_utils
from utils.logging_utils import get_logger

from .claim_provenance import DocumentClaims
from .document_provenance import (
    DocumentProvenance,
    build_run_identity,
    emit_document_failure,
    plan_run_documents,
)

logger = get_logger(__name__)

__all__ = [
    "CHAIN_VALIDATION_STAGE",
    "DEFAULT_OUTPUT_SUBDIR",
    "FAIL_ON_SEVERITIES",
    "ProvenanceRunSummary",
    "ProvenanceSettings",
    "RunProvenance",
    "artifact_directory",
    "finish_run_provenance",
    "load_run_provenance_block",
    "resolve_provenance_settings",
    "start_run_provenance",
    "validated_output_subdir",
]

CHAIN_VALIDATION_STAGE = "chain_validation"
DEFAULT_OUTPUT_SUBDIR = "provenance"
FAIL_ON_SEVERITIES: tuple[str, ...] = ("critical", "error")
"""The values ``provenance.fail_run_on_severity`` accepts besides ``None``."""

_SEVERITY_RANK: Mapping[str, int] = {"critical": 0, "error": 1, "warning": 2, "info": 3}
_REASON_LIMIT = 300
_VALUE_LIMIT = 80


def _describe_value(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _VALUE_LIMIT else text[: _VALUE_LIMIT - 3] + "..."


def _describe_error(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}"
    return text if len(text) <= _REASON_LIMIT else text[: _REASON_LIMIT - 3] + "..."


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceSettings:
    """The validated ``provenance`` configuration; every field has the documented type."""

    enabled: bool = True
    output_subdir: str = DEFAULT_OUTPUT_SUBDIR
    severity_overrides: Mapping[str, Any] = field(default_factory=dict)
    fail_run_on_severity: str | None = None
    stage_contracts_strict: bool = True


_SEGMENT_SEPARATORS = re.compile(r"[\\/]+")
_LEGACY_RUN_ROOT_FRAGMENT = "outputs"  # resolve_run_output_path maps this fragment to the run root


def validated_output_subdir(value: object) -> str:
    """Return ``value`` as a usable run-folder subdirectory, or ``"provenance"`` after a WARNING.

    Rejected (Design, "Artifact directory"): anything that is not a string, an
    empty or blank string, an absolute path (POSIX, Windows, drive-qualified, or
    with a leading separator -- ``resolve_run_output_path`` would silently strip
    the separator and accept it), any value with a ``..`` segment, and any value
    that names the run folder itself (``"."``, ``"./"``, ``"./."``, ``"outputs"``
    -- ``resolve_run_output_path`` maps the legacy ``"outputs"`` to the run root):
    the artifact must be stored apart from the extraction outputs (Requirement 1.3).
    """
    reason: str | None = None
    if not isinstance(value, str):
        reason = "it is not a string"
    else:
        text = value.strip()
        if not text:
            reason = "it is empty"
        elif "\x00" in text:
            reason = "it contains a NUL character"
        elif (
            PurePosixPath(text).is_absolute()
            or PureWindowsPath(text).is_absolute()
            or bool(PureWindowsPath(text).drive)
            or text.startswith(("/", "\\"))
        ):
            reason = "it is an absolute path"
        elif ".." in _SEGMENT_SEPARATORS.split(text):
            reason = "it contains a '..' segment"
        elif not [seg for seg in _SEGMENT_SEPARATORS.split(text) if seg not in ("", ".")] or text.rstrip(
            "/\\"
        ) in (".", _LEGACY_RUN_ROOT_FRAGMENT):
            reason = "it names the run folder itself, which holds the extraction outputs"
    if reason is not None:
        logger.warning(
            "provenance.output_subdir %s is not usable (%s); using %r under the run folder",
            _describe_value(value),
            reason,
            DEFAULT_OUTPUT_SUBDIR,
        )
        return DEFAULT_OUTPUT_SUBDIR
    assert isinstance(value, str)
    return value.strip()


def _bool_setting(block: Mapping[str, Any], key: str, default: bool) -> bool:
    value = block.get(key, default)
    if isinstance(value, bool):
        return value
    logger.warning(
        "provenance.%s must be a boolean (true or false), got %s; using %s",
        key,
        _describe_value(value),
        default,
    )
    return default


def resolve_provenance_settings(block: Mapping[str, Any] | None) -> ProvenanceSettings:
    """Validate the merged ``provenance`` block into :class:`ProvenanceSettings`.

    ``block`` is what ``config_utils.load_provenance_config`` returns (``None``
    means defaults). Validation never raises; each rejected value is reported in
    a WARNING and replaced by its documented default:

    ``enabled``, ``stage_contracts_strict``
        must be a real ``bool``. A string such as ``"false"`` is NOT interpreted
        (it would be truthy); the default ``True`` applies, with a WARNING.
    ``severity_overrides``
        ``None`` (a bare ``severity_overrides:`` in YAML) means no overrides; any
        other non-mapping is ignored with a WARNING. Entries of a mapping are
        passed to ``validate_chain``, which ignores unusable ones itself.
    ``fail_run_on_severity``
        ``None``, ``"critical"`` or ``"error"``; anything else is ``None``.
    ``output_subdir``
        see :func:`validated_output_subdir`.
    """
    if block is None:
        block = {}
    elif not isinstance(block, Mapping):
        logger.warning("the provenance configuration must be a mapping, got %s; using defaults", _describe_value(block))
        block = {}

    overrides_raw = block.get("severity_overrides")
    if overrides_raw is None:
        overrides: Mapping[str, Any] = {}
    elif isinstance(overrides_raw, Mapping):
        overrides = dict(overrides_raw)
    else:
        logger.warning(
            "provenance.severity_overrides must be a mapping of finding code to severity, got %s; ignoring it",
            _describe_value(overrides_raw),
        )
        overrides = {}

    fail_on = block.get("fail_run_on_severity")
    if fail_on is not None and fail_on not in FAIL_ON_SEVERITIES:
        logger.warning(
            "provenance.fail_run_on_severity must be null, 'critical' or 'error', got %s; ignoring it",
            _describe_value(fail_on),
        )
        fail_on = None

    return ProvenanceSettings(
        enabled=_bool_setting(block, "enabled", True),
        output_subdir=validated_output_subdir(block.get("output_subdir", DEFAULT_OUTPUT_SUBDIR)),
        severity_overrides=overrides,
        fail_run_on_severity=fail_on,
        stage_contracts_strict=_bool_setting(block, "stage_contracts_strict", True),
    )


def load_run_provenance_block(config_path: str | None = None) -> dict[str, Any]:
    """Return the ``provenance`` YAML block merged over the documented defaults.

    ``load_local_config`` does not expose the block (it returns only the local
    keys), so the YAML mapping is read with ``config_utils._load_config_yaml`` --
    the loader every other config reader in ``config_utils`` is built on -- and
    handed to the public ``load_provenance_config``. A missing or unreadable
    file yields the defaults (the orchestrator has already loaded the same file).
    """
    from utils import config_utils  # noqa: PLC0415 -- keeps the YAML read out of import time

    try:
        return config_utils.load_provenance_config(config_utils._load_config_yaml(config_path))
    except Exception as exc:  # noqa: BLE001 -- provenance must not abort extraction
        logger.warning("provenance: cannot read the provenance configuration (%s); using defaults", _describe_error(exc))
        return config_utils.load_provenance_config({})


def artifact_directory(settings: ProvenanceSettings) -> Path:
    """Resolve the directory the artifact is written to: ``<run folder>/<output_subdir>``.

    ``settings.output_subdir`` has already passed :func:`validated_output_subdir`;
    this adds the containment check on the RESOLVED path (a symlink inside the run
    folder could still lead out of it) and falls back to the default subdirectory
    with a WARNING when the check fails or when the path is the run folder itself.
    """
    run_root = path_utils.resolve_run_output_path("")
    resolved = path_utils.resolve_run_output_path(settings.output_subdir)
    if resolved == run_root:
        logger.warning(
            "provenance.output_subdir %s resolves to the run folder %s, which holds the extraction outputs; using %r",
            _describe_value(settings.output_subdir),
            run_root,
            DEFAULT_OUTPUT_SUBDIR,
        )
        return path_utils.resolve_run_output_path(DEFAULT_OUTPUT_SUBDIR)
    try:
        resolved.relative_to(run_root)
    except ValueError:
        logger.warning(
            "provenance.output_subdir %s resolves to %s, outside the run folder %s; using %r",
            _describe_value(settings.output_subdir),
            resolved,
            run_root,
            DEFAULT_OUTPUT_SUBDIR,
        )
        return path_utils.resolve_run_output_path(DEFAULT_OUTPUT_SUBDIR)
    return resolved


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceRunSummary:
    """What the run-end routine did and found; also what its INFO summary line reports.

    ``failed`` is the ``fail_run_on_severity`` verdict (findings reached the
    configured severity); ``errors`` lists the sub-steps that raised and were
    degraded. ``artifact_path`` is ``None`` when no artifact was written.
    """

    run_id: str | None
    artifact_path: Path | None = None
    node_counts: Mapping[str, int] = field(default_factory=dict)
    edge_count: int = 0
    finding_counts: Mapping[str, int] = field(default_factory=dict)
    completeness_state: str | None = None
    validated: bool = False
    failed: bool = False
    failure_reason: str | None = None
    errors: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Run scope
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RunProvenance:
    """One run's provenance state: the recorder, the settings and the per-document hooks.

    Built once by :func:`start_run_provenance` and passed explicitly. ``documents``
    maps the INDEX in the run's PDF list to the document's
    :class:`~pipeline.document_provenance.DocumentProvenance`, and holds only the
    first PDF of each stem that was planned -- a resumed document, a document
    without a usable fingerprint and the second of two same-stem PDFs have no entry
    and therefore receive no provenance.
    """

    settings: ProvenanceSettings
    recorder: ProvenanceRecorder
    run_id: str
    documents: Mapping[int, DocumentProvenance]
    names: Mapping[int, str]

    def document(self, index: int) -> DocumentProvenance | None:
        """The provenance to pass to ``build_qc_bundle`` / ``process_pdf`` for PDF ``index``, or ``None``."""
        return self.documents.get(index)

    def record_document_failure(self, index: int, error: BaseException) -> None:
        """Represent a document whose ``build_qc_bundle`` raised: its source, ``document_extraction`` failed.

        A no-op for a document without provenance. Never raises.
        """
        document = self.documents.get(index)
        if document is None:
            return
        try:
            emit_document_failure(
                self.recorder,
                document.identity,
                error,
                pdf_name=self.names.get(index, ""),
                run_id=self.run_id,
            )
        except Exception as exc:  # noqa: BLE001 -- provenance must not abort extraction
            logger.warning("provenance: could not record the failure of %s (%s)", self.names.get(index), _describe_error(exc))

    def record_extraction_failure(self, index: int, error: BaseException) -> None:
        """Mark a document's claim-scoped stages failed because ``process_pdf`` RAISED.

        A ``process_pdf`` that returns ``None`` has already marked what failed
        (6.4); an exception escaping it leaves the claim stages unmarked. Stages
        the document already completed or failed are left alone (status only
        escalates, and a completed stage must not be rewritten as failed). A no-op
        for a document without provenance. Never raises.
        """
        document = self.documents.get(index)
        if document is None:
            return
        name = self.names.get(index, "")
        try:
            claims = DocumentClaims.for_document(document, name)
            if claims is None:
                return
            decided = self.recorder.snapshot().document_stage_status.get(claims.source_id, {})
            stages = [
                stage
                for stage, contract in STAGE_CONTRACTS.items()
                if contract.scope == "claim" and decided.get(stage, "not_run") == "not_run"
            ]
            claims.mark_failed_all(stages, _describe_error(error))
        except Exception as exc:  # noqa: BLE001 -- provenance must not abort extraction
            logger.warning("provenance: could not mark the claim stages of %s failed (%s)", name, _describe_error(exc))


def start_run_provenance(
    pdf_paths: Sequence[Path],
    manifest: Mapping[str, Any],
    *,
    openai_config: Mapping[str, Any],
    qc_config: Mapping[str, Any],
    settings: ProvenanceSettings,
    clock: Callable[[], str] = _utc_now_iso,
) -> RunProvenance | None:
    """Plan the run's documents and construct its recorder, or return ``None`` (run without provenance).

    ``openai_config`` and ``qc_config`` are the UNMODIFIED resolved configs (never
    the orchestrator's flat ``runtime_config``): they feed the configuration
    fingerprint. ``clock`` supplies ``created_at`` (an ISO 8601 UTC string); no
    clock is read anywhere else. ``None`` is returned, without raising, when
    provenance is disabled, when no document would be recorded (all resumed or
    unreadable -- an empty run has no run identity to build, so no artifact is
    produced), or when planning, the run identity or the recorder fails.
    """
    if not settings.enabled:
        logger.info("Provenance is disabled (provenance.enabled: false); no provenance artifact will be produced")
        return None
    try:
        identities = plan_run_documents(pdf_paths, manifest, openai_config=openai_config, qc_config=qc_config)
        if not identities:
            logger.info(
                "Provenance: no document of this run needs recording (all resumed or without a usable "
                "fingerprint); no provenance artifact will be produced"
            )
            return None
        run = build_run_identity(
            identities,
            chunk_model=openai_config["chunk_model"],
            synthesis_model=openai_config.get("synthesis_model"),
            created_at=clock(),
        )
        if run is None:
            return None
        recorder = ProvenanceRecorder(run, enabled=settings.enabled)
    except Exception as exc:  # noqa: BLE001 -- provenance must not abort extraction
        logger.warning("Provenance: cannot start for this run (%s); continuing without provenance", _describe_error(exc))
        logger.debug("provenance start failure", exc_info=True)
        return None

    documents: dict[int, DocumentProvenance] = {}
    names: dict[int, str] = {}
    claimed: set[str] = set()
    for index, pdf_path in enumerate(pdf_paths):
        name = Path(pdf_path).stem
        names[index] = name
        if name in claimed:
            continue  # the second of two same-stem PDFs: the plan holds the first
        claimed.add(name)
        identity = identities.get(name)
        if identity is not None:
            documents[index] = DocumentProvenance(recorder, identity, run.run_id)
    logger.info(
        "Provenance enabled: %d of %d document(s) will be recorded (run %s)",
        len(documents),
        len(pdf_paths),
        run.run_id,
    )
    return RunProvenance(settings, recorder, run.run_id, documents, names)


# ---------------------------------------------------------------------------
# Run end
# ---------------------------------------------------------------------------


class _Degradations:
    """Collects the sub-steps that failed, logging each one (WARNING, traceback at DEBUG)."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, step: str, error: BaseException) -> str:
        reason = _describe_error(error)
        self.messages.append(f"{step} failed: {reason}")
        logger.warning("Provenance: %s failed (%s); degrading", step, reason)
        logger.debug("provenance %s failure", step, exc_info=error)
        return reason


def _undetermined_completeness(reason: str) -> CompletenessReport:
    return CompletenessReport(
        run=ArtifactCompleteness(
            state="undetermined",
            reason=f"completeness could not be computed: {reason}",
            missing_record_kinds=(),
            responsible_stages=(),
        ),
        documents={},
        claims={},
    )


def _severity_counts(findings: Sequence[Any]) -> dict[str, int]:
    counts = Counter(str(getattr(finding, "severity", "unknown")) for finding in findings)
    return {severity: counts[severity] for severity in sorted(counts, key=lambda s: (_SEVERITY_RANK.get(s, 99), s))}


def _reaches(counts: Mapping[str, int], threshold: str | None) -> bool:
    if threshold is None:
        return False
    limit = _SEVERITY_RANK[threshold]
    return any(n > 0 and _SEVERITY_RANK.get(severity, 99) <= limit for severity, n in counts.items())


def _log_summary(summary: ProvenanceRunSummary) -> None:
    nodes = ", ".join(f"{kind}={n}" for kind, n in sorted(summary.node_counts.items())) or "none"
    findings = ", ".join(f"{severity}={n}" for severity, n in summary.finding_counts.items()) or "none"
    logger.info(
        "Provenance summary: run=%s nodes=%d (%s) edges=%d findings=[%s] completeness=%s validated=%s "
        "artifact=%s provenance_failure=%s",
        summary.run_id,
        sum(summary.node_counts.values()),
        nodes,
        summary.edge_count,
        findings,
        summary.completeness_state or "n/a",
        summary.validated,
        summary.artifact_path if summary.artifact_path is not None else "not written",
        summary.failed,
    )


def finish_run_provenance(plan: RunProvenance) -> ProvenanceRunSummary:
    """Build, validate, complete and write the run's provenance artifact; log and return the summary.

    The sequence (design, "Run end"): snapshot -> ``build_graph`` ->
    ``validate_chain(graph, severity_overrides=..., recorded_findings=
    recorded.findings)`` -> ``attach_validation`` -> mark ``chain_validation``
    run-wide complete (or failed with the incompletion reason) -> POST-validation
    snapshot -> ``compute_completeness(..., stage_contracts_strict=<bool>)`` ->
    ``attach_completeness`` -> ``write_graph(graph, <artifact directory>)``.

    Never raises ``Exception`` (see the module docstring for what each failure
    degrades to). Call it after every document has finished and the extraction
    output has been written.
    """
    run_id: str | None = getattr(plan, "run_id", None)
    try:
        return _finish(plan)
    except Exception as exc:  # noqa: BLE001 -- the last line of defence: provenance never aborts extraction
        reason = _describe_error(exc)
        logger.warning("Provenance: the run-end routine failed unexpectedly (%s); no artifact written", reason)
        logger.debug("provenance run-end failure", exc_info=True)
        summary = ProvenanceRunSummary(run_id=run_id, errors=(f"run end: {reason}",))
        _log_summary(summary)
        return summary


def _finish(plan: RunProvenance) -> ProvenanceRunSummary:
    settings = plan.settings
    recorder = plan.recorder
    degraded = _Degradations()

    try:
        recorded = recorder.snapshot()
        graph = build_graph(recorded)
    except Exception as exc:  # noqa: BLE001
        degraded.add("graph assembly", exc)
        summary = ProvenanceRunSummary(run_id=plan.run_id, errors=tuple(degraded.messages))
        _log_summary(summary)
        return summary

    report = None
    validation_error: str | None = None
    try:
        report = validate_chain(
            graph,
            severity_overrides=settings.severity_overrides,
            recorded_findings=recorded.findings,
        )
        graph = attach_validation(graph, report)
    except Exception as exc:  # noqa: BLE001
        validation_error = degraded.add("chain validation", exc)
        report = None

    # chain_validation is a run-wide stage: complete only when the validator ran to
    # the end AND its report is on the graph; a run the validator could not finish is
    # unvalidated (8.6), never valid.
    try:
        if report is not None and report.completed:
            recorder.mark_stage_complete(CHAIN_VALIDATION_STAGE)
        else:
            reason = validation_error or (report.incompletion_reason if report is not None else None)
            recorder.mark_stage_failed(CHAIN_VALIDATION_STAGE, reason or "chain validation did not complete")
    except Exception as exc:  # noqa: BLE001 -- e.g. UnknownStageError if the stage table drifted
        degraded.add("marking chain_validation", exc)

    completeness: CompletenessReport
    try:
        post_validation = recorder.snapshot()
        completeness = compute_completeness(
            graph, post_validation, stage_contracts_strict=settings.stage_contracts_strict
        )
    except Exception as exc:  # noqa: BLE001
        completeness = _undetermined_completeness(degraded.add("completeness", exc))
    try:
        graph = attach_completeness(graph, completeness)
    except Exception as exc:  # noqa: BLE001
        degraded.add("attaching completeness", exc)

    artifact_path: Path | None = None
    try:
        artifact_path = write_graph(graph, artifact_directory(settings))
    except Exception as exc:  # noqa: BLE001 -- ValueError / TypeError / OSError from the writer, or anything else
        degraded.add("writing the artifact", exc)

    findings = report.findings if report is not None else recorded.findings
    finding_counts = _severity_counts(findings)
    node_counts = dict(Counter(node.kind for node in graph.nodes.values()))
    threshold = settings.fail_run_on_severity
    failed = _reaches(finding_counts, threshold)
    failure_reason = (
        f"provenance findings reached severity {threshold!r} or worse ("
        + ", ".join(f"{severity}={n}" for severity, n in finding_counts.items())
        + ")"
        if failed
        else None
    )
    summary = ProvenanceRunSummary(
        run_id=plan.run_id,
        artifact_path=artifact_path,
        node_counts=node_counts,
        edge_count=len(graph.edges),
        finding_counts=finding_counts,
        completeness_state=completeness.run.state,
        validated=bool(report is not None and report.completed),
        failed=failed,
        failure_reason=failure_reason,
        errors=tuple(degraded.messages),
    )
    _log_summary(summary)
    if failed:
        logger.error(
            "Provenance failure: %s (provenance.fail_run_on_severity=%s); the extraction output was still written",
            failure_reason,
            threshold,
        )
    return summary
