"""
tests/test_steering_drift_preservation.py
------------------------------------------
Preservation property tests for the codebase-steering-drift bugfix spec.

These tests encode the EXISTING runtime behaviour on non-buggy inputs and
MUST PASS on the current (unfixed) code.  They establish the regression
baseline that must continue to hold after every fix is applied.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9,
             3.10, 3.11**
"""

from __future__ import annotations

import os
import textwrap
from unittest.mock import patch

import pytest
import yaml
from hypothesis import assume, given, settings
from hypothesis import strategies as st

import pdf_extractor.extraction
from pdf_extractor.extraction import schemas
from pdf_extractor.extraction.quality_control.artifact_generator import build_canonical_artifacts


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_valid_blocks(label: str = "test", count: int = 1) -> list:
    """Return a minimal list of valid BlockDict-shaped dicts."""
    return [
        {
            "text": f"{label} block {i}",
            "page_index": i,
            "block_bbox": None,
            "spans": [],
        }
        for i in range(count)
    ]


_PYMUPDF_BLOCKS = _make_valid_blocks("pymupdf")
_FONT_META = [{"size": 12.0, "text": "hello", "page": 0}]

_FIXED_GROBID_XML = "<root><body>test content</body></root>"


# ---------------------------------------------------------------------------
# Test 1 — PyMuPDF-sufficient cascade (unit test)
# Validates: Requirement 3.1
# ---------------------------------------------------------------------------

def test_pymupdf_sufficient_cascade_no_fallback():
    """When PyMuPDF quality score >= threshold, extract_pdf() returns PyMuPDF
    blocks and non-empty font_metadata without calling any fallback backend.

    Preservation: this behaviour must survive all structural fixes.
    """
    with (
        patch("pdf_extractor.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)) as mock_pymupdf,
        patch("pdf_extractor.extraction._compute_quality_score", return_value=0.9),
        patch("pdf_extractor.extraction.extract_with_tesseract") as mock_tess,
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
    ):
        blocks, font_metadata = pdf_extractor.extraction.extract_pdf(
            pdf_path="dummy.pdf",
            ocr=True,
            ocr_text_quality_threshold=0.7,
        )

    assert blocks == _PYMUPDF_BLOCKS, (
        f"Expected PyMuPDF blocks, got {blocks!r}"
    )
    assert font_metadata, "font_metadata must be non-empty when PyMuPDF path is taken"
    assert font_metadata == _FONT_META

    mock_tess.assert_not_called()
    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — ocr=False cascade (unit test)
# Validates: Requirement 3.2
# ---------------------------------------------------------------------------

def test_ocr_false_returns_only_pymupdf_blocks():
    """When ocr=False, extract_pdf() returns only PyMuPDF blocks regardless
    of quality score (even a score of 0.0 must not trigger fallback).

    Preservation: this behaviour must survive all structural fixes.
    """
    with (
        patch("pdf_extractor.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("pdf_extractor.extraction._compute_quality_score", return_value=0.0),
        patch("pdf_extractor.extraction.extract_with_tesseract") as mock_tess,
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
    ):
        blocks, font_metadata = pdf_extractor.extraction.extract_pdf(
            pdf_path="dummy.pdf",
            ocr=False,
            ocr_text_quality_threshold=0.7,
        )

    assert blocks == _PYMUPDF_BLOCKS, (
        f"Expected PyMuPDF blocks with ocr=False, got {blocks!r}"
    )

    mock_tess.assert_not_called()
    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — validate_blocks on valid input (unit test)
# Validates: Requirement 3.3
# ---------------------------------------------------------------------------

def test_validate_blocks_valid_input_does_not_raise():
    """schemas.validate_blocks() on a valid BlockDict list must not raise.

    Preservation: this behaviour must survive all structural fixes.
    """
    valid_blocks = [
        {
            "text": "Hello world",
            "page_index": 0,
            "block_bbox": (0.0, 0.0, 100.0, 20.0),
            "spans": [],
        },
        {
            "text": "Second block",
            "page_index": 1,
            "block_bbox": None,
            "spans": [],
        },
    ]

    # Must not raise
    schemas.validate_blocks(valid_blocks)


# ---------------------------------------------------------------------------
# Test 4 — validate_blocks on invalid input (unit test)
# Validates: Requirement 3.4
# ---------------------------------------------------------------------------

def test_validate_blocks_missing_key_raises_value_error():
    """schemas.validate_blocks() on a block missing 'text' raises ValueError."""
    invalid_blocks = [
        {
            # 'text' key is intentionally absent
            "page_index": 0,
            "block_bbox": None,
            "spans": [],
        }
    ]
    with pytest.raises(ValueError):
        schemas.validate_blocks(invalid_blocks)


def test_validate_blocks_bool_page_index_raises_value_error():
    """schemas.validate_blocks() on a block where page_index is bool raises ValueError.

    bool is a subclass of int in Python, but is not a valid page index.
    """
    invalid_blocks = [
        {
            "text": "some text",
            "page_index": True,  # bool, not int
            "block_bbox": None,
            "spans": [],
        }
    ]
    with pytest.raises(ValueError):
        schemas.validate_blocks(invalid_blocks)


# ---------------------------------------------------------------------------
# Test 5 — load_config with valid YAML (unit test)
# Validates: Requirement 3.5
# ---------------------------------------------------------------------------

def test_load_config_valid_yaml_returns_merged_dict(tmp_path):
    """load_config() with a valid YAML returns a merged dict with all defaults
    applied and pdfs_path resolved to an absolute path.

    Preservation: this behaviour must survive all structural fixes.
    """
    from pdf_extractor.utils.config_utils import load_config

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            pdfs_path: data/pdfs
        """),
        encoding="utf-8",
    )

    result = load_config(str(cfg_file))

    assert isinstance(result, dict), "load_config must return a dict"
    assert os.path.isabs(result["pdfs_path"]), (
        f"pdfs_path must be absolute, got {result['pdfs_path']!r}"
    )
    assert "log_file" in result, "'log_file' default must be present in merged config"
    assert "ocr" in result, "'ocr' default must be present in merged config"


# ---------------------------------------------------------------------------
# Test 6 — load_config with unknown keys raises ValueError (unit test)
# Validates: Requirement 3.6
# ---------------------------------------------------------------------------

def test_load_config_unknown_keys_raises_value_error(tmp_path):
    """load_config() with unknown top-level keys raises ValueError.

    Preservation: this behaviour must survive all structural fixes.
    """
    from pdf_extractor.utils.config_utils import load_config

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            pdfs_path: data/pdfs
            unknown_key: value
        """),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_config(str(cfg_file))


# ---------------------------------------------------------------------------
# Test 7 — build_canonical_artifacts determinism (unit test)
# Validates: Requirement 3.10
# ---------------------------------------------------------------------------

def test_build_canonical_artifacts_deterministic():
    """build_canonical_artifacts() called twice with the same inputs returns
    identical SHA-256 IDs both times.

    Preservation: this behaviour must survive all structural fixes.
    """
    grobid_xml = _FIXED_GROBID_XML
    pymupdf_data = {"blocks": [{"text": "Hello", "page": 0}], "doc_id": "test123"}
    doc_id = "test_doc_001"

    result1 = build_canonical_artifacts(grobid_xml, pymupdf_data, doc_id)
    result2 = build_canonical_artifacts(grobid_xml, pymupdf_data, doc_id)

    assert result1["grobid"]["id"] == result2["grobid"]["id"], (
        "GROBID SHA-256 ID must be deterministic across two calls with the same input"
    )
    assert result1["pymupdf"]["id"] == result2["pymupdf"]["id"], (
        "PyMuPDF SHA-256 ID must be deterministic across two calls with the same input"
    )


# ---------------------------------------------------------------------------
# Test 8 — PBT: PyMuPDF-sufficient cascade for any score >= threshold
# Validates: Requirement 3.1
# ---------------------------------------------------------------------------

@given(
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=100)
def test_pbt_pymupdf_sufficient_no_fallback_for_any_score_above_threshold(score: float):
    """**Validates: Requirements 3.1**

    For any quality score in [threshold, 1.0], extract_pdf() with mocked
    PyMuPDF returning that score does NOT call extract_with_tesseract or
    extract_with_paddleocr.

    Preservation property: must hold on unfixed code and after every fix.
    """
    threshold = 0.7
    assume(score >= threshold)

    with (
        patch("pdf_extractor.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("pdf_extractor.extraction._compute_quality_score", return_value=score),
        patch("pdf_extractor.extraction.extract_with_tesseract") as mock_tess,
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
    ):
        blocks, font_metadata = pdf_extractor.extraction.extract_pdf(
            pdf_path="dummy.pdf",
            ocr=True,
            ocr_text_quality_threshold=threshold,
        )

    mock_tess.assert_not_called()
    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9 — PBT: ocr=False never calls fallback for any score
# Validates: Requirement 3.2
# ---------------------------------------------------------------------------

@given(
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=100)
def test_pbt_ocr_false_never_calls_fallback_for_any_score(score: float):
    """**Validates: Requirements 3.2**

    For any quality score in [0.0, 1.0], extract_pdf() with ocr=False does
    NOT call any fallback backend (tesseract or paddleocr).

    Preservation property: must hold on unfixed code and after every fix.
    """
    with (
        patch("pdf_extractor.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("pdf_extractor.extraction._compute_quality_score", return_value=score),
        patch("pdf_extractor.extraction.extract_with_tesseract") as mock_tess,
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
    ):
        blocks, font_metadata = pdf_extractor.extraction.extract_pdf(
            pdf_path="dummy.pdf",
            ocr=False,
            ocr_text_quality_threshold=0.7,
        )

    mock_tess.assert_not_called()
    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10 — PBT: build_canonical_artifacts determinism for any valid inputs
# Validates: Requirement 3.10
# ---------------------------------------------------------------------------

@given(
    doc_id=st.text(min_size=1),
    pymupdf_data=st.dictionaries(st.text(), st.text()),
)
@settings(max_examples=100)
def test_pbt_build_canonical_artifacts_deterministic_for_any_inputs(
    doc_id: str, pymupdf_data: dict
):
    """**Validates: Requirements 3.10**

    For any valid doc_id and PyMuPDF JSON dict, build_canonical_artifacts is
    deterministic — calling it twice with the same inputs returns identical
    SHA-256 IDs on both calls.

    Preservation property: must hold on unfixed code and after every fix.
    """
    grobid_xml = _FIXED_GROBID_XML

    result1 = build_canonical_artifacts(grobid_xml, pymupdf_data, doc_id)
    result2 = build_canonical_artifacts(grobid_xml, pymupdf_data, doc_id)

    assert result1["grobid"]["id"] == result2["grobid"]["id"], (
        f"GROBID SHA-256 ID is not deterministic for doc_id={doc_id!r}"
    )
    assert result1["pymupdf"]["id"] == result2["pymupdf"]["id"], (
        f"PyMuPDF SHA-256 ID is not deterministic for doc_id={doc_id!r}, "
        f"pymupdf_data={pymupdf_data!r}"
    )
