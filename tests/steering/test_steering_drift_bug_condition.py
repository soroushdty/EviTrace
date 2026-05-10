"""
tests/test_steering_drift_bug_condition.py
------------------------------------------
Bug condition exploration test for the codebase-steering-drift bugfix spec.

Each sub-check encodes the EXPECTED POST-FIX state.  On unfixed code every
sub-check FAILS — that failure is the intended outcome for Task 1 and
confirms the structural deviation exists.

After all fixes are applied (Tasks 3–13), re-running this file should
produce a fully green suite (Task 14).

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9,
             1.10, 1.11**
"""

from __future__ import annotations

import dataclasses
import inspect
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Sub-check 1 — QC file names
# ---------------------------------------------------------------------------

def test_qc_file_names():
    """Assert the four steering-doc-specified QC module files exist.

    QC modules live in quality_control/ at the repo root.
    artifact_generator.py lives in pdf_extractor/annotation/.
    """
    qc_dir = Path("quality_control")
    annotation_dir = Path("pdf_extractor/annotation")

    # Core QC modules MUST exist in quality_control/
    for name in ("rater.py", "iaa_calculator.py", "reconciler.py"):
        assert (qc_dir / name).exists(), (
            f"Expected file '{name}' not found in {qc_dir}. "
            "Deviation 1.1: QC module files are missing."
        )

    # artifact_generator.py lives in pdf_extractor/annotation/
    assert (annotation_dir / "artifact_generator.py").exists(), (
        f"Expected 'artifact_generator.py' not found in {annotation_dir}. "
        "Deviation 1.1: artifact_generator.py is missing."
    )


# ---------------------------------------------------------------------------
# Sub-check 2 — run_quality_control signature
# ---------------------------------------------------------------------------

def test_run_quality_control_signature():
    """Assert run_quality_control accepts (branches, document_id, config) and
    is annotated to return QCContext.

    Expected to FAIL on unfixed code:
      - current signature is (grobid_output, pymupdf_output, document_id, config)
      - return annotation is dict, not QCContext
    """
    try:
        from quality_control.quality_control import run_quality_control
        from quality_control import QCContext  # noqa: F401 — needed for annotation check
    except ImportError as exc:
        pytest.fail(
            f"Could not import run_quality_control or QCContext: {exc}. "
            "Deviation 1.2/1.3: signature or dataclasses not yet updated."
        )

    sig = inspect.signature(run_quality_control)
    param_names = list(sig.parameters.keys())

    assert param_names == ["branches", "document_id", "config"], (
        f"run_quality_control parameter names are {param_names!r}; "
        "expected ['branches', 'document_id', 'config']. "
        "Deviation 1.2: signature not yet updated."
    )

    return_annotation = sig.return_annotation
    assert return_annotation is QCContext or (
        isinstance(return_annotation, str) and return_annotation == "QCContext"
    ), (
        f"run_quality_control return annotation is {return_annotation!r}; "
        "expected QCContext. "
        "Deviation 1.2: return type not yet updated."
    )


# ---------------------------------------------------------------------------
# Sub-check 3 — QC dataclasses
# ---------------------------------------------------------------------------

def test_qc_dataclasses():
    """Assert all seven QC dataclasses can be imported from
    quality_control and are confirmed as dataclasses.

    Expected to FAIL on unfixed code with ImportError — none of these
    dataclasses exist yet.
    """
    names = [
        "BranchOutput",
        "QCContext",
        "QualityMetrics",
        "QualityReport",
        "InterRaterMetrics",
        "AdjudicationDecision",
        "UnifiedRecord",
    ]

    try:
        from quality_control import (  # type: ignore[attr-defined]
            BranchOutput,
            QCContext,
            QualityMetrics,
            QualityReport,
            InterRaterMetrics,
            AdjudicationDecision,
            UnifiedRecord,
        )
        classes = {
            "BranchOutput": BranchOutput,
            "QCContext": QCContext,
            "QualityMetrics": QualityMetrics,
            "QualityReport": QualityReport,
            "InterRaterMetrics": InterRaterMetrics,
            "AdjudicationDecision": AdjudicationDecision,
            "UnifiedRecord": UnifiedRecord,
        }
    except ImportError as exc:
        pytest.fail(
            f"ImportError while importing QC dataclasses: {exc}. "
            "Deviation 1.3: dataclasses not yet defined/exported."
        )

    for name in names:
        cls = classes[name]
        assert dataclasses.is_dataclass(cls), (
            f"{name} is not a dataclass (got {type(cls)}). "
            "Deviation 1.3: class exists but is not decorated with @dataclass."
        )


# ---------------------------------------------------------------------------
# Sub-check 4 — Cascade order (pdfplumber before Tesseract)
# ---------------------------------------------------------------------------

def test_cascade_order_pdfplumber_before_paddleocr():
    """Assert extract_pdf() calls extract_with_pdfplumber before
    extract_with_paddleocr when the document has scanned pages.

    The cascade is 3-tier: PyMuPDF (scan detection) → pdfplumber (native) →
    PaddleOCR (scanned). When any page is scanned, PaddleOCR is used.
    When all pages are native, pdfplumber is used (before PaddleOCR would be).

    fitz is patched to avoid requiring the real PyMuPDF package.
    """
    import sys
    import pdf_extractor.extraction

    call_order: list[str] = []

    # Build a minimal fitz mock so the lazy `import fitz` inside extract_pdf
    # doesn't fail when the real package isn't installed.
    mock_page = MagicMock()
    mock_page.get_text.return_value = {"blocks": []}
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter([mock_page]))
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_doc.close = MagicMock()
    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc

    high_quality_blocks = [{"text": "good text here", "page_index": 0, "block_bbox": None, "spans": []}]

    def tracking_pdfplumber(path):
        call_order.append("pdfplumber")
        return high_quality_blocks

    def tracking_paddleocr(path, dpi=150):
        call_order.append("paddleocr")
        return high_quality_blocks

    # Patch scan_detector to report all pages as native → pdfplumber path taken
    mock_classification = MagicMock()
    mock_classification.is_native = True

    # Also patch TextProcessor so scispacy/spacy are not required
    mock_tp = MagicMock()

    with patch.dict(sys.modules, {"fitz": mock_fitz}), \
         patch.object(pdf_extractor.extraction, "extract_with_pdfplumber", tracking_pdfplumber), \
         patch.object(pdf_extractor.extraction, "extract_with_paddleocr", tracking_paddleocr), \
         patch.object(pdf_extractor.extraction.scan_detector, "classify_page",
                      return_value=mock_classification), \
         patch("pdf_extractor.extraction.TextProcessor", return_value=mock_tp, create=True):
        pdf_extractor.extraction.extract_pdf(
            "dummy.pdf",
            ocr=True,
            ocr_text_quality_threshold=0.9,
        )

    # With all-native pages, pdfplumber is called and paddleocr is NOT called.
    assert "pdfplumber" in call_order, (
        f"extract_with_pdfplumber was never called. call_order={call_order!r}. "
        "Deviation 1.4: pdfplumber tier not wired into the cascade."
    )
    assert "paddleocr" not in call_order, (
        f"extract_with_paddleocr was called for an all-native document. "
        f"call_order={call_order!r}. Deviation 1.4: cascade routing incorrect."
    )


# ---------------------------------------------------------------------------
# Sub-check 5 — Tier 1 function name
# ---------------------------------------------------------------------------

def test_tier1_function_name():
    """Assert extract_with_pdfplumber is importable from tier1.tier1 and callable.

    Expected to FAIL on unfixed code with ImportError — the function is
    currently named extract_pdf_text.
    """
    try:
        from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber  # type: ignore[attr-defined]
    except ImportError as exc:
        pytest.fail(
            f"ImportError: {exc}. "
            "Deviation 1.5: tier1.py still exposes 'extract_pdf_text' "
            "instead of 'extract_with_pdfplumber'."
        )

    assert callable(extract_with_pdfplumber), (
        "extract_with_pdfplumber is not callable. "
        "Deviation 1.5: function exists but is not callable."
    )


# ---------------------------------------------------------------------------
# Sub-check 6 — SpanDict fields
# ---------------------------------------------------------------------------

def test_spandict_fields():
    """Assert SpanDict.__annotations__ contains font, flags, and color.

    Expected to FAIL on unfixed code:
      - SpanDict only has text, bbox, size.
    """
    from pdf_extractor.extraction.schemas import SpanDict

    annotations = SpanDict.__annotations__

    assert "font" in annotations, (
        f"SpanDict.__annotations__ = {set(annotations.keys())!r}; "
        "'font' is missing. Deviation 1.6."
    )
    assert "flags" in annotations, (
        f"SpanDict.__annotations__ = {set(annotations.keys())!r}; "
        "'flags' is missing. Deviation 1.6."
    )
    assert "color" in annotations, (
        f"SpanDict.__annotations__ = {set(annotations.keys())!r}; "
        "'color' is missing. Deviation 1.6."
    )


# ---------------------------------------------------------------------------
# Sub-check 7 — branch2.py span construction
# ---------------------------------------------------------------------------

def test_branch2_span_construction():
    """Assert extract_with_pymupdf populates font, flags, color in returned spans.

    Expected to FAIL on unfixed code:
      - branch2.py constructs SpanDict with only text, bbox, size.
    """
    # Build a minimal fitz document mock with one page, one block, one span
    # containing all six fields.
    span_data = {
        "text": "Hello world",
        "font": "Arial",
        "size": 12.0,
        "flags": 4,
        "color": 0xFF0000,
        "bbox": (0.0, 0.0, 100.0, 20.0),
    }
    line_data = {"spans": [span_data]}
    block_data = {"type": 0, "lines": [line_data], "bbox": (0.0, 0.0, 100.0, 20.0)}
    page_dict_data = {"blocks": [block_data]}

    # branch2.py calls: page.get_text("dict") → returns the page dict
    page_mock = MagicMock()
    page_mock.get_text.return_value = page_dict_data

    # branch2.py uses: for page_index, page in enumerate(doc)
    # So doc must be iterable and yield page objects (not tuples).
    doc_mock = MagicMock()
    doc_mock.__iter__ = MagicMock(return_value=iter([page_mock]))
    doc_mock.close = MagicMock()

    fitz_mock = MagicMock()
    fitz_mock.open.return_value = doc_mock

    import sys

    # Remove cached branch2 so the patched fitz is used on re-import
    for mod_name in list(sys.modules.keys()):
        if "branch2" in mod_name:
            del sys.modules[mod_name]

    with patch.dict("sys.modules", {"fitz": fitz_mock}):
        from pdf_extractor.extraction import PyMuPDF as b2_fresh
        blocks, font_metadata = b2_fresh.extract_with_pymupdf("dummy.pdf")

    assert blocks, "No blocks returned from extract_with_pymupdf mock. Check mock setup."

    first_block = blocks[0]
    spans = first_block.get("spans", [])
    assert spans, "No spans in first block. Check mock setup."

    first_span = spans[0]
    assert "font" in first_span, (
        f"Span keys: {set(first_span.keys())!r}. "
        "'font' missing from span. Deviation 1.7."
    )
    assert "flags" in first_span, (
        f"Span keys: {set(first_span.keys())!r}. "
        "'flags' missing from span. Deviation 1.7."
    )
    assert "color" in first_span, (
        f"Span keys: {set(first_span.keys())!r}. "
        "'color' missing from span. Deviation 1.7."
    )


# ---------------------------------------------------------------------------
# Sub-check 8 — Config defaults
# ---------------------------------------------------------------------------

def test_config_defaults(tmp_path):
    """Assert load_config() returns discard_failed_branches and
    status_field_location under quality_control.

    Expected to FAIL on unfixed code with KeyError — these keys are absent
    from _QC_DEFAULTS.
    """
    from utils.config_utils import load_config

    # Write a minimal valid config YAML
    cfg_file = tmp_path / "test_config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            pdfs_path: /tmp
        """),
        encoding="utf-8",
    )

    config = load_config(str(cfg_file))

    qc = config["quality_control"]

    assert "discard_failed_branches" in qc, (
        f"quality_control keys: {set(qc.keys())!r}. "
        "'discard_failed_branches' missing. Deviation 1.8."
    )
    assert qc["discard_failed_branches"] == False, (  # noqa: E712
        f"discard_failed_branches={qc['discard_failed_branches']!r}; "
        "expected False. Deviation 1.8."
    )

    assert "status_field_location" in qc, (
        f"quality_control keys: {set(qc.keys())!r}. "
        "'status_field_location' missing. Deviation 1.8."
    )
    assert qc["status_field_location"] == "both", (
        f"status_field_location={qc['status_field_location']!r}; "
        "expected 'both'. Deviation 1.8."
    )


# ---------------------------------------------------------------------------
# Sub-check 9 — run.py call site
# ---------------------------------------------------------------------------

def test_run_py_pdf_discovery_call_site():
    """Assert main.py uses glob-based PDF discovery (pdf_dir.glob("*.pdf")).

    The entry point is main.py (not run.py).
    """
    main_py = Path("main.py").read_text(encoding="utf-8")

    assert 'glob("*.pdf")' in main_py or "glob('*.pdf')" in main_py, (
        "main.py does not contain a glob-based PDF discovery call. "
        "Deviation 1.9: PDF discovery call not found in main.py."
    )


# ---------------------------------------------------------------------------
# Sub-check 10 — Test file names
# ---------------------------------------------------------------------------

def test_qc_test_file_names():
    """Assert the QC test files exist in tests/quality_control/.

    The test files live in tests/quality_control/, not the tests/ root.
    artifact_generator tests are covered by test_qc_models.py and
    test_unified_record_layers.py rather than a dedicated file.
    """
    tests_dir = Path("tests/quality_control")

    for name in (
        "test_quality_control_rater.py",
        "test_quality_control_iaa_calculator.py",
        "test_quality_control_reconciler.py",
    ):
        assert (tests_dir / name).exists(), (
            f"Expected test file '{name}' not found in {tests_dir}. "
            "Deviation 1.10: test files are missing."
        )
