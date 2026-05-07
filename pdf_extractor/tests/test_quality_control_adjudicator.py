"""
Tests for pdf_extractor/extraction/quality_control/adjudicator.py — quality evaluation and decision-making.

Covers:
  - Property 10: Adjudicator evaluates quality and delegates to Repair
  - Unit tests verifying adjudicate evaluates quality and calls repair.reconcile with decisions
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pdf_extractor.extraction.quality_control.adjudicator import adjudicate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# Property 10: Adjudicator evaluates quality and delegates to Repair
# Feature: quality-control-module, Property 10: Adjudicator evaluates quality and delegates to Repair
# ---------------------------------------------------------------------------


@given(
    document_id=st.text(min_size=1),
    grobid_id=st.text(min_size=1),
    pymupdf_id=st.text(min_size=1),
)
@settings(max_examples=100)
def test_adjudicator_evaluates_and_delegates(
    document_id: str, grobid_id: str, pymupdf_id: str
) -> None:
    """**Validates: Requirements 5.2**
    
    Adjudicator evaluates quality of both extractors and delegates to Repair
    with adjudication decisions.
    """
    known_result = {"adjudication_status": "accepted_pymupdf", "document_id": document_id}

    with patch(
        "pdf_extractor.extraction.quality_control.adjudicator.reconciler.reconcile",
        return_value=known_result,
    ) as mock_reconcile:
        grobid_artifact = _make_grobid_artifact(document_id, grobid_id)
        pymupdf_artifact = _make_pymupdf_artifact(document_id, pymupdf_id)
        config = {"quality_control": {}}

        result = adjudicate(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)

        # Result should be what repair.reconcile returned
        assert result is known_result
        
        # Verify repair.reconcile was called with adjudication decisions
        mock_reconcile.assert_called_once()
        call_args = mock_reconcile.call_args[0]
        
        # 6th argument should be adjudication_decisions dict
        adjudication_decisions = call_args[5]
        assert isinstance(adjudication_decisions, dict)
        assert "primary_extractor" in adjudication_decisions
        assert "confidence" in adjudication_decisions


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestAdjudicator:
    def test_adjudicate_calls_repair_reconcile_exactly_once(self) -> None:
        """repair.reconcile is called exactly once when adjudicate is invoked."""
        grobid_artifact = _make_grobid_artifact("doc-1", "gid-1")
        pymupdf_artifact = _make_pymupdf_artifact("doc-1", "pid-1")
        grobid_obs = {"extractor_name": "grobid", "status": "placeholder"}
        pymupdf_obs = {"extractor_name": "pymupdf", "status": "placeholder"}
        inv_obj = {"decision": "deferred_to_adjudicator"}
        config = {"quality_control": {}}

        sentinel = {"adjudication_status": "placeholder", "document_id": "doc-1"}

        with patch(
            "pdf_extractor.extraction.quality_control.adjudicator.reconciler.reconcile",
            return_value=sentinel,
        ) as mock_reconcile:
            result = adjudicate(
                grobid_artifact, pymupdf_artifact, grobid_obs, pymupdf_obs, inv_obj, config
            )

            mock_reconcile.assert_called_once()
            assert result is sentinel

    def test_adjudicate_evaluates_quality_and_makes_decisions(self) -> None:
        """adjudicate evaluates quality and passes adjudication decisions to repair.reconcile."""
        grobid_artifact = _make_grobid_artifact("doc-2", "gid-2")
        pymupdf_artifact = _make_pymupdf_artifact("doc-2", "pid-2")
        grobid_obs = {"extractor_name": "grobid"}
        pymupdf_obs = {"extractor_name": "pymupdf"}
        inv_obj = {"decision": "deferred_to_adjudicator"}
        config = {"quality_control": {"adjudicator": {"strategy": "placeholder"}}}

        with patch(
            "pdf_extractor.extraction.quality_control.adjudicator.reconciler.reconcile",
            return_value={},
        ) as mock_reconcile:
            adjudicate(
                grobid_artifact, pymupdf_artifact, grobid_obs, pymupdf_obs, inv_obj, config
            )

            # Verify repair.reconcile was called with adjudication decisions
            call_args = mock_reconcile.call_args
            assert call_args[0][0] is grobid_artifact
            assert call_args[0][1] is pymupdf_artifact
            assert call_args[0][2] is grobid_obs
            assert call_args[0][3] is pymupdf_obs
            assert call_args[0][4] is inv_obj
            
            # The 6th argument should be adjudication_decisions dict (not config)
            adjudication_decisions = call_args[0][5]
            assert isinstance(adjudication_decisions, dict)
            assert "primary_extractor" in adjudication_decisions
            assert "confidence" in adjudication_decisions
            assert "rationale" in adjudication_decisions
            
            # The 7th argument should be config
            assert call_args[0][6] is config
