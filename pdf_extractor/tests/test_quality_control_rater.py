"""
tests/test_quality_control_observer.py
=======================================
Tests for evi_trace/extraction/quality_control/observer.py.

Covers:
  - Property 5: Observer output contains all required fields with correct extractor name
  - Property 6: Observer attributes are driven entirely by config
  - Property 7: Observer output is deterministic and JSON-serializable
  - Unit tests for observer (4.4)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evi_trace.extraction.quality_control.rater import observe


# ---------------------------------------------------------------------------
# Shared test fixtures / helpers
# ---------------------------------------------------------------------------

_CANONICAL_ARTIFACT = {
    "document_id": "doc1",
    "grobid": {"id": "grobid_id_123", "content": "<root/>", "format": "tei_xml"},
    "pymupdf": {"id": "pymupdf_id_456", "content": "{}", "format": "json"},
}


def _make_config(attribute_names: list[str]) -> dict:
    return {"quality_control": {"rater": {"attributes": attribute_names}}}


# ---------------------------------------------------------------------------
# Property 5: Observer output contains all required fields with correct extractor name
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 5: Observer output contains all required fields with correct extractor name
@given(
    extractor_name=st.sampled_from(["grobid", "pymupdf"]),
    document_id=st.text(min_size=1),
    attribute_names=st.lists(st.text(min_size=1)),
)
@settings(max_examples=100)
def test_observer_required_fields_and_extractor_name(
    extractor_name: str, document_id: str, attribute_names: list[str]
):
    """**Validates: Requirements 3.2, 3.4**

    For any extractor name, canonical artifact dict, document_id, and config
    dict, observe SHALL return a dict containing all five required fields and
    extractor_name SHALL equal the input extractor name.
    """
    canonical_artifact = {
        "document_id": document_id,
        "grobid": {"id": "grobid_id_123", "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": "pymupdf_id_456", "content": "{}", "format": "json"},
    }
    config = _make_config(attribute_names)
    result = observe(extractor_name, canonical_artifact, document_id, config)

    # All five required fields must be present
    for field in ("extractor_name", "document_id", "attributes", "status", "provenance"):
        assert field in result, f"Missing required field: {field!r}"

    # extractor_name must equal the input
    assert result["extractor_name"] == extractor_name


# ---------------------------------------------------------------------------
# Property 6: Observer attributes are driven entirely by config
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 6: Observer attributes are driven entirely by config
@given(attribute_names=st.lists(st.text(min_size=1)))
@settings(max_examples=100)
def test_observer_attributes_driven_by_config(attribute_names: list[str]):
    """**Validates: Requirements 3.3**

    For any list of attribute name strings in config, the attributes dict in
    the returned Observation_Object SHALL contain exactly those keys (no more,
    no fewer) with None values.
    """
    config = _make_config(attribute_names)
    canonical_artifact = {
        "document_id": "doc1",
        "grobid": {"id": "g1", "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": "p1", "content": "{}", "format": "json"},
    }
    result = observe("grobid", canonical_artifact, "doc1", config)

    # Keys must match exactly (set comparison handles duplicates in input list)
    assert set(result["attributes"].keys()) == set(attribute_names)
    # All values must be None
    assert all(v is None for v in result["attributes"].values())


# ---------------------------------------------------------------------------
# Property 7: Observer output is deterministic and JSON-serializable
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 7: Observer output is deterministic and JSON-serializable
@given(
    extractor_name=st.sampled_from(["grobid", "pymupdf"]),
    document_id=st.text(min_size=1),
    attribute_names=st.lists(st.text(min_size=1)),
)
@settings(max_examples=100)
def test_observer_deterministic_and_json_serializable(
    extractor_name: str, document_id: str, attribute_names: list[str]
):
    """**Validates: Requirements 3.5**

    Calling observe twice with the same arguments SHALL return equal dicts,
    and json.dumps on the result SHALL succeed without raising.
    """
    config = _make_config(attribute_names)
    canonical_artifact = {
        "document_id": document_id,
        "grobid": {"id": "g1", "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": "p1", "content": "{}", "format": "json"},
    }
    result1 = observe(extractor_name, canonical_artifact, document_id, config)
    result2 = observe(extractor_name, canonical_artifact, document_id, config)

    assert result1 == result2
    json.dumps(result1)  # must not raise


# ---------------------------------------------------------------------------
# Unit tests: observer (4.4)
# ---------------------------------------------------------------------------

class TestObserver:
    def test_observe_produces_one_observation_per_extractor(self):
        """observe for grobid and pymupdf returns separate dicts with correct extractor_name."""
        config = _make_config(["length", "encoding"])
        grobid_result = observe("grobid", _CANONICAL_ARTIFACT, "doc1", config)
        pymupdf_result = observe("pymupdf", _CANONICAL_ARTIFACT, "doc1", config)

        assert grobid_result is not pymupdf_result
        assert grobid_result["extractor_name"] == "grobid"
        assert pymupdf_result["extractor_name"] == "pymupdf"

    def test_status_is_placeholder(self):
        """observe always sets status to 'placeholder'."""
        config = _make_config([])
        result = observe("grobid", _CANONICAL_ARTIFACT, "doc1", config)
        assert result["status"] == "placeholder"

    def test_provenance_references_correct_artifact(self):
        """provenance must reference the artifact id and format for the named extractor."""
        config = _make_config([])
        result = observe("grobid", _CANONICAL_ARTIFACT, "doc1", config)
        assert result["provenance"]["artifact_id"] == _CANONICAL_ARTIFACT["grobid"]["id"]
        assert result["provenance"]["artifact_format"] == "tei_xml"

    def test_provenance_references_pymupdf_artifact(self):
        """provenance for pymupdf extractor references the pymupdf artifact."""
        config = _make_config([])
        result = observe("pymupdf", _CANONICAL_ARTIFACT, "doc1", config)
        assert result["provenance"]["artifact_id"] == _CANONICAL_ARTIFACT["pymupdf"]["id"]
        assert result["provenance"]["artifact_format"] == "json"

    def test_document_id_forwarded(self):
        """document_id in the observation must equal the input document_id."""
        config = _make_config([])
        result = observe("grobid", _CANONICAL_ARTIFACT, "my_document_42", config)
        assert result["document_id"] == "my_document_42"

    def test_empty_attributes_when_config_list_is_empty(self):
        """When config attributes list is empty, attributes dict must be empty."""
        config = _make_config([])
        result = observe("grobid", _CANONICAL_ARTIFACT, "doc1", config)
        assert result["attributes"] == {}

    def test_observer_does_not_call_artifacts_module(self, monkeypatch):
        """observe must not call any function from the artifact_generator module."""
        import evi_trace.extraction.quality_control.artifact_generator as artifact_generator_mod

        called = []

        for fn_name in (
            "build_canonical_artifacts",
            "canonicalize_grobid_xml",
            "canonicalize_pymupdf_json",
            "export_canonical_artifacts",
        ):
            original = getattr(artifact_generator_mod, fn_name)

            def _spy(*args, fn=fn_name, orig=original, **kwargs):
                called.append(fn)
                return orig(*args, **kwargs)

            monkeypatch.setattr(artifact_generator_mod, fn_name, _spy)

        config = _make_config(["attr1"])
        observe("grobid", _CANONICAL_ARTIFACT, "doc1", config)

        assert called == [], (
            f"observe unexpectedly called artifact_generator functions: {called}"
        )
