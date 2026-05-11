# Implementation Plan: QC Migration (`qc-migration`)

## Overview

Reorganise the `quality_control/` package in three broad strokes: (1) rename legacy classes, sub-packages, metric names, and config keys to descriptive names; (2) add the new `quality_control/checks/` sub-package with four check modules and a stable `VerificationResult` dataclass; (3) update `run_quality_control`, `_QC_DEFAULTS`, `__init__.py`, and all import sites, then add the full test suite. Phase 2 (TextProcessor migration) must not begin until every task here is complete.

---

## Tasks

- [x] 1. Add `VerificationResult` dataclass to `quality_control/models.py`
  - [x] 1.1 Define `VerificationResult` dataclass with fields and `__post_init__` validation
    - Add `@dataclass` class with `_VALID_STATUSES` set and `ValueError` guards for invalid status and score outside `[0.0, 1.0]`
    - Ensure `models.py` does not define, export, or reference `semantic_qc`, `exact_match`, or `semantic_match`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [x] 1.2 Write property test for `VerificationResult` score range (Property 2)
    - **Property 2: VerificationResult score is constrained to [0.0, 1.0]**
    - **Validates: Requirements 2.4**
    - Use `@given(st.floats())` with `@settings(max_examples=100)` in `tests/quality_control/test_qc_verification_result.py`
    - Assert `ValueError` raised when `score < 0.0` or `score > 1.0`; assert construction succeeds when `0.0 <= score <= 1.0`

  - [x] 1.3 Write unit tests for `VerificationResult` field validation
    - Test all five valid status values succeed; test invalid status raises `ValueError`
    - Test all six standard evidence keys are present when produced by source-verification checks
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6_

- [x] 2. Create `quality_control/checks/` package skeleton
  - [x] 2.1 Create `quality_control/checks/__init__.py` exporting all four public names
    - Create directory `quality_control/checks/` and `__init__.py` that imports and re-exports `SourceTextPresenceCheck`, `SemanticSourceVerificationCheck`, `ExtractorAgreementCheck`, `build_task_quality_scaffold`
    - No top-level imports of `faiss`, `torch`, `sentence_transformers`, `spacy`, `scispacy`, `stanza`, `wtpsplit`, `TextProcessor`, or `text_processing`
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6_

- [x] 3. Implement `SourceTextPresenceCheck`
  - [x] 3.1 Write `quality_control/checks/source_text.py` with `SourceTextPresenceCheck` dataclass
    - `check_name: ClassVar[str] = "source_text_presence"`; `matcher: Callable` field
    - `run(needle, full_text, page_texts, blocks) -> VerificationResult`
    - When matcher returns non-`None` dict: `status="verified"`, `score=result.get("confidence", 1.0)` clamped to `[0.0, 1.0]`, evidence populated with six standard keys
    - When matcher returns `None`: `status="no_match"`, `score=0.0`, all six evidence keys set to `None`
    - No inline lexical matching logic; no import of `TextProcessor` or `text_processing`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

  - [x] 3.2 Write property test for `SourceTextPresenceCheck` output contract (Property 3)
    - **Property 3: SourceTextPresenceCheck output contract**
    - **Validates: Requirements 2.5, 2.6, 3.4, 3.6, 3.7, 3.8**
    - Use `st.text()`, `st.dictionaries()`, `st.lists()`, `st.one_of(st.none(), st.dictionaries(...))` in `tests/quality_control/test_qc_checks_source_text.py`
    - Assert matcher called with exactly `(needle, full_text, page_texts, blocks)`; assert status/score/evidence contract for both `None` and non-`None` returns

  - [x] 3.3 Write unit tests for `SourceTextPresenceCheck`
    - Test matcher called with correct args; test verified/no_match outcomes; test evidence keys present; test confidence passthrough and clamping
    - _Requirements: 3.4, 3.6, 3.7, 3.8_

- [x] 4. Implement `SemanticSourceVerificationCheck`
  - [x] 4.1 Write `quality_control/checks/semantic_source.py` with `SemanticSourceVerificationCheck` dataclass
    - `check_name: ClassVar[str] = "semantic_source_verification"`; `matcher: Callable`; `on_index_unavailable: str`
    - `__post_init__` raises `ValueError` for invalid `on_index_unavailable` values
    - `run(query, sentence_store, embed_fn, threshold, page_texts) -> VerificationResult`
    - Implement all three `on_index_unavailable` modes: `"skip"` returns `status="unavailable"`; `"fail"` raises `RuntimeError`; `"degrade"` calls matcher, emits `WARNING` via `utils.logging_utils`, returns result
    - When store available: `status="candidate_match"` if `score >= threshold`; `status="no_match"` otherwise; store below-threshold score in `details["below_threshold_score"]`
    - No top-level imports of `sentence_transformers`, `faiss`, `torch`; no model loading or FAISS index construction
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 4.11, 4.12, 4.13, 4.14, 4.15_

  - [x] 4.2 Write property test for `on_index_unavailable` modes (Property 4)
    - **Property 4: SemanticSourceVerificationCheck on_index_unavailable modes**
    - **Validates: Requirements 4.7, 4.8, 4.9**
    - Use `st.sampled_from(["skip", "fail", "degrade"])` and unavailable store variants in `tests/quality_control/test_qc_checks_semantic_source.py`
    - Assert skip returns `status="unavailable"` score=0.0 all-None evidence; fail raises `RuntimeError`; degrade calls matcher and emits WARNING

  - [x] 4.3 Write property test for threshold behavior (Property 5)
    - **Property 5: SemanticSourceVerificationCheck threshold behavior**
    - **Validates: Requirements 4.10, 4.11**
    - Use `st.floats(0.0, 1.0, allow_nan=False)` for threshold and candidate score
    - Assert `status="candidate_match"` when `score >= threshold`; `status="no_match"` when below; `details["below_threshold_score"]` populated when diagnostic score available

  - [x] 4.4 Write unit tests for `SemanticSourceVerificationCheck`
    - Test constructor `ValueError` for invalid `on_index_unavailable`; test all three modes; test no heavy imports in `sys.modules`
    - _Requirements: 4.5, 4.7, 4.8, 4.9, 4.14_

- [x] 5. Implement `ExtractorAgreementCheck`
  - [x] 5.1 Write `quality_control/checks/extractor_agreement.py` with `ExtractorAgreementCheck` dataclass
    - `exact_matcher: Callable`; `semantic_matcher: Callable | None = None`
    - `run(primary_blocks, candidate_blocks, config) -> dict`
    - Only runs when `quality_control.semantic_verification.extractor_agreement.enabled` is `true`
    - Discard candidate sentences shorter than `len_filter` (default 40) before matching
    - Pass all candidates through `exact_matcher` first; only unmatched go to `semantic_matcher`
    - Raise `ImportError` when semantic path reached but `semantic_matcher is None`
    - Report dict with all required keys: `primary_sentence_count`, `candidate_sentence_count`, `exact_match_count`, `near_match_count`, `unmatched_primary_count`, `unmatched_candidate_count`, `agreement_rate`, `semantic_threshold`, `examples`
    - `agreement_rate = (exact_match_count + near_match_count) / primary_sentence_count` (or `0.0` when count is 0); `semantic_threshold = 0.0` when `semantic_matcher is None`
    - Cap each list under `examples` at `max_examples`; result stored in `ctx.metrics_hierarchy["semantic_verification"]["extractor_agreement"]`
    - Result must NOT influence `ctx.decision`, `ctx.reports`, `ctx.unified`
    - No inline matching, embedding, or sentence segmentation logic
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 5.13, 5.14, 5.15, 5.16, 5.17, 5.18, 5.19, 5.20_

  - [x] 5.2 Write property test for `agreement_rate` formula (Property 6)
    - **Property 6: ExtractorAgreementCheck agreement_rate formula**
    - **Validates: Requirements 5.12, 5.14**
    - Use `st.integers(min_value=0)` for counts in `tests/quality_control/test_qc_checks_extractor_agreement.py`
    - Assert `agreement_rate == (exact_match_count + near_match_count) / primary_sentence_count` when count > 0; assert `0.0` when count == 0

  - [x] 5.3 Write property test for examples cap (Property 7)
    - **Property 7: ExtractorAgreementCheck examples cap**
    - **Validates: Requirements 5.17**
    - Use `st.integers(min_value=0, max_value=50)` for item counts
    - Assert `len(examples["unmatched_primary"]) <= max_examples`, same for `unmatched_candidate` and `near_matches`

  - [x] 5.4 Write unit tests for `ExtractorAgreementCheck`
    - Test exact-only mode produces complete report with `near_match_count=0`; test mocked semantic_matcher increments `near_match_count`; test `ImportError` when semantic path reached with `semantic_matcher=None`; test `semantic_threshold=0.0` in exact-only mode
    - _Requirements: 5.8, 5.9, 5.18, 5.19, 5.20_

- [x] 6. Implement `build_task_quality_scaffold`
  - [x] 6.1 Write `quality_control/checks/task_quality.py` with `build_task_quality_scaffold` function
    - Return JSON-serializable dict with placeholder entries for all eight metrics: `field_recall`, `critical_field_recall`, `evidence_validity`, `evidence_compactness`, `cost_reduction`, `manual_qc_rate`, `interobserver_agreement`, `pipeline_agreement`
    - Each placeholder: `status="scaffolded"` or `"not_computed"`, `value=null`
    - Top-level `details` key with non-empty string; top-level `status` key set to `"not_computed"` or `"scaffolded"`
    - No HTTP requests, no LLM API calls, no environment variable reads for credentials
    - When included in per-PDF output, stored under key `"task_quality_scaffold"` (never `"semantic_qc"`)
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11_

  - [x] 6.2 Write unit test for `build_task_quality_scaffold` JSON-serializability (Property 8)
    - **Property 8: build_task_quality_scaffold is always JSON-serializable**
    - **Validates: Requirements 7.5**
    - Call `build_task_quality_scaffold()` and assert `json.dumps(result)` succeeds without error and without a custom encoder
    - Assert all eight placeholder metric keys are present; assert `details` is a non-empty string; assert `status` key present

- [x] 7. Checkpoint - Ensure all checks/ tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Rename `LocalQCReport` and `LocalQCMetricRecord` to descriptive names
  - [x] 8.1 Rename `LocalQCReport` to `ExtractionCoverageReport` in `quality_control/local_metrics.py` and update all import sites
    - Rename class definition in `local_metrics.py`
    - Rename `_check_grobid_vs_native_ratio` to `_check_extraction_coverage_ratio` and metric_name string `"grobid_vs_native_ratio"` to `"extraction_coverage_ratio"` in `local_metrics.py`
    - Update all import sites in `quality_control/`, `pipeline/`, and `tests/quality_control/`
    - No compatibility shim or re-export alias for old names
    - _Requirements: 8.1, 8.6, 8.7, 8.12_

  - [x] 8.2 Rename `LocalQCMetricRecord` to `ExtractionCoverageMetricRecord` in `quality_control/models.py` and update all import sites
    - Rename class definition in `models.py`
    - Update all import sites in `quality_control/`, `pipeline/`, and `tests/quality_control/`
    - No compatibility shim or re-export alias for old names
    - _Requirements: 8.2, 8.12_

  - [x] 8.3 Update existing test files for renamed classes
    - Update `test_quality_control_local_metrics.py`: `LocalQCReport` to `ExtractionCoverageReport`, `LocalQCMetricRecord` to `ExtractionCoverageMetricRecord`, `"grobid_vs_native_ratio"` to `"extraction_coverage_ratio"`
    - Update `test_qc_models.py`: `LocalQCMetricRecord` to `ExtractionCoverageMetricRecord`; add `VerificationResult` tests
    - _Requirements: 8.1, 8.2, 8.6_

- [x] 9. Rename sub-package `defaults/` to `builtin_impls/`
  - [x] 9.1 Create `quality_control/builtin_impls/` with equivalent content and update all import sites
    - Create `quality_control/builtin_impls/` directory with `__init__.py`, `quality_report.py`, `inter_rater_report.py`, `adjudication_decision.py` with identical content to `quality_control/defaults/`
    - `builtin_impls/__init__.py` exports `QualityReport`, `InterRaterReport`, `AdjudicationDecision`
    - Update all import sites in `quality_control/`, `pipeline/`, and `tests/quality_control/` to use `quality_control.builtin_impls`
    - _Requirements: 8.3, 8.4, 8.12_

  - [x] 9.2 Delete `quality_control/defaults/` directory
    - Remove all files under `quality_control/defaults/` and the directory itself
    - Verify no remaining import of `quality_control.defaults` anywhere in the codebase
    - _Requirements: 8.5_

- [x] 10. Update `_QC_DEFAULTS` and `configs/config.yaml`
  - [x] 10.1 Update `utils/config_utils.py` `_QC_DEFAULTS` with new keys and remove deleted keys
    - Add `quality_control.source_text_verification.enabled = True`
    - Add `quality_control.semantic_verification` block with `enabled=False`, `similarity_threshold=0.85`, `max_sentences=10000`, `model_name="BAAI/bge-base-en-v1.5"`, `on_index_unavailable="skip"`, and `extractor_agreement` sub-block with `enabled=False`, `len_filter=40`, `max_examples=10`
    - Add `quality_control.task_quality_scaffold.enabled = True`
    - Rename `grobid_vs_native_ratio_threshold` to `extraction_coverage_ratio_threshold` (same numeric default)
    - Delete `quality_control.semantic_qc` key and `quality_control.local_metrics.grobid_vs_native_ratio_threshold` key
    - _Requirements: 9.1, 9.2, 9.3, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 9.13, 9.14, 9.15, 9.16, 9.17_

  - [x] 10.2 Update `configs/config.yaml` to match new key names
    - Rename `quality_control.semantic_qc` to `quality_control.semantic_verification`
    - Rename `quality_control.local_metrics.grobid_vs_native_ratio_threshold` to `extraction_coverage_ratio_threshold`
    - Add `source_text_verification`, `task_quality_scaffold`, and `extractor_agreement` sections
    - _Requirements: 9.1, 9.14, 9.15_

- [x] 11. Update `run_quality_control` in `quality_control/quality_control.py`
  - [x] 11.1 Update imports, metrics_hierarchy keys, and config reads in `quality_control/quality_control.py`
    - Import from `quality_control.builtin_impls` instead of `quality_control.defaults`
    - Import `ExtractionCoverageReport` instead of `LocalQCReport`
    - Initialize `metrics_hierarchy` with keys `"extraction_coverage"`, `"source_text_verification"`, `"semantic_verification"` (remove `"local_metrics"`, `"exact_match"`, `"semantic_match"`)
    - Replace `config.get("semantic_qc", {})` with `config.get("semantic_verification", {})`
    - Remove all literal strings `"Tier 1"`, `"Tier 2"`, `"Tier 3"` from docstrings and inline comments
    - _Requirements: 8.9, 8.10, 8.11, 8.13_

  - [x] 11.2 Wire `SourceTextPresenceCheck`, `SemanticSourceVerificationCheck`, and `ExtractorAgreementCheck` into `run_quality_control`
    - Wire `SourceTextPresenceCheck` for source-text verification; bypass when `source_text_verification.enabled=false` and record passing sentinel in `metrics_hierarchy["source_text_verification"]`
    - Wire `SemanticSourceVerificationCheck` for semantic verification, passing `on_index_unavailable` from config; bypass when `semantic_verification.enabled=false`
    - Wire `ExtractorAgreementCheck` when `extractor_agreement.enabled=true`; store result in `metrics_hierarchy["semantic_verification"]["extractor_agreement"]`
    - Preserve private helpers `_extract_branch_payload`, `_build_native_page_texts`, `_build_placeholder_sentence_store` with current signatures
    - _Requirements: 5.15, 9.4, 9.5, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

- [x] 12. Update `quality_control/__init__.py`
  - [x] 12.1 Update `quality_control/__init__.py` exports to use new names
    - Replace `LocalQCReport` with `ExtractionCoverageReport`
    - Replace `LocalQCMetricRecord` with `ExtractionCoverageMetricRecord`
    - Add `VerificationResult` to exports
    - Import from `quality_control.builtin_impls` instead of `quality_control.defaults`
    - Import from `quality_control.checks`
    - No old names remain in `__all__`
    - _Requirements: 8.12, 15.9_

- [x] 13. Checkpoint - Ensure all renamed-symbol tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Add AST-based separation test
  - [x] 14.1 Write `tests/steering/test_qc_textprocessor_separation.py`
    - Walk every `.py` file under `quality_control/checks/` using AST analysis (same pattern as `tests/test_dependency_directions.py`)
    - Fail with descriptive message if any file contains an import of `text_processing` or `utils.text_processor.TextProcessor`
    - _Requirements: 12.10, 13.9, 13.10_

- [x] 15. Add pipeline integration and preservation tests
  - [x] 15.1 Write `tests/quality_control/test_qc_pipeline_integration.py`
    - Test `run_quality_control` completes without error when `semantic_verification.enabled=false`
    - Assert `ctx.metrics_hierarchy` contains exactly keys `"extraction_coverage"`, `"source_text_verification"`, `"semantic_verification"`
    - Assert `metrics_hierarchy["semantic_verification"]["extractor_agreement"]` is absent or `status="skipped"` when `extractor_agreement.enabled=false`
    - Assert manifest status values are unchanged after migration
    - Include parametrized preservation case: same inputs to both `ExtractionCoverageReport` and `LocalQCReport` (imported under alias) assert identical pass/fail boolean outcome
    - Include production-import test: import `quality_control`, `quality_control.checks`, `quality_control.builtin_impls` and assert `sentence_transformers`, `faiss`, `torch` not in `sys.modules`
    - Assert `build_task_quality_scaffold()` return value serializes with `json.dumps()` without error
    - _Requirements: 10.1, 13.11, 13.12, 13.13, 13.14_

  - [x] 15.2 Write property test for `metrics_hierarchy` keys (Property 9)
    - **Property 9: metrics_hierarchy contains exactly the three new keys after run_quality_control**
    - **Validates: Requirements 8.9, 8.13, 10.1**
    - Use `st.lists(st.builds(Candidate, ...))` to generate varied branch inputs
    - Assert `metrics_hierarchy` contains exactly `"extraction_coverage"`, `"source_text_verification"`, `"semantic_verification"` and does NOT contain `"local_metrics"`, `"exact_match"`, `"semantic_match"`, `"semantic_qc"`

  - [x] 15.3 Write unit tests for bypass behavior
    - Test `source_text_verification.enabled=false`: assert lexical check not invoked and `metrics_hierarchy["source_text_verification"]` records passing result
    - Test `semantic_verification.enabled=false`: assert `sentence_transformers`, `faiss`, `torch` not in `sys.modules` after import
    - _Requirements: 9.4, 9.5, 10.5, 10.6, 13.2, 13.3_

- [x] 16. Update existing QC test files for pipeline key assertions
  - [x] 16.1 Update `tests/quality_control/test_quality_control_pipeline.py` metrics_hierarchy key assertions
    - Replace assertions on `"local_metrics"`, `"exact_match"`, `"semantic_match"` with `"extraction_coverage"`, `"source_text_verification"`, `"semantic_verification"`
    - _Requirements: 8.9, 8.13_

- [x] 17. Write `quality_control/README.md`
  - [x] 17.1 Create or update `quality_control/README.md` with new package layout documentation
    - Document `ExtractionCoverageReport`, `ExtractionCoverageMetricRecord`, `builtin_impls/`, `checks/` with role descriptions
    - Document `SourceTextPresenceCheck`, `SemanticSourceVerificationCheck`, `ExtractorAgreementCheck` (noting optional status of `ExtractorAgreementCheck`)
    - Must NOT contain `LocalQCReport`, `LocalQCMetricRecord`, `defaults/`, `local_metrics` as metrics-hierarchy key, `exact_match` as metrics-hierarchy key, `semantic_match` as metrics-hierarchy key, or `semantic_qc`
    - Must NOT describe `TextProcessor` internals, `SentenceSegment` class hierarchy, or mocking patterns
    - _Requirements: 14.1, 14.2, 14.3, 14.8_

- [x] 18. Update steering and changelog documentation
  - [x] 18.1 Update `.kiro/steering/config.md` with new QC config keys
    - Add named entries for `source_text_verification`, `semantic_verification`, `extraction_coverage_ratio_threshold`, `on_index_unavailable`, `semantic_verification.model_name`, `semantic_verification.max_sentences`, and all sub-keys of `semantic_verification.extractor_agreement`
    - Remove or replace occurrences of `semantic_qc`, `grobid_vs_native_ratio_threshold`, `local_metrics` as metrics-hierarchy key, `exact_match` as metrics-hierarchy key, `semantic_match` as metrics-hierarchy key
    - _Requirements: 14.4, 14.5_

  - [x] 18.2 Update `.kiro/steering/testing.md` with new test file names and contracts
    - Name `test_qc_textprocessor_separation.py` and the QC output preservation test file
    - Describe pass/fail contract for each: what each test checks and what constitutes a failure
    - _Requirements: 14.6_

  - [x] 18.3 Add QC migration entry to `CHANGELOG.md`
    - List by name each renamed QC symbol, each renamed config key, each deleted package/directory, and each added package/directory introduced by this migration
    - _Requirements: 14.7_

- [x] 19. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests (Properties 2-9) validate universal correctness properties using Hypothesis `@given` + `@settings(max_examples=100)`
- Property 1 (no forbidden imports in `checks/`) is validated by the AST-based test in task 14.1 - not a Hypothesis test
- Unit tests validate specific examples and edge cases
- Heavy optional deps (`faiss`, `torch`, `sentence_transformers`) must be patched via `patch.dict(sys.modules, ...)` - never require them to be installed
- All matcher dependencies in tests use `MagicMock` - never a real `TextProcessor`
- Tag format for property tests: `# Feature: qc-migration, Property N: <property_text>`
- Phase 2 (TextProcessor migration) must not begin until all tasks here are complete

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "3.1", "4.1", "5.1", "6.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "4.2", "4.3", "4.4", "5.2", "5.3", "5.4", "6.2"] },
    { "id": 3, "tasks": ["8.1", "8.2", "9.1", "10.1", "10.2"] },
    { "id": 4, "tasks": ["8.3", "9.2", "11.1"] },
    { "id": 5, "tasks": ["11.2", "12.1"] },
    { "id": 6, "tasks": ["14.1", "15.1", "16.1"] },
    { "id": 7, "tasks": ["15.2", "15.3", "17.1"] },
    { "id": 8, "tasks": ["18.1", "18.2", "18.3"] }
  ]
}
```
