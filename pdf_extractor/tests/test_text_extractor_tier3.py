"""
tests/test_text_extractor_tier3.py
------------------------------------
Property-based tests for ``pdf_extractor.extraction.tier3.tier3`` (PaddleOCR backend).

Properties covered:
  6 (PaddleOCR half): OCR backend output conforms to BlockDict schema with null geometry
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from pdf_extractor.extraction import schemas
from pdf_extractor.extraction.tier3 import tier3

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helper: build mock paddleocr, pdf2image, and numpy modules
# ---------------------------------------------------------------------------

def _build_mock_modules(page_texts: list[str]) -> dict:
    """Return a sys.modules patch dict for paddleocr and pdf2image."""
    # Mock PaddleOCR engine
    call_count = {"n": 0}

    def _ocr(image_array, cls=True):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(page_texts):
            text = page_texts[idx]
            if text.strip():
                # Return format: list[list[list]] — one image, one line
                return [[[None, (text, 0.99)]]]
        return [[]]

    mock_ocr_engine = MagicMock()
    mock_ocr_engine.ocr.side_effect = _ocr

    mock_paddleocr_module = MagicMock()
    mock_paddleocr_module.PaddleOCR.return_value = mock_ocr_engine

    # Mock pdf2image — PIL image must be convertible to a numpy array.
    # We use a real numpy array as the return value of np.array(pil_image).
    import numpy as np

    mock_pil_image = MagicMock()
    mock_pil_image.close = MagicMock()
    # Make np.array(mock_pil_image) return a real array by implementing __array__.
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
# Property 6 (PaddleOCR): OCR backend output conforms to BlockDict schema with null geometry
# Feature: text-extractor-restructure, Property 6: OCR backends output conforms to BlockDict schema with null geometry
# Validates: Requirements 6.1, 6.2, 11.4
# ---------------------------------------------------------------------------

@given(
    page_texts=st.lists(
        st.text(min_size=1),  # non-empty so blocks are included
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=100)
def test_tier3_output_conforms_to_blockdict_schema(page_texts):
    # Feature: text-extractor-restructure, Property 6: OCR backends output conforms to BlockDict schema with null geometry
    mock_modules = _build_mock_modules(page_texts)

    with patch.dict(sys.modules, mock_modules):
        blocks = tier3.extract_with_paddleocr("fake.pdf")

    # Every block must pass validate_blocks without raising.
    schemas.validate_blocks(blocks)

    # Null-geometry invariants.
    for block in blocks:
        assert block["block_bbox"] is None, "block_bbox must be None for PaddleOCR backend"
        assert block["spans"] == [], "spans must be [] for PaddleOCR backend"
