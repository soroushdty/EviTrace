"""
tests/pdf_extractor/test_parser_pipeline.py
============================================
Smoke tests for the pdfplumber extraction backend.
"""

import pytest
from unittest.mock import MagicMock, patch


def test_extract_with_pdfplumber_returns_list(tmp_path):
    """extract_with_pdfplumber returns a list of BlockDict-shaped dicts."""
    # Write a minimal stub PDF so pdfplumber.open does not raise on path validation.
    # We mock pdfplumber itself so no real PDF parsing occurs.
    mock_page = MagicMock()
    mock_page.find_tables.return_value = []
    mock_page.extract_text.return_value = "Hello world"

    mock_pdf = MagicMock()
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdf.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf):
        from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber
        blocks = extract_with_pdfplumber("fake.pdf")

    assert isinstance(blocks, list)
    assert len(blocks) >= 1
    assert "text" in blocks[0]
    assert "page_index" in blocks[0]


def test_extract_with_pdfplumber_block_has_required_keys(tmp_path):
    """Each block returned by extract_with_pdfplumber has the required schema keys."""
    mock_page = MagicMock()
    mock_page.find_tables.return_value = []
    mock_page.extract_text.return_value = "Sample text content"

    mock_pdf = MagicMock()
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdf.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf):
        from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber
        blocks = extract_with_pdfplumber("fake.pdf")

    for block in blocks:
        assert "text" in block
        assert "page_index" in block
        assert "block_bbox" in block
        assert "spans" in block
