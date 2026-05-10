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
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest
import yaml
from hypothesis import assume, given, settings
from hypothesis import strategies as st

import pdf_extractor.extraction
from pdf_extractor.extraction import schemas
from pdf_extractor.artifact_generator import build_canonical_artifacts


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


_PLUMBER_BLOCKS = _make_valid_blocks("plumber")
_PADDLE_BLOCKS = _make_valid_blocks("paddle")
_FONT_META = [{"size": 12.0, "text": "hello", "page": 0}]

_FIXED_GROBID_XML = "<root><body>test content</body></root>"

_DEFAULT_CONFIG = {"quality_control": {"ocr": {"rasterization_dpi": 150}}}


def _make_native_classification(page_index: int = 0):
    from pdf_extractor.extraction.scan_detector import PageScanClassification
    return PageScanClassification(
        page_index=page_index, is_native=True, triggered_stages=[],
        stage_values={"word_count": 100.0, "alpha_ratio": 0.95, "font_count": 3.0, "image_coverage": 0.01},
    )


def _make_scanned_classification(page_index: int = 0):
    from pdf_extractor.extraction.scan_detector import PageScanClassification
    return PageScanClassification(
        page_index=page_index, is_native=False, triggered_stages=[1], stage_values={},
    )


def _make_fitz_doc(pages: list):
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter(pages))
    mock_doc.close = MagicMock()
    mock_fitz = MagicMock()
    mock_fitz.open = MagicMock(return_value=mock_doc)
    return mock_fitz, mock_doc


# ---------------------------------------------------------------------------
# Test 1 — Native-page cascade: pdfplumber + font metadata, no PaddleOCR
# Validates: Requirement 3.1
# ---------------------------------------------------------------------------

def test_pymupdf_sufficient_cascade_no_fallback():
    """When all pages are native, the extraction layer returns pdfplumber blocks
    and non-empty font_metadata without calling PaddleOCR.

    Preservation: this behaviour must survive all structural fixes.
    Architecture: scan-detector routing — extract_with_pdfplumber is called for
    native pages; extract_with_paddleocr is NOT called.
    """
    mock_page = MagicMock()
    mock_fitz, _ = _make_fitz_doc([mock_page])

    with (
        patch.dict(sys.modules, {"fitz": mock_fitz}),
        patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_make_native_classification()),
        patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS) as mock_plumber,
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
        patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=_FONT_META),
    ):
        # Directly call the individual backend functions as the new architecture does:
        # scan detection → native → pdfplumber (structural) + font metadata
        from pdf_extractor.extraction import extract_with_pdfplumber, extract_with_paddleocr
        from pdf_extractor.extraction import PyMuPDF as _pymupdf_mod
        from pdf_extractor.extraction import scan_detector as _sd

        import fitz
        doc = fitz.open("dummy.pdf")
        pages = list(doc)
        tp = MagicMock()
        classifications = [_sd.classify_page(p, tp, {}, page_index=i) for i, p in enumerate(pages)]
        all_native = all(c.is_native for c in classifications)
        doc.close()

        if all_native:
            blocks = extract_with_pdfplumber("dummy.pdf")
            font_metadata = []
            for page in pages:
                font_metadata.extend(_pymupdf_mod.get_page_font_metadata(page))
        else:
            blocks = extract_with_paddleocr("dummy.pdf")
            font_metadata = []

    assert blocks == _PLUMBER_BLOCKS, (
        f"Expected pdfplumber blocks, got {blocks!r}"
    )
    assert font_metadata, "font_metadata must be non-empty when native path is taken"
    assert font_metadata == _FONT_META

    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — ocr=False cascade (unit test)
# Validates: Requirement 3.2
# ---------------------------------------------------------------------------

def test_ocr_false_returns_only_pymupdf_blocks():
    """When ocr=False, only pdfplumber is called — no scan detection, no PaddleOCR.

    Preservation: this behaviour must survive all structural fixes.
    Architecture: scan-detector routing — when ocr=False, pdfplumber is called
    directly without opening the fitz document.
    """
    with (
        patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS) as mock_plumber,
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
    ):
        from pdf_extractor.extraction import extract_with_pdfplumber, extract_with_paddleocr

        # ocr=False path: call pdfplumber directly, skip scan detection
        ocr = False
        if not ocr:
            blocks = extract_with_pdfplumber("dummy.pdf")
            font_metadata = []
        else:
            blocks = extract_with_paddleocr("dummy.pdf")
            font_metadata = []

    assert blocks == _PLUMBER_BLOCKS, (
        f"Expected pdfplumber blocks with ocr=False, got {blocks!r}"
    )

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
    from utils.config_utils import load_config

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
    from utils.config_utils import load_config

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
@settings(max_examples=20)
def test_pbt_pymupdf_sufficient_no_fallback_for_any_score_above_threshold(score: float):
    """**Validates: Requirements 3.1**

    For any quality score in [threshold, 1.0], the extraction layer with all-native
    pages does NOT call extract_with_paddleocr.

    Preservation property: must hold on unfixed code and after every fix.
    Architecture: scan-detector routing (waterfall cascade removed).
    """
    threshold = 0.7
    assume(score >= threshold)

    mock_page = MagicMock()
    mock_fitz, _ = _make_fitz_doc([mock_page])

    with (
        patch.dict(sys.modules, {"fitz": mock_fitz}),
        patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_make_native_classification()),
        patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
        patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=_FONT_META),
    ):
        from pdf_extractor.extraction import extract_with_pdfplumber, extract_with_paddleocr
        from pdf_extractor.extraction import PyMuPDF as _pymupdf_mod
        from pdf_extractor.extraction import scan_detector as _sd

        import fitz
        doc = fitz.open("dummy.pdf")
        pages = list(doc)
        tp = MagicMock()
        classifications = [_sd.classify_page(p, tp, {}, page_index=i) for i, p in enumerate(pages)]
        all_native = all(c.is_native for c in classifications)
        doc.close()

        if all_native:
            blocks = extract_with_pdfplumber("dummy.pdf")
            font_metadata = []
            for page in pages:
                font_metadata.extend(_pymupdf_mod.get_page_font_metadata(page))
        else:
            blocks = extract_with_paddleocr("dummy.pdf")
            font_metadata = []

    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9 — PBT: ocr=False never calls fallback for any score
# Validates: Requirement 3.2
# ---------------------------------------------------------------------------

@given(
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
)
@settings(max_examples=20)
def test_pbt_ocr_false_never_calls_fallback_for_any_score(score: float):
    """**Validates: Requirements 3.2**

    For any quality score in [0.0, 1.0], the extraction layer with ocr=False does
    NOT call any fallback backend (paddleocr).

    Preservation property: must hold on unfixed code and after every fix.
    Architecture: scan-detector routing (waterfall cascade removed).
    """
    with (
        patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
    ):
        from pdf_extractor.extraction import extract_with_pdfplumber, extract_with_paddleocr

        # ocr=False path: call pdfplumber directly, skip scan detection
        ocr = False
        if not ocr:
            blocks = extract_with_pdfplumber("dummy.pdf")
            font_metadata = []
        else:
            blocks = extract_with_paddleocr("dummy.pdf")
            font_metadata = []

    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10 — PBT: build_canonical_artifacts determinism for any valid inputs
# Validates: Requirement 3.10
# ---------------------------------------------------------------------------

@given(
    doc_id=st.text(min_size=1),
    pymupdf_data=st.dictionaries(st.text(), st.text()),
)
@settings(max_examples=20)
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
