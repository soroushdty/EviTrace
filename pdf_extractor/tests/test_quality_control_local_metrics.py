"""
tests/test_quality_control_local_metrics.py
============================================
Tests for pdf_extractor/extraction/quality_control/local_metrics.py

Covers all 13 test cases for LocalQCReport and the 8 Tier 1 Local_QC_Metrics:

 1. Import succeeds
 2. Empty LocalQCReport: passes_check() returns True, metric_records has 8 entries
 3. Metric 1 (per-page text coverage): triggered when branch page text < min_chars AND native has more
 4. Metric 2 (GROBID-vs-native ratio): triggered when avg ratio < threshold
 5. Metric 3 (long-sentence fraction): triggered when fraction of long sentences > threshold
 6. Metric 4 (section coverage): triggered when expected sections are absent from full_pdf_text
 7. Metric 5 (caption coverage): triggered when "Table 1" in text but not in any block text
 8. Metric 6 (coordinate availability): triggered when fraction of blocks lacking bbox > threshold
 9. Metric 7 (references in body): triggered when reference keywords appear in too many sentences
10. Metric 8 (weird char ratio): triggered when replacement character '?' ratio exceeds threshold
11. All 8 metrics produce LocalQCMetricRecord with correct field names
12. passes_check() returns False when at least one metric is triggered
13. Config values are used (not hardcoded): test with custom thresholds
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Test 1: Import succeeds
# ---------------------------------------------------------------------------

def test_import_local_qc_report():
    """LocalQCReport must be importable from pdf_extractor.extraction.quality_control."""
    from pdf_extractor.extraction.quality_control import LocalQCReport  # noqa: F401
    assert LocalQCReport is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(**kwargs):
    """Create a LocalQCReport with sensible defaults, overriding with kwargs."""
    from pdf_extractor.extraction.quality_control import LocalQCReport
    defaults = {
        "config": {},
        "blocks": [],
        "sentence_records": [],
        "full_pdf_text": "",
        "page_texts": {},
        "native_page_texts": {},
        "metric_records": [],
    }
    defaults.update(kwargs)
    return LocalQCReport(**defaults)


# ---------------------------------------------------------------------------
# Test 2: Empty LocalQCReport instance
# ---------------------------------------------------------------------------

def test_empty_report_passes_and_has_eight_records():
    """Empty LocalQCReport: passes_check() returns True, metric_records has 8 entries."""
    report = _make_report()
    result = report.passes_check()
    assert result is True
    assert len(report.metric_records) == 8


# ---------------------------------------------------------------------------
# Test 3: Metric 1 – per-page text coverage
# ---------------------------------------------------------------------------

def test_metric1_min_chars_per_page_triggered():
    """Triggered when branch page text < min_chars AND native page text has more."""
    config = {
        "quality_control": {
            "local_metrics": {
                "min_chars_per_page": 100,
            }
        }
    }
    report = _make_report(
        config=config,
        page_texts={0: "short"},        # 5 chars < 100
        native_page_texts={0: "x" * 200},  # 200 chars > 100
    )
    result = report.passes_check()
    assert result is False
    m1 = next(r for r in report.metric_records if r.metric_name == "min_chars_per_page")
    assert m1.triggered is True
    assert m1.computed_value >= 1  # at least one triggered page


def test_metric1_not_triggered_when_native_also_short():
    """Not triggered when native page also has fewer chars than threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "min_chars_per_page": 100,
            }
        }
    }
    report = _make_report(
        config=config,
        page_texts={0: "short"},       # 5 chars < 100
        native_page_texts={0: "also short"},  # also < 100
    )
    report.passes_check()
    m1 = next(r for r in report.metric_records if r.metric_name == "min_chars_per_page")
    assert m1.triggered is False


# ---------------------------------------------------------------------------
# Test 4: Metric 2 – GROBID-vs-native ratio
# ---------------------------------------------------------------------------

def test_metric2_grobid_vs_native_ratio_triggered():
    """Triggered when avg ratio of branch text / native text < threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "grobid_vs_native_ratio_threshold": 0.6,
            }
        }
    }
    report = _make_report(
        config=config,
        page_texts={0: "ab"},           # 2 chars
        native_page_texts={0: "x" * 100},  # 100 chars  => ratio = 0.02 < 0.6
    )
    result = report.passes_check()
    assert result is False
    m2 = next(r for r in report.metric_records if r.metric_name == "grobid_vs_native_ratio")
    assert m2.triggered is True
    assert m2.computed_value < 0.6


def test_metric2_not_triggered_when_ratio_sufficient():
    """Not triggered when average ratio >= threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "grobid_vs_native_ratio_threshold": 0.6,
            }
        }
    }
    report = _make_report(
        config=config,
        page_texts={0: "x" * 80},
        native_page_texts={0: "x" * 100},  # ratio = 0.8 >= 0.6
    )
    report.passes_check()
    m2 = next(r for r in report.metric_records if r.metric_name == "grobid_vs_native_ratio")
    assert m2.triggered is False


# ---------------------------------------------------------------------------
# Test 5: Metric 3 – long-sentence fraction
# ---------------------------------------------------------------------------

def test_metric3_long_sentence_fraction_triggered():
    """Triggered when fraction of long sentences (>word_threshold words) > max_fraction."""
    config = {
        "quality_control": {
            "local_metrics": {
                "long_sentence_word_threshold": 5,
                "long_sentence_max_fraction": 0.1,
            }
        }
    }
    # 2 long sentences out of 10 = 0.2 > 0.1
    long_sentence = " ".join(["word"] * 10)
    short_sentence = "short"
    sentence_records = [{"sentence": long_sentence}] * 2 + [{"sentence": short_sentence}] * 8
    report = _make_report(config=config, sentence_records=sentence_records)
    result = report.passes_check()
    assert result is False
    m3 = next(r for r in report.metric_records if r.metric_name == "long_sentence_fraction")
    assert m3.triggered is True
    assert m3.computed_value > 0.1


def test_metric3_not_triggered_when_fraction_low():
    """Not triggered when long-sentence fraction is below threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "long_sentence_word_threshold": 100,
                "long_sentence_max_fraction": 0.5,
            }
        }
    }
    sentence_records = [{"sentence": "short sentence"}] * 10
    report = _make_report(config=config, sentence_records=sentence_records)
    report.passes_check()
    m3 = next(r for r in report.metric_records if r.metric_name == "long_sentence_fraction")
    assert m3.triggered is False


# ---------------------------------------------------------------------------
# Test 6: Metric 4 – section coverage
# ---------------------------------------------------------------------------

def test_metric4_section_coverage_triggered():
    """Triggered when expected sections are absent from full_pdf_text."""
    config = {
        "quality_control": {
            "local_metrics": {
                "expected_sections": ["abstract", "introduction", "methods", "results"],
            }
        }
    }
    # Only 'abstract' is present
    report = _make_report(
        config=config,
        full_pdf_text="This is the abstract of the paper.",
    )
    result = report.passes_check()
    assert result is False
    m4 = next(r for r in report.metric_records if r.metric_name == "section_coverage")
    assert m4.triggered is True
    assert m4.computed_value < 4  # fewer than 4 sections found


def test_metric4_not_triggered_when_all_sections_present():
    """Not triggered when all expected sections appear in full_pdf_text."""
    config = {
        "quality_control": {
            "local_metrics": {
                "expected_sections": ["abstract", "introduction"],
            }
        }
    }
    report = _make_report(
        config=config,
        full_pdf_text="abstract ... introduction ...",
    )
    report.passes_check()
    m4 = next(r for r in report.metric_records if r.metric_name == "section_coverage")
    assert m4.triggered is False


# ---------------------------------------------------------------------------
# Test 7: Metric 5 – caption/table/figure coverage
# ---------------------------------------------------------------------------

def test_metric5_caption_coverage_triggered():
    """Triggered when 'Table 1' appears in text but not in any block's text."""
    config = {
        "quality_control": {
            "local_metrics": {
                "caption_table_figure_check_enabled": True,
            }
        }
    }
    report = _make_report(
        config=config,
        full_pdf_text="See Table 1 for details.",
        blocks=[{"text": "Some unrelated block text", "block_bbox": [0, 0, 100, 20], "page_index": 0}],
    )
    result = report.passes_check()
    assert result is False
    m5 = next(r for r in report.metric_records if r.metric_name == "caption_table_figure_coverage")
    assert m5.triggered is True


def test_metric5_not_triggered_when_caption_in_block():
    """Not triggered when 'Table 1' appears both in text and in a block."""
    config = {
        "quality_control": {
            "local_metrics": {
                "caption_table_figure_check_enabled": True,
            }
        }
    }
    report = _make_report(
        config=config,
        full_pdf_text="See Table 1 for details.",
        blocks=[{"text": "Table 1 shows the results", "block_bbox": [0, 0, 100, 20], "page_index": 0}],
    )
    report.passes_check()
    m5 = next(r for r in report.metric_records if r.metric_name == "caption_table_figure_coverage")
    assert m5.triggered is False


def test_metric5_not_triggered_when_check_disabled():
    """Not triggered when caption_table_figure_check_enabled is False."""
    config = {
        "quality_control": {
            "local_metrics": {
                "caption_table_figure_check_enabled": False,
            }
        }
    }
    report = _make_report(
        config=config,
        full_pdf_text="See Table 1 for details.",
        blocks=[],
    )
    report.passes_check()
    m5 = next(r for r in report.metric_records if r.metric_name == "caption_table_figure_coverage")
    assert m5.triggered is False


# ---------------------------------------------------------------------------
# Test 8: Metric 6 – coordinate availability
# ---------------------------------------------------------------------------

def test_metric6_coordinate_availability_triggered():
    """Triggered when fraction of blocks lacking bbox > threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "coordinate_coverage_threshold": 0.1,
            }
        }
    }
    # 5 out of 5 blocks have no block_bbox AND no page_index => missing_fraction = 1.0 > 0.1
    blocks = [{"text": "block", "block_bbox": None, "page_index": None}] * 5
    report = _make_report(config=config, blocks=blocks)
    result = report.passes_check()
    assert result is False
    m6 = next(r for r in report.metric_records if r.metric_name == "coordinate_availability")
    assert m6.triggered is True
    assert m6.computed_value > 0.1


def test_metric6_not_triggered_when_coords_present():
    """Not triggered when all blocks have bounding boxes."""
    config = {
        "quality_control": {
            "local_metrics": {
                "coordinate_coverage_threshold": 0.1,
            }
        }
    }
    blocks = [{"text": "block", "block_bbox": [0, 0, 100, 20], "page_index": 0}] * 5
    report = _make_report(config=config, blocks=blocks)
    report.passes_check()
    m6 = next(r for r in report.metric_records if r.metric_name == "coordinate_availability")
    assert m6.triggered is False


# ---------------------------------------------------------------------------
# Test 9: Metric 7 – references in body
# ---------------------------------------------------------------------------

def test_metric7_references_in_body_triggered():
    """Triggered when reference keywords appear in too many body sentences."""
    config = {
        "quality_control": {
            "local_metrics": {
                "references_in_body_threshold": 0.05,
            }
        }
    }
    # 3 out of 10 sentences contain "references" => 0.30 > 0.05
    ref_sentence = "See references for more details."
    normal_sentence = "This sentence is normal."
    sentence_records = [{"sentence": ref_sentence}] * 3 + [{"sentence": normal_sentence}] * 7
    report = _make_report(config=config, sentence_records=sentence_records)
    result = report.passes_check()
    assert result is False
    m7 = next(r for r in report.metric_records if r.metric_name == "references_in_body")
    assert m7.triggered is True
    assert m7.computed_value > 0.05


def test_metric7_not_triggered_when_few_references():
    """Not triggered when reference keywords appear in few sentences."""
    config = {
        "quality_control": {
            "local_metrics": {
                "references_in_body_threshold": 0.5,
            }
        }
    }
    sentence_records = [{"sentence": "Normal sentence about nothing."}] * 10
    report = _make_report(config=config, sentence_records=sentence_records)
    report.passes_check()
    m7 = next(r for r in report.metric_records if r.metric_name == "references_in_body")
    assert m7.triggered is False


# ---------------------------------------------------------------------------
# Test 10: Metric 8 – weird char ratio
# ---------------------------------------------------------------------------

def test_metric8_weird_char_ratio_triggered():
    """Triggered when replacement character '\\ufffd' ratio exceeds threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "weird_char_ratio_threshold": 0.05,
            }
        }
    }
    # 10 weird chars in 20-char text => ratio driven by matches of the pattern
    weird_text = "�" * 10 + "normal text"  # lots of replacement chars
    report = _make_report(config=config, full_pdf_text=weird_text)
    result = report.passes_check()
    assert result is False
    m8 = next(r for r in report.metric_records if r.metric_name == "weird_char_ratio")
    assert m8.triggered is True


def test_metric8_not_triggered_on_clean_text():
    """Not triggered on clean ASCII text."""
    config = {
        "quality_control": {
            "local_metrics": {
                "weird_char_ratio_threshold": 0.05,
            }
        }
    }
    report = _make_report(
        config=config,
        full_pdf_text="This is perfectly clean ASCII text with no weird characters at all.",
    )
    report.passes_check()
    m8 = next(r for r in report.metric_records if r.metric_name == "weird_char_ratio")
    assert m8.triggered is False


# ---------------------------------------------------------------------------
# Test 11: All 8 metrics produce LocalQCMetricRecord with correct field names
# ---------------------------------------------------------------------------

def test_all_eight_metric_records_have_correct_fields():
    """All 8 metrics produce LocalQCMetricRecord instances with the required fields."""
    from pdf_extractor.extraction.quality_control import LocalQCMetricRecord

    report = _make_report()
    report.passes_check()

    expected_metric_names = {
        "min_chars_per_page",
        "grobid_vs_native_ratio",
        "long_sentence_fraction",
        "section_coverage",
        "caption_table_figure_coverage",
        "coordinate_availability",
        "references_in_body",
        "weird_char_ratio",
    }

    assert len(report.metric_records) == 8
    actual_names = {r.metric_name for r in report.metric_records}
    assert actual_names == expected_metric_names

    for record in report.metric_records:
        assert isinstance(record, LocalQCMetricRecord)
        assert hasattr(record, "metric_name")
        assert hasattr(record, "computed_value")
        assert hasattr(record, "threshold")
        assert hasattr(record, "triggered")
        assert isinstance(record.triggered, bool)


# ---------------------------------------------------------------------------
# Test 12: passes_check() returns False when at least one metric is triggered
# ---------------------------------------------------------------------------

def test_passes_check_returns_false_when_any_metric_triggered():
    """passes_check() returns False when at least one metric fires."""
    # Use weird char ratio trigger (easy to control)
    config = {
        "quality_control": {
            "local_metrics": {
                "weird_char_ratio_threshold": 0.001,  # very low threshold
            }
        }
    }
    weird_text = "�" * 50 + "a" * 50  # 50% weird chars far above 0.001
    report = _make_report(config=config, full_pdf_text=weird_text)
    result = report.passes_check()
    assert result is False

    # Confirm at least one record is triggered
    triggered = [r for r in report.metric_records if r.triggered]
    assert len(triggered) >= 1


# ---------------------------------------------------------------------------
# Test 13: Config values are used (not hardcoded) — custom thresholds
# ---------------------------------------------------------------------------

def test_config_thresholds_are_respected_for_min_chars():
    """Custom min_chars_per_page threshold from config is used, not a hardcoded value."""
    # With a very high threshold (1000), even 500-char pages trigger the metric
    config_high = {
        "quality_control": {
            "local_metrics": {
                "min_chars_per_page": 1000,
            }
        }
    }
    config_low = {
        "quality_control": {
            "local_metrics": {
                "min_chars_per_page": 10,
            }
        }
    }
    page_texts = {0: "x" * 50}       # 50 chars
    native_page_texts = {0: "x" * 2000}  # 2000 native chars

    report_high = _make_report(
        config=config_high,
        page_texts=page_texts,
        native_page_texts=native_page_texts,
    )
    report_low = _make_report(
        config=config_low,
        page_texts=page_texts,
        native_page_texts=native_page_texts,
    )

    report_high.passes_check()
    report_low.passes_check()

    m1_high = next(r for r in report_high.metric_records if r.metric_name == "min_chars_per_page")
    m1_low = next(r for r in report_low.metric_records if r.metric_name == "min_chars_per_page")

    # With threshold=1000, 50-char page triggers; with threshold=10, 50-char page does not
    assert m1_high.triggered is True
    assert m1_low.triggered is False


def test_config_thresholds_are_respected_for_long_sentence_fraction():
    """Custom long_sentence_max_fraction threshold from config is used."""
    long_sentence = " ".join(["word"] * 10)  # 10 words
    short_sentence = "short"
    # 1 long out of 10 = 10%
    sentence_records = [{"sentence": long_sentence}] * 1 + [{"sentence": short_sentence}] * 9

    # With max_fraction=0.05 (5%), 10% triggers
    config_strict = {
        "quality_control": {
            "local_metrics": {
                "long_sentence_word_threshold": 5,
                "long_sentence_max_fraction": 0.05,
            }
        }
    }
    # With max_fraction=0.50 (50%), 10% does not trigger
    config_lenient = {
        "quality_control": {
            "local_metrics": {
                "long_sentence_word_threshold": 5,
                "long_sentence_max_fraction": 0.50,
            }
        }
    }

    report_strict = _make_report(config=config_strict, sentence_records=sentence_records)
    report_lenient = _make_report(config=config_lenient, sentence_records=sentence_records)

    report_strict.passes_check()
    report_lenient.passes_check()

    m3_strict = next(r for r in report_strict.metric_records if r.metric_name == "long_sentence_fraction")
    m3_lenient = next(r for r in report_lenient.metric_records if r.metric_name == "long_sentence_fraction")

    assert m3_strict.triggered is True
    assert m3_lenient.triggered is False


def test_local_qc_report_is_subclass_of_quality_report():
    """LocalQCReport must be a subclass of QualityReport."""
    from pdf_extractor.extraction.quality_control import LocalQCReport, QualityReport
    assert issubclass(LocalQCReport, QualityReport)


def test_local_qc_report_is_dataclass():
    """LocalQCReport must be a dataclass."""
    import dataclasses
    from pdf_extractor.extraction.quality_control import LocalQCReport
    assert dataclasses.is_dataclass(LocalQCReport)
