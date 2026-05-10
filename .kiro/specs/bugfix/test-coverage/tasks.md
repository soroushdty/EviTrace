# Implementation Plan: test-coverage

## Overview

Create nine new test files (plus `__init__.py` infrastructure) that bring dedicated unit and property-based test coverage to `agents/openai/` and five `pipeline/` helper modules. All tests mock external dependencies (OpenAI, GROBID, file I/O) and run without credentials or running services. Async functions are exercised via `asyncio.run`. Property-based tests use Hypothesis `@given` / `@settings`.

---

## Tasks

- [x] 1. Create test infrastructure (`__init__.py` files and verify conftest)
  - Create `tests/agents/__init__.py` (empty) so pytest can collect from the new subdirectory
  - Create `tests/agents/openai/__init__.py` (empty)
  - Verify the root-level `conftest.py` already inserts the project root into `sys.path` (no changes needed if it does)
  - _Requirements: 12.1, 12.4_

- [x] 2. Implement `tests/agents/openai/test_api_client_async.py`
  - [x] 2.1 Write the `_import_api_client` helper and `_make_response` factory
    - Implement `_import_api_client()`: clears `sys.modules` of any `api_client`/`agents.openai` entries, patches `utils.config_utils.load_openai_config` with `_FAKE_CONFIG` (including `retry_base_delay: 0`), then imports and returns `agents.openai.api_client`
    - Implement `_make_response(text)`: returns a `MagicMock` with `output_text=text` and a `usage` mock
    - _Requirements: 12.1, 12.4_

  - [x] 2.2 Write retry and error-handling tests for `_call_api_with_retries`
    - `test_rate_limit_retries_and_raises`: mock raises `RateLimitError` every call; assert `RuntimeError` raised; assert `asyncio.sleep` called `MAX_RETRIES` times
    - `test_required_false_returns_none`: mock raises every call; `required=False`; assert `None` returned
    - `test_retryable_exceptions_parametrized`: `@pytest.mark.parametrize` over `[APIStatusError, APIConnectionError, APITimeoutError]`; assert same retry behaviour as `RateLimitError`
    - `test_non_retryable_exception_reraises`: mock raises `ValueError`; assert `ValueError` propagated immediately; mock called exactly once
    - Patch `asyncio.sleep` with `AsyncMock` throughout; use `asyncio.run(coroutine)`
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 2.3 Write `extract_chunk` and `warm_pdf_cache` tests
    - `test_extract_chunk_happy_path`: mock returns valid JSON on first attempt; assert returned list has correct `field_index` values
    - `test_extract_chunk_validation_failure_retries`: mock returns invalid JSON every time; assert `RuntimeError` after `MAX_RETRIES`
    - `test_warm_pdf_cache_returns_true`: mock returns valid response; assert `True`
    - `test_warm_pdf_cache_failure_returns_false`: mock raises every call; assert `False` without raising
    - Replace `api_client_mod._client` with a `MagicMock` whose `.responses.create` is `AsyncMock`
    - _Requirements: 1.5, 1.6, 1.7, 1.8_

  - [x] 2.4 Write `_response_text` tests
    - `test_response_text_output_text_attr`: response with `output_text="hello"`; assert `"hello"` returned
    - `test_response_text_output_list_message_block`: response with `output=[{type:"message", content:[{type:"text", text:"hello"}]}]`; assert `"hello"` returned
    - `test_response_text_refusal_raises`: response with `refusal="policy"`; assert `RuntimeError` with `"policy"` in message
    - _Requirements: 1.9, 1.10, 1.11_

- [x] 3. Implement `tests/agents/openai/test_api_client_cache_key.py`
  - [x] 3.1 Write PBT tests for `paper_cache_key`
    - Import `api_client` via `_import_api_client()` (reuse the same helper pattern, patching only `load_openai_config`)
    - `test_cache_key_deterministic`: `@given(st.text(min_size=1))`; `@settings(max_examples=100)`; assert `paper_cache_key(s) == paper_cache_key(s)`
    - `test_cache_key_format`: `@given(st.text(min_size=1))`; assert result matches `r"^[^:]+:[0-9a-f]{16}$"`
    - `test_cache_key_distinct_inputs`: `@given(st.text(min_size=1), st.text(min_size=1))`; `assume(a != b)`; assert `paper_cache_key(a) != paper_cache_key(b)`
    - **Property 1: paper_cache_key determinism** — Validates: Requirements 2.1
    - **Property 2: paper_cache_key format invariant** — Validates: Requirements 2.2
    - **Property 3: paper_cache_key collision resistance** — Validates: Requirements 2.3
    - _Requirements: 2.1, 2.2, 2.3_

- [x] 4. Implement `tests/agents/openai/test_prompts_builders.py`
  - [x] 4.1 Write unit tests for `SYSTEM_PROMPT` and message builders
    - Import `agents.openai.prompts` directly (no config patching needed)
    - `test_system_prompt_contains_json_format`: assert `'{"extractions":[...]}' in SYSTEM_PROMPT`
    - `test_system_prompt_contains_cache_warmup_instruction`: assert `"CACHE WARMUP ONLY"` in `SYSTEM_PROMPT`
    - `test_system_prompt_contains_confidence_tiers`: assert all of `"h"`, `"m"`, `"l"`, `"nr"` appear in `SYSTEM_PROMPT`
    - `test_build_cache_warmup_message_starts_with_prefix`: assert `build_cache_warmup_message(pkg).startswith(_shared_paper_prefix(pkg))`
    - `test_build_user_message_no_prior_context`: assert `"PRIOR EXTRACTION RESULTS"` not in result when `prior_context=None`
    - `test_build_user_message_with_prior_context`: assert `"PRIOR EXTRACTION RESULTS"` in result and `json.dumps(prior_context)` in result
    - `test_build_user_message_different_fields_same_prefix`: call twice with same `source_package` but different `chunk_fields`; assert both share identical leading prefix equal to `_shared_paper_prefix(source_package)`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 5. Implement `tests/agents/openai/test_prompts_pbt.py`
  - [x] 5.1 Write PBT tests for `build_user_message` structural invariants
    - Define `st_source_package = st.text(min_size=1, max_size=500)` and `st_chunk_fields` strategy (list of dicts with `field_index`, `field_name`, `definition`)
    - `test_shared_prefix_invariant`: `@given(st_source_package, st_chunk_fields)`; `@settings(max_examples=100)`; assert first `len(_shared_paper_prefix(source_package))` chars of message are byte-identical to `_shared_paper_prefix(source_package)`
    - `test_source_package_in_prefix`: `@given(st_source_package, st_chunk_fields, st.lists(st.text()))`; assert `source_package` appears within the shared prefix section of the message
    - **Property 4: build_user_message shared-prefix invariant** — Validates: Requirements 3.2, 3.3, 4.1
    - **Property 5: build_user_message source_package presence in prefix** — Validates: Requirements 4.2
    - _Requirements: 4.1, 4.2_

- [x] 6. Checkpoint — Ensure agents tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement `tests/pipeline/test_manifest_io.py`
  - [x] 7.1 Write unit tests for `load_manifest` and `save_manifest`
    - Patch `pipeline.manifest.MANIFEST_FILE` to `tmp_path / "manifest.json"` in each test
    - `test_load_manifest_missing_file`: patch to non-existent path; assert `{}` returned
    - `test_load_manifest_valid_json`: write JSON to tmp file; assert parsed dict returned
    - `test_save_manifest_writes_valid_json`: save dict; read file with `json.loads`; assert equality
    - `test_save_load_round_trip`: save then load; assert equality (example-based)
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 7.2 Write PBT round-trip test for manifest
    - Define `st_json_manifest` strategy: `st.dictionaries(keys=st.text(min_size=1, max_size=50), values=st.one_of(st.text(max_size=100), st.integers(), st.booleans(), st.none()), max_size=20)`
    - `test_manifest_round_trip_pbt`: `@given(st_json_manifest)`; `@settings(max_examples=50)`; save then load; assert equality
    - **Property 6: manifest save/load round-trip** — Validates: Requirements 5.3, 5.4, 6.1
    - _Requirements: 6.1_

- [x] 8. Implement `tests/pipeline/test_extraction_map_grouping.py`
  - [x] 8.1 Write unit tests for `_build_field_lookup` and `load_chunk_fields`
    - Write `_write_fake_map(tmp_path, fields)` helper that writes a JSON file and returns its path
    - Patch `pipeline.extraction_map.EXTRACTION_MAP` to the tmp file path; patch `utils.config_utils.load_openai_config` to return `{"domain_to_chunk": {...}}`
    - `test_build_field_lookup_size`: N fields → lookup has exactly N entries keyed by `field_index`
    - `test_build_field_lookup_keys_and_values`: each entry contains `domain_group` and `field_name`
    - `test_load_chunk_fields_partition`: every field appears in exactly one chunk's field list
    - `test_load_chunk_fields_correct_assignment`: each field lands in the chunk matching its `domain_group` prefix per `DOMAIN_TO_CHUNK`
    - `test_infer_chunk_field_ranges_missing_domain_raises`: field with unmapped domain prefix → `ValueError` with domain name in message
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 8.2 Write PBT tests for `_build_field_lookup` and `load_chunk_fields`
    - Define `st_field_dict` strategy: `st.fixed_dictionaries({"field_index": st.integers(min_value=1, max_value=62), "domain_group": st.text(min_size=1, max_size=30), "field_name": st.text(min_size=1, max_size=30)})`
    - `test_build_field_lookup_pbt`: `@given(st.lists(st_field_dict, min_size=1, max_size=20))`; assert `len(lookup) == len(fields)` and all entries have `domain_group` and `field_name`
    - `test_load_chunk_fields_partition_pbt`: `@given(st.lists(st_field_dict, min_size=1))`; assert every `field_index` appears in exactly one chunk
    - **Property 7: _build_field_lookup size and structure invariant** — Validates: Requirements 7.1
    - **Property 8: load_chunk_fields partition invariant** — Validates: Requirements 7.2, 7.4
    - _Requirements: 7.1, 7.2, 7.4_

- [x] 9. Implement `tests/pipeline/test_extraction_report_qc.py`
  - [x] 9.1 Write unit tests for `_collect_qc_data`, `_write_qc_csv`, and `generate_qc_report`
    - Write `_make_results(entries)` helper: builds a pipeline results list from `[(pdf_name, [(field_index, confidence, extracted_value)])]`
    - Patch `pipeline.extraction_report.OUTPUT_DIR` and `pipeline.extraction_report.QC_REPORT_FILE` to `tmp_path` locations
    - `test_collect_qc_data_flags_low_confidence`: results with `"l"` and `"nr"` fields; assert all appear in `flagged_rows`
    - `test_collect_qc_data_excludes_high_confidence`: results with `"h"` and `"m"` fields; assert none appear in `flagged_rows`
    - `test_collect_qc_data_not_reported_count`: results with known `"nr"` counts; assert `not_reported` dict matches expected counts
    - `test_collect_qc_data_sort_order`: assert `flagged_rows` sorted by `(field_index, pdf)` ascending
    - `test_write_qc_csv_header`: call `_write_qc_csv`; read CSV; assert header columns are exactly `["pdf", "field_index", "domain_group", "field_name", "extracted_value", "evidence", "confidence"]`
    - `test_generate_qc_report_row_count`: call `generate_qc_report`; read CSV; assert data row count equals number of flagged fields
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [x] 9.2 Write PBT tests for `_collect_qc_data` completeness
    - Define `st_confidence`, `st_field_entry`, and `st_result_entry` strategies as specified in the design
    - `test_collect_qc_data_completeness_pbt`: `@given(st.lists(st_result_entry))`; `@settings(max_examples=100)`; assert `flagged_rows` contains exactly the `l`/`nr` fields and excludes `h`/`m` fields
    - `test_collect_qc_data_not_reported_count_pbt`: `@given(st.lists(st_result_entry))`; assert `not_reported[fi]` equals count of `extracted_value == "nr"` for each `fi`
    - **Property 9: _collect_qc_data completeness and exclusion** — Validates: Requirements 8.1, 8.2, 9.1
    - **Property 10: _collect_qc_data not_reported count accuracy** — Validates: Requirements 8.3, 9.2
    - _Requirements: 9.1, 9.2_

- [x] 10. Checkpoint — Ensure pipeline helper tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Implement `tests/pipeline/test_pdf_processor_helpers.py`
  - [x] 11.1 Write unit tests for `_load_completed_result` and `_save_pdf_output`
    - Patch `pipeline.pdf_processor.OUTPUT_DIR` to `tmp_path` in each test
    - `test_load_completed_result_complete_with_file`: write output JSON to `tmp_path/<pdf>.extracted.json`; set manifest `status: "complete"`; assert fields list returned
    - `test_load_completed_result_not_complete`: manifest `status: "failed_chunks"`; assert `None` returned
    - `test_load_completed_result_complete_missing_file`: manifest `status: "complete"` but no output file; assert `None` returned
    - `test_save_pdf_output_round_trip`: call `_save_pdf_output("paper1", fields)`; read JSON from `tmp_path/paper1.extracted.json`; assert equality
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x] 11.2 Write async tests for `_run_parallel_chunks` and `process_pdf`
    - Patch `pipeline.pdf_processor.extract_chunk` with `AsyncMock`; use `asyncio.run(coroutine)`
    - `test_run_parallel_chunks_all_succeed`: mock `extract_chunk` returns valid results for each chunk; assert returned list length equals chunk count
    - `test_run_parallel_chunks_one_fails`: mock one `extract_chunk` raises; assert `None` returned; assert manifest updated with `status: "failed_chunks"`
    - `test_process_pdf_cache_hit_skips_extract_chunk`: write output JSON + set manifest `"complete"`; call `process_pdf`; assert `extract_chunk` never called
    - _Requirements: 10.5, 10.6, 10.7_

  - [x] 11.3 Write PBT round-trip test for `_save_pdf_output`
    - Define `st_field_entry` strategy (reuse from extraction_report or define locally)
    - `test_save_pdf_output_round_trip_pbt`: `@given(st.lists(st_field_entry, min_size=0, max_size=20))`; `@settings(max_examples=50)`; save then load; assert equality
    - **Property 11: _save_pdf_output round-trip** — Validates: Requirements 10.4
    - _Requirements: 10.4_

- [x] 12. Implement `tests/pipeline/test_orchestrator_concurrency.py`
  - [x] 12.1 Write the orchestrator import helper and GROBID fallback tests
    - Implement `_import_orchestrator()`: clears `sys.modules` of orchestrator/pipeline entries, patches both `utils.config_utils.load_openai_config` and `utils.config_utils.load_qc_config` with fake configs, then imports and returns `pipeline.orchestrator`
    - `test_build_qc_context_grobid_fallback`: patch `extract_with_grobid` to raise; set `failure_behavior="fallback"` in QC config; assert no exception raised; assert GROBID branch has empty payload
    - `test_build_qc_context_grobid_manifest_fail`: patch `extract_with_grobid` to raise; set `failure_behavior="manifest_fail"`; assert exception re-raised
    - _Requirements: 11.1, 11.2, 12.3_

  - [x] 12.2 Write `run_pipeline` result collection and error-handling tests
    - Patch `pipeline.orchestrator._build_qc_context`, `pipeline.orchestrator.process_pdf`, `pipeline.orchestrator.extract_with_grobid`, `pipeline.orchestrator.extract_with_pymupdf`
    - `test_run_pipeline_all_succeed`: mock all dependencies succeed for all PDFs; assert result list length equals PDF count; use `asyncio.run(run_pipeline(...))`
    - `test_run_pipeline_one_qc_failure`: mock `_build_qc_context` raises `RuntimeError` for one PDF; assert manifest updated with `status: "failed_qc_pipeline"` for that PDF; assert other PDFs processed normally
    - _Requirements: 11.3, 11.4_

  - [x] 12.3 Write concurrency and config-propagation tests
    - `test_run_pipeline_concurrency_1`: track concurrent `_build_qc_context` calls with a shared counter and `asyncio.Semaphore`; call `run_pipeline` with `pdf_concurrency=1`; assert max concurrent calls == 1
    - `test_run_pipeline_cache_prewarm_false_propagated`: capture `runtime_config` passed to `process_pdf`; call with `enable_cache_prewarm=False`; assert `runtime_config["enable_cache_prewarm"] is False`
    - _Requirements: 11.5, 11.6_

- [x] 13. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- `tests/agents/__init__.py` and `tests/agents/openai/__init__.py` must be created before any agents test files are collected by pytest
- All async tests use `asyncio.run(coroutine)` — no `pytest-asyncio` plugin required
- `asyncio.sleep` is always patched to `AsyncMock` in retry tests so they complete instantly
- `_FAKE_CONFIG` sets `retry_base_delay: 0` so exponential-backoff tests run without real delays
- No test file applies `pytestmark = pytest.mark.slow` — all heavy dependencies are mocked
- PBT tests for file I/O (manifest, pdf_processor) use `@settings(max_examples=50)`; all others use `@settings(max_examples=100)`

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "4.1", "7.1", "8.1", "9.1", "11.1", "12.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "5.1", "7.2", "8.2", "9.2", "11.2", "12.2"] },
    { "id": 3, "tasks": ["3.1", "11.3", "12.3"] }
  ]
}
```
