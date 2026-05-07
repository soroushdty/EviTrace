"""
Tests for the QC Pipeline orchestrator (quality_control.py).

Covers:
  - Property 13: Document ID derivation is deterministic
  - Property 14: Pipeline propagates sub-module exceptions
  - Unit tests for pipeline orchestration (call ordering, export flag, type errors)
  - Integration test for the full pipeline (no mocks)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quality_control.quality_control import run_quality_control
from quality_control import BranchOutput, QCContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_config() -> dict:
    return {
        "quality_control": {
            "artifact_generator": {"export_to_disk": False, "output_dir": "output/qc_artifacts"},
            "rater": {"attributes": []},
            "iaa_calculator": {"thresholds": {}, "agreement_metrics": []},
            "adjudicator": {"strategy": "placeholder"},
            "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
        }
    }


def _make_branches(grobid_output: str, pymupdf_output: dict | list) -> list[BranchOutput]:
    """Build a standard two-branch list from grobid and pymupdf payloads."""
    return [
        BranchOutput(extractor="grobid", branch=0, payload=grobid_output, status=None),
        BranchOutput(extractor="pymupdf", branch=1, payload=pymupdf_output, status=None),
    ]


# ---------------------------------------------------------------------------
# 9.1  Property 13: Document ID derivation is deterministic
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 13: Document ID derivation is deterministic
@given(pymupdf_output=st.dictionaries(st.text(), st.text()))
@settings(max_examples=100)
def test_document_id_derivation_deterministic(pymupdf_output):
    """Validates: Requirements 1.3"""
    grobid_output = "<root><body>test</body></root>"
    config = _make_minimal_config()
    branches = _make_branches(grobid_output, pymupdf_output)
    result1 = run_quality_control(branches, "test-doc-id", config)
    result2 = run_quality_control(branches, "test-doc-id", config)
    assert result1.unified.document_id == result2.unified.document_id


# ---------------------------------------------------------------------------
# 9.2  Property 14: Pipeline propagates sub-module exceptions
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 14: Pipeline propagates sub-module exceptions
@given(st.sampled_from(["artifact_generator", "rater", "iaa_calculator", "adjudicator"]))
@settings(max_examples=20)
def test_pipeline_propagates_exceptions(module_name):
    """Validates: Requirements 1.7"""
    error = RuntimeError("test error")

    with patch(
        f"quality_control.quality_control.{module_name}"
    ) as mock_mod:
        if module_name == "artifact_generator":
            mock_mod.build_canonical_artifacts.side_effect = error
        elif module_name == "rater":
            mock_mod.observe.side_effect = error
        elif module_name == "iaa_calculator":
            mock_mod.investigate.side_effect = error
        elif module_name == "adjudicator":
            mock_mod.adjudicate.side_effect = error

        config = _make_minimal_config()
        branches = [BranchOutput(extractor="grobid", branch=0, payload="<root/>", status=None)]
        with pytest.raises(RuntimeError, match="test error"):
            run_quality_control(branches, "test-doc", config)


# ---------------------------------------------------------------------------
# 9.3  Unit tests for pipeline orchestration
# ---------------------------------------------------------------------------

class TestPipelineOrchestration:
    """Unit tests that mock all sub-modules to verify orchestration behaviour."""

    def _make_mock_artifacts(self):
        """Return a minimal canonical artifacts dict."""
        return {
            "document_id": "test-doc-id",
            "grobid": {"id": "grobid-hash", "content": "<root/>", "format": "tei_xml"},
            "pymupdf": {"id": "pymupdf-hash", "content": "{}", "format": "json"},
        }

    def _make_mock_observation(self, extractor_name: str) -> dict:
        return {
            "extractor_name": extractor_name,
            "document_id": "test-doc-id",
            "attributes": {},
            "status": "placeholder",
            "provenance": {"artifact_id": f"{extractor_name}-hash", "artifact_format": "tei_xml"},
        }

    def _make_mock_investigator_object(self) -> dict:
        return {
            "grobid_threshold_checks": {},
            "pymupdf_threshold_checks": {},
            "agreement_metrics": {},
            "grobid_observation_ref": {},
            "pymupdf_observation_ref": {},
            "grobid_artifact_ref": "grobid-hash",
            "pymupdf_artifact_ref": "pymupdf-hash",
            "decision": "deferred_to_adjudicator",
        }

    def _make_mock_unified_output(self) -> dict:
        return {
            "document_id": "test-doc-id",
            "metadata": {},
            "pages": [],
            "segments": [],
            "annotations": [],
            "tables": [],
            "figures": [],
            "images": [],
            "exact_text": "",
            "geometry": {},
            "provenance": {},
            "observer_summary": {},
            "investigator_summary": {},
            "adjudication_status": "placeholder",
            "placeholder_notice": "placeholder",
        }

    def test_sub_module_call_ordering(self):
        """Verify call order: artifact_generator → rater × 2 → iaa_calculator → adjudicator."""
        canonical_arts = self._make_mock_artifacts()
        grobid_obs = self._make_mock_observation("grobid")
        pymupdf_obs = self._make_mock_observation("pymupdf")
        inv_obj = self._make_mock_investigator_object()
        unified = self._make_mock_unified_output()

        manager = MagicMock()

        with (
            patch("quality_control.quality_control.artifact_generator") as mock_arts,
            patch("quality_control.quality_control.rater") as mock_obs,
            patch("quality_control.quality_control.iaa_calculator") as mock_inv,
            patch("quality_control.quality_control.adjudicator") as mock_adj,
        ):
            mock_arts.build_canonical_artifacts.return_value = canonical_arts
            mock_obs.observe.side_effect = [grobid_obs, pymupdf_obs]
            mock_inv.investigate.return_value = inv_obj
            mock_adj.adjudicate.return_value = unified

            # Attach to manager to track cross-mock ordering
            manager.attach_mock(mock_arts.build_canonical_artifacts, "build_canonical_artifacts")
            manager.attach_mock(mock_obs.observe, "observe")
            manager.attach_mock(mock_inv.investigate, "investigate")
            manager.attach_mock(mock_adj.adjudicate, "adjudicate")

            config = _make_minimal_config()
            branches = _make_branches("<root/>", {})
            run_quality_control(branches, "test-doc-id", config)

            call_names = [c[0] for c in manager.mock_calls]
            assert call_names.index("build_canonical_artifacts") < call_names.index("observe")
            # observe is called twice
            observe_indices = [i for i, n in enumerate(call_names) if n == "observe"]
            assert len(observe_indices) == 2
            assert observe_indices[-1] < call_names.index("investigate")
            assert call_names.index("investigate") < call_names.index("adjudicate")

    def test_canonical_artifacts_passed_to_observer(self):
        """Rater.observe must receive canonical_artifacts, not native outputs."""
        canonical_arts = self._make_mock_artifacts()
        grobid_obs = self._make_mock_observation("grobid")
        pymupdf_obs = self._make_mock_observation("pymupdf")
        inv_obj = self._make_mock_investigator_object()
        unified = self._make_mock_unified_output()

        with (
            patch("quality_control.quality_control.artifact_generator") as mock_arts,
            patch("quality_control.quality_control.rater") as mock_obs,
            patch("quality_control.quality_control.iaa_calculator") as mock_inv,
            patch("quality_control.quality_control.adjudicator") as mock_adj,
        ):
            mock_arts.build_canonical_artifacts.return_value = canonical_arts
            mock_obs.observe.side_effect = [grobid_obs, pymupdf_obs]
            mock_inv.investigate.return_value = inv_obj
            mock_adj.adjudicate.return_value = unified

            config = _make_minimal_config()
            branches = _make_branches("<root/>", {"key": "val"})
            run_quality_control(branches, "test-doc-id", config)

            # Both observe calls should receive canonical_arts as second positional arg
            for c in mock_obs.observe.call_args_list:
                assert c.args[1] is canonical_arts

    def test_export_called_when_export_to_disk_true(self):
        """export_canonical_artifacts is called when export_to_disk=True."""
        canonical_arts = self._make_mock_artifacts()
        grobid_obs = self._make_mock_observation("grobid")
        pymupdf_obs = self._make_mock_observation("pymupdf")
        inv_obj = self._make_mock_investigator_object()
        unified = self._make_mock_unified_output()

        with (
            patch("quality_control.quality_control.artifact_generator") as mock_arts,
            patch("quality_control.quality_control.rater") as mock_obs,
            patch("quality_control.quality_control.iaa_calculator") as mock_inv,
            patch("quality_control.quality_control.adjudicator") as mock_adj,
        ):
            mock_arts.build_canonical_artifacts.return_value = canonical_arts
            mock_obs.observe.side_effect = [grobid_obs, pymupdf_obs]
            mock_inv.investigate.return_value = inv_obj
            mock_adj.adjudicate.return_value = unified

            config = _make_minimal_config()
            config["quality_control"]["artifact_generator"]["export_to_disk"] = True
            config["quality_control"]["artifact_generator"]["output_dir"] = "/tmp/qc_test"

            branches = _make_branches("<root/>", {})
            run_quality_control(branches, "test-doc-id", config)

            mock_arts.export_canonical_artifacts.assert_called_once_with(
                canonical_arts, "/tmp/qc_test"
            )

    def test_export_not_called_when_export_to_disk_false(self):
        """export_canonical_artifacts is NOT called when export_to_disk=False."""
        canonical_arts = self._make_mock_artifacts()
        grobid_obs = self._make_mock_observation("grobid")
        pymupdf_obs = self._make_mock_observation("pymupdf")
        inv_obj = self._make_mock_investigator_object()
        unified = self._make_mock_unified_output()

        with (
            patch("quality_control.quality_control.artifact_generator") as mock_arts,
            patch("quality_control.quality_control.rater") as mock_obs,
            patch("quality_control.quality_control.iaa_calculator") as mock_inv,
            patch("quality_control.quality_control.adjudicator") as mock_adj,
        ):
            mock_arts.build_canonical_artifacts.return_value = canonical_arts
            mock_obs.observe.side_effect = [grobid_obs, pymupdf_obs]
            mock_inv.investigate.return_value = inv_obj
            mock_adj.adjudicate.return_value = unified

            config = _make_minimal_config()  # export_to_disk=False by default

            branches = _make_branches("<root/>", {})
            run_quality_control(branches, "test-doc-id", config)

            mock_arts.export_canonical_artifacts.assert_not_called()

    def test_accepts_minimal_valid_inputs(self):
        """run_quality_control with a non-empty TEI XML string and dict should not raise."""
        config = _make_minimal_config()
        branches = _make_branches(
            "<TEI><text><body><p>Hello</p></body></text></TEI>",
            {"blocks": []},
        )
        result = run_quality_control(branches, "test-doc-id", config)
        assert isinstance(result, QCContext)

    def test_type_error_for_branches_not_a_list(self):
        """run_quality_control('not a list', 'doc-id', config) must raise TypeError."""
        config = _make_minimal_config()
        with pytest.raises(TypeError):
            run_quality_control("not a list", "doc-id", config)

    def test_type_error_for_empty_document_id(self):
        """run_quality_control([], '', config) must raise TypeError."""
        config = _make_minimal_config()
        with pytest.raises(TypeError):
            run_quality_control([], "", config)

    def test_type_error_for_none_document_id(self):
        """run_quality_control([], None, config) must raise TypeError."""
        config = _make_minimal_config()
        with pytest.raises(TypeError):
            run_quality_control([], None, config)

    def test_explicit_document_id_is_used(self):
        """When document_id is provided, it must appear in the Unified Output."""
        config = _make_minimal_config()
        branches = _make_branches(
            "<TEI><text><body><p>Hello</p></body></text></TEI>",
            {},
        )
        result = run_quality_control(branches, "my-explicit-doc-id", config)
        assert result.unified.document_id == "my-explicit-doc-id"

    def test_list_pymupdf_output_accepted(self):
        """pymupdf_output as a list (not just dict) must be accepted without TypeError."""
        config = _make_minimal_config()
        branches = _make_branches(
            "<TEI><text><body><p>Hello</p></body></text></TEI>",
            [{"text": "Hello", "page": 0}],
        )
        result = run_quality_control(branches, "test-doc-id", config)
        assert isinstance(result, QCContext)


# ---------------------------------------------------------------------------
# 9.4  Integration test for the full pipeline (no mocks)
# ---------------------------------------------------------------------------

def test_full_pipeline_integration():
    """Call run_quality_control with real inputs; assert structure and JSON-serializability."""
    grobid_output = "<TEI><text><body><p>Hello world</p></body></text></TEI>"
    pymupdf_output = {"blocks": [{"text": "Hello world", "page": 0}]}
    config = _make_minimal_config()

    branches = _make_branches(grobid_output, pymupdf_output)
    result = run_quality_control(branches, "integration-test-doc-id", config)

    assert isinstance(result, QCContext)
    assert result.unified is not None

    required_fields = [
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
    for field in required_fields:
        assert field in result.unified.content, f"Missing required field: {field!r}"

    # Must be JSON-serializable without custom encoders
    json.dumps(result.unified.content)
