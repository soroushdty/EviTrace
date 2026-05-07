"""
Tests for evi_trace/extraction/quality_control/repair.py — Unified Output production.

Covers:
  - Property 11: Unified Output contains all required top-level fields with correct status
  - Property 12: Unified Output is JSON-serializable without custom encoders
  - Unit tests for PLACEHOLDER_NOTICE, document_id, provenance, and structural contract
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evi_trace.extraction.quality_control.reconciler import PLACEHOLDER_NOTICE, reconcile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = [
    "document_id",
    "metadata",
    "pages",
    "segments",
    "annotations",
    "tables",
    "figures",
    "images",
    "exact_text",
    "geometry",
    "provenance",
    "observer_summary",
    "investigator_summary",
    "adjudication_status",
    "placeholder_notice",
]

REQUIRED_PROVENANCE_KEYS = [
    "grobid_artifact_id",
    "pymupdf_artifact_id",
    "grobid_observation",
    "pymupdf_observation",
    "investigator_object",
]


def _make_grobid_artifact(document_id: str, grobid_id: str) -> dict:
    return {
        "document_id": document_id,
        "grobid": {"id": grobid_id, "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": "p1", "content": "{}", "format": "json"},
    }


def _make_pymupdf_artifact(document_id: str, pymupdf_id: str) -> dict:
    return {
        "document_id": document_id,
        "grobid": {"id": "g1", "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": pymupdf_id, "content": "{}", "format": "json"},
    }


# ---------------------------------------------------------------------------
# Property 11: Unified Output contains all required top-level fields with correct status
# Feature: quality-control-module, Property 11: Unified Output contains all required top-level fields with correct status
# ---------------------------------------------------------------------------


@given(
    document_id=st.text(min_size=1),
    grobid_id=st.text(min_size=1),
    pymupdf_id=st.text(min_size=1),
)
@settings(max_examples=100)
def test_unified_output_required_fields_and_status(
    document_id: str, grobid_id: str, pymupdf_id: str
) -> None:
    """**Validates: Requirements 6.3, 6.4**"""
    grobid_artifact = _make_grobid_artifact(document_id, grobid_id)
    pymupdf_artifact = _make_pymupdf_artifact(document_id, pymupdf_id)
    config = {"quality_control": {}}

    result = reconcile(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)

    for field in REQUIRED_FIELDS:
        assert field in result, f"Missing required field: {field!r}"

    assert result["adjudication_status"] == "placeholder"
    assert result["placeholder_notice"] == PLACEHOLDER_NOTICE


# ---------------------------------------------------------------------------
# Property 12: Unified Output is JSON-serializable without custom encoders
# Feature: quality-control-module, Property 12: Unified Output is JSON-serializable without custom encoders
# ---------------------------------------------------------------------------


@given(
    document_id=st.text(min_size=1),
    grobid_id=st.text(min_size=1),
    pymupdf_id=st.text(min_size=1),
)
@settings(max_examples=100)
def test_unified_output_json_serializable(
    document_id: str, grobid_id: str, pymupdf_id: str
) -> None:
    """**Validates: Requirements 6.6**"""
    grobid_artifact = _make_grobid_artifact(document_id, grobid_id)
    pymupdf_artifact = _make_pymupdf_artifact(document_id, pymupdf_id)
    config = {"quality_control": {}}

    result = reconcile(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)
    # Must not raise
    json.dumps(result)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestRepair:
    def test_placeholder_notice_is_non_empty_string(self) -> None:
        assert isinstance(PLACEHOLDER_NOTICE, str)
        assert len(PLACEHOLDER_NOTICE) > 0

    def test_document_id_from_grobid_artifact(self) -> None:
        """document_id in Unified Output matches grobid_artifact["document_id"]."""
        grobid_artifact = _make_grobid_artifact("doc-abc-123", "gid-1")
        pymupdf_artifact = _make_pymupdf_artifact("doc-abc-123", "pid-1")
        config = {"quality_control": {}}

        result = reconcile(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)

        assert result["document_id"] == "doc-abc-123"

    def test_provenance_contains_all_five_references(self) -> None:
        """provenance has all five required keys."""
        grobid_artifact = _make_grobid_artifact("doc-xyz", "gid-42")
        pymupdf_artifact = _make_pymupdf_artifact("doc-xyz", "pid-99")
        grobid_obs = {"extractor_name": "grobid", "status": "placeholder"}
        pymupdf_obs = {"extractor_name": "pymupdf", "status": "placeholder"}
        inv_obj = {"decision": "deferred_to_adjudicator"}
        config = {"quality_control": {}}

        result = reconcile(
            grobid_artifact, pymupdf_artifact, grobid_obs, pymupdf_obs, inv_obj, config
        )

        provenance = result["provenance"]
        for key in REQUIRED_PROVENANCE_KEYS:
            assert key in provenance, f"Missing provenance key: {key!r}"

        assert provenance["grobid_artifact_id"] == "gid-42"
        assert provenance["pymupdf_artifact_id"] == "pid-99"
        assert provenance["grobid_observation"] is grobid_obs
        assert provenance["pymupdf_observation"] is pymupdf_obs
        assert provenance["investigator_object"] is inv_obj

    def test_reconcile_is_only_producer_of_unified_output(self) -> None:
        """Structural test: reconcile returns a dict with all 15 required fields."""
        grobid_artifact = _make_grobid_artifact("doc-struct", "gid-s")
        pymupdf_artifact = _make_pymupdf_artifact("doc-struct", "pid-s")
        config = {"quality_control": {}}

        result = reconcile(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)

        assert isinstance(result, dict)
        for field in REQUIRED_FIELDS:
            assert field in result, f"Missing required field: {field!r}"
