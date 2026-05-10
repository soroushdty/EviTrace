"""
tests/pdf_extractor/test_concern_strategies.py
===============================================
Tests for the concern strategy package:
  TextFidelityConcern, SectionVerificationConcern, TableFigureMergeConcern.

Covers Requirements: 7.3, 10
Boundary: tests/pdf_extractor_

Key behaviors verified:
- TextFidelityConcern:
  - Identical inputs -> agreement="full", edit_distance=0.0
  - Divergent inputs -> agreement="divergent"
  - preferred_reading is ALWAYS the reference argument, never primary
  - Swapping arg order produces different preferred_reading when a != b
  - adjudicate() returns dict with preferred_source, confidence, rationale
  - DEFAULT_TEXT_FIDELITY.source_label == "pdfplumber"
- SectionVerificationConcern:
  - Matching heading + adequate font size -> high confidence
  - Font size below configured median threshold -> reduced confidence
  - primary_section is not mutated after call
  - Return type is float in [0.0, 1.0]
  - DEFAULT_SECTION_VERIFICATION is instantiable
- TableFigureMergeConcern:
  - Both present -> merged record containing primary_label and reference_label keys
    plus "agreement" and "merged_text"
  - primary=None -> MissingContributionError
  - reference=None -> MissingContributionError
  - Primary record is not mutated after merge
  - DEFAULT_TABLE_FIGURE_MERGE uses "grobid" / "pdfplumber" as labels
  - MissingContributionError is a ValueError subclass
- Package __init__.py exports all three classes, defaults, MissingContributionError
"""

from __future__ import annotations

import copy
import dataclasses

import pytest


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

class MockTextProcessor:
    """Minimal text processor for testing; compare() uses exact-match shortcut."""

    def compare(self, a: str, b: str) -> float:
        """Return 1.0 for identical strings, 0.0 otherwise (simple mock)."""
        if a == b:
            return 1.0
        # simulate difflib ratio approximation for non-identical strings
        # We use a simple length-based mock so tests are deterministic.
        common = sum(c1 == c2 for c1, c2 in zip(a, b))
        total = len(a) + len(b)
        return (2 * common / total) if total > 0 else 1.0

    def normalize(self, text: str) -> str:
        return text.strip()


# ---------------------------------------------------------------------------
# Package-level import tests
# ---------------------------------------------------------------------------

class TestPackageImports:
    """quality_control.concerns package must export required names."""

    def test_import_text_fidelity_concern(self):
        from quality_control.concerns import TextFidelityConcern  # noqa: F401

    def test_import_section_verification_concern(self):
        from quality_control.concerns import SectionVerificationConcern  # noqa: F401

    def test_import_table_figure_merge_concern(self):
        from quality_control.concerns import TableFigureMergeConcern  # noqa: F401

    def test_import_missing_contribution_error(self):
        from quality_control.concerns import MissingContributionError  # noqa: F401

    def test_import_default_text_fidelity(self):
        from quality_control.concerns import DEFAULT_TEXT_FIDELITY  # noqa: F401

    def test_import_default_section_verification(self):
        from quality_control.concerns import DEFAULT_SECTION_VERIFICATION  # noqa: F401

    def test_import_default_table_figure_merge(self):
        from quality_control.concerns import DEFAULT_TABLE_FIGURE_MERGE  # noqa: F401


# ---------------------------------------------------------------------------
# TextFidelityConcern
# ---------------------------------------------------------------------------

class TestTextFidelityConcernReconcile:
    """TextFidelityConcern.reconcile() behavior (Req 7.3)."""

    def _make_concern(self, **kwargs):
        from quality_control.concerns import TextFidelityConcern
        return TextFidelityConcern(**kwargs)

    def test_identical_inputs_agreement_full(self):
        concern = self._make_concern()
        tp = MockTextProcessor()
        result = concern.reconcile("hello world", "hello world", tp)
        assert result["agreement"] == "full"

    def test_identical_inputs_edit_distance_zero(self):
        concern = self._make_concern()
        tp = MockTextProcessor()
        result = concern.reconcile("hello world", "hello world", tp)
        assert result["edit_distance"] == 0.0

    def test_identical_inputs_confidence_one(self):
        concern = self._make_concern()
        tp = MockTextProcessor()
        result = concern.reconcile("hello world", "hello world", tp)
        assert result["confidence"] == 1.0

    def test_divergent_inputs_agreement_divergent(self):
        concern = self._make_concern(threshold=0.10)
        tp = MockTextProcessor()
        # completely different strings -> compare() ~ 0.0 -> edit_distance ~ 1.0
        result = concern.reconcile("aaaaaaaaaa", "zzzzzzzzzz", tp)
        assert result["agreement"] == "divergent"

    def test_partial_agreement_within_threshold(self):
        """edit_distance < threshold -> 'partial'."""
        from quality_control.concerns import TextFidelityConcern

        class AlmostIdenticalTP:
            """Returns similarity of 0.97 (edit_distance=0.03), below threshold=0.10."""
            def compare(self, a, b):
                return 0.97

        concern = TextFidelityConcern(threshold=0.10)
        result = concern.reconcile("abc", "abx", AlmostIdenticalTP())
        assert result["agreement"] == "partial"
        assert result["edit_distance"] == pytest.approx(0.03, abs=1e-9)

    def test_preferred_reading_is_reference(self):
        """preferred_reading MUST be the reference argument (never primary)."""
        concern = self._make_concern()
        tp = MockTextProcessor()
        primary = "primary text"
        reference = "reference text"
        result = concern.reconcile(primary, reference, tp)
        assert result["preferred_reading"] == reference

    def test_preferred_reading_is_never_primary(self):
        """Even when edit_distance != 0, preferred_reading is reference."""
        concern = self._make_concern(threshold=0.10)
        tp = MockTextProcessor()
        result = concern.reconcile("aaa", "bbb", tp)
        assert result["preferred_reading"] == "bbb"
        assert result["preferred_reading"] != "aaa"

    def test_swapping_args_produces_different_preferred_reading(self):
        """Asymmetry: reconcile(a, b) and reconcile(b, a) give different preferred_reading."""
        concern = self._make_concern()
        tp = MockTextProcessor()
        a = "text from extractor A"
        b = "text from extractor B"
        result_ab = concern.reconcile(a, b, tp)
        result_ba = concern.reconcile(b, a, tp)
        assert result_ab["preferred_reading"] == b
        assert result_ba["preferred_reading"] == a
        # They differ when a != b
        assert result_ab["preferred_reading"] != result_ba["preferred_reading"]

    def test_result_has_required_keys(self):
        concern = self._make_concern()
        tp = MockTextProcessor()
        result = concern.reconcile("x", "y", tp)
        assert "edit_distance" in result
        assert "agreement" in result
        assert "preferred_reading" in result
        assert "confidence" in result

    def test_confidence_equals_one_minus_edit_distance(self):
        from quality_control.concerns import TextFidelityConcern

        class FixedTP:
            def compare(self, a, b):
                return 0.75  # edit_distance = 0.25

        concern = TextFidelityConcern()
        result = concern.reconcile("any", "thing", FixedTP())
        assert result["edit_distance"] == pytest.approx(0.25, abs=1e-9)
        assert result["confidence"] == pytest.approx(0.75, abs=1e-9)


class TestTextFidelityConcernAdjudicate:
    """TextFidelityConcern.adjudicate() behavior."""

    def test_empty_entries_returns_fallback(self):
        from quality_control.concerns import TextFidelityConcern
        concern = TextFidelityConcern(source_label="pdfplumber")
        result = concern.adjudicate([], config={})
        assert result["preferred_source"] == "pdfplumber"
        assert result["confidence"] == 0.0
        assert "rationale" in result

    def test_adjudicate_picks_lowest_edit_distance(self):
        from quality_control.concerns import TextFidelityConcern
        from quality_control.models import AlignmentMapEntry

        concern = TextFidelityConcern(source_label="pdfplumber")
        entries = [
            AlignmentMapEntry(source="grobid", edit_distance=0.5, confidence=0.5),
            AlignmentMapEntry(source="pdfplumber", edit_distance=0.1, confidence=0.9),
        ]
        result = concern.adjudicate(entries, config={})
        assert result["preferred_source"] == "pdfplumber"

    def test_adjudicate_result_keys(self):
        from quality_control.concerns import TextFidelityConcern
        concern = TextFidelityConcern()
        result = concern.adjudicate([], config={})
        assert set(result.keys()) == {"preferred_source", "confidence", "rationale"}


class TestDefaultTextFidelity:
    """DEFAULT_TEXT_FIDELITY instance properties."""

    def test_source_label_is_pdfplumber(self):
        from quality_control.concerns import DEFAULT_TEXT_FIDELITY
        assert DEFAULT_TEXT_FIDELITY.source_label == "pdfplumber"

    def test_is_text_fidelity_concern_instance(self):
        from quality_control.concerns import DEFAULT_TEXT_FIDELITY, TextFidelityConcern
        assert isinstance(DEFAULT_TEXT_FIDELITY, TextFidelityConcern)

    def test_has_threshold_attribute(self):
        from quality_control.concerns import DEFAULT_TEXT_FIDELITY
        assert hasattr(DEFAULT_TEXT_FIDELITY, "threshold")
        assert isinstance(DEFAULT_TEXT_FIDELITY.threshold, float)


# ---------------------------------------------------------------------------
# SectionVerificationConcern
# ---------------------------------------------------------------------------

class TestSectionVerificationConcernReconcile:
    """SectionVerificationConcern.reconcile() behavior (Req 7.3)."""

    def _make_concern(self, **kwargs):
        from quality_control.concerns import SectionVerificationConcern
        return SectionVerificationConcern(**kwargs)

    def _primary_section(self, heading="Introduction"):
        return {"heading": heading, "label": "1", "depth": 1}

    def _reference_block(self, text="Introduction", font_size=14.0):
        return {"text": text, "font_size": font_size, "bold": True, "bbox": (0, 0, 100, 20)}

    def test_return_type_is_float(self):
        concern = self._make_concern()
        tp = MockTextProcessor()
        result = concern.reconcile(self._primary_section(), self._reference_block(), tp)
        assert isinstance(result, float)

    def test_return_value_in_range(self):
        concern = self._make_concern()
        tp = MockTextProcessor()
        result = concern.reconcile(self._primary_section(), self._reference_block(), tp)
        assert 0.0 <= result <= 1.0

    def test_matching_heading_high_confidence(self):
        """Identical heading + adequate font -> confidence close to 1.0."""
        concern = self._make_concern()
        tp = MockTextProcessor()
        primary = self._primary_section(heading="Introduction")
        reference = self._reference_block(text="Introduction", font_size=14.0)
        result = concern.reconcile(primary, reference, tp)
        assert result > 0.5

    def test_font_size_below_threshold_reduces_confidence(self):
        """font_size below configured median threshold -> lower confidence than adequate size."""
        concern = self._make_concern()
        tp = MockTextProcessor()
        primary = self._primary_section(heading="Introduction")
        # Use same heading text so text comparison alone gives max score
        ref_adequate = self._reference_block(text="Introduction", font_size=14.0)
        ref_tiny = self._reference_block(text="Introduction", font_size=1.0)
        result_adequate = concern.reconcile(primary, ref_adequate, tp)
        result_tiny = concern.reconcile(primary, ref_tiny, tp)
        assert result_tiny < result_adequate

    def test_primary_section_not_mutated(self):
        """Req 7.3: reconcile must never modify primary_section."""
        concern = self._make_concern()
        tp = MockTextProcessor()
        primary = {"heading": "Methods", "label": "2", "depth": 1}
        original_copy = copy.deepcopy(primary)
        concern.reconcile(primary, self._reference_block(text="Methods"), tp)
        assert primary == original_copy

    def test_mismatched_heading_lower_confidence(self):
        """Different heading text -> lower confidence than matching."""
        concern = self._make_concern()
        tp = MockTextProcessor()
        primary = self._primary_section(heading="Introduction")
        ref_match = self._reference_block(text="Introduction", font_size=14.0)
        ref_mismatch = self._reference_block(text="ZZZZZZZ completely different", font_size=14.0)
        result_match = concern.reconcile(primary, ref_match, tp)
        result_mismatch = concern.reconcile(primary, ref_mismatch, tp)
        assert result_mismatch < result_match


class TestDefaultSectionVerification:
    """DEFAULT_SECTION_VERIFICATION instance properties."""

    def test_is_section_verification_concern_instance(self):
        from quality_control.concerns import DEFAULT_SECTION_VERIFICATION, SectionVerificationConcern
        assert isinstance(DEFAULT_SECTION_VERIFICATION, SectionVerificationConcern)

    def test_callable_reconcile(self):
        from quality_control.concerns import DEFAULT_SECTION_VERIFICATION
        assert callable(DEFAULT_SECTION_VERIFICATION.reconcile)


# ---------------------------------------------------------------------------
# TableFigureMergeConcern
# ---------------------------------------------------------------------------

class TestTableFigureMergeConcernMerge:
    """TableFigureMergeConcern.merge() behavior (Req 7.3)."""

    def _make_concern(self, **kwargs):
        from quality_control.concerns import TableFigureMergeConcern
        return TableFigureMergeConcern(**kwargs)

    def test_both_present_returns_merged_dict(self):
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1", "page_index": 0}
        reference = {"bbox": (10, 20, 100, 50), "page_index": 0}
        result = concern.merge(primary, reference)
        assert isinstance(result, dict)

    def test_merged_dict_has_primary_label_key(self):
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1", "page_index": 0}
        reference = {"bbox": (10, 20, 100, 50)}
        result = concern.merge(primary, reference)
        assert "grobid" in result

    def test_merged_dict_has_reference_label_key(self):
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1", "page_index": 0}
        reference = {"bbox": (10, 20, 100, 50)}
        result = concern.merge(primary, reference)
        assert "pdfplumber" in result

    def test_merged_dict_has_agreement_key(self):
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1"}
        reference = {"bbox": (10, 20, 100, 50)}
        result = concern.merge(primary, reference)
        assert "agreement" in result

    def test_merged_dict_has_merged_text_key(self):
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1"}
        reference = {"bbox": (10, 20, 100, 50)}
        result = concern.merge(primary, reference)
        assert "merged_text" in result

    def test_primary_none_raises_missing_contribution_error(self):
        from quality_control.concerns import MissingContributionError
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        reference = {"bbox": (10, 20, 100, 50)}
        with pytest.raises(MissingContributionError):
            concern.merge(None, reference)

    def test_reference_none_raises_missing_contribution_error(self):
        from quality_control.concerns import MissingContributionError
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1"}
        with pytest.raises(MissingContributionError):
            concern.merge(primary, None)

    def test_both_none_raises_missing_contribution_error(self):
        from quality_control.concerns import MissingContributionError
        concern = self._make_concern()
        with pytest.raises(MissingContributionError):
            concern.merge(None, None)

    def test_primary_none_error_names_absent_side(self):
        """Error message must name the absent side."""
        from quality_control.concerns import MissingContributionError
        concern = self._make_concern()
        with pytest.raises(MissingContributionError, match="primary"):
            concern.merge(None, {"bbox": (0, 0, 1, 1)})

    def test_reference_none_error_names_absent_side(self):
        """Error message must name the absent side."""
        from quality_control.concerns import MissingContributionError
        concern = self._make_concern()
        with pytest.raises(MissingContributionError, match="reference"):
            concern.merge({"caption": "Fig"}, None)

    def test_primary_record_not_mutated_after_merge(self):
        """Req 7.3: primary record must be unmodified after merge."""
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1", "page_index": 0}
        reference = {"bbox": (10, 20, 100, 50)}
        original_primary = copy.deepcopy(primary)
        concern.merge(primary, reference)
        assert primary == original_primary

    def test_primary_label_key_holds_primary_data(self):
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1"}
        reference = {"bbox": (10, 20, 100, 50)}
        result = concern.merge(primary, reference)
        assert result["grobid"] == primary

    def test_reference_label_key_holds_reference_data(self):
        concern = self._make_concern(primary_label="grobid", reference_label="pdfplumber")
        primary = {"caption": "Table 1"}
        reference = {"bbox": (10, 20, 100, 50)}
        result = concern.merge(primary, reference)
        assert result["pdfplumber"] == reference


class TestMissingContributionError:
    """MissingContributionError is a ValueError subclass."""

    def test_is_value_error_subclass(self):
        from quality_control.concerns import MissingContributionError
        assert issubclass(MissingContributionError, ValueError)

    def test_can_be_raised_with_message(self):
        from quality_control.concerns import MissingContributionError
        with pytest.raises(ValueError):
            raise MissingContributionError("primary side is missing")

    def test_can_be_caught_as_value_error(self):
        from quality_control.concerns import MissingContributionError
        try:
            raise MissingContributionError("test")
        except ValueError:
            pass  # expected


class TestDefaultTableFigureMerge:
    """DEFAULT_TABLE_FIGURE_MERGE uses 'grobid'/'pdfplumber' labels."""

    def test_is_table_figure_merge_concern_instance(self):
        from quality_control.concerns import DEFAULT_TABLE_FIGURE_MERGE, TableFigureMergeConcern
        assert isinstance(DEFAULT_TABLE_FIGURE_MERGE, TableFigureMergeConcern)

    def test_primary_label_is_grobid(self):
        from quality_control.concerns import DEFAULT_TABLE_FIGURE_MERGE
        assert DEFAULT_TABLE_FIGURE_MERGE.primary_label == "grobid"

    def test_reference_label_is_pdfplumber(self):
        from quality_control.concerns import DEFAULT_TABLE_FIGURE_MERGE
        assert DEFAULT_TABLE_FIGURE_MERGE.reference_label == "pdfplumber"

    def test_merge_uses_grobid_pdfplumber_keys(self):
        from quality_control.concerns import DEFAULT_TABLE_FIGURE_MERGE
        primary = {"caption": "Table 1"}
        reference = {"bbox": (0, 0, 100, 50)}
        result = DEFAULT_TABLE_FIGURE_MERGE.merge(primary, reference)
        assert "grobid" in result
        assert "pdfplumber" in result

    def test_merge_raises_for_none_primary(self):
        from quality_control.concerns import DEFAULT_TABLE_FIGURE_MERGE, MissingContributionError
        with pytest.raises(MissingContributionError):
            DEFAULT_TABLE_FIGURE_MERGE.merge(None, {"bbox": (0, 0, 1, 1)})

    def test_merge_raises_for_none_reference(self):
        from quality_control.concerns import DEFAULT_TABLE_FIGURE_MERGE, MissingContributionError
        with pytest.raises(MissingContributionError):
            DEFAULT_TABLE_FIGURE_MERGE.merge({"caption": "x"}, None)
