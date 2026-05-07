"""
tests/test_text_extractor_tier1.py
------------------------------------
Property-based tests for ``pdf_extractor.extraction.tier1.tier1`` (pdfplumber backend).

Properties covered:
  7. pdfplumber backend output conforms to BlockDict schema with null geometry
  8. pdfplumber backend embeds [PAGE n] marker in every block
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from pdf_extractor.extraction import schemas
from pdf_extractor.extraction.tier1 import tier1

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helper: build a mock pdfplumber module
# ---------------------------------------------------------------------------

def _build_mock_pdfplumber(page_texts: list[str]) -> MagicMock:
    """Return a mock pdfplumber module whose ``open`` yields *page_texts*."""
    mock_pages = []
    for text in page_texts:
        mock_page = MagicMock()
        mock_page.extract_text.return_value = text
        mock_page.extract_tables.return_value = []
        mock_pages.append(mock_page)

    mock_pdf = MagicMock()
    mock_pdf.pages = mock_pages
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    mock_pdfplumber = MagicMock()
    mock_pdfplumber.open.return_value = mock_pdf

    return mock_pdfplumber


# ---------------------------------------------------------------------------
# Property 7: pdfplumber backend output conforms to BlockDict schema with null geometry
# Feature: text-extractor-restructure, Property 7: pdfplumber backend output conforms to BlockDict schema with null geometry
# Validates: Requirements 4.1, 4.3, 11.2
# ---------------------------------------------------------------------------

@given(
    page_texts=st.lists(
        st.text(min_size=1),  # non-empty so pages are included
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=100)
def test_tier1_output_conforms_to_blockdict_schema(page_texts):
    # Feature: text-extractor-restructure, Property 7: pdfplumber backend output conforms to BlockDict schema with null geometry
    mock_pdfplumber = _build_mock_pdfplumber(page_texts)

    with patch.dict(sys.modules, {"pdfplumber": mock_pdfplumber}):
        blocks = tier1.extract_pdf_text(Path("fake.pdf"))

    # Every block must pass validate_blocks without raising.
    schemas.validate_blocks(blocks)

    # Null-geometry invariants.
    for block in blocks:
        assert block["block_bbox"] is None, "block_bbox must be None for pdfplumber backend"
        assert block["spans"] == [], "spans must be [] for pdfplumber backend"


# ---------------------------------------------------------------------------
# Property 8: pdfplumber backend embeds [PAGE n] marker in every block
# Feature: text-extractor-restructure, Property 8: pdfplumber backend embeds [PAGE n] marker in every block
# Validates: Requirements 4.2
# ---------------------------------------------------------------------------

@given(
    page_count=st.integers(min_value=1, max_value=20),
    page_texts=st.data(),
)
@settings(max_examples=100)
def test_tier1_embeds_page_markers(page_count, page_texts):
    # Feature: text-extractor-restructure, Property 8: pdfplumber backend embeds [PAGE n] marker in every block
    texts = [page_texts.draw(st.text(min_size=1)) for _ in range(page_count)]
    mock_pdfplumber = _build_mock_pdfplumber(texts)

    with patch.dict(sys.modules, {"pdfplumber": mock_pdfplumber}):
        blocks = tier1.extract_pdf_text(Path("fake.pdf"))

    assert len(blocks) == page_count, (
        f"Expected {page_count} blocks, got {len(blocks)}"
    )

    for i, block in enumerate(blocks):
        expected_marker = f"[PAGE {i + 1}]"
        assert expected_marker in block["text"], (
            f"Block {i} text does not contain '{expected_marker}': {block['text']!r}"
        )
