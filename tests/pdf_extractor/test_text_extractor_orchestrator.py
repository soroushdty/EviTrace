"""
tests/test_text_extractor_orchestrator.py
------------------------------------------
Property-based tests for the ``text_extractor`` orchestrator
(``pdf_extractor/extraction/__init__.py``).

Properties covered:
  9.  Orchestrator returns PyMuPDF blocks when score meets threshold or OCR is disabled
  10. Orchestrator cascade reaches PaddleOCR when all prior tiers are below threshold
  11. validate_blocks is always called before extract_pdf returns

Note: Tesseract backend removed as of architecture-migration task 1.1.
The cascade is now 3-tier: PyMuPDF → pdfplumber → PaddleOCR.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from hypothesis import given, settings, strategies as st

import pdf_extractor.extraction


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_blocks(label: str, count: int = 1) -> list:
    """Return a minimal list of valid BlockDict-shaped dicts for testing."""
    return [
        {
            "text": f"{label} block {i}",
            "page_index": i,
            "block_bbox": None,
            "spans": [],
        }
        for i in range(count)
    ]


_PYMUPDF_BLOCKS = _make_blocks("pymupdf")
_PLUMBER_BLOCKS = _make_blocks("pdfplumber")
_PADDLE_BLOCKS = _make_blocks("paddle")

_FONT_META = [{"size": 12.0, "text": "hello", "page": 0}]

# Score strategies — floats in [0.0, 1.0], no NaN/inf.
_score_st = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


# ---------------------------------------------------------------------------
# Property 9: Orchestrator returns PyMuPDF blocks when score meets threshold
#             or OCR is disabled
# Feature: text-extractor-restructure, Property 9: Orchestrator returns PyMuPDF blocks when score meets threshold or OCR is disabled
# Validates: Requirements 7.4
# ---------------------------------------------------------------------------

@given(
    score=_score_st,
    threshold=_score_st,
    ocr=st.booleans(),
)
@settings(max_examples=200)
def test_property9_pymupdf_path_taken_when_score_meets_threshold_or_ocr_disabled(
    score, threshold, ocr
):
    # Feature: text-extractor-restructure, Property 9: Orchestrator returns PyMuPDF blocks when score meets threshold or OCR is disabled
    # Only exercise cases where PyMuPDF path should be taken:
    # either score >= threshold OR ocr is False.
    if not (score >= threshold or not ocr):
        return  # skip cases that would fall through to OCR

    with (
        patch("pdf_extractor.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)) as mock_pymupdf,
        patch("pdf_extractor.extraction.extract_with_pdfplumber") as mock_plumber,
        patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
        patch("pdf_extractor.extraction._compute_quality_score", return_value=score),
    ):
        blocks, font_meta = pdf_extractor.extraction.extract_pdf(
            pdf_path="fake.pdf",
            ocr=ocr,
            ocr_text_quality_threshold=threshold,
            embed_model=None,
        )

    # Must return PyMuPDF blocks and font metadata.
    assert blocks == _PYMUPDF_BLOCKS
    assert font_meta == _FONT_META

    # Fallback backends must NOT have been called.
    mock_plumber.assert_not_called()
    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Property 10: Orchestrator cascade reaches PaddleOCR when all prior tiers
#              are below threshold
# Feature: text-extractor-restructure, Property 10: Orchestrator cascade selects PaddleOCR as final OCR backend
# Validates: Requirements 7.5, 7.6
# Note: Tesseract removed; PaddleOCR is now the sole OCR backend (tier 3).
# ---------------------------------------------------------------------------

# Scores strictly below 1.0 so all fallback backends are below threshold.
_sub_threshold_score_st = st.floats(
    min_value=0.0, max_value=0.99, allow_nan=False, allow_infinity=False
)


@given(
    pymupdf_score=_sub_threshold_score_st,
    plumber_score=_sub_threshold_score_st,
)
@settings(max_examples=200)
def test_property10_cascade_reaches_paddleocr_when_prior_tiers_below_threshold(
    pymupdf_score, plumber_score
):
    # Feature: text-extractor-restructure, Property 10: Orchestrator cascade selects PaddleOCR as final OCR backend
    # Threshold is 1.0 so PyMuPDF and pdfplumber scores are always below it.
    threshold = 1.0

    # Call order: pymupdf_blocks → plumber_blocks → (PaddleOCR called unconditionally).
    score_sequence = [pymupdf_score, plumber_score]
    score_iter = iter(score_sequence)

    def _side_effect_score(blocks, embed_model):
        return next(score_iter)

    with (
        patch("pdf_extractor.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
        patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
        patch("pdf_extractor.extraction._compute_quality_score", side_effect=_side_effect_score),
    ):
        blocks, font_meta = pdf_extractor.extraction.extract_pdf(
            pdf_path="fake.pdf",
            ocr=True,
            ocr_text_quality_threshold=threshold,
            embed_model=None,
        )

    # PaddleOCR is the sole final-tier backend; it always wins when reached.
    assert blocks == _PADDLE_BLOCKS, "Expected paddle blocks when all prior tiers are below threshold"
    assert font_meta == []


# ---------------------------------------------------------------------------
# Property 11: validate_blocks is always called before extract_pdf returns
# Feature: text-extractor-restructure, Property 11: validate_blocks is always called before extract_pdf returns
# Validates: Requirements 7.7
# ---------------------------------------------------------------------------

# Enumerate all cascade paths explicitly (now 3-tier: PyMuPDF → pdfplumber → PaddleOCR).
# score_sequence entries: [pymupdf_score, plumber_score]
# None entries are omitted from the sequence (path stops before reaching that tier).
_CASCADE_PATHS = [
    # label, pymupdf_score, plumber_score, ocr, threshold, expected_blocks
    # Path A: PyMuPDF wins (score >= threshold)
    ("pymupdf_wins_score", 0.9, None, True, 0.5, _PYMUPDF_BLOCKS),
    # Path B: PyMuPDF wins (ocr=False)
    ("pymupdf_wins_ocr_false", 0.0, None, False, 0.5, _PYMUPDF_BLOCKS),
    # Path C: pdfplumber wins (plumber_score >= threshold)
    ("plumber_wins", 0.0, 0.9, True, 0.5, _PLUMBER_BLOCKS),
    # Path D: PaddleOCR wins (all prior tiers below threshold)
    ("paddle_wins", 0.0, 0.0, True, 1.0, _PADDLE_BLOCKS),
]


@pytest.mark.parametrize(
    "label,pymupdf_score,plumber_score,ocr,threshold,expected_blocks",
    _CASCADE_PATHS,
)
def test_property11_validate_blocks_called_exactly_once(
    label, pymupdf_score, plumber_score, ocr, threshold, expected_blocks
):
    # Feature: text-extractor-restructure, Property 11: validate_blocks is always called before extract_pdf returns
    score_sequence = [s for s in [pymupdf_score, plumber_score] if s is not None]
    score_iter = iter(score_sequence)

    def _side_effect_score(blocks, embed_model):
        return next(score_iter)

    with (
        patch("pdf_extractor.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
        patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
        patch("pdf_extractor.extraction._compute_quality_score", side_effect=_side_effect_score),
        patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_validate,
    ):
        blocks, _ = pdf_extractor.extraction.extract_pdf(
            pdf_path="fake.pdf",
            ocr=ocr,
            ocr_text_quality_threshold=threshold,
            embed_model=None,
        )

    # validate_blocks must be called exactly once.
    assert mock_validate.call_count == 1, (
        f"[{label}] Expected validate_blocks to be called once, "
        f"got {mock_validate.call_count}"
    )

    # It must be called with the correct (winning) block list.
    mock_validate.assert_called_once_with(expected_blocks)

    # The returned blocks must be the expected ones.
    assert blocks == expected_blocks, f"[{label}] Wrong blocks returned"


@given(
    score=_score_st,
    threshold=_score_st,
    ocr=st.booleans(),
)
@settings(max_examples=200)
def test_property11_validate_blocks_called_exactly_once_property(score, threshold, ocr):
    # Feature: text-extractor-restructure, Property 11: validate_blocks is always called before extract_pdf returns
    # Use a fixed pymupdf score; if it triggers fallback, use fixed scores for all tiers.
    pymupdf_score = score
    plumber_score = 0.3

    score_sequence = [pymupdf_score, plumber_score]
    score_iter = iter(score_sequence)

    def _side_effect_score(blocks, embed_model):
        return next(score_iter)

    with (
        patch("pdf_extractor.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
        patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
        patch("pdf_extractor.extraction._compute_quality_score", side_effect=_side_effect_score),
        patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_validate,
    ):
        pdf_extractor.extraction.extract_pdf(
            pdf_path="fake.pdf",
            ocr=ocr,
            ocr_text_quality_threshold=threshold,
            embed_model=None,
        )

    # Regardless of which path was taken, validate_blocks must be called exactly once.
    assert mock_validate.call_count == 1
