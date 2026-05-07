"""
tests/test_text_extractor_tier2.py
------------------------------------
Property-based tests for ``evi_trace.extraction.tier2.tier2`` (Tesseract backend).

Properties covered:
  6 (Tesseract half): OCR backend output conforms to BlockDict schema with null geometry
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from evi_trace.extraction import schemas
from evi_trace.extraction.tier2 import tier2

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helper: build mock pytesseract, pdf2image, and PIL modules
# ---------------------------------------------------------------------------

def _build_mock_modules(page_texts: list[str]) -> dict:
    """Return a sys.modules patch dict for pytesseract, pdf2image, and PIL."""
    # Mock pytesseract
    mock_pytesseract = MagicMock()
    mock_pytesseract.get_tesseract_version.return_value = "5.0.0"

    # image_to_string returns successive page texts
    call_count = {"n": 0}

    def _image_to_string(image, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(page_texts):
            return page_texts[idx]
        return ""

    mock_pytesseract.image_to_string.side_effect = _image_to_string

    # Mock PIL.Image
    mock_image_obj = MagicMock()
    mock_image_obj.close = MagicMock()
    mock_pil_image = MagicMock()
    mock_pil_image.Image = MagicMock()

    # Mock pdf2image
    mock_pdf2image = MagicMock()
    mock_pdf2image.pdfinfo_from_path.return_value = {"Pages": len(page_texts)}

    def _convert_from_path(path, first_page=1, last_page=1, **kwargs):
        return [mock_image_obj]

    mock_pdf2image.convert_from_path.side_effect = _convert_from_path

    return {
        "pytesseract": mock_pytesseract,
        "pdf2image": mock_pdf2image,
        "PIL": mock_pil_image,
        "PIL.Image": mock_pil_image.Image,
    }


# ---------------------------------------------------------------------------
# Property 6 (Tesseract): OCR backend output conforms to BlockDict schema with null geometry
# Feature: text-extractor-restructure, Property 6: OCR backends output conforms to BlockDict schema with null geometry
# Validates: Requirements 5.1, 5.2, 11.3
# ---------------------------------------------------------------------------

@given(
    page_texts=st.lists(
        st.text(min_size=1),  # non-empty so blocks are included
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=100)
def test_tier2_output_conforms_to_blockdict_schema(page_texts):
    # Feature: text-extractor-restructure, Property 6: OCR backends output conforms to BlockDict schema with null geometry
    mock_modules = _build_mock_modules(page_texts)

    with patch.dict(sys.modules, mock_modules):
        blocks = tier2.extract_with_tesseract("fake.pdf")

    # Every block must pass validate_blocks without raising.
    schemas.validate_blocks(blocks)

    # Null-geometry invariants.
    for block in blocks:
        assert block["block_bbox"] is None, "block_bbox must be None for Tesseract backend"
        assert block["spans"] == [], "spans must be [] for Tesseract backend"
