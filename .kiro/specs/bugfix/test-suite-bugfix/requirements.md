# Test Suite Bug Fix — Requirements

## Overview

The EviTrace test suite has **4 collection errors** (tests that cannot even be
imported) and **51 test failures** across the collectible suite.  This spec
tracks the fixes required to bring the suite to a clean state.

Failures fall into eight distinct root-cause categories.  Each requirement
below maps to one category and is scoped to the minimum change needed to fix
it without altering production behaviour.

---

## Requirements

### REQ-1 — Fix stale import paths in four broken test files

**Priority:** Critical (blocks collection of ~30 tests)

The following test files import symbols from paths that no longer exist or
were renamed during the architecture migration.  They must be updated to
import from the correct current locations.

| File | Broken import | Correct import |
|---|---|---|
| `tests/pdf_extractor/test_logging_utils.py` | `pdf_extractor.utils.logging_utils` | `utils.logging_utils` |
| `tests/pdf_extractor/test_parser_pipeline.py` | `pdf_extractor.extraction.pdfplumber.parse_document` | `pdf_extractor.extraction.pdfplumber.extract_with_pdfplumber` |
| `tests/pdf_extractor/test_quality_control_artifact_generator.py` | `quality_control.artifact_generator` | `pdf_extractor.artifact_generator` |
| `tests/pdf_extractor/test_metrics_hierarchy.py` | `pdf_extractor.processing.sentence_processor.build_metrics_hierarchy` | Function does not exist |

For `test_logging_utils.py`: update the import and verify the test body still
makes sense against `utils.logging_utils` (which exposes `get_logger` and
`setup_logging`, not `setup_logger`).

For `test_parser_pipeline.py`: update the import and update the test body —
`extract_with_pdfplumber` takes a `pdf_path` argument and returns
`list[BlockDict]`, not a dict.  The test must be rewritten to match the
actual function signature.

For `test_quality_control_artifact_generator.py`: update the import path only;
the test body is correct.

For `test_metrics_hierarchy.py`: `build_metrics_hierarchy` does not exist and
is not planned.  Delete this test file.

**Acceptance criteria:**
- `python -m pytest --collect-only` produces zero collection errors.
- The three updated test files collect and run without `ImportError`.

---

### REQ-2 — Re-export `extract_with_pymupdf` from `pdf_extractor.extraction`

**Priority:** High (18 test failures)

`test_scan_detector_routing.py`, `test_steering_drift_preservation.py`, and
`test_steering_drift_bug_condition.py` all patch
`pdf_extractor.extraction.extract_with_pymupdf`.  The current
`pdf_extractor/extraction/__init__.py` imports the `PyMuPDF` submodule as a
module object but does not re-export `extract_with_pymupdf` as a direct
attribute.  `unittest.mock.patch` requires the symbol to exist at the target
path.

Add the following import to `pdf_extractor/extraction/__init__.py`:

```python
from .PyMuPDF import extract_with_pymupdf
```

**Acceptance criteria:**
- `patch("pdf_extractor.extraction.extract_with_pymupdf", ...)` no longer
  raises `AttributeError`.
- All `TestOcrTrueWithAllNativePages`, `TestOcrTrueWithAllScannedPages`,
  `TestClassifyPageCalledPerPage`, and `TestValidateBlocksAlwaysCalled` tests
  in `test_scan_detector_routing.py` pass.

---

### REQ-3 — Retire stale waterfall-cascade tests

**Priority:** High (10 test failures)

`test_text_extractor_orchestrator.py` tests the old three-tier waterfall
cascade (`_compute_quality_score`, `extract_with_pymupdf` as a scored tier,
`extract_with_pdfplumber` as a scored tier, `extract_with_paddleocr` as the
final tier).  This architecture was replaced by scan-detector routing in
`pdf_extractor/extraction/__init__.py`.  The old cascade no longer exists and
`_compute_quality_score` is not defined.

These tests must be replaced with tests that match the current scan-detector
routing architecture (already covered by `test_scan_detector_routing.py`).
The file `test_text_extractor_orchestrator.py` must be rewritten to test the
current `extract_pdf` behaviour:

- `ocr=False` → pdfplumber only, no scan detection, empty font metadata.
- `ocr=True`, all native → pdfplumber + font metadata, no PaddleOCR.
- `ocr=True`, any scanned → PaddleOCR, no pdfplumber, empty font metadata.
- `validate_blocks` is always called exactly once before returning.

The new tests must patch at the correct module-level paths
(`pdf_extractor.extraction.extract_with_pdfplumber`,
`pdf_extractor.extraction.extract_with_paddleocr`,
`pdf_extractor.extraction.scan_detector.classify_page`) and must not
reference `_compute_quality_score` or `extract_with_pymupdf` as a scored
cascade tier.

**Acceptance criteria:**
- `test_text_extractor_orchestrator.py` collects and all tests pass.
- No test in the file references `_compute_quality_score`.

---

### REQ-4 — Mock scispaCy in all tests that construct `TextProcessor`

**Priority:** High (14 test failures)

`TextProcessor.__init__` with default config eagerly instantiates
`ScispaCySentenceSegment`, which calls `spacy.load("en_core_sci_sm")`.
`scispacy` and `en_core_sci_sm` are not installed in the test environment.

Per the testing conventions, heavy NLP models must be mocked.  The following
test files construct `TextProcessor` (directly or indirectly via
`run_quality_control`) without mocking `scispacy`/`spacy`:

- `tests/pdf_extractor/test_scan_detector_routing.py`
  (`TestOcrTrueWithAllNativePages`, `TestOcrTrueWithAllScannedPages`,
  `TestClassifyPageCalledPerPage`, `TestValidateBlocksAlwaysCalled`)
- `tests/pdf_extractor/test_quality_control_pipeline.py`
  (all tests that call `run_quality_control`)
- `tests/pdf_extractor/test_quality_control_reconciler.py`
  (`TestConcernRouting`)
- `tests/pdf_extractor/test_sentence_processor_task61.py`
  (`TestPdfExtractorPassesTextProcessor`)
- `tests/pdf_extractor/test_domain_agnosticism.py`
  (`test_text_fidelity_concern_asymmetric_preferred_reading`,
  `test_scan_detector_returns_page_scan_classification`)

Each affected test file must add a module-level or `autouse` fixture that
patches `scispacy` and `spacy` in `sys.modules` before any `TextProcessor`
is constructed, following the pattern in the testing conventions:

```python
import sys
from unittest.mock import MagicMock

@pytest.fixture(autouse=True)
def _mock_scispacy(monkeypatch):
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    # Evict any cached TextProcessor/ScispaCy modules so the patch takes effect
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)
```

For `test_quality_control_pipeline.py` specifically: the existing `autouse`
fixture `_mock_text_processor_tokenize` patches `tokenize_sentences` but does
not prevent the eager `spacy.load` call in `__init__`.  The fixture must be
extended to also patch `sys.modules["scispacy"]` and `sys.modules["spacy"]`
before the `TextProcessor` constructor runs.

**Acceptance criteria:**
- All 14 previously failing tests pass without `scispacy` or `en_core_sci_sm`
  being installed.
- No test imports `scispacy` or `spacy` directly.

---

### REQ-5 — Fix `pdf2image` mock setup in PaddleOCR coordinate tests

**Priority:** Medium (7 test failures)

`TestExtractWithPaddleOCRCoordinateConversion` in
`test_scan_detector_routing.py` patches `pdf2image.pdfinfo_from_path` and
`pdf2image.convert_from_path`, but `PaddleOCR.py` calls
`_ensure_pdf2image()` which attempts a real `import pdf2image` before the
patch takes effect.  Since `pdf2image` is not installed, the import fails.

The fix is to add `"pdf2image"` to the `patch.dict(sys.modules, ...)` call
inside `_run_paddleocr_with_mock` so that the lazy-install guard is bypassed:

```python
mock_pdf2image = MagicMock()
mock_pdf2image.pdfinfo_from_path.return_value = {"Pages": 1}
mock_pdf2image.convert_from_path.return_value = [pil_img]

with (
    patch.dict(sys.modules, {
        "paddleocr": mock_paddle_module,
        "paddlepaddle": MagicMock(),
        "pdf2image": mock_pdf2image,
    }),
    ...
):
```

The individual `patch("pdf2image.pdfinfo_from_path", ...)` and
`patch("pdf2image.convert_from_path", ...)` calls must be removed and
replaced by the module-level mock above.

**Acceptance criteria:**
- All 7 `TestExtractWithPaddleOCRCoordinateConversion` tests pass without
  `pdf2image` being installed.

---

### REQ-6 — Add `MockFaiss` to `test_embedding_utils.py`

**Priority:** Medium (2 test failures)

`TestEmbedQueryShape` references `MockFaiss()` but the class is never defined
in the file.  Add a `MockFaiss` class that provides a `normalize_L2` no-op
so that `l2_normalise` can complete without the real `faiss` package:

```python
class MockFaiss:
    """Minimal faiss stand-in: normalize_L2 is a no-op (vectors already unit)."""

    def normalize_L2(self, vectors) -> None:
        pass  # no-op; MockModel.encode returns all-ones, close enough for shape tests
```

**Acceptance criteria:**
- `test_embed_query_returns_correct_shape` and
  `test_embed_query_shape_with_small_dim` pass.

---

### REQ-7 — Fix `test_quality_control_pipeline.py` logic failures

**Priority:** Medium (2 test failures)

Two tests in `test_quality_control_pipeline.py` fail for logic reasons
unrelated to missing dependencies:

**7a — `test_reconciler_call_is_strategy_driven_and_extractor_agnostic`**

The test mocks `reconciler.reconcile` to return a `UnifiedRecord` with
`semantic=MagicMock(sentences=[])`.  The reconciler closure in
`quality_control.quality_control` then iterates
`updated_unified.semantic.paragraphs` to populate sentences — but
`MagicMock().paragraphs` is itself a `MagicMock`, not a list, so iteration
produces unexpected results.

Fix: update the mock return value to use a real `SemanticLayer` with an
empty `paragraphs` list:

```python
from quality_control.models import SemanticLayer, StructuralLayer

mock_reconcile.return_value = UnifiedRecord(
    document_id="test-doc-id",
    content={},
    semantic=SemanticLayer(paragraphs=[], sentences=[]),
    structural=StructuralLayer(),
    alignment=AlignmentMap(paragraph_to_blocks=[{"ok": True}]),
)
```

**7b — `test_full_pipeline_integration`**

The test asserts that `result.unified.content` contains legacy keys
(`"metadata"`, `"pages"`, `"segments"`, `"exact_text"`, `"geometry"`,
`"provenance"`, `"observer_summary"`, `"investigator_summary"`,
`"adjudication_status"`, `"placeholder_notice"`).

The reconciler's placeholder path (triggered when `adjudication_decisions is
None`) does populate all these keys.  However, the `_pdf_reconciler_fn`
closure in `quality_control.quality_control` calls `reconciler.reconcile`
with a non-None `adjudication_decisions` dict, which takes the full
strategy-driven path.  That path also populates all the same keys.

The test fails because `run_quality_control` is called with a minimal config
that has no `quality_control.text_processor` key, causing `_load_text_processor`
to fall back to `None`, and the reconciler then calls `TextProcessor()` which
triggers the scispaCy load.

This failure is therefore a duplicate of REQ-4.  Once REQ-4 is applied
(scispaCy mocked via the `autouse` fixture), this test will pass without
further changes.

**Acceptance criteria:**
- `test_reconciler_call_is_strategy_driven_and_extractor_agnostic` passes
  with the corrected mock.
- `test_full_pipeline_integration` passes once REQ-4 is applied.

---

### REQ-8 — Fix `test_domain_agnosticism.py::test_no_tesseract_references`

**Priority:** Low (1 test failure)

The `git grep` command in `test_no_tesseract_references` finds
`extract_with_tesseract` in the test files themselves (the old test files
reference it in comments or strings).  The grep exclusion pattern
`:(exclude)**/*.md` does not exclude test files.

Fix: add `:(exclude)**/test_*.py` to the exclusion list in the `git grep`
call so that test files are excluded from the search:

```python
result = subprocess.run(
    [
        "git", "grep", "-l", "extract_with_tesseract",
        "--",
        ":(exclude).kiro/",
        ":(exclude)**.md",
        ":(exclude)**/test_*.py",
    ],
    ...
)
```

**Acceptance criteria:**
- `test_no_tesseract_references` passes.
- The grep correctly finds any non-test, non-doc references to
  `extract_with_tesseract` if they exist.

---

## Out of Scope

The following failures are **intentionally failing** and are excluded from
this spec:

- `test_steering_drift_bug_condition.py::test_qc_file_names` — expects a
  `pdf_extractor/extraction/quality_control/` directory that is part of a
  separate in-progress architecture migration spec.
- `test_steering_drift_bug_condition.py::test_run_py_pdf_discovery_call_site`
  — expects a `run.py` file that does not exist; entry point is `main.py`.
- `test_steering_drift_bug_condition.py::test_qc_test_file_names` — expects
  test files at `tests/test_quality_control_*.py` (flat layout); tests live
  in `tests/pdf_extractor/`.
- `test_steering_drift_bug_condition.py::test_cascade_order_pdfplumber_before_paddleocr`
  — this test will pass once REQ-2 is applied (re-export of
  `extract_with_pymupdf`).

These four tests encode the post-fix state of the architecture-migration spec
and will be addressed in that separate spec.
