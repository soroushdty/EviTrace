"""
tests/pdf_extractor/test_text_extractor_orchestrator.py
---------------------------------------------------------
Tests for the extract_pdf orchestrator in pdf_extractor/extraction/__init__.py.

Covers the current scan-detector routing architecture:
  - ocr=False  → pdfplumber only, no scan detection, empty font metadata
  - ocr=True, all native  → pdfplumber + font metadata, no PaddleOCR
  - ocr=True, any scanned → PaddleOCR, no pdfplumber, empty font metadata
  - validate_blocks is always called exactly once before returning
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
# Shared helpers
# ---------------------------------------------------------------------------

def _make_block(label: str, page_index: int = 0) -> dict:
    return {"text": f"{label} text", "page_index": page_index, "block_bbox": None, "spans": []}


def _make_fitz_doc(pages: list):
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter(pages))
    mock_doc.close = MagicMock()
    mock_fitz = MagicMock()
    mock_fitz.open = MagicMock(return_value=mock_doc)
    return mock_fitz, mock_doc


def _native(page_index: int = 0) -> PageScanClassification:
    return PageScanClassification(
        page_index=page_index, is_native=True, triggered_stages=[],
        stage_values={"word_count": 100.0, "alpha_ratio": 0.95, "font_count": 3.0, "image_coverage": 0.01},
    )


def _scanned(page_index: int = 0) -> PageScanClassification:
    return PageScanClassification(
        page_index=page_index, is_native=False, triggered_stages=[1], stage_values={},
    )


_PLUMBER_BLOCKS = [_make_block("plumber")]
_PADDLE_BLOCKS = [_make_block("paddle")]
_FONT_META = [{"size": 12.0, "text": "hello", "page": 0}]
_CONFIG = {"quality_control": {"ocr": {"rasterization_dpi": 150}}}


# ---------------------------------------------------------------------------
# ocr=False path
# ---------------------------------------------------------------------------

class TestOcrFalse:
    def test_returns_pdfplumber_blocks(self):
        with patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert blocks == _PLUMBER_BLOCKS

    def test_returns_empty_font_metadata(self):
        with patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS):
            _, font_meta = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert font_meta == []

    def test_scan_detection_not_called(self):
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.scan_detector.classify_page") as mock_cls,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        mock_cls.assert_not_called()

    def test_validate_blocks_called_once(self):
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_val,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        mock_val.assert_called_once_with(_PLUMBER_BLOCKS)


# ---------------------------------------------------------------------------
# ocr=True, all native pages
# ---------------------------------------------------------------------------

class TestOcrTrueAllNative:
    def test_returns_pdfplumber_blocks(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_native()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=_FONT_META),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            blocks, _ = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert blocks == _PLUMBER_BLOCKS
        mock_paddle.assert_not_called()

    def test_returns_font_metadata(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_native()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr"),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=_FONT_META),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            _, font_meta = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert font_meta == _FONT_META

    def test_validate_blocks_called_once(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_native()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr"),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=[]),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_val,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert mock_val.call_count == 1


# ---------------------------------------------------------------------------
# ocr=True, scanned pages
# ---------------------------------------------------------------------------

class TestOcrTrueScanned:
    def test_returns_paddle_blocks(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_scanned()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber") as mock_plumber,
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata"),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            blocks, _ = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert blocks == _PADDLE_BLOCKS
        mock_plumber.assert_not_called()

    def test_returns_empty_font_metadata(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_scanned()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber"),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata"),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            _, font_meta = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert font_meta == []

    def test_validate_blocks_called_once(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_scanned()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber"),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata"),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_val,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert mock_val.call_count == 1
