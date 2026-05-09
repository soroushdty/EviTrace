"""
QC pipeline orchestrator. Provides a generic ``run_pipeline`` entry point
that accepts injectable stage callables, and a PDF-specific
``run_quality_control`` wrapper that wires the existing five-module pipeline
(Artifact Generator, Rater, IAA Calculator, Adjudicator, Reconciler) into it.

Generic pipeline
----------------
``run_pipeline`` is domain-agnostic.  Pass any callables that satisfy the
four stage signatures to adjudicate between agents, LLM outputs, or any set
of branch outputs — not just PDF extraction results.

PDF pipeline
------------
``run_quality_control`` builds PDF-specific stage closures and delegates to
``run_pipeline``.  Tier 1 / 2 / 3 metrics tracking is layered on top via
``ctx.metrics_hierarchy``.

Public API
----------
- run_pipeline(branches, *, rater_fn, iaa_fn, adjudicator_fn, reconciler_fn, config) -> QCContext
- run_quality_control(branches, document_id, config) -> QCContext
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
from typing import Callable

from . import rater, iaa_calculator, adjudicator, reconciler
from .local_metrics import LocalQCReport
from .models import (
    AdjudicationDecision,
    AdjudicationRules,
    AlignmentMap,
    BranchOutput,
    InterRaterMetrics,
    InterRaterReport,
    QCContext,
    QualityMetrics,
    QualityReport,
    SemanticLayer,
    StructuralLayer,
    UnifiedRecord,
)
from pdf_extractor.utils.text_utils import exact_match_search, semantic_search

logger = logging.getLogger("pdf_extractor")


# ---------------------------------------------------------------------------
# Generic pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    branches: list[BranchOutput],
    *,
    rater_fn: Callable[[BranchOutput, list[BranchOutput], int, dict], QualityMetrics],
    iaa_fn: Callable[[list[QualityMetrics], dict], InterRaterMetrics],
    adjudicator_fn: Callable[[list[QualityMetrics], InterRaterMetrics, dict], AdjudicationRules],
    reconciler_fn: Callable[[AdjudicationRules, list[BranchOutput], dict], UnifiedRecord],
    config: dict | None = None,
) -> QCContext:
    """Generic four-stage QC pipeline with injectable stage callables.

    Each stage callable receives the outputs of the previous stages plus the
    shared config dict, making it straightforward to swap in custom
    implementations for any domain — LLM attribute extraction, multi-agent
    adjudication, or any other use case beyond PDF text extraction.

    Stage signatures
    ----------------
    rater_fn(branch, branches, index, config) -> QualityMetrics
        Called once per branch.  Must call ``passes_check`` internally and set
        ``report.status`` to ``"pass"`` or ``"fail"`` before returning.

    iaa_fn(reports, config) -> InterRaterMetrics
        Receives all reports after every branch has been rated.

    adjudicator_fn(reports, iaa_metrics, config) -> AdjudicationRules
        Receives all reports and the IAA metrics; returns a decision that
        carries ``primary_extractor`` (or ``primary_agent``), ``confidence``,
        and ``rationale``.

    reconciler_fn(decision, branches, config) -> UnifiedRecord
        Produces the final reconciled output from the adjudication decision
        and the original branch payloads.

    Parameters
    ----------
    branches:
        List of branch outputs (one per agent / extractor).
    rater_fn:
        Per-branch rating callable.
    iaa_fn:
        Inter-rater agreement callable.
    adjudicator_fn:
        Adjudication callable.
    reconciler_fn:
        Reconciliation callable.
    config:
        Shared config dict passed verbatim to every stage.  Defaults to ``{}``.

    Returns
    -------
    QCContext
        Fully populated context with ``branches``, ``reports``,
        ``iaa_metrics``, ``decision``, and ``unified`` set.

    Raises
    ------
    TypeError
        If ``branches`` is not a list.
    """
    if not isinstance(branches, list):
        raise TypeError(f"branches must be a list, got {type(branches).__name__!r}")

    config = config or {}
    ctx = QCContext(branches=branches)

    for i, branch in enumerate(branches):
        report = rater_fn(branch, branches, i, config)
        ctx.reports.append(report)
        branch.status = "pass" if report.status == "pass" else "fail"

    ctx.iaa_metrics = iaa_fn(ctx.reports, config)
    ctx.decision = adjudicator_fn(ctx.reports, ctx.iaa_metrics, config)
    ctx.unified = reconciler_fn(ctx.decision, branches, config)

    return ctx


# ---------------------------------------------------------------------------
# Internal helpers (shared by PDF-specific stage closures)
# ---------------------------------------------------------------------------

def _load_text_processor(config: dict) -> object:
    """Resolve and instantiate the configured TextProcessor class.

    Expects config["quality_control"]["text_processor"]["class"] to be a
    fully-qualified import path (eg. "utils.text_processor.TextProcessor").
    If absent, defaults to utils.text_processor.TextProcessor.
    """
    qc_cfg = (config or {}).get("quality_control", {})
    tp_cfg = qc_cfg.get("text_processor", {}) if isinstance(qc_cfg, dict) else {}
    class_path = tp_cfg.get("class", "utils.text_processor.TextProcessor")
    try:
        module_name, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls(config=tp_cfg)
    except Exception as exc:  # pragma: no cover - defensive path
        raise ImportError(f"Could not load TextProcessor class {class_path}: {exc}")


def _coerce_page_index(page_index: object) -> int:
    try:
        return int(page_index)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _extract_branch_payload(payload: object) -> tuple[str, dict[int, str], list[dict]]:
    """Extract full text, per-page texts, and block-like dicts from a branch payload."""
    blocks: list[dict] = []

    if isinstance(payload, str):
        full_text = payload
        page_texts = {0: payload} if payload.strip() else {}
        if payload.strip():
            blocks = [{"text": payload, "page_index": 0, "block_bbox": None, "span_bboxes": []}]
        return full_text, page_texts, blocks

    if isinstance(payload, list):
        blocks = [block for block in payload if isinstance(block, dict)]
    elif isinstance(payload, dict):
        candidate_blocks = payload.get("blocks")
        if isinstance(candidate_blocks, list):
            blocks = [block for block in candidate_blocks if isinstance(block, dict)]
        elif isinstance(payload.get("text"), str):
            full_text = payload.get("text", "")
            page_texts = {0: full_text} if full_text.strip() else {}
            if full_text.strip():
                blocks = [{"text": full_text, "page_index": 0, "block_bbox": None, "span_bboxes": []}]
            return full_text, page_texts, blocks

    page_texts_acc: dict[int, list[str]] = {}
    text_parts: list[str] = []
    for block in blocks:
        text = block.get("text", "")
        if text:
            text_parts.append(text)
        page_index = _coerce_page_index(block.get("page_index", 0))
        if text:
            page_texts_acc.setdefault(page_index, []).append(text)

    return (
        "\n".join(text_parts),
        {pi: "\n".join(vals) for pi, vals in page_texts_acc.items()},
        blocks,
    )


def _build_native_page_texts(branches: list[BranchOutput], current_index: int) -> dict[int, str]:
    """Build a best-effort native backend page-text map from the other branches."""
    native_page_texts: dict[int, list[str]] = {}
    for index, branch in enumerate(branches):
        if index == current_index:
            continue
        _, page_texts, _ = _extract_branch_payload(branch.payload)
        for page_index, text in page_texts.items():
            if text:
                native_page_texts.setdefault(page_index, []).append(text)

    return {
        page_index: "\n".join(values)
        for page_index, values in native_page_texts.items()
    }


def _build_tier1_report(
    branch: BranchOutput,
    branches: list[BranchOutput],
    branch_index: int,
    config: dict,
    text_processor,
) -> LocalQCReport:
    """Create and evaluate the Metrics Tier 1 report for a single branch."""
    full_text, page_texts, blocks = _extract_branch_payload(branch.payload)
    sentence_records = [
        {"sentence": sentence}
        for sentence in (text_processor.tokenize_sentences(full_text) if text_processor else [])
    ]
    native_page_texts = _build_native_page_texts(branches, branch_index)

    report = LocalQCReport(
        config=config,
        blocks=blocks,
        sentence_records=sentence_records,
        full_pdf_text=full_text,
        page_texts=page_texts,
        native_page_texts=native_page_texts,
    )
    report.passes_check()
    return report


def _build_placeholder_sentence_store(full_text: str, text_processor) -> dict:
    """Return a scaffold sentence store used only to record Tier 3 attempts."""
    first_sentence = (
        (text_processor.tokenize_sentences(full_text)[:1] if text_processor else [])
    )
    return {
        "pdf_path": "",
        "sentences": first_sentence,
        "pages": [0] if first_sentence else [],
        "block_bboxes": [None] if first_sentence else [],
        "span_bboxes": [[]] if first_sentence else [],
        "embeddings": [],
        "faiss_index": None,
    }


def _derive_document_id(grobid_output: str, pymupdf_output: dict | list) -> str:
    """Derive a deterministic SHA-256 document ID from both extractor outputs."""
    payload = json.dumps(
        {"grobid": grobid_output, "pymupdf": pymupdf_output},
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _run_legacy_pipeline(
    grobid_output: str,
    pymupdf_output: dict | list,
    document_id: str,
    config: dict,
) -> UnifiedRecord:
    """Run the legacy two-extractor QC pipeline and return a UnifiedRecord."""
    from pdf_extractor import artifact_generator

    logger.debug("QC: building canonical artifacts for document_id=%s", document_id)
    canonical_artifacts = artifact_generator.build_canonical_artifacts(
        grobid_output, pymupdf_output, document_id
    )
    logger.debug("QC: canonical artifacts built for document_id=%s", document_id)

    if config.get("quality_control", {}).get("artifact_generator", {}).get("export_to_disk", False):
        output_dir = config["quality_control"]["artifact_generator"]["output_dir"]
        artifact_generator.export_canonical_artifacts(canonical_artifacts, output_dir)

    logger.debug("QC: rating grobid for document_id=%s", document_id)
    grobid_observation = rater.observe("grobid", canonical_artifacts, document_id, config)
    logger.debug("QC: rating pymupdf for document_id=%s", document_id)
    pymupdf_observation = rater.observe("pymupdf", canonical_artifacts, document_id, config)

    logger.debug("QC: computing IAA for document_id=%s", document_id)
    investigator_object = iaa_calculator.investigate(
        grobid_observation,
        pymupdf_observation,
        canonical_artifacts,
        canonical_artifacts,
        config,
    )
    logger.debug("QC: IAA computation complete for document_id=%s", document_id)

    logger.debug("QC: adjudicating for document_id=%s", document_id)
    # Build a minimal AlignmentMap for the legacy pipeline path.
    # The legacy path has no pre-computed alignment entries, so all lists are
    # empty and the adjudicator will return an empty decisions dict.
    alignment_map = AlignmentMap()
    decisions = adjudicator.adjudicate(alignment_map, config)
    logger.debug("QC: adjudication complete for document_id=%s", document_id)

    # Delegate to the reconciler with adjudication decisions (may be empty).
    # reconciler.reconcile() returns a UnifiedRecord; return it directly.
    return reconciler.reconcile(
        canonical_artifacts,
        canonical_artifacts,
        grobid_observation,
        pymupdf_observation,
        investigator_object,
        decisions if decisions else None,
        config,
    )


# ---------------------------------------------------------------------------
# PDF-specific pipeline (wraps run_pipeline with concrete stage closures)
# ---------------------------------------------------------------------------

def run_quality_control(
    branches: list[BranchOutput],
    document_id: str,
    config: dict,
) -> QCContext:
    """PDF-specific QC pipeline built on top of ``run_pipeline``.

    Accepts a list of :class:`~quality_control.models.BranchOutput` instances
    (one per extractor branch), wires the five-module PDF pipeline into the
    generic ``run_pipeline`` orchestrator, and returns a fully populated
    :class:`~quality_control.models.QCContext`.

    Metrics hierarchy
    -----------------
    The pipeline runs three tiers of quality checks, independent of the
    extractor hierarchy:

    - **Tier 1** (Local_QC_Metrics) — cheap heuristics, always run.
    - **Tier 2** (exact/fuzzy text comparison) — run on borderline Tier 1
      results (1–2 triggered metrics).
    - **Tier 3** (FAISS semantic comparison) — scaffolded; not yet wired into
      adjudication.

    Results from all tiers are stored in ``ctx.metrics_hierarchy``.

    Parameters
    ----------
    branches:
        List of extractor branch outputs.
    document_id:
        Stable document identifier (non-empty string).
    config:
        Loaded pipeline config dict.

    Returns
    -------
    QCContext
        Fully populated context with ``branches``, ``reports``,
        ``iaa_metrics``, ``decision``, and ``unified`` set.

    Raises
    ------
    TypeError
        If ``branches`` is not a list or ``document_id`` is not a non-empty str.
    """
    if not isinstance(branches, list):
        raise TypeError(f"branches must be a list, got {type(branches).__name__!r}")
    if not isinstance(document_id, str) or not document_id:
        raise TypeError(
            f"document_id must be a non-empty str, got {type(document_id).__name__!r}"
        )

    logger.info("QC pipeline start: document_id=%s", document_id)

    # Tier 3 scaffolding check: enabled flag is recorded but does NOT alter
    # any branch selection or adjudication outcome.
    semantic_qc_enabled = (
        config.get("quality_control", {})
        .get("semantic_qc", {})
        .get("enabled", False)
    )
    if semantic_qc_enabled:
        logger.debug(
            "Metrics Tier 3 (semantic QC) is enabled but not yet wired into adjudication"
            " — scaffolded only"
        )

    # --- PDF-specific state captured by stage closures ---
    borderline_branches: list[tuple[int, BranchOutput, LocalQCReport]] = []
    metrics_hierarchy: dict = {"tier1": [], "tier2": [], "tier3": []}

    # Instantiate a single TextProcessor for this run (Task 12.1)
    try:
        text_processor = _load_text_processor(config)
    except Exception:
        # Fall back to None to preserve behavior in test environments
        text_processor = None

    # --- Stage 1: PDF rater (Tier 1 LocalQCReport) ---
    def _pdf_rater_fn(
        branch: BranchOutput,
        all_branches: list[BranchOutput],
        index: int,
        cfg: dict,
    ) -> QualityMetrics:
        report = _build_tier1_report(branch, all_branches, index, cfg, text_processor)
        passed = not any(m.triggered for m in report.metric_records)
        report.status = "pass" if passed else "fail"
        metrics_hierarchy["tier1"].append(
            {"extractor": branch.extractor, "branch": branch.branch, "report": report}
        )
        triggered_count = sum(1 for m in report.metric_records if m.triggered)
        if 0 < triggered_count <= 2:
            borderline_branches.append((index, branch, report))
        return report

    # --- Stage 2: IAA ---
    def _pdf_iaa_fn(reports: list[QualityMetrics], cfg: dict) -> InterRaterMetrics:  # noqa: ARG001
        iaa = InterRaterReport()
        iaa.compute(reports)
        return iaa

    # --- Stage 3: Adjudication ---
    def _pdf_adjudicator_fn(
        reports: list[QualityMetrics],
        iaa_metrics: InterRaterMetrics,
        cfg: dict,  # noqa: ARG001
    ) -> AdjudicationRules:
        # Maintain legacy AdjudicationDecision behavior for now
        decision = AdjudicationDecision()
        decision.adjudicate(reports, iaa_metrics)
        return decision

    # --- Stage 4: Reconciliation (strategy-driven, extractor-agnostic call site) ---
    def _build_reconciler_artifact(branch: BranchOutput | None) -> dict:
        if branch is None:
            return {"document_id": document_id, "blocks": []}
        full_text, _page_texts, blocks = _extract_branch_payload(branch.payload)
        return {
            "document_id": document_id,
            "extractor": branch.extractor,
            "branch": branch.branch,
            "text": full_text,
            "blocks": blocks,
        }

    def _as_adjudication_decisions(decision_obj: AdjudicationRules | dict | None) -> dict:
        if isinstance(decision_obj, dict):
            confidence = float(decision_obj.get("confidence", 1.0))
            primary = str(decision_obj.get("primary_extractor", "primary"))
            rationale = str(decision_obj.get("rationale", "strategy-driven reconciliation"))
            return {
                "primary_extractor": primary,
                "confidence": confidence,
                "rationale": rationale,
            }
        confidence = float(getattr(decision_obj, "confidence", 1.0))
        primary = str(getattr(decision_obj, "primary_extractor", "primary"))
        rationale = str(getattr(decision_obj, "rationale", "strategy-driven reconciliation"))
        return {
            "primary_extractor": primary,
            "confidence": confidence,
            "rationale": rationale,
        }

    def _pdf_reconciler_fn(
        decision: AdjudicationRules,
        all_branches: list[BranchOutput],
        cfg: dict,
    ) -> UnifiedRecord:
        from quality_control.concerns import (
            DEFAULT_SECTION_VERIFICATION,
            DEFAULT_TABLE_FIGURE_MERGE,
            DEFAULT_TEXT_FIDELITY,
        )
        from pdf_extractor.annotation import w3c_annotation
        from pdf_extractor.annotation import artifact_generator as annotation_artifact_generator

        grobid_branch = next(
            (b for b in all_branches if b.extractor == "grobid"),
            None,
        )
        secondary_branch = next(
            (
                b
                for b in all_branches
                if str(b.branch).lower() in {"pdfplumber", "pymupdf"}
            ),
            None,
        )
        if secondary_branch is None:
            secondary_branch = next(
                (b for b in all_branches if b.extractor in {"pdfplumber", "pymupdf"}),
                None,
            )

        primary_artifact = _build_reconciler_artifact(grobid_branch)
        secondary_artifact = _build_reconciler_artifact(secondary_branch)

        updated_unified = reconciler.reconcile(
            primary_artifact=primary_artifact,
            secondary_artifact=secondary_artifact,
            adjudication_decisions=_as_adjudication_decisions(decision),
            config=cfg,
            text_fidelity_strategy=DEFAULT_TEXT_FIDELITY,
            section_strategy=DEFAULT_SECTION_VERIFICATION,
            table_figure_strategy=DEFAULT_TABLE_FIGURE_MERGE,
            text_processor=text_processor,
        )

        if (
            text_processor is not None
            and updated_unified.semantic is not None
            and not updated_unified.semantic.sentences
        ):
            for para in updated_unified.semantic.paragraphs:
                para_text = para.get("text", "")
                page_index = para.get("page_index", 0)
                for sentence in text_processor.tokenize_sentences(para_text):
                    updated_unified.semantic.sentences.append(
                        {
                            "text": sentence,
                            "page_index": page_index,
                            "ocr_derived": False,
                        }
                    )

        # Wire annotation chain: project and generate JSON-LD, store on record.
        annotation_records = w3c_annotation.project(updated_unified)
        jsonld = annotation_artifact_generator.generate_w3c_jsonld(annotation_records)
        if not isinstance(updated_unified.content, dict):
            updated_unified.content = {}
        updated_unified.content["annotations"] = jsonld

        # If any branch exists, enforce non-None typed layers.
        if all_branches:
            if updated_unified.semantic is None:
                updated_unified.semantic = SemanticLayer()
            if updated_unified.structural is None:
                updated_unified.structural = StructuralLayer()
            if updated_unified.alignment is None:
                updated_unified.alignment = AlignmentMap()

        return updated_unified

    # Retained for compatibility; no longer used by the active reconciler closure.
    def _run_legacy_annotation_path(unified: UnifiedRecord) -> UnifiedRecord:  # pragma: no cover
        try:
            from pdf_extractor.annotation import w3c_annotation as _w3c_annotation
            from pdf_extractor.annotation import artifact_generator as _annotation_artifact_generator
            annotation_records = _w3c_annotation.project(unified)
            jsonld = _annotation_artifact_generator.generate_w3c_jsonld(annotation_records)
            if isinstance(unified.content, dict):
                unified.content["annotations"] = jsonld
        except ImportError as exc:
            logger.warning("Annotation projection unavailable: %s", exc)
        return unified

    # --- Run the generic pipeline ---
    ctx = run_pipeline(
        branches,
        rater_fn=_pdf_rater_fn,
        iaa_fn=_pdf_iaa_fn,
        adjudicator_fn=_pdf_adjudicator_fn,
        reconciler_fn=_pdf_reconciler_fn,
        config=config,
    )
    ctx.metrics_hierarchy = metrics_hierarchy

    # --- Tier 2: exact-match search on borderline branches ---
    tier2_result = None
    for branch_index, branch, report in borderline_branches:
        exact_sentence = (
            report.sentence_records[0]["sentence"]
            if report.sentence_records
            else report.full_pdf_text
        )
        if not exact_sentence:
            continue

        for candidate_index, candidate_branch in enumerate(branches):
            if candidate_index == branch_index:
                continue

            candidate_text, candidate_page_texts, candidate_blocks = _extract_branch_payload(
                candidate_branch.payload
            )
            candidate_result = exact_match_search(
                exact_sentence,
                candidate_text,
                candidate_page_texts,
                candidate_blocks,
            )
            metrics_hierarchy["tier2"].append(
                {
                    "source_branch": branch.branch,
                    "target_branch": candidate_branch.branch,
                    "result": candidate_result,
                }
            )
            if candidate_result is not None:
                tier2_result = candidate_result
                break

        if tier2_result is not None:
            break

    # --- Tier 3: semantic search (scaffolded only) ---
    if semantic_qc_enabled and tier2_result is None and borderline_branches:
        branch_index, branch, report = borderline_branches[0]
        exact_sentence = (
            report.sentence_records[0]["sentence"]
            if report.sentence_records
            else report.full_pdf_text
        )
        if exact_sentence:
            semantic_result = semantic_search(
                exact_sentence,
                _build_placeholder_sentence_store(report.full_pdf_text, text_processor),
                lambda query_text, model=None, query_prefix="": None,
                config.get("quality_control", {})
                .get("semantic_qc", {})
                .get("similarity_threshold", 0.85),
                None,
            )
        else:
            semantic_result = None

        metrics_hierarchy["tier3"].append(
            {"source_branch": branch.branch, "result": semantic_result}
        )

    logger.info("QC pipeline complete: document_id=%s", document_id)
    return ctx
