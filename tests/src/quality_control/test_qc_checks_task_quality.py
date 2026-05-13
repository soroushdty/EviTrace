"""Unit tests for build_task_quality_scaffold JSON-serializability.

# Feature: qc-migration, Property 8: build_task_quality_scaffold is always JSON-serializable

Validates: Requirements 7.5
"""
from __future__ import annotations

import json

import pytest

from quality_control.checks.task_quality import build_task_quality_scaffold

_EXPECTED_METRIC_KEYS = [
    "field_recall",
    "critical_field_recall",
    "evidence_validity",
    "evidence_compactness",
    "cost_reduction",
    "manual_qc_rate",
    "interobserver_agreement",
    "pipeline_agreement",
]


def test_result_is_json_serializable_without_custom_encoder():
    """json.dumps(result) succeeds without error and without a custom encoder."""
    result = build_task_quality_scaffold()
    # Must not raise; no cls= or default= argument allowed
    serialized = json.dumps(result)
    assert isinstance(serialized, str)
    assert len(serialized) > 0


def test_all_eight_metric_keys_present():
    """All eight placeholder metric keys are present in the returned dict."""
    result = build_task_quality_scaffold()
    for key in _EXPECTED_METRIC_KEYS:
        assert key in result, f"Expected metric key '{key}' missing from scaffold"


def test_details_is_non_empty_string():
    """Top-level 'details' key is a non-empty string."""
    result = build_task_quality_scaffold()
    assert "details" in result, "'details' key missing from scaffold"
    assert isinstance(result["details"], str), "'details' must be a string"
    assert len(result["details"]) > 0, "'details' must be a non-empty string"


def test_top_level_status_key_present():
    """Top-level 'status' key is present."""
    result = build_task_quality_scaffold()
    assert "status" in result, "'status' key missing from scaffold"


def test_each_metric_value_has_status_and_value_keys():
    """Each metric entry contains both 'status' and 'value' keys."""
    result = build_task_quality_scaffold()
    for key in _EXPECTED_METRIC_KEYS:
        entry = result[key]
        assert isinstance(entry, dict), f"Metric '{key}' entry must be a dict"
        assert "status" in entry, f"Metric '{key}' entry missing 'status' key"
        assert "value" in entry, f"Metric '{key}' entry missing 'value' key"


def test_each_metric_value_is_none():
    """Each metric 'value' is None (JSON null)."""
    result = build_task_quality_scaffold()
    for key in _EXPECTED_METRIC_KEYS:
        assert result[key]["value"] is None, (
            f"Metric '{key}' value must be None (JSON null), got {result[key]['value']!r}"
        )


def test_json_round_trip_preserves_null_values():
    """After JSON round-trip, metric values remain null (None)."""
    result = build_task_quality_scaffold()
    serialized = json.dumps(result)
    deserialized = json.loads(serialized)
    for key in _EXPECTED_METRIC_KEYS:
        assert deserialized[key]["value"] is None, (
            f"After JSON round-trip, metric '{key}' value must be null"
        )


def test_top_level_status_is_valid_scaffold_value():
    """Top-level 'status' is either 'not_computed' or 'scaffolded'."""
    result = build_task_quality_scaffold()
    assert result["status"] in {"not_computed", "scaffolded"}, (
        f"Top-level 'status' must be 'not_computed' or 'scaffolded', got {result['status']!r}"
    )


def test_each_metric_status_is_valid_scaffold_value():
    """Each metric 'status' is either 'scaffolded' or 'not_computed'."""
    result = build_task_quality_scaffold()
    for key in _EXPECTED_METRIC_KEYS:
        assert result[key]["status"] in {"scaffolded", "not_computed"}, (
            f"Metric '{key}' status must be 'scaffolded' or 'not_computed', "
            f"got {result[key]['status']!r}"
        )
