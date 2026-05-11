# Changelog

All significant changes to the repository are recorded here. This file is permanent
and should never be deleted. Add a brief entry whenever a spec is implemented,
steering docs change, README files change, or any other significant code change
occurs.

Format: `## [date] — title` followed by a short description of what changed
and why. No need to be exhaustive — just enough for a future reader to
understand what happened and where to look.

## [2026-05] — Rename `BranchOutput` → `Candidate`; generalize fields

`BranchOutput` was renamed to `Candidate` to reflect that the QC pipeline is domain-agnostic — the contributor can be an extractor, an LLM agent, a human annotator, or anything else.

Field changes on `Candidate`:
- `extractor: str` → `source: str` (canonical field name)
- `branch: int` → `index: int`
- `.extractor` and `.agent` are now both read-only properties aliasing `source`

`QualityReport` in `quality_control/defaults/` updated to match: `extractor` → `source`, `branch` → `index`, with `.extractor` and `.agent` as aliases.

Updated across all call sites: `extraction_pipeline.py`, `pdf_extractor.py`, `quality_control/`, `pipeline/`, all tests, steering docs.

## [2026-05] — Rename `QCContext` → `QCBundle`

`QCContext` was renamed to `QCBundle` across the entire codebase. The new name better reflects what the object is — the bundled outputs of one QC pipeline run (branches, reports, IAA metrics, adjudication decision, unified record, metrics hierarchy) — rather than the vague "context" suffix.

- Renamed in: `quality_control/models.py`, `quality_control/quality_control.py`, `quality_control/__init__.py`
- Updated all call sites: `pipeline/orchestrator.py`, `pipeline/pdf_processor.py`, `pipeline/validator.py`
- Updated all test files under `tests/`
- Updated steering docs and READMEs

## [2026-05] — Extract concrete QC defaults to `quality_control/defaults/`; self-sufficient `pdf_extractor.py` CLI

**QC model split:** Moved the three concrete default implementations out of `quality_control/models.py` into a new `quality_control/defaults/` subpackage. `models.py` now contains only ABCs and pure data containers.

- `quality_control/defaults/quality_report.py` — `QualityReport` (default always-pass rater)
- `quality_control/defaults/inter_rater_report.py` — `InterRaterReport` (pairwise pass/fail IAA)
- `quality_control/defaults/adjudication_decision.py` — `AdjudicationDecision` (majority-vote election)
- `quality_control/__init__.py` re-exports all three for backwards compatibility
- Updated `quality_control/rater.py`, `quality_control/local_metrics.py`, `quality_control/quality_control.py` to import from `defaults/`

**Shared extraction pipeline:** Extracted `_build_qc_context` logic from `pipeline/orchestrator.py` into `pdf_extractor/extraction_pipeline.py` (`build_qc_bundle`). Both the orchestrator and the standalone CLI now share the same scan-detection → backend-routing → QC pipeline code.

**Self-sufficient `pdf_extractor.py` CLI:** Rewrote `pdf_extractor/pdf_extractor.py` to run the full multi-backend extraction pipeline (GROBID + pdfplumber for native pages; PaddleOCR + PyMuPDF for scanned pages) and write `UnifiedRecord`-based JSON artifacts. No OpenAI API key required.

## [2026-05] — Rename `AlignmentMapEntry` → `AlignmentRecord`, `AlignmentMap` → `DocumentAlignment`

The old names implied a subclass relationship that didn't exist. Renamed to clarify
that `AlignmentRecord` is a single provenance record and `DocumentAlignment` is the
document-level container that holds lists of them.

- `quality_control/models.py`: class renames
- `quality_control/__init__.py`: updated imports and `__all__`
- `quality_control/reconciler.py`, `quality_control/quality_control.py`, `quality_control/concerns/text_fidelity.py`: updated all references
- All affected test files updated accordingly
- `.kiro/steering/product.md`: data model table updated

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
  document_id, config)` to `observe(branch: Candidate, config: dict) ->
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
- Native pages → `Candidate(extractor="grobid")` + `Candidate(extractor="pdfplumber")`
- Scanned pages with `ocr: true` → `Candidate(extractor="paddleocr")` +
  `Candidate(extractor="pymupdf")` (built-in OCR cross-validator)
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
