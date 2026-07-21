# Implementation Plan: Token-Efficient Extraction

## Overview

This plan implements a token-efficiency layer for the EviTrace extraction pipeline across four new modules (`telemetry.py`, `deterministic_merge.py`, `token_budget.py`, `token_report.py`) and modifications to existing modules (`api_client.py`, `prompts.py`, `pdf_processor.py`, `evidence_index.py`, `orchestrator.py`, `configs/config.yaml`). The implementation proceeds bottom-up: data models and pure functions first, then integration into the pipeline, then reporting and regression tests.

## Tasks

- [x] 1. Create telemetry module with data models and collector
  - [x] 1.1 Create `src/agents/openai/telemetry.py` with TelemetryRecord, PromptFingerprint, and StageSummary dataclasses
    - Define `PromptFingerprint` dataclass with `stable_prefix_hash: str` (16 hex chars) and `prompt_version: str` (max 64 chars)
    - Define `TelemetryRecord` dataclass with all fields: stage, model, timestamp, input_tokens, output_tokens, cached_input_tokens, uncached_input_tokens, total_tokens, prompt_fingerprint, optional field_index_start/end, domain_group, repair_attempt, error_type
    - Define `StageSummary` dataclass with stage, totals, request_count, mean_cache_rate
    - Implement `compute_prompt_fingerprint(stable_prefix: str, prompt_version: str) -> PromptFingerprint` using SHA-256 truncated to 16 hex chars
    - _Requirements: 1.1, 1.2, 1.5, 8.1_

  - [x] 1.2 Implement `TelemetryCollector` class in `src/agents/openai/telemetry.py`
    - Thread-safe collector using a list with lock protection
    - `record(record: TelemetryRecord) -> None` — append a record
    - `stage_summaries() -> list[StageSummary]` — aggregate per-stage totals and compute mean_cache_rate
    - `all_records() -> list[TelemetryRecord]` — return all records
    - `top_n_expensive(n: int = 5) -> list[TelemetryRecord]` — return top N by total_tokens descending
    - `check_cache_diagnostics(threshold: float = 50.0) -> None` — warn if any stage with ≥3 requests has cache rate below threshold
    - `check_prefix_drift() -> None` — warn if same stage+prompt_version produces different stable_prefix_hash values
    - _Requirements: 1.4, 1.6, 8.3, 8.4_

  - [x]* 1.3 Write property tests for telemetry in `tests/src/agents/openai/test_telemetry_properties.py`
    - **Property 1: Telemetry record completeness and uncached token invariant**
    - **Property 2: Stage summary aggregation correctness**
    - **Property 16: Prompt fingerprint correctness**
    - **Property 17: Cache diagnostics warning fires below threshold**
    - **Property 18: Prefix drift detection**
    - **Validates: Requirements 1.1, 1.4, 1.5, 8.1, 8.3, 8.4**

  - [x]* 1.4 Write unit tests for telemetry in `tests/src/agents/openai/test_telemetry.py`
    - Test stage labeling for extraction_chunk, synthesis, validation_repair, cache_warmup, finalization (Req 1.2, 1.3)
    - Test repair telemetry includes attempt number and error_type (Req 6.5)
    - Test fingerprint inclusion in TelemetryRecord (Req 8.2)
    - Test graceful handling of missing usage fields (Req 1.6)
    - _Requirements: 1.2, 1.3, 1.6, 6.5, 8.2_

- [ ] 2. Implement deterministic merge module
  - [x] 2.1 Create `src/pipeline/deterministic_merge.py` with MergeResult dataclass and merge logic
    - Define `MergeResult` dataclass with `merged_fields: list[dict]`, `conflicts: list[int]`, `skipped_synthesis: bool`
    - Implement `normalize_value(value: str | None) -> str | None` — strip + collapse internal whitespace
    - Implement `deterministic_merge(chunk_results: list[list[dict]], total_fields: int = 62) -> MergeResult`
    - Rules: all agree after normalization → lowest-chunk value; single provider → use it; all null/empty → "nr"; disagreement → conflict
    - Evidence_ID deduplication: union of unique IDs sorted ascending
    - Confidence resolution: h > m > l > nr (select highest)
    - Output must be order-independent (same result regardless of chunk_results permutation)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 4.3_

  - [x]* 2.2 Write property tests for deterministic merge in `tests/src/pipeline/test_deterministic_merge_properties.py`
    - **Property 8: Deterministic merge is order-independent (confluence)**
    - **Property 9: Non-conflicting fields merge without LLM**
    - **Property 10: Evidence_ID deduplication produces sorted unique union**
    - **Property 11: Confidence resolution selects highest label**
    - **Property 12: Synthesis candidate limiting**
    - **Validates: Requirements 5.7, 5.1, 5.5, 5.6, 4.3, 5.3, 5.4, 4.7**

  - [ ]* 2.3 Write unit tests for deterministic merge in `tests/src/pipeline/test_deterministic_merge.py`
    - Test zero-candidate fields get "nr" confidence (Req 4.5, 5.2)
    - Test synthesis output schema conformance with compact keys (Req 4.6)
    - Test synthesis prompt excludes full evidence (Req 4.1)
    - Test single-candidate with no conflict skips synthesis (Req 4.3)
    - Test max 5 candidates per conflicting field (Req 4.7)
    - _Requirements: 4.1, 4.3, 4.5, 4.6, 4.7, 5.2_

- [ ] 3. Implement token budget module
  - [ ] 3.1 Create `src/pipeline/token_budget.py` with estimation, budget checking, and mitigation
    - Implement `estimate_tokens(text: str) -> int` — `len(text) // 4`
    - Define `BudgetCheckResult` dataclass with within_budget, estimated_tokens, budget_limit, stage, top_sections
    - Implement `check_budget(prompt_text: str, stage: str, budgets: dict[str, int]) -> BudgetCheckResult`
    - Define `TokenBudgetExceededError` exception with stage, estimated, budget, top_sections
    - Implement `apply_mitigation(prompt_parts: dict[str, str], stage: str, budget: int, config: dict) -> tuple[str, list[str]]`
    - Mitigation order: (a) evidence pruning, (b) request splitting, (c) rejection
    - Implement `load_budgets(config: dict) -> dict[str, int]` — validate config values, use defaults for invalid entries
    - Default budgets: extraction_chunk=100000, validation_repair=20000, synthesis=120000, cache_warmup=10000
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 3.2 Write property tests for token budget in `tests/src/pipeline/test_token_budget_properties.py`
    - **Property 15: Token estimation is chars divided by 4**
    - **Property 21: Evidence pruning preserves high-confidence references**
    - **Property 22: Budget mitigation ordering**
    - **Validates: Requirements 7.1, 9.4, 7.2**

  - [ ]* 3.3 Write unit tests for token budget in `tests/src/pipeline/test_token_budget.py`
    - Test default budget values loaded correctly (Req 7.5)
    - Test invalid config fallback with warning (Req 7.6)
    - Test synthesis conflict-only fallback when over budget (Req 7.3)
    - Test budget exceeded warning format includes stage, estimate, budget, top-3 sections (Req 7.4)
    - _Requirements: 7.3, 7.4, 7.5, 7.6_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement token report module
  - [ ] 5.1 Create `src/pipeline/token_report.py` with TokenReport dataclass and report generation
    - Define `TokenReport` dataclass with all fields: totals, rates, per_stage, top_5_expensive, telemetry_records, delta, status
    - Implement `generate_token_report(collector: TelemetryCollector, output_dir: Path) -> TokenReport`
    - Compute overall_cache_rate = total_cached / total_input (handle zero division)
    - Compute output_to_input_ratio = total_output / total_input (handle zero division)
    - Include per-stage breakdown from collector.stage_summaries()
    - Include top 5 expensive requests from collector.top_n_expensive(5)
    - Include raw telemetry records as dicts
    - Implement delta comparison: if prior `token_report.json` exists, compute cache_rate_change, avg_uncached_per_request_change, total_tokens_change
    - Handle telemetry unavailable case: write status="telemetry_unavailable"
    - Write JSON to `output_dir / "token_report.json"`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [ ]* 5.2 Write property tests for token report in `tests/src/pipeline/test_token_report_properties.py`
    - **Property 19: Token report sum invariant**
    - **Property 20: Token report delta correctness**
    - **Validates: Requirements 9.5, 10.2, 10.3, 10.5**

  - [ ]* 5.3 Write unit tests for token report in `tests/src/pipeline/test_token_report.py`
    - Test file written to output directory (Req 10.1)
    - Test raw + aggregated both present when telemetry available (Req 10.4)
    - Test telemetry unavailable status output (Req 10.6)
    - Test delta comparison with prior report (Req 10.5)
    - _Requirements: 10.1, 10.4, 10.5, 10.6_

- [ ] 6. Modify prompt construction for stable prefix and evidence ordering
  - [ ] 6.1 Update `src/agents/openai/prompts.py` for system prompt caching and stable serialization
    - Cache `get_system_prompt()` result as module-level singleton (same object reference on every call)
    - Ensure evidence items in `source_package` are serialized sorted by Evidence_ID ascending
    - Ensure field definitions in extraction map are sorted by `field_index` ascending
    - Exclude runtime metadata (timestamps, run IDs, chunk numbers, PDF file names) from stable prefix
    - Implement `compute_stable_prefix(system_prompt: str, evidence_package: str, rules: str) -> str` helper for fingerprinting
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 2.7_

  - [ ] 6.2 Update `src/pipeline/evidence_index.py` for deterministic Evidence_IDs and stable serialization
    - Assign Evidence_IDs using positional scheme: S000001, T000001, F000001 (type prefix + zero-padded 6-digit counter)
    - Serialize evidence items sorted by Evidence_ID in ascending lexicographic order
    - Implement cache reuse: check `{paper_id}_{pdf_hash}.evidence.json` existence and PDF hash match before re-parsing
    - Ensure `build_paper_evidence_package()` respects `max_evidence_items_per_chunk` and `max_evidence_chars_per_chunk` limits
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 6.3 Write property tests for prompt stability in `tests/src/agents/openai/test_prompts_stability_properties.py`
    - **Property 3: Stable prefix byte-identity across chunk calls**
    - **Property 4: Evidence serialization sort stability**
    - **Property 5: Field definitions ordered by field_index**
    - **Property 6: Evidence_ID determinism**
    - **Property 7: Evidence selection respects configured limits**
    - **Property 13: Compact synthesis snippet truncation**
    - **Property 14: Repair prompt is smaller than original**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.6, 3.1, 3.2, 3.3, 4.2, 6.2**

  - [ ]* 6.4 Write unit tests for evidence index stability in `tests/src/pipeline/test_evidence_index_stability.py`
    - Test cache reuse with matching PDF hash (Req 3.5)
    - Test Evidence_ID in loc field output (Req 3.4)
    - Test runtime metadata excluded from prefix (Req 2.4)
    - Test system prompt caching returns same object reference (Req 2.7)
    - _Requirements: 2.4, 2.7, 3.4, 3.5_

- [ ] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Integrate telemetry and budget into API client and pipeline
  - [ ] 8.1 Update `src/agents/openai/api_client.py` to emit TelemetryRecords after each API call
    - After each OpenAI API response, create a TelemetryRecord with usage data, stage label, and prompt fingerprint
    - Pass stage label and field range metadata from caller context
    - Handle missing `usage` field gracefully: log warning, continue processing (Req 1.6)
    - Include prompt_fingerprint computed from stable prefix hash and prompt_version from config
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6_

  - [ ] 8.2 Update `src/pipeline/pdf_processor.py` to integrate deterministic merge, token budget, and repair telemetry
    - Insert `deterministic_merge()` call before synthesis — skip synthesis when `skipped_synthesis=True`
    - Apply `check_budget()` before dispatching prompts; call `apply_mitigation()` when over budget
    - Construct compact synthesis input: only conflicting fields with candidate records (field_index, field_name, value, confidence, Evidence_IDs, 200-char snippet)
    - Implement Repair_Prompt construction: validation errors + affected field definitions + invalid output fragment only
    - Emit repair telemetry with stage="validation_repair", attempt number, and error_type
    - Record failed chunks with metadata (chunk number, last error, error type, attempt count) after max repair attempts
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2_

  - [ ] 8.3 Update `src/pipeline/orchestrator.py` to invoke token report generation after run completion
    - After `run_pipeline` completes, call `generate_token_report(collector, output_dir)`
    - Call `collector.check_cache_diagnostics()` and `collector.check_prefix_drift()` before report generation
    - Write aggregate summary to run log (Req 1.7)
    - Make per-request TelemetryRecords available in audit package (Req 1.7)
    - _Requirements: 1.7, 10.1_

  - [ ] 8.4 Update `configs/config.yaml` with token_budgets and cache_diagnostics sections
    - Add `token_budgets` key with defaults: extraction_chunk=100000, validation_repair=20000, synthesis=120000, cache_warmup=10000
    - Add `cache_diagnostics.threshold: 50` key
    - Register new top-level keys in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `src/utils/config_utils.py` if needed
    - _Requirements: 7.5, 8.6_

- [ ] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Implement token-efficiency regression tests
  - [ ] 10.1 Create `tests/src/pipeline/test_token_efficiency_regression.py` with fixture-based regression tests
    - Test estimated uncached input tokens per request ≤ 5000 baseline threshold (Req 9.1)
    - Test byte-level longest common prefix ratio ≥ 90% between stable prefixes (Req 9.2)
    - Test no synthesis model call when all fields non-conflicting (Req 9.3)
    - Test high-confidence Evidence_IDs preserved after evidence pruning (Req 9.4)
    - Test token_report.json conforms to schema and per-stage sums equal overall totals (Req 9.5)
    - Test failure output includes measured value, threshold, and breaching component (Req 9.6)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [ ] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (22 properties across 5 test files)
- Unit tests validate specific examples, edge cases, and integration points
- Regression tests guard against future changes increasing token usage beyond acceptable thresholds
- All new modules follow existing project conventions: dataclasses, asyncio, `get_logger(__name__)`, lazy imports for heavy deps
- Config additions must be registered in `_ALL_KNOWN_TOP_LEVEL_KEYS` in `src/utils/config_utils.py`

## Implementation Notes

- `tests/src/pipeline/test_manifest_resume_properties.py::test_property_16_is_output_valid_rejects_corrupt_files` and `test_property_16_corrupt_output_treated_as_absent` fail on a clean checkout unrelated to this feature (confirmed at commit e261fad, before any token-efficient-extraction test files existed). Hypothesis's local `.hypothesis/` example cache found a minimal falsifying case (`pdf_name='0'`, `corrupt_content='0'`) showing `_is_output_valid()` treats the bare JSON literal `"0"` as valid rather than corrupt. Pre-existing bug in manifest-resume logic, out of scope for this spec — disregard at "Checkpoint - Ensure all tests pass" gates (tasks 4, 7, 9, 11) unless a new full-suite run introduces additional failures beyond these two.
- Task 2.1's `deterministic_merge()`: the "all agree after normalization" case emits the **normalized** value as canonical `v`, not a raw pre-normalization string tied to chunk position. Requirement 5.1's literal text ("value from the lowest-indexed chunk") is superseded here by the stronger, unconditional order-independence requirement (5.7 / Property 8) — a positional raw-string tie-break cannot be order-independent when two chunks agree post-normalization but differ in raw whitespace. Same code path also normalizes single-provider fields (Req 5.5). Downstream tasks (8.2 integration) should expect `merged_fields` values to always be whitespace-normalized, never raw chunk output verbatim.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.2", "2.3", "3.2", "3.3"] },
    { "id": 2, "tasks": ["1.4", "5.1"] },
    { "id": 3, "tasks": ["5.2", "5.3", "6.1", "6.2"] },
    { "id": 4, "tasks": ["6.3", "6.4"] },
    { "id": 5, "tasks": ["8.1", "8.2", "8.4"] },
    { "id": 6, "tasks": ["8.3"] },
    { "id": 7, "tasks": ["10.1"] }
  ]
}
```
