"""
tests/pdf_extractor/test_text_extractor_orchestrator.py
---------------------------------------------------------
Tests for the public API of ``pdf_extractor.extraction``.

Verifies that all six expected symbols are exported from the package and
that ``extract_pdf`` (a removed orchestrator function) is not present.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import pdf_extractor.extraction
from pdf_extractor.extraction.scan_detector import PageScanClassification


# ---------------------------------------------------------------------------
# Autouse fixture: prevent spacy.load('en_core_sci_sm') from running in CI
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_scispacy(monkeypatch):
    """Prevent spacy.load('en_core_sci_sm') from running in CI."""
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)


# ---------------------------------------------------------------------------
# Public API verification — extract_pdf must NOT exist
# ---------------------------------------------------------------------------

def test_extract_pdf_not_in_public_api():
    """extract_pdf() must NOT be present in pdf_extractor.extraction.

    The standalone extract_pdf() orchestrator is not part of the current
    public API.  Routing is handled by pipeline/orchestrator.
    """
    assert not hasattr(pdf_extractor.extraction, "extract_pdf"), (
        "extract_pdf() must not be exported from pdf_extractor.extraction."
    )


# ---------------------------------------------------------------------------
# Module-level re-exports still present
# ---------------------------------------------------------------------------

def test_extract_with_pdfplumber_exported():
    """extract_with_pdfplumber must be importable from pdf_extractor.extraction."""
    assert hasattr(pdf_extractor.extraction, "extract_with_pdfplumber"), (
        "extract_with_pdfplumber must be re-exported from pdf_extractor.extraction"
    )
    assert callable(pdf_extractor.extraction.extract_with_pdfplumber)


def test_extract_with_paddleocr_exported():
    """extract_with_paddleocr must be importable from pdf_extractor.extraction."""
    assert hasattr(pdf_extractor.extraction, "extract_with_paddleocr"), (
        "extract_with_paddleocr must be re-exported from pdf_extractor.extraction"
    )
    assert callable(pdf_extractor.extraction.extract_with_paddleocr)


def test_extract_with_pymupdf_exported():
    """extract_with_pymupdf must be importable from pdf_extractor.extraction."""
    assert hasattr(pdf_extractor.extraction, "extract_with_pymupdf"), (
        "extract_with_pymupdf must be re-exported from pdf_extractor.extraction"
    )
    assert callable(pdf_extractor.extraction.extract_with_pymupdf)


def test_scan_detector_exported():
    """scan_detector must be importable from pdf_extractor.extraction."""
    assert hasattr(pdf_extractor.extraction, "scan_detector"), (
        "scan_detector must be re-exported from pdf_extractor.extraction"
    )


def test_schemas_exported():
    """schemas must be importable from pdf_extractor.extraction."""
    assert hasattr(pdf_extractor.extraction, "schemas"), (
        "schemas must be re-exported from pdf_extractor.extraction"
    )


def test_pymupdf_module_exported():
    """PyMuPDF module must be importable from pdf_extractor.extraction."""
    assert hasattr(pdf_extractor.extraction, "PyMuPDF"), (
        "PyMuPDF module must be re-exported from pdf_extractor.extraction"
    )
