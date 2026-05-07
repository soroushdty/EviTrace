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
    """Assert the four steering-doc-specified QC module files exist and the
    four old names do NOT exist.

    Expected to FAIL on unfixed code:
      - artifact_generator.py, rater.py, iaa_calculator.py, reconciler.py
        are absent
      - artifacts.py, observer.py, investigator.py, repair.py are present
    """
    qc_dir = Path("pdf_extractor/extraction/quality_control")

    # New names MUST exist
    for name in ("artifact_generator.py", "rater.py", "iaa_calculator.py", "reconciler.py"):
        assert (qc_dir / name).exists(), (
            f"Expected file '{name}' not found in {qc_dir}. "
            "Deviation 1.1: QC module files have not been renamed."
        )

    # Old names MUST NOT exist
    for name in ("artifacts.py", "observer.py", "investigator.py", "repair.py"):
        assert not (qc_dir / name).exists(), (
            f"Old file '{name}' still exists in {qc_dir}. "
            "Deviation 1.1: QC module files have not been renamed."
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
        from pdf_extractor.extraction.quality_control.quality_control import run_quality_control
        from pdf_extractor.extraction.quality_control import QCContext  # noqa: F401 — needed for annotation check
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
    pdf_extractor.extraction.quality_control and are confirmed as dataclasses.

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
        from pdf_extractor.extraction.quality_control import (  # type: ignore[attr-defined]
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

def test_cascade_order_pdfplumber_before_tesseract():
    """Assert extract_pdf() calls extract_with_pdfplumber before
    extract_with_tesseract when PyMuPDF quality is below threshold.

    Expected to FAIL on unfixed code:
      - extract_with_pdfplumber is never imported or called in
        pdf_extractor/extraction/__init__.py; Tesseract is called directly.
    """
    import pdf_extractor.extraction

    # Low-quality blocks: all non-alpha characters so score ≈ 0.0
    low_quality_blocks = [{"text": "!@#$%^&*()", "page_index": 0, "block_bbox": None, "spans": []}]
    high_quality_blocks = [{"text": "good text here", "page_index": 0, "block_bbox": None, "spans": []}]

    pymupdf_mock = MagicMock(return_value=(low_quality_blocks, []))
    pdfplumber_mock = MagicMock(return_value=high_quality_blocks)
    tesseract_mock = MagicMock(return_value=high_quality_blocks)
    paddleocr_mock = MagicMock(return_value=high_quality_blocks)

    call_order: list[str] = []

    def tracking_pymupdf(path):
        call_order.append("pymupdf")
        return low_quality_blocks, []

    def tracking_pdfplumber(path):
        call_order.append("pdfplumber")
        return high_quality_blocks

    def tracking_tesseract(path):
        call_order.append("tesseract")
        return high_quality_blocks

    def tracking_paddleocr(path):
        call_order.append("paddleocr")
        return high_quality_blocks

    # Patch at the text_extractor module level
    with patch.object(text_extractor, "extract_with_pymupdf", tracking_pymupdf):
        with patch.object(text_extractor, "extract_with_tesseract", tracking_tesseract):
            with patch.object(text_extractor, "extract_with_paddleocr", tracking_paddleocr):
                # extract_with_pdfplumber may not exist yet — handle gracefully
                try:
                    with patch.object(text_extractor, "extract_with_pdfplumber", tracking_pdfplumber):
                        pdf_extractor.extraction.extract_pdf(
                            "dummy.pdf",
                            ocr=True,
                            ocr_text_quality_threshold=0.9,
                        )
                except AttributeError:
                    # extract_with_pdfplumber not yet wired into text_extractor
                    # Run without it so we can still check call_order
                    pdf_extractor.extraction.extract_pdf(
                        "dummy.pdf",
                        ocr=True,
                        ocr_text_quality_threshold=0.9,
                    )

    assert "pdfplumber" in call_order, (
        f"extract_with_pdfplumber was never called. call_order={call_order!r}. "
        "Deviation 1.4: pdfplumber tier not wired into the cascade."
    )

    pdfplumber_idx = call_order.index("pdfplumber")
    tesseract_idx = call_order.index("tesseract") if "tesseract" in call_order else float("inf")

    assert pdfplumber_idx < tesseract_idx, (
        f"pdfplumber called at position {pdfplumber_idx} but tesseract at "
        f"{tesseract_idx}; pdfplumber must come first. "
        "Deviation 1.4: cascade order incorrect."
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
        from pdf_extractor.extraction.tier1.tier1 import extract_with_pdfplumber  # type: ignore[attr-defined]
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
        from pdf_extractor.extraction.core import branch2 as b2_fresh
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
    from pdf_extractor.utils.config_utils import load_config

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
    """Assert run.py uses list_pdf_files_from_source, not list_pdf_files_from_dir.

    Expected to FAIL on unfixed code:
      - run.py calls list_pdf_files_from_dir.
    """
    run_py = Path("run.py").read_text(encoding="utf-8")

    assert "list_pdf_files_from_source" in run_py, (
        "run.py does not contain 'list_pdf_files_from_source'. "
        "Deviation 1.9: PDF discovery call not yet updated."
    )
    assert "list_pdf_files_from_dir" not in run_py, (
        "run.py still contains 'list_pdf_files_from_dir'. "
        "Deviation 1.9: old PDF discovery call not yet replaced."
    )


# ---------------------------------------------------------------------------
# Sub-check 10 — Test file names
# ---------------------------------------------------------------------------

def test_qc_test_file_names():
    """Assert the four steering-doc-specified QC test files exist.

    Expected to FAIL on unfixed code:
      - test files still use old names (artifacts, observer, investigator, repair).
    """
    tests_dir = Path("tests")

    for name in (
        "test_quality_control_artifact_generator.py",
        "test_quality_control_rater.py",
        "test_quality_control_iaa_calculator.py",
        "test_quality_control_reconciler.py",
    ):
        assert (tests_dir / name).exists(), (
            f"Expected test file '{name}' not found in {tests_dir}. "
            "Deviation 1.10: test files have not been renamed."
        )
