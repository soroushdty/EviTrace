# Requirements Document

## Introduction

This spec covers the 14 requirements from the EviTrace Initial Audit Remediation that are either not implemented or only partially implemented. It converts each gap into actionable implementation requirements with concrete acceptance criteria.

The requirements are grouped into three milestones by priority: P0 correctness blockers first, then P1 evidence quality gaps, then P2 runtime reliability items.

## Glossary

- **Compact_Schema**: The JSON schema for LLM chunk output records using keys `i`, `v`, `loc`, `c`
- **Final_Schema**: The JSON schema for per-field extraction output records using keys `field_index`, `domain_group`, `field_name`, `extracted_value`, `evidence`, `location`, `location_metadata`, `confidence`
- **build_qc_bundle**: The function in `src/pipeline/extraction_pipeline.py` that orchestrates scan detection → backend routing → QC for one PDF
- **Evidence_Cache**: The disk cache in `outputs/evidence_cache/` keyed by paper identity
- **Manifest**: The `manifest.json` checkpoint file tracking per-PDF processing status
- **Repair_Prompt**: A follow-up LLM prompt that includes the parse/validation error from a failed chunk and asks the model to fix its output
- **Atomic_Write**: Writing to a temporary file then renaming to the final path, so the final path is never in a partial state

## Requirements

### Requirement 1: Final Output Schema Contract Validation

**User Story:** As a pipeline user, I want every emitted extraction JSON file to conform to a stable schema before it is written to disk, so that downstream analysis never encounters invalid output.

#### Acceptance Criteria

1. WHEN `_save_pdf_output()` is called THEN it SHALL validate the merged field list against the Final_Schema before writing to disk; if validation fails, the pipeline SHALL mark the PDF as `failed_schema_validation` in the manifest and NOT write the output file
2. WHEN schema validation fails THEN the error message SHALL include the field index, field name (if available), and the JSON path of the invalid element
3. WHEN `location_metadata` is present in a field record THEN every metadata item SHALL reference an evidence location ID that exists in the field's `location` list, or be explicitly marked `"unresolved"`
4. WHEN a CI test runs THEN it SHALL validate at least one representative compact chunk output and one representative final extraction document against their respective schemas
5. WHEN the Final_Schema is defined THEN it SHALL be stored as a JSON Schema file at `configs/final_output_schema.json` and loaded by a single validator class

---

### Requirement 2: OCR Configuration Propagation — DPI Wiring

**User Story:** As a user configuring the pipeline, I want `quality_control.ocr.rasterization_dpi` to be passed to OCR extractors, so that the configured resolution is actually used.

#### Acceptance Criteria

1. WHEN `build_qc_bundle()` invokes `extract_with_paddleocr()` THEN it SHALL pass the `rasterization_dpi` value from `quality_control.ocr.rasterization_dpi` (default 150) as a parameter
2. WHEN `extract_with_paddleocr()` receives a DPI parameter THEN it SHALL use that value for page rasterization instead of a hardcoded default
3. WHEN the legacy top-level `ocr` key is present in config alongside `quality_control.ocr` THEN `load_qc_config()` SHALL emit a deprecation warning and prefer `quality_control.ocr` settings
4. WHEN a unit test sets `quality_control.ocr.rasterization_dpi` to a non-default value THEN it SHALL assert that the OCR extractor receives that value

---

### Requirement 3: True Per-Page Extraction Routing

**User Story:** As a user processing mixed native/scanned PDFs, I want the pipeline to route extraction at the page level, so that one scanned page does not force OCR for the entire document.

#### Acceptance Criteria

1. WHEN a PDF contains both native-text and scanned pages THEN `build_qc_bundle()` SHALL route native pages through GROBID+pdfplumber and scanned pages through PaddleOCR+PyMuPDF, producing page-level extraction results that are merged in original page order
2. WHEN page-level routing completes THEN each page SHALL include routing metadata in the QCBundle: page number, selected extractor, fallback extractor (if any), and routing reason (which scan_detector stage fired or "all_native")
3. WHEN all pages are native THEN OCR extractors SHALL NOT be invoked unless `ocr` is explicitly forced in config
4. WHEN all pages are scanned and `ocr=false` THEN the pipeline SHALL log a WARNING per page and produce no extraction branch for those pages
5. WHEN a unit test provides a 3-page PDF fixture (native, scanned, native) THEN the test SHALL assert that pages 1 and 3 use native extractors and page 2 uses OCR extractors

---

### Requirement 4: Extractor Branch Quality-Based Reconciliation

**User Story:** As a pipeline maintainer, I want reconciliation to prefer the best available extraction branch based on quality scores, so that failed or weak branches cannot dominate the reconciled output.

#### Acceptance Criteria

1. WHEN an extractor branch fails or returns empty text THEN it SHALL NOT be selected as the primary branch unless all branches fail
2. WHEN multiple branches produce content THEN the primary branch SHALL be selected using a composite quality score considering: non-empty text (boolean), page coverage (fraction of pages with content), section structure presence, text length plausibility, OCR/noise indicators (weird-char ratio), and extractor agreement with other branches
3. WHEN `discard_failed_branches` is `true` in config THEN failed branches SHALL be excluded from the candidate set before primary-source selection
4. WHEN reconciliation selects a non-GROBID primary branch THEN the QC report SHALL record the reason (e.g., "GROBID branch empty", "GROBID quality score below threshold")
5. WHEN a unit test provides one empty GROBID branch and one valid pdfplumber branch THEN the test SHALL assert that pdfplumber is selected as primary

---

### Requirement 5: Validation-Aware LLM Retries

**User Story:** As a pipeline user, I want malformed LLM chunk outputs to be repaired automatically via targeted retry prompts, so that transient formatting errors do not cause field loss.

#### Acceptance Criteria

1. WHEN a model response fails JSON parsing THEN the pipeline SHALL retry with a Repair_Prompt that includes the parse error message and the required Compact_Schema format
2. WHEN a model response parses as JSON but fails schema validation THEN the pipeline SHALL retry with a Repair_Prompt listing the exact validation failures (missing keys, invalid confidence values, out-of-range field indexes)
3. WHEN a chunk contains field indexes outside the allowed range for that chunk THEN the Repair_Prompt SHALL specify the valid field-index range
4. WHEN validation-aware retries are exhausted (configurable, default 2 repair attempts beyond the initial try) THEN the failed chunk SHALL be recorded with structured error metadata including the last parse/validation error
5. WHEN a repair retry succeeds THEN the final result SHALL contain only the repaired valid output, not the original malformed response
6. WHEN a unit test provides an intentionally malformed JSON response followed by a valid response THEN the retry loop SHALL recover successfully

---

### Requirement 6: Safe Logging of Model Responses

**User Story:** As a maintainer operating the pipeline, I want logs to expose bounded debugging information without leaking full prompts or large article excerpts.

#### Acceptance Criteria

1. WHEN validation fails THEN logs SHALL include a response preview truncated to a configurable character limit (default 500 chars) at WARNING level
2. WHEN a raw response is truncated in logs THEN the log message SHALL include a SHA-256 hash of the full response for correlation with debug artifacts
3. WHEN `log_level` is set to DEBUG and a debug-artifact directory is configured THEN the full raw response SHALL be written to a file named `{pdf_name}_chunk{n}_{hash[:12]}.raw.txt` in that directory
4. WHEN no debug-artifact directory is configured THEN full raw responses SHALL NOT be written to disk or logs regardless of log level
5. WHEN a configurable `max_log_response_chars` key is added to config THEN it SHALL default to 500 and be respected by all model-response log statements

---

### Requirement 7: Content-Hash Evidence Caching

**User Story:** As a user rerunning the pipeline, I want evidence cache invalidation to depend on PDF file contents, so that stale evidence is never reused after a PDF is replaced.

#### Acceptance Criteria

1. WHEN evidence is cached in `evidence_index.py` THEN the cache key SHALL include a SHA-256 hash of the PDF file bytes (matching the pattern already used by the GROBID TEI cache)
2. WHEN a PDF changes content but keeps the same filename, file size, or modified time THEN the evidence cache SHALL miss
3. WHEN the stat-based fast hash is retained as an optimization THEN it SHALL be used only as a first-pass filter; a cache hit SHALL be confirmed by verifying the SHA-256 matches the stored hash
4. WHEN a unit test replaces a file with same-size different content THEN the test SHALL assert that the cache returns a miss
5. WHEN the cache key is computed THEN it SHALL also include the `extraction_map` schema version (hash of `configs/extraction_map.json`) so that schema changes invalidate cached evidence

---

### Requirement 8: Publication-Year Multi-Source Resolver

**User Story:** As an extraction reviewer, I want publication year to be resolved from multiple reliable sources, so that obvious years are not returned as `nr` when TEI metadata is incomplete.

#### Acceptance Criteria

1. WHEN TEI metadata includes a publication year THEN that value SHALL be used with confidence `h`
2. WHEN TEI metadata lacks a year THEN the resolver SHALL check, in order: (a) PDF document metadata (`/CreationDate`, `/ModDate`), (b) first-page bibliographic text (regex for 4-digit year near author names), (c) filename patterns (e.g., `Shahn_2015.pdf` → 2015)
3. WHEN the year is inferred from filename only THEN confidence SHALL be `m` unless corroborated by another source
4. WHEN year remains unavailable from all sources THEN the system SHALL return `nr` with no fabricated value
5. WHEN a year value is resolved THEN its provenance SHALL be recorded in a `year_provenance` field (e.g., `"tei_header"`, `"pdf_metadata"`, `"filename_pattern"`) available in debug output
6. WHEN tested with a filename `Shahn_2015.pdf` and no TEI year THEN the resolver SHALL return `2015` with confidence `m` and provenance `"filename_pattern"`

---

### Requirement 9: Normalized Annotation Schema

**User Story:** As a downstream component, I want all entity and annotation records to share one uniform schema, so that heuristic and service-derived annotations can be consumed uniformly.

#### Acceptance Criteria

1. WHEN heuristic annotations are generated (e.g., from regex or rule-based detection) THEN they SHALL use the same object shape as service-derived annotations
2. WHEN any annotation is stored THEN it SHALL include at minimum: `text` (str), `type` (str), `source` (str identifying the producer), and optional `confidence` (float 0.0–1.0 or null)
3. WHEN a service returns extra metadata beyond the base fields THEN it SHALL be stored under a `metadata` dict field within the annotation object
4. WHEN annotation lists are serialized THEN they SHALL NOT contain mixed string/object entries — every element SHALL be a dict conforming to the annotation schema
5. WHEN W3C JSON-LD annotations are produced by `w3c_annotation.py` THEN they SHALL remain unchanged; the normalized annotation schema applies to internal pipeline annotations only, not the W3C output artifact
6. WHEN a unit test creates annotations from two different sources (heuristic + service) THEN both SHALL validate against the same schema

---

### Requirement 10: Deterministic Dependency Management

**User Story:** As a deployer, I want dependencies installed before runtime, so that the pipeline does not execute package installation commands during extraction.

#### Acceptance Criteria

1. WHEN `src/text_processing/base.py` encounters a missing scispaCy model THEN it SHALL raise `ImportError` with a clear message including the install command, NOT execute `subprocess.run(["pip", "install", ...])` at runtime
2. WHEN any extraction module is imported THEN it SHALL NOT run `pip install`, `subprocess.run` with pip, or any equivalent package installation command
3. WHEN optional dependencies are documented THEN `pyproject.toml` SHALL define install extras (e.g., `[project.optional-dependencies]` with groups like `ocr`, `nlp`, `semantic`) listing the optional packages
4. WHEN a CI test scans all `.py` files under `src/` THEN it SHALL fail if any file contains a `subprocess.run` or `subprocess.call` invocation with `pip` or `install` in its arguments (excluding test files)

---

### Requirement 11: Atomic Output Writes

**User Story:** As a user running long extraction jobs, I want output files to be written atomically, so that interrupted runs do not leave partial JSON files.

#### Acceptance Criteria

1. WHEN `_save_pdf_output()` writes final JSON THEN it SHALL first write to a temporary file in the same directory (e.g., `{pdf_name}.extracted.json.tmp`)
2. WHEN the temporary file is complete and valid JSON THEN it SHALL be atomically renamed to the final output path using `os.replace()`
3. WHEN a write fails before rename THEN the final output path SHALL remain absent or contain the previous complete file; the `.tmp` file SHALL be cleaned up on next run
4. WHEN `save_manifest()` writes the manifest THEN it SHALL use the same atomic write pattern (temp file + rename)
5. WHEN resume logic checks for an existing output file THEN it SHALL verify that the file parses as valid JSON before treating it as complete; if parsing fails, the file SHALL be treated as absent

---

### Requirement 12: Manifest Identity and Resumability

**User Story:** As a user rerunning the pipeline with changed inputs or config, I want the manifest to distinguish run identity, so that stale statuses do not affect new runs.

#### Acceptance Criteria

1. WHEN a manifest entry is created for a PDF THEN it SHALL include: `pdf_content_hash` (SHA-256 of PDF bytes), `config_hash` (SHA-256 of serialized relevant config sections), `extraction_map_hash` (SHA-256 of `configs/extraction_map.json`), `model_id` (chunk model name), `schema_version` (a version string), and `output_path` (relative path to the output file)
2. WHEN any identity component changes between runs THEN the manifest SHALL treat the PDF as requiring re-processing (status reset)
3. WHEN a completed manifest entry points to a missing or invalid output file THEN it SHALL NOT be considered complete; the PDF SHALL be re-processed
4. WHEN the manifest is loaded THEN entries with mismatched identity fields SHALL be logged at INFO level and treated as stale
5. WHEN a unit test changes the config hash for a previously-completed PDF THEN the test SHALL assert that the PDF is re-processed

---

### Requirement 13: PyMuPDF Text Spacing Preservation

**User Story:** As an extraction user, I want PyMuPDF text extraction to preserve word boundaries, so that fallback text does not concatenate clinical terms.

#### Acceptance Criteria

1. WHEN PyMuPDF spans within a line are joined THEN word boundaries SHALL be preserved by inserting a space between adjacent spans whose bounding boxes have a horizontal gap exceeding a threshold (default: 1/4 of the average character width in the preceding span)
2. WHEN two spans are immediately adjacent with no gap (e.g., bold + normal within one word) THEN they SHALL be joined without a space
3. WHEN whitespace normalization runs on joined text THEN it SHALL NOT merge adjacent alphanumeric tokens that were separated by the span-joining logic
4. WHEN a unit test provides two adjacent spans with text `"cardio"` and `"vascular"` from the same word (zero gap) THEN joined text SHALL be `"cardiovascular"`
5. WHEN a unit test provides two spans with text `"heart"` and `"failure"` from separate words (gap > threshold) THEN joined text SHALL be `"heart failure"`

---

### Requirement 14: CI Coverage for Extraction Modes

**User Story:** As a maintainer, I want CI to cover native, scanned, and mixed PDF workflows with integration-level tests, so that routing and reconciliation regressions are caught early.

#### Acceptance Criteria

1. WHEN CI runs extraction mode tests THEN it SHALL include a test exercising the native-text path through `build_qc_bundle()` with mocked GROBID and pdfplumber returning valid content
2. WHEN CI runs extraction mode tests THEN it SHALL include a test exercising the OCR path through `build_qc_bundle()` with mocked PaddleOCR and PyMuPDF returning valid content, asserting that GROBID is NOT called
3. WHEN CI runs extraction mode tests THEN it SHALL include a test exercising the mixed path through `build_qc_bundle()` with a fixture where page 1 is native and page 2 is scanned, asserting that both native and OCR extractors are invoked
4. WHEN external services (GROBID, PaddleOCR) are unavailable THEN tests SHALL use mocks and not hang on network timeouts
5. WHEN these tests are added THEN they SHALL live at `tests/src/pipeline/test_extraction_modes.py` and be collected by the default pytest run (not marked slow)

---

## Prioritization

### P0: Correctness Blockers (Milestone 1)
- Requirement 1: Final output schema contract validation
- Requirement 2: OCR configuration propagation — DPI wiring
- Requirement 3: True per-page extraction routing
- Requirement 4: Extractor branch quality-based reconciliation
- Requirement 5: Validation-aware LLM retries
- Requirement 7: Content-hash evidence caching

### P1: Evidence Quality and Provenance (Milestone 2)
- Requirement 8: Publication-year multi-source resolver
- Requirement 9: Normalized annotation schema
- Requirement 13: PyMuPDF text spacing preservation

### P2: Runtime Reliability and Maintainability (Milestone 3)
- Requirement 6: Safe logging of model responses
- Requirement 10: Deterministic dependency management
- Requirement 11: Atomic output writes
- Requirement 12: Manifest identity and resumability
- Requirement 14: CI coverage for extraction modes

---

## Success Metrics

- Final extraction JSON validates against `configs/final_output_schema.json` in 100% of successful runs
- OCR DPI configuration is propagated and testable
- Mixed PDFs are routed page-by-page with correct page ordering preserved
- Failed or empty extractor branches cannot be selected as primary when another branch has valid content
- Malformed LLM chunk responses are repaired automatically when repairable (at least 1 parse + 1 schema failure recovered in tests)
- Evidence cache invalidates when PDF content changes, regardless of filename or mtime
- Publication year resolves from filename when TEI is absent
- No runtime `pip install` calls exist in source modules
- Output writes are atomic; interrupted runs leave no partial JSON
- Manifest entries include identity hashes; config changes trigger re-processing
- PyMuPDF span joining preserves word boundaries
- CI includes native, scanned, and mixed extraction mode integration tests
