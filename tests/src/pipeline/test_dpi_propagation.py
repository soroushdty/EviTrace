"""Unit tests for DPI wiring through build_qc_bundle and deprecation warning in load_qc_config.

Requirements: 2.4
"""
from __future__ import annotations

import sys
import textwrap
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test 1: Non-default DPI value is passed through to OCR extractor
# ---------------------------------------------------------------------------


def test_dpi_value_passed_to_paddleocr():
    """build_qc_bundle passes configured rasterization_dpi to extract_with_paddleocr."""
    # Arrange: config with non-default DPI
    dpi_value = 300
    qc_config = {
        "ocr": True,
        "quality_control": {
            "ocr": {"rasterization_dpi": dpi_value},
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

    # Mock scan_detector to classify all pages as scanned (triggers OCR path)
    mock_classification = MagicMock()
    mock_classification.is_native = False
    mock_classification.page_index = 0
    mock_classification.triggered_stages = ["low_text_density"]
    mock_classification.stage_values = {}

    # Mock extract_with_paddleocr to capture the dpi argument
    mock_paddleocr = MagicMock(return_value=[])

    # Mock other heavy dependencies
    mock_pymupdf_result = ([], None)
    mock_extract_pymupdf = MagicMock(return_value=mock_pymupdf_result)

    # Mock fitz (PyMuPDF) for scan_detector
    mock_page = MagicMock()
    mock_fitz_doc = MagicMock()
    mock_fitz_doc.__enter__ = MagicMock(return_value=mock_fitz_doc)
    mock_fitz_doc.__exit__ = MagicMock(return_value=False)
    mock_fitz_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
    mock_fitz_doc.close = MagicMock()

    mock_fitz = MagicMock()
    mock_fitz.open = MagicMock(return_value=mock_fitz_doc)

    # Mock QC pipeline
    mock_qc_bundle = MagicMock()
    mock_qc_bundle.unified = None

    # Mock text processor
    mock_tp = MagicMock()

    with patch("pipeline.extraction_pipeline.extract_with_paddleocr", mock_paddleocr), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", mock_extract_pymupdf), \
         patch("pipeline.extraction_pipeline.scan_detector") as mock_scan_mod, \
         patch("pipeline.extraction_pipeline.run_quality_control", return_value=mock_qc_bundle), \
         patch("pipeline.extraction_pipeline._get_text_processor", return_value=mock_tp), \
         patch("pipeline.extraction_pipeline._get_lexical_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_semantic_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline.w3c_project", return_value=[]), \
         patch("pipeline.extraction_pipeline.generate_w3c_jsonld", return_value={}), \
         patch.dict(sys.modules, {"fitz": mock_fitz}):

        mock_scan_mod.classify_page.return_value = mock_classification

        from pipeline.extraction_pipeline import build_qc_bundle

        build_qc_bundle(
            pdf_path=Path("/fake/test.pdf"),
            pdf_name="test_paper",
            qc_config=qc_config,
        )

    # Assert: extract_with_paddleocr was called with dpi=300
    mock_paddleocr.assert_called_once()
    call_kwargs = mock_paddleocr.call_args
    # Check positional or keyword dpi argument
    if call_kwargs.kwargs.get("dpi") is not None:
        assert call_kwargs.kwargs["dpi"] == dpi_value, (
            f"Expected dpi={dpi_value}, got dpi={call_kwargs.kwargs['dpi']}"
        )
    else:
        # dpi might be passed as second positional arg
        assert len(call_kwargs.args) >= 2 and call_kwargs.args[1] == dpi_value, (
            f"Expected dpi={dpi_value} as positional arg, got args={call_kwargs.args}"
        )


# ---------------------------------------------------------------------------
# Test 2: Deprecation warning emitted for legacy ocr_dpi key
# ---------------------------------------------------------------------------


def test_deprecation_warning_for_legacy_ocr_dpi(tmp_path):
    """load_qc_config emits DeprecationWarning when legacy ocr_dpi key exists alongside quality_control.ocr."""
    # Create a config file with both legacy ocr_dpi and modern quality_control.ocr
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            ocr_dpi: 200
            quality_control:
              ocr:
                rasterization_dpi: 300
        """),
        encoding="utf-8",
    )

    from utils.config_utils import load_qc_config

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = load_qc_config(str(cfg_file))

    # Assert a DeprecationWarning was emitted
    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) >= 1, (
        f"Expected DeprecationWarning for legacy 'ocr_dpi' key, got: {[w.category.__name__ for w in caught]}"
    )

    # Assert the warning message mentions the legacy key and the modern path
    msg = str(deprecation_warnings[0].message)
    assert "ocr_dpi" in msg, f"Warning message should mention 'ocr_dpi', got: {msg}"
    assert "quality_control.ocr.rasterization_dpi" in msg, (
        f"Warning message should mention the modern config path, got: {msg}"
    )

    # Assert the modern value takes precedence
    assert result["quality_control"]["ocr"]["rasterization_dpi"] == 300


def test_no_deprecation_warning_without_legacy_key(tmp_path):
    """load_qc_config does NOT emit DeprecationWarning when ocr_dpi is absent."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            quality_control:
              ocr:
                rasterization_dpi: 300
        """),
        encoding="utf-8",
    )

    from utils.config_utils import load_qc_config

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_qc_config(str(cfg_file))

    deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecation_warnings) == 0, (
        f"No DeprecationWarning expected without legacy key, got: {deprecation_warnings}"
    )
