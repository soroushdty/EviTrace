"""
tests/pdf_extractor/test_paddleocr_backend.py
----------------------------------------------
Property-based tests for ``pdf_extractor.extraction.PaddleOCR`` (PaddleOCR backend).

Properties covered:
  - PaddleOCR backend output conforms to BlockDict schema with non-None bbox
    in PDF-space coordinates
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from pdf_extractor.extraction import schemas
from pdf_extractor.extraction import PaddleOCR as paddleocr_backend

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helper: build mock paddleocr, pdf2image, and numpy modules
# ---------------------------------------------------------------------------

def _build_mock_modules(page_texts: list[str]) -> dict:
    """Return a sys.modules patch dict for paddleocr and pdf2image.

    Each page produces one OCR line with a proper bounding box so that
    ``block_bbox`` is populated with PDF-space coordinates.
    """
    # Mock PaddleOCR engine — returns lines with bounding boxes
    call_count = {"n": 0}

    def _ocr(image_array, cls=True):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(page_texts):
            text = page_texts[idx]
            if text.strip():
                # Return format: list[list[list]] — one image, one line
                # bbox_pts: four [x, y] corner points (pixel coords)
                bbox_pts = [[10.0, 10.0], [200.0, 10.0], [200.0, 30.0], [10.0, 30.0]]
                return [[[bbox_pts, (text, 0.99)]]]
        return [[]]

    mock_ocr_engine = MagicMock()
    mock_ocr_engine.ocr.side_effect = _ocr

    mock_paddleocr_module = MagicMock()
    mock_paddleocr_module.PaddleOCR.return_value = mock_ocr_engine

    # Mock pdf2image — PIL image must be convertible to a numpy array.
    import numpy as np

    mock_pil_image = MagicMock()
    mock_pil_image.close = MagicMock()
    mock_pil_image.__array__ = MagicMock(return_value=np.zeros((10, 10, 3), dtype=np.uint8))

    mock_pdf2image = MagicMock()
    mock_pdf2image.pdfinfo_from_path.return_value = {"Pages": len(page_texts)}

    def _convert_from_path(path, first_page=1, last_page=1, **kwargs):
        return [mock_pil_image]

    mock_pdf2image.convert_from_path.side_effect = _convert_from_path

    return {
        "paddleocr": mock_paddleocr_module,
        "pdf2image": mock_pdf2image,
    }


# ---------------------------------------------------------------------------
# PaddleOCR backend output conforms to BlockDict schema with non-None bbox
# ---------------------------------------------------------------------------

@given(
    page_texts=st.lists(
        st.text(min_size=1),  # non-empty so blocks are included
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=20)
def test_paddleocr_output_conforms_to_blockdict_schema(page_texts):
    mock_modules = _build_mock_modules(page_texts)

    with patch.dict(sys.modules, mock_modules):
        blocks = paddleocr_backend.extract_with_paddleocr("fake.pdf")

    # Every block must pass validate_blocks without raising.
    schemas.validate_blocks(blocks)

    # PaddleOCR blocks carry PDF-space bounding boxes (non-None).
    for block in blocks:
        assert block["block_bbox"] is not None, (
            "block_bbox must not be None for PaddleOCR backend — "
            "it should contain PDF-space coordinates (x0, y0, x1, y1)"
        )
        bbox = block["block_bbox"]
        assert isinstance(bbox, tuple), f"block_bbox must be a tuple, got {type(bbox)}"
        assert len(bbox) == 4, f"block_bbox must have 4 elements, got {len(bbox)}"
        # All coordinates must be finite floats
        for coord in bbox:
            assert isinstance(coord, float), f"bbox coordinate must be float, got {type(coord)}"
        # x1 > x0 and y1 > y0 (valid bounding box)
        x0, y0, x1, y1 = bbox
        assert x1 > x0, f"block_bbox x1 ({x1}) must be greater than x0 ({x0})"
        assert y1 > y0, f"block_bbox y1 ({y1}) must be greater than y0 ({y0})"
        # spans is always [] for PaddleOCR backend
        assert block["spans"] == [], "spans must be [] for PaddleOCR backend"
