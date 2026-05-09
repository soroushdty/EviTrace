"""
tests/pdf_extractor/test_scan_detector_routing.py
===================================================
TDD tests for tasks 11.1 and 11.2 of the architecture-migration spec.

Task 11.1 — Replace waterfall cascade with scan-detector routing
    - Remove _compute_quality_score
    - When ocr=False: run pdfplumber only, skip scan detection
    - When ocr=True: per-page classify_page() routing
      * Native pages → pdfplumber (structural) + get_page_font_metadata (font)
      * Scanned pages → extract_with_paddleocr(dpi=config dpi)

Task 11.2 — Update PaddleOCR to return per-block PDF coordinate bboxes
    - Add dpi parameter to extract_with_paddleocr()
    - Convert pixel bboxes to PDF user-space via pdf_x = pixel_x * (72 / dpi)
    - Use make_ocr_block() factory
    - block_bbox is never None for PaddleOCR blocks after this change

Requirements: 3.1, 3.2, 3.3
Boundary: pdf_extractor/extraction/__init__.py, pdf_extractor/extraction/PaddleOCR.py
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch, call

import pytest

import pdf_extractor.extraction


# ---------------------------------------------------------------------------
# fitz (PyMuPDF) is not installed in CI; mock it at the sys.modules level
# so that `import fitz` inside production functions succeeds.
# ---------------------------------------------------------------------------

def _make_fitz_doc_with_pages(pages: list):
    """Build a mock fitz.Document that iterates over page mocks.

    Returns (mock_fitz_module, mock_doc).
    """
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter(pages))
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_doc.close = MagicMock()

    mock_fitz = MagicMock()
    mock_fitz.open = MagicMock(return_value=mock_doc)
    return mock_fitz, mock_doc


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_block(label: str, page_index: int = 0) -> dict:
    return {
        "text": f"{label} text",
        "page_index": page_index,
        "block_bbox": None,
        "spans": [],
    }


def _make_ocr_block_dict(label: str, page_index: int = 0) -> dict:
    return {
        "text": f"{label} ocr text",
        "page_index": page_index,
        "block_bbox": (10.0, 20.0, 100.0, 200.0),
        "spans": [],
        "rasterization_dpi": 150,
        "ocr_confidence": 0.95,
    }


_PLUMBER_BLOCKS = [_make_block("plumber", 0)]
_PADDLE_BLOCKS = [_make_ocr_block_dict("paddle", 0)]
_FONT_META = [{"size": 12.0, "text": "hello", "page": 0}]

_DEFAULT_CONFIG = {
    "quality_control": {
        "ocr": {
            "rasterization_dpi": 150,
        }
    }
}


def _make_native_classification(page_index: int = 0):
    from pdf_extractor.extraction.scan_detector import PageScanClassification
    return PageScanClassification(
        page_index=page_index,
        is_native=True,
        triggered_stages=[],
        stage_values={"word_count": 100.0, "alpha_ratio": 0.95, "font_count": 3.0, "image_coverage": 0.01},
    )


def _make_scanned_classification(page_index: int = 0):
    from pdf_extractor.extraction.scan_detector import PageScanClassification
    return PageScanClassification(
        page_index=page_index,
        is_native=False,
        triggered_stages=[1],
        stage_values={},
    )


# ---------------------------------------------------------------------------
# Task 11.1 — Tests for new orchestrator behavior
# ---------------------------------------------------------------------------

class TestOcrFalseRunsPdfplumberOnly:
    """11.1 / Req 3.3: When ocr=False, run pdfplumber only — no scan detection."""

    def test_ocr_false_returns_pdfplumber_blocks(self):
        """ocr=False must return pdfplumber blocks."""
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS) as mock_plumber,
            patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
        ):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=False,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        assert blocks == _PLUMBER_BLOCKS
        mock_plumber.assert_called_once_with("fake.pdf")
        mock_paddle.assert_not_called()

    def test_ocr_false_returns_empty_font_metadata(self):
        """ocr=False path returns empty font metadata list."""
        with patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=False,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        assert font_meta == []

    def test_ocr_false_does_not_call_scan_detection(self):
        """ocr=False must never call classify_page (scan detection skipped)."""
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.scan_detector.classify_page") as mock_classify,
        ):
            pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=False,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        mock_classify.assert_not_called()

    def test_ocr_false_calls_validate_blocks(self):
        """validate_blocks is called once with pdfplumber blocks when ocr=False."""
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_validate,
        ):
            pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=False,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        mock_validate.assert_called_once_with(_PLUMBER_BLOCKS)

    def test_ocr_false_does_not_open_fitz_doc(self):
        """When ocr=False, fitz.open must NOT be called (no scan detection)."""
        mock_fitz, mock_doc = _make_fitz_doc_with_pages([])
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch.dict(sys.modules, {"fitz": mock_fitz}),
        ):
            pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=False,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        mock_fitz.open.assert_not_called()


class TestOcrTrueWithAllNativePages:
    """11.1 / Req 3.1: When ocr=True and all pages are native, use pdfplumber + font metadata."""

    def test_native_pages_run_pdfplumber(self):
        """All native pages → pdfplumber must be called for structural blocks."""
        mock_page = MagicMock()
        mock_page.number = 0
        native_cls = _make_native_classification(0)
        font_meta_from_page = [{"size": 12.0, "text": "hello", "page": 0}]
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=native_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS) as mock_plumber,
            patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=font_meta_from_page),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        mock_plumber.assert_called()
        mock_paddle.assert_not_called()

    def test_native_pages_collect_font_metadata(self):
        """For native pages, get_page_font_metadata is called once per page."""
        mock_page = MagicMock()
        mock_page.number = 0
        native_cls = _make_native_classification(0)
        font_meta_from_page = [{"size": 12.0, "text": "hello", "page": 0}]
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=native_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr"),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=font_meta_from_page) as mock_font_meta,
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        mock_font_meta.assert_called_once_with(mock_page)
        assert font_meta == font_meta_from_page

    def test_native_pages_paddle_not_called(self):
        """PaddleOCR must not be called for all-native documents."""
        mock_page = MagicMock()
        mock_page.number = 0
        native_cls = _make_native_classification(0)
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=native_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=_FONT_META),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        mock_paddle.assert_not_called()


class TestOcrTrueWithAllScannedPages:
    """11.1 / Req 3.2: When ocr=True and pages are scanned, use PaddleOCR."""

    def test_scanned_pages_run_paddleocr(self):
        """Confirmed scanned pages must route to PaddleOCR."""
        mock_page = MagicMock()
        mock_page.number = 0
        scanned_cls = _make_scanned_classification(0)
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=scanned_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber") as mock_plumber,
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS) as mock_paddle,
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata"),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        mock_paddle.assert_called()

    def test_scanned_pages_paddleocr_receives_dpi_from_config(self):
        """PaddleOCR must be called with dpi=rasterization_dpi from config."""
        mock_page = MagicMock()
        mock_page.number = 0
        scanned_cls = _make_scanned_classification(0)
        config_with_dpi = {"quality_control": {"ocr": {"rasterization_dpi": 300}}}
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=scanned_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber"),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS) as mock_paddle,
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata"),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=config_with_dpi,
            )

        # extract_with_paddleocr must be called with dpi=300
        paddle_call_args = mock_paddle.call_args
        assert paddle_call_args is not None
        called_dpi = paddle_call_args.kwargs.get("dpi") or (
            paddle_call_args.args[1] if len(paddle_call_args.args) >= 2 else None
        )
        assert called_dpi == 300, f"Expected dpi=300, got {called_dpi}"

    def test_scanned_pages_font_metadata_not_called(self):
        """get_page_font_metadata is not called for scanned pages."""
        mock_page = MagicMock()
        mock_page.number = 0
        scanned_cls = _make_scanned_classification(0)
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=scanned_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber"),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata") as mock_font_meta,
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        mock_font_meta.assert_not_called()

    def test_scanned_pages_empty_font_metadata_returned(self):
        """Scanned path must return empty font_metadata list."""
        mock_page = MagicMock()
        mock_page.number = 0
        scanned_cls = _make_scanned_classification(0)
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=scanned_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber"),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata"),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        assert font_meta == []


class TestClassifyPageCalledPerPage:
    """11.1: classify_page is called once per page when ocr=True."""

    def test_classify_page_called_once_for_single_page(self):
        mock_page = MagicMock()
        mock_page.number = 0
        native_cls = _make_native_classification(0)
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=native_cls) as mock_classify,
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=[]),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=[]),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        assert mock_classify.call_count == 1

    def test_classify_page_called_for_each_of_three_pages(self):
        pages = [MagicMock() for _ in range(3)]
        for i, p in enumerate(pages):
            p.number = i
        native_cls = _make_native_classification(0)
        mock_fitz, _ = _make_fitz_doc_with_pages(pages)

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=native_cls) as mock_classify,
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=[]),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=[]),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=True,
                ocr_text_quality_threshold=0.5,
                config=_DEFAULT_CONFIG,
            )

        assert mock_classify.call_count == 3


class TestComputeQualityScoreRemoved:
    """11.1: _compute_quality_score function must NOT exist in the new implementation."""

    def test_compute_quality_score_absent(self):
        """After task 11.1, _compute_quality_score must not be a module attribute."""
        assert not hasattr(pdf_extractor.extraction, "_compute_quality_score"), (
            "_compute_quality_score must be removed; waterfall cascade is replaced by scan-detector routing"
        )


class TestSignatureBackwardCompatibility:
    """11.1: extract_pdf retains ocr_text_quality_threshold param for API compat."""

    def test_signature_accepts_ocr_text_quality_threshold(self):
        """extract_pdf must still accept ocr_text_quality_threshold without error."""
        with patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS):
            blocks, _ = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=False,
                ocr_text_quality_threshold=0.8,
                config=_DEFAULT_CONFIG,
            )

    def test_signature_accepts_embed_model(self):
        """extract_pdf must still accept embed_model kwarg (reserved, unused)."""
        with patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS):
            blocks, _ = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=False,
                ocr_text_quality_threshold=0.5,
                embed_model=None,
                config=_DEFAULT_CONFIG,
            )

    def test_config_none_defaults_gracefully(self):
        """When config=None, extract_pdf must not crash (uses empty config)."""
        with patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS):
            blocks, _ = pdf_extractor.extraction.extract_pdf(
                pdf_path="fake.pdf",
                ocr=False,
                ocr_text_quality_threshold=0.5,
                config=None,
            )
        assert blocks == _PLUMBER_BLOCKS


class TestValidateBlocksAlwaysCalled:
    """11.1: validate_blocks must be called exactly once before extract_pdf returns."""

    def test_validate_called_ocr_false(self):
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_validate,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_DEFAULT_CONFIG
            )

        assert mock_validate.call_count == 1

    def test_validate_called_ocr_true_native(self):
        mock_page = MagicMock()
        mock_page.number = 0
        native_cls = _make_native_classification(0)
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=native_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=[]),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=[]),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_validate,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_DEFAULT_CONFIG
            )

        assert mock_validate.call_count == 1

    def test_validate_called_ocr_true_scanned(self):
        mock_page = MagicMock()
        mock_page.number = 0
        scanned_cls = _make_scanned_classification(0)
        mock_fitz, _ = _make_fitz_doc_with_pages([mock_page])

        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=scanned_cls),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=[]),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=[]),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_validate,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_DEFAULT_CONFIG
            )

        assert mock_validate.call_count == 1


# ---------------------------------------------------------------------------
# Task 11.2 — Tests for PaddleOCR coordinate conversion
# ---------------------------------------------------------------------------

class TestExtractWithPaddleOCRSignature:
    """11.2: extract_with_paddleocr accepts a dpi parameter."""

    def test_dpi_parameter_accepted(self):
        """extract_with_paddleocr must accept dpi keyword argument."""
        from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr
        import inspect
        sig = inspect.signature(extract_with_paddleocr)
        assert "dpi" in sig.parameters, (
            "extract_with_paddleocr must have a 'dpi' parameter"
        )

    def test_dpi_default_is_150(self):
        """The default value for dpi must be 150."""
        from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr
        import inspect
        sig = inspect.signature(extract_with_paddleocr)
        dpi_param = sig.parameters["dpi"]
        assert dpi_param.default == 150, (
            f"Default dpi must be 150, got {dpi_param.default}"
        )


class TestExtractWithPaddleOCRCoordinateConversion:
    """11.2 / Req 3.2: PaddleOCR blocks carry PDF-space bboxes converted from pixel coords."""

    def _make_paddle_result(self, pixel_bbox, text="OCR text", confidence=0.95):
        """Build a minimal PaddleOCR result structure.

        PaddleOCR returns: list[list[list]]
        - outer: per-image (always 1 here)
        - inner: [bounding_box_4pts, (text, confidence)]
        where bounding_box_4pts is [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
        """
        x0, y0, x1, y1 = pixel_bbox
        bbox_pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        line_info = [bbox_pts, (text, confidence)]
        return [[line_info]]  # 1 image, 1 line

    def _run_paddleocr_with_mock(self, ocr_result, dpi=150):
        """Run extract_with_paddleocr with full external deps mocked."""
        import numpy as np
        from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr

        mock_engine = MagicMock()
        mock_engine.ocr.return_value = ocr_result

        mock_paddle_module = MagicMock()
        mock_paddle_module.PaddleOCR = MagicMock(return_value=mock_engine)

        pil_img = MagicMock()
        pil_img.close = MagicMock()

        dummy_array = np.zeros((10, 10, 3), dtype=np.uint8)

        with (
            patch.dict(sys.modules, {"paddleocr": mock_paddle_module, "paddlepaddle": MagicMock()}),
            patch("pdf2image.pdfinfo_from_path", return_value={"Pages": 1}),
            patch("pdf2image.convert_from_path", return_value=[pil_img]),
            patch("numpy.array", return_value=dummy_array),
        ):
            blocks = extract_with_paddleocr("fake.pdf", dpi=dpi)

        return blocks

    def test_block_bbox_is_tuple_of_floats(self):
        """block_bbox must be a 4-tuple of floats in PDF user-space."""
        pixel_bbox = (100, 200, 400, 600)
        ocr_result = self._make_paddle_result(pixel_bbox)
        blocks = self._run_paddleocr_with_mock(ocr_result, dpi=150)

        assert len(blocks) == 1
        bbox = blocks[0]["block_bbox"]
        assert bbox is not None, "block_bbox must not be None after task 11.2"
        assert isinstance(bbox, tuple), f"block_bbox must be a tuple, got {type(bbox)}"
        assert len(bbox) == 4, f"block_bbox must have 4 elements, got {len(bbox)}"
        for v in bbox:
            assert isinstance(v, float), f"Each bbox element must be float, got {type(v)}"

    def test_pixel_to_pdf_conversion_at_150dpi(self):
        """Pixel coords are converted: pdf_x = pixel_x * (72.0 / dpi)."""
        dpi = 150
        pixel_x0, pixel_y0, pixel_x1, pixel_y1 = 150, 300, 600, 900
        expected = (
            pixel_x0 * (72.0 / dpi),  # 72.0
            pixel_y0 * (72.0 / dpi),  # 144.0
            pixel_x1 * (72.0 / dpi),  # 288.0
            pixel_y1 * (72.0 / dpi),  # 432.0
        )

        ocr_result = self._make_paddle_result((pixel_x0, pixel_y0, pixel_x1, pixel_y1))
        blocks = self._run_paddleocr_with_mock(ocr_result, dpi=dpi)

        assert len(blocks) == 1
        bbox = blocks[0]["block_bbox"]
        assert bbox == pytest.approx(expected, abs=0.01), (
            f"Expected PDF-space bbox {expected}, got {bbox}"
        )

    def test_pixel_to_pdf_conversion_at_300dpi(self):
        """Conversion formula is correct at 300 DPI."""
        dpi = 300
        pixel_x0, pixel_y0, pixel_x1, pixel_y1 = 300, 600, 1200, 1800
        expected = (
            pixel_x0 * (72.0 / dpi),  # 72.0
            pixel_y0 * (72.0 / dpi),  # 144.0
            pixel_x1 * (72.0 / dpi),  # 288.0
            pixel_y1 * (72.0 / dpi),  # 432.0
        )

        ocr_result = self._make_paddle_result((pixel_x0, pixel_y0, pixel_x1, pixel_y1))
        blocks = self._run_paddleocr_with_mock(ocr_result, dpi=dpi)

        assert len(blocks) == 1
        bbox = blocks[0]["block_bbox"]
        assert bbox == pytest.approx(expected, abs=0.01)

    def test_block_is_paddle_ocr_block_dict(self):
        """Returned blocks must carry rasterization_dpi and ocr_confidence."""
        pixel_bbox = (100, 200, 400, 600)
        confidence = 0.92
        ocr_result = self._make_paddle_result(pixel_bbox, confidence=confidence)
        blocks = self._run_paddleocr_with_mock(ocr_result, dpi=150)

        assert len(blocks) == 1
        block = blocks[0]
        assert "rasterization_dpi" in block, "Block must carry rasterization_dpi"
        assert block["rasterization_dpi"] == 150
        assert "ocr_confidence" in block, "Block must carry ocr_confidence"
        assert block["ocr_confidence"] == pytest.approx(confidence, abs=0.001)

    def test_block_bbox_never_none(self):
        """After task 11.2, block_bbox must never be None for PaddleOCR blocks with detections."""
        pixel_bbox = (0, 0, 100, 100)
        ocr_result = self._make_paddle_result(pixel_bbox)
        blocks = self._run_paddleocr_with_mock(ocr_result, dpi=150)

        for block in blocks:
            assert block["block_bbox"] is not None, (
                "block_bbox must not be None for PaddleOCR blocks after task 11.2"
            )

    def test_make_ocr_block_factory_used(self):
        """extract_with_paddleocr must use make_ocr_block() factory."""
        import numpy as np
        from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr
        from pdf_extractor.extraction import schemas

        pixel_bbox = (10, 20, 100, 200)
        ocr_result = self._make_paddle_result(pixel_bbox)

        mock_engine = MagicMock()
        mock_engine.ocr.return_value = ocr_result
        mock_paddle_module = MagicMock()
        mock_paddle_module.PaddleOCR = MagicMock(return_value=mock_engine)

        pil_img = MagicMock()
        pil_img.close = MagicMock()
        dummy_array = np.zeros((10, 10, 3), dtype=np.uint8)

        with (
            patch.dict(sys.modules, {"paddleocr": mock_paddle_module, "paddlepaddle": MagicMock()}),
            patch("pdf2image.pdfinfo_from_path", return_value={"Pages": 1}),
            patch("pdf2image.convert_from_path", return_value=[pil_img]),
            patch("numpy.array", return_value=dummy_array),
            patch.object(schemas, "make_ocr_block", wraps=schemas.make_ocr_block) as mock_make_ocr_block,
        ):
            blocks = extract_with_paddleocr("fake.pdf", dpi=150)

        assert mock_make_ocr_block.call_count >= 1, (
            "make_ocr_block() must be called to build PaddleOCR result blocks"
        )


class TestExtractWithPaddleOCRBBoxParsing:
    """11.2: Correctly parse PaddleOCR 4-point bbox format to (x0,y0,x1,y1)."""

    def _run_paddleocr_with_mock(self, ocr_result, dpi=72):
        import numpy as np
        from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr

        mock_engine = MagicMock()
        mock_engine.ocr.return_value = ocr_result
        mock_paddle_module = MagicMock()
        mock_paddle_module.PaddleOCR = MagicMock(return_value=mock_engine)

        pil_img = MagicMock()
        pil_img.close = MagicMock()
        dummy_array = np.zeros((10, 10, 3), dtype=np.uint8)

        with (
            patch.dict(sys.modules, {"paddleocr": mock_paddle_module, "paddlepaddle": MagicMock()}),
            patch("pdf2image.pdfinfo_from_path", return_value={"Pages": 1}),
            patch("pdf2image.convert_from_path", return_value=[pil_img]),
            patch("numpy.array", return_value=dummy_array),
        ):
            blocks = extract_with_paddleocr("fake.pdf", dpi=dpi)

        return blocks

    def test_4pt_bbox_parsed_to_x0y0x1y1(self):
        """PaddleOCR returns 4 corner points; we must extract min/max to get (x0,y0,x1,y1).

        At dpi=72, pdf_x = pixel_x * (72/72) = pixel_x, so coords are unchanged.
        """
        dpi = 72
        # 4 corner points: TL, TR, BR, BL
        pts = [[50, 100], [200, 100], [200, 300], [50, 300]]
        line_info = [pts, ("text", 0.9)]
        ocr_result = [[line_info]]

        blocks = self._run_paddleocr_with_mock(ocr_result, dpi=dpi)

        assert len(blocks) == 1
        bbox = blocks[0]["block_bbox"]
        # x0=min(50,200,200,50)=50, y0=min(100,100,300,300)=100
        # x1=max(50,200,200,50)=200, y1=max(100,100,300,300)=300
        assert bbox == pytest.approx((50.0, 100.0, 200.0, 300.0), abs=0.01), (
            f"Expected (50.0, 100.0, 200.0, 300.0), got {bbox}"
        )
