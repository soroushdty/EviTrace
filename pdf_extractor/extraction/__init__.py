"""
pdf_extractor/extraction/__init__.py
-------------------------------------
Extraction orchestrator. Runs a four-tier cascade:
  1. PyMuPDF  (extract_with_pymupdf)
  2. pdfplumber (extract_with_pdfplumber)
  3. Tesseract OCR (extract_with_tesseract)
  4. PaddleOCR (extract_with_paddleocr)

Each tier is scored with ``_compute_quality_score``. The first tier that
meets or exceeds ``ocr_text_quality_threshold`` wins. If none do, the
highest-scoring OCR backend (tier 3 vs tier 4) wins.

Public API
----------
- extract_pdf(pdf_path, ocr, ocr_text_quality_threshold, embed_model=None)
  -> tuple[list[BlockDict], list[FontMetaDict]]
"""

from __future__ import annotations

from . import schemas
from .PyMuPDF import extract_with_pymupdf
from .pdfplumber import extract_with_pdfplumber
from .Tesseract import extract_with_tesseract
from .PaddleOCR import extract_with_paddleocr


def _compute_quality_score(blocks: list, embed_model=None) -> float:
    """Return a text-quality score in [0.0, 1.0] for *blocks*.

    Uses the ratio of alphanumeric characters to total non-whitespace
    characters as a simple proxy for extraction quality. Returns 0.0
    for empty or blank blocks. ``embed_model`` is accepted but currently
    unused (reserved for future embedding-based scoring).
    """
    if not blocks:
        return 0.0
    total_text = "".join(b.get("text", "") for b in blocks)
    non_ws = total_text.replace(" ", "").replace("\n", "").replace("\t", "")
    if not non_ws:
        return 0.0
    alnum = sum(1 for c in non_ws if c.isalnum())
    return alnum / len(non_ws)


def extract_pdf(
    pdf_path: str,
    ocr: bool,
    ocr_text_quality_threshold: float,
    embed_model=None,
) -> tuple:
    """Extract text from *pdf_path* using a four-tier cascade.

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
    # --- Tier 1: PyMuPDF ---
    pymupdf_blocks, font_metadata = extract_with_pymupdf(pdf_path)
    score = _compute_quality_score(pymupdf_blocks, embed_model)
    if score >= ocr_text_quality_threshold or not ocr:
        schemas.validate_blocks(pymupdf_blocks)
        return pymupdf_blocks, font_metadata

    # --- Tier 2: pdfplumber ---
    plumber_blocks = extract_with_pdfplumber(pdf_path)
    score = _compute_quality_score(plumber_blocks, embed_model)
    if score >= ocr_text_quality_threshold:
        schemas.validate_blocks(plumber_blocks)
        return plumber_blocks, []

    # --- Tier 3: Tesseract ---
    tess_blocks = extract_with_tesseract(pdf_path)
    tess_score = _compute_quality_score(tess_blocks, embed_model)
    if tess_score >= ocr_text_quality_threshold:
        schemas.validate_blocks(tess_blocks)
        return tess_blocks, []

    # --- Tier 4: PaddleOCR ---
    paddle_blocks = extract_with_paddleocr(pdf_path)
    paddle_score = _compute_quality_score(paddle_blocks, embed_model)

    winner = paddle_blocks if paddle_score >= tess_score else tess_blocks
    schemas.validate_blocks(winner)
    return winner, []
