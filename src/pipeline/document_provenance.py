"""document_provenance -- source nodes and QC derivation steps for the extraction pipeline.

Pipeline-side half of the provenance integration for the ``document_extraction``
and ``quality_control`` stages (provenance-core design, "PipelineIntegration";
Requirements 1.1, 1.3, 3.1, 3.5, 6.1). ``pipeline`` is the sole integrator, so
this module -- not the ``provenance`` package -- computes document identities
and reads ``unified.content["provenance"]``; ``quality_control`` is not touched.

Run scope (called once per run, before any document is processed)
------------------------------------------------------------------
:func:`plan_run_documents`
    Computes each document's :class:`~pipeline.manifest.ManifestIdentity` with
    ``manifest.compute_identity`` -- the single place content is hashed -- and
    returns the identities of the documents *actually processed*:

    * a **resumed** document (the one ``pdf_processor.process_pdf`` skips,
      decided by :func:`pdf_processor.is_resumed`) is excluded: no identity, no
      fingerprint in the run identity, no source node;
    * a document whose identity carries a manifest **sentinel** (``"nohash"``
      for an unreadable PDF, ``"no_extraction_map"`` for a missing extraction
      map) has no usable fingerprint and is excluded too, with a WARNING. It
      is still extracted normally -- it simply does not appear in provenance.
      A missing extraction map is run-wide, so it excludes every document and
      the run's provenance is empty (``undetermined``).

:func:`build_run_identity`
    Builds the :class:`~provenance.models.RunIdentity` from those identities,
    adding the synthesis model to the model identifiers when it differs from
    the chunk model (3.5). ``created_at`` is passed in (an ISO 8601 UTC string);
    no clock is read here. When the adapter refuses (no documents, or
    disagreeing run-wide fingerprints) it returns ``None`` after a WARNING, so
    the caller degrades to "no provenance" instead of aborting extraction.

Which config the fingerprint covers
-----------------------------------
``manifest._compute_config_hash`` hashes only the ``openai``, ``extraction`` and
``quality_control`` keys of the mapping it is given. The orchestrator's
``runtime_config`` is a flat merge without those keys (hashing it would yield one
constant), so :func:`identity_config` assembles them from the *resolved* configs
(env > yaml > default), keeping only what can change the extraction output:
``openai`` keeps :data:`OPENAI_EXTRACTION_AFFECTING_KEYS` of the resolved OpenAI
config; ``quality_control`` is the resolved section minus
:data:`QC_EXCLUDED_PATHS` (locations and service infrastructure); ``extraction``
holds the pipeline-level settings that live outside those sections (``ocr``,
``text_processor``). Credentials, endpoints, run-scoped paths (the evidence cache
directory embeds the timestamped run folder) and purely operational keys are
excluded, so the fingerprint -- and ``run_id`` -- is the same for every run of an
identical configuration. Every document of one run gets the same mapping, hence
the same config fingerprint (3.6).

Document scope (called per document, from ``build_qc_bundle``)
--------------------------------------------------------------
:func:`emit_document_provenance`
    Records the source node (``x-manifest-*`` extensions carry every identity key
    the adapter does not consume, including the run-relative ``output_path`` that
    is Requirement 1.3's link to the extraction output), the one ``reconcile``
    derivation step built from ``unified.content["provenance"]``, and the
    per-document ``document_extraction`` / ``quality_control`` stage marks. It
    never raises: any provenance error is logged, and turned into a failed
    stage mark for the document when its source id is known.

Two PDFs with identical bytes share one ``src:<hash>``: the source is recorded
once (the recorder keeps the first), a single WARNING names both labels, and any
conflicting later record surfaces as a ``duplicate_identity`` finding.

The recorder is lock-guarded and this module adds no other shared mutable state,
so :func:`emit_document_provenance` is safe under ``asyncio.to_thread``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from provenance.adapters.derivation_adapter import derivations_from_qc_provenance
from provenance.adapters.identity_adapter import run_identity_from_identities, source_node_from_identity
from provenance.identity import InvalidIdentifierError
from provenance.models import RunIdentity
from provenance.recorder import ProvenanceRecorder
from utils import path_utils
from utils.logging_utils import get_logger

from . import manifest as _manifest
from . import pdf_processor

logger = get_logger(__name__)

__all__ = [
    "DocumentProvenance",
    "build_run_identity",
    "emit_document_failure",
    "emit_document_provenance",
    "identity_config",
    "missing_fingerprint_reason",
    "plan_run_documents",
    "run_relative_output_path",
]

_DOCUMENT_STAGE = "document_extraction"
_QC_STAGE = "quality_control"
_SOURCE_FORMAT = "pdf"
_REASON_LIMIT = 200

# Sentinels returned by pipeline.manifest when an input cannot be read
# (``_compute_file_sha256`` / ``_compute_extraction_map_hash``).
_UNREADABLE_PDF_SENTINEL = "nohash"
_MISSING_EXTRACTION_MAP_SENTINEL = "no_extraction_map"

# ---------------------------------------------------------------------------
# What the configuration fingerprint covers
# ---------------------------------------------------------------------------
# Every key ``load_openai_config()`` returns is classified in exactly one of the
# two sets below; a test loads the real config and fails when a key is in
# neither, so a newly added key must be classified here before it ships. A key
# is EXTRACTION_AFFECTING when changing it can change the extracted output (or
# which documents fail); it is EXCLUDED when it is a credential, an endpoint, a
# run-scoped or derived path (these embed the timestamped run folder and would
# make the fingerprint -- and ``run_id`` -- differ on every run of an identical
# config), or purely operational (throughput, retry pacing, logging, cache
# warm-up) and cannot change the output.

OPENAI_EXTRACTION_AFFECTING_KEYS: frozenset[str] = frozenset(
    {
        "chunk_model",  # model identity
        "synthesis_model",  # model identity
        "temperature",  # sampling
        "num_chunks",  # how fields are partitioned across calls
        "chunk_max_tokens",  # output token ceiling per chunk (truncation)
        "domain_to_chunk",  # which domain is extracted in which chunk
        "max_repair_attempts",  # whether a malformed response is repaired or fails
        "max_evidence_items_per_chunk",  # evidence the model sees
        "max_evidence_chars_per_chunk",  # evidence the model sees
        "min_evidence_coverage_ratio",  # recorded coverage shortfall (borderline: kept per the run-scope decision)
        "grobid_failure_behavior",  # whether a GROBID failure aborts the document or falls back
        "token_budgets",  # per-stage prompt/response budgets
        # Borderline, kept: a cache namespace that does not change the model's answer, but
        # changing it deliberately starts a new prompt-cache lineage, which the run records.
        "prompt_cache_key_prefix",
    }
)
"""``load_openai_config()`` keys hashed into the configuration fingerprint."""

OPENAI_EXCLUDED_KEYS: frozenset[str] = frozenset(
    {
        "api_key",  # credential: rotating it is not a configuration change
        "base_url",  # endpoint, not behaviour
        "evidence_cache_dir",  # run-scoped absolute path (embeds the timestamped run folder)
        "pdf_concurrency",  # throughput
        "global_api_limit",  # throughput
        "max_retries",  # retry pacing for transient API errors
        "retry_base_delay",  # retry pacing
        "max_log_response_chars",  # logging
        "enable_cache_prewarm",  # cache warm-up call; CLI-overridable, output unchanged
        "cache_warmup_max_tokens",  # cache warm-up call
        "prewarm_synthesis_if_model_diff",  # cache warm-up call
        "prompt_cache_retention",  # server-side cache retention window
    }
)
"""``load_openai_config()`` keys left out of the configuration fingerprint."""

QC_EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        # output / cache locations (relative here, but locations, not behaviour)
        "artifact_generator.output_dir",
        "artifact_generator.export_to_disk",
        "grobid.tei_cache_dir",
        # GROBID service infrastructure: where and how the service runs, not what it extracts
        "grobid.url",
        "grobid.timeout",
        "grobid.max_retries",
        "grobid.docker_image",
        "grobid.java_opts",
        "grobid.concurrency",
        "grobid.cpus",
        "grobid.auto_start",
        "grobid.container_name",
        "grobid.stop_on_exit",
        "grobid.warmup",
        # add-on service endpoints and probes (``enabled`` stays: it decides what is extracted)
        *(
            f"addons.{addon}.{key}"
            for addon in ("grobid_quantities", "datastet", "entity_fishing")
            for key in ("url", "endpoint", "isalive", "timeout", "preflight_timeout")
        ),
    }
)
"""Dotted paths inside ``quality_control`` left out of the configuration fingerprint.

Everything else under ``quality_control`` is hashed. The real QC section holds no
timestamped or absolute path (a test checks), so only locations and service
infrastructure are excluded.
"""


def _prune(section: Mapping[str, Any], excluded: frozenset[str], prefix: str = "") -> dict[str, Any]:
    """Copy ``section`` without the dotted paths in ``excluded`` (a path may name a whole subtree)."""
    kept: dict[str, Any] = {}
    for key, value in section.items():
        path = f"{prefix}{key}"
        if path in excluded:
            continue
        kept[key] = _prune(value, excluded, f"{path}.") if isinstance(value, Mapping) else value
    return kept


@dataclass(frozen=True)
class DocumentProvenance:
    """What ``build_qc_bundle`` needs to emit one document's provenance.

    Built by the orchestrator only for documents in :func:`plan_run_documents`'
    result, so a resumed document (which has no identity) never carries one.
    """

    recorder: ProvenanceRecorder
    identity: _manifest.ManifestIdentity
    run_id: str


# ---------------------------------------------------------------------------
# Identity inputs
# ---------------------------------------------------------------------------


def identity_config(openai_config: Mapping[str, Any], qc_config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the mapping whose ``openai`` / ``extraction`` / ``quality_control`` sections are hashed.

    ``openai_config`` is the resolved OpenAI config (``load_openai_config()``),
    ``qc_config`` the resolved QC config (``load_qc_config()``). Only the
    extraction-affecting keys are kept (:data:`OPENAI_EXTRACTION_AFFECTING_KEYS`,
    :data:`QC_EXCLUDED_PATHS`), so the fingerprint is the same for every run of an
    identical configuration and changes when the extraction configuration does.
    ``extraction`` holds the pipeline-level settings that live outside those two
    sections (``ocr``, ``text_processor``).
    """
    return {
        "openai": {k: v for k, v in openai_config.items() if k in OPENAI_EXTRACTION_AFFECTING_KEYS},
        "extraction": {
            "ocr": qc_config.get("ocr"),
            "text_processor": qc_config.get("text_processor"),
        },
        "quality_control": _prune(qc_config.get("quality_control", {}), QC_EXCLUDED_PATHS),
    }


def run_relative_output_path(
    pdf_name: str,
    *,
    output_dir: Path | None = None,
    run_root: Path | None = None,
) -> str:
    """Return the extracted-JSON path of ``pdf_name`` relative to the run output folder (1.3).

    ``output_dir`` is where ``pdf_processor`` writes ``<pdf_name>.extracted.json``
    (default ``utils.path_utils.OUTPUT_DIR``); ``run_root`` is the run folder
    (default ``resolve_run_output_path("")``). A directory outside the run folder
    degrades to the bare file name, with a WARNING.
    """
    file_name = f"{pdf_name}.extracted.json"
    out_dir = Path(output_dir) if output_dir is not None else path_utils.OUTPUT_DIR
    root = Path(run_root) if run_root is not None else path_utils.resolve_run_output_path("")
    try:
        return (out_dir / file_name).relative_to(root).as_posix()
    except ValueError:
        logger.warning(
            "provenance: output directory %s is not under the run folder %s; "
            "recording %s as the output path",
            out_dir,
            root,
            file_name,
        )
        return file_name


def missing_fingerprint_reason(identity: _manifest.ManifestIdentity) -> str | None:
    """Return why ``identity`` has no usable fingerprint, or ``None`` when it has all of them.

    The manifest reports an unreadable PDF as ``"nohash"`` and a missing
    extraction map as ``"no_extraction_map"``; those are sentinels, never
    identities.
    """
    if identity.pdf_content_hash in ("", _UNREADABLE_PDF_SENTINEL):
        return "no usable content fingerprint (the PDF could not be read)"
    if identity.extraction_map_hash in ("", _MISSING_EXTRACTION_MAP_SENTINEL):
        return "no usable extraction map fingerprint (the extraction map could not be read)"
    if not identity.config_hash:
        return "no usable configuration fingerprint"
    return None


# ---------------------------------------------------------------------------
# Run scope
# ---------------------------------------------------------------------------


def plan_run_documents(
    pdf_paths: Sequence[Path | str],
    manifest: Mapping[str, Any],
    *,
    openai_config: Mapping[str, Any],
    qc_config: Mapping[str, Any],
    output_dir: Path | None = None,
    run_root: Path | None = None,
) -> dict[str, _manifest.ManifestIdentity]:
    """Return ``pdf_name -> ManifestIdentity`` for the documents this run actually processes.

    Excludes resumed documents and documents without a usable fingerprint (see
    the module docstring). Keys are PDF stems, the pipeline's document ids.
    ``manifest`` is the manifest dict as loaded for the run. Two PDFs with the same
    stem (in different directories) are one document to the manifest and the output
    file: the first wins and a WARNING names the repeated name.
    """
    config = identity_config(openai_config, qc_config)
    model_id = openai_config.get("chunk_model")
    identities: dict[str, _manifest.ManifestIdentity] = {}
    seen: set[str] = set()
    for pdf_path in pdf_paths:
        pdf_name = Path(pdf_path).stem
        if pdf_name in seen:
            logger.warning(
                "provenance: two PDFs share the document name %r (the manifest and output file are keyed "
                "by it); only the first is planned",
                pdf_name,
            )
            continue
        seen.add(pdf_name)
        try:
            resumed = pdf_processor.is_resumed(pdf_name, manifest)
        except Exception as exc:  # a malformed manifest entry must not abort the run
            logger.warning("provenance: cannot tell whether %s is resumed (%s); treating it as not resumed", pdf_name, exc)
            resumed = False
        if resumed:
            logger.debug("provenance: %s is already complete in the manifest; excluded from this run", pdf_name)
            continue
        identity = _manifest.compute_identity(
            pdf_path,
            config,
            model_id=model_id,
            output_path=run_relative_output_path(pdf_name, output_dir=output_dir, run_root=run_root),
        )
        reason = missing_fingerprint_reason(identity)
        if reason is not None:
            logger.warning("provenance: %s has %s; it is left out of provenance", pdf_name, reason)
            continue
        identities[pdf_name] = identity
    return identities


def build_run_identity(
    identities: Mapping[str, _manifest.ManifestIdentity],
    *,
    chunk_model: str,
    synthesis_model: str | None,
    created_at: str,
) -> RunIdentity | None:
    """Build the run identity from the processed documents' identities (3.5), or ``None``.

    The synthesis model is added to the model identifiers when it is set and
    differs from ``chunk_model``. ``created_at`` is an ISO 8601 UTC string
    supplied by the caller. A document identity carrying a sentinel is dropped.
    ``None`` (after a WARNING) means no identity could be built -- no usable
    documents, or documents that disagree on a run-wide fingerprint -- and the
    caller should run without provenance. Argument type errors still raise.
    """
    usable: dict[str, dict[str, str]] = {}
    for name, identity in identities.items():
        reason = missing_fingerprint_reason(identity)
        if reason is not None:
            logger.warning("provenance: %s has %s; it is left out of the run identity", name, reason)
            continue
        usable[name] = identity.to_dict()
    extra_models = (synthesis_model,) if synthesis_model and synthesis_model != chunk_model else ()
    try:
        return run_identity_from_identities(usable, created_at=created_at, additional_model_ids=extra_models)
    except InvalidIdentifierError as exc:
        logger.warning("provenance: no run identity could be built (%s); this run has no provenance", exc)
        return None


# ---------------------------------------------------------------------------
# Document scope
# ---------------------------------------------------------------------------


def _describe(error: object) -> str:
    text = str(error) if isinstance(error, str) else f"{type(error).__name__}: {error}"
    return text if len(text) <= _REASON_LIMIT else text[: _REASON_LIMIT - 3] + "..."


def _mark_failed(recorder: Any, stage: str, reason: str, source_id: str) -> None:
    try:
        recorder.mark_stage_failed(stage, reason, source_id=source_id)
    except Exception as exc:
        logger.warning("provenance: could not mark %s failed for %s (%s)", stage, source_id, exc)


def _mark_complete(recorder: Any, stage: str, source_id: str) -> None:
    try:
        recorder.mark_stage_complete(stage, source_id=source_id)
    except Exception as exc:
        logger.warning("provenance: could not mark %s complete for %s (%s)", stage, source_id, exc)


def _warn_if_source_shared(recorder: Any, source_id: str, pdf_name: str) -> None:
    """Warn once, naming both labels, when ``source_id`` is held by a different document.

    Checked after recording, so the label found is the one the recorder kept
    whichever document won a race.
    """
    try:
        kept = recorder.snapshot().nodes.get(source_id)
    except Exception as exc:
        logger.debug("provenance: shared-source check skipped for %s (%s)", source_id, exc)
        return
    kept_label = getattr(kept, "source_label", None)
    if kept_label is not None and kept_label != pdf_name:
        logger.warning(
            "provenance: documents %r and %r have identical content (%s); the source is "
            "recorded once, as %r, and a conflicting later record is reported as duplicate_identity",
            kept_label,
            pdf_name,
            source_id,
            kept_label,
        )


def _record_source(
    recorder: Any,
    identity: _manifest.ManifestIdentity,
    pdf_name: str,
    run_id: str,
) -> str | None:
    """Record the document's source node; return its id, or ``None`` when that is not possible.

    Never raises ``Exception``: a wrong-typed ``identity`` (anything that is not a
    ``ManifestIdentity``) is logged and yields ``None`` like any other failure.
    """
    try:
        reason = missing_fingerprint_reason(identity)
        if reason is not None:
            logger.warning("provenance: %s has %s; no source node recorded", pdf_name, reason)
            return None
        node = source_node_from_identity(
            identity.to_dict(), source_label=pdf_name, source_format=_SOURCE_FORMAT, run_id=run_id
        )
        source_id = recorder.record_source(node)
    except Exception as exc:
        logger.warning("provenance: no source node recorded for %s (%s)", pdf_name, _describe(exc))
        return None
    _warn_if_source_shared(recorder, source_id, pdf_name)
    return source_id


def _json_native(value: Any, changed: list[bool]) -> Any:
    """Copy ``value`` in the exact form ``provenance.serialization`` writes and reads back.

    The artifact writer accepts ``None``, ``str``, ``bool``, ``int``, finite
    ``float``, mappings with ``str`` keys and lists/tuples, and reads tuples back
    as lists. So here (recording every deviation in ``changed``): a non-``str``
    mapping key becomes ``str(key)``; a tuple becomes a list; a set becomes a
    list sorted by ``repr``; a non-finite float and any other object become
    ``str(value)``. Applying this to its own output changes nothing, which keeps
    ``graph_from_dict(graph_to_dict(g)) == g`` true for the stored extension.
    """
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                changed[0] = True
            out.setdefault(str(key), _json_native(item, changed))
        return out
    if isinstance(value, list):
        return [_json_native(item, changed) for item in value]
    if isinstance(value, tuple):
        changed[0] = True
        return [_json_native(item, changed) for item in value]
    if isinstance(value, (set, frozenset)):
        changed[0] = True
        return [_json_native(item, changed) for item in sorted(value, key=repr)]
    if isinstance(value, float) and not math.isfinite(value):
        changed[0] = True
        return str(value)
    if value is None or isinstance(value, (str, int, float)):
        return value
    changed[0] = True
    return str(value)


def _ensure_json_native(mapping: Mapping[str, Any], pdf_name: str) -> Mapping[str, Any]:
    """Return ``mapping`` unchanged when the artifact writer takes it as is, else a JSON-native copy.

    The derivation adapter copies values verbatim and the artifact is strict JSON,
    so one stray object in the QC mapping would otherwise cost the whole run its
    artifact. See :func:`_json_native` for the conversion rules.
    """
    changed = [False]
    native = _json_native(mapping, changed)
    if not changed[0]:
        return mapping
    logger.warning(
        "provenance: QC provenance of %s holds values that are not JSON-native; converting them", pdf_name
    )
    return native


def _qc_provenance_mapping(bundle: Any, pdf_name: str) -> Mapping[str, Any]:
    """The reconciled record's provenance mapping; absent or ``None`` is empty, a non-mapping warns."""
    content = getattr(getattr(bundle, "unified", None), "content", None)
    value = content.get("provenance") if isinstance(content, Mapping) else None
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        logger.warning(
            "provenance: QC provenance of %s is not a mapping (%s); recording the reconcile step without it",
            pdf_name,
            type(value).__name__,
        )
        return {}
    return _ensure_json_native(value, pdf_name)


def _emit_quality_control(recorder: Any, bundle: Any, source_id: str, pdf_name: str, run_id: str) -> None:
    if getattr(bundle, "unified", None) is None:
        _mark_failed(recorder, _QC_STAGE, "quality control produced no unified record", source_id)
        return
    try:
        steps = derivations_from_qc_provenance(
            _qc_provenance_mapping(bundle, pdf_name), source_id=source_id, run_id=run_id
        )
        for step in steps:
            recorder.record_derivation(step)
    except Exception as exc:
        logger.warning("provenance: no QC derivation recorded for %s (%s)", pdf_name, _describe(exc))
        _mark_failed(recorder, _QC_STAGE, _describe(exc), source_id)
        return
    _mark_complete(recorder, _QC_STAGE, source_id)


def emit_document_provenance(
    recorder: ProvenanceRecorder | None,
    bundle: Any,
    identity: _manifest.ManifestIdentity | None,
    *,
    pdf_name: str,
    run_id: str,
) -> str | None:
    """Record one processed document's source node, QC derivation step and stage marks.

    ``bundle`` is the document's ``QCBundle``; ``identity`` its planned
    :class:`~pipeline.manifest.ManifestIdentity` (``None`` for a resumed or
    unplanned document, which is a no-op); ``run_id`` the recorder's run id.
    Returns the document's ``src:<fingerprint>`` id, or ``None`` when nothing
    could be recorded for it. Never raises (``BaseException`` aside): provenance
    must not abort extraction.
    """
    if recorder is None or identity is None:
        return None
    source_id = _record_source(recorder, identity, pdf_name, run_id)
    if source_id is None:
        return None
    _mark_complete(recorder, _DOCUMENT_STAGE, source_id)
    _emit_quality_control(recorder, bundle, source_id, pdf_name, run_id)
    return source_id


def emit_document_failure(
    recorder: ProvenanceRecorder | None,
    identity: _manifest.ManifestIdentity | None,
    error: object,
    *,
    pdf_name: str,
    run_id: str,
) -> str | None:
    """Record a document whose extraction failed: its source node, and ``document_extraction`` failed.

    For the caller whose ``build_qc_bundle`` raised, so the document is still
    represented and its failure is attributed to it alone. Same no-op and
    never-raise rules as :func:`emit_document_provenance`.
    """
    if recorder is None or identity is None:
        return None
    source_id = _record_source(recorder, identity, pdf_name, run_id)
    if source_id is None:
        return None
    _mark_failed(recorder, _DOCUMENT_STAGE, _describe(error), source_id)
    return source_id
