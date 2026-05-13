"""
tests/test_quality_control_local_metrics.py
============================================
Tests for pdf_extractor/extraction/quality_control/local_metrics.py

Covers all 13 test cases for ExtractionCoverageReport and the 8 Tier 1 Local_QC_Metrics:

 1. Import succeeds
 2. Empty ExtractionCoverageReport: passes_check() returns True, metric_records has 8 entries
 3. Metric 1 (per-page text coverage): triggered when branch page text < min_chars AND native has more
 4. Metric 2 (extraction coverage ratio): triggered when avg ratio < threshold
 5. Metric 3 (long-sentence fraction): triggered when fraction of long sentences > threshold
 6. Metric 4 (section coverage): triggered when expected sections are absent from full_pdf_text
 7. Metric 5 (caption coverage): triggered when "Table 1" in text but not in any block text
 8. Metric 6 (coordinate availability): triggered when fraction of blocks lacking bbox > threshold
 9. Metric 7 (references in body): triggered when reference keywords appear in too many sentences
10. Metric 8 (weird char ratio): triggered when replacement character '?' ratio exceeds threshold
11. All 8 metrics produce ExtractionCoverageMetricRecord with correct field names
12. passes_check() returns False when at least one metric is triggered
13. Config values are used (not hardcoded): test with custom thresholds
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Test 1: Import succeeds
# ---------------------------------------------------------------------------

def test_import_local_qc_report():
    """ExtractionCoverageReport must be importable from quality_control."""
    from quality_control import ExtractionCoverageReport  # noqa: F401
    assert ExtractionCoverageReport is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(**kwargs):
    """Create a ExtractionCoverageReport with sensible defaults, overriding with kwargs."""
    from quality_control import ExtractionCoverageReport
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
    return ExtractionCoverageReport(**defaults)


# ---------------------------------------------------------------------------
# Test 2: Empty ExtractionCoverageReport instance
# ---------------------------------------------------------------------------

def test_empty_report_passes_and_has_eight_records():
    """Empty ExtractionCoverageReport: passes_check() returns True, metric_records has 8 entries."""
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
# Test 4: Metric 2 – extraction coverage ratio
# ---------------------------------------------------------------------------

def test_metric2_extraction_coverage_ratio_triggered():
    """Triggered when avg ratio of branch text / native text < threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "extraction_coverage_ratio_threshold": 0.6,
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
    m2 = next(r for r in report.metric_records if r.metric_name == "extraction_coverage_ratio")
    assert m2.triggered is True
    assert m2.computed_value < 0.6


def test_metric2_not_triggered_when_ratio_sufficient():
    """Not triggered when average ratio >= threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "extraction_coverage_ratio_threshold": 0.6,
            }
        }
    }
    report = _make_report(
        config=config,
        page_texts={0: "x" * 80},
        native_page_texts={0: "x" * 100},  # ratio = 0.8 >= 0.6
    )
    report.passes_check()
    m2 = next(r for r in report.metric_records if r.metric_name == "extraction_coverage_ratio")
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
    # Only "abstract" is present; "introduction", "methods", "results" are missing
    report = _make_report(
        config=config,
        full_pdf_text="This is the abstract section.",
    )
    result = report.passes_check()
    assert result is False
    m4 = next(r for r in report.metric_records if r.metric_name == "section_coverage")
    assert m4.triggered is True


def test_metric4_section_coverage_not_triggered():
    """Not triggered when all expected sections are present."""
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


def test_metric4_not_triggered_when_no_text():
    """Not triggered when full_pdf_text is empty (nothing to check)."""
    report = _make_report(full_pdf_text="")
    report.passes_check()
    m4 = next(r for r in report.metric_records if r.metric_name == "section_coverage")
    assert m4.triggered is False


# ---------------------------------------------------------------------------
# Test 7: Metric 5 – caption coverage
# ---------------------------------------------------------------------------

def test_metric5_caption_coverage_triggered():
    """Triggered when 'Table 1' appears in text but not in any block text."""
    report = _make_report(
        full_pdf_text="See Table 1 for details.",
        blocks=[{"text": "Some unrelated block text"}],
    )
    result = report.passes_check()
    assert result is False
    m5 = next(r for r in report.metric_records if r.metric_name == "caption_table_figure_coverage")
    assert m5.triggered is True


def test_metric5_caption_coverage_not_triggered_when_block_matches():
    """Not triggered when the caption reference appears in a block."""
    report = _make_report(
        full_pdf_text="See Table 1 for details.",
        blocks=[{"text": "Table 1: Summary of results"}],
    )
    report.passes_check()
    m5 = next(r for r in report.metric_records if r.metric_name == "caption_table_figure_coverage")
    assert m5.triggered is False


def test_metric5_not_triggered_when_no_refs():
    """Not triggered when no Table/Figure references appear in text."""
    report = _make_report(
        full_pdf_text="No captions here.",
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
    # 5 blocks, all missing block_bbox and page_index => fraction = 1.0 > 0.1
    blocks = [{"text": "block"}] * 5
    report = _make_report(config=config, blocks=blocks)
    result = report.passes_check()
    assert result is False
    m6 = next(r for r in report.metric_records if r.metric_name == "coordinate_availability")
    assert m6.triggered is True
    assert m6.computed_value > 0.1


def test_metric6_not_triggered_when_coords_present():
    """Not triggered when blocks have bbox coordinates."""
    config = {
        "quality_control": {
            "local_metrics": {
                "coordinate_coverage_threshold": 0.1,
            }
        }
    }
    blocks = [{"text": "block", "block_bbox": [0, 0, 100, 20]}] * 5
    report = _make_report(config=config, blocks=blocks)
    report.passes_check()
    m6 = next(r for r in report.metric_records if r.metric_name == "coordinate_availability")
    assert m6.triggered is False


# ---------------------------------------------------------------------------
# Test 9: Metric 7 – references in body
# ---------------------------------------------------------------------------

def test_metric7_references_in_body_triggered():
    """Triggered when reference keywords appear in too many sentences."""
    config = {
        "quality_control": {
            "local_metrics": {
                "references_in_body_threshold": 0.05,
            }
        }
    }
    # 3 out of 5 sentences contain "references" => fraction = 0.6 > 0.05
    sentence_records = (
        [{"sentence": "See references for details."}] * 3
        + [{"sentence": "Normal sentence."}] * 2
    )
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
    sentence_records = (
        [{"sentence": "See references."}] * 1
        + [{"sentence": "Normal sentence."}] * 9
    )
    report = _make_report(config=config, sentence_records=sentence_records)
    report.passes_check()
    m7 = next(r for r in report.metric_records if r.metric_name == "references_in_body")
    assert m7.triggered is False


# ---------------------------------------------------------------------------
# Test 10: Metric 8 – weird char ratio
# ---------------------------------------------------------------------------

def test_metric8_weird_char_ratio_triggered():
    """Triggered when replacement character ratio exceeds threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "weird_char_ratio_threshold": 0.05,
            }
        }
    }
    # 10 non-ASCII chars out of 20 total => ratio = 0.5 > 0.05
    full_pdf_text = "normal" * 1 + "\xff" * 10 + "x" * 4
    report = _make_report(config=config, full_pdf_text=full_pdf_text)
    result = report.passes_check()
    assert result is False
    m8 = next(r for r in report.metric_records if r.metric_name == "weird_char_ratio")
    assert m8.triggered is True
    assert m8.computed_value > 0.05


def test_metric8_not_triggered_when_ratio_low():
    """Not triggered when weird char ratio is below threshold."""
    config = {
        "quality_control": {
            "local_metrics": {
                "weird_char_ratio_threshold": 0.05,
            }
        }
    }
    # All ASCII text => ratio = 0.0
    report = _make_report(config=config, full_pdf_text="All normal ASCII text here.")
    report.passes_check()
    m8 = next(r for r in report.metric_records if r.metric_name == "weird_char_ratio")
    assert m8.triggered is False


# ---------------------------------------------------------------------------
# Test 11: All 8 metrics produce ExtractionCoverageMetricRecord with correct fields
# ---------------------------------------------------------------------------

def test_all_eight_metrics_produce_correct_record_type():
    """All 8 metrics produce ExtractionCoverageMetricRecord with correct field names."""
    from quality_control import ExtractionCoverageMetricRecord
    import dataclasses

    report = _make_report()
    report.passes_check()

    assert len(report.metric_records) == 8
    expected_names = {
        "min_chars_per_page",
        "extraction_coverage_ratio",
        "long_sentence_fraction",
        "section_coverage",
        "caption_table_figure_coverage",
        "coordinate_availability",
        "references_in_body",
        "weird_char_ratio",
    }
    actual_names = {r.metric_name for r in report.metric_records}
    assert actual_names == expected_names

    for rec in report.metric_records:
        assert isinstance(rec, ExtractionCoverageMetricRecord)
        field_names = {f.name for f in dataclasses.fields(rec)}
        assert "metric_name" in field_names
        assert "computed_value" in field_names
        assert "threshold" in field_names
        assert "triggered" in field_names


# ---------------------------------------------------------------------------
# Test 12: passes_check() returns False when at least one metric is triggered
# ---------------------------------------------------------------------------

def test_passes_check_false_when_any_metric_triggered():
    """passes_check() returns False when at least one metric is triggered."""
    config = {
        "quality_control": {
            "local_metrics": {
                "min_chars_per_page": 100,
            }
        }
    }
    # Trigger metric 1 only
    report = _make_report(
        config=config,
        page_texts={0: "short"},
        native_page_texts={0: "x" * 200},
    )
    result = report.passes_check()
    assert result is False


# ---------------------------------------------------------------------------
# Test 13: Config values are used (not hardcoded)
# ---------------------------------------------------------------------------

def test_config_values_used_not_hardcoded():
    """Custom threshold values from config are respected."""
    # Use a very high threshold so the metric triggers on normal text
    config = {
        "quality_control": {
            "local_metrics": {
                "extraction_coverage_ratio_threshold": 0.99,
            }
        }
    }
    # ratio = 0.8 which is < 0.99 => should trigger
    report = _make_report(
        config=config,
        page_texts={0: "x" * 80},
        native_page_texts={0: "x" * 100},
    )
    result = report.passes_check()
    assert result is False
    m2 = next(r for r in report.metric_records if r.metric_name == "extraction_coverage_ratio")
    assert m2.triggered is True

    # Now use a low threshold so the same ratio does NOT trigger
    config_low = {
        "quality_control": {
            "local_metrics": {
                "extraction_coverage_ratio_threshold": 0.5,
            }
        }
    }
    report2 = _make_report(
        config=config_low,
        page_texts={0: "x" * 80},
        native_page_texts={0: "x" * 100},
    )
    report2.passes_check()
    m2b = next(r for r in report2.metric_records if r.metric_name == "extraction_coverage_ratio")
    assert m2b.triggered is False
