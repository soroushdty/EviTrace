# Design Document — test-coverage

## Overview

This design covers seven new test files that bring dedicated unit and property-based test coverage to `agents/openai/` (api_client, prompts) and five pipeline helper modules (manifest, extraction_map, extraction_report, pdf_processor, orchestrator). All tests run without real OpenAI, GROBID, or file-system side effects. Async functions are exercised with `asyncio.run` or `pytest-asyncio`. Property-based tests use Hypothesis `@given` / `@settings`.

---

## Architecture

### Test File Map

| Test file | Module under test | Location |
|---|---|---|
| `tests/agents/openai/test_api_client_async.py` | `agents/openai/api_client.py` | async retry, error handling, response parsing |
| `tests/agents/openai/test_api_client_cache_key.py` | `agents/openai/api_client.py` | `paper_cache_key` PBT |
| `tests/agents/openai/test_prompts_builders.py` | `agents/openai/prompts.py` | message builder unit tests |
| `tests/agents/openai/test_prompts_pbt.py` | `agents/openai/prompts.py` | `build_user_message` PBT |
| `tests/pipeline/test_manifest_io.py` | `pipeline/manifest.py` | read/write + round-trip PBT |
| `tests/pipeline/test_extraction_map_grouping.py` | `pipeline/extraction_map.py` | field grouping + lookup PBT |
| `tests/pipeline/test_extraction_report_qc.py` | `pipeline/extraction_report.py` | aggregation + CSV + PBT |
| `tests/pipeline/test_pdf_processor_helpers.py` | `pipeline/pdf_processor.py` | checkpointing + async helpers |
| `tests/pipeline/test_orchestrator_concurrency.py` | `pipeline/orchestrator.py` | concurrency + error handling |

> Note: the requirements specify seven new test files. The design groups `api_client` into two files (async tests + PBT) and `prompts` into two files (unit + PBT) to keep file sizes manageable, giving nine files total. The seven logical test areas from the requirements are fully covered.

---

## Module Import Strategy

### Problem

`agents/openai/api_client.py` and `pipeline/orchestrator.py` call `load_openai_config()` and `load_qc_config()` at **module import time**, which requires a real `config/config.yaml`. Tests must prevent this.

### Solution: importlib reload with patched config

```python
import importlib
import sys
from unittest.mock import patch, MagicMock

_FAKE_CONFIG = {
    "api_key": "test-key",
    "base_url": None,
    "chunk_model": "gpt-test",
    "synthesis_model": "gpt-test",
    "temperature": None,
    "prompt_cache_key_prefix": "test-prefix",
    "prompt_cache_retention": "",
    "max_retries": 3,
    "retry_base_delay": 0,   # zero delay so tests run fast
    "num_chunks": 3,
    "chunk_max_tokens": {1: 4096, 2: 4096, 3: 4096},
    "enable_cache_prewarm": False,
    "global_api_limit": 5,
    "pdf_concurrency": 1,
    "prewarm_synthesis_if_model_diff": False,
    "domain_to_chunk": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
}

def _import_api_client():
    """Import api_client with patched config and a fresh AsyncOpenAI mock."""
    for mod in list(sys.modules):
        if "api_client" in mod or "agents.openai" in mod:
            del sys.modules[mod]
    with patch("utils.config_utils.load_openai_config", return_value=_FAKE_CONFIG):
        import agents.openai.api_client as m
    return m
```

The same pattern applies to `pipeline/orchestrator.py` — both `load_openai_config` and `load_qc_config` are patched before the module is imported.

### prompts.py and pipeline helpers

`agents/openai/prompts.py`, `pipeline/manifest.py`, `pipeline/extraction_map.py`, `pipeline/extraction_report.py`, and `pipeline/pdf_processor.py` do **not** call config loaders at import time, so they can be imported directly. File-path constants (`MANIFEST_FILE`, `OUTPUT_DIR`, `QC_REPORT_FILE`) are patched via `unittest.mock.patch` at the point of use.

---

## Async Test Strategy

All async functions under test (`_call_api_with_retries`, `extract_chunk`, `warm_pdf_cache`, `_run_parallel_chunks`, `process_pdf`, `run_pipeline`) are exercised using one of two approaches:

### Option A — `asyncio.run` (preferred for isolated unit tests)

```python
import asyncio

def test_warm_pdf_cache_returns_true(mock_client):
    result = asyncio.run(warm_pdf_cache("pkg", asyncio.Semaphore(1)))
    assert result is True
```

### Option B — `pytest-asyncio` (preferred when the test itself needs `async` fixtures)

```python
import pytest

@pytest.mark.asyncio
async def test_run_pipeline_returns_results(mock_grobid, mock_process_pdf):
    from pipeline.orchestrator import run_pipeline
    results = await run_pipeline([Path("a.pdf"), Path("b.pdf")])
    assert len(results) == 2
```

`asyncio.sleep` is always patched to a no-op so retry tests complete instantly:

```python
with patch("asyncio.sleep", new_callable=AsyncMock):
    ...
```

---

## Mocking Strategy

### OpenAI AsyncOpenAI client

The module-level `_client` in `api_client.py` is replaced after import:

```python
import agents.openai.api_client as api_client_mod

mock_client = MagicMock()
mock_client.responses = MagicMock()
mock_client.responses.create = AsyncMock(return_value=_make_response("hello"))
api_client_mod._client = mock_client
```

`AsyncMock` is used for all coroutine attributes so `await mock_client.responses.create(...)` works correctly.

### GROBID and PyMuPDF (orchestrator tests)

```python
with patch("pipeline.orchestrator.extract_with_grobid") as mock_grobid, \
     patch("pipeline.orchestrator.extract_with_pymupdf") as mock_pymupdf:
    mock_grobid.return_value = ("<TEI/>", {})
    mock_pymupdf.return_value = ([], {})
    ...
```

### File I/O (manifest, output JSON, QC CSV)

`MANIFEST_FILE`, `OUTPUT_DIR`, and `QC_REPORT_FILE` are patched to `tmp_path`-based paths:

```python
def test_save_load_manifest(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    with patch("pipeline.manifest.MANIFEST_FILE", manifest_path):
        save_manifest({"paper1": {"status": "complete"}})
        result = load_manifest()
    assert result == {"paper1": {"status": "complete"}}
```

For `extraction_report`, both `OUTPUT_DIR` and `QC_REPORT_FILE` are patched:

```python
with patch("pipeline.extraction_report.OUTPUT_DIR", tmp_path), \
     patch("pipeline.extraction_report.QC_REPORT_FILE", tmp_path / "qc_report.csv"):
    generate_qc_report(results)
```

### extraction_map.py (EXTRACTION_MAP and config)

`EXTRACTION_MAP` is patched to a `tmp_path` JSON file, and `load_openai_config` is patched to return a controlled `domain_to_chunk` mapping:

```python
def _write_fake_map(tmp_path, fields):
    p = tmp_path / "extraction_map.json"
    p.write_text(json.dumps(fields), encoding="utf-8")
    return p

with patch("pipeline.extraction_map.EXTRACTION_MAP", fake_map_path), \
     patch("utils.config_utils.load_openai_config", return_value={"domain_to_chunk": {1: 1, 2: 2}}):
    result = load_chunk_fields()
```

### pdf_processor.py (OUTPUT_DIR and extract_chunk)

```python
with patch("pipeline.pdf_processor.OUTPUT_DIR", tmp_path):
    _save_pdf_output("paper1", fields)

with patch("pipeline.pdf_processor.extract_chunk", new_callable=AsyncMock) as mock_ec:
    mock_ec.return_value = [{"i": 3, "v": "val", "loc": [], "c": "h"}]
    ...
```

---

## Helper Fixtures and Factories

### `_make_response(text)` — builds a minimal OpenAI response mock

```python
def _make_response(text: str):
    resp = MagicMock()
    resp.output_text = text
    resp.usage = MagicMock(input_tokens=10, output_tokens=5, input_tokens_details=MagicMock(cached_tokens=0))
    return resp
```

### `_make_results(entries)` — builds a pipeline results list for extraction_report tests

```python
def _make_results(entries):
    """entries: list of (pdf_name, [(field_index, confidence, extracted_value)])"""
    return [
        {
            "pdf": pdf,
            "fields": [
                {
                    "field_index": fi,
                    "confidence": conf,
                    "extracted_value": val,
                    "domain_group": f"{fi}. Group",
                    "field_name": f"Field {fi}",
                    "evidence": "",
                }
                for fi, conf, val in fields
            ],
        }
        for pdf, fields in entries
    ]
```

### `_fake_qc_context(pdf_name, exact_text, tmp_path)` — minimal QCContext for pdf_processor tests

```python
from quality_control.models import QCContext, UnifiedRecord

def _fake_qc_context(pdf_name, exact_text="text", tmp_path=None):
    unified = UnifiedRecord(
        document_id=pdf_name,
        content={"exact_text": exact_text, "source_pdf_path": str(tmp_path / f"{pdf_name}.pdf") if tmp_path else ""},
    )
    return QCContext(branches=[], unified=unified)
```

---

## Data Models for Hypothesis Generators

### `st_json_manifest()` — strategy for manifest dicts

```python
from hypothesis import strategies as st

st_json_manifest = st.dictionaries(
    keys=st.text(min_size=1, max_size=50),
    values=st.one_of(
        st.text(max_size=100),
        st.integers(),
        st.booleans(),
        st.none(),
    ),
    max_size=20,
)
```

### `st_source_package()` — strategy for source_package strings

```python
st_source_package = st.text(min_size=1, max_size=500)
```

### `st_chunk_fields()` — strategy for chunk_fields lists

```python
st_chunk_fields = st.lists(
    st.fixed_dictionaries({
        "field_index": st.integers(min_value=1, max_value=62),
        "field_name": st.text(min_size=1, max_size=50),
        "definition": st.text(max_size=100),
    }),
    min_size=1,
    max_size=10,
)
```

### `st_result_entry()` — strategy for extraction report result entries

```python
st_confidence = st.sampled_from(["h", "m", "l", "nr"])

st_field_entry = st.fixed_dictionaries({
    "field_index": st.integers(min_value=1, max_value=62),
    "confidence": st_confidence,
    "extracted_value": st.one_of(st.just("nr"), st.text(min_size=1, max_size=50)),
    "domain_group": st.text(min_size=1, max_size=30),
    "field_name": st.text(min_size=1, max_size=30),
    "evidence": st.text(max_size=100),
})

st_result_entry = st.fixed_dictionaries({
    "pdf": st.text(min_size=1, max_size=30),
    "fields": st.lists(st_field_entry, min_size=0, max_size=10),
})
```

---

## Per-File Design

### 1. `tests/agents/openai/test_api_client_async.py`

Tests all async behaviors of `api_client.py` using `asyncio.run`. The module is imported fresh for each test class via `_import_api_client()` to avoid cross-test state pollution from module-level constants.

**Key test functions:**

- `test_rate_limit_retries_and_raises` — mock raises `RateLimitError` every call; assert `RuntimeError` raised; assert `asyncio.sleep` called with `[0, 0, 0]` (base_delay=0 in fake config).
- `test_required_false_returns_none` — mock raises every call; `required=False`; assert `None` returned.
- `test_retryable_exceptions_parametrized` — `@pytest.mark.parametrize` over `[APIStatusError, APIConnectionError, APITimeoutError]`; assert same retry behavior.
- `test_non_retryable_exception_reraises` — mock raises `ValueError`; assert `ValueError` propagated; mock called once.
- `test_extract_chunk_happy_path` — mock returns valid JSON; assert returned list has correct `field_index` values.
- `test_extract_chunk_validation_failure_retries` — mock returns invalid JSON every time; assert `RuntimeError` after `MAX_RETRIES`.
- `test_warm_pdf_cache_returns_true` — mock returns valid response; assert `True`.
- `test_warm_pdf_cache_failure_returns_false` — mock raises every call; assert `False`.
- `test_response_text_output_text_attr` — `_response_text` with `output_text="hello"`; assert `"hello"`.
- `test_response_text_output_list_message_block` — `_response_text` with `output=[{type:"message", content:[{type:"text", text:"hello"}]}]`; assert `"hello"`.
- `test_response_text_refusal_raises` — `_response_text` with `refusal="policy"`; assert `RuntimeError` with `"policy"` in message.

**Async pattern:** `asyncio.run(coroutine)` with `patch("asyncio.sleep", new_callable=AsyncMock)`.

---

### 2. `tests/agents/openai/test_api_client_cache_key.py`

Pure-function PBT for `paper_cache_key`. No async, no mocking of the client — only `load_openai_config` is patched at import time.

**Key test functions:**

- `test_cache_key_deterministic` — `@given(st_source_package)`; assert `paper_cache_key(s) == paper_cache_key(s)`.
- `test_cache_key_format` — `@given(st_source_package)`; assert result matches `r"^[^:]+:[0-9a-f]{16}$"`.
- `test_cache_key_distinct_inputs` — `@given(st.text(min_size=1), st.text(min_size=1))`; `assume(a != b)`; assert `paper_cache_key(a) != paper_cache_key(b)`.

---

### 3. `tests/agents/openai/test_prompts_builders.py`

Unit tests for `prompts.py`. No mocking needed — the module has no side effects at import time.

**Key test functions:**

- `test_system_prompt_contains_json_format` — assert `'{"extractions":[...]}' in SYSTEM_PROMPT`.
- `test_system_prompt_contains_cache_warmup_instruction` — assert `"CACHE WARMUP ONLY"` in `SYSTEM_PROMPT`.
- `test_system_prompt_contains_confidence_tiers` — assert all of `"h"`, `"m"`, `"l"`, `"nr"` appear in `SYSTEM_PROMPT`.
- `test_build_cache_warmup_message_starts_with_prefix` — assert `build_cache_warmup_message(pkg).startswith(_shared_paper_prefix(pkg))`.
- `test_build_user_message_no_prior_context` — assert `"PRIOR EXTRACTION RESULTS"` not in result.
- `test_build_user_message_with_prior_context` — assert `"PRIOR EXTRACTION RESULTS"` in result and `json.dumps(prior_context)` in result.
- `test_build_user_message_different_fields_same_prefix` — call twice with same `source_package` but different `chunk_fields`; assert both share identical leading prefix.

---

### 4. `tests/agents/openai/test_prompts_pbt.py`

PBT for `build_user_message` structural invariants.

**Key test functions:**

- `test_shared_prefix_invariant` — `@given(st_source_package, st_chunk_fields)`; assert message starts with `_shared_paper_prefix(source_package)`.
- `test_source_package_in_prefix` — `@given(st_source_package, st_chunk_fields, st.lists(st.text()))`; assert `source_package` appears within the shared prefix section of the message.

---

### 5. `tests/pipeline/test_manifest_io.py`

Unit tests and PBT for `pipeline/manifest.py`. All tests patch `pipeline.manifest.MANIFEST_FILE` to a `tmp_path` location.

**Key test functions:**

- `test_load_manifest_missing_file` — patch to non-existent path; assert `{}`.
- `test_load_manifest_valid_json` — write JSON to tmp file; assert parsed dict returned.
- `test_save_manifest_writes_valid_json` — save dict; read file with `json.loads`; assert equality.
- `test_save_load_round_trip` — save then load; assert equality (example-based).
- `test_manifest_round_trip_pbt` — `@given(st_json_manifest)`; save then load; assert equality.

---

### 6. `tests/pipeline/test_extraction_map_grouping.py`

Unit tests and PBT for `pipeline/extraction_map.py`. `EXTRACTION_MAP` is patched to a `tmp_path` JSON file; `load_openai_config` is patched to return a controlled `domain_to_chunk` mapping.

**Key test functions:**

- `test_build_field_lookup_size` — N fields → lookup has N entries.
- `test_build_field_lookup_keys_and_values` — each entry has `domain_group` and `field_name`.
- `test_load_chunk_fields_partition` — every field appears in exactly one chunk.
- `test_load_chunk_fields_correct_assignment` — each field lands in the chunk matching its domain prefix.
- `test_infer_chunk_field_ranges_missing_domain_raises` — field with unmapped domain → `ValueError` with domain in message.
- `test_build_field_lookup_pbt` — `@given(st.lists(st_field_dict, min_size=1, max_size=20))`; assert `len(lookup) == len(fields)` and all entries have required keys.
- `test_load_chunk_fields_partition_pbt` — `@given(st.lists(st_field_dict, min_size=1))`; assert every field_index appears in exactly one chunk.

---

### 7. `tests/pipeline/test_extraction_report_qc.py`

Unit tests and PBT for `pipeline/extraction_report.py`. `OUTPUT_DIR` and `QC_REPORT_FILE` are patched to `tmp_path`.

**Key test functions:**

- `test_collect_qc_data_flags_low_confidence` — results with `"l"` and `"nr"` fields; assert all appear in `flagged_rows`.
- `test_collect_qc_data_excludes_high_confidence` — results with `"h"` and `"m"` fields; assert none appear in `flagged_rows`.
- `test_collect_qc_data_not_reported_count` — results with known `"nr"` counts; assert `not_reported` matches.
- `test_collect_qc_data_sort_order` — assert `flagged_rows` sorted by `(field_index, pdf)`.
- `test_write_qc_csv_header` — call `_write_qc_csv`; read CSV; assert header columns match exactly.
- `test_generate_qc_report_row_count` — call `generate_qc_report`; read CSV; assert data row count equals flagged field count.
- `test_collect_qc_data_completeness_pbt` — `@given(st.lists(st_result_entry))`; assert `flagged_rows` contains exactly the `l`/`nr` fields and excludes `h`/`m` fields.
- `test_collect_qc_data_not_reported_count_pbt` — `@given(st.lists(st_result_entry))`; assert `not_reported[fi]` equals count of `extracted_value == "nr"` for each `fi`.

---

### 8. `tests/pipeline/test_pdf_processor_helpers.py`

Unit tests for `pipeline/pdf_processor.py` helper functions. `OUTPUT_DIR` is patched to `tmp_path`. `extract_chunk` and `warm_pdf_cache` are patched with `AsyncMock`.

**Key test functions:**

- `test_load_completed_result_complete_with_file` — write output JSON; set manifest `"complete"`; assert fields returned.
- `test_load_completed_result_not_complete` — manifest status `"failed_chunks"`; assert `None`.
- `test_load_completed_result_complete_missing_file` — manifest `"complete"` but no file; assert `None`.
- `test_save_pdf_output_round_trip` — save fields; read JSON; assert equality.
- `test_run_parallel_chunks_all_succeed` — mock `extract_chunk` returns valid results; assert list length equals chunk count.
- `test_run_parallel_chunks_one_fails` — mock one `extract_chunk` raises; assert `None` returned; assert manifest updated with `"failed_chunks"`.
- `test_process_pdf_cache_hit_skips_extract_chunk` — complete manifest + output file; assert `extract_chunk` never called.
- `test_save_pdf_output_round_trip_pbt` — `@given(st.lists(st_field_entry, min_size=0, max_size=20))`; save then load; assert equality.

**Async pattern:** `asyncio.run(coroutine)` for all async helper tests.

---

### 9. `tests/pipeline/test_orchestrator_concurrency.py`

Unit tests for `pipeline/orchestrator.py`. Both `load_openai_config` and `load_qc_config` are patched before import. `extract_with_grobid`, `extract_with_pymupdf`, `run_quality_control`, and `pdf_processor.process_pdf` are patched.

**Key test functions:**

- `test_build_qc_context_grobid_fallback` — `extract_with_grobid` raises; `failure_behavior="fallback"`; assert no exception; assert GROBID branch has empty payload.
- `test_build_qc_context_grobid_manifest_fail` — `extract_with_grobid` raises; `failure_behavior="manifest_fail"`; assert exception re-raised.
- `test_run_pipeline_all_succeed` — mock all dependencies; assert result list length equals PDF count.
- `test_run_pipeline_one_qc_failure` — mock `_build_qc_context` raises for one PDF; assert manifest updated with `"failed_qc_pipeline"`; assert other PDFs processed.
- `test_run_pipeline_concurrency_1` — track concurrent `_build_qc_context` calls with a counter; assert max concurrent == 1.
- `test_run_pipeline_cache_prewarm_false_propagated` — capture `runtime_config` passed to `process_pdf`; assert `enable_cache_prewarm is False`.

**Async pattern:** `asyncio.run(run_pipeline(...))` for all orchestrator tests.

---

## Error Handling Design

### Retry tests with zero delay

`RETRY_BASE_DELAY` is set to `0` in `_FAKE_CONFIG` so exponential-backoff tests complete instantly. `asyncio.sleep` is still patched to `AsyncMock` to prevent any accidental real sleeps and to allow call-count assertions.

### ValidationError in extract_chunk

`pipeline.validator.ValidationError` is imported directly in tests that need to trigger validation-failure retries. The mock response returns a JSON string that `validate_chunk_output` will reject (e.g., wrong field indices).

### Exception propagation in orchestrator

`_build_qc_context` is a synchronous function called via `asyncio.to_thread`. In tests it is patched directly on the `pipeline.orchestrator` module so the thread-dispatch is bypassed:

```python
with patch("pipeline.orchestrator._build_qc_context", side_effect=RuntimeError("GROBID down")):
    results = asyncio.run(run_pipeline([Path("a.pdf")]))
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: paper_cache_key determinism

*For any* non-empty `source_package` string, calling `paper_cache_key` twice with the same input SHALL return the same string.

**Validates: Requirements 2.1**

---

### Property 2: paper_cache_key format invariant

*For any* non-empty `source_package` string, `paper_cache_key` SHALL return a string matching the pattern `{prefix}:{16-hex-chars}` where the prefix is the configured `PROMPT_CACHE_KEY_PREFIX` stripped of surrounding whitespace.

**Validates: Requirements 2.2**

---

### Property 3: paper_cache_key collision resistance

*For any* two distinct non-empty `source_package` strings `a` and `b` where `a != b`, `paper_cache_key(a)` SHALL not equal `paper_cache_key(b)`.

**Validates: Requirements 2.3**

---

### Property 4: build_user_message shared-prefix invariant

*For any* `source_package` string and any `chunk_fields` list, the first `len(_shared_paper_prefix(source_package))` characters of `build_user_message(source_package, chunk_fields)` SHALL be byte-identical to `_shared_paper_prefix(source_package)`.

**Validates: Requirements 3.2, 3.3, 4.1**

---

### Property 5: build_user_message source_package presence in prefix

*For any* `source_package` string, any `chunk_fields` list, and any `prior_context` list, the `source_package` text SHALL appear within the shared prefix section of the message produced by `build_user_message`.

**Validates: Requirements 4.2**

---

### Property 6: manifest save/load round-trip

*For any* dict with string keys and JSON-serialisable values, calling `save_manifest` followed by `load_manifest` SHALL return a dict equal to the original.

**Validates: Requirements 5.3, 5.4, 6.1**

---

### Property 7: _build_field_lookup size and structure invariant

*For any* list of N field dicts (each with `field_index`, `domain_group`, `field_name`), `_build_field_lookup` SHALL return a dict with exactly N entries, each keyed by `field_index` and containing both `domain_group` and `field_name`.

**Validates: Requirements 7.1**

---

### Property 8: load_chunk_fields partition invariant

*For any* extraction map and `DOMAIN_TO_CHUNK` mapping, `load_chunk_fields` SHALL assign every field to exactly one chunk — no field appears in two chunks and no field is omitted.

**Validates: Requirements 7.2, 7.4**

---

### Property 9: _collect_qc_data completeness and exclusion

*For any* results list, `_collect_qc_data` SHALL include in `flagged_rows` every field whose `confidence` is in `{"l", "nr"}` and SHALL exclude every field whose `confidence` is in `{"h", "m"}`.

**Validates: Requirements 8.1, 8.2, 9.1**

---

### Property 10: _collect_qc_data not_reported count accuracy

*For any* results list, `_collect_qc_data` SHALL set `not_reported[fi]` equal to the total count of fields with `extracted_value == "nr"` and `field_index == fi` across all PDFs in the results list.

**Validates: Requirements 8.3, 9.2**

---

### Property 11: _save_pdf_output round-trip

*For any* fields list, `_save_pdf_output` SHALL write a JSON file such that reading and parsing that file returns a list equal to the original fields list.

**Validates: Requirements 10.4**

---

## Test Infrastructure Requirements

### conftest.py additions

No new `conftest.py` files are needed. The existing root-level `conftest.py` already inserts the project root into `sys.path`, which covers `agents.*` imports.

A new `tests/agents/__init__.py` and `tests/agents/openai/__init__.py` (empty files) are needed so pytest can collect from the new subdirectory.

### pytest-asyncio

If `pytest-asyncio` is not already installed, tests using `@pytest.mark.asyncio` require it. As a fallback, all async tests can use `asyncio.run(coroutine)` in a synchronous test function, which requires no additional plugins. The design defaults to `asyncio.run` for simplicity and uses `@pytest.mark.asyncio` only where `async` fixtures are genuinely needed.

### Hypothesis settings

All PBT tests use `@settings(max_examples=100)`. Tests that involve file I/O (manifest, pdf_processor) use `@settings(max_examples=50)` to keep the suite fast.

### Slow test marking

None of the seven new test files require slow marking — they mock all heavy dependencies. The `pytestmark = pytest.mark.slow` annotation is NOT applied to any of these files.
