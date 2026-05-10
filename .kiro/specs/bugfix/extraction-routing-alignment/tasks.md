# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Extraction Routing Bypass
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate that `_build_qc_context` bypasses per-page routing
  - **Scoped PBT Approach**: Scope the property to the concrete failing cases — native PDFs and scanned PDFs with `ocr=true` — to ensure reproducibility
  - Test file: `tests/steering/test_steering_drift_extraction_routing_alignment_bug_condition.py`
  - Mock `fitz.open`, `extract_with_grobid`, `extract_with_pymupdf`, `extract_with_pdfplumber`, `extract_with_paddleocr`, and `scan_detector.classify_page` using `unittest.mock`
  - Sub-check 1 — Native PDF branch set: mock a single-page native PDF; assert `ctx.branches` contains `"grobid"` and `"pdfplumber"` extractors and does NOT contain `"pymupdf"` as a branch (from Bug Condition: `"pdfplumber" NOT IN extractor_names` OR `"pymupdf" IN extractor_names AND all_pages_native`)
  - Sub-check 2 — Scanned PDF + `ocr=true` branch set: mock a single-page scanned PDF with `ocr: true`; assert `ctx.branches` contains `"paddleocr"` and `"pymupdf"` extractors (from Bug Condition: `has_scanned_pages AND "paddleocr" NOT IN extractor_names`)
  - Sub-check 3 — Scan detection invocation: mock a two-page native PDF; assert `scan_detector.classify_page` is called exactly twice before any extraction backend is invoked
  - Sub-check 4 — Scanned page + `ocr=false` skip: mock a single-page scanned PDF with `ocr: false`; assert a WARNING is logged and no extraction branch is produced for that page
  - Sub-check 5 — Dead code removal: assert `hasattr(pdf_extractor.extraction, "extract_pdf")` is `False`
  - Use `pytest.fail()` with descriptive messages referencing the deviation number (e.g. `"Deviation 1.1"`) for each sub-check
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug exists)
  - Document counterexamples found (e.g. `ctx.branches` contains `BranchOutput(extractor="pymupdf")` instead of `BranchOutput(extractor="pdfplumber")` for native PDFs; `classify_page` call count is 0)
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - GROBID and QC Pipeline Continuity
  - **IMPORTANT**: Follow observation-first methodology
  - Test file: `tests/steering/test_steering_drift_extraction_routing_alignment_preservation.py`
  - Mock `fitz.open`, `extract_with_grobid`, `extract_with_pdfplumber`, `scan_detector.classify_page`, and `run_quality_control` using `unittest.mock`; never call real GROBID, PaddleOCR, or OpenAI
  - Observe on UNFIXED code: `extract_with_grobid()` is called and its output appears in a `BranchOutput(extractor="grobid")` branch for native PDFs
  - Observe on UNFIXED code: `run_quality_control()` is called and the returned `QCContext` has `unified`, `reports`, `iaa_metrics`, and `decision` all set (not `None`)
  - Observe on UNFIXED code: when GROBID raises and `failure_behavior="fallback"`, a WARNING is logged and processing continues with empty TEI XML
  - Observe on UNFIXED code: when GROBID raises and `failure_behavior="manifest_fail"`, the exception is re-raised
  - Observe on UNFIXED code: `ctx.unified.content["source_pdf_path"]` and `ctx.unified.content["grobid_tei_xml"]` are set after `_build_qc_context` completes
  - Write property-based tests (Hypothesis `@given`) capturing these observed behaviors across the input domain:
    - PBT: for any native PDF (random page count 1–20, all pages native), `extract_with_grobid()` is called and `ctx.branches` always contains a `BranchOutput(extractor="grobid")`
    - PBT: for any native PDF, `run_quality_control()` is called and the returned `QCContext` always has `unified`, `reports`, `iaa_metrics`, and `decision` set
    - PBT: for any random GROBID TEI XML string, `ctx.unified.content["grobid_tei_xml"]` always equals the generated string after `_build_qc_context` completes
  - Verify tests PASS on UNFIXED code before implementing the fix
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix extraction routing alignment

  - [x] 3.1 Add per-page scan detection to `_build_qc_context` in `pipeline/orchestrator.py`
    - Add imports: `scan_detector` from `pdf_extractor.extraction`, `extract_with_pdfplumber` from `pdf_extractor.extraction.pdfplumber`, `extract_with_paddleocr` from `pdf_extractor.extraction.PaddleOCR`, `TextProcessor` from `utils.text_processor`
    - Open the PDF with `fitz.open(pdf_path)`, iterate over every page, call `scan_detector.classify_page(page, tp, qc_config, page_index=i)` for each page, collect the resulting `PageScanClassification` objects, close the document after classification
    - _Bug_Condition: `isBugCondition(pdf_path, qc_config)` where `"pdfplumber" NOT IN extractor_names` OR `"pymupdf" IN extractor_names AND all_pages_native(pdf_path)` OR `has_scanned_pages(pdf_path) AND "paddleocr" NOT IN extractor_names`_
    - _Expected_Behavior: `scan_detector.classify_page()` is called on every page before any extraction backend is invoked, producing a `PageScanClassification` for each page_
    - _Preservation: `scan_detector.classify_page()` MUST continue to return `PageScanClassification` with `is_native=True` only when no detection stage fires (Requirement 3.6)_
    - _Requirements: 2.1_

  - [x] 3.2 Replace flat branch construction with per-page routing logic in `_build_qc_context`
    - Native path (all pages native): call `extract_with_grobid()` (semantic authority) and `extract_with_pdfplumber()` (structural authority); collect PyMuPDF font metadata separately via `extract_with_pymupdf()` or `get_page_font_metadata()` and store it in `ctx.unified.content` — NOT as a branch; `branches` list MUST contain exactly `BranchOutput(extractor="grobid")` and `BranchOutput(extractor="pdfplumber")`
    - Scanned path with `ocr=false`: skip extraction for that page, log a WARNING with the page index and PDF name via `get_logger(__name__)`, record the skipped page in the manifest entry; produce no branch for that page
    - Scanned path with `ocr=true`: call `extract_with_paddleocr()` (primary) and `extract_with_pymupdf()` built-in OCR (secondary cross-validation); feed these branches into GROBID for downstream processing; `branches` list MUST contain exactly `BranchOutput(extractor="paddleocr")` and `BranchOutput(extractor="pymupdf")`
    - Preserve the existing try/except around `extract_with_grobid()` and the `failure_behavior` check unchanged
    - Preserve the existing block that sets `ctx.unified.content["source_pdf_path"]` and `ctx.unified.content["grobid_tei_xml"]` unchanged
    - _Bug_Condition: `isBugCondition(pdf_path, qc_config)` — flat hardcoded `[BranchOutput("grobid"), BranchOutput("pymupdf")]` regardless of page classification_
    - _Expected_Behavior: native PDFs → `["grobid", "pdfplumber"]` branches; scanned+ocr=true → `["paddleocr", "pymupdf"]` branches; scanned+ocr=false → skip + WARNING + manifest record_
    - _Preservation: GROBID MUST continue to be called for native PDFs; `run_quality_control()` MUST continue to be called and return fully populated `QCContext`; GROBID failure handling MUST remain unchanged; `ctx.unified.content` population MUST remain unchanged (Requirements 3.1–3.5)_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 3.3 Remove `extract_pdf()` from `pdf_extractor/extraction/__init__.py`
    - Delete the entire `extract_pdf()` function body and its docstring from `pdf_extractor/extraction/__init__.py`
    - Update the module docstring to remove any reference to the three-tier cascade architecture
    - Verify the file exposes only the re-exports and helpers required by live code paths: `schemas`, `PyMuPDF`, `extract_with_pymupdf`, `extract_with_pdfplumber`, `extract_with_paddleocr`, `scan_detector`
    - Verify no `ImportError` is raised from `orchestrator.py`, `pdf_processor.py`, or any other live module after removal
    - _Bug_Condition: `hasattr(pdf_extractor.extraction, "extract_pdf")` is `True` — dead legacy function still importable_
    - _Expected_Behavior: `hasattr(pdf_extractor.extraction, "extract_pdf")` is `False` after the fix_
    - _Preservation: all existing re-exports and helpers used by live code paths MUST remain importable without error_
    - _Requirements: 2.5_

  - [x] 3.4 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Extraction Routing Bypass
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior for all five sub-checks
    - When this test passes, it confirms: native PDFs produce `["grobid", "pdfplumber"]` branches; scanned+ocr=true produces `["paddleocr", "pymupdf"]` branches; `classify_page` is called once per page; scanned+ocr=false is skipped with WARNING; `extract_pdf` is absent
    - Run `tests/steering/test_steering_drift_extraction_routing_alignment_bug_condition.py` on FIXED code
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.5 Verify preservation tests still pass
    - **Property 2: Preservation** - GROBID and QC Pipeline Continuity
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run `tests/steering/test_steering_drift_extraction_routing_alignment_preservation.py` on FIXED code
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions in GROBID extraction, QC pipeline continuity, failure handling, and `ctx.unified.content` population)
    - Confirm all property-based tests still pass after fix (no regressions)

- [ ] 4. Checkpoint — Ensure all tests pass
  - Run `python -m pytest tests/steering/test_steering_drift_extraction_routing_alignment_bug_condition.py tests/steering/test_steering_drift_extraction_routing_alignment_preservation.py -v` from the repo root
  - Confirm all sub-checks in the bug condition test pass
  - Confirm all property-based preservation tests pass
  - Run `python -m pytest -q` to confirm no regressions in the broader fast test suite
  - Ask the user if any questions arise
