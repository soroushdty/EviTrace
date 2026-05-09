"""
tests/test_text_extractor_tier2.py
------------------------------------
Property-based tests for ``pdf_extractor.extraction.PyMuPDF`` (PyMuPDF backend).

Tesseract OCR backend removed as of architecture-migration task 1.1.
Scanned pages are now routed to PaddleOCR (tier 3).

Properties covered:
  9. PyMuPDF backend output conforms to BlockDict schema with non-null geometry
  10. PyMuPDF backend returns font metadata with correct schema
"""

import sys
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from pdf_extractor.extraction import schemas
from pdf_extractor.extraction import PyMuPDF as tier2

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helper: build a mock fitz (PyMuPDF) module
# ---------------------------------------------------------------------------

def _build_mock_fitz(page_spans: list[list[dict]]) -> MagicMock:
    """Return a mock fitz module whose ``open`` yields pages with *page_spans*.

    Parameters
    ----------
    page_spans:
        A list (one per page) of span-definition dicts, each containing
        ``text``, ``font``, ``size``, ``flags``, ``color``, and ``bbox``.
    """
    mock_pages = []
    for page_idx, spans in enumerate(page_spans):
        # Build blocks -> lines -> spans structure matching fitz.Page.get_text("dict")
        fitz_spans = [
            {
                "text": s["text"],
                "font": s.get("font", "Arial"),
                "size": s.get("size", 12.0),
                "flags": s.get("flags", 0),
                "color": s.get("color", 0),
                "bbox": s.get("bbox", (0.0, 0.0, 100.0, 20.0)),
            }
            for s in spans
        ]
        line_dict = {"spans": fitz_spans}
        block_dict = {
            "type": 0,
            "bbox": (0.0, 0.0, 200.0, 40.0),
            "lines": [line_dict],
        }
        page_text_dict = {"blocks": [block_dict]}

        mock_page = MagicMock()
        mock_page.get_text.return_value = page_text_dict
        mock_pages.append(mock_page)

    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter(mock_pages))
    mock_doc.close = MagicMock()

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc

    return mock_fitz


# ---------------------------------------------------------------------------
# Property 9: PyMuPDF backend output conforms to BlockDict schema with non-null geometry
# Feature: text-extractor-restructure, Property 9: PyMuPDF backend output conforms to BlockDict schema
# Validates: Requirements 3.1, 4
# ---------------------------------------------------------------------------

@given(
    page_spans=st.lists(
        st.lists(
            st.fixed_dictionaries({
                "text": st.text(min_size=1),
                "font": st.just("Arial"),
                "size": st.floats(min_value=1.0, max_value=72.0, allow_nan=False),
                "flags": st.just(0),
                "color": st.just(0),
                "bbox": st.just((0.0, 0.0, 100.0, 20.0)),
            }),
            min_size=1,
            max_size=5,
        ),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=50)
def test_tier2_output_conforms_to_blockdict_schema(page_spans):
    # Feature: text-extractor-restructure, Property 9: PyMuPDF backend output conforms to BlockDict schema
    mock_fitz = _build_mock_fitz(page_spans)

    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        blocks, font_metadata = tier2.extract_with_pymupdf("fake.pdf")

    # Every block must pass validate_blocks without raising.
    schemas.validate_blocks(blocks)

    # PyMuPDF blocks carry actual bounding boxes (non-None).
    for block in blocks:
        assert block["block_bbox"] is not None, (
            "block_bbox must not be None for PyMuPDF backend"
        )


# ---------------------------------------------------------------------------
# Property 10: PyMuPDF backend returns font metadata with correct schema
# Feature: text-extractor-restructure, Property 10: PyMuPDF backend returns FontMetaDict
# Validates: Requirements 3.1, 4
# ---------------------------------------------------------------------------

@given(
    page_spans=st.lists(
        st.lists(
            st.fixed_dictionaries({
                "text": st.text(min_size=1),
                "font": st.just("Helvetica"),
                "size": st.floats(min_value=6.0, max_value=72.0, allow_nan=False),
                "flags": st.just(0),
                "color": st.just(0),
                "bbox": st.just((0.0, 0.0, 100.0, 20.0)),
            }),
            min_size=1,
            max_size=5,
        ),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=50)
def test_tier2_returns_font_metadata(page_spans):
    # Feature: text-extractor-restructure, Property 10: PyMuPDF backend returns FontMetaDict
    mock_fitz = _build_mock_fitz(page_spans)

    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        blocks, font_metadata = tier2.extract_with_pymupdf("fake.pdf")

    # Font metadata must be returned alongside blocks.
    total_spans = sum(len(spans) for spans in page_spans)
    assert len(font_metadata) == total_spans, (
        f"Expected {total_spans} FontMetaDict entries, got {len(font_metadata)}"
    )

    # Each font metadata entry must have the required keys.
    for entry in font_metadata:
        assert "size" in entry, "FontMetaDict missing 'size'"
        assert "text" in entry, "FontMetaDict missing 'text'"
        assert "page" in entry, "FontMetaDict missing 'page'"
        assert isinstance(entry["size"], float), "FontMetaDict 'size' must be float"
        assert isinstance(entry["text"], str), "FontMetaDict 'text' must be str"
        assert isinstance(entry["page"], int), "FontMetaDict 'page' must be int"
