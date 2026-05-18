# Implementation Plan: Audit Remediation

## Overview

This plan implements 14 audit remediation gaps across three priority milestones. Each task builds incrementally on previous work, starting with core schema/validation infrastructure (P0), then evidence quality improvements (P1), and finally runtime reliability hardening (P2). All code is Python 3.10+ following existing EviTrace conventions.

## Tasks

- [ ] 1. P0 — Final Output Schema Validation (Req 1)
  - [ ] 1.1 Create `configs/final_output_schema.json` and `FinalOutputValidator` class
    - Create the JSON Schema (Draft 7) file at `configs/final_output_schema.json` defining the array of field records with required keys: `field_index`, `domain_group`, `field_name`, `extracted_value`, `evidence`, `location`, `location_metadata`, `confidence`
    - Extend `src/pipeline/validator.py` with a `FinalOutputValidator` class that loads the schema once and exposes `validate(fields) -> ValidationResult` and `format_error(error) -> str` methods
    - `format_error` must include `field_index`, `field_name` (if present), and JSON path in the error message
    - _Requirements: 1.1, 1.2, 1.5_

  - [ ] 1.2 Integrate `FinalOutputValidator` into `_save_pdf_output()` gate
    - Modify `src/pipeline/pdf_processor.py` `_save_pdf_output()` to call `FinalOutputValidator.validate()` before writing
    - On validation failure: set manifest status to `"failed_schema_validation"`, log structured errors, and return without writing the output file
    - Ensure `location_metadata` cross-reference check (each metadata item's `id` exists in the field's `location` list or equals `"unresolved"`)
    - _Requirements: 1.1, 1.3_

  - [ ]* 1.3 Write property tests for schema validation (Properties 1, 2, 3)
    - **Property 1: Schema validation gates output writes** — generate random field lists (valid + invalid), assert output file exists iff validation passes, manifest status is `"failed_schema_validation"` on failure
    - **Property 2: Validation errors include field identifiers** — generate invalid field dicts with known indexes, assert error messages contain `field_index` and JSON path
    - **Property 3: Location metadata cross-reference integrity** — generate field records with location/metadata, assert every metadata `id` exists in `location` or equals `"unresolved"`
    - Test file: `tests/src/pipeline/test_final_output_validator_properties.py`
    - **Validates: Requirements 1.1, 1.2, 1.3**

  - [ ]* 1.4 Write unit tests for `FinalOutputValidator`
    - Test representative compact chunk output and final extraction document against schemas
    - Test that `configs/final_output_schema.json` exists and loads correctly
    - Test error formatting includes field identifiers
    - Test file: `tests/src/pipeline/test_final_output_validator.py`
    - _Requirements: 1.4, 1.5_

- [ ] 2. P0 — DPI Configuration Propagation (Req 2)
  - [ ] 2.1 Wire DPI from config to `extract_with_paddleocr()` in `build_qc_bundle()`
    - Modify `src/pipeline/extraction_pipeline.py` `build_qc_bundle()` to read `qc_config["quality_control"]["ocr"]["rasterization_dpi"]` and pass it explicitly to `extract_with_paddleocr(pdf_path, dpi=dpi_value)`
    - Add deprecation warning in `load_qc_config()` when legacy top-level `ocr_dpi` key exists alongside `quality_control.ocr`
    - _Requirements: 2.1, 2.2, 2.3_

  - [ ]* 2.2 Write property test for DPI propagation (Property 4)
    - **Property 4: DPI propagation end-to-end** — generate random DPI values (72–600), mock `extract_with_paddleocr`, assert the DPI parameter received equals the configured value
    - Test file: `tests/src/pipeline/test_dpi_propagation_properties.py`
    - **Validates: Requirements 2.1, 2.2**

  - [ ]* 2.3 Write unit test for DPI wiring and deprecation warning
    - Test that non-default DPI value is passed through to OCR extractor
    - Test deprecation warning emitted for legacy `ocr_dpi` key
    - Test file: `tests/src/pipeline/test_dpi_propagation.py`
    - _Requirements: 2.4_

- [ ] 3. P0 — Per-Page Extraction Routing (Req 3)
  - [ ] 3.1 Implement `PageRoutingResult` dataclass and per-page routing logic
    - Add `PageRoutingResult` dataclass to `src/pipeline/extraction_pipeline.py`
    - Refactor `build_qc_bundle()` to classify pages individually, partition into native/scanned sets, route native pages through GROBID+pdfplumber and scanned pages through PaddleOCR+PyMuPDF
    - Filter GROBID output to native page indices only (GROBID processes full document)
    - Merge page-level results in original page order
    - Attach `PageRoutingResult` list to `QCBundle` via `ctx.unified.content["page_routing"]`
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 3.2 Write property tests for per-page routing (Properties 5, 6)
    - **Property 5: Per-page routing correctness** — generate random page classification sequences, assert native pages routed to GROBID+pdfplumber, scanned to PaddleOCR+PyMuPDF, merged in original order, OCR not invoked when all native
    - **Property 6: Routing metadata completeness** — generate random routing results, assert every page has `page_index`, `selected_extractor`, `routing_reason` (non-empty)
    - Test file: `tests/src/pipeline/test_page_routing_properties.py`
    - **Validates: Requirements 3.1, 3.2, 3.3**

- [ ] 4. P0 — Quality-Based Branch Reconciliation (Req 4)
  - [ ] 4.1 Implement `BranchQualityScore` and quality-based primary selection
    - Add `BranchQualityScore` dataclass to `src/quality_control/adjudicator.py` with composite scoring (has_content, page_coverage, section_structure, text_length_plausible, weird_char_ratio, agreement_score)
    - Implement selection logic: score branches, sort by composite descending, failed/empty branches score 0
    - When `discard_failed_branches=true`, exclude failed branches from candidate set entirely
    - Record rationale in `AdjudicationDecision.rationale` when non-GROBID branch selected
    - _Requirements: 4.1, 4.2, 4.3, 4.4_

  - [ ]* 4.2 Write property tests for branch reconciliation (Properties 7, 8)
    - **Property 7: Failed branches never selected as primary** — generate random branch sets with at least one content-producing branch, assert failed/empty branches never selected as primary
    - **Property 8: Discard-failed-branches exclusion** — generate branch sets with failures and `discard_failed_branches=true`, assert failed branches excluded from candidate set
    - Test file: `tests/src/quality_control/test_reconciliation_properties.py`
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [ ]* 4.3 Write unit test for branch selection with empty GROBID
    - Test that when GROBID branch is empty and pdfplumber has valid content, pdfplumber is selected as primary
    - Test file: `tests/src/quality_control/test_quality_control_adjudicator.py` (extend existing)
    - _Requirements: 4.5_

- [ ] 5. P0 — Validation-Aware LLM Retries (Req 5)
  - [ ] 5.1 Implement `RepairRetryLoop` class in `pdf_processor.py`
    - Create `RepairRetryLoop` class in `src/pipeline/pdf_processor.py` with `extract_with_repair()` async method and `_build_repair_prompt()` helper
    - For JSON parse errors: include error message + required Compact_Schema format in repair prompt
    - For schema validation errors: list specific failures (missing keys, invalid confidence, out-of-range indexes)
    - For out-of-range field indexes: specify valid range `[min, max]`
    - On exhaustion after `max_repair_attempts` (default 2): record structured error metadata `{"status": "failed_validation", "chunk": n, "last_error": str, "error_type": "parse"|"schema", "attempts": int}`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ] 5.2 Integrate `RepairRetryLoop` into chunk extraction flow
    - Replace direct `extract_chunk` calls with `RepairRetryLoop.extract_with_repair()` in the chunk processing loop
    - Ensure successful repair returns only the repaired valid output (not original malformed response)
    - Update manifest with `"failed_chunks"` status when repair exhaustion occurs
    - _Requirements: 5.4, 5.5_

  - [ ]* 5.3 Write property tests for repair retry (Properties 9, 10)
    - **Property 9: Repair prompt includes error context** — generate malformed JSON strings, assert repair prompt contains parse error message and Compact_Schema; generate JSON that fails schema validation, assert repair prompt lists specific failures
    - **Property 10: Successful repair yields only valid output** — generate (malformed, valid) pairs, assert final result contains only the valid repaired output
    - Test file: `tests/src/pipeline/test_repair_retry_properties.py`
    - **Validates: Requirements 5.1, 5.2, 5.5**

  - [ ]* 5.4 Write unit tests for repair retry loop
    - Test exhaustion metadata structure
    - Test recovery from intentionally malformed JSON followed by valid response
    - Test file: `tests/src/pipeline/test_repair_retry.py`
    - _Requirements: 5.4, 5.6_

- [ ] 6. P0 — Content-Hash Evidence Caching (Req 7)
  - [ ] 6.1 Replace stat-based `_pdf_hash()` with SHA-256 content hash
    - Modify `src/pipeline/evidence_index.py` to replace `_pdf_hash()` with SHA-256 of file bytes
    - Update cache key to `f"{paper_id}_{pdf_sha256}_{extraction_map_hash}"` where `extraction_map_hash` is SHA-256 of `configs/extraction_map.json` computed once at module load
    - Optionally retain stat-based check as fast-path filter (verify SHA-256 before returning cached data on stat match)
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [ ]* 6.2 Write property test for content-hash cache invalidation (Property 12)
    - **Property 12: Content-hash cache invalidation** — generate random file content pairs, assert cache key includes SHA-256 of file bytes and extraction_map hash, assert content change causes cache miss regardless of filename/size/mtime
    - Test file: `tests/src/pipeline/test_evidence_cache_properties.py`
    - **Validates: Requirements 7.1, 7.2, 7.5**

  - [ ]* 6.3 Write unit test for same-size different-content cache miss
    - Test that replacing a file with same-size different content returns a cache miss
    - Test file: `tests/src/pipeline/test_evidence_cache.py`
    - _Requirements: 7.4_

- [ ] 7. Checkpoint — P0 Milestone
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. P1 — Publication-Year Multi-Source Resolver (Req 8)
  - [ ] 8.1 Implement `resolve_publication_year()` function
    - Add `YearResolution` dataclass and `resolve_publication_year()` function to `src/pipeline/evidence_index.py`
    - Implement priority chain: TEI metadata → PDF document metadata → first-page bibliographic text → filename pattern → `nr`
    - Implement corroboration logic: if filename year matches another source, upgrade confidence to `h`
    - Record provenance in `year_provenance` field
    - Integrate into `_build_items_from_tei` to populate year fields in evidence items
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 8.2 Write property test for year resolution (Property 13)
    - **Property 13: Year resolution priority and provenance** — generate random TEI/filename combinations, assert TEI year used with confidence `h`, filename-only yields confidence `m`, every resolved year has non-empty provenance
    - Test file: `tests/src/pipeline/test_year_resolver_properties.py`
    - **Validates: Requirements 8.2, 8.3, 8.5**

  - [ ]* 8.3 Write unit tests for year resolver
    - Test TEI year extraction with confidence `h`
    - Test filename pattern `Shahn_2015.pdf` → year `2015`, confidence `m`, provenance `"filename_pattern"`
    - Test `nr` returned when no sources available
    - Test file: `tests/src/pipeline/test_year_resolver.py`
    - _Requirements: 8.1, 8.6_

- [ ] 9. P1 — Normalized Annotation Schema (Req 9)
  - [ ] 9.1 Define `NormalizedAnnotation` schema and migrate heuristic annotations
    - Define `NormalizedAnnotation` TypedDict in `src/pipeline/evidence_index.py` with fields: `text`, `type`, `source`, `confidence`, `metadata`
    - Migrate heuristic annotations from plain strings to `NormalizedAnnotation` dicts
    - Normalize service-derived annotations (varying shapes) to the same schema with extra fields under `metadata`
    - Ensure W3C annotations in `w3c_annotation.py` remain unchanged
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ]* 9.2 Write property test for annotation uniformity (Property 14)
    - **Property 14: Annotation schema uniformity** — generate random annotations from heuristic and service sources, assert every annotation is a dict with `text` (str), `type` (str), `source` (str), optional `confidence` (float in [0.0, 1.0] or null), extra metadata under `metadata` key, no mixed string/dict entries in annotation lists
    - Test file: `tests/src/pipeline/test_annotation_schema_properties.py`
    - **Validates: Requirements 9.1, 9.2, 9.3, 9.4**

  - [ ]* 9.3 Write unit tests for annotation schema
    - Test that W3C annotations remain unchanged
    - Test that annotations from two different sources (heuristic + service) both validate against the same schema
    - Test file: `tests/src/pipeline/test_annotation_schema.py`
    - _Requirements: 9.5, 9.6_

- [ ] 10. P1 — PyMuPDF Text Spacing Preservation (Req 13)
  - [ ] 10.1 Implement gap-aware span joining in `extract_with_pymupdf()`
    - Add `_should_insert_space(prev_span, curr_span) -> bool` function to `src/pdf_extractor/extraction/PyMuPDF.py`
    - Threshold: space inserted when horizontal gap > 1/4 of average character width in preceding span
    - Replace `"".join(block_spans_text)` with gap-aware joining in the inner loop of `extract_with_pymupdf()`
    - Zero-gap spans joined without space; gap > threshold inserts space
    - _Requirements: 13.1, 13.2, 13.3_

  - [ ]* 10.2 Write property test for span joining (Property 18)
    - **Property 18: Span joining preserves word boundaries** — generate random span pairs with bboxes, assert space inserted iff gap > 1/4 avg char width, zero-gap spans joined without space
    - Test file: `tests/src/pdf_extractor/test_pymupdf_spacing_properties.py`
    - **Validates: Requirements 13.1, 13.2**

  - [ ]* 10.3 Write unit tests for span joining
    - Test `"cardio"` + `"vascular"` (zero gap) → `"cardiovascular"`
    - Test `"heart"` + `"failure"` (gap > threshold) → `"heart failure"`
    - Test file: `tests/src/pdf_extractor/test_pymupdf_spacing.py`
    - _Requirements: 13.4, 13.5_

- [ ] 11. Checkpoint — P1 Milestone
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. P2 — Safe Logging of Model Responses (Req 6)
  - [ ] 12.1 Implement `log_model_response()` utility
    - Create `src/utils/logging_utils.py` helper function `log_model_response()` (extend existing module)
    - Always: log truncated preview (max `max_log_response_chars`, default 500) + SHA-256 hash at WARNING
    - If `debug_artifact_dir` configured AND `log_level=DEBUG`: write full response to `{pdf_name}_chunk{n}_{hash[:12]}.raw.txt`
    - If no artifact dir: never write full responses regardless of level
    - Add `max_log_response_chars` config key to the `retry` section (default 500)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ] 12.2 Integrate safe logging into `pdf_processor.py`
    - Replace existing raw response logging in `src/pipeline/pdf_processor.py` with calls to `log_model_response()`
    - Ensure no full model responses appear in log output when debug artifact dir is not configured
    - _Requirements: 6.1, 6.4_

  - [ ]* 12.3 Write property test for log truncation (Property 11)
    - **Property 11: Log truncation with hash correlation** — generate random response strings exceeding `max_log_response_chars`, assert WARNING log truncated to limit AND includes SHA-256 hash, assert full response never in log output when no artifact dir configured
    - Test file: `tests/src/utils/test_safe_logging_properties.py`
    - **Validates: Requirements 6.1, 6.2, 6.4**

  - [ ]* 12.4 Write unit tests for safe logging
    - Test debug artifact file writing when dir configured and DEBUG level
    - Test config default of 500 chars
    - Test file: `tests/src/utils/test_safe_logging.py`
    - _Requirements: 6.3, 6.5_

- [ ] 13. P2 — Deterministic Dependency Management (Req 10)
  - [ ] 13.1 Remove runtime pip install and add `pyproject.toml` extras
    - Modify `src/text_processing/base.py` `ScispaCySentenceSegment._load_model()` to raise `ImportError` with clear install instructions instead of calling `subprocess.run(["pip", "install", ...])`
    - Add optional dependency groups to `pyproject.toml`: `ocr`, `nlp`, `semantic`
    - _Requirements: 10.1, 10.2, 10.3_

  - [ ]* 13.2 Write CI guard test for no runtime pip
    - Create `tests/test_no_runtime_pip.py` that AST-scans all `.py` files under `src/` for `subprocess.run` or `subprocess.call` with `pip` or `install` in arguments (excluding test files)
    - Test file: `tests/test_no_runtime_pip.py`
    - _Requirements: 10.4_

- [ ] 14. P2 — Atomic Output Writes (Req 11)
  - [ ] 14.1 Implement `_atomic_write_json()` helper and integrate
    - Add `_atomic_write_json(path, data, indent)` function to `src/pipeline/pdf_processor.py`
    - Uses temp file + `os.replace()` pattern with cleanup in `finally` block
    - Integrate into `_save_pdf_output()` and `save_manifest()` in `src/pipeline/manifest.py`
    - Add resume validation: `_load_completed_result()` wraps `json.load()` in try/except, returns `None` on parse failure (triggering re-processing)
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [ ]* 14.2 Write property test for atomic writes (Property 15)
    - **Property 15: Atomic write integrity** — generate random JSON data, simulate write failures before rename, assert final path either doesn't exist or contains previous valid content (never partial write)
    - Test file: `tests/src/pipeline/test_atomic_write_properties.py`
    - **Validates: Requirements 11.1, 11.2, 11.4**

  - [ ]* 14.3 Write property test for resume validation (Property 16)
    - **Property 16: Resume validates output file integrity** — generate valid/corrupt output files, assert corrupt files treated as absent and PDF re-processed
    - Test file: `tests/src/pipeline/test_manifest_resume_properties.py`
    - **Validates: Requirements 11.5, 12.3**

- [ ] 15. P2 — Manifest Identity and Resumability (Req 12)
  - [ ] 15.1 Implement `ManifestIdentity` and staleness detection
    - Add `ManifestIdentity` dataclass and `compute_identity()` function to `src/pipeline/manifest.py`
    - Add `is_stale(entry, current_identity) -> bool` function
    - Each manifest entry gains identity fields: `pdf_content_hash`, `config_hash`, `extraction_map_hash`, `model_id`, `schema_version`, `output_path`
    - On load: entries with mismatched identity logged at INFO and treated as stale (status reset)
    - Entries pointing to missing/invalid output files treated as incomplete
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

  - [ ]* 15.2 Write property test for manifest identity (Property 17)
    - **Property 17: Manifest identity invalidation** — generate identity field variations, assert any changed component causes entry to be treated as stale and PDF re-processed
    - Test file: `tests/src/pipeline/test_manifest_identity_properties.py`
    - **Validates: Requirements 12.1, 12.2**

  - [ ]* 15.3 Write unit test for manifest identity staleness
    - Test that changing config hash for a previously-completed PDF triggers re-processing
    - Test file: `tests/src/pipeline/test_manifest_identity.py`
    - _Requirements: 12.5_

- [ ] 16. P2 — CI Extraction Mode Tests (Req 14)
  - [ ] 16.1 Create integration tests for native, OCR, and mixed extraction paths
    - Create `tests/src/pipeline/test_extraction_modes.py` with three integration tests:
      1. Native path: mock GROBID + pdfplumber returning valid content, assert OCR not called
      2. OCR path: mock scan_detector to flag all pages scanned, mock PaddleOCR + PyMuPDF, assert GROBID not called
      3. Mixed path: mock scan_detector with page 1 native + page 2 scanned, assert both native and OCR extractors invoked
    - All tests use `unittest.mock.patch` for external services
    - Tests NOT marked `@pytest.mark.slow` — collected by default pytest run
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

- [ ] 17. Final Checkpoint — P2 Milestone
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at each milestone boundary
- Property tests validate universal correctness properties defined in the design (Hypothesis, min 100 examples)
- Unit tests validate specific examples and edge cases
- All tests follow existing EviTrace conventions: `tests/src/` mirrors `src/` layout, Hypothesis for PBT, mocked heavy dependencies
- The implementation language is Python 3.10+ matching the existing codebase and design document

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "13.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "2.2", "2.3", "6.1"] },
    { "id": 2, "tasks": ["3.1", "6.2", "6.3", "13.2"] },
    { "id": 3, "tasks": ["3.2", "4.1", "5.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "5.2"] },
    { "id": 5, "tasks": ["5.3", "5.4"] },
    { "id": 6, "tasks": ["8.1", "9.1", "10.1"] },
    { "id": 7, "tasks": ["8.2", "8.3", "9.2", "9.3", "10.2", "10.3"] },
    { "id": 8, "tasks": ["12.1", "14.1"] },
    { "id": 9, "tasks": ["12.2", "12.3", "12.4", "15.1"] },
    { "id": 10, "tasks": ["14.2", "14.3", "15.2", "15.3"] },
    { "id": 11, "tasks": ["16.1"] }
  ]
}
```
