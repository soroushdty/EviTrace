# Requirements Document

## Introduction

This document specifies the Quality Control (QC) migration for the EviTrace project. The migration reorganises the `quality_control/` package so that QC verification logic lives in dedicated check classes, output uses descriptive naming throughout, and QC checks depend on injected matcher contracts rather than on `TextProcessor` implementation details. This is Phase 1 of a two-phase migration; the TextProcessor migration (Phase 2) must not begin until every requirement here is complete.

The migration does not create, move, delete, rename, or refactor any TextProcessor or domain-agnostic text-processing implementation. In particular it does not own `text_processing/`, `utils/text_processor.py`, `pdf_extractor/utils/text_utils.py`, `pdf_extractor/utils/embedding_utils.py`, sentence segmentation backends, normalizers, tokenizers, lexical matcher implementations, semantic matcher implementations, embedding model loading, FAISS index construction, or optional semantic dependency loading.

## Glossary

- **QCBundle**: The shared mutable state object passed through all QC pipeline stages, carrying `branches`, `reports`, `iaa_metrics`, `decision`, `unified`, and `metrics_hierarchy`.
- **VerificationResult**: The new stable dataclass produced by QC check classes to report the outcome of a single verification check.
- **ExtractionCoverageReport**: The renamed class (previously `LocalQCReport`) that runs the 8 heuristic Tier 1 checks.
- **ExtractionCoverageMetricRecord**: The renamed class (previously `LocalQCMetricRecord`) that holds a single Tier 1 metric result.
- **SourceTextPresenceCheck**: A new QC check class that verifies source-text presence via an injected lexical matcher dependency.
- **SemanticSourceVerificationCheck**: A new QC check class that verifies source text semantically via an injected semantic-search dependency.
- **ExtractorAgreementCheck**: An optional new QC check class that compares two extractor branch payloads and emits an agreement report.
- **builtin_impls**: The renamed sub-package (previously `defaults/`) that exports `QualityReport`, `InterRaterReport`, and `AdjudicationDecision`.
- **metrics_hierarchy**: The dict on `QCBundle` that organises QC results under the keys `extraction_coverage`, `source_text_verification`, and `semantic_verification`.
- **_QC_DEFAULTS**: The dict in `utils/config_utils.py` that holds default values for all `quality_control` and `text_processor` config keys.
- **TextProcessor**: The existing class in `utils/text_processor.py`; QC check modules must not import it.
- **sentence_store**: A dict structure holding sentences, page indices, bounding boxes, and optionally a FAISS index, used by semantic verification.
- **embed_fn**: A callable that converts a query string to an embedding vector; injected into `SemanticSourceVerificationCheck.run()`.

## Requirements

### Requirement 1: Create QC Check Package

**User Story:** As a developer, I want QC verification logic to live in dedicated QC check classes, so that QC behavior is isolated from text-processing implementation details.

#### Acceptance Criteria

1. THE `quality_control/checks/` package SHALL exist as a directory containing an `__init__.py` file, making it a valid Python package importable as `quality_control.checks`.
2. THE `quality_control/checks/__init__.py` SHALL export `SourceTextPresenceCheck`, `SemanticSourceVerificationCheck`, `ExtractorAgreementCheck`, and `build_task_quality_scaffold` such that each name is directly importable from `quality_control.checks` without further submodule qualification.
3. THE package SHALL contain the files `quality_control/checks/source_text.py`, `quality_control/checks/semantic_source.py`, `quality_control/checks/extractor_agreement.py`, and `quality_control/checks/task_quality.py`, each existing on disk and importable without error.
4. ALL `.py` files under `quality_control/checks/`, including `__init__.py`, SHALL NOT contain any import statement that imports `TextProcessor` by name or imports from `utils.text_processor`.
5. ALL `.py` files under `quality_control/checks/`, including `__init__.py`, SHALL NOT contain any import statement that imports from the `text_processing` package.
6. ALL `.py` files under `quality_control/checks/`, including `__init__.py`, SHALL NOT contain any top-level import statement (outside of function or method bodies) that imports `faiss`, `torch`, `sentence_transformers`, `spacy`, `scispacy`, `stanza`, or `wtpsplit`.
7. ALL `.py` files under `quality_control/checks/`, including `__init__.py`, SHALL NOT contain any class or function that implements normalization, tokenization, embedding processing, sentence segmentation, lexical matching algorithms, or semantic matching algorithms as inline logic within the file.

### Requirement 2: Define QC Result Models

**User Story:** As a developer consuming QC results, I want stable QC result dataclasses, so that downstream code can read QC output without depending on matcher internals.

#### Acceptance Criteria

1. THE `quality_control/models.py` module SHALL define `VerificationResult` as a dataclass decorated with `@dataclass`.
2. THE `VerificationResult` dataclass SHALL contain exactly the fields `check_name: str`, `status: str`, `score: float`, `evidence: dict`, and `details: dict`, with no additional required fields.
3. THE allowed `VerificationResult.status` values SHALL be `"verified"`, `"candidate_match"`, `"no_match"`, `"skipped"`, and `"unavailable"`; any other value SHALL be considered invalid.
4. THE `VerificationResult.score` value SHALL be constrained to the closed range `[0.0, 1.0]`; constructing a `VerificationResult` with a score outside this range SHALL raise `ValueError`.
5. THE `VerificationResult.evidence` dict SHALL contain exactly the keys `found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, and `span_bboxes` when produced by source-verification checks.
6. WHEN no evidence is available, each of the six source-verification evidence keys SHALL be present in the dict with value `None`.
7. THE `quality_control/models.py` module SHALL NOT define, export, or reference any attribute, field, or dict key named `semantic_qc`, `exact_match`, or `semantic_match`.

### Requirement 3: Implement Source Text Presence Check

**User Story:** As a developer, I want source-text presence verification to be a QC check that consumes an injected search dependency, so that QC can run before the TextProcessor migration.

#### Acceptance Criteria

1. THE `SourceTextPresenceCheck` class SHALL be defined in `quality_control/checks/source_text.py`.
2. THE class SHALL set `check_name` as a class-level attribute with the string value `"source_text_presence"`.
3. THE constructor SHALL accept a positional or keyword parameter named `matcher` that receives the injected matcher dependency.
4. THE injected `matcher` dependency SHALL be called with the signature `matcher(needle, full_text, page_texts, blocks)` and SHALL return either a dict containing evidence fields or `None`.
5. THE class SHALL implement `run(needle: str, full_text: str, page_texts: dict, blocks: list) -> VerificationResult`.
6. WHEN the injected `matcher` returns a non-`None` dict, THE `SourceTextPresenceCheck.run()` SHALL produce a `VerificationResult` with `status="verified"`.
7. WHEN the injected `matcher` returns a non-`None` dict, THE `SourceTextPresenceCheck.run()` SHALL produce a `VerificationResult` with `score=1.0` unless the candidate dict contains a `confidence` key whose value is a float in `[0.0, 1.0)`, in which case that value SHALL be used as the score.
8. WHEN the injected `matcher` returns `None`, THE `SourceTextPresenceCheck.run()` SHALL produce a `VerificationResult` with `status="no_match"`, `score=0.0`, and all six evidence keys (`found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`) set to `None`.
9. THE `SourceTextPresenceCheck` class body SHALL NOT contain any import statement or inline implementation of lexical normalization or exact-match search logic.
10. THE `SourceTextPresenceCheck` SHALL be importable without error when the `text_processing` package does not exist.

### Requirement 4: Implement Semantic Source Verification Check

**User Story:** As a developer, I want semantic source verification to be a QC check that consumes an injected semantic-search dependency, so that QC result handling is separate from embedding and FAISS implementation.

#### Acceptance Criteria

1. THE `SemanticSourceVerificationCheck` class SHALL be defined in `quality_control/checks/semantic_source.py`.
2. THE class SHALL set `check_name` as a class-level attribute with the string value `"semantic_source_verification"`.
3. THE constructor SHALL accept a positional or keyword parameter named `matcher` that receives the injected semantic-search dependency.
4. THE constructor SHALL accept a keyword parameter named `on_index_unavailable` whose valid values are the strings `"skip"`, `"fail"`, and `"degrade"`.
5. WHEN `on_index_unavailable` is set to any value other than `"skip"`, `"fail"`, or `"degrade"`, THE constructor SHALL raise `ValueError` with a message that lists all three valid values.
6. THE class SHALL implement `run(query: str, sentence_store: dict, embed_fn: callable, threshold: float, page_texts: dict | None) -> VerificationResult`. A `sentence_store` is considered unavailable when it is `None`, empty (`{}`), or lacks a `sentences` key with at least one entry.
7. WHEN the sentence store is unavailable and `on_index_unavailable="skip"`, THE `SemanticSourceVerificationCheck.run()` SHALL produce a `VerificationResult` with `status="unavailable"`, `score=0.0`, and all six evidence keys set to `None`.
8. WHEN the sentence store is unavailable and `on_index_unavailable="fail"`, THE `SemanticSourceVerificationCheck.run()` SHALL raise `RuntimeError` with a message indicating the sentence store is unavailable.
9. WHEN the sentence store is unavailable and `on_index_unavailable="degrade"`, THE `SemanticSourceVerificationCheck.run()` SHALL call the injected `matcher` as a lexical-degrade fallback, emit a log message at `WARNING` level via the `utils.logging_utils` logger, and return a `VerificationResult` with the six standard evidence keys populated from the matcher result or set to `None` if the matcher returns `None`.
10. WHEN the sentence store is available but the injected `matcher` returns no candidate with a score meeting or exceeding `threshold`, THE `SemanticSourceVerificationCheck.run()` SHALL produce a `VerificationResult` with `status="no_match"` and `score=0.0`; IF the matcher returns a below-threshold diagnostic score, that score SHALL be stored in `details` under the key `"below_threshold_score"`.
11. WHEN the injected `matcher` returns a candidate with a score meeting or exceeding `threshold`, THE `SemanticSourceVerificationCheck.run()` SHALL produce a `VerificationResult` with `status="candidate_match"` and `score` set to the candidate's score value.
12. THE `SemanticSourceVerificationCheck` class body SHALL NOT contain any call that loads or initialises an embedding model.
13. THE `SemanticSourceVerificationCheck` class body SHALL NOT contain any call that builds or initialises a FAISS index.
14. THE `SemanticSourceVerificationCheck` module SHALL NOT contain any top-level import of `sentence_transformers`, `faiss`, or `torch`.
15. THE `SemanticSourceVerificationCheck` SHALL be importable without error when the `text_processing` package does not exist.

### Requirement 5: Add Optional Extractor Agreement Reporting

**User Story:** As a developer evaluating extractor behavior, I want optional extractor-agreement metrics, so that I can inspect agreement without changing parser output selection.

#### Acceptance Criteria

1. THE `ExtractorAgreementCheck` class SHALL be defined in `quality_control/checks/extractor_agreement.py`.
2. THE `ExtractorAgreementCheck` SHALL be optional and SHALL NOT run unless `quality_control.semantic_verification.extractor_agreement.enabled` is `true` in the active configuration.
3. THE `ExtractorAgreementCheck` constructor SHALL accept an injected `exact_matcher` dependency and an optional injected `semantic_matcher` dependency (defaulting to `None`).
4. THE `ExtractorAgreementCheck` class body SHALL NOT contain any inline implementation of exact matching, semantic matching, embedding model loading, or sentence segmentation backends.
5. THE `ExtractorAgreementCheck` SHALL accept `primary_blocks` and `candidate_blocks` as its two input payloads for comparison.
6. THE `ExtractorAgreementCheck` MAY call existing block-oriented PDF sentence processing functions from `pdf_extractor` to extract sentences from blocks, because block-level sentence extraction is not a TextProcessor responsibility in this QC phase.
7. THE `ExtractorAgreementCheck` SHALL discard any candidate sentence whose character length is strictly less than the value of `quality_control.semantic_verification.extractor_agreement.len_filter` (default `40`) before performing any matching.
8. THE `ExtractorAgreementCheck` SHALL pass all candidate sentences through the injected `exact_matcher` first; only sentences not matched by `exact_matcher` SHALL be passed to `semantic_matcher`.
9. THE `ExtractorAgreementCheck` SHALL invoke `semantic_matcher` only when semantic comparison is enabled (i.e., `semantic_matcher` is not `None`) and the sentence has not already been matched by `exact_matcher`.
10. THE `ExtractorAgreementCheck` SHALL NOT call any function that downloads a model or accesses GPU resources; all model access SHALL be delegated to the injected dependencies.
11. THE `ExtractorAgreementCheck` SHALL NOT require GPU resources to be available; all computation SHALL be CPU-compatible when injected dependencies are CPU-compatible.
12. THE `ExtractorAgreementCheck` SHALL emit a report dict with exactly the keys: `primary_sentence_count` (int), `candidate_sentence_count` (int), `exact_match_count` (int), `near_match_count` (int), `unmatched_primary_count` (int), `unmatched_candidate_count` (int), `agreement_rate` (float), `semantic_threshold` (float), and `examples` (dict with keys `unmatched_primary`, `unmatched_candidate`, and `near_matches`, each a list).
13. THE `agreement_rate` in the report SHALL equal `(exact_match_count + near_match_count) / primary_sentence_count` computed as a float.
14. WHEN `primary_sentence_count == 0`, THE `agreement_rate` SHALL be `0.0`.
15. THE extractor-agreement report SHALL be stored in `ctx.metrics_hierarchy["semantic_verification"]["extractor_agreement"]` on the `QCBundle` instance.
16. THE extractor-agreement report SHALL NOT be read by or passed to any component that performs fallback selection, branch election, reconciler output, `UnifiedRecord` construction, W3C annotation projection, LLM prompt assembly, or field persistence.
17. THE `examples` dict in the report SHALL contain at most `quality_control.semantic_verification.extractor_agreement.max_examples` (default `10`) items per list key.
18. IF `semantic_matcher` is `None` while semantic matching is requested (i.e., the semantic comparison path is reached), THE `ExtractorAgreementCheck` SHALL raise `ImportError` with a message identifying the missing dependency.
19. IF `semantic_matcher` is `None` while semantic matching is not requested, THE `ExtractorAgreementCheck` SHALL produce a complete report using exact-match results only.
20. WHEN semantic matching is disabled (i.e., `semantic_matcher` is `None`), THE `semantic_threshold` field in the report SHALL be `0.0`.

### Requirement 6: Keep Semantic Verification Report-Only

**User Story:** As a developer, I want semantic-verification metrics to be observable without controlling extraction behavior, so that metrics can be validated safely.

#### Acceptance Criteria

1. IF the relevant input data (e.g., sentence store, page texts, section list) is non-null and non-empty, THE semantic-verification layer MAY report extractor agreement rate, missing-sentence rate, page semantic coverage, section coverage, evidence recoverability, duplicate/glued text detection, and fallback confidence.
2. THE semantic-verification metrics SHALL be stored in `ctx.metrics_hierarchy["semantic_verification"]` on the `QCBundle` instance as QC report dicts or `VerificationResult` instances.
3. THE semantic-verification metrics SHALL NOT be stored in `ctx.decision`, `ctx.reports` adjudication fields, or any field read by `AdjudicationRules.adjudicate()`.
4. THE semantic-verification metrics SHALL NOT cause any call to scan-detection re-routing, backend selection modification, OCR fallback activation, page patching, or extractor branch replacement.
5. THE semantic-verification metrics SHALL NOT alter the value of `ctx.unified.content["exact_text"]`, the evidence bundle passed to the LLM, LLM prompt strings, `ValidationResult` objects, or any field written to `outputs/<paper>.extracted.json`.
6. IF semantic verification is disabled in configuration, THE parser SHALL produce output that passes `StructureSchemaValidator` validation without any semantic-verification fields present.
7. IF semantic matching is disabled, THE semantic-verification metrics SHALL be deterministic: given identical `QCBundle` inputs, the same metric values SHALL be produced on every invocation within the same process run.
8. EACH semantic-verification metric value SHALL include a `computation_method` field set to one of `"exact_only"`, `"semantic"`, or `"skipped"` to identify how it was computed.

### Requirement 7: Add Task Quality Scaffold

**User Story:** As a developer evaluating downstream evidence extraction, I want a scaffold for task-quality metrics, so that parser output can reserve stable fields without running task-specific LLM checks.

#### Acceptance Criteria

1. THE `quality_control/checks/task_quality.py` module SHALL define and export a function named `build_task_quality_scaffold`.
2. THE `build_task_quality_scaffold` function SHALL NOT make any HTTP request or call any function that communicates with an LLM API.
3. THE `build_task_quality_scaffold` function SHALL NOT read or require any environment variable or config key related to OpenAI credentials.
4. THE `build_task_quality_scaffold` function SHALL NOT modify `ctx.branches`, `ctx.decision`, `ctx.unified`, or any field read by the evidence bundle builder, validator, or LLM prompt assembler.
5. THE `build_task_quality_scaffold` function SHALL return a value that is JSON-serializable using the standard `json.dumps()` function without error.
6. THE `build_task_quality_scaffold` function SHALL set the `status` of every unimplemented metric to either `"scaffolded"` or `"not_computed"` and the `value` of every unimplemented metric to `null`.
7. THE return value of `build_task_quality_scaffold` SHALL include a placeholder entry for each of the following metrics: `field_recall`, `critical_field_recall`, `evidence_validity`, `evidence_compactness`, `cost_reduction`, `manual_qc_rate`, `interobserver_agreement`, and `pipeline_agreement`.
8. THE return value of `build_task_quality_scaffold` SHALL include a top-level `details` key whose value is a non-empty string stating that task-specific criteria are not active in this refactor.
9. WHEN the scaffold is included in per-PDF output JSON, THE scaffold value SHALL be stored under the key `"task_quality_scaffold"`.
10. THE scaffold SHALL NOT be stored under the key `"semantic_qc"` in any output dict or `QCBundle` field.
11. THE `quality_control/checks/task_quality.py` module SHALL NOT introduce any new entry in `install_requires` or `extras_require` in the project's dependency configuration.

### Requirement 8: Rename QC Classes, Packages, Metrics, and Keys

**User Story:** As a developer reading QC code, I want descriptive QC names, so that code and output are understandable without legacy naming.

#### Acceptance Criteria

1. THE class `LocalQCReport` in `quality_control/local_metrics.py` SHALL be renamed to `ExtractionCoverageReport`; all import sites in `quality_control/`, `pipeline/`, and `tests/quality_control/` SHALL be updated to use the new name.
2. THE class `LocalQCMetricRecord` in `quality_control/models.py` SHALL be renamed to `ExtractionCoverageMetricRecord`; all import sites in `quality_control/`, `pipeline/`, and `tests/quality_control/` SHALL be updated to use the new name.
3. THE sub-package `quality_control/defaults/` SHALL be renamed to `quality_control/builtin_impls/` by creating the new directory with equivalent content and deleting the old directory.
4. THE new `quality_control/builtin_impls/__init__.py` SHALL export `QualityReport`, `InterRaterReport`, and `AdjudicationDecision` such that each is directly importable from `quality_control.builtin_impls`.
5. THE old directory `quality_control/defaults/` SHALL NOT exist on disk after this migration completes.
6. THE string literal `"grobid_vs_native_ratio"` used as a `metric_name` value in `ExtractionCoverageMetricRecord` instances SHALL be replaced with `"extraction_coverage_ratio"` in all locations within `quality_control/`.
7. THE method `_check_grobid_vs_native_ratio` in `quality_control/local_metrics.py` SHALL be renamed to `_check_extraction_coverage_ratio`.
8. THE config key `grobid_vs_native_ratio_threshold` SHALL be renamed to `extraction_coverage_ratio_threshold` in `configs/config.yaml`, in `_QC_DEFAULTS` in `utils/config_utils.py`, and at every `.get("grobid_vs_native_ratio_threshold", ...)` read site in `quality_control/`.
9. THE `QCBundle.metrics_hierarchy` initialization dict literal in `run_quality_control` and all write sites SHALL use the keys `"extraction_coverage"`, `"source_text_verification"`, and `"semantic_verification"` in place of `"local_metrics"`, `"exact_match"`, and `"semantic_match"`.
10. THE config read sites `config.get("semantic_qc", {})` in `quality_control/quality_control.py` and the corresponding key in `_QC_DEFAULTS` SHALL be updated to use `"semantic_verification"`.
11. THE literal strings `"Tier 1"`, `"Tier 2"`, and `"Tier 3"` SHALL NOT appear in any docstring or inline comment in `quality_control/quality_control.py`.
12. ALL import statements and call sites in `quality_control/`, `pipeline/`, and `tests/quality_control/` that reference old QC names SHALL be updated to the new names; no old name SHALL remain as a live symbol reference.
13. No in-memory `QCBundle.metrics_hierarchy` dict and no serialized form written to disk or logs SHALL use `"local_metrics"`, `"exact_match"`, `"semantic_match"`, or `"semantic_qc"` as a key.

### Requirement 9: Update QC Configuration Schema

**User Story:** As a developer configuring QC, I want clear independent toggles, so that checks can be enabled or disabled without ambiguity.

#### Acceptance Criteria

1. THE config key `quality_control.semantic_qc` SHALL be deleted from `configs/config.yaml` and from the `_QC_DEFAULTS` dict in `utils/config_utils.py`.
2. THE config key `quality_control.semantic_verification.enabled` SHALL exist in `_QC_DEFAULTS` with boolean default `false`.
3. THE config key `quality_control.source_text_verification.enabled` SHALL exist in `_QC_DEFAULTS` with boolean default `true`.
4. WHEN `source_text_verification.enabled` is `false`, THE source-text check SHALL be bypassed without evaluating any check logic, and the result SHALL be recorded as passed in `ctx.metrics_hierarchy["source_text_verification"]`.
5. WHEN `semantic_verification.enabled` is `false`, THE semantic source check SHALL be bypassed without evaluating any check logic, and the result SHALL be recorded as passed in `ctx.metrics_hierarchy["semantic_verification"]`.
6. THE config key `quality_control.semantic_verification.similarity_threshold` SHALL exist in `_QC_DEFAULTS` with float default `0.85`.
7. THE config key `quality_control.semantic_verification.max_sentences` SHALL exist in `_QC_DEFAULTS` with integer default `10000`.
8. THE config key `quality_control.semantic_verification.model_name` SHALL exist in `_QC_DEFAULTS` with string default `"BAAI/bge-base-en-v1.5"`; THE QC pipeline SHALL read and store this value but SHALL NOT call any function that loads or downloads the model.
9. THE config key `quality_control.semantic_verification.on_index_unavailable` SHALL exist in `_QC_DEFAULTS` with string default `"skip"`; the valid values are `"skip"`, `"fail"`, and `"degrade"`, and the pipeline SHALL pass this value to `SemanticSourceVerificationCheck` at construction time.
10. THE config key `quality_control.semantic_verification.extractor_agreement.enabled` SHALL exist in `_QC_DEFAULTS` with boolean default `false`.
11. THE config key `quality_control.semantic_verification.extractor_agreement.len_filter` SHALL exist in `_QC_DEFAULTS` with integer default `40`.
12. THE config key `quality_control.semantic_verification.extractor_agreement.max_examples` SHALL exist in `_QC_DEFAULTS` with integer default `10`.
13. IF `quality_control.task_quality_scaffold.enabled` is `true`, THE config key SHALL exist in `_QC_DEFAULTS` with boolean default `true`.
14. THE config key `quality_control.local_metrics.extraction_coverage_ratio_threshold` SHALL exist in `_QC_DEFAULTS` with the same numeric default previously held by `grobid_vs_native_ratio_threshold`.
15. THE config key `quality_control.local_metrics.grobid_vs_native_ratio_threshold` SHALL be deleted from `configs/config.yaml` and from `_QC_DEFAULTS`.
16. ALL of the following keys SHALL be present in `_QC_DEFAULTS`: `quality_control.semantic_verification.enabled`, `quality_control.semantic_verification.similarity_threshold`, `quality_control.semantic_verification.max_sentences`, `quality_control.semantic_verification.model_name`, `quality_control.semantic_verification.on_index_unavailable`, `quality_control.semantic_verification.extractor_agreement.enabled`, `quality_control.semantic_verification.extractor_agreement.len_filter`, `quality_control.semantic_verification.extractor_agreement.max_examples`, `quality_control.source_text_verification.enabled`, and `quality_control.local_metrics.extraction_coverage_ratio_threshold`.
17. THE parser SHALL NOT call `config.get("semantic_qc")`, `config.get("semantic_qc_threshold")`, `config.get("semantic_qc_max_sentences")`, or `config.get("semantic_qc_model")` anywhere in the codebase.
18. THE pipeline manifest read/write behavior (status values `complete`, `failed_qc_pipeline`, `failed_chunks`, `failed_chunk_<n>`) SHALL remain unchanged by this migration; no manifest key SHALL be added, removed, or renamed.

### Requirement 10: Define QC Output Hierarchy

**User Story:** As a developer consuming QC results, I want output organized under descriptive keys, so that downstream code does not map legacy names.

#### Acceptance Criteria

1. THE `QCBundle.metrics_hierarchy` dict SHALL contain exactly the top-level keys `"extraction_coverage"`, `"source_text_verification"`, and `"semantic_verification"` after `run_quality_control` completes.
2. THE value at `metrics_hierarchy["extraction_coverage"]` SHALL be a list of `ExtractionCoverageMetricRecord` instances produced by `ExtractionCoverageReport`.
3. THE value at `metrics_hierarchy["source_text_verification"]` SHALL be a list of `VerificationResult` instances produced by `SourceTextPresenceCheck`.
4. THE value at `metrics_hierarchy["semantic_verification"]` SHALL be a dict containing `VerificationResult` instances and, when extractor-agreement reporting is enabled, a nested `"extractor_agreement"` key holding the agreement report dict.
5. WHEN `semantic_verification.enabled` is `false`, THE `metrics_hierarchy["semantic_verification"]` value SHALL either be absent from the dict or be a dict containing a single `VerificationResult` with `status="skipped"`.
6. WHEN `semantic_verification.enabled` is `false`, THE parser SHALL NOT import or call any function from `sentence_transformers`, `faiss`, or `torch` through the QC code path.
7. WHEN `quality_control.semantic_verification.extractor_agreement.enabled` is `false`, THE `"extractor_agreement"` key SHALL either be absent from `metrics_hierarchy["semantic_verification"]` or be a dict with `status="skipped"`.
8. WHEN `quality_control.task_quality_scaffold.enabled` is `true`, THE scaffold dict returned by `build_task_quality_scaffold` SHALL include a top-level `"status"` key set to `"not_computed"` or `"scaffolded"` to prevent it from being interpreted as a failure by downstream consumers.

### Requirement 11: Preserve QC Private Helpers

**User Story:** As a developer migrating QC internals, I want directly relevant private helpers preserved, so that the QC boundary remains stable during the later TextProcessor migration.

#### Acceptance Criteria

1. THE private helper `_extract_branch_payload` SHALL remain defined in `quality_control/quality_control.py` with its current signature.
2. THE private helper `_build_native_page_texts` SHALL remain defined in `quality_control/quality_control.py` with its current signature.
3. THE private helper `_build_placeholder_sentence_store` SHALL remain defined in `quality_control/quality_control.py` with its current signature.
4. These helpers SHALL remain private by retaining the leading underscore prefix in their names.
5. These helpers SHALL NOT be moved to a `text_processing/` package by this migration.
6. These helpers SHALL NOT contain inline implementations of lexical matching, semantic matching, embedding computation, text normalization, or sentence segmentation; calling an injected dependency or an externally defined function that performs those operations is permitted.

### Requirement 12: Enforce QC/TextProcessor Separation

**User Story:** As a maintainer, I want the QC migration to be independent from the TextProcessor migration, so that the work can be implemented and reviewed first.

#### Acceptance Criteria

1. THIS migration SHALL NOT create a `text_processing/` package; the directory SHALL NOT exist after this migration.
2. THIS migration SHALL NOT delete or modify `utils/text_processor.py`.
3. THIS migration SHALL NOT delete or modify `pdf_extractor/utils/text_utils.py`.
4. THIS migration SHALL NOT delete or modify `pdf_extractor/utils/embedding_utils.py`.
5. THIS migration SHALL NOT move the functions `normalise_ws`, `normalise_full`, `exact_match_search`, `semantic_search`, `load_embedding_model`, `embed_query`, `l2_normalise`, `build_faiss_index`, or `build_sentence_store` from their current modules.
6. THIS migration SHALL NOT add an abstract base class or abstract methods to `TextProcessor` in `utils/text_processor.py`.
7. THIS migration SHALL NOT change the class hierarchy, constructor signature, or public method signatures of any `SentenceSegment` subclass.
8. THIS migration SHALL NOT change the interface or implementation of any normalizer, tokenizer, lexical matcher, semantic matcher, or embedding function.
9. QC tests for this phase SHALL use one of: a fake `LexicalMatcher` callable, a fake `SemanticMatcher` callable, a `MagicMock`, or the existing `pdf_extractor.utils` adapter wiring — never a real `TextProcessor` instance — for all matcher dependencies.
10. THE `tests/steering/` directory SHALL include a test file that uses AST-based analysis (consistent with the pattern in `tests/test_dependency_directions.py`) to verify that no `.py` file under `quality_control/checks/` contains an import of the `text_processing` package or of `utils.text_processor.TextProcessor`.

### Requirement 13: QC Tests

**User Story:** As a developer, I want tests for QC contracts and output, so that the QC migration is safe before TextProcessor work begins.

#### Acceptance Criteria

1. THE `tests/quality_control/` directory SHALL include at least one test file for each of: `SourceTextPresenceCheck` (in `test_qc_checks_source_text.py` or equivalent), `SemanticSourceVerificationCheck` (in `test_qc_checks_semantic_source.py` or equivalent), `ExtractorAgreementCheck` (in `test_qc_checks_extractor_agreement.py` or equivalent), `VerificationResult` model validation, bypass behavior for both check types, and all three `on_index_unavailable` modes.
2. THE tests SHALL include a test that sets `source_text_verification.enabled=false` in the config, calls the QC pipeline, and asserts that the lexical check was not invoked and that `metrics_hierarchy["source_text_verification"]` records a passing result.
3. THE tests SHALL include a test that sets `semantic_verification.enabled=false` in the config, imports `quality_control.checks.semantic_source`, and asserts that `sentence_transformers`, `faiss`, and `torch` are not present in `sys.modules` after the import.
4. THE tests SHALL include a test that constructs `SemanticSourceVerificationCheck(matcher=..., on_index_unavailable="skip")`, calls `run()` with an unavailable sentence store, and asserts the returned `VerificationResult.status == "unavailable"`.
5. THE tests SHALL include a test that constructs `SemanticSourceVerificationCheck(matcher=..., on_index_unavailable="fail")`, calls `run()` with an unavailable sentence store, and asserts that `RuntimeError` is raised.
6. THE tests SHALL include a test that constructs `SemanticSourceVerificationCheck(matcher=mock_degrade, on_index_unavailable="degrade")`, calls `run()` with an unavailable sentence store, asserts that `mock_degrade` was called, and asserts that a `WARNING`-level log record was emitted.
7. THE tests SHALL include tests that verify: (a) exact-only mode produces a complete report with `near_match_count=0`; (b) a mocked `semantic_matcher` returning a near-match increments `near_match_count`; (c) unmatched examples are capped at `max_examples`; (d) `agreement_rate` equals `(exact_match_count + near_match_count) / primary_sentence_count`.
8. THE tests SHALL assert that no function in `torch`, `sentence_transformers`, or `faiss` was called, using `unittest.mock.patch` or `patch.dict(sys.modules, ...)` to prevent any real model loading.
9. THE `tests/steering/` directory SHALL include a test file named `test_qc_textprocessor_separation.py` or equivalent.
10. THE separation test SHALL use AST parsing to inspect every `.py` file under `quality_control/checks/` and SHALL fail with a descriptive message if any file contains an import of `text_processing` or `utils.text_processor.TextProcessor`.
11. THE `tests/quality_control/` directory SHALL include a preservation test that constructs a `QCBundle`, runs `run_quality_control`, and asserts that `ctx.metrics_hierarchy` contains exactly the keys `"extraction_coverage"`, `"source_text_verification"`, and `"semantic_verification"`.
12. THE preservation test SHALL include a parametrized case that passes the same inputs to both `ExtractionCoverageReport` and the previous `LocalQCReport` (imported under an alias if needed) and asserts that the pass/fail boolean outcome is identical.
13. THE `tests/quality_control/` directory SHALL include a production-import test that imports `quality_control`, `quality_control.checks`, and `quality_control.builtin_impls` and asserts that none of `sentence_transformers`, `faiss`, or `torch` appear in `sys.modules` after the imports.
14. THE `tests/quality_control/` directory SHALL include pipeline-integration tests that assert: (a) `run_quality_control` completes without error when `semantic_verification.enabled=false`; (b) manifest status values are unchanged after the migration; (c) `metrics_hierarchy["semantic_verification"]["extractor_agreement"]` is absent or `status="skipped"` when `extractor_agreement.enabled=false`; (d) `build_task_quality_scaffold()` can be called and its return value serialized with `json.dumps()` without error.

### Requirement 14: QC Documentation

**User Story:** As a developer onboarding to QC, I want documentation to match the new QC package layout and config keys.

#### Acceptance Criteria

1. THE file `quality_control/README.md` SHALL contain a documented entry for each of `ExtractionCoverageReport`, `ExtractionCoverageMetricRecord`, `builtin_impls/`, and `checks/`, with each entry describing the symbol's role in the package.
2. THE same README SHALL include a prose description of the role of each of `SourceTextPresenceCheck`, `SemanticSourceVerificationCheck`, and `ExtractorAgreementCheck` (noting that `ExtractorAgreementCheck` is optional).
3. THE same README SHALL NOT contain any occurrence of `LocalQCReport`, `LocalQCMetricRecord`, `defaults/`, `local_metrics` as a metrics-hierarchy key, `exact_match` as a metrics-hierarchy key, `semantic_match` as a metrics-hierarchy key, or `semantic_qc` as a symbol name, directory name, config key, or metrics-hierarchy key.
4. THE file `.kiro/steering/config.md` SHALL contain a named entry for each of `source_text_verification`, `semantic_verification`, `extraction_coverage_ratio_threshold`, `on_index_unavailable`, `semantic_verification.model_name`, `semantic_verification.max_sentences`, and all sub-keys of `semantic_verification.extractor_agreement`, with each entry including its accepted values or type.
5. THE config steering file SHALL NOT contain any occurrence of `semantic_qc` as a config key, `grobid_vs_native_ratio_threshold` as a config key, `local_metrics` as a metrics-hierarchy key, `exact_match` as a metrics-hierarchy key, or `semantic_match` as a metrics-hierarchy key.
6. THE file `.kiro/steering/testing.md` SHALL name the two test files `test_qc_textprocessor_separation.py` and the QC output preservation test file, and SHALL describe their pass/fail contract (what each test checks and what constitutes a failure).
7. THE `CHANGELOG.md` SHALL include a QC migration entry that lists by name each renamed QC symbol, each renamed config key, each deleted package or directory name, and each added package or directory name introduced by this migration.
8. THE file `quality_control/README.md` SHALL NOT describe the internal backends, class hierarchy, config keys, or mocking patterns of `TextProcessor` or any `SentenceSegment` subclass.

### Requirement 15: QC Non-Goals

**User Story:** As a maintainer, I want explicit QC non-goals, so that this phase does not expand into text-processing or extraction behavior changes.

#### Acceptance Criteria

1. THIS phase SHALL NOT implement any extraction fallback path that selects a different backend output when the primary backend fails or produces low-quality output.
2. THIS phase SHALL NOT implement any logic that decides whether to activate PaddleOCR or PyMuPDF built-in OCR based on QC metrics.
3. THIS phase SHALL NOT implement any logic that replaces, overwrites, or supplements page content in a `Candidate` branch using output from a different extractor.
4. THIS phase SHALL NOT implement any logic that selects GROBID, PyMuPDF, pdfplumber, PaddleOCR, or OCR output as the preferred branch based on semantic agreement metrics.
5. THIS phase SHALL NOT implement any QC check that calls an LLM API to generate task-specific evaluation criteria.
6. THIS phase SHALL NOT implement any logic that reads spreadsheet-format hallucination annotations and applies them as QC checks inside the parser.
7. THIS phase SHALL NOT implement `route_row()` or `verify_row()` functions or their equivalents inside the parser core.
8. THIS phase SHALL NOT add `faiss`, `torch`, or `sentence-transformers` to the list of packages required for a default-configuration parser run (i.e., a run where `semantic_verification.enabled=false`).
9. THIS phase SHALL NOT introduce any import alias, re-export, or compatibility shim that makes the old names `LocalQCReport`, `LocalQCMetricRecord`, `defaults`, `local_metrics`, `exact_match`, `semantic_match`, or `semantic_qc` resolvable after the migration.
10. THIS phase SHALL NOT change the `field_index`, `field_name`, `domain_group`, `definition`, `reviewer_question`, `format`, or `categories_or_examples` fields in `configs/extraction_map.json`, nor the compact LLM output keys `i`, `v`, `loc`, or `c`.
