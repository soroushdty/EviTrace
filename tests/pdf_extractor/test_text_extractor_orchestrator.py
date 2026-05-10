"""
tests/pdf_extractor/test_text_extractor_orchestrator.py
---------------------------------------------------------
Tests for the extraction routing architecture in pdf_extractor/extraction/.

NOTE: The standalone ``extract_pdf()`` orchestrator function was removed from
``pdf_extractor/extraction/__init__.py`` as part of the extraction-routing-
alignment bugfix (task 3.3).  The routing logic now lives in
``pipeline/orchestrator._build_qc_context()``, which is tested by:

  - tests/steering/test_steering_drift_extraction_routing_alignment_bug_condition.py
  - tests/steering/test_steering_drift_extraction_routing_alignment_preservation.py
  - tests/pipeline/test_orchestrator_concurrency.py

The tests in this file have been updated to reflect the new architecture.
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
# Architecture verification — extract_pdf must NOT exist
# ---------------------------------------------------------------------------

def test_extract_pdf_removed():
    """extract_pdf() must NOT be present in pdf_extractor.extraction.

    The standalone extract_pdf() orchestrator was removed as part of the
    extraction-routing-alignment bugfix (task 3.3).  Routing is now handled
    by pipeline/orchestrator._build_qc_context().
    """
    assert not hasattr(pdf_extractor.extraction, "extract_pdf"), (
        "extract_pdf() must be removed from pdf_extractor.extraction. "
        "Routing is now handled by pipeline/orchestrator._build_qc_context()."
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
