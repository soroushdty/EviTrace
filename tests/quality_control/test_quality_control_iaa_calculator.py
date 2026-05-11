"""
tests/test_quality_control_investigator.py
==========================================
Tests for pdf_extractor/extraction/quality_control/investigator.py.

Covers:
  - Property 8: Investigator output contains all required fields and defers decision
  - Property 9: Investigator agreement metrics are driven entirely by config
  - Unit tests for investigator (5.3)
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quality_control.iaa_calculator import investigate


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_artifact(primary_id: str = "g1", secondary_id: str = "p1") -> dict:
    return {
        "id": primary_id,
        "document_id": "doc1",
        "content": "<root/>",
        "format": "tei_xml",
    }


def _make_config(metric_names: list[str]) -> dict:
    return {"quality_control": {"iaa_calculator": {"agreement_metrics": metric_names}}}


_PRIMARY_OBS = {"extractor_name": "primary", "status": "placeholder"}
_SECONDARY_OBS = {"extractor_name": "secondary", "status": "placeholder"}
_ARTIFACT = _make_artifact()


# ---------------------------------------------------------------------------
# Property 8: Investigator output contains all required fields and defers decision
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 8: Investigator output contains all required fields and defers decision
@given(
    metric_names=st.lists(st.text(min_size=1)),
    primary_id=st.text(min_size=1),
    secondary_id=st.text(min_size=1),
)
@settings(max_examples=20)
def test_investigator_required_fields_and_decision(
    metric_names: list[str], primary_id: str, secondary_id: str
):
    """**Validates: Requirements 4.2, 4.3, 4.5**

    For any pair of observation objects, pair of artifact dicts, and config
    dict, investigate SHALL return a dict containing all required fields and
    decision SHALL always equal "deferred_to_adjudicator".
    """
    primary_observation = {"extractor_name": "primary", "status": "placeholder"}
    secondary_observation = {"extractor_name": "secondary", "status": "placeholder"}
    primary_artifact = {"id": primary_id, "document_id": "doc1", "content": "<root/>", "format": "tei_xml"}
    secondary_artifact = {"id": secondary_id, "document_id": "doc1", "content": "{}", "format": "json"}
    config = _make_config(metric_names)

    result = investigate(
        primary_observation, secondary_observation, primary_artifact, secondary_artifact, config
    )

    required_fields = [
        "primary_threshold_checks",
        "secondary_threshold_checks",
        "agreement_metrics",
        "primary_observation_ref",
        "secondary_observation_ref",
        "primary_artifact_ref",
        "secondary_artifact_ref",
        "decision",
    ]
    for field in required_fields:
        assert field in result, f"Missing required field: {field!r}"

    assert result["decision"] == "deferred_to_adjudicator"


# ---------------------------------------------------------------------------
# Property 9: Investigator agreement metrics are driven entirely by config
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 9: Investigator agreement metrics are driven entirely by config
@given(metric_names=st.lists(st.text(min_size=1)))
@settings(max_examples=20)
def test_investigator_metrics_driven_by_config(metric_names: list[str]):
    """**Validates: Requirements 4.4**

    For any list of metric name strings in config, the agreement_metrics dict
    in the returned Investigator_Object SHALL contain exactly those keys (no
    more, no fewer) with None values.
    """
    config = _make_config(metric_names)
    primary_artifact = _make_artifact(primary_id="g1", secondary_id="p1")
    secondary_artifact = primary_artifact

    result = investigate({}, {}, primary_artifact, secondary_artifact, config)

    assert set(result["agreement_metrics"].keys()) == set(metric_names)
    assert all(v is None for v in result["agreement_metrics"].values())


# ---------------------------------------------------------------------------
# Unit tests: investigator (5.3)
# ---------------------------------------------------------------------------

class TestInvestigator:
    def test_observation_refs_are_input_objects(self):
        """primary_observation_ref and secondary_observation_ref must be the
        exact input observation dicts (identity, not copies)."""
        primary_obs = {"extractor_name": "primary", "status": "placeholder"}
        secondary_obs = {"extractor_name": "secondary", "status": "placeholder"}
        config = _make_config([])

        result = investigate(primary_obs, secondary_obs, _ARTIFACT, _ARTIFACT, config)

        assert result["primary_observation_ref"] is primary_obs
        assert result["secondary_observation_ref"] is secondary_obs

    def test_artifact_refs_are_correct_ids(self):
        """primary_artifact_ref == primary_artifact.get("id", "") and
        secondary_artifact_ref == secondary_artifact.get("id", "")."""
        primary_artifact = {"id": "primary-sha-abc", "document_id": "doc1"}
        secondary_artifact = {"id": "secondary-sha-xyz", "document_id": "doc1"}
        config = _make_config([])

        result = investigate(
            _PRIMARY_OBS, _SECONDARY_OBS, primary_artifact, secondary_artifact, config
        )

        assert result["primary_artifact_ref"] == "primary-sha-abc"
        assert result["secondary_artifact_ref"] == "secondary-sha-xyz"

    def test_decision_is_always_deferred(self):
        """decision is always 'deferred_to_adjudicator' regardless of inputs."""
        config = _make_config(["metric_a", "metric_b"])
        result = investigate(
            _PRIMARY_OBS, _SECONDARY_OBS, _ARTIFACT, _ARTIFACT, config
        )
        assert result["decision"] == "deferred_to_adjudicator"

    def test_threshold_checks_are_empty_dicts(self):
        """primary_threshold_checks and secondary_threshold_checks are both {}."""
        config = _make_config([])
        result = investigate(
            _PRIMARY_OBS, _SECONDARY_OBS, _ARTIFACT, _ARTIFACT, config
        )
        assert result["primary_threshold_checks"] == {}
        assert result["secondary_threshold_checks"] == {}

    def test_empty_metrics_when_config_list_is_empty(self):
        """When config agreement_metrics list is empty, agreement_metrics dict is {}."""
        config = _make_config([])
        result = investigate(
            _PRIMARY_OBS, _SECONDARY_OBS, _ARTIFACT, _ARTIFACT, config
        )
        assert result["agreement_metrics"] == {}

    def test_output_is_json_serializable(self):
        """investigate output must be JSON-serializable without custom encoders."""
        config = _make_config(["cosine_similarity", "jaccard"])
        result = investigate(
            _PRIMARY_OBS, _SECONDARY_OBS, _ARTIFACT, _ARTIFACT, config
        )
        # Must not raise
        json.dumps(result)

    def test_artifact_ref_missing_id_returns_empty_string(self):
        """When artifact dict has no 'id' key, artifact_ref falls back to ''."""
        config = _make_config([])
        artifact_no_id = {"document_id": "doc1", "content": "{}"}

        result = investigate(
            _PRIMARY_OBS, _SECONDARY_OBS, artifact_no_id, artifact_no_id, config
        )

        assert result["primary_artifact_ref"] == ""
        assert result["secondary_artifact_ref"] == ""
