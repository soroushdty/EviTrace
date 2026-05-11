"""
pipeline/extraction_pipeline.py
--------------------------------------
Shared extraction logic: per-page scan detection, backend routing, and QC
pipeline for a single PDF.

This module is the single source of truth for the full multi-backend
extraction flow.  Both the standalone ``pdf_extractor.py`` CLI and the async
``pipeline/orchestrator.py`` delegate to :func:`build_qc_bundle` rather than
duplicating the routing logic.

Public API
----------
build_qc_bundle(pdf_path, pdf_name, qc_config) -> QCBundle
    Run scan detection → backend routing → QC pipeline for one PDF and
    return a fully populated :class:`~quality_control.models.QCBundle`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pdf_extractor.extraction.GROBID import extract_with_grobid
from pdf_extractor.extraction.PyMuPDF import extract_with_pymupdf
from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber
from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr
from pdf_extractor.extraction import scan_detector
from pdf_extractor.annotation import w3c_annotation
from pdf_extractor.annotation import artifact_generator as annotation_artifact_generator
from quality_control import QCBundle, run_quality_control
from quality_control.models import Candidate
from utils.text_processor import TextProcessor

logger = logging.getLogger("pdf_extractor")


def build_qc_bundle(
    pdf_path: Path | str,
    pdf_name: str,
    qc_config: dict,
) -> QCBundle:
    """Run per-page scan detection, route to correct extractors, and run the
    full QC pipeline for one PDF.

    Per-page routing
    ----------------
    - All pages native → GROBID (semantic authority) + pdfplumber (structural
      authority); PyMuPDF font metadata stored in ``ctx.unified.content``.
    - Any page scanned + ``ocr=true`` → PaddleOCR (primary) + PyMuPDF OCR
      (secondary cross-validation).
    - Any page scanned + ``ocr=false`` → skip extraction, log WARNING, no branch.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.
    pdf_name:
        Human-readable name used in log messages and as the document ID.
    qc_config:
        Loaded QC config dict (from ``load_qc_config()``).

    Returns
    -------
    QCBundle
        Fully populated bundle with ``branches``, ``reports``, ``iaa_metrics``,
        ``decision``, and ``unified`` set.
    """
    pdf_path = Path(pdf_path)

    grobid_failure_behavior = (
        qc_config.get("quality_control", {})
        .get("grobid_integration", {})
        .get("failure_behavior", "fallback")
    )
    ocr_enabled: bool = bool(qc_config.get("ocr", True))

    # ------------------------------------------------------------------
    # Step 1 — Per-page scan detection
    # ------------------------------------------------------------------
    tp_cfg = qc_config.get(
        "text_processor",
        {"sentence_tokenizer": {"backend": "nltk_punkt"}},
    )
    tp = TextProcessor(config=tp_cfg)

    import fitz as _fitz  # noqa: PLC0415 — lazy; not installed in all envs
    doc = _fitz.open(str(pdf_path))
    try:
        pages = list(doc)
        scan_cfg = qc_config.get("quality_control", {})
        page_classifications = [
            scan_detector.classify_page(page, tp, scan_cfg, page_index=i)
            for i, page in enumerate(pages)
        ]
    finally:
        doc.close()

    all_native = all(c.is_native for c in page_classifications)
    has_scanned = not all_native

    # ------------------------------------------------------------------
    # Step 2 — Route to correct extractors based on page classifications
    # ------------------------------------------------------------------
    tei_xml = ""
    branches: list[Candidate] = []

    if all_native:
        # Native path: GROBID (semantic) + pdfplumber (structural)
        try:
            tei_xml, _ = extract_with_grobid(str(pdf_path))
        except Exception:
            if grobid_failure_behavior == "manifest_fail":
                raise
            logger.warning(
                "GROBID failed for %s; continuing with fallback mode", pdf_name
            )
            tei_xml = ""

        plumber_blocks = extract_with_pdfplumber(str(pdf_path))
        branches = [
            Candidate(source="grobid",     index=0, payload=tei_xml,        status=None),
            Candidate(source="pdfplumber",  index=1, payload=plumber_blocks, status=None),
        ]

    elif has_scanned and not ocr_enabled:
        # Scanned path with ocr=false: skip extraction, log WARNING
        for cls in page_classifications:
            if not cls.is_native:
                logger.warning(
                    "Skipping scanned page %d in '%s' — OCR is disabled (ocr=false)",
                    cls.page_index,
                    pdf_name,
                )

    else:
        # Scanned path with ocr=true: PaddleOCR (primary) + PyMuPDF OCR (secondary)
        paddle_blocks = extract_with_paddleocr(str(pdf_path))
        pymupdf_blocks, _ = extract_with_pymupdf(str(pdf_path))
        branches = [
            Candidate(source="paddleocr", index=0, payload=paddle_blocks,  status=None),
            Candidate(source="pymupdf",   index=1, payload=pymupdf_blocks, status=None),
        ]

    # ------------------------------------------------------------------
    # Step 3 — QC pipeline
    # ------------------------------------------------------------------
    from pdf_extractor.utils.text_utils import exact_match_search, semantic_search  # noqa: PLC0415
    ctx = run_quality_control(
        branches,
        pdf_name,
        qc_config,
        exact_match_fn=exact_match_search,
        semantic_search_fn=semantic_search,
    )
    if ctx.unified is not None and isinstance(ctx.unified.content, dict):
        ctx.unified.content["source_pdf_path"] = str(pdf_path)
        ctx.unified.content["grobid_tei_xml"] = tei_xml

    # ------------------------------------------------------------------
    # Step 4 — Annotation chain (project W3C JSON-LD from unified record)
    # ------------------------------------------------------------------
    if ctx.unified is not None:
        annotation_records = w3c_annotation.project(ctx.unified)
        jsonld = annotation_artifact_generator.generate_w3c_jsonld(annotation_records)
        if not isinstance(ctx.unified.content, dict):
            ctx.unified.content = {}
        ctx.unified.content["annotations"] = jsonld

    return ctx
