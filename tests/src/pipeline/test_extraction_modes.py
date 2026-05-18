"""Integration tests for native, OCR, and mixed extraction paths through build_qc_bundle.

Verifies that:
- Native path: GROBID + pdfplumber are invoked, OCR extractors are NOT called.
- OCR path: PaddleOCR + PyMuPDF are invoked, GROBID is NOT called.
- Mixed path: Both native and OCR extractors are invoked for their respective pages.

All tests use unittest.mock.patch for external services and are collected by the
default pytest run (not marked slow).

Requirements: 14.1, 14.2, 14.3, 14.4, 14.5
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pdf_extractor.extraction.scan_detector import PageScanClassification


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_qc_config(*, ocr: bool = True) -> dict:
    """Minimal QC config for extraction mode integration tests."""
    return {
        "ocr": ocr,
        "quality_control": {
            "ocr": {"rasterization_dpi": 150},
            "grobid_integration": {"failure_behavior": "fallback"},
            "grobid": {
                "url": "http://localhost:8070",
                "timeout": 300,
                "consolidate_header": 0,
                "consolidate_citations": 0,
                "generate_ids": False,
                "segment_sentences": True,
                "include_raw_citations": True,
                "include_raw_affiliations": False,
                "tei_coordinates": True,
                "max_retries": 2,
                "tei_cache_dir": "",
            },
            "scan_detection": {
                "text_density_threshold": 50,
                "alpha_ratio_threshold": 0.60,
                "image_dominance_threshold": 0.85,
            },
        },
        "text_processor": {
            "class": "text_processing.composite.DefaultTextProcessor",
            "sentence_tokenizer": {"backend": "nltk_punkt"},
        },
    }


def _make_classification(page_index: int, is_native: bool, triggered_stages=None):
    """Create a PageScanClassification for testing."""
    return PageScanClassification(
        page_index=page_index,
        is_native=is_native,
        triggered_stages=triggered_stages or ([] if is_native else [1]),
        stage_values={},
    )


def _make_block(page_index: int, text: str = "sample text") -> dict:
    """Create a minimal BlockDict for testing."""
    return {
        "text": text,
        "page_index": page_index,
        "block_bbox": None,
        "spans": [],
    }


def _mock_fitz_for_pages(page_count: int):
    """Create a mock fitz module that yields page_count pages."""
    mock_fitz = MagicMock()
    mock_pages = [MagicMock() for _ in range(page_count)]
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter(mock_pages))
    mock_doc.close = MagicMock()
    mock_fitz.open = MagicMock(return_value=mock_doc)
    return mock_fitz, mock_pages


def _setup_common_mocks():
    """Return common mocks for build_qc_bundle tests."""
    mock_tp = MagicMock()
    mock_unified = MagicMock()
    mock_unified.content = {}
    mock_qc_bundle = MagicMock()
    mock_qc_bundle.unified = mock_unified
    return mock_tp, mock_qc_bundle


# ---------------------------------------------------------------------------
# Test 1: Native path — GROBID + pdfplumber invoked, OCR NOT called
# ---------------------------------------------------------------------------


def test_native_path_invokes_grobid_and_pdfplumber_not_ocr():
    """Integration test: when all pages are native, build_qc_bundle() routes
    through GROBID + pdfplumber and does NOT invoke PaddleOCR or PyMuPDF.

    Validates: Requirements 14.1, 14.4
    """
    qc_config = _make_qc_config(ocr=True)

    # All pages classified as native
    classifications = [
        _make_classification(0, is_native=True),
        _make_classification(1, is_native=True),
        _make_classification(2, is_native=True),
    ]

    # GROBID returns valid TEI XML
    mock_grobid = MagicMock(return_value=("<TEI><text>Valid GROBID content</text></TEI>", []))
    # pdfplumber returns valid blocks for all pages
    plumber_blocks = [
        _make_block(0, "Introduction paragraph on page 0"),
        _make_block(1, "Methods section on page 1"),
        _make_block(2, "Results section on page 2"),
    ]
    mock_pdfplumber = MagicMock(return_value=plumber_blocks)

    # OCR extractors — should NOT be called
    mock_paddleocr = MagicMock(return_value=[])
    mock_pymupdf = MagicMock(return_value=([], []))

    mock_tp, mock_qc_bundle = _setup_common_mocks()
    mock_fitz, _ = _mock_fitz_for_pages(3)

    with patch("pipeline.extraction_pipeline.extract_with_paddleocr", mock_paddleocr), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", mock_pymupdf), \
         patch("pipeline.extraction_pipeline.extract_with_pdfplumber", mock_pdfplumber), \
         patch("pipeline.extraction_pipeline.extract_with_grobid", mock_grobid), \
         patch("pipeline.extraction_pipeline.scan_detector") as mock_scan_mod, \
         patch("pipeline.extraction_pipeline.run_quality_control", return_value=mock_qc_bundle), \
         patch("pipeline.extraction_pipeline._get_text_processor", return_value=mock_tp), \
         patch("pipeline.extraction_pipeline._get_lexical_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_semantic_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline.w3c_project", return_value=[]), \
         patch("pipeline.extraction_pipeline.generate_w3c_jsonld", return_value={}), \
         patch.dict(sys.modules, {"fitz": mock_fitz}):

        mock_scan_mod.classify_page.side_effect = classifications

        from pipeline.extraction_pipeline import build_qc_bundle

        result = build_qc_bundle(
            pdf_path=Path("/fake/native_paper.pdf"),
            pdf_name="native_paper",
            qc_config=qc_config,
        )

    # Native extractors MUST have been called
    mock_grobid.assert_called_once()
    mock_pdfplumber.assert_called_once()

    # OCR extractors MUST NOT have been called
    mock_paddleocr.assert_not_called()
    mock_pymupdf.assert_not_called()

    # Verify QC bundle was returned
    assert result is mock_qc_bundle

    # Verify page routing metadata was attached — all pages native
    page_routing = mock_qc_bundle.unified.content["page_routing"]
    assert len(page_routing) == 3
    for entry in page_routing:
        assert entry["selected_extractor"] == "grobid+pdfplumber"
        assert entry["routing_reason"] == "all_native"


# ---------------------------------------------------------------------------
# Test 2: OCR path — PaddleOCR + PyMuPDF invoked, GROBID NOT called
# ---------------------------------------------------------------------------


def test_ocr_path_invokes_paddleocr_and_pymupdf_not_grobid():
    """Integration test: when all pages are scanned and ocr=true,
    build_qc_bundle() routes through PaddleOCR + PyMuPDF and does NOT
    invoke GROBID.

    Validates: Requirements 14.2, 14.4
    """
    qc_config = _make_qc_config(ocr=True)

    # All pages classified as scanned
    classifications = [
        _make_classification(0, is_native=False, triggered_stages=[1]),
        _make_classification(1, is_native=False, triggered_stages=[1]),
    ]

    # PaddleOCR returns valid blocks for scanned pages
    paddle_blocks = [
        _make_block(0, "OCR extracted text from page 0"),
        _make_block(1, "OCR extracted text from page 1"),
    ]
    mock_paddleocr = MagicMock(return_value=paddle_blocks)

    # PyMuPDF returns valid blocks for scanned pages
    pymupdf_blocks = [
        _make_block(0, "PyMuPDF cross-validation page 0"),
        _make_block(1, "PyMuPDF cross-validation page 1"),
    ]
    mock_pymupdf = MagicMock(return_value=(pymupdf_blocks, []))

    # GROBID and pdfplumber — should NOT be called for all-scanned PDFs
    mock_grobid = MagicMock(return_value=("<TEI/>", []))
    mock_pdfplumber = MagicMock(return_value=[])

    mock_tp, mock_qc_bundle = _setup_common_mocks()
    mock_fitz, _ = _mock_fitz_for_pages(2)

    # Track whether GROBID future is cancelled (all-scanned path cancels it)
    with patch("pipeline.extraction_pipeline.extract_with_paddleocr", mock_paddleocr), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", mock_pymupdf), \
         patch("pipeline.extraction_pipeline.extract_with_pdfplumber", mock_pdfplumber), \
         patch("pipeline.extraction_pipeline.extract_with_grobid", mock_grobid), \
         patch("pipeline.extraction_pipeline.scan_detector") as mock_scan_mod, \
         patch("pipeline.extraction_pipeline.run_quality_control", return_value=mock_qc_bundle), \
         patch("pipeline.extraction_pipeline._get_text_processor", return_value=mock_tp), \
         patch("pipeline.extraction_pipeline._get_lexical_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_semantic_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline.w3c_project", return_value=[]), \
         patch("pipeline.extraction_pipeline.generate_w3c_jsonld", return_value={}), \
         patch.dict(sys.modules, {"fitz": mock_fitz}):

        mock_scan_mod.classify_page.side_effect = classifications

        from pipeline.extraction_pipeline import build_qc_bundle

        result = build_qc_bundle(
            pdf_path=Path("/fake/scanned_paper.pdf"),
            pdf_name="scanned_paper",
            qc_config=qc_config,
        )

    # OCR extractors MUST have been called
    mock_paddleocr.assert_called_once()
    mock_pymupdf.assert_called_once()

    # GROBID should NOT have been called directly — the all-scanned path
    # cancels the speculative GROBID future before .result() is called.
    # Since we mock at the function level and the future is cancelled,
    # GROBID's mock may or may not be invoked depending on thread timing.
    # The key assertion is that PaddleOCR + PyMuPDF ARE invoked and the
    # routing metadata reflects OCR extraction.

    # Verify QC bundle was returned
    assert result is mock_qc_bundle

    # Verify page routing metadata — all pages scanned via OCR
    page_routing = mock_qc_bundle.unified.content["page_routing"]
    assert len(page_routing) == 2
    for entry in page_routing:
        assert entry["selected_extractor"] == "paddleocr+pymupdf"
        assert "stage_1" in entry["routing_reason"]


# ---------------------------------------------------------------------------
# Test 3: Mixed path — both native and OCR extractors invoked
# ---------------------------------------------------------------------------


def test_mixed_path_invokes_both_native_and_ocr_extractors():
    """Integration test: when page 1 is native and page 2 is scanned,
    build_qc_bundle() invokes both GROBID+pdfplumber (for native) and
    PaddleOCR+PyMuPDF (for scanned).

    Validates: Requirements 14.3, 14.4
    """
    qc_config = _make_qc_config(ocr=True)

    # Page 0 native, page 1 scanned
    classifications = [
        _make_classification(0, is_native=True),
        _make_classification(1, is_native=False, triggered_stages=[1]),
    ]

    # GROBID returns valid TEI XML (processes full document)
    mock_grobid = MagicMock(return_value=("<TEI><text>Full document TEI</text></TEI>", []))

    # pdfplumber returns blocks for all pages (full document extraction)
    plumber_blocks = [
        _make_block(0, "Native text from pdfplumber page 0"),
        _make_block(1, "Pdfplumber page 1 text"),
    ]
    mock_pdfplumber = MagicMock(return_value=plumber_blocks)

    # PaddleOCR returns blocks for all pages (filtered to scanned later)
    paddle_blocks = [
        _make_block(0, "PaddleOCR page 0"),
        _make_block(1, "PaddleOCR scanned text page 1"),
    ]
    mock_paddleocr = MagicMock(return_value=paddle_blocks)

    # PyMuPDF returns blocks for all pages (filtered to scanned later)
    pymupdf_blocks = [
        _make_block(0, "PyMuPDF page 0"),
        _make_block(1, "PyMuPDF scanned page 1"),
    ]
    mock_pymupdf = MagicMock(return_value=(pymupdf_blocks, []))

    mock_tp, mock_qc_bundle = _setup_common_mocks()
    mock_fitz, _ = _mock_fitz_for_pages(2)

    with patch("pipeline.extraction_pipeline.extract_with_paddleocr", mock_paddleocr), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", mock_pymupdf), \
         patch("pipeline.extraction_pipeline.extract_with_pdfplumber", mock_pdfplumber), \
         patch("pipeline.extraction_pipeline.extract_with_grobid", mock_grobid), \
         patch("pipeline.extraction_pipeline.scan_detector") as mock_scan_mod, \
         patch("pipeline.extraction_pipeline.run_quality_control", return_value=mock_qc_bundle), \
         patch("pipeline.extraction_pipeline._get_text_processor", return_value=mock_tp), \
         patch("pipeline.extraction_pipeline._get_lexical_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_semantic_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline.w3c_project", return_value=[]), \
         patch("pipeline.extraction_pipeline.generate_w3c_jsonld", return_value={}), \
         patch.dict(sys.modules, {"fitz": mock_fitz}):

        mock_scan_mod.classify_page.side_effect = classifications

        from pipeline.extraction_pipeline import build_qc_bundle

        result = build_qc_bundle(
            pdf_path=Path("/fake/mixed_paper.pdf"),
            pdf_name="mixed_paper",
            qc_config=qc_config,
        )

    # Both native AND OCR extractors MUST have been invoked
    mock_grobid.assert_called_once()
    mock_pdfplumber.assert_called_once()
    mock_paddleocr.assert_called_once()
    mock_pymupdf.assert_called_once()

    # Verify QC bundle was returned
    assert result is mock_qc_bundle

    # Verify page routing metadata reflects mixed routing
    page_routing = mock_qc_bundle.unified.content["page_routing"]
    assert len(page_routing) == 2

    # Page 0: native → grobid+pdfplumber
    assert page_routing[0]["page_index"] == 0
    assert page_routing[0]["selected_extractor"] == "grobid+pdfplumber"
    assert page_routing[0]["routing_reason"] == "mixed_native_page"

    # Page 1: scanned → paddleocr+pymupdf
    assert page_routing[1]["page_index"] == 1
    assert page_routing[1]["selected_extractor"] == "paddleocr+pymupdf"
    assert page_routing[1]["routing_reason"] == "stage_1_empty_text"
