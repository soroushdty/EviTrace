"""Unit tests for per-page extraction routing in build_qc_bundle.

Verifies that:
- Mixed PDFs route native pages through GROBID+pdfplumber and scanned pages
  through PaddleOCR+PyMuPDF.
- All-native PDFs do NOT invoke OCR extractors.
- All-scanned PDFs with ocr=false log WARNING and produce no extraction branch.
- PageRoutingResult metadata is attached to QCBundle via ctx.unified.content["page_routing"].
- Merged results preserve original page order.

Requirements: 3.1, 3.2, 3.3, 3.4
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
    """Minimal QC config for routing tests."""
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
# Test: Mixed PDF routes native pages to GROBID+pdfplumber, scanned to OCR
# ---------------------------------------------------------------------------


def test_mixed_pdf_routes_native_and_scanned_pages():
    """For a 3-page PDF (native, scanned, native), native pages use
    GROBID+pdfplumber and scanned page uses PaddleOCR+PyMuPDF.

    Requirements: 3.1
    """
    qc_config = _make_qc_config(ocr=True)

    # Page classifications: page 0 native, page 1 scanned, page 2 native
    classifications = [
        _make_classification(0, is_native=True),
        _make_classification(1, is_native=False, triggered_stages=[1]),
        _make_classification(2, is_native=True),
    ]

    # pdfplumber returns blocks for all pages (it processes full doc)
    plumber_blocks = [_make_block(0, "native page 0"), _make_block(1, "page 1"), _make_block(2, "native page 2")]
    # PaddleOCR returns blocks for all pages (it processes full doc)
    paddle_blocks = [_make_block(0, "ocr page 0"), _make_block(1, "ocr page 1"), _make_block(2, "ocr page 2")]
    # PyMuPDF returns blocks for all pages
    pymupdf_blocks = [_make_block(0, "pymupdf 0"), _make_block(1, "pymupdf 1"), _make_block(2, "pymupdf 2")]

    mock_paddleocr = MagicMock(return_value=paddle_blocks)
    mock_pymupdf = MagicMock(return_value=(pymupdf_blocks, []))
    mock_pdfplumber = MagicMock(return_value=plumber_blocks)
    mock_grobid = MagicMock(return_value=("<TEI>mock</TEI>", []))

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
            pdf_path=Path("/fake/test.pdf"),
            pdf_name="test_paper",
            qc_config=qc_config,
        )

    # Both native and OCR extractors should have been invoked
    mock_grobid.assert_called_once()
    mock_pdfplumber.assert_called_once()
    mock_paddleocr.assert_called_once()
    mock_pymupdf.assert_called_once()

    # Verify page_routing metadata was attached
    page_routing = mock_qc_bundle.unified.content["page_routing"]
    assert len(page_routing) == 3

    # Page 0: native
    assert page_routing[0]["page_index"] == 0
    assert page_routing[0]["selected_extractor"] == "grobid+pdfplumber"
    assert page_routing[0]["routing_reason"] == "mixed_native_page"

    # Page 1: scanned
    assert page_routing[1]["page_index"] == 1
    assert page_routing[1]["selected_extractor"] == "paddleocr+pymupdf"
    assert page_routing[1]["routing_reason"] == "stage_1_empty_text"

    # Page 2: native
    assert page_routing[2]["page_index"] == 2
    assert page_routing[2]["selected_extractor"] == "grobid+pdfplumber"
    assert page_routing[2]["routing_reason"] == "mixed_native_page"


# ---------------------------------------------------------------------------
# Test: All-native PDF does NOT invoke OCR extractors
# ---------------------------------------------------------------------------


def test_all_native_pdf_does_not_invoke_ocr():
    """When all pages are native, OCR extractors SHALL NOT be invoked.

    Requirements: 3.3
    """
    qc_config = _make_qc_config(ocr=True)

    classifications = [
        _make_classification(0, is_native=True),
        _make_classification(1, is_native=True),
    ]

    plumber_blocks = [_make_block(0), _make_block(1)]

    mock_paddleocr = MagicMock(return_value=[])
    mock_pymupdf = MagicMock(return_value=([], []))
    mock_pdfplumber = MagicMock(return_value=plumber_blocks)
    mock_grobid = MagicMock(return_value=("<TEI>mock</TEI>", []))

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

        build_qc_bundle(
            pdf_path=Path("/fake/test.pdf"),
            pdf_name="test_paper",
            qc_config=qc_config,
        )

    # OCR extractors should NOT have been called
    mock_paddleocr.assert_not_called()
    mock_pymupdf.assert_not_called()

    # Native extractors should have been called
    mock_grobid.assert_called_once()
    mock_pdfplumber.assert_called_once()

    # Verify routing metadata
    page_routing = mock_qc_bundle.unified.content["page_routing"]
    assert len(page_routing) == 2
    for entry in page_routing:
        assert entry["selected_extractor"] == "grobid+pdfplumber"
        assert entry["routing_reason"] == "all_native"


# ---------------------------------------------------------------------------
# Test: All-scanned + ocr=false logs WARNING and produces no branch
# ---------------------------------------------------------------------------


def test_all_scanned_ocr_false_logs_warning(caplog):
    """When all pages are scanned and ocr=false, pipeline logs WARNING per page
    and produces no extraction branch.

    Requirements: 3.4
    """
    import logging

    qc_config = _make_qc_config(ocr=False)

    classifications = [
        _make_classification(0, is_native=False, triggered_stages=[1]),
        _make_classification(1, is_native=False, triggered_stages=[2, 5]),
    ]

    mock_paddleocr = MagicMock(return_value=[])
    mock_pymupdf = MagicMock(return_value=([], []))
    mock_pdfplumber = MagicMock(return_value=[_make_block(0), _make_block(1)])
    mock_grobid = MagicMock(return_value=("<TEI>mock</TEI>", []))

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

        with caplog.at_level(logging.WARNING, logger="pdf_extractor"):
            build_qc_bundle(
                pdf_path=Path("/fake/test.pdf"),
                pdf_name="test_paper",
                qc_config=qc_config,
            )

    # OCR extractors should NOT have been called (ocr=false)
    mock_paddleocr.assert_not_called()
    mock_pymupdf.assert_not_called()

    # Should have logged warnings for scanned pages
    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Skipping scanned page 0" in msg for msg in warning_messages)
    assert any("Skipping scanned page 1" in msg for msg in warning_messages)


# ---------------------------------------------------------------------------
# Test: Routing metadata completeness
# ---------------------------------------------------------------------------


def test_routing_metadata_completeness():
    """Every page in routing metadata has page_index, selected_extractor,
    and routing_reason (non-empty).

    Requirements: 3.2
    """
    qc_config = _make_qc_config(ocr=True)

    # Mixed: page 0 native, page 1 scanned (stage 2+3), page 2 native
    classifications = [
        _make_classification(0, is_native=True),
        _make_classification(1, is_native=False, triggered_stages=[2, 3]),
        _make_classification(2, is_native=True),
    ]

    plumber_blocks = [_make_block(0), _make_block(1), _make_block(2)]
    paddle_blocks = [_make_block(0), _make_block(1), _make_block(2)]
    pymupdf_blocks = [_make_block(0), _make_block(1), _make_block(2)]

    mock_tp, mock_qc_bundle = _setup_common_mocks()
    mock_fitz, _ = _mock_fitz_for_pages(3)

    with patch("pipeline.extraction_pipeline.extract_with_paddleocr", MagicMock(return_value=paddle_blocks)), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", MagicMock(return_value=(pymupdf_blocks, []))), \
         patch("pipeline.extraction_pipeline.extract_with_pdfplumber", MagicMock(return_value=plumber_blocks)), \
         patch("pipeline.extraction_pipeline.extract_with_grobid", MagicMock(return_value=("<TEI/>", []))), \
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

        build_qc_bundle(
            pdf_path=Path("/fake/test.pdf"),
            pdf_name="test_paper",
            qc_config=qc_config,
        )

    page_routing = mock_qc_bundle.unified.content["page_routing"]
    assert len(page_routing) == 3

    for entry in page_routing:
        assert isinstance(entry["page_index"], int)
        assert entry["selected_extractor"] in ("grobid+pdfplumber", "paddleocr+pymupdf")
        assert entry["routing_reason"]  # non-empty string
        # fallback_extractor can be None or a string
        assert entry["fallback_extractor"] is None or isinstance(entry["fallback_extractor"], str)

    # Verify scanned page has stage-based routing reason
    scanned_entry = page_routing[1]
    assert scanned_entry["routing_reason"] == "stages_2_3"


# ---------------------------------------------------------------------------
# Test: Merged results preserve original page order
# ---------------------------------------------------------------------------


def test_merged_results_preserve_page_order():
    """Merged page-level results are in original page order.

    Requirements: 3.1
    """
    qc_config = _make_qc_config(ocr=True)

    # 4 pages: native, scanned, scanned, native
    classifications = [
        _make_classification(0, is_native=True),
        _make_classification(1, is_native=False, triggered_stages=[1]),
        _make_classification(2, is_native=False, triggered_stages=[1]),
        _make_classification(3, is_native=True),
    ]

    plumber_blocks = [_make_block(i, f"plumber page {i}") for i in range(4)]
    paddle_blocks = [_make_block(i, f"paddle page {i}") for i in range(4)]
    pymupdf_blocks = [_make_block(i, f"pymupdf page {i}") for i in range(4)]

    mock_tp, mock_qc_bundle = _setup_common_mocks()
    mock_fitz, _ = _mock_fitz_for_pages(4)

    # Capture the branches passed to run_quality_control
    captured_branches = []

    def mock_run_qc(branches, *args, **kwargs):
        captured_branches.extend(branches)
        return mock_qc_bundle

    with patch("pipeline.extraction_pipeline.extract_with_paddleocr", MagicMock(return_value=paddle_blocks)), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", MagicMock(return_value=(pymupdf_blocks, []))), \
         patch("pipeline.extraction_pipeline.extract_with_pdfplumber", MagicMock(return_value=plumber_blocks)), \
         patch("pipeline.extraction_pipeline.extract_with_grobid", MagicMock(return_value=("<TEI/>", []))), \
         patch("pipeline.extraction_pipeline.scan_detector") as mock_scan_mod, \
         patch("pipeline.extraction_pipeline.run_quality_control", side_effect=mock_run_qc), \
         patch("pipeline.extraction_pipeline._get_text_processor", return_value=mock_tp), \
         patch("pipeline.extraction_pipeline._get_lexical_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_semantic_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline.w3c_project", return_value=[]), \
         patch("pipeline.extraction_pipeline.generate_w3c_jsonld", return_value={}), \
         patch.dict(sys.modules, {"fitz": mock_fitz}):

        mock_scan_mod.classify_page.side_effect = classifications

        from pipeline.extraction_pipeline import build_qc_bundle

        build_qc_bundle(
            pdf_path=Path("/fake/test.pdf"),
            pdf_name="test_paper",
            qc_config=qc_config,
        )

    # Find the structural branch (the one with a list payload)
    structural_branch = None
    for b in captured_branches:
        if isinstance(b.payload, list):
            structural_branch = b
            break

    assert structural_branch is not None, "Expected a structural branch with list payload"

    # Verify blocks are in page order
    page_indices = [block["page_index"] for block in structural_branch.payload]
    assert page_indices == sorted(page_indices), (
        f"Blocks not in page order: {page_indices}"
    )

    # Native pages (0, 3) should have pdfplumber content
    native_page_blocks = [b for b in structural_branch.payload if b["page_index"] in (0, 3)]
    for b in native_page_blocks:
        assert "plumber" in b["text"], f"Native page block should come from pdfplumber: {b}"

    # Scanned pages (1, 2) should have PaddleOCR content
    scanned_page_blocks = [b for b in structural_branch.payload if b["page_index"] in (1, 2)]
    for b in scanned_page_blocks:
        assert "paddle" in b["text"], f"Scanned page block should come from PaddleOCR: {b}"
