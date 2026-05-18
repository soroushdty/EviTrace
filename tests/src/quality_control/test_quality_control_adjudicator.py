"""
Tests for quality_control/adjudicator.py — strategy-delegation adjudicator.

Covers:
  - Task 10.1: adjudicate() returns a decisions dict; no reconciler call;
                no hardcoded extractor names in the function body.
  - Task 10.2: strategy injection; mock strategy drives preferred_source;
                inspect.getsource contains no "grobid"/"pdfplumber" literals.
  - Task 4.3: branch selection with empty GROBID — pdfplumber selected as primary.

Requirements: 4.5, 7.2, 10
Boundary: tests/pdf_extractor_
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from quality_control.adjudicator import adjudicate, _adjudicate_concern, select_primary_branch
from quality_control.models import Candidate, DocumentAlignment, AlignmentRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alignment_map(
    para_entries=None,
    section_entries=None,
    flag_entries=None,
) -> DocumentAlignment:
    return DocumentAlignment(
        paragraph_to_blocks=para_entries or [],
        section_header_to_block=section_entries or [],
        reconciliation_flags=flag_entries or [],
    )


def _make_entry(source: str = "reference", confidence: float = 0.9, edit_distance: float = 0.1) -> AlignmentRecord:
    return AlignmentRecord(
        source=source,
        confidence=confidence,
        edit_distance=edit_distance,
    )


def _make_mock_strategy(preferred_source: str = "mock_source", confidence: float = 0.85) -> MagicMock:
    strategy = MagicMock()
    strategy.adjudicate.return_value = {
        "preferred_source": preferred_source,
        "confidence": confidence,
        "rationale": "mock rationale",
    }
    return strategy


# ---------------------------------------------------------------------------
# Task 10.2 — Inspection: no "grobid" or "pdfplumber" literals in adjudicate()
# ---------------------------------------------------------------------------


class TestNoHardcodedExtractorNames:
    """adjudicate() must not contain "grobid" or "pdfplumber" string literals."""

    def test_no_grobid_literal_in_adjudicate_source(self) -> None:
        src = inspect.getsource(adjudicate)
        assert "grobid" not in src, (
            'adjudicate() must not contain the literal string "grobid"'
        )

    def test_no_pdfplumber_literal_in_adjudicate_source(self) -> None:
        src = inspect.getsource(adjudicate)
        assert "pdfplumber" not in src, (
            'adjudicate() must not contain the literal string "pdfplumber"'
        )


# ---------------------------------------------------------------------------
# Task 10.1 — Return type: adjudicate() returns a dict (not a UnifiedRecord)
# ---------------------------------------------------------------------------


class TestAdjudicateReturnType:
    """adjudicate() must return a plain dict of decisions."""

    def test_returns_dict(self) -> None:
        alignment_map = _make_alignment_map()
        config = {}
        result = adjudicate(alignment_map, config)
        assert isinstance(result, dict)

    def test_empty_alignment_map_returns_empty_dict(self) -> None:
        alignment_map = _make_alignment_map()
        config = {}
        result = adjudicate(alignment_map, config)
        # No entries → no concerns to adjudicate → empty decisions
        assert result == {}

    def test_does_not_import_reconciler(self) -> None:
        """adjudicator module must not import reconciler at all."""
        import quality_control.adjudicator as adj_module
        assert not hasattr(adj_module, "reconciler"), (
            "adjudicator must not import or reference the reconciler module"
        )

    def test_adjudicate_result_is_not_unified_record_shape(self) -> None:
        """adjudicate() must return a decisions dict, not a UnifiedRecord-shaped dict."""
        entries = [_make_entry()]
        alignment_map = _make_alignment_map(para_entries=entries)
        config = {}
        mock_strategy = _make_mock_strategy("some_source")

        result = adjudicate(alignment_map, config, text_fidelity_strategy=mock_strategy)

        # A UnifiedRecord-shaped dict would have "document_id", "segments", etc.
        # A decisions dict has concern-type keys instead.
        assert "document_id" not in result
        assert "segments" not in result
        assert "text_fidelity" in result


# ---------------------------------------------------------------------------
# Task 10.2 — Strategy delegation: each strategy.adjudicate() is called
# ---------------------------------------------------------------------------


class TestStrategyDelegation:
    """adjudicate() must call strategy.adjudicate(alignment_entries, config) for each concern."""

    def test_text_fidelity_strategy_called(self) -> None:
        entries = [_make_entry("src_a")]
        alignment_map = _make_alignment_map(para_entries=entries)
        config = {"quality_control": {}}
        mock_strategy = _make_mock_strategy("src_a")

        adjudicate(alignment_map, config, text_fidelity_strategy=mock_strategy)

        mock_strategy.adjudicate.assert_called_once_with(entries, config)

    def test_section_strategy_called(self) -> None:
        entries = [_make_entry("sec_src")]
        alignment_map = _make_alignment_map(section_entries=entries)
        config = {}
        mock_strategy = _make_mock_strategy("sec_src")

        adjudicate(alignment_map, config, section_strategy=mock_strategy)

        mock_strategy.adjudicate.assert_called_once_with(entries, config)

    def test_table_figure_strategy_called(self) -> None:
        entries = [_make_entry("tf_src")]
        alignment_map = _make_alignment_map(flag_entries=entries)
        config = {}
        mock_strategy = _make_mock_strategy("tf_src")

        adjudicate(alignment_map, config, table_figure_strategy=mock_strategy)

        mock_strategy.adjudicate.assert_called_once_with(entries, config)

    def test_all_three_strategies_called_when_entries_present(self) -> None:
        para = [_make_entry("para_src")]
        section = [_make_entry("sec_src")]
        flags = [_make_entry("tf_src")]
        alignment_map = _make_alignment_map(
            para_entries=para,
            section_entries=section,
            flag_entries=flags,
        )
        config = {}
        mock_tf = _make_mock_strategy("para_src")
        mock_sv = _make_mock_strategy("sec_src")
        mock_tfm = _make_mock_strategy("tf_src")

        result = adjudicate(
            alignment_map,
            config,
            text_fidelity_strategy=mock_tf,
            section_strategy=mock_sv,
            table_figure_strategy=mock_tfm,
        )

        mock_tf.adjudicate.assert_called_once_with(para, config)
        mock_sv.adjudicate.assert_called_once_with(section, config)
        mock_tfm.adjudicate.assert_called_once_with(flags, config)
        assert "text_fidelity" in result
        assert "section_verification" in result
        assert "table_figure" in result

    def test_strategy_not_called_when_no_entries(self) -> None:
        """Strategy should not be called when the corresponding entries list is empty."""
        alignment_map = _make_alignment_map()  # all empty
        config = {}
        mock_strategy = _make_mock_strategy()

        adjudicate(
            alignment_map,
            config,
            text_fidelity_strategy=mock_strategy,
            section_strategy=mock_strategy,
            table_figure_strategy=mock_strategy,
        )

        mock_strategy.adjudicate.assert_not_called()


# ---------------------------------------------------------------------------
# Task 10.2 — preferred_source is set entirely by mock strategy return value
# ---------------------------------------------------------------------------


class TestPreferredSourceFromStrategy:
    """preferred_source in the decisions dict must come entirely from the strategy."""

    def test_custom_preferred_source_flows_through(self) -> None:
        entries = [_make_entry("custom_extractor")]
        alignment_map = _make_alignment_map(para_entries=entries)
        config = {}
        mock_strategy = _make_mock_strategy(preferred_source="custom_extractor")

        result = adjudicate(alignment_map, config, text_fidelity_strategy=mock_strategy)

        assert result["text_fidelity"]["preferred_source"] == "custom_extractor"

    def test_strategy_return_dict_is_used_verbatim(self) -> None:
        """The dict returned by strategy.adjudicate() must appear unchanged in decisions."""
        strategy_result = {
            "preferred_source": "special_extractor",
            "confidence": 0.99,
            "rationale": "test rationale",
        }
        entries = [_make_entry()]
        alignment_map = _make_alignment_map(para_entries=entries)
        config = {}
        mock_strategy = MagicMock()
        mock_strategy.adjudicate.return_value = strategy_result

        result = adjudicate(alignment_map, config, text_fidelity_strategy=mock_strategy)

        assert result["text_fidelity"] is strategy_result

    def test_custom_strategy_arbitrary_preferred_source(self) -> None:
        """A custom strategy returning preferred_source='my_custom_extractor' passes through unchanged."""
        entries = [_make_entry()]
        alignment_map = _make_alignment_map(para_entries=entries)
        config = {}

        class CustomStrategy:
            def adjudicate(self, alignment_entries, config):
                return {"preferred_source": "my_custom_extractor", "confidence": 1.0, "rationale": "custom"}

        result = adjudicate(alignment_map, config, text_fidelity_strategy=CustomStrategy())

        assert result["text_fidelity"]["preferred_source"] == "my_custom_extractor"


# ---------------------------------------------------------------------------
# Task 10.1 — _adjudicate_concern helper
# ---------------------------------------------------------------------------


class TestAdjudicateConcernHelper:
    """_adjudicate_concern(alignment_entries, strategy, config) calls strategy.adjudicate."""

    def test_delegates_to_strategy(self) -> None:
        entries = [_make_entry()]
        config = {"key": "val"}
        mock_strategy = _make_mock_strategy("src")

        result = _adjudicate_concern(entries, mock_strategy, config)

        mock_strategy.adjudicate.assert_called_once_with(entries, config)
        assert result["preferred_source"] == "src"

    def test_returns_strategy_result(self) -> None:
        expected = {"preferred_source": "x", "confidence": 0.5, "rationale": "r"}
        mock_strategy = MagicMock()
        mock_strategy.adjudicate.return_value = expected

        result = _adjudicate_concern([], mock_strategy, {})

        assert result is expected


# ---------------------------------------------------------------------------
# Task 10.1 — Default strategies are used when none injected
# ---------------------------------------------------------------------------


class TestDefaultStrategies:
    """When no strategies are injected, the module-level defaults are used."""

    def test_adjudicate_with_defaults_returns_dict(self) -> None:
        """adjudicate() with no injection still returns a dict."""
        entries = [_make_entry("native")]
        alignment_map = _make_alignment_map(para_entries=entries)
        config = {}

        result = adjudicate(alignment_map, config)

        assert isinstance(result, dict)
        # text_fidelity key must be present since para_entries is non-empty
        assert "text_fidelity" in result

    def test_default_strategy_produces_preferred_source_key(self) -> None:
        entries = [_make_entry("native")]
        alignment_map = _make_alignment_map(para_entries=entries)
        config = {}

        result = adjudicate(alignment_map, config)

        assert "preferred_source" in result["text_fidelity"]


# ---------------------------------------------------------------------------
# Task 4.3 — Branch selection with empty GROBID (Req 4.5)
# ---------------------------------------------------------------------------


class TestBranchSelectionEmptyGrobid:
    """When GROBID branch is empty and pdfplumber has valid content, pdfplumber is selected."""

    def test_pdfplumber_selected_when_grobid_empty_string(self) -> None:
        """Empty-string GROBID payload → pdfplumber selected as primary."""
        branches = [
            Candidate(source="grobid", index=0, payload="", status=None),
            Candidate(
                source="pdfplumber",
                index=1,
                payload=[{"text": "1. Introduction\nThis study investigates the effects of treatment on patient outcomes across multiple clinical trials conducted between 2010 and 2023. Methods included randomized controlled trials with blinded assessment of primary endpoints."}],
                status=None,
            ),
        ]
        config = {"quality_control": {}}

        selected_branch, score, rationale = select_primary_branch(branches, config)

        assert selected_branch.source == "pdfplumber"
        assert "GROBID branch empty" in rationale

    def test_pdfplumber_selected_when_grobid_empty_list(self) -> None:
        """Empty-list GROBID payload → pdfplumber selected as primary."""
        branches = [
            Candidate(source="grobid", index=0, payload=[], status=None),
            Candidate(
                source="pdfplumber",
                index=1,
                payload=[{"text": "Abstract\nWe present a comprehensive analysis of drug efficacy data spanning 500 patients enrolled in a phase III randomized trial. Results demonstrate significant improvement in primary outcome measures."}],
                status=None,
            ),
        ]
        config = {"quality_control": {}}

        selected_branch, score, rationale = select_primary_branch(branches, config)

        assert selected_branch.source == "pdfplumber"
        assert "GROBID branch empty" in rationale

    def test_pdfplumber_selected_when_grobid_none_payload(self) -> None:
        """None GROBID payload → pdfplumber selected as primary."""
        branches = [
            Candidate(source="grobid", index=0, payload=None, status=None),
            Candidate(
                source="pdfplumber",
                index=1,
                payload=[{"text": "Results\nThe intervention group showed a 35% reduction in adverse events compared to placebo (p<0.001). Secondary endpoints including quality of life measures also improved significantly."}],
                status=None,
            ),
        ]
        config = {"quality_control": {}}

        selected_branch, score, rationale = select_primary_branch(branches, config)

        assert selected_branch.source == "pdfplumber"
        assert "GROBID branch empty" in rationale

    def test_pdfplumber_has_positive_composite_score(self) -> None:
        """The selected pdfplumber branch must have a positive composite score."""
        branches = [
            Candidate(source="grobid", index=0, payload="", status=None),
            Candidate(
                source="pdfplumber",
                index=1,
                payload=[{"text": "Discussion\nOur findings are consistent with prior literature suggesting that early intervention leads to better long-term outcomes. The mechanism of action involves modulation of inflammatory pathways."}],
                status=None,
            ),
        ]
        config = {"quality_control": {}}

        selected_branch, score, rationale = select_primary_branch(branches, config)

        assert score.composite > 0.0
        assert score.has_content is True

    def test_grobid_empty_scores_zero_composite(self) -> None:
        """An empty GROBID branch must score 0.0 composite."""
        from quality_control.adjudicator import score_branch

        grobid_branch = Candidate(source="grobid", index=0, payload="", status=None)
        all_branches = [grobid_branch]

        grobid_score = score_branch(grobid_branch, all_branches)

        assert grobid_score.composite == 0.0
        assert grobid_score.has_content is False
