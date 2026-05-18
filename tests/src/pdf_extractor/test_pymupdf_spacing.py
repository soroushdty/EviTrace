"""
tests/src/pdf_extractor/test_pymupdf_spacing.py
-------------------------------------------------
Unit tests for PyMuPDF span-joining logic (_should_insert_space).

Validates: Requirements 13.4, 13.5
"""

import sys
from unittest.mock import MagicMock, patch

from pdf_extractor.extraction.PyMuPDF import _should_insert_space


class TestShouldInsertSpace:
    """Unit tests for the _should_insert_space helper."""

    def test_zero_gap_joins_without_space(self):
        """Requirement 13.4: 'cardio' + 'vascular' with zero gap → 'cardiovascular'.

        When two spans are immediately adjacent (right edge of prev == left edge
        of curr), they should be joined without a space.
        """
        # "cardio" occupies x=[0, 60], so avg char width = 60/6 = 10
        prev_span = {"bbox": (0.0, 0.0, 60.0, 12.0), "text": "cardio"}
        # "vascular" starts exactly where "cardio" ends (x=60)
        curr_span = {"bbox": (60.0, 0.0, 140.0, 12.0), "text": "vascular"}

        result = _should_insert_space(prev_span, curr_span)
        assert result is False, (
            "Zero-gap spans should be joined without a space"
        )

        # Verify the joined text would be "cardiovascular"
        joined = prev_span["text"] + ("" if not result else " ") + curr_span["text"]
        assert joined == "cardiovascular"

    def test_gap_exceeding_threshold_inserts_space(self):
        """Requirement 13.5: 'heart' + 'failure' with gap > threshold → 'heart failure'.

        When the horizontal gap between spans exceeds 1/4 of the average
        character width in the preceding span, a space should be inserted.
        """
        # "heart" occupies x=[0, 50], so avg char width = 50/5 = 10
        # Threshold = 10 / 4 = 2.5
        prev_span = {"bbox": (0.0, 0.0, 50.0, 12.0), "text": "heart"}
        # "failure" starts at x=55, gap = 55 - 50 = 5 > 2.5 threshold
        curr_span = {"bbox": (55.0, 0.0, 125.0, 12.0), "text": "failure"}

        result = _should_insert_space(prev_span, curr_span)
        assert result is True, (
            "Gap exceeding threshold should trigger space insertion"
        )

        # Verify the joined text would be "heart failure"
        joined = prev_span["text"] + (" " if result else "") + curr_span["text"]
        assert joined == "heart failure"

    def test_negative_gap_no_space(self):
        """Overlapping spans (negative gap) should not insert a space."""
        # "over" occupies x=[0, 40], avg char width = 10
        prev_span = {"bbox": (0.0, 0.0, 40.0, 12.0), "text": "over"}
        # "lap" starts at x=38, gap = 38 - 40 = -2 (overlap)
        curr_span = {"bbox": (38.0, 0.0, 68.0, 12.0), "text": "lap"}

        result = _should_insert_space(prev_span, curr_span)
        assert result is False

    def test_missing_bbox_inserts_space_conservatively(self):
        """When bbox is unavailable, conservatively insert a space."""
        prev_span = {"bbox": None, "text": "hello"}
        curr_span = {"bbox": (50.0, 0.0, 100.0, 12.0), "text": "world"}

        result = _should_insert_space(prev_span, curr_span)
        assert result is True

    def test_gap_at_threshold_boundary_no_space(self):
        """Gap exactly at threshold should NOT insert a space (gap must exceed)."""
        # "test" occupies x=[0, 40], avg char width = 40/4 = 10
        # Threshold = 10 / 4 = 2.5
        prev_span = {"bbox": (0.0, 0.0, 40.0, 12.0), "text": "test"}
        # Gap = 42.5 - 40 = 2.5, exactly at threshold (not exceeding)
        curr_span = {"bbox": (42.5, 0.0, 80.0, 12.0), "text": "case"}

        result = _should_insert_space(prev_span, curr_span)
        assert result is False, (
            "Gap exactly at threshold should not insert space (must exceed)"
        )


class TestSpanJoiningIntegration:
    """Integration tests verifying span joining through extract_with_pymupdf."""

    def _build_mock_fitz(self, spans: list[dict]) -> MagicMock:
        """Build a mock fitz module with a single page/block/line containing spans."""
        fitz_spans = [
            {
                "text": s["text"],
                "font": s.get("font", "Arial"),
                "size": s.get("size", 12.0),
                "flags": s.get("flags", 0),
                "color": s.get("color", 0),
                "bbox": s["bbox"],
            }
            for s in spans
        ]
        line_dict = {"spans": fitz_spans}
        block_dict = {
            "type": 0,
            "bbox": (0.0, 0.0, 200.0, 40.0),
            "lines": [line_dict],
        }
        page_text_dict = {"blocks": [block_dict]}

        mock_page = MagicMock()
        mock_page.get_text.return_value = page_text_dict

        mock_doc = MagicMock()
        mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc
        return mock_fitz

    def test_cardiovascular_joined_without_space(self):
        """Requirement 13.4: Full integration — 'cardio' + 'vascular' → 'cardiovascular'."""
        from pdf_extractor.extraction import PyMuPDF as pymupdf_backend

        spans = [
            {"text": "cardio", "bbox": (0.0, 0.0, 60.0, 12.0)},
            {"text": "vascular", "bbox": (60.0, 0.0, 140.0, 12.0)},
        ]
        mock_fitz = self._build_mock_fitz(spans)

        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            blocks, _ = pymupdf_backend.extract_with_pymupdf("fake.pdf")

        assert len(blocks) == 1
        assert blocks[0]["text"] == "cardiovascular"

    def test_heart_failure_joined_with_space(self):
        """Requirement 13.5: Full integration — 'heart' + 'failure' → 'heart failure'."""
        from pdf_extractor.extraction import PyMuPDF as pymupdf_backend

        # "heart" at x=[0,50], avg char width = 10, threshold = 2.5
        # "failure" starts at x=55, gap = 5 > 2.5
        spans = [
            {"text": "heart", "bbox": (0.0, 0.0, 50.0, 12.0)},
            {"text": "failure", "bbox": (55.0, 0.0, 125.0, 12.0)},
        ]
        mock_fitz = self._build_mock_fitz(spans)

        with patch.dict(sys.modules, {"fitz": mock_fitz}):
            blocks, _ = pymupdf_backend.extract_with_pymupdf("fake.pdf")

        assert len(blocks) == 1
        assert blocks[0]["text"] == "heart failure"
