"""
pdf_extractor/extraction/__init__.py
-------------------------------------
Extraction orchestrator. Runs a three-tier cascade:
  1. PyMuPDF  (extract_with_pymupdf)
  2. pdfplumber (extract_with_pdfplumber)
  3. PaddleOCR (extract_with_paddleocr)

Each tier is scored with ``_compute_quality_score``. The first tier that
meets or exceeds ``ocr_text_quality_threshold`` wins. If none do, the
PaddleOCR result is returned.

Public API
----------
- extract_pdf(pdf_path, ocr, ocr_text_quality_threshold, embed_model=None)
  -> tuple[list[BlockDict], list[FontMetaDict]]
"""

from __future__ import annotations

from . import schemas
from . import PyMuPDF
from .PyMuPDF import extract_with_pymupdf          # re-export for patch target resolution
from .pdfplumber import extract_with_pdfplumber
from .PaddleOCR import extract_with_paddleocr
from . import scan_detector
from utils.text_processor import TextProcessor


def extract_pdf(
    pdf_path: str,
    ocr: bool,
    ocr_text_quality_threshold: float,
    embed_model=None,
    config: dict | None = None,
) -> tuple:
    """Extract text from *pdf_path* using a three-tier cascade.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.
    ocr:
        When ``False`` the cascade stops after PyMuPDF regardless of score.
    ocr_text_quality_threshold:
        Minimum quality score for a tier's output to be accepted immediately.
    embed_model:
        Optional embedding model passed to ``_compute_quality_score``.

    Returns
    -------
    tuple
        ``(blocks, font_metadata)`` — font_metadata is non-empty only for
        the PyMuPDF path; all OCR paths return ``[]``.
    """
    cfg = config or {}

    # When OCR is disabled explicitly, skip scan detection and run pdfplumber.
    if not ocr:
        plumber_blocks = extract_with_pdfplumber(pdf_path)
        schemas.validate_blocks(plumber_blocks)
        return plumber_blocks, []

    # --- OCR enabled: per-page scan detection routing ---
    # Open the document and classify each page. We treat the document as
    # scanned if any page is classified as scanned (i.e., not native).
    import fitz  # lazy import

    doc = fitz.open(pdf_path)
    try:
        pages = list(doc)

        tp = TextProcessor()  # default-config instance for classify_page
        classifications = []
        for page_index, page in enumerate(pages):
            cls = scan_detector.classify_page(page, tp, cfg, page_index=page_index)
            classifications.append(cls)

        all_native = all(c.is_native for c in classifications)

        if all_native:
            # Structural extraction via pdfplumber; collect font metadata per native page
            plumber_blocks = extract_with_pdfplumber(pdf_path)
            font_meta: list = []
            for page in pages:
                # Use PyMuPDF helper to get per-page font metadata
                font_meta.extend(PyMuPDF.get_page_font_metadata(page))

            schemas.validate_blocks(plumber_blocks)
            return plumber_blocks, font_meta

        # If any page is scanned, run PaddleOCR for the whole document.
        dpi = cfg.get("quality_control", {}).get("ocr", {}).get("rasterization_dpi", 150)
        paddle_blocks = extract_with_paddleocr(pdf_path, dpi=dpi)
        schemas.validate_blocks(paddle_blocks)
        return paddle_blocks, []
    finally:
        doc.close()
