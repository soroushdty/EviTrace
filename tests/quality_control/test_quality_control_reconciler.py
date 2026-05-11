"""
Tests for quality_control/reconciler.py — Unified Output production.

Covers:
  - Property 11: Unified Output contains all required top-level fields
  - Property 12: Unified Output is JSON-serializable without custom encoders
  - Unit tests for document_id, provenance, and structural contract
  - Concern routing, strategy injection, no extractor literals
"""

from __future__ import annotations

import inspect
import json
import sys
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
from quality_control.reconciler import reconcile


@pytest.fixture(autouse=True)
def _mock_scispacy(monkeypatch):
    """Prevent spacy.load('en_core_sci_sm') from running in CI."""
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_PROVENANCE_KEYS = [
    "primary_artifact_id",
    "secondary_artifact_id",
    "primary_observation",
    "secondary_observation",
    "investigator_object",
]

_VALID_ADJUDICATION = {"primary_extractor": "primary", "confidence": 0.9, "rationale": "test"}


def _make_primary_artifact(document_id: str, primary_id: str) -> dict:
    return {
        "document_id": document_id,
        "primary": {"id": primary_id, "content": "<root/>", "format": "tei_xml"},
    }


def _make_secondary_artifact(document_id: str, secondary_id: str) -> dict:
    return {
        "document_id": document_id,
        "secondary": {"id": secondary_id, "content": "{}", "format": "json"},
    }


def _make_artifact_with_blocks(document_id: str, blocks: list[dict]) -> dict:
    """Make a primary artifact that contains directly-accessible blocks."""
    return {
        "document_id": document_id,
        "blocks": blocks,
    }


# ---------------------------------------------------------------------------
# Property 11: reconcile() returns UnifiedRecord with all required content keys
# ---------------------------------------------------------------------------


@given(
    document_id=st.text(min_size=1),
    primary_id=st.text(min_size=1),
    secondary_id=st.text(min_size=1),
)
@settings(max_examples=20)
def test_unified_output_required_fields_and_status(
    document_id: str, primary_id: str, secondary_id: str
) -> None:
    """**Validates: Requirements 6.3, 6.4** — reconcile returns UnifiedRecord with required keys."""
    primary_artifact = _make_primary_artifact(document_id, primary_id)
    secondary_artifact = _make_secondary_artifact(document_id, secondary_id)

    result = reconcile(
        primary_artifact,
        secondary_artifact,
        adjudication_decisions=_VALID_ADJUDICATION,
    )

    assert isinstance(result, UnifiedRecord)
    assert result.document_id == document_id

    required_content_keys = {
        "document_id", "metadata", "pages", "segments", "annotations",
        "tables", "figures", "images", "exact_text", "provenance",
    }
    for key in required_content_keys:
        assert key in result.content, f"Missing content key: {key!r}"


# ---------------------------------------------------------------------------
# Property 12: content dict is JSON-serializable
# ---------------------------------------------------------------------------


@given(
    document_id=st.text(min_size=1),
    primary_id=st.text(min_size=1),
    secondary_id=st.text(min_size=1),
)
@settings(max_examples=20)
def test_unified_output_json_serializable(
    document_id: str, primary_id: str, secondary_id: str
) -> None:
    """**Validates: Requirements 6.6** — content dict is JSON-serializable."""
    primary_artifact = _make_primary_artifact(document_id, primary_id)
    secondary_artifact = _make_secondary_artifact(document_id, secondary_id)

    result = reconcile(
        primary_artifact,
        secondary_artifact,
        adjudication_decisions=_VALID_ADJUDICATION,
    )
    # content must not raise
    json.dumps(result.content)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestRepair:
    def test_document_id_from_primary_artifact(self) -> None:
        """document_id in UnifiedRecord matches primary_artifact['document_id']."""
        primary_artifact = _make_primary_artifact("doc-abc-123", "pid-1")
        secondary_artifact = _make_secondary_artifact("doc-abc-123", "sid-1")

        result = reconcile(
            primary_artifact,
            secondary_artifact,
            adjudication_decisions=_VALID_ADJUDICATION,
        )

        assert result.document_id == "doc-abc-123"

    def test_provenance_contains_all_five_references(self) -> None:
        """provenance in content has all five required keys."""
        primary_artifact = _make_primary_artifact("doc-xyz", "pid-42")
        secondary_artifact = _make_secondary_artifact("doc-xyz", "sid-99")
        primary_obs = {"status": "ok"}
        secondary_obs = {"status": "ok"}
        inv_obj = {"decision": "deferred_to_adjudicator"}

        result = reconcile(
            primary_artifact,
            secondary_artifact,
            primary_obs,
            secondary_obs,
            inv_obj,
            adjudication_decisions=_VALID_ADJUDICATION,
        )

        provenance = result.content["provenance"]
        for key in REQUIRED_PROVENANCE_KEYS:
            assert key in provenance, f"Missing provenance key: {key!r}"

        assert provenance["primary_artifact_id"] == "pid-42"
        assert provenance["secondary_artifact_id"] == "sid-99"
        assert provenance["primary_observation"] is primary_obs
        assert provenance["secondary_observation"] is secondary_obs
        assert provenance["investigator_object"] is inv_obj

    def test_reconcile_returns_unified_record(self) -> None:
        """reconcile() returns a UnifiedRecord instance, not a plain dict."""
        primary_artifact = _make_primary_artifact("doc-struct", "pid-s")
        secondary_artifact = _make_secondary_artifact("doc-struct", "sid-s")

        result = reconcile(
            primary_artifact,
            secondary_artifact,
            adjudication_decisions=_VALID_ADJUDICATION,
        )

        assert isinstance(result, UnifiedRecord)


# ---------------------------------------------------------------------------
# Concern routing tests
# ---------------------------------------------------------------------------


class TestConcernRouting:
    """reconcile() routes concerns to injected strategy objects."""

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

    def test_mock_strategies_populate_alignment_map(self) -> None:
        """Injected concern strategies produce a populated AlignmentMap."""
        primary = {
            "document_id": "doc-align",
            "blocks": [
                {"text": "Intro", "page_index": 0, "block_type": "section"},
                {"text": "Sentence one.", "page_index": 0, "block_type": "paragraph"},
                {"text": "Table 1", "page_index": 0, "block_type": "table"},
            ],
        }
        secondary = {
            "document_id": "doc-align",
            "blocks": [
                {"text": "Introduction", "page_index": 0, "block_type": "section"},
                {"text": "Sentence one.", "page_index": 0, "block_type": "paragraph"},
                {"text": "Table 1", "page_index": 0, "block_type": "table"},
            ],
        }
        tf = self._make_text_fidelity_strategy()
        sec = self._make_section_strategy()
        tbl = self._make_table_figure_strategy()
        tp = self._make_text_processor()

        result = reconcile(
            primary_artifact=primary,
            secondary_artifact=secondary,
            adjudication_decisions={"primary_extractor": "primary", "confidence": 0.9},
            text_fidelity_strategy=tf,
            section_strategy=sec,
            table_figure_strategy=tbl,
            text_processor=tp,
        )

        assert result.alignment is not None
        assert len(result.alignment.paragraph_to_blocks) > 0
        assert len(result.alignment.section_header_to_block) > 0
