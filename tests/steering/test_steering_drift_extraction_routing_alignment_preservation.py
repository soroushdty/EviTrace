"""
tests/steering/test_steering_drift_extraction_routing_alignment_preservation.py
----------------------------------------------------------------------------------
Preservation property tests for the extraction-routing-alignment bugfix spec.

These tests encode the EXISTING correct behaviour on non-buggy inputs and
MUST PASS on the current (unfixed) code.  They establish the regression
baseline that must continue to hold after the fix is applied (Task 3).

Observed on UNFIXED code:
  - extract_with_grobid() IS called and its output appears in a
    BranchOutput(extractor="grobid") branch for native PDFs.
  - run_quality_control() IS called and the returned QCContext has
    unified, reports, iaa_metrics, and decision all set (not None).
  - When GROBID raises and failure_behavior="fallback", a WARNING is logged
    and processing continues with empty TEI XML.
  - When GROBID raises and failure_behavior="manifest_fail", the exception
    is re-raised.
  - ctx.unified.content["source_pdf_path"] and
    ctx.unified.content["grobid_tei_xml"] are set after _build_qc_context
    completes.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# scispaCy/spaCy autouse mock — prevents spacy.load('en_core_sci_sm') in CI
# ---------------------------------------------------------------------------


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
# Helpers — build minimal fitz page/doc mocks
# ---------------------------------------------------------------------------


def _make_native_page(page_index: int = 0) -> MagicMock:
    """Return a fitz.Page mock that classify_page will treat as native."""
    page = MagicMock()
    page.number = page_index
    # Enough words, all alpha → stages 1-3 won't fire
    page.get_text.return_value = " ".join(["word"] * 60)
    # One embedded font → stage 4 won't fire
    page.get_fonts.return_value = [("font1",)]
    # No images → stage 5 won't fire
    page.get_images.return_value = []
    page.rect = MagicMock()
    page.rect.width = 595.0
    page.rect.height = 842.0
    return page


def _make_fitz_doc(pages: list) -> MagicMock:
    """Return a fitz.open() mock that yields the given pages."""
    doc = MagicMock()
    doc.__iter__ = MagicMock(return_value=iter(pages))
    doc.__enter__ = MagicMock(return_value=doc)
    doc.__exit__ = MagicMock(return_value=False)
    doc.close = MagicMock()
    return doc


def _make_fitz_module(doc: MagicMock) -> MagicMock:
    """Return a fitz module mock whose open() returns *doc*."""
    mock_fitz = MagicMock()
    mock_fitz.open.return_value = doc
    return mock_fitz


def _minimal_qc_config(
    failure_behavior: str = "fallback",
    ocr: bool = True,
) -> dict:
    """Return a minimal qc_config dict for _build_qc_context."""
    return {
        "quality_control": {
            "grobid_integration": {
                "failure_behavior": failure_behavior,
            },
            "scan_detection": {
                "text_density_threshold": 50,
                "alpha_ratio_threshold": 0.60,
                "image_dominance_threshold": 0.85,
            },
        },
        "ocr": ocr,
    }


def _make_native_classification(page_index: int = 0):
    from pdf_extractor.extraction.scan_detector import PageScanClassification
    return PageScanClassification(
        page_index=page_index,
        is_native=True,
        triggered_stages=[],
        stage_values={
            "word_count": 100.0,
            "alpha_ratio": 0.95,
            "font_count": 3.0,
            "image_coverage": 0.01,
        },
    )


def _fake_run_qc_full(branches, document_id, config):
    """Simulate run_quality_control returning a fully populated QCContext."""
    from quality_control.models import (
        AdjudicationDecision,
        InterRaterReport,
        QCContext,
        QualityReport,
        UnifiedRecord,
    )

    ctx = QCContext(branches=branches)
    ctx.reports = [
        QualityReport(extractor=b.extractor, branch=b.branch, status="pass")
        for b in branches
    ]
    ctx.iaa_metrics = InterRaterReport(pairwise={})
    ctx.decision = AdjudicationDecision(
        primary_extractor=branches[0].extractor if branches else "",
        confidence=1.0,
        rationale="test",
    )
    ctx.unified = UnifiedRecord(document_id=document_id, content={})
    return ctx


# ---------------------------------------------------------------------------
# Import _build_qc_context once at module level.
# Unit tests and PBT tests both use patch() context managers to swap out
# the heavy dependencies per-call, so no module re-import is needed.
# ---------------------------------------------------------------------------

from pipeline.orchestrator import _build_qc_context  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helper for running _build_qc_context with all mocks applied
# ---------------------------------------------------------------------------


def _run_with_mocks(
    pages: list,
    tei_xml: str,
    qc_config: dict,
    grobid_side_effect=None,
):
    """Run _build_qc_context with all heavy dependencies mocked.

    Returns (ctx, mock_grobid, mock_qc).
    If grobid_side_effect is provided, it is used as the side_effect for
    extract_with_grobid (to simulate failures).
    """
    doc = _make_fitz_doc(pages)
    mock_fitz = _make_fitz_module(doc)

    grobid_kwargs = (
        {"side_effect": grobid_side_effect}
        if grobid_side_effect is not None
        else {"return_value": (tei_xml, [])}
    )

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch("pipeline.orchestrator.extract_with_grobid", **grobid_kwargs) as mock_grobid, \
         patch("pipeline.orchestrator.extract_with_pymupdf", return_value=([], [])), \
         patch("pipeline.orchestrator.run_quality_control", side_effect=_fake_run_qc_full) as mock_qc, \
         patch("pipeline.orchestrator.extract_with_pdfplumber", return_value=[], create=True), \
         patch("pipeline.orchestrator.extract_with_paddleocr", return_value=[], create=True), \
         patch("pipeline.orchestrator.scan_detector", create=True) as mock_sd:

        mock_sd.classify_page.return_value = _make_native_classification(0)

        ctx = _build_qc_context(
            Path("test.pdf"),
            "test",
            qc_config,
        )

    return ctx, mock_grobid, mock_qc


# ---------------------------------------------------------------------------
# Unit test 1 — GROBID is called and appears in branches for a native PDF
# Validates: Requirement 3.1
# ---------------------------------------------------------------------------


def test_grobid_called_and_in_branches_for_native_pdf():
    """Preservation: extract_with_grobid() is called for a native PDF and its
    output appears in a BranchOutput(extractor='grobid') branch.

    This behaviour MUST survive the fix unchanged.
    """
    tei_xml = "<TEI><text>native content</text></TEI>"
    pages = [_make_native_page(page_index=0)]

    ctx, mock_grobid, _ = _run_with_mocks(pages, tei_xml, _minimal_qc_config())

    # GROBID must have been called
    mock_grobid.assert_called_once()

    # A BranchOutput with extractor="grobid" must be in ctx.branches
    extractor_names = {b.extractor for b in ctx.branches}
    assert "grobid" in extractor_names, (
        f"Preservation failure: ctx.branches does not contain 'grobid'. "
        f"Got extractors: {extractor_names!r}"
    )

    # The grobid branch payload must equal the TEI XML returned by the mock
    grobid_branch = next(b for b in ctx.branches if b.extractor == "grobid")
    assert grobid_branch.payload == tei_xml, (
        f"Preservation failure: grobid branch payload is {grobid_branch.payload!r}, "
        f"expected {tei_xml!r}"
    )


# ---------------------------------------------------------------------------
# Unit test 2 — run_quality_control is called and QCContext is fully populated
# Validates: Requirement 3.2
# ---------------------------------------------------------------------------


def test_run_quality_control_called_and_qccontext_fully_populated():
    """Preservation: run_quality_control() is called and the returned QCContext
    has unified, reports, iaa_metrics, and decision all set (not None).

    This behaviour MUST survive the fix unchanged.
    """
    tei_xml = "<TEI><text>content</text></TEI>"
    pages = [_make_native_page(page_index=0)]

    ctx, _, mock_qc = _run_with_mocks(pages, tei_xml, _minimal_qc_config())

    # run_quality_control must have been called
    mock_qc.assert_called_once()

    # All four QCContext fields must be set
    assert ctx.unified is not None, (
        "Preservation failure: ctx.unified is None after _build_qc_context"
    )
    assert ctx.reports is not None and len(ctx.reports) > 0, (
        "Preservation failure: ctx.reports is empty after _build_qc_context"
    )
    assert ctx.iaa_metrics is not None, (
        "Preservation failure: ctx.iaa_metrics is None after _build_qc_context"
    )
    assert ctx.decision is not None, (
        "Preservation failure: ctx.decision is None after _build_qc_context"
    )


# ---------------------------------------------------------------------------
# Unit test 3 — GROBID fallback: WARNING logged, continues with empty TEI
# Validates: Requirement 3.3
# ---------------------------------------------------------------------------


def test_grobid_failure_fallback_logs_warning_and_continues(caplog):
    """Preservation: when GROBID raises and failure_behavior='fallback',
    a WARNING is logged and processing continues with empty TEI XML.

    This behaviour MUST survive the fix unchanged.
    """
    pages = [_make_native_page(page_index=0)]
    doc = _make_fitz_doc(pages)
    mock_fitz = _make_fitz_module(doc)

    def _grobid_raises(*args, **kwargs):
        raise RuntimeError("GROBID connection refused")

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch("pipeline.orchestrator.extract_with_grobid", side_effect=_grobid_raises), \
         patch("pipeline.orchestrator.extract_with_pymupdf", return_value=([], [])), \
         patch("pipeline.orchestrator.run_quality_control", side_effect=_fake_run_qc_full), \
         patch("pipeline.orchestrator.extract_with_pdfplumber", return_value=[], create=True), \
         patch("pipeline.orchestrator.extract_with_paddleocr", return_value=[], create=True), \
         patch("pipeline.orchestrator.scan_detector", create=True) as mock_sd:

        mock_sd.classify_page.return_value = _make_native_classification(0)

        with caplog.at_level(logging.WARNING):
            ctx = _build_qc_context(
                Path("fallback_test.pdf"),
                "fallback_test",
                _minimal_qc_config(failure_behavior="fallback"),
            )

    # A WARNING must have been logged
    warning_logged = any(
        r.levelno >= logging.WARNING
        for r in caplog.records
    )
    assert warning_logged, (
        "Preservation failure: no WARNING was logged when GROBID failed with "
        f"failure_behavior='fallback'. caplog.records={[r.message for r in caplog.records]!r}"
    )

    # Processing must have continued — ctx must be returned (not raised)
    assert ctx is not None, (
        "Preservation failure: _build_qc_context raised instead of continuing "
        "with fallback mode"
    )

    # The grobid branch payload must be empty string (fallback)
    grobid_branch = next(
        (b for b in ctx.branches if b.extractor == "grobid"), None
    )
    if grobid_branch is not None:
        assert grobid_branch.payload == "", (
            f"Preservation failure: grobid branch payload is {grobid_branch.payload!r} "
            "after GROBID failure with fallback; expected empty string"
        )


# ---------------------------------------------------------------------------
# Unit test 4 — GROBID manifest_fail: exception is re-raised
# Validates: Requirement 3.4
# ---------------------------------------------------------------------------


def test_grobid_failure_manifest_fail_reraises():
    """Preservation: when GROBID raises and failure_behavior='manifest_fail',
    the exception is re-raised.

    This behaviour MUST survive the fix unchanged.
    """
    pages = [_make_native_page(page_index=0)]
    doc = _make_fitz_doc(pages)
    mock_fitz = _make_fitz_module(doc)

    class _GrobidError(RuntimeError):
        pass

    def _grobid_raises(*args, **kwargs):
        raise _GrobidError("GROBID connection refused")

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch("pipeline.orchestrator.extract_with_grobid", side_effect=_grobid_raises), \
         patch("pipeline.orchestrator.extract_with_pymupdf", return_value=([], [])), \
         patch("pipeline.orchestrator.run_quality_control", side_effect=_fake_run_qc_full), \
         patch("pipeline.orchestrator.extract_with_pdfplumber", return_value=[], create=True), \
         patch("pipeline.orchestrator.extract_with_paddleocr", return_value=[], create=True), \
         patch("pipeline.orchestrator.scan_detector", create=True) as mock_sd:

        mock_sd.classify_page.return_value = _make_native_classification(0)

        with pytest.raises(_GrobidError):
            _build_qc_context(
                Path("manifest_fail_test.pdf"),
                "manifest_fail_test",
                _minimal_qc_config(failure_behavior="manifest_fail"),
            )


# ---------------------------------------------------------------------------
# Unit test 5 — ctx.unified.content keys are set after _build_qc_context
# Validates: Requirement 3.5
# ---------------------------------------------------------------------------


def test_unified_content_keys_set_after_build_qc_context():
    """Preservation: ctx.unified.content['source_pdf_path'] and
    ctx.unified.content['grobid_tei_xml'] are set after _build_qc_context.

    This behaviour MUST survive the fix unchanged.
    """
    tei_xml = "<TEI><text>content for unified</text></TEI>"
    pdf_path = Path("unified_test.pdf")
    pages = [_make_native_page(page_index=0)]
    doc = _make_fitz_doc(pages)
    mock_fitz = _make_fitz_module(doc)

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch("pipeline.orchestrator.extract_with_grobid", return_value=(tei_xml, [])), \
         patch("pipeline.orchestrator.extract_with_pymupdf", return_value=([], [])), \
         patch("pipeline.orchestrator.run_quality_control", side_effect=_fake_run_qc_full), \
         patch("pipeline.orchestrator.extract_with_pdfplumber", return_value=[], create=True), \
         patch("pipeline.orchestrator.extract_with_paddleocr", return_value=[], create=True), \
         patch("pipeline.orchestrator.scan_detector", create=True) as mock_sd:

        mock_sd.classify_page.return_value = _make_native_classification(0)

        ctx = _build_qc_context(pdf_path, "unified_test", _minimal_qc_config())

    assert ctx.unified is not None, (
        "Preservation failure: ctx.unified is None"
    )
    assert isinstance(ctx.unified.content, dict), (
        f"Preservation failure: ctx.unified.content is not a dict, "
        f"got {type(ctx.unified.content)!r}"
    )
    assert "source_pdf_path" in ctx.unified.content, (
        "Preservation failure: 'source_pdf_path' not in ctx.unified.content"
    )
    assert "grobid_tei_xml" in ctx.unified.content, (
        "Preservation failure: 'grobid_tei_xml' not in ctx.unified.content"
    )
    assert ctx.unified.content["source_pdf_path"] == str(pdf_path), (
        f"Preservation failure: source_pdf_path is "
        f"{ctx.unified.content['source_pdf_path']!r}, expected {str(pdf_path)!r}"
    )
    assert ctx.unified.content["grobid_tei_xml"] == tei_xml, (
        f"Preservation failure: grobid_tei_xml is "
        f"{ctx.unified.content['grobid_tei_xml']!r}, expected {tei_xml!r}"
    )


# ---------------------------------------------------------------------------
# PBT 1 — For any native PDF (page count 1–20), GROBID is called and
#          ctx.branches always contains BranchOutput(extractor="grobid")
# Validates: Requirement 3.1
# ---------------------------------------------------------------------------


@given(page_count=st.integers(min_value=1, max_value=20))
@settings(max_examples=10)
def test_pbt_grobid_called_and_in_branches_for_any_native_pdf(page_count: int):
    """**Validates: Requirements 3.1**

    For any native PDF with a random page count (1–20), extract_with_grobid()
    is called and ctx.branches always contains a BranchOutput(extractor='grobid').

    Preservation property: must hold on unfixed code and after every fix.
    """
    pages = [_make_native_page(page_index=i) for i in range(page_count)]
    tei_xml = f"<TEI><text>content for {page_count} pages</text></TEI>"

    ctx, mock_grobid, _ = _run_with_mocks(pages, tei_xml, _minimal_qc_config())

    # GROBID must have been called
    assert mock_grobid.called, (
        f"Preservation failure (page_count={page_count}): "
        "extract_with_grobid() was not called for a native PDF"
    )

    # ctx.branches must contain a grobid branch
    extractor_names = {b.extractor for b in ctx.branches}
    assert "grobid" in extractor_names, (
        f"Preservation failure (page_count={page_count}): "
        f"ctx.branches does not contain 'grobid'. Got: {extractor_names!r}"
    )


# ---------------------------------------------------------------------------
# PBT 2 — For any native PDF, run_quality_control is called and QCContext
#          always has unified, reports, iaa_metrics, and decision set
# Validates: Requirement 3.2
# ---------------------------------------------------------------------------


@given(page_count=st.integers(min_value=1, max_value=20))
@settings(max_examples=10)
def test_pbt_qccontext_fully_populated_for_any_native_pdf(page_count: int):
    """**Validates: Requirements 3.2**

    For any native PDF with a random page count (1–20), run_quality_control()
    is called and the returned QCContext always has unified, reports,
    iaa_metrics, and decision set (not None).

    Preservation property: must hold on unfixed code and after every fix.
    """
    pages = [_make_native_page(page_index=i) for i in range(page_count)]
    tei_xml = f"<TEI><text>content for {page_count} pages</text></TEI>"

    ctx, _, mock_qc = _run_with_mocks(pages, tei_xml, _minimal_qc_config())

    # run_quality_control must have been called
    assert mock_qc.called, (
        f"Preservation failure (page_count={page_count}): "
        "run_quality_control() was not called"
    )

    # All four QCContext fields must be set
    assert ctx.unified is not None, (
        f"Preservation failure (page_count={page_count}): ctx.unified is None"
    )
    assert ctx.reports is not None and len(ctx.reports) > 0, (
        f"Preservation failure (page_count={page_count}): ctx.reports is empty"
    )
    assert ctx.iaa_metrics is not None, (
        f"Preservation failure (page_count={page_count}): ctx.iaa_metrics is None"
    )
    assert ctx.decision is not None, (
        f"Preservation failure (page_count={page_count}): ctx.decision is None"
    )


# ---------------------------------------------------------------------------
# PBT 3 — For any random GROBID TEI XML string, ctx.unified.content
#          ["grobid_tei_xml"] always equals the generated string
# Validates: Requirement 3.5
# ---------------------------------------------------------------------------


@given(tei_xml=st.text(min_size=0, max_size=500))
@settings(max_examples=10)
def test_pbt_grobid_tei_xml_preserved_in_unified_content(tei_xml: str):
    """**Validates: Requirements 3.5**

    For any random GROBID TEI XML string, ctx.unified.content['grobid_tei_xml']
    always equals the generated string after _build_qc_context completes.

    Preservation property: must hold on unfixed code and after every fix.
    """
    pages = [_make_native_page(page_index=0)]

    ctx, _, _ = _run_with_mocks(pages, tei_xml, _minimal_qc_config())

    assert ctx.unified is not None, (
        "Preservation failure: ctx.unified is None"
    )
    assert isinstance(ctx.unified.content, dict), (
        "Preservation failure: ctx.unified.content is not a dict"
    )
    assert "grobid_tei_xml" in ctx.unified.content, (
        "Preservation failure: 'grobid_tei_xml' not in ctx.unified.content"
    )
    assert ctx.unified.content["grobid_tei_xml"] == tei_xml, (
        f"Preservation failure: ctx.unified.content['grobid_tei_xml'] is "
        f"{ctx.unified.content['grobid_tei_xml']!r}, expected {tei_xml!r}"
    )
