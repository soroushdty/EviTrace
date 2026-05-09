"""
Tests for quality_control/reconciler.py — Unified Output production.

Covers:
  - Property 11: Unified Output contains all required top-level fields with correct status
  - Property 12: Unified Output is JSON-serializable without custom encoders
  - Unit tests for PLACEHOLDER_NOTICE, document_id, provenance, and structural contract
  - Tasks 9.1–9.3: concern routing, strategy injection, no extractor literals
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quality_control.models import (
    AlignmentMap,
    SemanticLayer,
    StructuralLayer,
    UnifiedRecord,
)
from quality_control.reconciler import PLACEHOLDER_NOTICE, reconcile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_PROVENANCE_KEYS = [
    "grobid_artifact_id",
    "pymupdf_artifact_id",
    "grobid_observation",
    "pymupdf_observation",
    "investigator_object",
]


def _make_primary_artifact(document_id: str, primary_id: str) -> dict:
    return {
        "document_id": document_id,
        "grobid": {"id": primary_id, "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": "p1", "content": "{}", "format": "json"},
    }


def _make_secondary_artifact(document_id: str, secondary_id: str) -> dict:
    return {
        "document_id": document_id,
        "grobid": {"id": "g1", "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": secondary_id, "content": "{}", "format": "json"},
    }


# Keep these as aliases for backward-compat test names
def _make_grobid_artifact(document_id: str, grobid_id: str) -> dict:
    return _make_primary_artifact(document_id, grobid_id)


def _make_pymupdf_artifact(document_id: str, pymupdf_id: str) -> dict:
    return _make_secondary_artifact(document_id, pymupdf_id)


def _make_artifact_with_blocks(document_id: str, blocks: list[dict]) -> dict:
    """Make a primary artifact that contains directly-accessible blocks."""
    return {
        "document_id": document_id,
        "blocks": blocks,
        "grobid": {"id": "g1", "content": "<root/>", "format": "tei_xml"},
        "pymupdf": {"id": "p1", "content": "{}", "format": "json"},
    }


# ---------------------------------------------------------------------------
# Property 11 (backward compat): placeholder path returns UnifiedRecord
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
    """**Validates: Requirements 6.3, 6.4** — placeholder path returns UnifiedRecord."""
    grobid_artifact = _make_grobid_artifact(document_id, grobid_id)
    pymupdf_artifact = _make_pymupdf_artifact(document_id, pymupdf_id)
    config = {"quality_control": {}}

    result = reconcile(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)

    assert isinstance(result, UnifiedRecord)
    assert result.document_id == document_id
    assert result.content.get("adjudication_status") == "placeholder"
    assert result.content.get("placeholder_notice") == PLACEHOLDER_NOTICE


# ---------------------------------------------------------------------------
# Property 12 (backward compat): content dict is JSON-serializable
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
    """**Validates: Requirements 6.6** — content dict is JSON-serializable."""
    grobid_artifact = _make_grobid_artifact(document_id, grobid_id)
    pymupdf_artifact = _make_pymupdf_artifact(document_id, pymupdf_id)
    config = {"quality_control": {}}

    result = reconcile(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)
    # content must not raise
    json.dumps(result.content)


# ---------------------------------------------------------------------------
# Unit tests (backward compat)
# ---------------------------------------------------------------------------


class TestRepair:
    def test_placeholder_notice_is_non_empty_string(self) -> None:
        assert isinstance(PLACEHOLDER_NOTICE, str)
        assert len(PLACEHOLDER_NOTICE) > 0

    def test_document_id_from_grobid_artifact(self) -> None:
        """document_id in UnifiedRecord matches primary_artifact['document_id']."""
        grobid_artifact = _make_grobid_artifact("doc-abc-123", "gid-1")
        pymupdf_artifact = _make_pymupdf_artifact("doc-abc-123", "pid-1")
        config = {"quality_control": {}}

        result = reconcile(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)

        assert result.document_id == "doc-abc-123"

    def test_provenance_contains_all_five_references(self) -> None:
        """provenance in content has all five required keys."""
        grobid_artifact = _make_grobid_artifact("doc-xyz", "gid-42")
        pymupdf_artifact = _make_pymupdf_artifact("doc-xyz", "pid-99")
        grobid_obs = {"extractor_name": "grobid", "status": "placeholder"}
        pymupdf_obs = {"extractor_name": "pymupdf", "status": "placeholder"}
        inv_obj = {"decision": "deferred_to_adjudicator"}
        config = {"quality_control": {}}

        result = reconcile(
            grobid_artifact, pymupdf_artifact, grobid_obs, pymupdf_obs, inv_obj, config
        )

        provenance = result.content["provenance"]
        for key in REQUIRED_PROVENANCE_KEYS:
            assert key in provenance, f"Missing provenance key: {key!r}"

        assert provenance["grobid_artifact_id"] == "gid-42"
        assert provenance["pymupdf_artifact_id"] == "pid-99"
        assert provenance["grobid_observation"] is grobid_obs
        assert provenance["pymupdf_observation"] is pymupdf_obs
        assert provenance["investigator_object"] is inv_obj

    def test_reconcile_returns_unified_record(self) -> None:
        """reconcile() returns a UnifiedRecord instance, not a plain dict."""
        grobid_artifact = _make_grobid_artifact("doc-struct", "gid-s")
        pymupdf_artifact = _make_pymupdf_artifact("doc-struct", "pid-s")
        config = {"quality_control": {}}

        result = reconcile(grobid_artifact, pymupdf_artifact, {}, {}, {}, config)

        assert isinstance(result, UnifiedRecord)


# ---------------------------------------------------------------------------
# Task 9.3 — Concern routing tests
# ---------------------------------------------------------------------------


class TestConcernRouting:
    """Task 9.3: reconcile() routes concerns to injected strategy objects."""

    def _make_text_processor(self) -> MagicMock:
        tp = MagicMock()
        tp.compare.return_value = 0.9
        return tp

    def _make_text_fidelity_strategy(self) -> MagicMock:
        strategy = MagicMock()
        strategy.reconcile.return_value = {
            "edit_distance": 0.1,
            "agreement": "partial",
            "preferred_reading": "reference text",
            "confidence": 0.9,
        }
        return strategy

    def _make_section_strategy(self) -> MagicMock:
        strategy = MagicMock()
        strategy.reconcile.return_value = 0.85
        return strategy

    def _make_table_figure_strategy(self) -> MagicMock:
        strategy = MagicMock()
        strategy.merge.return_value = {
            "primary": {"caption": "Table 1"},
            "reference": {"text": "Table 1", "bbox": [0, 0, 100, 20]},
            "agreement": "present",
            "merged_text": "Table 1",
        }
        return strategy

    def _make_artifact_with_paragraph_blocks(self, doc_id: str) -> dict:
        return {
            "document_id": doc_id,
            "blocks": [
                {
                    "text": "Hello world paragraph text.",
                    "page_index": 0,
                    "block_type": "paragraph",
                    "block_bbox": [0, 0, 100, 20],
                },
            ],
        }

    def _make_artifact_with_section_blocks(self, doc_id: str) -> dict:
        return {
            "document_id": doc_id,
            "blocks": [
                {
                    "text": "Introduction",
                    "page_index": 0,
                    "block_type": "section",
                    "block_bbox": [0, 0, 100, 20],
                    "font_size": 14.0,
                },
            ],
        }

    def _make_artifact_with_table_blocks(self, doc_id: str) -> dict:
        return {
            "document_id": doc_id,
            "blocks": [
                {
                    "text": "Table 1: Results",
                    "page_index": 0,
                    "block_type": "table",
                    "caption": "Table 1",
                    "block_bbox": [0, 0, 200, 50],
                },
            ],
        }

    def test_text_fidelity_strategy_called_for_paragraph_blocks(self) -> None:
        """reconcile() calls text_fidelity_strategy.reconcile() for paragraph content."""
        primary = self._make_artifact_with_paragraph_blocks("doc-para")
        secondary = self._make_artifact_with_paragraph_blocks("doc-para")
        text_fidelity = self._make_text_fidelity_strategy()
        tp = self._make_text_processor()

        reconcile(
            primary,
            secondary,
            {},
            {},
            {},
            adjudication_decisions={"primary_extractor": "primary", "confidence": 0.9},
            text_fidelity_strategy=text_fidelity,
            text_processor=tp,
        )

        text_fidelity.reconcile.assert_called()

    def test_section_strategy_called_for_section_blocks(self) -> None:
        """reconcile() calls section_strategy.reconcile() for section content."""
        primary = self._make_artifact_with_section_blocks("doc-sec")
        secondary = self._make_artifact_with_section_blocks("doc-sec")
        section_strat = self._make_section_strategy()
        tp = self._make_text_processor()

        reconcile(
            primary,
            secondary,
            {},
            {},
            {},
            adjudication_decisions={"primary_extractor": "primary", "confidence": 0.9},
            section_strategy=section_strat,
            text_processor=tp,
        )

        section_strat.reconcile.assert_called()

    def test_table_figure_strategy_called_for_table_blocks(self) -> None:
        """reconcile() calls table_figure_strategy.merge() for table content."""
        primary = self._make_artifact_with_table_blocks("doc-tbl")
        secondary = self._make_artifact_with_table_blocks("doc-tbl")
        tbl_strat = self._make_table_figure_strategy()
        tp = self._make_text_processor()

        reconcile(
            primary,
            secondary,
            {},
            {},
            {},
            adjudication_decisions={"primary_extractor": "primary", "confidence": 0.9},
            table_figure_strategy=tbl_strat,
            text_processor=tp,
        )

        tbl_strat.merge.assert_called()

    def test_custom_strategy_objects_used_not_defaults(self) -> None:
        """Injected strategy objects are used instead of defaults."""
        primary = self._make_artifact_with_paragraph_blocks("doc-custom")
        secondary = self._make_artifact_with_paragraph_blocks("doc-custom")
        custom_strategy = self._make_text_fidelity_strategy()
        tp = self._make_text_processor()

        reconcile(
            primary,
            secondary,
            {},
            {},
            {},
            adjudication_decisions={"primary_extractor": "primary", "confidence": 0.9},
            text_fidelity_strategy=custom_strategy,
            text_processor=tp,
        )

        # The custom strategy (not the default) should have been called
        assert custom_strategy.reconcile.called

    def test_returned_unified_record_has_non_none_semantic(self) -> None:
        """Returned UnifiedRecord has non-None semantic field."""
        primary = self._make_artifact_with_paragraph_blocks("doc-sem")
        secondary = self._make_artifact_with_paragraph_blocks("doc-sem")

        result = reconcile(
            primary,
            secondary,
            {},
            {},
            {},
            adjudication_decisions={"primary_extractor": "primary", "confidence": 0.9},
        )

        assert isinstance(result, UnifiedRecord)
        assert result.semantic is not None
        assert isinstance(result.semantic, SemanticLayer)

    def test_returned_unified_record_has_non_none_structural(self) -> None:
        """Returned UnifiedRecord has non-None structural field."""
        primary = self._make_artifact_with_paragraph_blocks("doc-str")
        secondary = self._make_artifact_with_paragraph_blocks("doc-str")

        result = reconcile(
            primary,
            secondary,
            {},
            {},
            {},
            adjudication_decisions={"primary_extractor": "primary", "confidence": 0.9},
        )

        assert result.structural is not None
        assert isinstance(result.structural, StructuralLayer)

    def test_returned_unified_record_has_non_none_alignment(self) -> None:
        """Returned UnifiedRecord has non-None alignment field."""
        primary = self._make_artifact_with_paragraph_blocks("doc-aln")
        secondary = self._make_artifact_with_paragraph_blocks("doc-aln")

        result = reconcile(
            primary,
            secondary,
            {},
            {},
            {},
            adjudication_decisions={"primary_extractor": "primary", "confidence": 0.9},
        )

        assert result.alignment is not None
        assert isinstance(result.alignment, AlignmentMap)

    def test_reconcile_source_has_no_grobid_or_pdfplumber_literals(self) -> None:
        """inspect.getsource(reconcile) contains no literal 'grobid' or 'pdfplumber'."""
        src = inspect.getsource(reconcile)
        assert "grobid" not in src, "reconcile() source contains literal 'grobid'"
        assert "pdfplumber" not in src, "reconcile() source contains literal 'pdfplumber'"

    def test_placeholder_path_retained_when_adjudication_decisions_is_none(self) -> None:
        """PLACEHOLDER_NOTICE backward-compat path is retained when decisions is None."""
        primary = self._make_artifact_with_paragraph_blocks("doc-ph")
        secondary = self._make_artifact_with_paragraph_blocks("doc-ph")

        result = reconcile(primary, secondary, {}, {}, {}, adjudication_decisions=None)

        assert isinstance(result, UnifiedRecord)
        assert result.content.get("adjudication_status") == "placeholder"
        assert result.content.get("placeholder_notice") == PLACEHOLDER_NOTICE
