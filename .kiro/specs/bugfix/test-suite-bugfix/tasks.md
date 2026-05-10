# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Test Suite Collection Errors and Failures
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: For deterministic bugs, scope the property to the concrete failing cases to ensure reproducibility
  - Run `python -m pytest --collect-only -q` and assert zero collection errors
  - Run `python -m pytest -q` and assert zero failures outside the four intentionally-failing steering-drift tests:
    - `test_steering_drift_bug_condition.py::test_qc_file_names`
    - `test_steering_drift_bug_condition.py::test_run_py_pdf_discovery_call_site`
    - `test_steering_drift_bug_condition.py::test_qc_test_file_names`
    - `test_steering_drift_bug_condition.py::test_cascade_order_pdfplumber_before_paddleocr`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug exists)
  - Document counterexamples found:
    - 4 collection errors: `ImportError` in `test_logging_utils.py`, `test_parser_pipeline.py`, `test_quality_control_artifact_generator.py`, `test_metrics_hierarchy.py`
    - 18 failures from `AttributeError: extract_with_pymupdf` (REQ-2)
    - 10 failures from `test_text_extractor_orchestrator.py` patching removed symbols (REQ-3)
    - 14 failures from `OSError: Can't find model 'en_core_sci_sm'` (REQ-4)
    - 7 failures from `ModuleNotFoundError: pdf2image` (REQ-5)
    - 2 failures from `NameError: MockFaiss` (REQ-6)
    - 2 logic failures in `test_quality_control_pipeline.py` (REQ-7)
    - 1 false-positive grep failure in `test_domain_agnosticism.py` (REQ-8)
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8_

- [-] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Currently-Passing Tests Must Not Regress
  - **IMPORTANT**: Follow observation-first methodology
  - Observe: run `python -m pytest -q` on UNFIXED code and record all currently-passing tests
  - Write property-based test: for all tests that currently pass (i.e., `isBugCondition` returns false for that test), the fixed codebase SHALL produce the same passing result
  - Scope: all passing tests in `tests/pdf_extractor/`, `tests/quality_control/`, `tests/pipeline/`, and `tests/utils/`
  - Verify test passes on UNFIXED code (confirms baseline behavior to preserve)
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8_

- [x] 3. Fix REQ-1 — Stale import paths in four broken test files

  - [x] 3.1 Delete `tests/pdf_extractor/test_metrics_hierarchy.py`
    - Delete the file entirely — `build_metrics_hierarchy` does not exist and is not planned
    - _Bug_Condition: isBugCondition where `test_metrics_hierarchy.py` raises `ImportError: cannot import name 'build_metrics_hierarchy'`_
    - _Expected_Behavior: file is absent; `--collect-only` produces no error for it_
    - _Preservation: no other test file imports from `test_metrics_hierarchy.py`_
    - _Requirements: REQ-1_

  - [x] 3.2 Fix `tests/pdf_extractor/test_logging_utils.py`
    - Replace import: `from pdf_extractor.utils.logging_utils import setup_logger, get_logger` → `from utils.logging_utils import get_logger, setup_logging`
    - Rewrite test body to match actual `utils.logging_utils` API:
      - `get_logger(name)` returns a `logging.Logger` — keep `test_get_logger_returns_same_instance`
      - Replace `setup_logger` calls with `setup_logging`
      - Remove `test_logger_propagation_disabled` (propagation is not disabled by `setup_logging`)
      - Rewrite `test_logger_level_settable` to use `console_level` string parameter
    - Replace entire file with the four-test version specified in design Fix 1b
    - _Bug_Condition: isBugCondition where file raises `ImportError: cannot import name 'setup_logger' from 'pdf_extractor.utils.logging_utils'`_
    - _Expected_Behavior: file collects and all four tests pass_
    - _Preservation: `utils.logging_utils` production module is not modified_
    - _Requirements: REQ-1_

  - [x] 3.3 Fix `tests/pdf_extractor/test_parser_pipeline.py`
    - Replace import: `pdf_extractor.extraction.pdfplumber.parse_document` → `pdf_extractor.extraction.pdfplumber.extract_with_pdfplumber`
    - Rewrite test body: `extract_with_pdfplumber` takes `pdf_path: str` and returns `list[BlockDict]`; mock `pdfplumber.open` to avoid real PDF parsing
    - Replace entire file with the two-test version specified in design Fix 1c
    - _Bug_Condition: isBugCondition where file raises `ImportError: cannot import name 'parse_document'`_
    - _Expected_Behavior: file collects and both tests pass_
    - _Preservation: `pdf_extractor.extraction.pdfplumber` production module is not modified_
    - _Requirements: REQ-1_

  - [x] 3.4 Fix `tests/pdf_extractor/test_quality_control_artifact_generator.py`
    - Replace import path only: `from quality_control.artifact_generator import ...` → `from pdf_extractor.artifact_generator import ...`
    - No changes to the test body
    - _Bug_Condition: isBugCondition where file raises `ModuleNotFoundError: No module named 'quality_control.artifact_generator'`_
    - _Expected_Behavior: file collects and all existing tests pass_
    - _Preservation: `pdf_extractor.artifact_generator` production module is not modified_
    - _Requirements: REQ-1_

  - [x] 3.5 Verify REQ-1 collection errors are resolved
    - Run: `python -m pytest --collect-only tests/pdf_extractor/test_logging_utils.py tests/pdf_extractor/test_parser_pipeline.py tests/pdf_extractor/test_quality_control_artifact_generator.py -q`
    - **EXPECTED OUTCOME**: Zero collection errors for all three files

- [x] 4. Fix REQ-2 — Re-export `extract_with_pymupdf` from `pdf_extractor.extraction`

  - [x] 4.1 Add re-export to `pdf_extractor/extraction/__init__.py`
    - Add one import line after `from . import PyMuPDF`:
      ```python
      from .PyMuPDF import extract_with_pymupdf
      ```
    - Do not modify the `extract_pdf` function body or any other line
    - _Bug_Condition: isBugCondition where `patch("pdf_extractor.extraction.extract_with_pymupdf", ...)` raises `AttributeError: <module 'pdf_extractor.extraction'> does not have the attribute 'extract_with_pymupdf'`_
    - _Expected_Behavior: `getattr(pdf_extractor.extraction, "extract_with_pymupdf")` resolves to the function from `PyMuPDF.py`_
    - _Preservation: `extract_pdf` function body is unchanged; the re-export is additive only_
    - _Requirements: REQ-2_

  - [x] 4.2 Verify REQ-2 patch target resolves
    - Run: `python -m pytest tests/pdf_extractor/test_scan_detector_routing.py -q`
    - **EXPECTED OUTCOME**: `AttributeError` on `extract_with_pymupdf` no longer appears

- [x] 5. Fix REQ-3 — Rewrite `test_text_extractor_orchestrator.py`

  - [x] 5.1 Replace `tests/pdf_extractor/test_text_extractor_orchestrator.py` with scan-detector routing tests
    - Replace entire file content with the new test suite specified in design Fix 3
    - New tests must cover all four routing scenarios:
      - `ocr=False` → pdfplumber only; no scan detection; empty font metadata
      - `ocr=True`, all native → pdfplumber + font metadata; no PaddleOCR
      - `ocr=True`, any scanned → PaddleOCR; no pdfplumber; empty font metadata
      - `validate_blocks` always called exactly once
    - Patch targets must use exact paths:
      - `pdf_extractor.extraction.extract_with_pdfplumber`
      - `pdf_extractor.extraction.extract_with_paddleocr`
      - `pdf_extractor.extraction.scan_detector.classify_page`
      - `pdf_extractor.extraction.schemas.validate_blocks`
      - `pdf_extractor.extraction.PyMuPDF.get_page_font_metadata`
    - No reference to `_compute_quality_score` anywhere in the file
    - No reference to `extract_with_pymupdf` as a scored cascade tier
    - Mock `fitz` via `patch.dict(sys.modules, {"fitz": mock_fitz})` for `ocr=True` paths
    - _Bug_Condition: isBugCondition where all 10 tests fail with `AttributeError` patching `_compute_quality_score` which does not exist_
    - _Expected_Behavior: file collects and all new tests pass against the current scan-detector routing architecture_
    - _Preservation: `pdf_extractor/extraction/__init__.py` production logic is not modified_
    - _Requirements: REQ-3_

  - [x] 5.2 Verify REQ-3 tests pass
    - Run: `python -m pytest tests/pdf_extractor/test_text_extractor_orchestrator.py -q`
    - **EXPECTED OUTCOME**: All tests pass; no reference to `_compute_quality_score`

- [x] 6. Fix REQ-4 — Add scispaCy/spaCy autouse mocks to five test files

  - [x] 6.1 Add `_mock_scispacy` autouse fixture to `tests/pdf_extractor/test_scan_detector_routing.py`
    - Add `import sys` to imports if not already present
    - Insert the `_mock_scispacy` autouse fixture (from design Fix 4a) after the `import pdf_extractor.extraction` line and before the `_make_fitz_doc_with_pages` helper
    - Fixture patches `sys.modules["scispacy"]` and `sys.modules["spacy"]` and evicts cached `text_processor`/`ScispaCy` modules
    - _Bug_Condition: isBugCondition where `TestOcrTrueWithAllNativePages`, `TestOcrTrueWithAllScannedPages`, `TestClassifyPageCalledPerPage`, `TestValidateBlocksAlwaysCalled` fail with `OSError: Can't find model 'en_core_sci_sm'`_
    - _Expected_Behavior: all four test classes pass without scispaCy installed_
    - _Preservation: fixture is `autouse=True` and scoped to the module; no production code is modified_
    - _Requirements: REQ-4_

  - [x] 6.2 Extend `_mock_text_processor_tokenize` fixture in `tests/pdf_extractor/test_quality_control_pipeline.py`
    - Add `import sys` to the top-level imports
    - Replace the existing `_mock_text_processor_tokenize` fixture body with the extended version from design Fix 4b
    - The extended fixture must: (1) patch `sys.modules["scispacy"]` and `sys.modules["spacy"]`, (2) evict cached modules, AND (3) still patch `tokenize_sentences` for deterministic output
    - _Bug_Condition: isBugCondition where `test_full_pipeline_integration` and other tests fail with `OSError: Can't find model 'en_core_sci_sm'` because `_mock_text_processor_tokenize` patches `tokenize_sentences` but not the eager `spacy.load` in `__init__`_
    - _Expected_Behavior: all tests in the file pass without scispaCy installed_
    - _Preservation: `tokenize_sentences` patch behavior is preserved; no production code is modified_
    - _Requirements: REQ-4_

  - [x] 6.3 Add `_mock_scispacy` autouse fixture to `tests/pdf_extractor/test_quality_control_reconciler.py`
    - Add `import sys` to imports
    - Insert the `_mock_scispacy` autouse fixture (from design Fix 4c) after the existing imports and before the `REQUIRED_PROVENANCE_KEYS` constant
    - _Bug_Condition: isBugCondition where `TestConcernRouting` tests fail with `OSError: Can't find model 'en_core_sci_sm'`_
    - _Expected_Behavior: all `TestConcernRouting` tests pass without scispaCy installed_
    - _Preservation: no production code is modified_
    - _Requirements: REQ-4_

  - [x] 6.4 Add `_mock_scispacy` autouse fixture to `tests/pdf_extractor/test_sentence_processor_task61.py`
    - Add `import sys` to imports
    - Insert the `_mock_scispacy` autouse fixture (from design Fix 4d) after the existing imports and before the `_fresh_sentence_processor` helper
    - _Bug_Condition: isBugCondition where `TestPdfExtractorPassesTextProcessor` tests fail with `OSError: Can't find model 'en_core_sci_sm'`_
    - _Expected_Behavior: all tests in the file pass without scispaCy installed_
    - _Preservation: no production code is modified_
    - _Requirements: REQ-4_

  - [x] 6.5 Add `_mock_scispacy` autouse fixture to `tests/pdf_extractor/test_domain_agnosticism.py`
    - Add `import sys` to top-level imports (currently imported inline inside one test function)
    - Insert the `_mock_scispacy` autouse fixture (from design Fix 4e) after the existing imports and before the first class definition (`TestRunPipelineDomainIsolation`)
    - _Bug_Condition: isBugCondition where `test_text_fidelity_concern_asymmetric_preferred_reading` and `test_scan_detector_returns_page_scan_classification` fail with `OSError: Can't find model 'en_core_sci_sm'`_
    - _Expected_Behavior: both tests pass without scispaCy installed_
    - _Preservation: no production code is modified_
    - _Requirements: REQ-4_

  - [x] 6.6 Verify REQ-4 scispaCy failures are resolved
    - Run: `python -m pytest tests/pdf_extractor/test_scan_detector_routing.py tests/pdf_extractor/test_quality_control_pipeline.py tests/pdf_extractor/test_quality_control_reconciler.py tests/pdf_extractor/test_sentence_processor_task61.py tests/pdf_extractor/test_domain_agnosticism.py -q`
    - **EXPECTED OUTCOME**: All 14 previously-failing scispaCy tests now pass

- [x] 7. Fix REQ-5 — Fix `pdf2image` mock setup in PaddleOCR coordinate tests

  - [x] 7.1 Update `_run_paddleocr_with_mock` in `tests/pdf_extractor/test_scan_detector_routing.py`
    - Replace the two individual `patch("pdf2image.pdfinfo_from_path", ...)` and `patch("pdf2image.convert_from_path", ...)` calls with a single `mock_pdf2image = MagicMock()` with pre-configured attributes
    - Add `"pdf2image": mock_pdf2image` to the `patch.dict(sys.modules, ...)` call so `_ensure_pdf2image()` finds the module already present
    - Use the exact replacement from design Fix 5
    - _Bug_Condition: isBugCondition where `TestExtractWithPaddleOCRCoordinateConversion` tests fail with `ModuleNotFoundError: No module named 'pdf2image'` because `_ensure_pdf2image()` runs `import pdf2image` before attribute patches are active_
    - _Expected_Behavior: all 7 `TestExtractWithPaddleOCRCoordinateConversion` tests pass without `pdf2image` installed_
    - _Preservation: `extract_with_paddleocr` production function signature and logic are not modified_
    - _Requirements: REQ-5_

  - [x] 7.2 Apply the same `pdf2image` sys.modules fix to `test_make_ocr_block_factory_used`
    - In `test_make_ocr_block_factory_used`, replace the individual `patch("pdf2image.pdfinfo_from_path", ...)` and `patch("pdf2image.convert_from_path", ...)` calls with the `mock_pdf2image` module-level mock pattern
    - Add `"pdf2image": mock_pdf2image` to the `patch.dict(sys.modules, ...)` call in that test
    - _Requirements: REQ-5_

  - [x] 7.3 Verify REQ-5 PaddleOCR coordinate tests pass
    - Run: `python -m pytest tests/pdf_extractor/test_scan_detector_routing.py::TestExtractWithPaddleOCRCoordinateConversion -q`
    - **EXPECTED OUTCOME**: All 7 tests pass without `pdf2image` installed

- [x] 8. Fix REQ-6 — Add `MockFaiss` class to `test_embedding_utils.py`

  - [x] 8.1 Add `MockFaiss` class to `tests/pdf_extractor/test_embedding_utils.py`
    - Insert the `MockFaiss` class definition (from design Fix 6) after the closing line of `MockModel` and before the `# Test 1: import succeeds...` comment block
    - Class must provide a no-op `normalize_L2(self, vectors) -> None` method
    - _Bug_Condition: isBugCondition where `test_embed_query_returns_correct_shape` and `test_embed_query_shape_with_small_dim` fail with `NameError: name 'MockFaiss' is not defined`_
    - _Expected_Behavior: both tests pass_
    - _Preservation: `MockModel` class and all other test helpers are unchanged_
    - _Requirements: REQ-6_

  - [x] 8.2 Verify REQ-6 embedding shape tests pass
    - Run: `python -m pytest tests/pdf_extractor/test_embedding_utils.py::TestEmbedQueryShape -q`
    - **EXPECTED OUTCOME**: Both `test_embed_query_returns_correct_shape` and `test_embed_query_shape_with_small_dim` pass

- [x] 9. Fix REQ-7 — Fix logic failures in `test_quality_control_pipeline.py`

  - [x] 9.1 Fix `test_reconciler_call_is_strategy_driven_and_extractor_agnostic`
    - Add `from quality_control.models import SemanticLayer, StructuralLayer` to the imports at the top of `tests/pdf_extractor/test_quality_control_pipeline.py`
    - Replace `mock_reconcile.return_value` inside the test: change `semantic=MagicMock(sentences=[])` and `structural=MagicMock()` to `semantic=SemanticLayer(paragraphs=[], sentences=[])` and `structural=StructuralLayer()`
    - Use the exact replacement from design Fix 7a
    - _Bug_Condition: isBugCondition where the test fails because `MagicMock().paragraphs` is not a list and iteration in the reconciler closure raises `TypeError`_
    - _Expected_Behavior: test passes with a real `SemanticLayer(paragraphs=[], sentences=[])` that the reconciler can iterate safely_
    - _Preservation: no production code is modified; only the mock return value is corrected_
    - _Requirements: REQ-7_

  - [x] 9.2 Verify REQ-7 logic failures are resolved
    - Run: `python -m pytest tests/pdf_extractor/test_quality_control_pipeline.py::TestPipelineOrchestration::test_reconciler_call_is_strategy_driven_and_extractor_agnostic tests/pdf_extractor/test_quality_control_pipeline.py::test_full_pipeline_integration -q`
    - **EXPECTED OUTCOME**: Both tests pass (REQ-7a fixed by 9.1; REQ-7b fixed by REQ-4 in task 6)

- [x] 10. Fix REQ-8 — Fix `git grep` exclusion pattern in `test_domain_agnosticism.py`

  - [x] 10.1 Add `:(exclude)**/test_*.py` to the `git grep` call in `test_no_tesseract_references`
    - In `tests/pdf_extractor/test_domain_agnosticism.py`, locate `TestAcceptanceCriteriaVerification.test_no_tesseract_references`
    - Add `":(exclude)**/test_*.py"` to the exclusion list in the first `git grep` call (the one searching for `extract_with_tesseract`)
    - Do not change the second `git grep` call (the one searching for `pytesseract`)
    - Use the exact replacement from design Fix 8
    - _Bug_Condition: isBugCondition where `test_no_tesseract_references` fails because `git grep` finds `extract_with_tesseract` in old test files (e.g., `test_text_extractor_orchestrator.py`) that are not excluded by the current `:(exclude)**.md` pattern_
    - _Expected_Behavior: `git grep` returns empty output (no non-test, non-doc references to `extract_with_tesseract`); assertion passes_
    - _Preservation: the grep still correctly finds any non-test, non-doc references to `extract_with_tesseract` if they exist_
    - _Requirements: REQ-8_

  - [x] 10.2 Verify REQ-8 grep test passes
    - Run: `python -m pytest tests/pdf_extractor/test_domain_agnosticism.py::TestAcceptanceCriteriaVerification::test_no_tesseract_references -q`
    - **EXPECTED OUTCOME**: Test passes

- [x] 11. Fix implementation — Verify bug condition exploration test now passes

  - [x] 11.1 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Clean Collection and Zero Failures
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - Run `python -m pytest --collect-only -q` and assert zero collection errors
    - Run `python -m pytest -q` and assert zero failures outside the four intentionally-failing steering-drift tests
    - **EXPECTED OUTCOME**: Test PASSES (confirms all eight bugs are fixed)
    - _Requirements: REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8_

  - [x] 11.2 Verify preservation tests still pass
    - **Property 2: Preservation** - No Regression in Currently-Passing Tests
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run: `python -m pytest -q`
    - **EXPECTED OUTCOME**: All previously-passing tests still pass; no regressions introduced

- [x] 12. Checkpoint — Ensure all tests pass
  - Run the full suite: `python -m pytest -q`
  - Confirm zero collection errors
  - Confirm zero failures outside the four intentionally-failing steering-drift tests:
    - `test_steering_drift_bug_condition.py::test_qc_file_names`
    - `test_steering_drift_bug_condition.py::test_run_py_pdf_discovery_call_site`
    - `test_steering_drift_bug_condition.py::test_qc_test_file_names`
    - `test_steering_drift_bug_condition.py::test_cascade_order_pdfplumber_before_paddleocr`
  - Ensure all tests pass; ask the user if questions arise.
