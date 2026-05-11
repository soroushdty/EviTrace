"""
tests/quality_control/test_quality_control_rater.py
=====================================================
Tests for quality_control/rater.py.

Covers:
  - Property 5: observe returns a QualityReport with correct extractor and branch
  - Property 6: observe is deterministic
  - Property 7: observe output is a QualityReport instance
  - Unit tests for rater
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quality_control.models import Candidate
from quality_control.defaults import QualityReport
from quality_control.rater import observe


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_branch(extractor: str = "grobid", branch: int = 0) -> Candidate:
    return Candidate(source=extractor, index=branch, payload=None, status=None)


def _make_config(attribute_names: list[str] | None = None) -> dict:
    return {"quality_control": {"rater": {"attributes": attribute_names or []}}}


# ---------------------------------------------------------------------------
# Property 5: observe returns a QualityReport with correct extractor and branch
# ---------------------------------------------------------------------------

@given(
    extractor=st.text(min_size=1),
    branch_idx=st.integers(min_value=0, max_value=100),
)
@settings(max_examples=20)
def test_observe_returns_quality_report_with_correct_fields(
    extractor: str, branch_idx: int
):
    """**Validates: Requirements 3.2, 3.4**

    For any extractor name and branch index, observe SHALL return a
    QualityReport whose extractor and branch fields match the input branch.
    """
    branch = Candidate(source=extractor, index=branch_idx, payload=None, status=None)
    config = _make_config()
    result = observe(branch, config)

    assert isinstance(result, QualityReport)
    assert result.extractor == extractor
    assert result.index == branch_idx


# ---------------------------------------------------------------------------
# Property 6: observe is deterministic
# ---------------------------------------------------------------------------

@given(
    extractor=st.text(min_size=1),
    branch_idx=st.integers(min_value=0, max_value=100),
)
@settings(max_examples=20)
def test_observe_is_deterministic(extractor: str, branch_idx: int):
    """**Validates: Requirements 3.5**

    Calling observe twice with the same branch SHALL return QualityReport
    instances with equal field values.
    """
    branch = Candidate(source=extractor, index=branch_idx, payload=None, status=None)
    config = _make_config()
    result1 = observe(branch, config)
    result2 = observe(branch, config)

    assert result1.extractor == result2.extractor
    assert result1.index == result2.index
    assert result1.status == result2.status


# ---------------------------------------------------------------------------
# Property 7: observe always returns a QualityReport instance
# ---------------------------------------------------------------------------

@given(
    extractor=st.sampled_from(["grobid", "pymupdf", "pdfplumber", "paddleocr"]),
    branch_idx=st.integers(min_value=0, max_value=10),
)
@settings(max_examples=20)
def test_observe_always_returns_quality_report_instance(
    extractor: str, branch_idx: int
):
    """**Validates: Requirements 3.3**

    observe SHALL always return an instance of QualityReport (not a plain dict).
    """
    branch = Candidate(source=extractor, index=branch_idx, payload=None, status=None)
    config = _make_config(["attr1", "attr2"])
    result = observe(branch, config)

    assert isinstance(result, QualityReport)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestRater:
    def test_observe_grobid_branch(self):
        """observe for a grobid branch returns QualityReport with extractor='grobid'."""
        branch = _make_branch(extractor="grobid", branch=0)
        result = observe(branch, _make_config())
        assert isinstance(result, QualityReport)
        assert result.extractor == "grobid"
        assert result.index == 0

    def test_observe_pymupdf_branch(self):
        """observe for a pymupdf branch returns QualityReport with extractor='pymupdf'."""
        branch = _make_branch(extractor="pymupdf", branch=1)
        result = observe(branch, _make_config())
        assert isinstance(result, QualityReport)
        assert result.extractor == "pymupdf"
        assert result.index == 1

    def test_observe_status_is_none(self):
        """observe sets status to None (not yet adjudicated)."""
        branch = _make_branch()
        result = observe(branch, _make_config())
        assert result.status is None

    def test_observe_different_branches_produce_separate_reports(self):
        """observe for two different branches returns separate QualityReport instances."""
        branch0 = _make_branch(extractor="grobid", branch=0)
        branch1 = _make_branch(extractor="pymupdf", branch=1)
        result0 = observe(branch0, _make_config())
        result1 = observe(branch1, _make_config())

        assert result0 is not result1
        assert result0.extractor == "grobid"
        assert result1.extractor == "pymupdf"

    def test_observe_does_not_call_artifacts_module(self, monkeypatch):
        """observe must not call any function from the artifact_generator module."""
        import pdf_extractor.artifact_generator as artifact_generator_mod

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

        branch = _make_branch(extractor="grobid", branch=0)
        observe(branch, _make_config(["attr1"]))

        assert called == [], (
            f"observe unexpectedly called artifact_generator functions: {called}"
        )

    def test_observe_passes_check_returns_true(self):
        """QualityReport.passes_check() must return True (default unconditional pass)."""
        branch = _make_branch()
        result = observe(branch, _make_config())
        assert result.passes_check() is True
        assert result.status == "pass"
