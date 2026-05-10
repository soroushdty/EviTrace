# Extraction Routing Alignment Bugfix Design

## Overview

`_build_qc_context` in `pipeline/orchestrator.py` bypasses the per-page routing
architecture defined in `product.md`. It calls `extract_with_grobid()` and
`extract_with_pymupdf()` directly — without first classifying pages via
`scan_detector.classify_page()` — and places PyMuPDF in the structural authority
branch role that belongs to pdfplumber. PaddleOCR is never invoked, so scanned
PDFs receive no OCR extraction at all.

Additionally, `extract_pdf()` in `pdf_extractor/extraction/__init__.py` is dead
legacy code from the old three-tier cascade architecture. It is no longer called
by any live code path and must be removed.

The fix replaces the flat two-branch call in `_build_qc_context` with a
per-page scan-detection router that feeds the correct extractor set into
`QCContext.branches` for each page class, and removes the dead `extract_pdf()`
function from the extraction package's public API.

---

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug — `_build_qc_context`
  is called without per-page scan detection routing, producing a `QCContext` whose
  `branches` list either omits pdfplumber, uses PyMuPDF as the structural authority
  on a native PDF, or omits PaddleOCR on a scanned PDF.
- **Property (P)**: The desired behavior when the bug condition holds — after the
  fix, `_build_qc_context` SHALL run `scan_detector.classify_page()` on every page
  first, then populate `branches` with the correct extractor set for each page class.
- **Preservation**: Existing behaviors that must remain unchanged — GROBID is still
  called for native PDFs, the full QC pipeline still runs, GROBID failure handling
  is unchanged, and `ctx.unified.content` is still populated.
- **`_build_qc_context`**: The function in `pipeline/orchestrator.py` that runs
  extraction and the full QC pipeline for one PDF, returning a `QCContext`.
- **`extract_pdf`**: The dead three-tier cascade function in
  `pdf_extractor/extraction/__init__.py` that is no longer called by any live code
  path.
- **`PageScanClassification`**: The dataclass returned by
  `scan_detector.classify_page()` with `is_native`, `triggered_stages`, and
  `stage_values` fields.
- **`BranchOutput`**: The dataclass in `quality_control/models.py` representing one
  extractor branch: `extractor`, `branch`, `payload`, `status`.
- **native PDF**: A PDF where every page returns `is_native=True` from
  `scan_detector.classify_page()` (no detection stage fires).
- **scanned PDF**: A PDF where at least one page returns `is_native=False`.
- **`ocr` flag**: The `quality_control.grobid_integration` config key (or equivalent
  local config key) that controls whether OCR extraction is attempted for scanned pages.

---

## Bug Details

### Bug Condition

The bug manifests when `_build_qc_context` is called for any PDF. The function
calls `extract_with_grobid()` and `extract_with_pymupdf()` directly without first
running `scan_detector.classify_page()` on any page. It then constructs a
`BranchOutput(extractor="pymupdf")` as the structural authority branch — a role
that `product.md` assigns to pdfplumber — and never creates a
`BranchOutput(extractor="pdfplumber")` or `BranchOutput(extractor="paddleocr")`.

**Formal Specification:**

```
FUNCTION isBugCondition(pdf_path, qc_config)
  INPUT: pdf_path of type Path, qc_config of type dict
  OUTPUT: boolean

  branches ← _build_qc_context(pdf_path, pdf_path.stem, qc_config).branches
  extractor_names ← {b.extractor FOR b IN branches}

  RETURN ("pdfplumber" NOT IN extractor_names)
      OR ("pymupdf" IN extractor_names AND all_pages_native(pdf_path))
      OR (has_scanned_pages(pdf_path) AND "paddleocr" NOT IN extractor_names)
END FUNCTION
```

### Examples

- **Native PDF, current behavior**: `_build_qc_context` produces
  `branches = [BranchOutput("grobid", ...), BranchOutput("pymupdf", ...)]`.
  Expected: `[BranchOutput("grobid", ...), BranchOutput("pdfplumber", ...)]` with
  PyMuPDF font metadata stored in `ctx.unified.content`, not as a branch.

- **Scanned PDF with `ocr: true`, current behavior**: `_build_qc_context` produces
  `branches = [BranchOutput("grobid", ...), BranchOutput("pymupdf", ...)]` — no
  PaddleOCR branch at all. Expected:
  `[BranchOutput("paddleocr", ...), BranchOutput("pymupdf", ...)]` fed into GROBID.

- **Scanned PDF with `ocr: false`, current behavior**: Same flat two-branch output
  as above. Expected: page skipped, WARNING logged, manifest entry updated.

- **Dead code**: `from pdf_extractor.extraction import extract_pdf` succeeds on
  current code. Expected: `extract_pdf` is not present in the module after the fix.

---

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `extract_with_grobid()` MUST continue to be called for native PDFs, and the
  resulting TEI XML MUST continue to appear in a `BranchOutput(extractor="grobid")`.
- The resulting `QCContext` MUST continue to be passed to `run_quality_control()`
  and returned with `unified`, `reports`, `iaa_metrics`, and `decision` populated.
- GROBID failure handling MUST remain unchanged: `failure_behavior="fallback"` logs
  a warning and continues with empty TEI; `failure_behavior="manifest_fail"` re-raises.
- `ctx.unified.content["source_pdf_path"]` and `ctx.unified.content["grobid_tei_xml"]`
  MUST continue to be set when `ctx.unified` is not `None`.
- `scan_detector.classify_page()` MUST continue to return `PageScanClassification`
  with `is_native=True` only when no detection stage fires.
- `extract_with_pdfplumber()` MUST continue to return `list[BlockDict]` with
  `[PAGE n]` and table markers preserved.
- `extract_with_paddleocr()` MUST continue to return `list[BlockDict]` with
  bounding-box coordinates converted to PDF user-space points.

**Scope:**
All inputs that do NOT trigger the bug condition — i.e., inputs where the current
code already produces the correct branch set — must be completely unaffected by
this fix. This includes:
- GROBID extraction and failure handling logic
- The `run_quality_control()` call and its return value
- Population of `ctx.unified.content`
- All existing behavior of `scan_detector.classify_page()`
- All existing behavior of `extract_with_pdfplumber()` and `extract_with_paddleocr()`

---

## Hypothesized Root Cause

Based on the bug description and reading `pipeline/orchestrator.py`, the causes are:

1. **Missing scan detection call**: `_build_qc_context` never imports or calls
   `scan_detector.classify_page()`. It calls `extract_with_grobid()` and
   `extract_with_pymupdf()` unconditionally for every PDF, regardless of page
   classification.

2. **Wrong structural authority extractor**: The `branches` list is hardcoded as
   `[BranchOutput("grobid", ...), BranchOutput("pymupdf", ...)]`. PyMuPDF is placed
   in the structural authority slot, but `product.md` assigns that role to pdfplumber.
   PyMuPDF's role is font metadata and comparison signals, stored outside `branches`.

3. **pdfplumber never called**: `extract_with_pdfplumber` is not imported in
   `orchestrator.py` and is never called from `_build_qc_context`.

4. **PaddleOCR never called**: `extract_with_paddleocr` is not imported in
   `orchestrator.py` and is never called from `_build_qc_context`. Scanned pages
   receive no OCR extraction.

5. **Dead `extract_pdf()` function**: `pdf_extractor/extraction/__init__.py` still
   exposes `extract_pdf()`, a three-tier cascade function from the old architecture.
   No live code path calls it, but it remains importable and adds confusion about
   the intended extraction flow.

---

## Correctness Properties

Property 1: Bug Condition — Native PDF Branch Set

_For any_ PDF where all pages are classified as native (`all_pages_native` is true),
the fixed `_build_qc_context` SHALL produce a `QCContext` whose `branches` list
contains exactly `BranchOutput(extractor="grobid")` and
`BranchOutput(extractor="pdfplumber")`, and SHALL NOT contain any
`BranchOutput(extractor="pymupdf")` as a QC branch. PyMuPDF font metadata SHALL
be stored in `ctx.unified.content` as a comparison signal, not as a branch.

**Validates: Requirements 2.1, 2.2**

Property 2: Bug Condition — Scanned PDF Branch Set (OCR enabled)

_For any_ PDF where at least one page is classified as scanned and `ocr` is `true`
in config, the fixed `_build_qc_context` SHALL produce a `QCContext` whose
`branches` list contains exactly `BranchOutput(extractor="paddleocr")` (primary)
and `BranchOutput(extractor="pymupdf")` (secondary cross-validation), and these
branches SHALL be fed into GROBID for downstream processing.

**Validates: Requirements 2.1, 2.4**

Property 3: Preservation — GROBID and QC Pipeline Continuity

_For any_ native PDF input where the bug condition does NOT hold (i.e., the fixed
code produces the correct branch set), the fixed `_build_qc_context` SHALL
continue to call `extract_with_grobid()`, pass the resulting branches to
`run_quality_control()`, and return a fully populated `QCContext` with `unified`,
`reports`, `iaa_metrics`, and `decision` set — identical to what the original
function would produce for the same GROBID and pdfplumber outputs.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

---

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File 1**: `pipeline/orchestrator.py`

**Function**: `_build_qc_context`

**Specific Changes**:

1. **Add imports**: Import `scan_detector` from `pdf_extractor.extraction`,
   `extract_with_pdfplumber` from `pdf_extractor.extraction.pdfplumber`,
   `extract_with_paddleocr` from `pdf_extractor.extraction.PaddleOCR`,
   and `TextProcessor` from `utils.text_processor`. Remove the import of
   `extract_with_pymupdf` from the top-level branches construction (it may
   still be needed for font metadata collection).

2. **Add per-page scan detection**: Open the PDF with `fitz.open(pdf_path)`,
   iterate over every page, call `scan_detector.classify_page(page, tp, qc_config,
   page_index=i)` for each page, and collect the resulting
   `PageScanClassification` objects. Close the document after classification.

3. **Replace flat branch construction with routing logic**:
   - If all pages are native: call `extract_with_grobid()` (semantic authority)
     and `extract_with_pdfplumber()` (structural authority). Collect PyMuPDF font
     metadata separately via `extract_with_pymupdf()` or `get_page_font_metadata()`
     and store it in `ctx.unified.content` — not as a branch.
   - If any page is scanned and `ocr` is `false` in config: skip extraction for
     that page, log a WARNING with the page index and PDF name, and record the
     skipped page in the manifest entry.
   - If any page is scanned and `ocr` is `true` in config: call
     `extract_with_paddleocr()` (primary) and `extract_with_pymupdf()` built-in
     OCR (secondary cross-validation). Feed these branches into GROBID for
     downstream processing.

4. **Preserve GROBID failure handling**: The existing try/except around
   `extract_with_grobid()` and the `failure_behavior` check must remain unchanged.

5. **Preserve `ctx.unified.content` population**: The existing block that sets
   `ctx.unified.content["source_pdf_path"]` and `ctx.unified.content["grobid_tei_xml"]`
   must remain unchanged.

---

**File 2**: `pdf_extractor/extraction/__init__.py`

**Specific Changes**:

1. **Remove `extract_pdf()`**: Delete the entire `extract_pdf()` function body and
   its docstring. The file SHALL expose only the re-exports and helpers required by
   live code paths: `schemas`, `PyMuPDF`, `extract_with_pymupdf`,
   `extract_with_pdfplumber`, `extract_with_paddleocr`, `scan_detector`.

2. **Update module docstring**: Remove the docstring that describes the three-tier
   cascade architecture, since that architecture no longer exists.

---

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples
that demonstrate the bug on unfixed code, then verify the fix works correctly and
preserves existing behavior.

Tests live in `tests/steering/` following the steering-drift regression test
convention. The bug condition file is named
`test_steering_drift_extraction_routing_alignment_bug_condition.py` and the
preservation file is named
`test_steering_drift_extraction_routing_alignment_preservation.py`.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the
fix. Confirm or refute the root cause analysis. If we refute, we will need to
re-hypothesize.

**Test Plan**: Write tests that call `_build_qc_context` with mocked PDF inputs
(using `unittest.mock` to patch `fitz.open`, `extract_with_grobid`,
`extract_with_pymupdf`, `extract_with_pdfplumber`, `extract_with_paddleocr`, and
`scan_detector.classify_page`) and assert the expected post-fix branch set. Run
these tests on the UNFIXED code to observe failures and confirm the root cause.

**Test Cases**:

1. **Native PDF branch set test**: Mock a single-page native PDF. Assert that
   `ctx.branches` contains `"grobid"` and `"pdfplumber"` extractors and does NOT
   contain `"pymupdf"` as a branch. Will fail on unfixed code because branches
   contain `"pymupdf"` instead of `"pdfplumber"`.

2. **Scanned PDF + ocr=true branch set test**: Mock a single-page scanned PDF with
   `ocr: true`. Assert that `ctx.branches` contains `"paddleocr"` and `"pymupdf"`
   extractors. Will fail on unfixed code because PaddleOCR is never called.

3. **Scan detection invocation test**: Mock a two-page native PDF. Assert that
   `scan_detector.classify_page` is called exactly twice (once per page) before any
   extraction backend is invoked. Will fail on unfixed code because
   `classify_page` is never called.

4. **Scanned page + ocr=false skip test**: Mock a single-page scanned PDF with
   `ocr: false`. Assert that a WARNING is logged and no extraction branch is
   produced for that page. Will fail on unfixed code because the page is not
   skipped.

5. **Dead code removal test**: Assert that `extract_pdf` is not importable from
   `pdf_extractor.extraction` (i.e., `hasattr(pdf_extractor.extraction, "extract_pdf")`
   is `False`). Will fail on unfixed code because the function still exists.

**Expected Counterexamples**:
- `ctx.branches` contains `BranchOutput(extractor="pymupdf")` instead of
  `BranchOutput(extractor="pdfplumber")` for native PDFs.
- `ctx.branches` contains no `BranchOutput(extractor="paddleocr")` for scanned PDFs.
- `scan_detector.classify_page` call count is 0 for any PDF.
- Possible causes: missing import of `scan_detector` and `extract_with_pdfplumber`
  in `orchestrator.py`, hardcoded branch list, no routing logic.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed
function produces the expected behavior.

**Pseudocode:**
```
FOR ALL pdf_path WHERE isBugCondition(pdf_path, qc_config) DO
  ctx ← _build_qc_context_fixed(pdf_path, pdf_path.stem, qc_config)
  extractor_names ← {b.extractor FOR b IN ctx.branches}

  IF all_pages_native(pdf_path) THEN
    ASSERT "grobid" IN extractor_names
    ASSERT "pdfplumber" IN extractor_names
    ASSERT "pymupdf" NOT IN extractor_names
  END IF

  IF has_scanned_pages(pdf_path) AND ocr_enabled(qc_config) THEN
    ASSERT "paddleocr" IN extractor_names
    ASSERT "pymupdf" IN extractor_names
  END IF
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the
fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL pdf_path WHERE NOT isBugCondition(pdf_path, qc_config) DO
  ASSERT _build_qc_context(pdf_path, ...) = _build_qc_context_fixed(pdf_path, ...)
  // i.e. GROBID is still called, QC pipeline still runs, unified record is populated
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation
checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for GROBID extraction and QC
pipeline execution, then write property-based tests capturing that behavior.

**Test Cases**:

1. **GROBID call preservation**: For any native PDF, verify that
   `extract_with_grobid()` is still called and its output appears in a
   `BranchOutput(extractor="grobid")` branch.

2. **QC pipeline continuity**: For any native PDF, verify that `run_quality_control()`
   is called with the branches and that the returned `QCContext` has `unified`,
   `reports`, `iaa_metrics`, and `decision` all set (not `None`).

3. **GROBID fallback preservation**: When GROBID raises and
   `failure_behavior="fallback"`, verify a WARNING is logged and processing
   continues with empty TEI XML.

4. **GROBID manifest_fail preservation**: When GROBID raises and
   `failure_behavior="manifest_fail"`, verify the exception is re-raised.

5. **unified.content preservation**: After `_build_qc_context` completes, verify
   `ctx.unified.content["source_pdf_path"]` and
   `ctx.unified.content["grobid_tei_xml"]` are set.

### Unit Tests

- Test that `scan_detector.classify_page()` is called once per page in
  `_build_qc_context` for a multi-page PDF.
- Test that a native PDF produces exactly `["grobid", "pdfplumber"]` extractor
  names in `ctx.branches`.
- Test that a scanned PDF with `ocr=true` produces exactly `["paddleocr", "pymupdf"]`
  extractor names in `ctx.branches`.
- Test that a scanned PDF with `ocr=false` produces no branches for the scanned
  page, logs a WARNING, and records the skip.
- Test that `extract_pdf` is absent from `pdf_extractor.extraction`'s public API.
- Test edge case: PDF with zero pages produces an empty branches list without error.

### Property-Based Tests

- Generate random page counts (1–20) for all-native PDFs and verify that
  `scan_detector.classify_page` is called exactly N times and branches always
  contain exactly `"grobid"` and `"pdfplumber"`.
- Generate random mixes of native and scanned pages with `ocr=true` and verify
  that `"paddleocr"` and `"pymupdf"` branches are always produced when any page
  is scanned.
- Generate random GROBID TEI XML strings and verify that `ctx.unified.content`
  always contains `"grobid_tei_xml"` equal to the generated string after the fix.

### Integration Tests

- Test full `_build_qc_context` flow for a mocked native PDF: scan detection →
  GROBID extraction → pdfplumber extraction → QC pipeline → populated `QCContext`.
- Test full `_build_qc_context` flow for a mocked scanned PDF with `ocr=true`:
  scan detection → PaddleOCR extraction → PyMuPDF OCR extraction → GROBID →
  QC pipeline → populated `QCContext`.
- Test that removing `extract_pdf()` does not break any existing import in the
  live code paths (verify no `ImportError` from `orchestrator.py`,
  `pdf_processor.py`, or any other live module).
