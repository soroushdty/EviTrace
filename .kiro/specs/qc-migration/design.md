# Design Document: QC Migration (`qc-migration`)

## Overview

This migration reorganises the `quality_control/` package in EviTrace so that:

1. QC verification logic lives in dedicated check classes (`quality_control/checks/`) that consume injected matcher dependencies — no inline matching logic, no `TextProcessor` imports.
2. A stable `VerificationResult` dataclass in `quality_control/models.py` provides a uniform result type for all check classes.
3. All QC classes, sub-packages, metrics-hierarchy keys, and config keys are renamed to descriptive names that do not carry legacy PDF-extractor-specific terminology.
4. The `run_quality_control` orchestrator is updated to wire the new check classes and use the new key names throughout.
5. Tests, documentation, and steering files are updated to match.

This is **Phase 1** of a two-phase migration. The TextProcessor migration (Phase 2) must not begin until every requirement here is complete. This phase deliberately does not create a `text_processing/` package, does not modify `utils/text_processor.py` or any `pdf_extractor/utils/` file, and does not move any matching or embedding function.

---

## Architecture

### Current state

```
quality_control/
├── __init__.py                  # exports LocalQCReport, LocalQCMetricRecord, defaults.*
├── local_metrics.py             # LocalQCReport — 8 heuristic Tier 1 checks
├── models.py                    # LocalQCMetricRecord, QCBundle, Candidate, …
├── quality_control.py           # run_quality_control — uses "local_metrics"/"exact_match"/"semantic_match"
├── defaults/                    # QualityReport, InterRaterReport, AdjudicationDecision
│   ├── __init__.py
│   ├── quality_report.py
│   ├── inter_rater_report.py
│   └── adjudication_decision.py
└── concerns/                    # injectable strategy objects (unchanged)
```

`utils/config_utils.py` `_QC_DEFAULTS` contains `quality_control.semantic_qc` and `quality_control.local_metrics.grobid_vs_native_ratio_threshold`.

### Target state

```
quality_control/
├── __init__.py                  # exports ExtractionCoverageReport, ExtractionCoverageMetricRecord,
│                                #   VerificationResult, checks.*, builtin_impls.*
├── local_metrics.py             # ExtractionCoverageReport (renamed from LocalQCReport)
├── models.py                    # ExtractionCoverageMetricRecord (renamed), VerificationResult (new), …
├── quality_control.py           # run_quality_control — uses new key names
├── checks/                      # NEW — QC check classes
│   ├── __init__.py              # exports SourceTextPresenceCheck, SemanticSourceVerificationCheck,
│   │                            #   ExtractorAgreementCheck, build_task_quality_scaffold
│   ├── source_text.py           # SourceTextPresenceCheck
│   ├── semantic_source.py       # SemanticSourceVerificationCheck
│   ├── extractor_agreement.py   # ExtractorAgreementCheck
│   └── task_quality.py          # build_task_quality_scaffold
├── builtin_impls/               # RENAMED from defaults/
│   ├── __init__.py              # same exports: QualityReport, InterRaterReport, AdjudicationDecision
│   ├── quality_report.py
│   ├── inter_rater_report.py
│   └── adjudication_decision.py
└── concerns/                    # unchanged
```

`utils/config_utils.py` `_QC_DEFAULTS` gains `quality_control.semantic_verification`, `quality_control.source_text_verification`, and `quality_control.local_metrics.extraction_coverage_ratio_threshold`; loses `quality_control.semantic_qc` and `quality_control.local_metrics.grobid_vs_native_ratio_threshold`.

### Dependency rules (unchanged)

`quality_control` must not import from `agents`, `pipeline`, or `pdf_extractor`. The new `quality_control/checks/` sub-package inherits this rule. `ExtractorAgreementCheck` may call block-oriented sentence extraction functions from `pdf_extractor` only if those functions are injected as dependencies — it must not import `pdf_extractor` at module level.

---

## Components and Interfaces

### 1. `quality_control/checks/` package

A new Python package containing four modules. All files in this package are subject to the following hard constraints:

- No import of `TextProcessor` by name or from `utils.text_processor`.
- No import from the `text_processing` package.
- No top-level import of `faiss`, `torch`, `sentence_transformers`, `spacy`, `scispacy`, `stanza`, or `wtpsplit`.
- No inline implementation of normalization, tokenization, embedding processing, sentence segmentation, lexical matching, or semantic matching.

#### `SourceTextPresenceCheck` (`checks/source_text.py`)

```python
@dataclass
class SourceTextPresenceCheck:
    check_name: ClassVar[str] = "source_text_presence"
    matcher: Callable  # injected; signature: (needle, full_text, page_texts, blocks) -> dict | None

    def run(
        self,
        needle: str,
        full_text: str,
        page_texts: dict,
        blocks: list,
    ) -> VerificationResult: ...
```

Behavior:
- Calls `self.matcher(needle, full_text, page_texts, blocks)`.
- If result is a non-`None` dict: `status="verified"`, `score=result.get("confidence", 1.0)` clamped to `[0.0, 1.0]` (default `1.0` when key absent or value ≥ 1.0), evidence populated from result dict.
- If result is `None`: `status="no_match"`, `score=0.0`, all six evidence keys set to `None`.

#### `SemanticSourceVerificationCheck` (`checks/semantic_source.py`)

```python
@dataclass
class SemanticSourceVerificationCheck:
    check_name: ClassVar[str] = "semantic_source_verification"
    matcher: Callable  # injected semantic-search dependency
    on_index_unavailable: str  # "skip" | "fail" | "degrade"

    def __post_init__(self) -> None:
        if self.on_index_unavailable not in {"skip", "fail", "degrade"}:
            raise ValueError(
                f"on_index_unavailable must be one of 'skip', 'fail', 'degrade'; "
                f"got {self.on_index_unavailable!r}"
            )

    def run(
        self,
        query: str,
        sentence_store: dict | None,
        embed_fn: Callable,
        threshold: float,
        page_texts: dict | None,
    ) -> VerificationResult: ...
```

A `sentence_store` is considered **unavailable** when it is `None`, `{}`, or lacks a `sentences` key with at least one entry.

Behavior by `on_index_unavailable`:
- `"skip"`: return `VerificationResult(status="unavailable", score=0.0, evidence={all None})`.
- `"fail"`: raise `RuntimeError("sentence store is unavailable")`.
- `"degrade"`: call `self.matcher` as lexical fallback, emit `WARNING` via `utils.logging_utils` logger, return `VerificationResult` with evidence from matcher result (or all-`None` if matcher returns `None`).

When sentence store is available:
- If matcher returns a candidate with `score >= threshold`: `status="candidate_match"`, `score=candidate.score`.
- If matcher returns no candidate meeting threshold: `status="no_match"`, `score=0.0`; if a below-threshold diagnostic score is available, store it in `details["below_threshold_score"]`.

Heavy optional imports (`sentence_transformers`, `faiss`, `torch`) must not appear at module top level.

#### `ExtractorAgreementCheck` (`checks/extractor_agreement.py`)

```python
@dataclass
class ExtractorAgreementCheck:
    exact_matcher: Callable   # injected
    semantic_matcher: Callable | None = None  # injected; None = exact-only mode

    def run(
        self,
        primary_blocks: list,
        candidate_blocks: list,
        config: dict,
    ) -> dict: ...
```

Behavior:
- Only runs when `quality_control.semantic_verification.extractor_agreement.enabled` is `true`.
- Discards candidate sentences shorter than `len_filter` (default 40 chars) before matching.
- Passes all candidate sentences through `exact_matcher` first; only unmatched sentences go to `semantic_matcher`.
- Calls `semantic_matcher` only when it is not `None` and the sentence has not been matched by `exact_matcher`.
- If `semantic_matcher` is `None` while semantic matching is requested (i.e., the semantic path is reached), raises `ImportError` identifying the missing dependency.
- Report dict keys: `primary_sentence_count`, `candidate_sentence_count`, `exact_match_count`, `near_match_count`, `unmatched_primary_count`, `unmatched_candidate_count`, `agreement_rate`, `semantic_threshold`, `examples` (with sub-keys `unmatched_primary`, `unmatched_candidate`, `near_matches`, each a list capped at `max_examples`).
- `agreement_rate = (exact_match_count + near_match_count) / primary_sentence_count` (or `0.0` when `primary_sentence_count == 0`).
- `semantic_threshold = 0.0` when `semantic_matcher is None`.
- Result stored in `ctx.metrics_hierarchy["semantic_verification"]["extractor_agreement"]`.
- Result must NOT influence `ctx.decision`, `ctx.reports`, `ctx.unified`, or any field read by the evidence bundle builder, validator, or LLM prompt assembler.

#### `build_task_quality_scaffold` (`checks/task_quality.py`)

```python
def build_task_quality_scaffold() -> dict: ...
```

Returns a JSON-serializable dict with:
- A placeholder entry for each of: `field_recall`, `critical_field_recall`, `evidence_validity`, `evidence_compactness`, `cost_reduction`, `manual_qc_rate`, `interobserver_agreement`, `pipeline_agreement`.
- Each placeholder has `status` set to `"scaffolded"` or `"not_computed"` and `value` set to `null`.
- A top-level `details` key with a non-empty string stating task-specific criteria are not active in this refactor.
- A top-level `status` key set to `"not_computed"` or `"scaffolded"`.
- No HTTP requests, no LLM API calls, no environment variable reads for credentials.
- When included in per-PDF output JSON, stored under key `"task_quality_scaffold"` (never `"semantic_qc"`).

### 2. `VerificationResult` dataclass (`quality_control/models.py`)

```python
@dataclass
class VerificationResult:
    check_name: str
    status: str       # "verified" | "candidate_match" | "no_match" | "skipped" | "unavailable"
    score: float      # [0.0, 1.0]; raises ValueError if outside range
    evidence: dict    # keys: found_sentence, page_index, prefix, suffix, block_bbox, span_bboxes
    details: dict     # free-form additional info

    def __post_init__(self) -> None:
        _VALID_STATUSES = {"verified", "candidate_match", "no_match", "skipped", "unavailable"}
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}; got {self.status!r}")
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score must be in [0.0, 1.0]; got {self.score}")
```

The six standard evidence keys (`found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`) must all be present when produced by source-verification checks; each is `None` when no evidence is available.

`models.py` must not define, export, or reference any attribute named `semantic_qc`, `exact_match`, or `semantic_match`.

### 3. Renamed classes

| Old name | New name | File |
|---|---|---|
| `LocalQCReport` | `ExtractionCoverageReport` | `quality_control/local_metrics.py` |
| `LocalQCMetricRecord` | `ExtractionCoverageMetricRecord` | `quality_control/models.py` |
| `_check_grobid_vs_native_ratio` | `_check_extraction_coverage_ratio` | `quality_control/local_metrics.py` |
| metric_name `"grobid_vs_native_ratio"` | `"extraction_coverage_ratio"` | `quality_control/local_metrics.py` |

All import sites in `quality_control/`, `pipeline/`, and `tests/quality_control/` must be updated. No compatibility shim or re-export alias for old names.

### 4. Renamed sub-package: `defaults/` → `builtin_impls/`

Create `quality_control/builtin_impls/` with identical content to `quality_control/defaults/`. Update all import sites. Delete `quality_control/defaults/`. The new `__init__.py` exports `QualityReport`, `InterRaterReport`, `AdjudicationDecision` from `quality_control.builtin_impls`.

### 5. Updated `run_quality_control` (`quality_control/quality_control.py`)

Key changes:
- Import from `quality_control.builtin_impls` instead of `quality_control.defaults`.
- Import `ExtractionCoverageReport` instead of `LocalQCReport`.
- Initialize `metrics_hierarchy` with keys `"extraction_coverage"`, `"source_text_verification"`, `"semantic_verification"` instead of `"local_metrics"`, `"exact_match"`, `"semantic_match"`.
- Read `config.get("quality_control", {}).get("semantic_verification", {}).get("enabled", False)` instead of `semantic_qc`.
- Wire `SourceTextPresenceCheck` for source-text verification (Tier 2 equivalent).
- Wire `SemanticSourceVerificationCheck` for semantic verification (Tier 3 equivalent), passing `on_index_unavailable` from config.
- Wire `ExtractorAgreementCheck` when `extractor_agreement.enabled` is `true`.
- Remove all literal strings `"Tier 1"`, `"Tier 2"`, `"Tier 3"` from docstrings and inline comments.
- Private helpers `_extract_branch_payload`, `_build_native_page_texts`, `_build_placeholder_sentence_store` remain in this file with their current signatures.

### 6. Updated `_QC_DEFAULTS` (`utils/config_utils.py`)

Additions:
```python
"quality_control": {
    ...
    "source_text_verification": {
        "enabled": True,
    },
    "semantic_verification": {
        "enabled": False,
        "similarity_threshold": 0.85,
        "max_sentences": 10000,
        "model_name": "BAAI/bge-base-en-v1.5",
        "on_index_unavailable": "skip",
        "extractor_agreement": {
            "enabled": False,
            "len_filter": 40,
            "max_examples": 10,
        },
    },
    "task_quality_scaffold": {
        "enabled": True,
    },
    "local_metrics": {
        ...
        "extraction_coverage_ratio_threshold": 0.6,  # renamed from grobid_vs_native_ratio_threshold
        # grobid_vs_native_ratio_threshold DELETED
    },
    # semantic_qc DELETED
}
```

### 7. Updated `quality_control/__init__.py`

- Replace `LocalQCReport` with `ExtractionCoverageReport`.
- Replace `LocalQCMetricRecord` with `ExtractionCoverageMetricRecord`.
- Add `VerificationResult` to exports.
- Import from `quality_control.builtin_impls` instead of `quality_control.defaults`.
- No old names remain in `__all__`.

---

## Data Models

### `VerificationResult` (new)

| Field | Type | Constraint |
|---|---|---|
| `check_name` | `str` | Non-empty string identifying the check |
| `status` | `str` | One of `"verified"`, `"candidate_match"`, `"no_match"`, `"skipped"`, `"unavailable"` |
| `score` | `float` | `[0.0, 1.0]`; `ValueError` if outside range |
| `evidence` | `dict` | Six standard keys when from source-verification checks |
| `details` | `dict` | Free-form; `"below_threshold_score"` key used by semantic check |

Standard evidence keys: `found_sentence`, `page_index`, `prefix`, `suffix`, `block_bbox`, `span_bboxes`.

### `ExtractionCoverageMetricRecord` (renamed from `LocalQCMetricRecord`)

Fields unchanged: `metric_name`, `computed_value`, `threshold`, `triggered`. The `metric_name` value `"grobid_vs_native_ratio"` becomes `"extraction_coverage_ratio"`.

### `QCBundle.metrics_hierarchy` (updated keys)

| Key | Value type | Populated by |
|---|---|---|
| `"extraction_coverage"` | `list[ExtractionCoverageMetricRecord]` | `ExtractionCoverageReport` |
| `"source_text_verification"` | `list[VerificationResult]` | `SourceTextPresenceCheck` |
| `"semantic_verification"` | `dict` containing `VerificationResult` instances and optionally `"extractor_agreement"` | `SemanticSourceVerificationCheck`, `ExtractorAgreementCheck` |

When `source_text_verification.enabled` is `false`, `metrics_hierarchy["source_text_verification"]` records a passing result (bypassed without evaluating check logic).

When `semantic_verification.enabled` is `false`, `metrics_hierarchy["semantic_verification"]` is either absent or contains a single `VerificationResult` with `status="skipped"`. No `sentence_transformers`, `faiss`, or `torch` import occurs through the QC code path.

When `extractor_agreement.enabled` is `false`, `metrics_hierarchy["semantic_verification"]["extractor_agreement"]` is either absent or a dict with `status="skipped"`.

### Manifest status values (unchanged)

`complete`, `failed_qc_pipeline`, `failed_chunks`, `failed_chunk_<n>` — no manifest key is added, removed, or renamed by this migration.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: QC checks/ package has no forbidden imports

*For any* `.py` file under `quality_control/checks/` (including `__init__.py`), AST-parsing that file must reveal no import of `utils.text_processor`, `TextProcessor` by name, the `text_processing` package, or any top-level import of `faiss`, `torch`, `sentence_transformers`, `spacy`, `scispacy`, `stanza`, or `wtpsplit`.

**Validates: Requirements 1.4, 1.5, 1.6, 12.10**

### Property 2: VerificationResult score is constrained to [0.0, 1.0]

*For any* float value `x`, constructing `VerificationResult(check_name="c", status="no_match", score=x, evidence={}, details={})` must raise `ValueError` when `x < 0.0` or `x > 1.0`, and must succeed when `0.0 <= x <= 1.0`.

**Validates: Requirements 2.4**

### Property 3: SourceTextPresenceCheck output contract

*For any* `needle`, `full_text`, `page_texts`, and `blocks`, calling `SourceTextPresenceCheck(matcher=m).run(needle, full_text, page_texts, blocks)` must satisfy:
- The injected `matcher` is called with exactly `(needle, full_text, page_texts, blocks)`.
- When `matcher` returns a non-`None` dict: `status="verified"`, `score` equals `dict.get("confidence", 1.0)` clamped to `[0.0, 1.0]`, and `evidence` contains exactly the six standard keys.
- When `matcher` returns `None`: `status="no_match"`, `score=0.0`, and all six evidence keys are present with value `None`.

**Validates: Requirements 2.5, 2.6, 3.4, 3.6, 3.7, 3.8**

### Property 4: SemanticSourceVerificationCheck on_index_unavailable modes

*For any* unavailable sentence store (i.e., `None`, `{}`, or missing `sentences` key), calling `SemanticSourceVerificationCheck(matcher=m, on_index_unavailable=mode).run(...)` must satisfy:
- When `mode="skip"`: returns `VerificationResult` with `status="unavailable"`, `score=0.0`, all six evidence keys `None`.
- When `mode="fail"`: raises `RuntimeError`.
- When `mode="degrade"`: calls the injected `matcher`, emits a `WARNING`-level log record via the `utils.logging_utils` logger, and returns a `VerificationResult` with evidence populated from the matcher result (or all-`None` if matcher returns `None`).

**Validates: Requirements 4.7, 4.8, 4.9**

### Property 5: SemanticSourceVerificationCheck threshold behavior

*For any* available sentence store and injected matcher, calling `run(query, sentence_store, embed_fn, threshold, page_texts)` must satisfy:
- When the matcher returns a candidate with `score >= threshold`: `status="candidate_match"` and `score` equals the candidate's score.
- When the matcher returns no candidate meeting `threshold`: `status="no_match"` and `score=0.0`; if a below-threshold diagnostic score is available, it is stored in `details["below_threshold_score"]`.

**Validates: Requirements 4.10, 4.11**

### Property 6: ExtractorAgreementCheck agreement_rate formula

*For any* `primary_sentence_count >= 0`, `exact_match_count >= 0`, and `near_match_count >= 0` (with `exact_match_count + near_match_count <= primary_sentence_count`), the `agreement_rate` in the report must equal `(exact_match_count + near_match_count) / primary_sentence_count` when `primary_sentence_count > 0`, and `0.0` when `primary_sentence_count == 0`.

**Validates: Requirements 5.12, 5.14**

### Property 7: ExtractorAgreementCheck examples cap

*For any* number of unmatched primary sentences, unmatched candidate sentences, or near-match pairs, the length of each list under `examples["unmatched_primary"]`, `examples["unmatched_candidate"]`, and `examples["near_matches"]` must not exceed `max_examples`.

**Validates: Requirements 5.17**

### Property 8: build_task_quality_scaffold is always JSON-serializable

*For any* call to `build_task_quality_scaffold()`, the return value must be serializable by `json.dumps()` without error and without a custom encoder.

**Validates: Requirements 7.5**

### Property 9: metrics_hierarchy contains exactly the three new keys after run_quality_control

*For any* valid `branches`, `document_id`, and `config` passed to `run_quality_control`, the returned `QCBundle.metrics_hierarchy` must contain exactly the keys `"extraction_coverage"`, `"source_text_verification"`, and `"semantic_verification"`, and must not contain `"local_metrics"`, `"exact_match"`, `"semantic_match"`, or `"semantic_qc"`.

**Validates: Requirements 8.9, 8.13, 10.1**

---

## Error Handling

### `VerificationResult` construction errors

`ValueError` is raised in `__post_init__` for invalid `status` values or `score` outside `[0.0, 1.0]`. Callers must not catch this silently — it indicates a programming error in the check class.

### `SemanticSourceVerificationCheck` constructor errors

`ValueError` is raised when `on_index_unavailable` is not one of the three valid values. This is a configuration error and must propagate to the caller.

### `SemanticSourceVerificationCheck.run()` with `on_index_unavailable="fail"`

`RuntimeError` is raised when the sentence store is unavailable. The pipeline must handle this by catching `RuntimeError` and recording a failure in `metrics_hierarchy["semantic_verification"]`.

### `ExtractorAgreementCheck` with missing `semantic_matcher`

`ImportError` is raised when the semantic matching path is reached but `semantic_matcher is None`. The pipeline must handle this by logging the error and recording a failure in `metrics_hierarchy["semantic_verification"]["extractor_agreement"]`.

### `_load_text_processor` failure

The existing fallback to `text_processor = None` is preserved. All code paths that use `text_processor` must guard with `if text_processor is not None`.

### Bypass behavior

When `source_text_verification.enabled` is `false`, the check is bypassed entirely — no matcher is called, no `VerificationResult` is constructed from check logic. A passing sentinel result is recorded in `metrics_hierarchy["source_text_verification"]`.

When `semantic_verification.enabled` is `false`, the semantic check is bypassed entirely — no `sentence_transformers`, `faiss`, or `torch` import occurs through the QC code path.

---

## Testing Strategy

### Unit tests (example-based)

New test files in `tests/quality_control/`:

| File | What it covers |
|---|---|
| `test_qc_checks_source_text.py` | `SourceTextPresenceCheck`: matcher called with correct args, verified/no_match outcomes, evidence keys, confidence passthrough |
| `test_qc_checks_semantic_source.py` | `SemanticSourceVerificationCheck`: all three `on_index_unavailable` modes, threshold behavior, constructor `ValueError`, no heavy imports |
| `test_qc_checks_extractor_agreement.py` | `ExtractorAgreementCheck`: exact-only mode, near-match mode, `agreement_rate` formula, examples cap, `ImportError` when semantic_matcher missing |
| `test_qc_checks_task_quality.py` | `build_task_quality_scaffold`: all placeholder keys present, JSON-serializable, no HTTP/LLM calls |
| `test_qc_verification_result.py` | `VerificationResult`: field validation, score range, status validation, evidence keys |
| `test_qc_pipeline_integration.py` | `run_quality_control` with new key names, bypass behavior, manifest status unchanged |

Existing test files to update:
- `test_quality_control_local_metrics.py` — update `LocalQCReport` → `ExtractionCoverageReport`, `LocalQCMetricRecord` → `ExtractionCoverageMetricRecord`, `grobid_vs_native_ratio` → `extraction_coverage_ratio`.
- `test_qc_models.py` — update `LocalQCMetricRecord` → `ExtractionCoverageMetricRecord`, add `VerificationResult` tests.
- `test_quality_control_pipeline.py` — update metrics_hierarchy key assertions.

### Property-based tests (Hypothesis)

Property tests use `@given` + `@settings(max_examples=100)` and live alongside unit tests in `tests/quality_control/`.

| Property | Test function | Strategy |
|---|---|---|
| P1: No forbidden imports in checks/ | `test_qc_textprocessor_separation.py` (AST, not Hypothesis) | Walk all .py files under checks/ |
| P2: score range | `test_qc_verification_result.py` | `st.floats()` outside and inside [0.0, 1.0] |
| P3: SourceTextPresenceCheck output contract | `test_qc_checks_source_text.py` | `st.text()`, `st.dictionaries()`, `st.lists()`, `st.one_of(st.none(), st.dictionaries(...))` |
| P4: on_index_unavailable modes | `test_qc_checks_semantic_source.py` | `st.sampled_from(["skip","fail","degrade"])`, unavailable store variants |
| P5: threshold behavior | `test_qc_checks_semantic_source.py` | `st.floats(0.0, 1.0)` for threshold and score |
| P6: agreement_rate formula | `test_qc_checks_extractor_agreement.py` | `st.integers(min_value=0)` for counts |
| P7: examples cap | `test_qc_checks_extractor_agreement.py` | `st.integers(min_value=0, max_value=50)` for item counts |
| P8: scaffold JSON-serializable | `test_qc_checks_task_quality.py` | Single call, no Hypothesis needed (deterministic) |
| P9: metrics_hierarchy keys | `test_qc_pipeline_integration.py` | `st.lists(st.builds(Candidate, ...))` |

### AST-based separation test

`tests/steering/test_qc_textprocessor_separation.py` — uses the same AST-walking pattern as `tests/test_dependency_directions.py`. Walks every `.py` file under `quality_control/checks/` and fails with a descriptive message if any file contains an import of `text_processing` or `utils.text_processor.TextProcessor`.

### Mocking conventions

- All matcher dependencies are `MagicMock` instances — never real `TextProcessor`.
- Heavy optional deps (`faiss`, `torch`, `sentence_transformers`) are patched via `patch.dict(sys.modules, {"faiss": None, "torch": None, "sentence_transformers": None})`.
- `pytestmark = pytest.mark.slow` for any test that would require real model loading (none expected in this phase).

### Preservation tests

`tests/quality_control/test_qc_pipeline_integration.py` includes:
- A parametrized case that passes the same inputs to both `ExtractionCoverageReport` and `LocalQCReport` (imported under an alias) and asserts the pass/fail boolean outcome is identical.
- A production-import test asserting that importing `quality_control`, `quality_control.checks`, and `quality_control.builtin_impls` does not cause `sentence_transformers`, `faiss`, or `torch` to appear in `sys.modules`.

### Tag format for property tests

```python
# Feature: qc-migration, Property N: <property_text>
```
