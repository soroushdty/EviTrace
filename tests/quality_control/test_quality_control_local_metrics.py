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
    """LocalQCReport must be importable from quality_control."""
    from quality_control import LocalQCReport  # noqa: F401
    assert LocalQCReport is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(**kwargs):
    """Create a LocalQCReport with sensible defaults, overriding with kwargs."""
    from quality_control import LocalQCReport
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
