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

def _make_artifact(grobid_id: str = "g1", pymupdf_id: str = "p1") -> dict:
    return {
        "document_id": "doc1",
        "grobid": {"id": grobid_id, "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": pymupdf_id, "content": "{}", "format": "json"},
    }


def _make_config(metric_names: list[str]) -> dict:
    return {"quality_control": {"iaa_calculator": {"agreement_metrics": metric_names}}}


_GROBID_OBS = {"extractor_name": "grobid", "status": "placeholder"}
_PYMUPDF_OBS = {"extractor_name": "pymupdf", "status": "placeholder"}
_ARTIFACT = _make_artifact()


# ---------------------------------------------------------------------------
# Property 8: Investigator output contains all required fields and defers decision
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 8: Investigator output contains all required fields and defers decision
@given(
    metric_names=st.lists(st.text(min_size=1)),
    grobid_id=st.text(min_size=1),
    pymupdf_id=st.text(min_size=1),
)
@settings(max_examples=100)
def test_investigator_required_fields_and_decision(
    metric_names: list[str], grobid_id: str, pymupdf_id: str
):
    """**Validates: Requirements 4.2, 4.3, 4.5**

    For any pair of observation objects, pair of artifact dicts, and config
    dict, investigate SHALL return a dict containing all required fields and
    decision SHALL always equal "deferred_to_adjudicator".
    """
    grobid_observation = {"extractor_name": "grobid", "status": "placeholder"}
    pymupdf_observation = {"extractor_name": "pymupdf", "status": "placeholder"}
    grobid_artifact = {
        "document_id": "doc1",
        "grobid": {"id": grobid_id, "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": "p1", "content": "{}", "format": "json"},
    }
    pymupdf_artifact = {
        "document_id": "doc1",
        "grobid": {"id": "g1", "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": pymupdf_id, "content": "{}", "format": "json"},
    }
    config = _make_config(metric_names)

    result = investigate(
        grobid_observation, pymupdf_observation, grobid_artifact, pymupdf_artifact, config
    )

    required_fields = [
        "grobid_threshold_checks",
        "pymupdf_threshold_checks",
        "agreement_metrics",
        "grobid_observation_ref",
        "pymupdf_observation_ref",
        "grobid_artifact_ref",
        "pymupdf_artifact_ref",
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
@settings(max_examples=100)
def test_investigator_metrics_driven_by_config(metric_names: list[str]):
    """**Validates: Requirements 4.4**

    For any list of metric name strings in config, the agreement_metrics dict
    in the returned Investigator_Object SHALL contain exactly those keys (no
    more, no fewer) with None values.
    """
    config = _make_config(metric_names)
    grobid_artifact = _make_artifact(grobid_id="g1", pymupdf_id="p1")
    pymupdf_artifact = grobid_artifact

    result = investigate({}, {}, grobid_artifact, pymupdf_artifact, config)

    assert set(result["agreement_metrics"].keys()) == set(metric_names)
    assert all(v is None for v in result["agreement_metrics"].values())


# ---------------------------------------------------------------------------
# Unit tests: investigator (5.3)
# ---------------------------------------------------------------------------

class TestInvestigator:
    def test_observation_refs_are_input_objects(self):
        """grobid_observation_ref and pymupdf_observation_ref must be the
        exact input observation dicts (identity, not copies)."""
        grobid_obs = {"extractor_name": "grobid", "status": "placeholder"}
        pymupdf_obs = {"extractor_name": "pymupdf", "status": "placeholder"}
        config = _make_config([])

        result = investigate(grobid_obs, pymupdf_obs, _ARTIFACT, _ARTIFACT, config)

        assert result["grobid_observation_ref"] is grobid_obs
        assert result["pymupdf_observation_ref"] is pymupdf_obs

    def test_artifact_refs_are_correct_ids(self):
        """grobid_artifact_ref == grobid_artifact["grobid"]["id"] and
        pymupdf_artifact_ref == pymupdf_artifact["pymupdf"]["id"]."""
        grobid_artifact = _make_artifact(grobid_id="grobid-sha-abc", pymupdf_id="x")
        pymupdf_artifact = _make_artifact(grobid_id="y", pymupdf_id="pymupdf-sha-xyz")
        config = _make_config([])

        result = investigate(
            _GROBID_OBS, _PYMUPDF_OBS, grobid_artifact, pymupdf_artifact, config
        )

        assert result["grobid_artifact_ref"] == "grobid-sha-abc"
        assert result["pymupdf_artifact_ref"] == "pymupdf-sha-xyz"

    def test_decision_is_always_deferred(self):
        """decision is always 'deferred_to_adjudicator' regardless of inputs."""
        config = _make_config(["metric_a", "metric_b"])
        result = investigate(
            _GROBID_OBS, _PYMUPDF_OBS, _ARTIFACT, _ARTIFACT, config
        )
        assert result["decision"] == "deferred_to_adjudicator"

    def test_threshold_checks_are_empty_dicts(self):
        """grobid_threshold_checks and pymupdf_threshold_checks are both {}."""
        config = _make_config([])
        result = investigate(
            _GROBID_OBS, _PYMUPDF_OBS, _ARTIFACT, _ARTIFACT, config
        )
        assert result["grobid_threshold_checks"] == {}
        assert result["pymupdf_threshold_checks"] == {}

    def test_empty_metrics_when_config_list_is_empty(self):
        """When config agreement_metrics list is empty, agreement_metrics dict is {}."""
        config = _make_config([])
        result = investigate(
            _GROBID_OBS, _PYMUPDF_OBS, _ARTIFACT, _ARTIFACT, config
        )
        assert result["agreement_metrics"] == {}

    def test_output_is_json_serializable(self):
        """investigate output must be JSON-serializable without custom encoders."""
        config = _make_config(["cosine_similarity", "jaccard"])
        result = investigate(
            _GROBID_OBS, _PYMUPDF_OBS, _ARTIFACT, _ARTIFACT, config
        )
        # Must not raise
        json.dumps(result)
