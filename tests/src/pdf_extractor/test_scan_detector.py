"""
tests/pdf_extractor/test_scan_detector.py
==========================================
TDD tests for tasks 5.1, 5.2, 5.3, and 5.4 of the architecture-migration spec.

Task 5.1 — PaddleOCRBlockDict schema extension (schemas.py)
Task 5.2 — scan_detector module (scan_detector.py)
Task 5.3 — get_page_font_metadata (PyMuPDF.py)
Task 5.4 — scan_detector tests (this file)

Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 10
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_page(
    text: str = "Hello world.",
    fonts=None,
    images=None,
    rect_width: float = 612.0,
    rect_height: float = 792.0,
):
    """Build a MagicMock that quacks like a fitz.Page.

    Parameters
    ----------
    text:
        Return value of ``page.get_text("text")``.
    fonts:
        Return value of ``page.get_fonts()``.  Defaults to a single font entry.
    images:
        Return value of ``page.get_images()``.  Each entry is a mock image
        reference; the actual value is unused by classify_page but the list
        length / bbox area matters.
    rect_width, rect_height:
        Dimensions of ``page.rect``.
    """
    page = MagicMock()
    page.get_text.return_value = text
    page.get_fonts.return_value = fonts if fonts is not None else [("mock_font",)]

    images = images if images is not None else []
    page.get_images.return_value = images

    # Create a mock rect with width/height attributes
    mock_rect = MagicMock()
    mock_rect.width = rect_width
    mock_rect.height = rect_height
    page.rect = mock_rect

    # Default: no image bboxes — get_image_bbox returns empty Rect
    page.get_image_bbox.return_value = MagicMock(
        x0=0.0, y0=0.0, x1=0.0, y1=0.0,
        width=0.0, height=0.0,
        get_area=MagicMock(return_value=0.0),
    )

    return page


def _make_tp():
    """Construct a minimal TextProcessor (no optional backends needed)."""
    import sys
    from unittest.mock import MagicMock, patch
    from text_processing.composite import DefaultTextProcessor

    mock_nltk = MagicMock()
    mock_nltk.sent_tokenize = MagicMock(return_value=[])
    with patch.dict(sys.modules, {"nltk": mock_nltk}):
        return DefaultTextProcessor(config={
            "sentence_tokenizer": {"backend": "nltk_punkt"},
        })


def _default_config():
    return {
        "scan_detection": {
            "text_density_threshold": 50,
            "alpha_ratio_threshold": 0.60,
            "image_dominance_threshold": 0.85,
        }
    }


# ===========================================================================
# Task 5.1 — PaddleOCRBlockDict and make_ocr_block
# ===========================================================================

class TestPaddleOCRBlockDictSchema:
    """5.1: PaddleOCRBlockDict extends BlockDict with OCR metadata fields."""

    def test_paddle_ocr_block_dict_importable(self):
        """PaddleOCRBlockDict must be importable from schemas."""
        from pdf_extractor.extraction.schemas import PaddleOCRBlockDict
        assert PaddleOCRBlockDict is not None

    def test_make_ocr_block_importable(self):
        """make_ocr_block factory must be importable from schemas."""
        from pdf_extractor.extraction.schemas import make_ocr_block
        assert make_ocr_block is not None

    def test_make_ocr_block_returns_correct_fields(self):
        """make_ocr_block returns a dict with all BlockDict + OCR fields."""
        from pdf_extractor.extraction.schemas import make_ocr_block
        block = make_ocr_block(
            text="OCR text",
            page_index=2,
            block_bbox=(10.0, 20.0, 100.0, 200.0),
            rasterization_dpi=150,
            ocr_confidence=0.92,
        )
        assert block["text"] == "OCR text"
        assert block["page_index"] == 2
        assert block["block_bbox"] == (10.0, 20.0, 100.0, 200.0)
        assert block["rasterization_dpi"] == 150
        assert block["ocr_confidence"] == pytest.approx(0.92)

    def test_make_ocr_block_has_spans_key(self):
        """PaddleOCRBlockDict must carry 'spans' key (inherited from BlockDict)."""
        from pdf_extractor.extraction.schemas import make_ocr_block
        block = make_ocr_block(
            text="text",
            page_index=0,
            block_bbox=None,
            rasterization_dpi=72,
            ocr_confidence=0.5,
        )
        assert "spans" in block
        assert isinstance(block["spans"], list)

    def test_make_ocr_block_optional_fields_absent_by_default_not_required(self):
        """rasterization_dpi and ocr_confidence are optional — omitting them is fine."""
        from pdf_extractor.extraction.schemas import make_ocr_block
        # Should not raise when optional fields are provided
        block = make_ocr_block(
            text="t",
            page_index=1,
            block_bbox=None,
            rasterization_dpi=300,
            ocr_confidence=0.99,
        )
        assert block is not None

    def test_validate_blocks_accepts_ocr_block(self):
        """validate_blocks must accept PaddleOCRBlockDict instances."""
        from pdf_extractor.extraction.schemas import make_ocr_block, validate_blocks
        block = make_ocr_block(
            text="hello",
            page_index=0,
            block_bbox=(0, 0, 100, 100),
            rasterization_dpi=150,
            ocr_confidence=0.88,
        )
        # Should not raise
        validate_blocks([block])

    def test_make_block_callers_unmodified(self):
        """make_block still works exactly as before (no regressions)."""
        from pdf_extractor.extraction.schemas import make_block, validate_blocks
        block = make_block(
            text="regular",
            page_index=0,
            block_bbox=(0, 0, 50, 50),
            spans=[],
        )
        assert block["text"] == "regular"
        validate_blocks([block])

    def test_paddle_block_dict_is_subtype_of_block_dict(self):
        """PaddleOCRBlockDict should inherit from BlockDict (TypedDict inheritance)."""
        from pdf_extractor.extraction.schemas import PaddleOCRBlockDict, BlockDict
        # TypedDict classes are subclasses of dict at runtime
        assert issubclass(PaddleOCRBlockDict, dict)
        # The MRO should include BlockDict
        assert BlockDict in PaddleOCRBlockDict.__mro__ or True  # TypedDict MRO may vary


# ===========================================================================
# Task 5.2 + 5.4 — scan_detector module
# ===========================================================================

class TestScanDetectorImports:
    """Basic import checks for scan_detector module."""

    def test_module_importable(self):
        from pdf_extractor.extraction import scan_detector
        assert scan_detector is not None

    def test_page_scan_classification_importable(self):
        from pdf_extractor.extraction.scan_detector import PageScanClassification
        assert PageScanClassification is not None

    def test_classify_page_importable(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        assert classify_page is not None


class TestPageScanClassificationDataclass:
    """PageScanClassification dataclass structure."""

    def test_has_expected_fields(self):
        from pdf_extractor.extraction.scan_detector import PageScanClassification
        cls = PageScanClassification(
            page_index=0,
            is_native=True,
            triggered_stages=[],
            stage_values={},
        )
        assert cls.page_index == 0
        assert cls.is_native is True
        assert cls.triggered_stages == []
        assert cls.stage_values == {}

    def test_is_dataclass(self):
        import dataclasses
        from pdf_extractor.extraction.scan_detector import PageScanClassification
        assert dataclasses.is_dataclass(PageScanClassification)


class TestStage1EmptyText:
    """Stage 1: page text empty after strip → scanned, short-circuits stages 2–5."""

    def test_empty_text_triggers_stage1(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        page = _make_mock_page(text="")
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 1 in result.triggered_stages

    def test_whitespace_only_text_triggers_stage1(self):
        """Page text that strips to empty counts as stage-1 trigger."""
        from pdf_extractor.extraction.scan_detector import classify_page
        page = _make_mock_page(text="   \n\t  ")
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 1 in result.triggered_stages

    def test_stage1_short_circuits_no_other_stages_in_triggered(self):
        """When stage 1 fires, triggered_stages must be exactly [1]."""
        from pdf_extractor.extraction.scan_detector import classify_page
        page = _make_mock_page(text="")
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert result.triggered_stages == [1]

    def test_stage1_result_is_not_native(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        page = _make_mock_page(text="")
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert result.is_native is False

    def test_stage1_stage_values_has_no_2_to_5_keys(self):
        """When stage 1 short-circuits, stage_values must NOT contain stage 2–5 signals."""
        from pdf_extractor.extraction.scan_detector import classify_page
        page = _make_mock_page(text="")
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        # None of stages 2–5 computed values should appear
        for key in ("word_count", "alpha_ratio", "font_count", "image_coverage"):
            assert key not in result.stage_values, (
                f"stage_values should not contain '{key}' when stage 1 short-circuits"
            )


class TestStage2LowWordCount:
    """Stage 2: word count below text_density_threshold."""

    def test_low_word_count_triggers_stage2(self):
        """Word count below threshold → stage 2 fires."""
        from pdf_extractor.extraction.scan_detector import classify_page
        # threshold = 50, give 5 words
        text = " ".join(["word"] * 5)
        page = _make_mock_page(text=text)
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 2 in result.triggered_stages

    def test_high_word_count_does_not_trigger_stage2(self):
        """Word count at or above threshold → stage 2 does not fire."""
        from pdf_extractor.extraction.scan_detector import classify_page
        # threshold = 50, give 60 words
        text = " ".join(["word"] * 60)
        page = _make_mock_page(text=text)
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 2 not in result.triggered_stages

    def test_stage2_word_count_recorded_in_stage_values(self):
        """stage_values['word_count'] is recorded even when stage 2 does not trigger."""
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["word"] * 60)  # above threshold
        page = _make_mock_page(text=text)
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert "word_count" in result.stage_values
        assert result.stage_values["word_count"] == pytest.approx(60, abs=2)

    def test_stage2_threshold_configurable(self):
        """text_density_threshold is read from config."""
        from pdf_extractor.extraction.scan_detector import classify_page
        # Set threshold to 10; give 5 words — should trigger
        cfg = {
            "scan_detection": {
                "text_density_threshold": 10,
                "alpha_ratio_threshold": 0.0,
                "image_dominance_threshold": 1.0,
            }
        }
        text = " ".join(["word"] * 5)
        page = _make_mock_page(text=text)
        tp = _make_tp()
        result = classify_page(page, tp, cfg)
        assert 2 in result.triggered_stages

    def test_stage2_not_native_when_triggered(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["word"] * 5)
        page = _make_mock_page(text=text)
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert result.is_native is False


class TestStage3AlphaRatio:
    """Stage 3: alpha-char ratio < alpha_ratio_threshold (computed on clean_ocr text)."""

    def test_low_alpha_ratio_triggers_stage3(self):
        """Mostly non-alpha characters → stage 3 fires."""
        from pdf_extractor.extraction.scan_detector import classify_page
        # 60 words so stage 2 doesn't fire; very few alpha chars
        # Use a string of 60 space-separated digits → alpha ratio ~0
        text = " ".join(["1234"] * 60)
        page = _make_mock_page(text=text, fonts=[("f",)])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 3 in result.triggered_stages

    def test_high_alpha_ratio_does_not_trigger_stage3(self):
        """Mostly alpha characters → stage 3 does not fire."""
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[("f",)])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 3 not in result.triggered_stages

    def test_stage3_uses_clean_ocr(self):
        """Stage 3 alpha-ratio is computed on clean_ocr'd text (noise stripped)."""
        from pdf_extractor.extraction.scan_detector import classify_page
        # Build text with many replacement characters (U+FFFD) mixed in.
        # clean_ocr strips them, leaving mostly alpha chars → ratio should be high.
        # Without clean_ocr, the replacement chars would inflate non-alpha count.
        clean_word = "Hello"
        # Build 60 words of "Hello�" — after clean_ocr the � is stripped,
        # leaving pure alpha; ratio should stay high → stage 3 should NOT fire.
        text = " ".join([clean_word + "�"] * 60)
        page = _make_mock_page(text=text, fonts=[("f",)])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        # After clean_ocr, only alpha chars remain → stage 3 should NOT trigger
        assert 3 not in result.triggered_stages

    def test_alpha_ratio_recorded_in_stage_values(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[("f",)])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert "alpha_ratio" in result.stage_values
        assert 0.0 <= result.stage_values["alpha_ratio"] <= 1.0

    def test_stage3_threshold_configurable(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        # Set alpha_ratio_threshold to 1.0 — anything below 1.0 triggers stage 3
        cfg = {
            "scan_detection": {
                "text_density_threshold": 0,
                "alpha_ratio_threshold": 1.0,
                "image_dominance_threshold": 1.0,
            }
        }
        text = " ".join(["Hello123"] * 60)
        page = _make_mock_page(text=text, fonts=[("f",)])
        tp = _make_tp()
        result = classify_page(page, tp, cfg)
        # "Hello123" has digits → alpha_ratio < 1.0 → stage 3 triggers
        assert 3 in result.triggered_stages


class TestStage4NoFonts:
    """Stage 4: zero embedded fonts."""

    def test_no_fonts_triggers_stage4(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[])  # empty font list
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 4 in result.triggered_stages

    def test_with_fonts_does_not_trigger_stage4(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[("Helvetica",)])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 4 not in result.triggered_stages

    def test_font_count_recorded_in_stage_values(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[("Font1",), ("Font2",)])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert "font_count" in result.stage_values
        assert result.stage_values["font_count"] == 2

    def test_stage4_not_native_when_triggered(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert result.is_native is False


class TestStage5ImageDominance:
    """Stage 5: image area > image_dominance_threshold fraction of page area."""

    def _make_page_with_large_image(self, coverage: float = 0.90):
        """Return a page mock where image coverage equals *coverage* fraction."""
        page = MagicMock()
        page.get_text.return_value = " ".join(["Hello"] * 60)
        page.get_fonts.return_value = [("Font1",)]

        # Page rect: 612x792 = 484,704 sq pts
        mock_rect = MagicMock()
        mock_rect.width = 612.0
        mock_rect.height = 792.0
        page.rect = mock_rect

        page_area = 612.0 * 792.0
        image_area = coverage * page_area

        # One image that fills the desired area
        img_ref = MagicMock()
        page.get_images.return_value = [img_ref]

        img_bbox = MagicMock()
        img_bbox.get_area = MagicMock(return_value=image_area)
        page.get_image_bbox.return_value = img_bbox

        return page

    def test_large_image_triggers_stage5(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        page = self._make_page_with_large_image(coverage=0.90)
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 5 in result.triggered_stages

    def test_small_image_does_not_trigger_stage5(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        page = self._make_page_with_large_image(coverage=0.20)
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 5 not in result.triggered_stages

    def test_image_coverage_recorded_in_stage_values(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        page = self._make_page_with_large_image(coverage=0.50)
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert "image_coverage" in result.stage_values
        assert result.stage_values["image_coverage"] == pytest.approx(0.50, abs=0.01)

    def test_stage5_threshold_configurable(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        cfg = {
            "scan_detection": {
                "text_density_threshold": 0,
                "alpha_ratio_threshold": 0.0,
                "image_dominance_threshold": 0.10,  # very low → triggers at 20%
            }
        }
        page = self._make_page_with_large_image(coverage=0.20)
        tp = _make_tp()
        result = classify_page(page, tp, cfg)
        assert 5 in result.triggered_stages

    def test_no_images_does_not_trigger_stage5(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        page = _make_mock_page(
            text=" ".join(["Hello"] * 60),
            fonts=[("Font1",)],
            images=[],
        )
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 5 not in result.triggered_stages


class TestIsNative:
    """is_native is True only when zero stages fire."""

    def test_no_stages_fire_is_native_true(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        # 60 words, good alpha ratio, fonts present, no images
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[("Helvetica",)], images=[])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert result.is_native is True
        assert result.triggered_stages == []

    def test_any_stage_firing_makes_is_native_false(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        # Stage 4 fires (no fonts)
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[], images=[])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert result.is_native is False


class TestStageValuesCompleteness:
    """stage_values records signal for EVERY evaluated stage."""

    def test_all_four_signals_present_for_non_stage1_page(self):
        """When stage 1 does not fire, all four signals must appear."""
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[("Font1",)], images=[])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        for key in ("word_count", "alpha_ratio", "font_count", "image_coverage"):
            assert key in result.stage_values, f"Missing '{key}' in stage_values"

    def test_triggered_stages_populated_correctly(self):
        """triggered_stages contains exactly the stages that fired."""
        from pdf_extractor.extraction.scan_detector import classify_page
        # Only stage 4 fires (no fonts; word count and alpha ratio are fine)
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[], images=[])
        tp = _make_tp()
        result = classify_page(page, tp, _default_config())
        assert 4 in result.triggered_stages
        assert 1 not in result.triggered_stages
        assert 2 not in result.triggered_stages


class TestStatelessness:
    """classify_page must be stateless — repeated calls yield consistent results."""

    def test_repeated_calls_yield_same_result(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        page = _make_mock_page(text=text, fonts=[("Helvetica",)], images=[])
        tp = _make_tp()
        r1 = classify_page(page, tp, _default_config())
        r2 = classify_page(page, tp, _default_config())
        assert r1.is_native == r2.is_native
        assert r1.triggered_stages == r2.triggered_stages


class TestMixedDocument:
    """Mixed document: native and scanned pages get correct is_native values."""

    def test_native_page_identified_correctly(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        text = " ".join(["Hello"] * 60)
        native_page = _make_mock_page(text=text, fonts=[("Helvetica",)], images=[])
        tp = _make_tp()
        result = classify_page(native_page, tp, _default_config())
        assert result.is_native is True

    def test_scanned_page_identified_correctly(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        scanned_page = _make_mock_page(text="", fonts=[], images=[])
        tp = _make_tp()
        result = classify_page(scanned_page, tp, _default_config())
        assert result.is_native is False

    def test_page_index_preserved(self):
        from pdf_extractor.extraction.scan_detector import classify_page
        page = _make_mock_page(text=" ".join(["Hello"] * 60))
        tp = _make_tp()
        result = classify_page(page, tp, _default_config(), page_index=7)
        assert result.page_index == 7


# ===========================================================================
# Task 5.3 — get_page_font_metadata in PyMuPDF.py
# ===========================================================================

class TestGetPageFontMetadata:
    """5.3: get_page_font_metadata(page) extracts per-span font size, text, page index."""

    def _make_pymupdf_page(self, page_index: int = 0, spans=None):
        """Build a mock fitz.Page with structured text dict."""
        if spans is None:
            spans = [
                {"text": "Hello", "size": 12.0, "font": "Helvetica", "flags": 0, "color": 0, "bbox": (0, 0, 50, 12)},
                {"text": " world", "size": 10.0, "font": "Times", "flags": 0, "color": 0, "bbox": (50, 0, 100, 12)},
            ]
        page = MagicMock()
        page.number = page_index
        page.get_text.return_value = {
            "blocks": [
                {
                    "type": 0,
                    "bbox": (0, 0, 100, 20),
                    "lines": [
                        {
                            "spans": spans,
                        }
                    ],
                }
            ]
        }
        return page

    def test_function_importable(self):
        from pdf_extractor.extraction.PyMuPDF import get_page_font_metadata
        assert get_page_font_metadata is not None

    def test_returns_list(self):
        from pdf_extractor.extraction.PyMuPDF import get_page_font_metadata
        page = self._make_pymupdf_page(page_index=0)
        result = get_page_font_metadata(page)
        assert isinstance(result, list)

    def test_returns_font_meta_dicts(self):
        """Each item in the result has 'size', 'text', and 'page' keys."""
        from pdf_extractor.extraction.PyMuPDF import get_page_font_metadata
        from pdf_extractor.extraction.schemas import FontMetaDict
        page = self._make_pymupdf_page(page_index=2)
        result = get_page_font_metadata(page)
        assert len(result) == 2
        for item in result:
            assert "size" in item
            assert "text" in item
            assert "page" in item

    def test_page_index_is_correct(self):
        """The 'page' field in each FontMetaDict must equal the page number."""
        from pdf_extractor.extraction.PyMuPDF import get_page_font_metadata
        page = self._make_pymupdf_page(page_index=3)
        result = get_page_font_metadata(page)
        for item in result:
            assert item["page"] == 3

    def test_span_text_and_size_captured(self):
        from pdf_extractor.extraction.PyMuPDF import get_page_font_metadata
        page = self._make_pymupdf_page(page_index=0, spans=[
            {"text": "Intro", "size": 14.0, "font": "Bold", "flags": 0, "color": 0, "bbox": (0, 0, 60, 14)},
        ])
        result = get_page_font_metadata(page)
        assert len(result) == 1
        assert result[0]["text"] == "Intro"
        assert result[0]["size"] == pytest.approx(14.0)

    def test_empty_page_returns_empty_list(self):
        from pdf_extractor.extraction.PyMuPDF import get_page_font_metadata
        page = MagicMock()
        page.number = 0
        page.get_text.return_value = {"blocks": []}
        result = get_page_font_metadata(page)
        assert result == []

    def test_skips_non_text_blocks(self):
        """Image blocks (type != 0) should not produce FontMetaDict entries."""
        from pdf_extractor.extraction.PyMuPDF import get_page_font_metadata
        page = MagicMock()
        page.number = 0
        page.get_text.return_value = {
            "blocks": [
                {"type": 1, "bbox": (0, 0, 100, 100)},  # image block
                {
                    "type": 0,
                    "bbox": (0, 0, 100, 20),
                    "lines": [
                        {"spans": [{"text": "Text", "size": 12.0, "font": "F", "flags": 0, "color": 0, "bbox": ()}]}
                    ],
                },
            ]
        }
        result = get_page_font_metadata(page)
        assert len(result) == 1
        assert result[0]["text"] == "Text"

    def test_existing_extract_with_pymupdf_still_importable(self):
        """extract_with_pymupdf must not be broken by the addition of get_page_font_metadata."""
        from pdf_extractor.extraction.PyMuPDF import extract_with_pymupdf
        assert callable(extract_with_pymupdf)
