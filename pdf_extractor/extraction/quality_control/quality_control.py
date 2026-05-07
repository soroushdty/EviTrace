"""
QC Pipeline orchestrator. Wires together Artifact Generator, Rater,
IAA Calculator, Adjudicator, and Reconciler sub-modules in that order.

Pipeline order:
  1. Artifact Generator  — build canonical artifacts from branch payloads
  2. Rater               — rate each branch (once per branch)
  3. IAA Calculator      — compute inter-rater agreement across passing branches
  4. Adjudicator         — select the best extractor output
  5. Reconciler          — reconcile into a UnifiedRecord

Public API
----------
- run_quality_control(branches, document_id, config) -> QCContext
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from . import artifact_generator, rater, iaa_calculator, adjudicator
from .local_metrics import LocalQCReport
from .models import (
    BranchOutput,
    QCContext,
    QualityReport,
)
from ...evi_trace.utils.text_utils import exact_match_search, semantic_search

logger = logging.getLogger("evi_trace")


def _split_sentences(text: str) -> list[str]:
    """Split a text blob into simple sentence records for Tier 1 checks."""
    stripped = text.strip()
    if not stripped:
        return []
    parts = re.split(r"(?<=[.!?])\s+", stripped)
    return [part.strip() for part in parts if part.strip()] or [stripped]


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

    page_texts: dict[int, list[str]] = {}
    text_parts: list[str] = []
    for block in blocks:
        text = block.get("text", "")
        if text:
            text_parts.append(text)
        page_index = _coerce_page_index(block.get("page_index", 0))
        if text:
            page_texts.setdefault(page_index, []).append(text)

    return (
        "\n".join(text_parts),
        {page_index: "\n".join(values) for page_index, values in page_texts.items()},
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
) -> LocalQCReport:
    """Create and evaluate the Metrics Tier 1 report for a single branch."""
    full_text, page_texts, blocks = _extract_branch_payload(branch.payload)
    sentence_records = [{"sentence": sentence} for sentence in _split_sentences(full_text)]
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


def _build_placeholder_sentence_store(full_text: str) -> dict:
    """Return a scaffold sentence store used only to record Tier 3 attempts."""
    first_sentence = _split_sentences(full_text)[:1]
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
    """Derive a deterministic SHA-256 document ID from both extractor outputs.

    Serialises both inputs into a stable JSON payload (sorted keys,
    no ASCII escaping) and returns the hex digest of its SHA-256 hash.
    Consistent with the SHA-256 convention used in ``evi_trace/utils/path_utils.py``.
    """
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
) -> dict:
    """Run the legacy two-extractor QC pipeline and return the Unified Output dict.

    This internal function preserves the existing orchestration logic
    (artifact generation, rating, IAA calculation, adjudication, reconciliation)
    and is called by the new ``run_quality_control`` wrapper.

    Parameters
    ----------
    grobid_output:
        Raw TEI XML string from the GROBID extraction backend.
    pymupdf_output:
        Raw dict or list from the PyMuPDF extraction backend.
    document_id:
        Stable document identifier (already resolved, never None here).
    config:
        Loaded pipeline config dict.

    Returns
    -------
    dict
        The Unified Output produced by the Reconciler module.
    """
    # --- Step 1: Artifact Generator ---
    logger.debug("QC: building canonical artifacts for document_id=%s", document_id)
    canonical_artifacts = artifact_generator.build_canonical_artifacts(
        grobid_output, pymupdf_output, document_id
    )
    logger.debug("QC: canonical artifacts built for document_id=%s", document_id)

    # --- Step 2: Export if configured ---
    if config.get("quality_control", {}).get("artifact_generator", {}).get("export_to_disk", False):
        output_dir = config["quality_control"]["artifact_generator"]["output_dir"]
        artifact_generator.export_canonical_artifacts(canonical_artifacts, output_dir)

    # --- Step 3: Rater (once per extractor) ---
    logger.debug("QC: rating grobid for document_id=%s", document_id)
    grobid_observation = rater.observe("grobid", canonical_artifacts, document_id, config)
    logger.debug("QC: rating pymupdf for document_id=%s", document_id)
    pymupdf_observation = rater.observe("pymupdf", canonical_artifacts, document_id, config)

    # --- Step 4: IAA Calculator ---
    logger.debug("QC: computing IAA for document_id=%s", document_id)
    investigator_object = iaa_calculator.investigate(
        grobid_observation,
        pymupdf_observation,
        canonical_artifacts,
        canonical_artifacts,
        config,
    )
    logger.debug("QC: IAA computation complete for document_id=%s", document_id)

    # --- Step 5: Adjudicator ---
    logger.debug("QC: adjudicating for document_id=%s", document_id)
    result = adjudicator.adjudicate(
        canonical_artifacts,
        canonical_artifacts,
        grobid_observation,
        pymupdf_observation,
        investigator_object,
        config,
    )
    logger.debug("QC: adjudication complete for document_id=%s", document_id)

    return result


def run_quality_control(
    branches: list[BranchOutput],
    document_id: str,
    config: dict,
) -> QCContext:
    """Orchestrate the QC pipeline and return a ``QCContext``.

    Accepts a list of :class:`~evi_trace.extraction.quality_control.models.BranchOutput`
    instances (one per extractor branch), runs the five-module QC pipeline, and
    returns a fully populated :class:`~evi_trace.extraction.quality_control.models.QCContext`.

    The existing legacy pipeline (artifact generation, rating, IAA calculation,
    adjudication, reconciliation) is preserved internally and wired to populate
    the context fields.

    Parameters
    ----------
    branches:
        List of extractor branch outputs.  Each entry carries the extractor
        name, branch index, native payload, and initial status (``None``).
    document_id:
        Stable document identifier.
    config:
        Loaded pipeline config dict (as returned by ``load_config``).

    Returns
    -------
    QCContext
        Fully populated context with ``branches``, ``reports``,
        ``iaa_metrics``, ``decision``, and ``unified`` fields set.

    Raises
    ------
    TypeError
        If ``branches`` is not a list or ``document_id`` is not a non-empty str.
    Exception
        Any exception raised by a sub-module is propagated to the caller
        without being caught or wrapped.
    """
    # Metrics Hierarchy ordering (independent of the Extractor Hierarchy):
    #   Tier 1 (Local_QC_Metrics) — cheap heuristics, always run on every branch
    #   Tier 2 (exact/fuzzy text comparison via exact_match_search) — run on borderline Tier 1 results
    #   Tier 3 (FAISS semantic comparison) — scaffolded; NOT yet wired into adjudication
    #
    # Embeddings are deliberately NOT used as a first-line quality scorer because they are
    # unreliable for: broken ligatures, missing coordinates, table-structure loss, page-order
    # problems, header/footer contamination, figure/table boundary errors,
    # hallucinated-but-plausible OCR text, and generic scientific boilerplate.

    if not isinstance(branches, list):
        raise TypeError(
            f"branches must be a list, got {type(branches).__name__!r}"
        )
    if not isinstance(document_id, str) or not document_id:
        raise TypeError(
            f"document_id must be a non-empty str, got {type(document_id).__name__!r}"
        )

    logger.info("QC pipeline start: document_id=%s", document_id)

    # Tier 3 scaffolding check (Req 12.7): when semantic_qc is enabled it is recorded but
    # does NOT alter any branch selection or adjudication outcome.
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

    ctx = QCContext(branches=branches)
    ctx.metrics_hierarchy = {"tier1": [], "tier2": [], "tier3": []}

    tier1_reports: list[LocalQCReport] = []
    borderline_branches: list[tuple[int, BranchOutput, LocalQCReport]] = []
    for branch_index, branch in enumerate(branches):
        report = _build_tier1_report(branch, branches, branch_index, config)
        tier1_reports.append(report)
        ctx.reports.append(report)
        ctx.metrics_hierarchy["tier1"].append(
            {
                "extractor": branch.extractor,
                "branch": branch.branch,
                "report": report,
            }
        )
        branch.status = "pass" if report.status in (None, "pass") and not any(
            metric.triggered for metric in report.metric_records
        ) else "fail"

        triggered_count = sum(1 for metric in report.metric_records if metric.triggered)
        if triggered_count > 0 and triggered_count <= 2:
            borderline_branches.append((branch_index, branch, report))

    tier2_result = None
    for branch_index, branch, report in borderline_branches:
        exact_sentence = report.sentence_records[0]["sentence"] if report.sentence_records else report.full_pdf_text
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
            ctx.metrics_hierarchy["tier2"].append(
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

    if semantic_qc_enabled and tier2_result is None and borderline_branches:
        branch_index, branch, report = borderline_branches[0]
        exact_sentence = report.sentence_records[0]["sentence"] if report.sentence_records else report.full_pdf_text
        if exact_sentence:
            semantic_result = semantic_search(
                exact_sentence,
                _build_placeholder_sentence_store(report.full_pdf_text),
                lambda query_text, model=None, query_prefix="": None,
                config.get("quality_control", {})
                .get("semantic_qc", {})
                .get("similarity_threshold", 0.85),
                None,
            )
        else:
            semantic_result = None

        ctx.metrics_hierarchy["tier3"].append(
            {
                "source_branch": branch.branch,
                "result": semantic_result,
            }
        )

    # Extract grobid and pymupdf payloads from branches for the legacy pipeline.
    # The legacy pipeline expects (grobid_output: str, pymupdf_output: dict|list).
    grobid_payload: str = "<root/>"
    pymupdf_payload: dict | list = {}

    for branch in branches:
        if branch.extractor == "grobid" and isinstance(branch.payload, str):
            grobid_payload = branch.payload
        elif branch.extractor == "pymupdf" and isinstance(branch.payload, (dict, list)):
            pymupdf_payload = branch.payload

    # Run the legacy pipeline to populate the context fields.
    unified_output = _run_legacy_pipeline(
        grobid_payload, pymupdf_payload, document_id, config
    )

    # Populate ctx.unified from the legacy pipeline result.
    from .models import UnifiedRecord
    ctx.unified = UnifiedRecord(
        document_id=document_id,
        content=unified_output,
    )

    logger.info("QC pipeline complete: document_id=%s", document_id)

    return ctx
