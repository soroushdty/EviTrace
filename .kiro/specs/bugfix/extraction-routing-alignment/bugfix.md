# Bugfix Requirements Document

## Introduction

`_build_qc_context` in `pipeline/orchestrator.py` bypasses the per-page routing
architecture described in `product.md`. It calls GROBID and PyMuPDF directly
without first classifying pages, uses PyMuPDF as a structural QC branch (a role
that belongs to pdfplumber), never invokes PaddleOCR, and has no handling for
scanned pages at all. Additionally, `extract_pdf()` in
`pdf_extractor/extraction/__init__.py` is dead legacy code from the old
cascade architecture that is no longer called by any live code path.

The fix must replace the flat two-branch call with a per-page scan-detection
router that feeds the correct extractor set into `QCContext.branches` for each
page class, and must remove the dead `extract_pdf()` function.

---

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `_build_qc_context` is called for any PDF THEN the system calls
`extract_with_grobid()` and `extract_with_pymupdf()` directly without first
running `scan_detector.classify_page()` on any page.

1.2 WHEN `_build_qc_context` builds the `branches` list THEN the system
creates a `BranchOutput(extractor="pymupdf")` as the structural authority
branch, placing PyMuPDF in a role that product.md assigns to pdfplumber.

1.3 WHEN `_build_qc_context` builds the `branches` list THEN the system never
creates a `BranchOutput(extractor="pdfplumber")`, so pdfplumber is absent from
the QC pipeline entirely.

1.4 WHEN `_build_qc_context` is called for a PDF that contains scanned pages
THEN the system does not call `extract_with_paddleocr()` and produces no
`BranchOutput(extractor="paddleocr")` branch.

1.5 WHEN a page is classified as scanned and `ocr` is `false` in config THEN
the system neither skips the page, nor logs the skip, nor records it in the
manifest.

1.6 WHEN a page is classified as scanned and `ocr` is `true` in config THEN
the system does not call `extract_with_paddleocr()` as the primary extractor
or PyMuPDF built-in OCR as the secondary cross-validation extractor for that
page.

1.7 WHEN `pdf_extractor/extraction/__init__.py` is imported THEN the system
exposes `extract_pdf()`, a three-tier cascade function that is no longer
called by any live code path and implements the old architecture.

### Expected Behavior (Correct)

2.1 WHEN `_build_qc_context` is called for any PDF THEN the system SHALL run
`scan_detector.classify_page()` on every page before invoking any extraction
backend, producing a `PageScanClassification` for each page.

2.2 WHEN all pages in a PDF are classified as native THEN the system SHALL
produce a `QCContext` whose `branches` list contains exactly:
- `BranchOutput(extractor="grobid")` carrying the TEI XML string (semantic authority)
- `BranchOutput(extractor="pdfplumber")` carrying the pdfplumber blocks (structural authority)

PyMuPDF font metadata SHALL be collected separately and stored outside
`branches` (e.g. in `ctx.unified.content`) as comparison signals, not as a
QC branch.

2.3 WHEN any page in a PDF is classified as scanned and `ocr` is `false` in
config THEN the system SHALL skip extraction for that page, log the skip at
WARNING level with the page index and PDF name, and record the skipped page in
the manifest entry for that PDF.

2.4 WHEN any page in a PDF is classified as scanned and `ocr` is `true` in
config THEN the system SHALL produce a `QCContext` whose `branches` list
contains exactly:
- `BranchOutput(extractor="paddleocr")` carrying PaddleOCR blocks (primary extractor)
- `BranchOutput(extractor="pymupdf")` carrying PyMuPDF built-in OCR blocks (secondary cross-validation)

These branches SHALL then be fed into GROBID for downstream processing at
expected lower quality.

2.5 WHEN `pdf_extractor/extraction/__init__.py` is modified THEN the system
SHALL NOT contain the `extract_pdf()` function; the file SHALL expose only the
re-exports and helpers required by the live code paths.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a fully native PDF is processed THEN the system SHALL CONTINUE TO
call `extract_with_grobid()` and produce a `BranchOutput(extractor="grobid")`
carrying the TEI XML string.

3.2 WHEN a fully native PDF is processed THEN the system SHALL CONTINUE TO
pass the resulting `QCContext` to `run_quality_control()` and return a fully
populated `QCContext` with `unified`, `reports`, `iaa_metrics`, and `decision`
set.

3.3 WHEN GROBID fails and `failure_behavior` is `"fallback"` in config THEN
the system SHALL CONTINUE TO log a warning and proceed with an empty TEI XML
string rather than raising.

3.4 WHEN GROBID fails and `failure_behavior` is `"manifest_fail"` in config
THEN the system SHALL CONTINUE TO re-raise the exception so the caller can
record the failure in the manifest.

3.5 WHEN `_build_qc_context` completes successfully THEN the system SHALL
CONTINUE TO store `source_pdf_path` and `grobid_tei_xml` in
`ctx.unified.content` when `ctx.unified` is not `None`.

3.6 WHEN `scan_detector.classify_page()` is called THEN the system SHALL
CONTINUE TO return a `PageScanClassification` with `is_native=True` only when
none of the five detection stages fire, and `is_native=False` otherwise.

3.7 WHEN `extract_with_pdfplumber()` is called on a native PDF THEN the system
SHALL CONTINUE TO return a list of `BlockDict` objects, one per page, with
`[PAGE n]` markers and table markers preserved.

3.8 WHEN `extract_with_paddleocr()` is called on a scanned PDF THEN the system
SHALL CONTINUE TO return a list of `BlockDict` objects with bounding-box
coordinates converted to PDF user-space points.

---

## Bug Condition Pseudocode

### Bug Condition Function

```pascal
FUNCTION isBugCondition(pdf_path, qc_config)
  INPUT: pdf_path of type Path, qc_config of type dict
  OUTPUT: boolean

  // Returns true when the bug condition is met:
  // _build_qc_context is called without per-page scan detection routing
  branches ← _build_qc_context(pdf_path, pdf_path.stem, qc_config).branches
  extractor_names ← {b.extractor FOR b IN branches}

  // Bug fires when pdfplumber is absent OR pymupdf is used as structural branch
  // on a native PDF, OR paddleocr is absent on a scanned PDF
  RETURN ("pdfplumber" NOT IN extractor_names)
      OR ("pymupdf" IN extractor_names AND all_pages_native(pdf_path))
      OR (has_scanned_pages(pdf_path) AND "paddleocr" NOT IN extractor_names)
END FUNCTION
```

### Fix-Checking Property

```pascal
// Property: Fix Checking — correct branch set for native PDFs
FOR ALL pdf_path WHERE all_pages_native(pdf_path) DO
  ctx ← _build_qc_context'(pdf_path, pdf_path.stem, qc_config)
  extractor_names ← {b.extractor FOR b IN ctx.branches}
  ASSERT "grobid" IN extractor_names
  ASSERT "pdfplumber" IN extractor_names
  ASSERT "pymupdf" NOT IN extractor_names  // PyMuPDF is not a QC branch
END FOR

// Property: Fix Checking — correct branch set for scanned PDFs with ocr:true
FOR ALL pdf_path WHERE has_scanned_pages(pdf_path) AND ocr_enabled(qc_config) DO
  ctx ← _build_qc_context'(pdf_path, pdf_path.stem, qc_config)
  extractor_names ← {b.extractor FOR b IN ctx.branches}
  ASSERT "paddleocr" IN extractor_names
  ASSERT "pymupdf" IN extractor_names
END FOR
```

### Preservation Property

```pascal
// Property: Preservation Checking
FOR ALL pdf_path WHERE NOT isBugCondition(pdf_path, qc_config) DO
  ASSERT _build_qc_context(pdf_path, ...) = _build_qc_context'(pdf_path, ...)
  // i.e. GROBID is still called, QC pipeline still runs, unified record is populated
END FOR
```
