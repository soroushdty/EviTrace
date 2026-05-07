"""
tests/test_qc_models.py
=======================
Tests for LocalQCMetricRecord dataclass in evi_trace/extraction/quality_control/models.py.

Covers:
  - Requirements 13.11, 14.4
  - Import succeeds from the public quality_control package
  - Instantiation with valid field values
  - Field type annotations match design spec
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Import test (TDD: this test fails until LocalQCMetricRecord is added)
# ---------------------------------------------------------------------------

def test_local_qc_metric_record_importable():
    """LocalQCMetricRecord must be importable from the public package."""
    from evi_trace.extraction.quality_control import LocalQCMetricRecord  # noqa: F401


def test_local_qc_metric_record_in_all():
    """LocalQCMetricRecord must appear in __all__ of the quality_control package."""
    import evi_trace.extraction.quality_control as qc
    assert "LocalQCMetricRecord" in qc.__all__


# ---------------------------------------------------------------------------
# Instantiation tests
# ---------------------------------------------------------------------------

def test_local_qc_metric_record_basic_instantiation():
    """Creates a valid instance with float computed_value and float threshold."""
    from evi_trace.extraction.quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="min_chars_per_page",
        computed_value=0.5,
        threshold=0.7,
        triggered=False,
    )
    assert rec.metric_name == "min_chars_per_page"
    assert rec.computed_value == 0.5
    assert rec.threshold == 0.7
    assert rec.triggered is False


def test_local_qc_metric_record_int_values():
    """computed_value and threshold accept int values."""
    from evi_trace.extraction.quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="page_count",
        computed_value=3,
        threshold=1,
        triggered=True,
    )
    assert rec.computed_value == 3
    assert rec.threshold == 1
    assert rec.triggered is True


def test_local_qc_metric_record_bool_computed_value():
    """computed_value accepts bool (boolean checks)."""
    from evi_trace.extraction.quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="has_text",
        computed_value=True,
        threshold=None,
        triggered=False,
    )
    assert rec.computed_value is True
    assert rec.threshold is None


def test_local_qc_metric_record_none_threshold():
    """threshold can be None for boolean checks."""
    from evi_trace.extraction.quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="weird_char_ratio",
        computed_value=0.02,
        threshold=None,
        triggered=False,
    )
    assert rec.threshold is None


def test_local_qc_metric_record_triggered_true():
    """triggered=True when metric fires (issue detected)."""
    from evi_trace.extraction.quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="weird_char_ratio",
        computed_value=0.9,
        threshold=0.3,
        triggered=True,
    )
    assert rec.triggered is True


# ---------------------------------------------------------------------------
# Field annotation tests
# ---------------------------------------------------------------------------

def test_local_qc_metric_record_field_annotations():
    """Verify field names exist as expected on the dataclass."""
    from evi_trace.extraction.quality_control import LocalQCMetricRecord
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(LocalQCMetricRecord)}
    assert "metric_name" in field_names
    assert "computed_value" in field_names
    assert "threshold" in field_names
    assert "triggered" in field_names


def test_local_qc_metric_record_is_dataclass():
    """LocalQCMetricRecord must be a dataclass."""
    from evi_trace.extraction.quality_control import LocalQCMetricRecord
    import dataclasses

    assert dataclasses.is_dataclass(LocalQCMetricRecord)
