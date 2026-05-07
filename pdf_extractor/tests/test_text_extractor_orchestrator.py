"""
tests/test_text_extractor_orchestrator.py
------------------------------------------
Property-based tests for the ``text_extractor`` orchestrator
(``evi_trace/extraction/__init__.py``).

Properties covered:
  9.  Orchestrator returns PyMuPDF blocks when score meets threshold or OCR is disabled
  10. Orchestrator cascade selects the highest-scoring OCR backend
  11. validate_blocks is always called before extract_pdf returns
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from hypothesis import given, settings, strategies as st

import evi_trace.extraction


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
_TESS_BLOCKS = _make_blocks("tesseract")
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
        patch("evi_trace.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)) as mock_pymupdf,
        patch("evi_trace.extraction.extract_with_pdfplumber") as mock_plumber,
        patch("evi_trace.extraction.extract_with_tesseract") as mock_tess,
        patch("evi_trace.extraction.extract_with_paddleocr") as mock_paddle,
        patch("evi_trace.extraction._compute_quality_score", return_value=score),
    ):
        blocks, font_meta = evi_trace.extraction.extract_pdf(
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
    mock_tess.assert_not_called()
    mock_paddle.assert_not_called()


# ---------------------------------------------------------------------------
# Property 10: Orchestrator cascade selects the highest-scoring OCR backend
# Feature: text-extractor-restructure, Property 10: Orchestrator cascade selects the highest-scoring OCR backend
# Validates: Requirements 7.5, 7.6
# ---------------------------------------------------------------------------

# Scores strictly below 1.0 so all fallback backends are below threshold.
_sub_threshold_score_st = st.floats(
    min_value=0.0, max_value=0.99, allow_nan=False, allow_infinity=False
)


@given(
    tess_score=_sub_threshold_score_st,
    paddle_score=_sub_threshold_score_st,
)
@settings(max_examples=200)
def test_property10_cascade_selects_highest_scoring_ocr_backend(tess_score, paddle_score):
    # Feature: text-extractor-restructure, Property 10: Orchestrator cascade selects the highest-scoring OCR backend
    # Threshold is 1.0 so PyMuPDF score (0.0), pdfplumber score (0.0), and both OCR scores are below it.
    threshold = 1.0
    pymupdf_score = 0.0   # forces cascade into pdfplumber
    plumber_score = 0.0   # forces cascade into Tesseract

    # Map each call to _compute_quality_score to the right score value.
    # Call order: pymupdf_blocks → plumber_blocks → tess_blocks → paddle_blocks.
    score_sequence = [pymupdf_score, plumber_score, tess_score, paddle_score]
    score_iter = iter(score_sequence)

    def _side_effect_score(blocks, embed_model):
        return next(score_iter)

    with (
        patch("evi_trace.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("evi_trace.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
        patch("evi_trace.extraction.extract_with_tesseract", return_value=_TESS_BLOCKS),
        patch("evi_trace.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
        patch("evi_trace.extraction._compute_quality_score", side_effect=_side_effect_score),
    ):
        blocks, font_meta = evi_trace.extraction.extract_pdf(
            pdf_path="fake.pdf",
            ocr=True,
            ocr_text_quality_threshold=threshold,
            embed_model=None,
        )

    # The backend with the higher score should win.
    # Cascade rule: if paddle_score >= tess_score → paddle, else → tess.
    if paddle_score >= tess_score:
        assert blocks == _PADDLE_BLOCKS, (
            f"Expected paddle blocks (paddle={paddle_score} >= tess={tess_score})"
        )
        assert font_meta == []
    else:
        assert blocks == _TESS_BLOCKS, (
            f"Expected tess blocks (tess={tess_score} > paddle={paddle_score})"
        )
        assert font_meta == []


# ---------------------------------------------------------------------------
# Property 11: validate_blocks is always called before extract_pdf returns
# Feature: text-extractor-restructure, Property 11: validate_blocks is always called before extract_pdf returns
# Validates: Requirements 7.7
# ---------------------------------------------------------------------------

# Enumerate all cascade paths explicitly (now 4-tier: PyMuPDF → pdfplumber → Tesseract → PaddleOCR).
# score_sequence entries: [pymupdf_score, plumber_score, tess_score, paddle_score]
# None entries are omitted from the sequence (path stops before reaching that tier).
_CASCADE_PATHS = [
    # label, pymupdf_score, plumber_score, tess_score, paddle_score, ocr, threshold, expected_blocks
    # Path A: PyMuPDF wins (score >= threshold)
    ("pymupdf_wins_score", 0.9, None, None, None, True, 0.5, _PYMUPDF_BLOCKS),
    # Path B: PyMuPDF wins (ocr=False)
    ("pymupdf_wins_ocr_false", 0.0, None, None, None, False, 0.5, _PYMUPDF_BLOCKS),
    # Path C: pdfplumber wins (plumber_score >= threshold)
    ("plumber_wins", 0.0, 0.9, None, None, True, 0.5, _PLUMBER_BLOCKS),
    # Path D: Tesseract wins (tess_score >= threshold)
    ("tess_wins", 0.0, 0.0, 0.9, None, True, 0.5, _TESS_BLOCKS),
    # Path E: PaddleOCR wins (paddle_score >= tess_score, both below threshold)
    ("paddle_wins", 0.0, 0.0, 0.3, 0.6, True, 1.0, _PADDLE_BLOCKS),
    # Path F: Tesseract wins over PaddleOCR (tess_score > paddle_score, both below threshold)
    ("tess_wins_over_paddle", 0.0, 0.0, 0.6, 0.3, True, 1.0, _TESS_BLOCKS),
]


@pytest.mark.parametrize(
    "label,pymupdf_score,plumber_score,tess_score,paddle_score,ocr,threshold,expected_blocks",
    _CASCADE_PATHS,
)
def test_property11_validate_blocks_called_exactly_once(
    label, pymupdf_score, plumber_score, tess_score, paddle_score, ocr, threshold, expected_blocks
):
    # Feature: text-extractor-restructure, Property 11: validate_blocks is always called before extract_pdf returns
    score_sequence = [s for s in [pymupdf_score, plumber_score, tess_score, paddle_score] if s is not None]
    score_iter = iter(score_sequence)

    def _side_effect_score(blocks, embed_model):
        return next(score_iter)

    with (
        patch("evi_trace.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("evi_trace.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
        patch("evi_trace.extraction.extract_with_tesseract", return_value=_TESS_BLOCKS),
        patch("evi_trace.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
        patch("evi_trace.extraction._compute_quality_score", side_effect=_side_effect_score),
        patch("evi_trace.extraction.schemas.validate_blocks") as mock_validate,
    ):
        blocks, _ = evi_trace.extraction.extract_pdf(
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
    tess_score = 0.3
    paddle_score = 0.4

    score_sequence = [pymupdf_score, plumber_score, tess_score, paddle_score]
    score_iter = iter(score_sequence)

    def _side_effect_score(blocks, embed_model):
        return next(score_iter)

    with (
        patch("evi_trace.extraction.extract_with_pymupdf", return_value=(_PYMUPDF_BLOCKS, _FONT_META)),
        patch("evi_trace.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
        patch("evi_trace.extraction.extract_with_tesseract", return_value=_TESS_BLOCKS),
        patch("evi_trace.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
        patch("evi_trace.extraction._compute_quality_score", side_effect=_side_effect_score),
        patch("evi_trace.extraction.schemas.validate_blocks") as mock_validate,
    ):
        evi_trace.extraction.extract_pdf(
            pdf_path="fake.pdf",
            ocr=ocr,
            ocr_text_quality_threshold=threshold,
            embed_model=None,
        )

    # Regardless of which path was taken, validate_blocks must be called exactly once.
    assert mock_validate.call_count == 1
