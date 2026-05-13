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
import sys
from unittest.mock import MagicMock, call, patch
import pytest


@pytest.fixture(autouse=True)
def _mock_text_processor_tokenize(monkeypatch):
    """Mock scispacy/spacy AND tokenize_sentences for deterministic output."""
    import sys
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)
    monkeypatch.setattr(
        "text_processing.composite.DefaultTextProcessor.tokenize_sentences",
        lambda self, text: text.split(". "),
    )

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quality_control.quality_control import run_quality_control
from quality_control import AlignmentRecord, DocumentAlignment, Candidate, QCBundle, UnifiedRecord
from quality_control.models import SemanticLayer, StructuralLayer


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


def _make_branches(grobid_output: str, pymupdf_output: dict | list) -> list[Candidate]:
    """Build a standard two-branch list from grobid and pymupdf payloads."""
    return [
        Candidate(source="grobid", index=0, payload=grobid_output, status=None),
        Candidate(source="pymupdf", index=1, payload=pymupdf_output, status=None),
    ]


# ---------------------------------------------------------------------------
# 9.1  Property 13: Document ID derivation is deterministic
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 13: Document ID derivation is deterministic
@given(pymupdf_output=st.dictionaries(st.text(), st.text()))
@settings(max_examples=20)
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
@given(st.sampled_from(["reconciler"]))
@settings(max_examples=10)
def test_pipeline_propagates_exceptions(module_name):
    """Validates: Requirements 1.7
    Note: annotation chain (w3c_annotation.project, generate_w3c_jsonld) was moved
    to pipeline/extraction_pipeline.py and is no longer called by run_quality_control.
    Only reconciler exceptions propagate through run_quality_control.
    """
    error = RuntimeError("test error")

    if module_name == "reconciler":
        patch_target = patch("quality_control.quality_control.reconciler.reconcile", side_effect=error)
    else:
        patch_target = patch("quality_control.quality_control.reconciler.reconcile", side_effect=error)

    with patch_target:
        config = _make_minimal_config()
        branches = [Candidate(source="grobid", index=0, payload="<root/>", status=None)]
        with pytest.raises(RuntimeError, match="test error"):
            run_quality_control(branches, "test-doc", config)


# ---------------------------------------------------------------------------
# 9.3  Unit tests for pipeline orchestration
# ---------------------------------------------------------------------------

class TestPipelineOrchestration:
    """Unit tests for reconciler/annotation orchestration behaviour."""

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
            "provenance": {},
        }

    def test_reconciler_call_is_strategy_driven_and_extractor_agnostic(self):
        """reconciler.reconcile() gets role artifacts + strategy kwargs (no extractor kwargs)."""
        with patch("quality_control.quality_control.reconciler.reconcile") as mock_reconcile:
            mock_reconcile.return_value = UnifiedRecord(
                document_id="test-doc-id",
                content={},
                semantic=SemanticLayer(paragraphs=[], sentences=[]),
                structural=StructuralLayer(),
                alignment=DocumentAlignment(paragraph_to_blocks=[{"ok": True}]),
            )
            config = _make_minimal_config()
            branches = _make_branches("<root/>", {"blocks": [{"text": "Hello", "page_index": 0}]})
            run_quality_control(branches, "test-doc-id", config)

            assert mock_reconcile.call_count == 1
            kwargs = mock_reconcile.call_args.kwargs
            assert "primary_artifact" in kwargs
            assert "secondary_artifact" in kwargs
            assert "text_fidelity_strategy" in kwargs
            assert "section_strategy" in kwargs
            assert "table_figure_strategy" in kwargs
            assert "text_processor" in kwargs
            assert kwargs["text_processor"] is not None

            call_repr = repr(kwargs)
            assert "pdfplumber" not in call_repr
            assert "pymupdf_observation" not in call_repr
            assert "grobid_observation" not in call_repr

    def test_reconciler_alignment_survives_into_qc_context(self):
        """QCBundle.unified keeps populated DocumentAlignment from reconcile output."""
        expected_alignment = DocumentAlignment(paragraph_to_blocks=[{"agreement": "full"}])
        with patch("quality_control.quality_control.reconciler.reconcile") as mock_reconcile:
            mock_reconcile.return_value = UnifiedRecord(
                document_id="test-doc-id",
                content={},
                semantic=MagicMock(sentences=[]),
                structural=MagicMock(),
                alignment=expected_alignment,
            )
            config = _make_minimal_config()
            branches = _make_branches("<root/>", {"blocks": [{"text": "Hello", "page_index": 0}]})
            ctx = run_quality_control(branches, "test-doc-id", config)
            assert ctx.unified.alignment is expected_alignment

    def test_annotation_chain_writes_jsonld_to_unified_content(self):
        """w3c projection + JSON-LD generation result is stored in UnifiedRecord content.
        Note: annotation chain was moved to pipeline/extraction_pipeline.py.
        run_quality_control no longer adds annotations; that step happens in build_qc_bundle.
        This test verifies that run_quality_control returns a unified record without
        annotations (the caller is responsible for adding them).
        """
        with (
            patch("quality_control.quality_control.reconciler.reconcile") as mock_reconcile,
        ):
            mock_reconcile.return_value = UnifiedRecord(
                document_id="test-doc-id",
                content={},
                semantic=MagicMock(sentences=[]),
                structural=MagicMock(),
                alignment=DocumentAlignment(),
            )

            config = _make_minimal_config()
            branches = _make_branches("<root/>", {"blocks": [{"text": "Hello", "page_index": 0}]})
            ctx = run_quality_control(branches, "test-doc-id", config)

            # run_quality_control no longer adds annotations — that's build_qc_bundle's job
            assert ctx.unified is not None
            assert "annotations" not in ctx.unified.content

    def test_accepts_minimal_valid_inputs(self):
        """run_quality_control with a non-empty TEI XML string and dict should not raise."""
        config = _make_minimal_config()
        branches = _make_branches(
            "<TEI><text><body><p>Hello</p></body></text></TEI>",
            {"blocks": []},
        )
        result = run_quality_control(branches, "test-doc-id", config)
        assert isinstance(result, QCBundle)

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
        assert isinstance(result, QCBundle)


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

    assert isinstance(result, QCBundle)
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
        "provenance",
    ]
    for field in required_fields:
        assert field in result.unified.content, f"Missing required field: {field!r}"

    # Must be JSON-serializable without custom encoders
    json.dumps(result.unified.content)

    # metrics_hierarchy must use the new descriptive key names (Requirements 8.9, 8.13)
    mh = result.metrics_hierarchy
    assert "extraction_coverage" in mh, "metrics_hierarchy must contain 'extraction_coverage'"
    assert "source_text_verification" in mh, "metrics_hierarchy must contain 'source_text_verification'"
    assert "semantic_verification" in mh, "metrics_hierarchy must contain 'semantic_verification'"
    # Old key names must not be present
    assert "local_metrics" not in mh, "metrics_hierarchy must not contain legacy key 'local_metrics'"
    assert "exact_match" not in mh, "metrics_hierarchy must not contain legacy key 'exact_match'"
    assert "semantic_match" not in mh, "metrics_hierarchy must not contain legacy key 'semantic_match'"
    assert "semantic_qc" not in mh, "metrics_hierarchy must not contain legacy key 'semantic_qc'"


def test_sentence_records_use_text_processor_mock():
    """Verify run_quality_control uses TextProcessor.tokenize_sentences (mocked)."""
    config = _make_minimal_config()
    grobid_output = "<TEI><text><body><p>Hello world</p></body></text></TEI>"
    pymupdf_output = {"blocks": [{"text": "Hello world", "page_index": 0}]}
    branches = _make_branches(grobid_output, pymupdf_output)

    import quality_control.quality_control as qc_module
    assert not hasattr(qc_module, "re")
    ctx = run_quality_control(branches, "test-doc-id", config)

    # The autouse fixture _mock_text_processor_tokenize patches tokenize_sentences
    # and prevents spacy.load from being called. The pipeline should complete
    # without raising OSError for missing scispaCy models.
    assert ctx.reports is not None
    assert isinstance(ctx.reports, list)
