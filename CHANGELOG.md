# Changelog

All significant changes to the repository are recorded here. This file is permanent
and should never be deleted. Add a brief entry whenever a spec is implemented,
steering docs change, README files change, or any other significant code change
occurs.

Format: `## [date] — title` followed by a short description of what changed
and why. No need to be exhaustive — just enough for a future reader to
understand what happened and where to look.

---

## [2026-05] — Migration Artifact Scrub (`bugfix/migration-artifact-scrub`)

Removed all artifacts from the old waterfall-cascade architecture. The
codebase now fully reflects the current scan-detector routing architecture
described in `product.md`.

**Dead code removed:**
- `_run_legacy_pipeline`, `_run_legacy_annotation_path`, `_derive_document_id`
  from `quality_control/quality_control.py`
- `PLACEHOLDER_NOTICE` constant and placeholder reconciliation path from
  `quality_control/reconciler.py`
- `load_config` backward-compat alias from `utils/config_utils.py`
- `ocr_text_quality_threshold` from `_LOCAL_DEFAULTS` and `load_local_config`
- Unused `extract_with_pymupdf` import and `font_metadata = []` assignment
  from `pdf_extractor/pdf_extractor.py`

**Renamed to extractor-agnostic names:**
- `iaa_calculator.investigate()` parameters: `grobid_observation` →
  `primary_observation`, `pymupdf_observation` → `secondary_observation`,
  `grobid_artifact` → `primary_artifact`, `pymupdf_artifact` →
  `secondary_artifact`; return keys updated to match
- `reconciler._build_provenance_dict` keys: `grobid_artifact_id` →
  `primary_artifact_id`, `pymupdf_artifact_id` → `secondary_artifact_id`,
  `grobid_observation` → `primary_observation`, `pymupdf_observation` →
  `secondary_observation`
- `rater.observe()` refactored from `observe(extractor_name, canonical_artifact,
  document_id, config)` to `observe(branch: BranchOutput, config: dict) ->
  QualityReport`
- `metrics_hierarchy` keys: `"tier1"` → `"local_metrics"`, `"tier2"` →
  `"exact_match"`, `"tier3"` → `"semantic_match"`
- `_build_tier1_report` → `_build_local_metrics_report`

**Content dict cleaned up (removed keys):** `observer_summary`,
`investigator_summary`, `geometry`, `adjudication_status`, `placeholder_notice`

**Test files renamed:** `test_text_extractor_tier1.py` →
`test_pdfplumber_backend.py`, `test_text_extractor_tier2.py` →
`test_pymupdf_backend.py`, `test_text_extractor_tier3.py` →
`test_paddleocr_backend.py`, `test_text_extractor_branch2.py` →
`test_pymupdf_schema.py`, `test_sentence_processor_task61.py` →
`test_sentence_processor.py`

**Stale files deleted:** All 6 files in `tests/steering/` (migration-era
steering drift tests); spec directories `bugfix/extraction-routing-alignment`,
`bugfix/test-suite-bugfix`, `bugfix/test-coverage`

**Docstrings cleaned:** Removed all `Design reference:`, `Requirements:`,
`Boundary:`, and `Task N.N` cross-references from production module docstrings
in `utils/text_processor.py`, `pdf_extractor/extraction/scan_detector.py`,
`quality_control/concerns/`, `quality_control/reconciler.py`,
`quality_control/quality_control.py`

**Documentation rewritten:** `pdf_extractor/extraction/README.md` replaced
entirely to describe current scan-detector routing architecture; root
`README.md` updated to remove cascade/tier/Tesseract references

---

## [2026-04] — Extraction Routing Alignment (`bugfix/extraction-routing-alignment`)

Fixed `pipeline/orchestrator._build_qc_context` which bypassed the per-page
routing architecture. It was calling GROBID and PyMuPDF directly without
running `scan_detector.classify_page()`, using PyMuPDF as the structural QC
branch (a role belonging to pdfplumber), and never invoking PaddleOCR for
scanned pages.

**What changed:**
- `_build_qc_context` now runs `scan_detector.classify_page()` on every page
  before invoking any extraction backend
- Native pages → `BranchOutput(extractor="grobid")` + `BranchOutput(extractor="pdfplumber")`
- Scanned pages with `ocr: true` → `BranchOutput(extractor="paddleocr")` +
  `BranchOutput(extractor="pymupdf")` (built-in OCR cross-validator)
- PyMuPDF font metadata stored outside `branches` as comparison signals
- `extract_pdf()` removed from `pdf_extractor/extraction/__init__.py` (dead
  legacy cascade function)
- `extract_with_pymupdf` re-exported from `pdf_extractor.extraction` so patch
  targets resolve correctly in tests

---

## [2026-04] — Test Suite Bug Fix (`bugfix/test-suite-bugfix`)

Fixed 4 collection errors and 51 test failures caused by stale import paths
and outdated test logic left over from the architecture migration.

**What changed:**
- Fixed stale imports in `test_logging_utils.py`, `test_parser_pipeline.py`,
  `test_quality_control_artifact_generator.py`; deleted `test_metrics_hierarchy.py`
  (tested a function that no longer exists)
- Rewrote `test_text_extractor_orchestrator.py` to test scan-detector routing
  instead of the old waterfall cascade
- Added `autouse` scispaCy/spaCy mock fixtures to all test files that
  construct `TextProcessor` directly or indirectly
- Fixed `pdf2image` mock setup in PaddleOCR coordinate tests
- Added missing `MockFaiss` class to `test_embedding_utils.py`
- Fixed `test_reconciler_call_is_strategy_driven_and_extractor_agnostic` to
  use a real `SemanticLayer` instead of a `MagicMock` for the reconciler
  return value
- Fixed `test_no_tesseract_references` grep exclusion to skip test files

---

## [2026-03] — Test Coverage for Pipeline Modules (`bugfix/test-coverage`)

Added seven new test files covering modules that previously had no dedicated
unit tests: `agents/openai/api_client.py`, `agents/openai/prompts.py`,
`pipeline/manifest.py`, `pipeline/extraction_map.py`,
`pipeline/extraction_report.py`, `pipeline/pdf_processor.py`,
`pipeline/orchestrator.py`.

**What was added:**
- Async retry logic tests for `_call_api_with_retries` (RateLimitError,
  APIStatusError, APIConnectionError, APITimeoutError, unexpected exceptions)
- `paper_cache_key` determinism and format PBT
- Prompt cache-stability tests: shared prefix invariant across warmup and
  extraction messages
- Manifest round-trip idempotency PBT
- ExtractionMap field grouping and domain-to-chunk assignment tests
- QC report flagged-row aggregation and CSV output tests
- PdfProcessor checkpointing, output persistence, and chunk orchestration tests
- Orchestrator PDF-level concurrency and GROBID failure-handling tests
- All tests mock external dependencies (OpenAI API, GROBID, file I/O) — no
  real credentials or services required
