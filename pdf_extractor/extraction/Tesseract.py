"""
pdf_extractor/extraction/Tesseract.py
-------------------------------------
Tesseract OCR extraction backend.

Block construction uses :func:`~pdf_extractor.extraction.schemas.make_block`.

``pytesseract`` and ``pdf2image`` are installed lazily inside the
function body — no import-time side effects.
"""

from __future__ import annotations

import subprocess
import sys

from .. import schemas


def _ensure_pdf2image() -> None:
    """Install pdf2image if it is not already importable."""
    try:
        import pdf2image  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "pdf2image"]
        )


def extract_with_tesseract(pdf_path: str) -> list[schemas.BlockDict]:
    """Extract text from a PDF using Tesseract OCR.

    Converts each page to a PIL image with *pdf2image*, then runs
    ``pytesseract.image_to_string`` on each image individually so that
    only one page image is held in memory at a time.

    **Lazy installation**: if ``pytesseract`` or ``pdf2image`` are not
    already installed they are installed inside this function before use.

    Parameters
    ----------
    pdf_path:
        Absolute or relative path to the PDF file.

    Returns
    -------
    list[BlockDict]
        One block per page (0-based page index).  ``block_bbox`` is
        ``None`` and ``spans`` is ``[]``.

    Raises
    ------
    RuntimeError
        If the Tesseract binary is not found on PATH.
    """
    # ------------------------------------------------------------------ install
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "pytesseract", "pdf2image"]
        )

    _ensure_pdf2image()

    # ------------------------------------------------------------------ imports
    import pytesseract
    import pdf2image

    # Validate that local Tesseract binary is available.
    try:
        _ = pytesseract.get_tesseract_version()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Tesseract binary not found on this machine. Install Tesseract OCR and "
            "ensure it is available on PATH."
        ) from exc

    # ------------------------------------------------------------------ OCR
    blocks: list[schemas.BlockDict] = []

    from PIL import Image  # bundled with pdf2image's dependency chain

    # Determine total page count cheaply.
    info = pdf2image.pdfinfo_from_path(pdf_path)
    total_pages: int = info.get("Pages", 0)

    for page_index in range(total_pages):
        # pdf2image uses 1-based page numbers.
        page_images = pdf2image.convert_from_path(
            pdf_path,
            first_page=page_index + 1,
            last_page=page_index + 1,
        )
        if not page_images:
            continue

        image: Image.Image = page_images[0]
        page_text: str = pytesseract.image_to_string(image)

        # Discard the image from memory before the next iteration.
        image.close()
        del page_images

        if page_text.strip():
            blocks.append(
                schemas.make_block(
                    text=page_text,
                    page_index=page_index,
                    block_bbox=None,
                    spans=[],
                )
            )

    return blocks
