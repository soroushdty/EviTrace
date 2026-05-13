"""
tests/pdf_extractor/test_scan_detector_routing.py
===================================================
Tests for the scan-detector routing architecture.

NOTE: The standalone ``extract_pdf()`` orchestrator function was removed from
``pdf_extractor/extraction/__init__.py`` as part of the extraction-routing-
alignment bugfix (task 3.3).  The routing logic now lives in
``pipeline/orchestrator._build_qc_context()``, which is tested by:

  - tests/steering/test_steering_drift_extraction_routing_alignment_bug_condition.py
  - tests/steering/test_steering_drift_extraction_routing_alignment_preservation.py
  - tests/pipeline/test_orchestrator_concurrency.py

This file retains tests for:
  - Task 11.1: _compute_quality_score removal (TestComputeQualityScoreRemoved)
  - Task 11.2: PaddleOCR coordinate conversion (TestExtractWithPaddleOCR*)

Requirements: 3.1, 3.2, 3.3
Boundary: pdf_extractor/extraction/__init__.py, pdf_extractor/extraction/PaddleOCR.py
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch, call

import pytest

import pdf_extractor.extraction


# ---------------------------------------------------------------------------
# scispaCy/spaCy autouse mock — prevents spacy.load('en_core_sci_sm') in CI
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
# Architecture verification
# ---------------------------------------------------------------------------

class TestExtractPdfRemoved:
    """extract_pdf() must NOT exist in pdf_extractor.extraction after task 3.3."""

    def test_extract_pdf_absent(self):
        """extract_pdf must be removed; routing is now in _build_qc_context."""
        assert not hasattr(pdf_extractor.extraction, "extract_pdf"), (
            "extract_pdf() must be removed from pdf_extractor.extraction. "
            "Routing is now handled by pipeline/orchestrator._build_qc_context()."
        )


class TestComputeQualityScoreRemoved:
    """11.1: _compute_quality_score function must NOT exist in the new implementation."""

    def test_compute_quality_score_absent(self):
        """After task 11.1, _compute_quality_score must not be a module attribute."""
        assert not hasattr(pdf_extractor.extraction, "_compute_quality_score"), (
            "_compute_quality_score must be removed; waterfall cascade is replaced by scan-detector routing"
        )


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

        mock_pdf2image = MagicMock()
        mock_pdf2image.pdfinfo_from_path.return_value = {"Pages": 1}
        mock_pdf2image.convert_from_path.return_value = [pil_img]

        with (
            patch.dict(sys.modules, {
                "paddleocr": mock_paddle_module,
                "paddlepaddle": MagicMock(),
                "pdf2image": mock_pdf2image,
            }),
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

        mock_pdf2image = MagicMock()
        mock_pdf2image.pdfinfo_from_path.return_value = {"Pages": 1}
        mock_pdf2image.convert_from_path.return_value = [pil_img]

        with (
            patch.dict(sys.modules, {
                "paddleocr": mock_paddle_module,
                "paddlepaddle": MagicMock(),
                "pdf2image": mock_pdf2image,
            }),
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

        mock_pdf2image = MagicMock()
        mock_pdf2image.pdfinfo_from_path.return_value = {"Pages": 1}
        mock_pdf2image.convert_from_path.return_value = [pil_img]

        with (
            patch.dict(sys.modules, {
                "paddleocr": mock_paddle_module,
                "paddlepaddle": MagicMock(),
                "pdf2image": mock_pdf2image,
            }),
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
