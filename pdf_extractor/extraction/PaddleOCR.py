"""
pdf_extractor/extraction/PaddleOCR.py
-------------------------------------
PaddleOCR extraction backend.

Block construction uses :func:`~pdf_extractor.extraction.schemas.make_block`.

``paddleocr``, ``paddlepaddle``, and ``pdf2image`` are installed lazily
inside the function body — no import-time side effects.
"""

from __future__ import annotations

import subprocess
import sys

from . import schemas


def _ensure_pdf2image() -> None:
    """Install pdf2image if it is not already importable."""
    try:
        import pdf2image  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "pdf2image"]
        )


def extract_with_paddleocr(pdf_path: str) -> list[schemas.BlockDict]:
    """Extract text from a PDF using PaddleOCR.

    Converts each page to a NumPy array with *pdf2image*, then runs
    ``PaddleOCR`` on each page individually so that only one page image
    is held in memory at a time.  Result lines from PaddleOCR are joined
    with newlines to form the page text block.

    **Lazy installation**: if ``paddleocr`` / ``paddlepaddle`` are not
    already installed they are installed inside this function before use.
    ``pdf2image`` is also installed here if absent.

    Parameters
    ----------
    pdf_path:
        Absolute or relative path to the PDF file.

    Returns
    -------
    list[BlockDict]
        One block per page (0-based page index).  ``block_bbox`` is
        ``None`` and ``spans`` is ``[]``.
    """
    # ------------------------------------------------------------------ install
    try:
        from paddleocr import PaddleOCR  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "paddleocr", "paddlepaddle"]
        )

    _ensure_pdf2image()

    # ------------------------------------------------------------------ imports
    from paddleocr import PaddleOCR
    import numpy as np
    import pdf2image

    # Initialise once; use_angle_cls handles rotated text gracefully.
    ocr_engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

    # ------------------------------------------------------------------ OCR
    blocks: list[schemas.BlockDict] = []

    info = pdf2image.pdfinfo_from_path(pdf_path)
    total_pages: int = info.get("Pages", 0)

    for page_index in range(total_pages):
        page_images = pdf2image.convert_from_path(
            pdf_path,
            first_page=page_index + 1,
            last_page=page_index + 1,
        )
        if not page_images:
            continue

        pil_image = page_images[0]
        # PaddleOCR expects a NumPy array (H, W, C) in BGR or RGB.
        image_array: np.ndarray = np.array(pil_image)

        # Discard PIL image immediately; we only need the array.
        pil_image.close()
        del page_images

        ocr_result = ocr_engine.ocr(image_array, cls=True)

        # Discard the array before processing the next page.
        del image_array

        # ocr_result is list[list[list]] — outer list per image (always 1
        # here), inner lists are [bounding_box, (text, confidence)].
        page_lines: list[str] = []
        if ocr_result and ocr_result[0]:
            for line_info in ocr_result[0]:
                if line_info and len(line_info) >= 2:
                    text_conf = line_info[1]
                    if isinstance(text_conf, (list, tuple)) and text_conf:
                        page_lines.append(str(text_conf[0]))

        page_text = "\n".join(page_lines).strip()
        if page_text:
            blocks.append(
                schemas.make_block(
                    text=page_text,
                    page_index=page_index,
                    block_bbox=None,
                    spans=[],
                )
            )

    return blocks
