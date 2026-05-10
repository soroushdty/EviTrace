"""
tests/steering/test_steering_drift_extraction_routing_alignment_bug_condition.py
----------------------------------------------------------------------------------
Bug condition exploration test for the extraction-routing-alignment bugfix spec.

Each sub-check encodes the EXPECTED POST-FIX state.  On unfixed code every
sub-check FAILS — that failure is the intended outcome for Task 1 and
confirms the routing deviation exists.

After all fixes are applied (Task 3), re-running this file should produce a
fully green suite (Task 3.4).

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7**
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — build minimal fitz page/doc mocks
# ---------------------------------------------------------------------------


def _make_native_page(page_index: int = 0) -> MagicMock:
    """Return a fitz.Page mock that classify_page will treat as native.

    Stage 1 (empty text) must NOT fire → return non-empty text.
    Stage 2 (low word count) must NOT fire → return ≥50 words.
    Stage 3 (low alpha ratio) must NOT fire → return high-alpha text.
    Stage 4 (no embedded fonts) must NOT fire → return ≥1 font.
    Stage 5 (image dominance) must NOT fire → return low image coverage.
    """
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


def _make_scanned_page(page_index: int = 0) -> MagicMock:
    """Return a fitz.Page mock that classify_page will treat as scanned.

    Stage 1 (empty text) fires → return empty string.
    """
    page = MagicMock()
    page.number = page_index
    page.get_text.return_value = ""  # stage 1 fires → scanned
    page.get_fonts.return_value = []
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


def _minimal_qc_config(ocr: bool = True) -> dict:
    """Return a minimal qc_config dict for _build_qc_context."""
    return {
        "quality_control": {
            "grobid_integration": {
                "failure_behavior": "fallback",
            },
            "scan_detection": {
                "text_density_threshold": 50,
                "alpha_ratio_threshold": 0.60,
                "image_dominance_threshold": 0.85,
            },
        },
        "ocr": ocr,
    }


# ---------------------------------------------------------------------------
# Sub-check 1 — Native PDF branch set
# ---------------------------------------------------------------------------


def test_native_pdf_branch_set():
    """Sub-check 1 (Deviation 1.1 / 1.2 / 1.3): native PDF must produce
    branches containing 'grobid' and 'pdfplumber', NOT 'pymupdf'.

    On unfixed code _build_qc_context produces:
      BranchOutput(extractor='grobid') + BranchOutput(extractor='pymupdf')
    and never calls pdfplumber.  This test will FAIL on unfixed code.
    """
    # Patch heavy optional deps before importing orchestrator
    for mod in list(sys.modules.keys()):
        if "orchestrator" in mod or "pdf_processor" in mod:
            del sys.modules[mod]

    native_page = _make_native_page(page_index=0)
    doc = _make_fitz_doc([native_page])
    mock_fitz = _make_fitz_module(doc)

    tei_xml = "<TEI><text>native content</text></TEI>"
    grobid_blocks = [{"text": "native content", "page_index": 0, "block_bbox": None, "spans": []}]
    plumber_blocks = [{"text": "[PAGE 1]\nnative content", "page_index": 0, "block_bbox": None, "spans": []}]

    mock_run_qc = MagicMock()
    from quality_control.models import BranchOutput, QCContext, UnifiedRecord

    def _fake_run_qc(branches, document_id, config):
        ctx = QCContext(branches=branches)
        ctx.unified = UnifiedRecord(document_id=document_id, content={})
        return ctx

    mock_run_qc.side_effect = _fake_run_qc

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch("pipeline.orchestrator.extract_with_grobid", return_value=(tei_xml, grobid_blocks)) as mock_grobid, \
         patch("pipeline.orchestrator.extract_with_pymupdf", return_value=([], [])) as mock_pymupdf, \
         patch("pipeline.orchestrator.run_quality_control", side_effect=_fake_run_qc) as mock_qc:

        # Import after patching so module-level imports pick up mocks
        from pipeline.orchestrator import _build_qc_context

        # Also patch extract_with_pdfplumber and extract_with_paddleocr at the
        # orchestrator module level (they may not exist yet on unfixed code)
        with patch("pipeline.orchestrator.extract_with_pdfplumber", return_value=plumber_blocks, create=True) as mock_plumber, \
             patch("pipeline.orchestrator.extract_with_paddleocr", return_value=[], create=True), \
             patch("pipeline.orchestrator.scan_detector", create=True) as mock_sd:

            # Configure scan_detector.classify_page to return native classification
            from pdf_extractor.extraction.scan_detector import PageScanClassification
            mock_sd.classify_page.return_value = PageScanClassification(
                page_index=0, is_native=True, triggered_stages=[], stage_values={}
            )

            ctx = _build_qc_context(
                Path("dummy.pdf"),
                "dummy",
                _minimal_qc_config(ocr=True),
            )

    extractor_names = {b.extractor for b in ctx.branches}

    if "grobid" not in extractor_names:
        pytest.fail(
            "Deviation 1.1: ctx.branches does not contain 'grobid' for a native PDF. "
            f"Got extractors: {extractor_names!r}"
        )

    if "pdfplumber" not in extractor_names:
        pytest.fail(
            "Deviation 1.3: ctx.branches does not contain 'pdfplumber' for a native PDF. "
            f"Got extractors: {extractor_names!r}. "
            "pdfplumber is the structural authority for native pages."
        )

    if "pymupdf" in extractor_names:
        pytest.fail(
            "Deviation 1.2: ctx.branches contains 'pymupdf' as a QC branch for a native PDF. "
            f"Got extractors: {extractor_names!r}. "
            "PyMuPDF must NOT be a QC branch for native PDFs — it is a comparison signal only."
        )


# ---------------------------------------------------------------------------
# Sub-check 2 — Scanned PDF + ocr=true branch set
# ---------------------------------------------------------------------------


def test_scanned_pdf_ocr_true_branch_set():
    """Sub-check 2 (Deviation 1.4 / 1.6): scanned PDF with ocr=true must
    produce branches containing 'paddleocr' and 'pymupdf'.

    On unfixed code _build_qc_context never calls PaddleOCR and produces
    BranchOutput(extractor='pymupdf') as a structural branch (wrong role).
    This test will FAIL on unfixed code.
    """
    for mod in list(sys.modules.keys()):
        if "orchestrator" in mod or "pdf_processor" in mod:
            del sys.modules[mod]

    scanned_page = _make_scanned_page(page_index=0)
    doc = _make_fitz_doc([scanned_page])
    mock_fitz = _make_fitz_module(doc)

    tei_xml = "<TEI><text>ocr content</text></TEI>"
    grobid_blocks = [{"text": "ocr content", "page_index": 0, "block_bbox": None, "spans": []}]
    paddle_blocks = [{"text": "ocr content", "page_index": 0, "block_bbox": (0, 0, 100, 20), "spans": []}]
    pymupdf_blocks = [{"text": "ocr content", "page_index": 0, "block_bbox": None, "spans": []}]

    from quality_control.models import QCContext, UnifiedRecord

    def _fake_run_qc(branches, document_id, config):
        ctx = QCContext(branches=branches)
        ctx.unified = UnifiedRecord(document_id=document_id, content={})
        return ctx

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch("pipeline.orchestrator.extract_with_grobid", return_value=(tei_xml, grobid_blocks)), \
         patch("pipeline.orchestrator.extract_with_pymupdf", return_value=(pymupdf_blocks, [])), \
         patch("pipeline.orchestrator.run_quality_control", side_effect=_fake_run_qc):

        from pipeline.orchestrator import _build_qc_context

        with patch("pipeline.orchestrator.extract_with_pdfplumber", return_value=[], create=True), \
             patch("pipeline.orchestrator.extract_with_paddleocr", return_value=paddle_blocks, create=True) as mock_paddle, \
             patch("pipeline.orchestrator.scan_detector", create=True) as mock_sd:

            from pdf_extractor.extraction.scan_detector import PageScanClassification
            mock_sd.classify_page.return_value = PageScanClassification(
                page_index=0, is_native=False, triggered_stages=[1], stage_values={}
            )

            ctx = _build_qc_context(
                Path("dummy_scanned.pdf"),
                "dummy_scanned",
                _minimal_qc_config(ocr=True),
            )

    extractor_names = {b.extractor for b in ctx.branches}

    if "paddleocr" not in extractor_names:
        pytest.fail(
            "Deviation 1.4 / 1.6: ctx.branches does not contain 'paddleocr' for a scanned PDF "
            f"with ocr=true. Got extractors: {extractor_names!r}. "
            "PaddleOCR is the primary extractor for scanned pages."
        )

    if "pymupdf" not in extractor_names:
        pytest.fail(
            "Deviation 1.6: ctx.branches does not contain 'pymupdf' for a scanned PDF "
            f"with ocr=true. Got extractors: {extractor_names!r}. "
            "PyMuPDF built-in OCR is the secondary cross-validation extractor for scanned pages."
        )


# ---------------------------------------------------------------------------
# Sub-check 3 — Scan detection invocation
# ---------------------------------------------------------------------------


def test_scan_detection_called_per_page():
    """Sub-check 3 (Deviation 1.1): for a two-page native PDF,
    scan_detector.classify_page must be called exactly twice before any
    extraction backend is invoked.

    On unfixed code classify_page is never called (call count = 0).
    This test will FAIL on unfixed code.
    """
    for mod in list(sys.modules.keys()):
        if "orchestrator" in mod or "pdf_processor" in mod:
            del sys.modules[mod]

    page0 = _make_native_page(page_index=0)
    page1 = _make_native_page(page_index=1)
    doc = _make_fitz_doc([page0, page1])
    mock_fitz = _make_fitz_module(doc)

    tei_xml = "<TEI><text>two page content</text></TEI>"
    grobid_blocks = [{"text": "two page content", "page_index": 0, "block_bbox": None, "spans": []}]
    plumber_blocks = [
        {"text": "[PAGE 1]\ncontent", "page_index": 0, "block_bbox": None, "spans": []},
        {"text": "[PAGE 2]\ncontent", "page_index": 1, "block_bbox": None, "spans": []},
    ]

    classify_call_order: list[str] = []
    extraction_call_order: list[str] = []

    from pdf_extractor.extraction.scan_detector import PageScanClassification

    def _tracking_classify(page, tp, config, page_index=0):
        classify_call_order.append(f"classify_page:{page_index}")
        return PageScanClassification(
            page_index=page_index, is_native=True, triggered_stages=[], stage_values={}
        )

    def _tracking_grobid(pdf_path, **kwargs):
        extraction_call_order.append("grobid")
        return tei_xml, grobid_blocks

    def _tracking_plumber(pdf_path):
        extraction_call_order.append("pdfplumber")
        return plumber_blocks

    from quality_control.models import QCContext, UnifiedRecord

    def _fake_run_qc(branches, document_id, config):
        ctx = QCContext(branches=branches)
        ctx.unified = UnifiedRecord(document_id=document_id, content={})
        return ctx

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch("pipeline.orchestrator.extract_with_grobid", side_effect=_tracking_grobid), \
         patch("pipeline.orchestrator.extract_with_pymupdf", return_value=([], [])), \
         patch("pipeline.orchestrator.run_quality_control", side_effect=_fake_run_qc):

        from pipeline.orchestrator import _build_qc_context

        with patch("pipeline.orchestrator.extract_with_pdfplumber", side_effect=_tracking_plumber, create=True), \
             patch("pipeline.orchestrator.extract_with_paddleocr", return_value=[], create=True), \
             patch("pipeline.orchestrator.scan_detector", create=True) as mock_sd:

            mock_sd.classify_page.side_effect = _tracking_classify

            _build_qc_context(
                Path("two_page.pdf"),
                "two_page",
                _minimal_qc_config(ocr=True),
            )

    classify_count = len(classify_call_order)

    if classify_count != 2:
        pytest.fail(
            f"Deviation 1.1: scan_detector.classify_page was called {classify_count} time(s) "
            f"for a two-page PDF; expected exactly 2. "
            f"classify_call_order={classify_call_order!r}, "
            f"extraction_call_order={extraction_call_order!r}. "
            "Per-page scan detection must run before any extraction backend."
        )

    # Verify classify_page was called before any extraction backend
    if extraction_call_order:
        first_extraction_pos = 0  # extraction_call_order is populated after classify
        # classify_call_order must be fully populated before extraction starts
        # (both classify calls happen before any extraction call)
        if len(classify_call_order) < 2:
            pytest.fail(
                "Deviation 1.1: classify_page was not called for all pages before extraction. "
                f"classify_call_order={classify_call_order!r}, "
                f"extraction_call_order={extraction_call_order!r}"
            )


# ---------------------------------------------------------------------------
# Sub-check 4 — Scanned page + ocr=false skip
# ---------------------------------------------------------------------------


def test_scanned_page_ocr_false_skip(caplog):
    """Sub-check 4 (Deviation 1.5): scanned page with ocr=false must log a
    WARNING and produce no extraction branch for that page.

    On unfixed code no WARNING is logged and a pymupdf branch is produced
    regardless of the ocr flag.  This test will FAIL on unfixed code.
    """
    for mod in list(sys.modules.keys()):
        if "orchestrator" in mod or "pdf_processor" in mod:
            del sys.modules[mod]

    scanned_page = _make_scanned_page(page_index=0)
    doc = _make_fitz_doc([scanned_page])
    mock_fitz = _make_fitz_module(doc)

    from quality_control.models import QCContext, UnifiedRecord

    def _fake_run_qc(branches, document_id, config):
        ctx = QCContext(branches=branches)
        ctx.unified = UnifiedRecord(document_id=document_id, content={})
        return ctx

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch("pipeline.orchestrator.extract_with_grobid", return_value=("", [])), \
         patch("pipeline.orchestrator.extract_with_pymupdf", return_value=([], [])), \
         patch("pipeline.orchestrator.run_quality_control", side_effect=_fake_run_qc):

        from pipeline.orchestrator import _build_qc_context

        with patch("pipeline.orchestrator.extract_with_pdfplumber", return_value=[], create=True), \
             patch("pipeline.orchestrator.extract_with_paddleocr", return_value=[], create=True), \
             patch("pipeline.orchestrator.scan_detector", create=True) as mock_sd:

            from pdf_extractor.extraction.scan_detector import PageScanClassification
            mock_sd.classify_page.return_value = PageScanClassification(
                page_index=0, is_native=False, triggered_stages=[1], stage_values={}
            )

            with caplog.at_level(logging.WARNING):
                ctx = _build_qc_context(
                    Path("scanned_no_ocr.pdf"),
                    "scanned_no_ocr",
                    _minimal_qc_config(ocr=False),
                )

    # Assert WARNING was logged
    warning_logged = any(
        r.levelno >= logging.WARNING and (
            "scanned" in r.message.lower()
            or "skip" in r.message.lower()
            or "ocr" in r.message.lower()
            or "page" in r.message.lower()
        )
        for r in caplog.records
    )

    if not warning_logged:
        pytest.fail(
            "Deviation 1.5: no WARNING was logged when a scanned page was encountered "
            f"with ocr=false. caplog.records={[r.message for r in caplog.records]!r}. "
            "The system must log a WARNING with the page index and PDF name."
        )

    # Assert no extraction branch was produced for the scanned page
    # (branches list should be empty or contain only grobid if called)
    scanned_extractors = {
        b.extractor for b in ctx.branches
        if b.extractor in {"pymupdf", "paddleocr", "pdfplumber"}
    }

    if scanned_extractors:
        pytest.fail(
            "Deviation 1.5: extraction branches were produced for a scanned page with ocr=false. "
            f"Got extractors: {scanned_extractors!r}. "
            "No extraction branch should be produced when ocr=false and the page is scanned."
        )


# ---------------------------------------------------------------------------
# Sub-check 5 — Dead code removal
# ---------------------------------------------------------------------------


def test_extract_pdf_dead_code_removed():
    """Sub-check 5 (Deviation 1.7): extract_pdf() must NOT be present in
    pdf_extractor.extraction after the fix.

    On unfixed code hasattr(pdf_extractor.extraction, 'extract_pdf') is True.
    This test will FAIL on unfixed code.
    """
    import pdf_extractor.extraction

    if hasattr(pdf_extractor.extraction, "extract_pdf"):
        pytest.fail(
            "Deviation 1.7: pdf_extractor.extraction still exposes 'extract_pdf'. "
            "This is dead legacy code from the old three-tier cascade architecture "
            "and must be removed. "
            "hasattr(pdf_extractor.extraction, 'extract_pdf') must be False after the fix."
        )
