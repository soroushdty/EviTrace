# Test Suite Bugfix — Design


## Overview

The EviTrace test suite has 4 collection errors and 51 test failures caused by eight distinct
root-cause categories. All failures are in test code only — no production behaviour is changed
by any fix in this spec.

The bug condition is: **the test suite cannot be collected and run cleanly**. The fix is a
targeted set of changes to test files and one production `__init__.py`, applied in dependency
order so that each step unblocks the next.

**Fix order (dependency-driven):**

1. REQ-1 — Delete `test_metrics_hierarchy.py`; fix imports in three other test files.
2. REQ-2 — Re-export `extract_with_pymupdf` from `pdf_extractor/extraction/__init__.py`.
3. REQ-3 — Rewrite `test_text_extractor_orchestrator.py` to match current architecture.
4. REQ-4 — Add scispaCy/spaCy autouse mocks to five test files.
5. REQ-5 — Fix `pdf2image` mock setup in `test_scan_detector_routing.py`.
6. REQ-6 — Add `MockFaiss` class to `test_embedding_utils.py`.
7. REQ-7 — Fix two logic failures in `test_quality_control_pipeline.py`.
8. REQ-8 — Fix `git grep` exclusion pattern in `test_domain_agnosticism.py`.

REQ-2 must precede REQ-3 and REQ-4 because those tests patch
`pdf_extractor.extraction.extract_with_pymupdf`, which only resolves after REQ-2 adds the
re-export. REQ-4 must precede REQ-7b because `test_full_pipeline_integration` fails due to
the missing scispaCy mock, not a logic error.


---

## Glossary

- **Bug_Condition (C)**: Any state in which `python -m pytest --collect-only` produces a
  collection error, or any test that was previously passing now fails due to a broken import,
  missing symbol, or incorrect mock setup.
- **Property (P)**: The desired post-fix state — zero collection errors and all 51 previously
  failing tests pass (excluding the four intentionally-failing steering-drift tests listed in
  the requirements Out of Scope section).
- **Preservation**: All tests that currently pass must continue to pass after each fix is
  applied. No production module behaviour is altered except for the single re-export in
  `pdf_extractor/extraction/__init__.py` (REQ-2), which is additive only.
- **extract_with_pymupdf**: The function in `pdf_extractor/extraction/PyMuPDF.py` that
  extracts text and font metadata using the `fitz` library. Currently not re-exported from
  the package `__init__`, which breaks `unittest.mock.patch` calls that target
  `pdf_extractor.extraction.extract_with_pymupdf`.
- **scan-detector routing**: The current architecture in
  `pdf_extractor/extraction/__init__.py` — `extract_pdf` classifies each page with
  `scan_detector.classify_page`, routes native pages to pdfplumber + font metadata, and
  scanned pages to PaddleOCR. Replaces the old waterfall cascade.
- **waterfall cascade**: The old architecture (now deleted from production code) that called
  `_compute_quality_score` on PyMuPDF, pdfplumber, and PaddleOCR outputs in sequence.
  `test_text_extractor_orchestrator.py` still tests this removed architecture.
- **ScispaCySentenceSegment**: The default `SentenceSegment` backend in `TextProcessor`.
  Its `__init__` calls `spacy.load("en_core_sci_sm")` eagerly, which fails in CI because
  `scispacy` and `en_core_sci_sm` are not installed.
- **autouse fixture**: A pytest fixture decorated with `autouse=True` that runs automatically
  for every test in its scope without being explicitly requested.


---

## Bug Details

### Bug Condition

The test suite fails to collect or run cleanly. The bug manifests in eight independent
sub-conditions, each with a distinct root cause.

**Formal Specification:**

```
FUNCTION isBugCondition(test_run_result)
  INPUT: test_run_result — output of `python -m pytest --collect-only` or `python -m pytest`
  OUTPUT: boolean

  RETURN (collection_errors(test_run_result) > 0)
      OR (failing_tests(test_run_result) > 0
          AND failing_tests(test_run_result) NOT IN intentionally_failing_set)
END FUNCTION

WHERE intentionally_failing_set = {
  "test_steering_drift_bug_condition.py::test_qc_file_names",
  "test_steering_drift_bug_condition.py::test_run_py_pdf_discovery_call_site",
  "test_steering_drift_bug_condition.py::test_qc_test_file_names",
  "test_steering_drift_bug_condition.py::test_cascade_order_pdfplumber_before_paddleocr",
}
```

### Examples of Bug Manifestation

**REQ-1 (collection errors):**
- `tests/pdf_extractor/test_logging_utils.py` — `ImportError: cannot import name 'setup_logger'
  from 'pdf_extractor.utils.logging_utils'` (module does not exist; correct path is
  `utils.logging_utils`, which exposes `get_logger` and `setup_logging`, not `setup_logger`).
- `tests/pdf_extractor/test_parser_pipeline.py` — `ImportError: cannot import name
  'parse_document' from 'pdf_extractor.extraction.pdfplumber'` (function was renamed to
  `extract_with_pdfplumber`).
- `tests/pdf_extractor/test_quality_control_artifact_generator.py` — `ImportError: No module
  named 'quality_control.artifact_generator'` (module moved to
  `pdf_extractor.artifact_generator`).
- `tests/pdf_extractor/test_metrics_hierarchy.py` — `ImportError: cannot import name
  'build_metrics_hierarchy' from 'pdf_extractor.processing.sentence_processor'` (function
  does not exist and is not planned).

**REQ-2 (AttributeError at patch time):**
- `patch("pdf_extractor.extraction.extract_with_pymupdf", ...)` raises
  `AttributeError: <module 'pdf_extractor.extraction'> does not have the attribute
  'extract_with_pymupdf'` because `__init__.py` imports `PyMuPDF` as a module object but
  does not re-export the function.

**REQ-3 (test logic mismatch):**
- `test_text_extractor_orchestrator.py` patches `pdf_extractor.extraction.extract_with_pymupdf`
  and `pdf_extractor.extraction._compute_quality_score`, neither of which exists at the
  patched path in the current architecture. All 10 tests fail with `AttributeError`.

**REQ-4 (spacy.load failure):**
- Any test that constructs `TextProcessor()` without mocking `spacy` raises
  `OSError: [E050] Can't find model 'en_core_sci_sm'` or
  `ModuleNotFoundError: No module named 'scispacy'`.

**REQ-5 (pdf2image import failure):**
- `TestExtractWithPaddleOCRCoordinateConversion._run_paddleocr_with_mock` patches
  `pdf2image.pdfinfo_from_path` and `pdf2image.convert_from_path` as individual attributes,
  but `PaddleOCR.py` calls `_ensure_pdf2image()` which does `import pdf2image` before the
  attribute patches are in scope. Since `pdf2image` is not installed, the import fails with
  `ModuleNotFoundError`.

**REQ-6 (NameError):**
- `TestEmbedQueryShape.test_embed_query_returns_correct_shape` and
  `test_embed_query_shape_with_small_dim` both call `MockFaiss()`, but `MockFaiss` is never
  defined in `test_embedding_utils.py`. Fails with `NameError: name 'MockFaiss' is not defined`.

**REQ-7a (MagicMock iteration):**
- `test_reconciler_call_is_strategy_driven_and_extractor_agnostic` mocks
  `reconciler.reconcile` to return a `UnifiedRecord` with `semantic=MagicMock(sentences=[])`.
  The reconciler closure in `quality_control.quality_control` then iterates
  `updated_unified.semantic.paragraphs` — but `MagicMock().paragraphs` is itself a
  `MagicMock`, not a list, so iteration raises `TypeError` or produces unexpected results.

**REQ-8 (false positive grep):**
- `test_no_tesseract_references` runs `git grep -l extract_with_tesseract` and finds the
  string in old test files (e.g. `test_text_extractor_orchestrator.py` contains it in
  comments). The exclusion pattern `:(exclude)**.md` does not exclude test files, so the
  assertion fails.


---

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- All tests that currently pass must continue to pass after every fix is applied.
- No production module logic is altered except for the single additive re-export in
  `pdf_extractor/extraction/__init__.py` (REQ-2). The re-export does not change the
  behaviour of `extract_pdf` or any other function.
- The `extract_with_pymupdf` function in `pdf_extractor/extraction/PyMuPDF.py` is not
  modified; only its name is made accessible at the package level.
- The `TextProcessor` class and all `SentenceSegment` backends are not modified; only
  test fixtures are added to prevent the eager `spacy.load` call from running in CI.
- The `extract_with_paddleocr` function signature and logic are not modified; only the
  test helper `_run_paddleocr_with_mock` is updated to inject `pdf2image` into
  `sys.modules` before the function runs.

**Scope:**
All inputs that do NOT involve the eight broken test files or the missing re-export are
completely unaffected. This includes:
- All passing tests in `tests/pdf_extractor/`, `tests/quality_control/`,
  `tests/pipeline/`, and `tests/utils/`.
- All production modules under `pdf_extractor/`, `quality_control/`, `pipeline/`,
  `agents/`, and `utils/` (except the one-line addition to
  `pdf_extractor/extraction/__init__.py`).
- The pytest configuration in `pyproject.toml` and both `conftest.py` files.


---

## Hypothesized Root Cause

Each of the eight root causes is independent. They are listed in fix order.

1. **Stale import paths after architecture migration (REQ-1)**: During the migration from
   `pdf_extractor.utils.*` to `utils.*` and from `quality_control.*` to
   `pdf_extractor.*`, four test files were not updated. One test file references a function
   (`build_metrics_hierarchy`) that was never implemented and is not planned.

2. **Missing re-export in extraction `__init__` (REQ-2)**: `pdf_extractor/extraction/__init__.py`
   imports `PyMuPDF` as a module object (`from . import PyMuPDF`) but does not re-export
   `extract_with_pymupdf` as a direct attribute. `unittest.mock.patch` resolves the target
   by attribute lookup on the module object, so `patch("pdf_extractor.extraction.extract_with_pymupdf")`
   fails with `AttributeError` unless the name exists directly on the package.

3. **Test file not updated after waterfall-to-scan-detector migration (REQ-3)**:
   `test_text_extractor_orchestrator.py` was written for the old three-tier waterfall cascade
   that used `_compute_quality_score`. The production code was rewritten to use scan-detector
   routing, but the test file was not updated. The tests patch symbols that no longer exist
   at the patched paths.

4. **Missing scispaCy/spaCy mock in five test files (REQ-4)**: `TextProcessor.__init__` with
   default config instantiates `ScispaCySentenceSegment`, which calls `spacy.load("en_core_sci_sm")`
   eagerly. Five test files construct `TextProcessor` (directly or via `run_quality_control`)
   without first patching `sys.modules["scispacy"]` and `sys.modules["spacy"]`. The existing
   `_mock_text_processor_tokenize` fixture in `test_quality_control_pipeline.py` patches
   `tokenize_sentences` but does not prevent the `__init__` from calling `spacy.load`.

5. **pdf2image patched too late in PaddleOCR coordinate tests (REQ-5)**: The test helper
   `_run_paddleocr_with_mock` patches `pdf2image.pdfinfo_from_path` and
   `pdf2image.convert_from_path` as attribute patches on the `pdf2image` module. However,
   `PaddleOCR.py` calls `_ensure_pdf2image()` which does `import pdf2image` before the
   attribute patches are active. Since `pdf2image` is not installed, the import fails.
   The fix is to inject a `MagicMock` for `pdf2image` into `sys.modules` via `patch.dict`
   before the function runs, so `_ensure_pdf2image()` finds the module already present.

6. **MockFaiss class never defined (REQ-6)**: `TestEmbedQueryShape` references `MockFaiss()`
   in two tests, but the class was never added to the file. This is a straightforward
   omission — the class needs to be defined with a no-op `normalize_L2` method.

7. **Mock return value uses MagicMock instead of real SemanticLayer (REQ-7a)**: The test
   `test_reconciler_call_is_strategy_driven_and_extractor_agnostic` mocks
   `reconciler.reconcile` to return a `UnifiedRecord` with `semantic=MagicMock(sentences=[])`.
   The `quality_control.quality_control` module then iterates `updated_unified.semantic.paragraphs`
   to build sentence records. `MagicMock().paragraphs` is a `MagicMock` object, not a list,
   so iteration fails. The fix is to use a real `SemanticLayer(paragraphs=[], sentences=[])`.

8. **git grep exclusion pattern does not cover test files (REQ-8)**: The `git grep` call in
   `test_no_tesseract_references` excludes `.kiro/` and `**.md` but not test files. Old test
   files (including `test_text_extractor_orchestrator.py`) contain the string
   `extract_with_tesseract` in comments or strings, causing the grep to return non-empty
   output and the assertion to fail.


---

## Correctness Properties

Property 1: Bug Condition — Clean Collection and Zero Failures

_For any_ invocation of `python -m pytest` on the EviTrace test suite after all eight fixes
are applied, the fixed test suite SHALL produce zero collection errors and zero test failures
outside the four intentionally-failing steering-drift tests listed in the Out of Scope section
of the requirements.

**Validates: Requirements REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8**

Property 2: Preservation — No Regression in Currently-Passing Tests

_For any_ test that passes on the unfixed codebase (i.e., `isBugCondition` returns false for
that test), the fixed codebase SHALL produce the same passing result, preserving all existing
correct behaviour in both test and production code.

**Validates: Requirements REQ-1 (acceptance criteria), REQ-2 (acceptance criteria),
REQ-3 (acceptance criteria), REQ-4 (acceptance criteria), REQ-5 (acceptance criteria),
REQ-6 (acceptance criteria), REQ-7 (acceptance criteria), REQ-8 (acceptance criteria)**


---

## Fix Implementation

Each sub-section below specifies exactly which file to change, what to change, and the
complete replacement content where relevant. Changes are listed in dependency order.

---

### Fix 1 — REQ-1: Stale import paths in four test files

#### 1a. Delete `tests/pdf_extractor/test_metrics_hierarchy.py`

**Action:** Delete the file entirely.

**Reason:** `build_metrics_hierarchy` does not exist in
`pdf_extractor.processing.sentence_processor` and is not planned. There is no correct import
path to fix to.

---

#### 1b. Fix `tests/pdf_extractor/test_logging_utils.py`

**File:** `tests/pdf_extractor/test_logging_utils.py`

**Changes:**

1. Replace the import line:
   ```python
   # BEFORE
   from pdf_extractor.utils.logging_utils import setup_logger, get_logger

   # AFTER
   from utils.logging_utils import get_logger, setup_logging
   ```

2. Rewrite the test body to match the actual API of `utils.logging_utils`:
   - `get_logger(name)` returns a `logging.Logger` — keep `test_get_logger_returns_same_instance`.
   - `setup_logging(...)` returns a `logging.Logger` — replace `setup_logger` calls with
     `setup_logging`.
   - `setup_logging` does not have a `propagate` parameter and does not disable propagation
     on the returned logger — remove `test_logger_propagation_disabled` or rewrite it to
     test something `setup_logging` actually does (e.g., that it returns a `logging.Logger`).
   - `setup_logging` accepts `console_level` as a string, not a `level` integer keyword —
     rewrite `test_logger_level_settable` accordingly.

   **Complete replacement content for the test file:**

   ```python
   """
   tests/pdf_extractor/test_logging_utils.py
   ==========================================
   Tests for :mod:`utils.logging_utils`.
   """

   import logging
   import pytest
   from utils.logging_utils import get_logger, setup_logging


   def test_get_logger_returns_logger_object():
       logger = get_logger("test_logger")
       assert isinstance(logger, logging.Logger)


   def test_get_logger_returns_same_instance():
       l1 = get_logger("same")
       l2 = get_logger("same")
       assert l1 is l2


   def test_setup_logging_returns_logger(tmp_path):
       log_file = str(tmp_path / "test.log")
       logger = setup_logging(log_file=log_file, console_level="WARNING")
       assert isinstance(logger, logging.Logger)


   def test_setup_logging_accepts_debug_level(tmp_path):
       log_file = str(tmp_path / "debug.log")
       logger = setup_logging(log_file=log_file, console_level="DEBUG")
       assert isinstance(logger, logging.Logger)
   ```

---

#### 1c. Fix `tests/pdf_extractor/test_parser_pipeline.py`

**File:** `tests/pdf_extractor/test_parser_pipeline.py`

**Changes:**

Replace the entire file content. The old test imported `parse_document` (which does not
exist) and passed a dict to it. The correct function is `extract_with_pdfplumber`, which
takes a `pdf_path: str` and returns `list[BlockDict]`. Since calling the real function
requires a real PDF file, the test must mock the underlying `pdfplumber` library.

**Complete replacement content:**

```python
"""
tests/pdf_extractor/test_parser_pipeline.py
============================================
Smoke tests for the pdfplumber extraction backend.
"""

import pytest
from unittest.mock import MagicMock, patch


def test_extract_with_pdfplumber_returns_list(tmp_path):
    """extract_with_pdfplumber returns a list of BlockDict-shaped dicts."""
    # Write a minimal stub PDF so pdfplumber.open does not raise on path validation.
    # We mock pdfplumber itself so no real PDF parsing occurs.
    mock_page = MagicMock()
    mock_page.find_tables.return_value = []
    mock_page.extract_text.return_value = "Hello world"

    mock_pdf = MagicMock()
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdf.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf):
        from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber
        blocks = extract_with_pdfplumber("fake.pdf")

    assert isinstance(blocks, list)
    assert len(blocks) >= 1
    assert "text" in blocks[0]
    assert "page_index" in blocks[0]


def test_extract_with_pdfplumber_block_has_required_keys(tmp_path):
    """Each block returned by extract_with_pdfplumber has the required schema keys."""
    mock_page = MagicMock()
    mock_page.find_tables.return_value = []
    mock_page.extract_text.return_value = "Sample text content"

    mock_pdf = MagicMock()
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdf.pages = [mock_page]

    with patch("pdfplumber.open", return_value=mock_pdf):
        from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber
        blocks = extract_with_pdfplumber("fake.pdf")

    for block in blocks:
        assert "text" in block
        assert "page_index" in block
        assert "block_bbox" in block
        assert "spans" in block
```

---

#### 1d. Fix `tests/pdf_extractor/test_quality_control_artifact_generator.py`

**File:** `tests/pdf_extractor/test_quality_control_artifact_generator.py`

**Change:** Update the import path only. The test body is correct.

```python
# BEFORE
from quality_control.artifact_generator import (
    build_canonical_artifacts,
    canonicalize_grobid_xml,
    canonicalize_pymupdf_json,
    export_canonical_artifacts,
)

# AFTER
from pdf_extractor.artifact_generator import (
    build_canonical_artifacts,
    canonicalize_grobid_xml,
    canonicalize_pymupdf_json,
    export_canonical_artifacts,
)
```

No other changes to this file.

---

### Fix 2 — REQ-2: Re-export `extract_with_pymupdf` from `pdf_extractor.extraction`

**File:** `pdf_extractor/extraction/__init__.py`

**Change:** Add one import line after the existing `from . import PyMuPDF` line.

```python
# BEFORE (relevant section)
from . import schemas
from . import PyMuPDF
from .pdfplumber import extract_with_pdfplumber
from .PaddleOCR import extract_with_paddleocr
from . import scan_detector

# AFTER
from . import schemas
from . import PyMuPDF
from .PyMuPDF import extract_with_pymupdf          # <-- ADD THIS LINE
from .pdfplumber import extract_with_pdfplumber
from .PaddleOCR import extract_with_paddleocr
from . import scan_detector
```

**Why this works:** `unittest.mock.patch("pdf_extractor.extraction.extract_with_pymupdf")`
resolves the target by doing `getattr(pdf_extractor.extraction, "extract_with_pymupdf")`.
After this change, `extract_with_pymupdf` is a direct attribute of the package module, so
the lookup succeeds.

**No other changes to this file.** The `extract_pdf` function body is not modified; it
continues to call `PyMuPDF.get_page_font_metadata(page)` via the module reference.

---

### Fix 3 — REQ-3: Rewrite `test_text_extractor_orchestrator.py`

**File:** `tests/pdf_extractor/test_text_extractor_orchestrator.py`

**Action:** Replace the entire file content with tests that match the current scan-detector
routing architecture.

**What the new tests must cover (from REQ-3 acceptance criteria):**

| Scenario | Expected behaviour |
|---|---|
| `ocr=False` | pdfplumber only; no scan detection; empty font metadata |
| `ocr=True`, all native | pdfplumber + font metadata; no PaddleOCR |
| `ocr=True`, any scanned | PaddleOCR; no pdfplumber; empty font metadata |
| Always | `validate_blocks` called exactly once before returning |

**Patch targets (all must use these exact paths):**

- `pdf_extractor.extraction.extract_with_pdfplumber`
- `pdf_extractor.extraction.extract_with_paddleocr`
- `pdf_extractor.extraction.scan_detector.classify_page`
- `pdf_extractor.extraction.schemas.validate_blocks`
- `pdf_extractor.extraction.PyMuPDF.get_page_font_metadata`

**Constraints:**

- No reference to `_compute_quality_score` anywhere in the file.
- No reference to `extract_with_pymupdf` as a scored cascade tier.
- `fitz` must be mocked via `patch.dict(sys.modules, {"fitz": mock_fitz})` for tests that
  exercise the `ocr=True` path (same pattern as `test_scan_detector_routing.py`).
- The file must import `pdf_extractor.extraction` (not individual functions) so that
  `patch("pdf_extractor.extraction.extract_with_pdfplumber", ...)` works correctly.

**Complete replacement content:**

```python
"""
tests/pdf_extractor/test_text_extractor_orchestrator.py
---------------------------------------------------------
Tests for the extract_pdf orchestrator in pdf_extractor/extraction/__init__.py.

Covers the current scan-detector routing architecture:
  - ocr=False  → pdfplumber only, no scan detection, empty font metadata
  - ocr=True, all native  → pdfplumber + font metadata, no PaddleOCR
  - ocr=True, any scanned → PaddleOCR, no pdfplumber, empty font metadata
  - validate_blocks is always called exactly once before returning
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import pdf_extractor.extraction
from pdf_extractor.extraction.scan_detector import PageScanClassification


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_block(label: str, page_index: int = 0) -> dict:
    return {"text": f"{label} text", "page_index": page_index, "block_bbox": None, "spans": []}


def _make_fitz_doc(pages: list):
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter(pages))
    mock_doc.close = MagicMock()
    mock_fitz = MagicMock()
    mock_fitz.open = MagicMock(return_value=mock_doc)
    return mock_fitz, mock_doc


def _native(page_index: int = 0) -> PageScanClassification:
    return PageScanClassification(
        page_index=page_index, is_native=True, triggered_stages=[],
        stage_values={"word_count": 100.0, "alpha_ratio": 0.95, "font_count": 3.0, "image_coverage": 0.01},
    )


def _scanned(page_index: int = 0) -> PageScanClassification:
    return PageScanClassification(
        page_index=page_index, is_native=False, triggered_stages=[1], stage_values={},
    )


_PLUMBER_BLOCKS = [_make_block("plumber")]
_PADDLE_BLOCKS = [_make_block("paddle")]
_FONT_META = [{"size": 12.0, "text": "hello", "page": 0}]
_CONFIG = {"quality_control": {"ocr": {"rasterization_dpi": 150}}}


# ---------------------------------------------------------------------------
# ocr=False path
# ---------------------------------------------------------------------------

class TestOcrFalse:
    def test_returns_pdfplumber_blocks(self):
        with patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert blocks == _PLUMBER_BLOCKS

    def test_returns_empty_font_metadata(self):
        with patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS):
            _, font_meta = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert font_meta == []

    def test_scan_detection_not_called(self):
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.scan_detector.classify_page") as mock_cls,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        mock_cls.assert_not_called()

    def test_validate_blocks_called_once(self):
        with (
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_val,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=False, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        mock_val.assert_called_once_with(_PLUMBER_BLOCKS)


# ---------------------------------------------------------------------------
# ocr=True, all native pages
# ---------------------------------------------------------------------------

class TestOcrTrueAllNative:
    def test_returns_pdfplumber_blocks(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_native()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr") as mock_paddle,
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=_FONT_META),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            blocks, _ = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert blocks == _PLUMBER_BLOCKS
        mock_paddle.assert_not_called()

    def test_returns_font_metadata(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_native()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr"),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=_FONT_META),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            _, font_meta = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert font_meta == _FONT_META

    def test_validate_blocks_called_once(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_native()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber", return_value=_PLUMBER_BLOCKS),
            patch("pdf_extractor.extraction.extract_with_paddleocr"),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata", return_value=[]),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_val,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert mock_val.call_count == 1


# ---------------------------------------------------------------------------
# ocr=True, scanned pages
# ---------------------------------------------------------------------------

class TestOcrTrueScanned:
    def test_returns_paddle_blocks(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_scanned()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber") as mock_plumber,
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata"),
            patch("pdf_extractor.extraction.schemas.validate_blocks"),
        ):
            blocks, font_meta = pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert blocks == _PADDLE_BLOCKS
        assert font_meta == []
        mock_plumber.assert_not_called()

    def test_validate_blocks_called_once(self):
        mock_page = MagicMock()
        mock_fitz, _ = _make_fitz_doc([mock_page])
        with (
            patch.dict(sys.modules, {"fitz": mock_fitz}),
            patch("pdf_extractor.extraction.scan_detector.classify_page", return_value=_scanned()),
            patch("pdf_extractor.extraction.extract_with_pdfplumber"),
            patch("pdf_extractor.extraction.extract_with_paddleocr", return_value=_PADDLE_BLOCKS),
            patch("pdf_extractor.extraction.PyMuPDF.get_page_font_metadata"),
            patch("pdf_extractor.extraction.schemas.validate_blocks") as mock_val,
        ):
            pdf_extractor.extraction.extract_pdf(
                "fake.pdf", ocr=True, ocr_text_quality_threshold=0.5, config=_CONFIG
            )
        assert mock_val.call_count == 1


# ---------------------------------------------------------------------------
# Verify _compute_quality_score is absent
# ---------------------------------------------------------------------------

def test_compute_quality_score_not_present():
    """_compute_quality_score must not exist — waterfall cascade was removed."""
    assert not hasattr(pdf_extractor.extraction, "_compute_quality_score")
```

---

### Fix 4 — REQ-4: Add scispaCy/spaCy autouse mocks to five test files

The same autouse fixture pattern must be added to each of the five affected test files.
The fixture patches `sys.modules["scispacy"]` and `sys.modules["spacy"]` before any
`TextProcessor` is constructed, and evicts any cached `text_processor` or `ScispaCy`
modules so the patch takes effect even if the module was previously imported.

**Fixture to add (identical in all five files):**

```python
import sys
from unittest.mock import MagicMock
import pytest

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
```

**Files that need this fixture added:**

#### 4a. `tests/pdf_extractor/test_scan_detector_routing.py`

Add the `_mock_scispacy` fixture at module level (after the existing imports, before the
first helper function). The fixture must appear before any class or function that constructs
a `TextProcessor`.

**Specific location:** Insert after the `import pdf_extractor.extraction` line and before
the `_make_fitz_doc_with_pages` helper function.

#### 4b. `tests/pdf_extractor/test_quality_control_pipeline.py`

The file already has an `autouse=True` fixture `_mock_text_processor_tokenize` that patches
`tokenize_sentences`. This fixture must be **extended** (not replaced) to also patch
`sys.modules["scispacy"]` and `sys.modules["spacy"]` before the `TextProcessor` constructor
runs.

**Specific change:** Replace the existing `_mock_text_processor_tokenize` fixture body with:

```python
@pytest.fixture(autouse=True)
def _mock_text_processor_tokenize(monkeypatch):
    """Prevent spacy.load and ensure deterministic tokenization in tests."""
    # Block the eager spacy.load call in TextProcessor.__init__
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)

    # Also patch tokenize_sentences for deterministic output
    def _fake_tokenize(self, text):
        if not text:
            return []
        return ["sentence one", "sentence two"]

    monkeypatch.setattr(
        "utils.text_processor.TextProcessor.tokenize_sentences",
        _fake_tokenize,
    )
```

Also add `import sys` to the imports at the top of the file (it is not currently imported).

#### 4c. `tests/pdf_extractor/test_quality_control_reconciler.py`

Add the `_mock_scispacy` fixture at module level. The `TestConcernRouting` class constructs
`TextProcessor` via `self._make_text_processor()` which returns a `MagicMock`, so the
`TextProcessor` constructor is not called in that class. However, the fixture is needed
because `reconcile()` may call `TextProcessor()` internally when no `text_processor` kwarg
is passed.

**Specific location:** Add after the existing imports and before the `REQUIRED_PROVENANCE_KEYS`
constant. Also add `import sys` to the imports.

#### 4d. `tests/pdf_extractor/test_sentence_processor_task61.py`

Add the `_mock_scispacy` fixture at module level. The
`TestPdfExtractorPassesTextProcessor.test_process_sentences_called_with_text_processor_with_pdf`
test calls `pe_module.run_pipeline(...)` which constructs a `TextProcessor`.

**Specific location:** Add after the existing imports and before the `_fresh_sentence_processor`
helper. Also add `import sys` to the imports.

#### 4e. `tests/pdf_extractor/test_domain_agnosticism.py`

Add the `_mock_scispacy` fixture at module level. The
`test_text_fidelity_concern_asymmetric_preferred_reading` and
`test_scan_detector_returns_page_scan_classification` tests construct `TextProcessor()`.

**Specific location:** Add after the existing imports and before the first class definition
(`TestRunPipelineDomainIsolation`). Also add `import sys` to the imports (it is not currently
imported at the top level — it is imported inline inside `test_no_regex_sentence_splitter_references`).

---

### Fix 5 — REQ-5: Fix `pdf2image` mock setup in PaddleOCR coordinate tests

**File:** `tests/pdf_extractor/test_scan_detector_routing.py`

**Affected method:** `TestExtractWithPaddleOCRCoordinateConversion._run_paddleocr_with_mock`

**Problem:** The method patches `pdf2image.pdfinfo_from_path` and
`pdf2image.convert_from_path` as attribute patches. But `PaddleOCR.py` calls
`_ensure_pdf2image()` which does `import pdf2image` before those attribute patches are
active. Since `pdf2image` is not installed, the import fails.

**Fix:** Replace the two individual `patch(...)` calls with a single `patch.dict(sys.modules, ...)`
entry that injects a pre-configured `MagicMock` for `pdf2image` before the function runs.

**Current `_run_paddleocr_with_mock` method:**

```python
def _run_paddleocr_with_mock(self, ocr_result, dpi=150):
    import numpy as np
    from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr

    mock_engine = MagicMock()
    mock_engine.ocr.return_value = ocr_result

    mock_paddle_module = MagicMock()
    mock_paddle_module.PaddleOCR = MagicMock(return_value=mock_engine)

    pil_img = MagicMock()
    pil_img.close = MagicMock()

    dummy_array = np.zeros((10, 10, 3), dtype=np.uint8)

    with (
        patch.dict(sys.modules, {"paddleocr": mock_paddle_module, "paddlepaddle": MagicMock()}),
        patch("pdf2image.pdfinfo_from_path", return_value={"Pages": 1}),
        patch("pdf2image.convert_from_path", return_value=[pil_img]),
        patch("numpy.array", return_value=dummy_array),
    ):
        blocks = extract_with_paddleocr("fake.pdf", dpi=dpi)

    return blocks
```

**Replacement `_run_paddleocr_with_mock` method:**

```python
def _run_paddleocr_with_mock(self, ocr_result, dpi=150):
    import numpy as np
    from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr

    mock_engine = MagicMock()
    mock_engine.ocr.return_value = ocr_result

    mock_paddle_module = MagicMock()
    mock_paddle_module.PaddleOCR = MagicMock(return_value=mock_engine)

    pil_img = MagicMock()
    pil_img.close = MagicMock()

    dummy_array = np.zeros((10, 10, 3), dtype=np.uint8)

    # Inject pdf2image into sys.modules so _ensure_pdf2image() finds it
    # without attempting a real import (pdf2image is not installed in CI).
    mock_pdf2image = MagicMock()
    mock_pdf2image.pdfinfo_from_path.return_value = {"Pages": 1}
    mock_pdf2image.convert_from_path.return_value = [pil_img]

    with (
        patch.dict(sys.modules, {
            "paddleocr": mock_paddle_module,
            "paddlepaddle": MagicMock(),
            "pdf2image": mock_pdf2image,
        }),
        patch("numpy.array", return_value=dummy_array),
    ):
        blocks = extract_with_paddleocr("fake.pdf", dpi=dpi)

    return blocks
```

**Key differences:**
- `mock_pdf2image` is a `MagicMock` with `pdfinfo_from_path` and `convert_from_path`
  pre-configured as attributes.
- `"pdf2image": mock_pdf2image` is added to the `patch.dict(sys.modules, ...)` call so
  `_ensure_pdf2image()` finds the module already present and skips the `pip install`.
- The two individual `patch("pdf2image.pdfinfo_from_path", ...)` and
  `patch("pdf2image.convert_from_path", ...)` calls are removed.

The `test_make_ocr_block_factory_used` test method has its own inline mock setup that also
patches `pdf2image` attributes individually. Apply the same fix there:

**In `test_make_ocr_block_factory_used`**, replace:
```python
patch("pdf2image.pdfinfo_from_path", return_value={"Pages": 1}),
patch("pdf2image.convert_from_path", return_value=[pil_img]),
```
with:
```python
# (build mock_pdf2image before the with block)
mock_pdf2image = MagicMock()
mock_pdf2image.pdfinfo_from_path.return_value = {"Pages": 1}
mock_pdf2image.convert_from_path.return_value = [pil_img]
```
and add `"pdf2image": mock_pdf2image` to the `patch.dict(sys.modules, ...)` call.

---

### Fix 6 — REQ-6: Add `MockFaiss` to `test_embedding_utils.py`

**File:** `tests/pdf_extractor/test_embedding_utils.py`

**Change:** Add the `MockFaiss` class definition after the `MockModel` class and before
`TestImportSafety`.

**Exact insertion point:** After the closing line of `MockModel` (the `return` statement
of `encode`) and before the `# Test 1: import succeeds...` comment block.

**Content to insert:**

```python
class MockFaiss:
    """Minimal faiss stand-in for shape tests.

    normalize_L2 is a no-op because MockModel.encode returns all-ones vectors,
    which are close enough to unit-norm for shape-only assertions.
    """

    def normalize_L2(self, vectors) -> None:
        pass  # no-op; shape tests do not require actual normalization
```

No other changes to this file.

---

### Fix 7 — REQ-7: Fix logic failures in `test_quality_control_pipeline.py`

**File:** `tests/pdf_extractor/test_quality_control_pipeline.py`

#### 7a. Fix `test_reconciler_call_is_strategy_driven_and_extractor_agnostic`

**Problem:** The mock return value uses `semantic=MagicMock(sentences=[])`. The reconciler
closure in `quality_control.quality_control` iterates `updated_unified.semantic.paragraphs`
to build sentence records. `MagicMock().paragraphs` is a `MagicMock`, not a list, so
iteration fails.

**Fix:** Replace the `mock_reconcile.return_value` assignment with a real `UnifiedRecord`
that uses a proper `SemanticLayer`.

**Add to imports** (at the top of the file, alongside the existing `quality_control` imports):

```python
from quality_control.models import SemanticLayer, StructuralLayer
```

**Replace the mock return value** inside `test_reconciler_call_is_strategy_driven_and_extractor_agnostic`:

```python
# BEFORE
mock_reconcile.return_value = UnifiedRecord(
    document_id="test-doc-id",
    content={},
    semantic=MagicMock(sentences=[]),
    structural=MagicMock(),
    alignment=AlignmentMap(paragraph_to_blocks=[{"ok": True}]),
)

# AFTER
mock_reconcile.return_value = UnifiedRecord(
    document_id="test-doc-id",
    content={},
    semantic=SemanticLayer(paragraphs=[], sentences=[]),
    structural=StructuralLayer(),
    alignment=AlignmentMap(paragraph_to_blocks=[{"ok": True}]),
)
```

#### 7b. `test_full_pipeline_integration` — no additional change needed

This test fails because `run_quality_control` constructs `TextProcessor()` which triggers
`spacy.load`. Once Fix 4b (the extended `_mock_text_processor_tokenize` fixture) is applied,
the scispaCy mock will be in place before `TextProcessor.__init__` runs, and this test will
pass without any further modification.

---

### Fix 8 — REQ-8: Fix `git grep` exclusion pattern in `test_domain_agnosticism.py`

**File:** `tests/pdf_extractor/test_domain_agnosticism.py`

**Affected test:** `TestAcceptanceCriteriaVerification.test_no_tesseract_references`

**Change:** Add `":(exclude)**/test_*.py"` to the exclusion list in the first `git grep`
call (the one searching for `extract_with_tesseract`).

**Current code:**

```python
result = subprocess.run(
    [
        "git",
        "grep",
        "-l",
        "extract_with_tesseract",
        "--",
        ":(exclude).kiro/",
        ":(exclude)**.md",
    ],
    cwd=repo_root,
    capture_output=True,
    text=True,
)
```

**Replacement:**

```python
result = subprocess.run(
    [
        "git",
        "grep",
        "-l",
        "extract_with_tesseract",
        "--",
        ":(exclude).kiro/",
        ":(exclude)**.md",
        ":(exclude)**/test_*.py",
    ],
    cwd=repo_root,
    capture_output=True,
    text=True,
)
```

No change is needed to the second `git grep` call (the one searching for `pytesseract`),
because `pytesseract` does not appear in any test file.


---

## Testing Strategy

### Validation Approach

The testing strategy follows the bug condition methodology: first confirm the bug is
reproducible on the unfixed codebase (exploratory checking), then verify each fix resolves
its target failures (fix checking), then verify no previously-passing tests regress
(preservation checking).

Because all eight fixes are in test code (plus one additive production change), the
"test" for each fix is simply running the affected test file and observing the result.

---

### Exploratory Bug Condition Checking

**Goal:** Confirm the 4 collection errors and 51 failures are reproducible before applying
any fix. Establish a baseline.

**Test Plan:** Run `python -m pytest --collect-only -q` and observe collection errors.
Then run `python -m pytest -q` and record the failure count and failure names.

**Expected counterexamples (before any fix):**

Collection errors (4):
- `tests/pdf_extractor/test_logging_utils.py` — `ImportError: cannot import name 'setup_logger'`
- `tests/pdf_extractor/test_parser_pipeline.py` — `ImportError: cannot import name 'parse_document'`
- `tests/pdf_extractor/test_quality_control_artifact_generator.py` — `ModuleNotFoundError: quality_control.artifact_generator`
- `tests/pdf_extractor/test_metrics_hierarchy.py` — `ImportError: cannot import name 'build_metrics_hierarchy'`

Test failures (51) grouped by root cause:
- REQ-2: 18 failures in `test_scan_detector_routing.py` and steering-drift files
  (`AttributeError: extract_with_pymupdf`)
- REQ-3: 10 failures in `test_text_extractor_orchestrator.py`
  (patches `_compute_quality_score` which does not exist)
- REQ-4: 14 failures across 5 files (`OSError: Can't find model 'en_core_sci_sm'`)
- REQ-5: 7 failures in `TestExtractWithPaddleOCRCoordinateConversion`
  (`ModuleNotFoundError: pdf2image`)
- REQ-6: 2 failures in `TestEmbedQueryShape` (`NameError: MockFaiss`)
- REQ-7: 2 failures in `test_quality_control_pipeline.py` (logic errors)
- REQ-8: 1 failure in `test_domain_agnosticism.py` (false-positive grep)

---

### Fix Checking

**Goal:** After applying each fix, verify that the target failures are resolved.

**Pseudocode:**

```
FOR ALL fix IN [REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8] DO
  apply_fix(fix)
  result := run_pytest(affected_files(fix))
  ASSERT collection_errors(result) == 0
  ASSERT failing_tests(result) INTERSECT target_tests(fix) == empty_set
END FOR
```

**Per-fix verification commands:**

| Fix | Verification command |
|---|---|
| REQ-1 | `python -m pytest --collect-only tests/pdf_extractor/test_logging_utils.py tests/pdf_extractor/test_parser_pipeline.py tests/pdf_extractor/test_quality_control_artifact_generator.py -q` |
| REQ-2 | `python -m pytest tests/pdf_extractor/test_scan_detector_routing.py -q` |
| REQ-3 | `python -m pytest tests/pdf_extractor/test_text_extractor_orchestrator.py -q` |
| REQ-4 | `python -m pytest tests/pdf_extractor/test_scan_detector_routing.py tests/pdf_extractor/test_quality_control_pipeline.py tests/pdf_extractor/test_quality_control_reconciler.py tests/pdf_extractor/test_sentence_processor_task61.py tests/pdf_extractor/test_domain_agnosticism.py -q` |
| REQ-5 | `python -m pytest tests/pdf_extractor/test_scan_detector_routing.py::TestExtractWithPaddleOCRCoordinateConversion -q` |
| REQ-6 | `python -m pytest tests/pdf_extractor/test_embedding_utils.py::TestEmbedQueryShape -q` |
| REQ-7 | `python -m pytest tests/pdf_extractor/test_quality_control_pipeline.py::TestPipelineOrchestration::test_reconciler_call_is_strategy_driven_and_extractor_agnostic tests/pdf_extractor/test_quality_control_pipeline.py::test_full_pipeline_integration -q` |
| REQ-8 | `python -m pytest tests/pdf_extractor/test_domain_agnosticism.py::TestAcceptanceCriteriaVerification::test_no_tesseract_references -q` |

---

### Preservation Checking

**Goal:** After all fixes are applied, verify that no previously-passing test now fails.

**Pseudocode:**

```
FOR ALL test WHERE NOT isBugCondition(test) DO
  ASSERT run_pytest(test) == PASS
END FOR
```

**Test Plan:** Run the full suite after all fixes are applied:

```bash
python -m pytest -q
```

Expected result: zero collection errors, zero failures outside the four intentionally-failing
steering-drift tests.

Property-based testing is not required for preservation checking here because all fixes are
additive (new imports, new fixtures, new class definitions) or targeted replacements in test
code. The risk of regression is low and is fully covered by running the existing suite.

---

### Unit Tests

- Each fixed test file is its own unit test. The acceptance criteria for each REQ are the
  unit tests.
- `test_logging_utils.py` — four tests covering `get_logger` and `setup_logging`.
- `test_parser_pipeline.py` — two tests covering `extract_with_pdfplumber` with mocked
  `pdfplumber`.
- `test_quality_control_artifact_generator.py` — existing tests (unchanged body); import
  fix only.
- `test_text_extractor_orchestrator.py` — nine new tests covering all four routing scenarios
  and the absence of `_compute_quality_score`.

---

### Property-Based Tests

No new property-based tests are introduced by this spec. The existing Hypothesis tests in
`test_quality_control_artifact_generator.py`, `test_quality_control_pipeline.py`,
`test_quality_control_reconciler.py`, and `test_embedding_utils.py` are preserved and must
continue to pass after the fixes.

---

### Integration Tests

- `test_full_pipeline_integration` in `test_quality_control_pipeline.py` is the primary
  integration test. It calls `run_quality_control` end-to-end with real inputs and no mocks
  (except the scispaCy fixture added by REQ-4). It must pass after REQ-4 is applied.
- `test_scan_detector_returns_page_scan_classification` in `test_domain_agnosticism.py`
  calls `classify_page` with a mock `fitz.Page` and a real `TextProcessor`. It must pass
  after REQ-4 is applied.
