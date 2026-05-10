# Requirements Document

## Introduction

EviTrace currently has no dedicated unit tests for `agents/openai/` (api_client, prompts) or `pipeline/orchestrator.py`, and several pipeline helper modules (`extraction_map.py`, `extraction_report.py`, `manifest.py`, `pdf_processor.py`) lack systematic test coverage. This feature adds seven new test files that cover all non-trivial logic in these modules, including async API calls with retry/error-handling, pure-function prompt builders, idempotent manifest I/O, extraction-map field grouping, QC report aggregation, per-PDF processing helpers, and orchestrator concurrency control. Property-based tests using Hypothesis are added where determinism or structural invariants can be expressed as universally quantified properties.

## Glossary

- **ApiClient**: The module `agents/openai/api_client.py`; provides `extract_chunk`, `warm_pdf_cache`, `_call_api_with_retries`, `paper_cache_key`, and `_response_text`.
- **Prompts**: The module `agents/openai/prompts.py`; provides `SYSTEM_PROMPT`, `build_user_message`, `build_cache_warmup_message`, and `_shared_paper_prefix`.
- **Manifest**: The module `pipeline/manifest.py`; provides `load_manifest` and `save_manifest`.
- **ExtractionMap**: The module `pipeline/extraction_map.py`; provides `load_chunk_fields`, `_infer_chunk_field_ranges`, and `_build_field_lookup`.
- **ExtractionReport**: The module `pipeline/extraction_report.py`; provides `_collect_qc_data`, `_write_qc_csv`, and `generate_qc_report`.
- **PdfProcessor**: The module `pipeline/pdf_processor.py`; provides `process_pdf`, `_run_parallel_chunks`, `_load_completed_result`, and `_save_pdf_output`.
- **Orchestrator**: The module `pipeline/orchestrator.py`; provides `run_pipeline` and `_build_qc_context`.
- **AsyncMock**: `unittest.mock.AsyncMock`; used to mock coroutines and async context managers in tests.
- **Hypothesis**: The property-based testing library; tests use `@given` and `@settings` decorators.
- **PBT**: Property-based test; a test decorated with `@given` that asserts an invariant holds for all generated inputs.

---

## Requirements

### Requirement 1 — OpenAI API Client: Async Function Tests

**User Story:** As a developer, I want unit tests for `agents/openai/api_client.py` async functions, so that retry logic, error handling, and response parsing are verified without calling the real OpenAI API.

#### Acceptance Criteria

1. WHEN `_call_api_with_retries` is called and the mocked OpenAI client raises `RateLimitError` on every attempt, THEN the ApiClient SHALL retry up to `MAX_RETRIES` times with exponential backoff delays and raise `RuntimeError` after all attempts are exhausted.

2. WHEN `_call_api_with_retries` is called with `required=False` and the mocked client raises an exception on every attempt, THEN the ApiClient SHALL return `None` instead of raising.

3. WHEN `_call_api_with_retries` is called and the mocked client raises `APIStatusError`, `APIConnectionError`, or `APITimeoutError`, THEN the ApiClient SHALL apply the same exponential-backoff retry behaviour as for `RateLimitError`.

4. WHEN `_call_api_with_retries` is called and the mocked client raises an unexpected exception type not in the retryable set, THEN the ApiClient SHALL re-raise the exception immediately without retrying.

5. WHEN `extract_chunk` is called with a mocked client that returns a valid structured response on the first attempt, THEN the ApiClient SHALL return a validated list of extraction dicts matching the expected field indices.

6. WHEN `extract_chunk` is called and the mocked client returns a response that fails `validate_chunk_output`, THEN the ApiClient SHALL retry up to `MAX_RETRIES` times and raise `RuntimeError` after all attempts are exhausted.

7. WHEN `warm_pdf_cache` is called with a mocked client that returns a valid response, THEN the ApiClient SHALL return `True`.

8. WHEN `warm_pdf_cache` is called with a mocked client that raises on every attempt, THEN the ApiClient SHALL return `False` without raising.

9. WHEN `_response_text` is called with a response object that has a non-empty `output_text` attribute, THEN the ApiClient SHALL return that string directly.

10. WHEN `_response_text` is called with a response object whose `output` list contains a message block with a `text` content item, THEN the ApiClient SHALL return the text from that block.

11. WHEN `_response_text` is called with a response object whose content block has a non-empty `refusal` field, THEN the ApiClient SHALL raise `RuntimeError` with a message containing the refusal text.

---

### Requirement 2 — OpenAI API Client: paper_cache_key Property

**User Story:** As a developer, I want a property-based test for `paper_cache_key`, so that its determinism and format invariants are verified across arbitrary inputs.

#### Acceptance Criteria

1. THE ApiClient SHALL produce a `paper_cache_key` that is identical on two successive calls with the same `source_package` string.

2. WHEN `paper_cache_key` is called with any non-empty string, THEN the ApiClient SHALL return a string in the format `"{prefix}:{16-hex-chars}"` where the prefix is the configured `PROMPT_CACHE_KEY_PREFIX` stripped of surrounding whitespace.

3. WHEN `paper_cache_key` is called with two distinct `source_package` strings that differ by at least one character, THEN the ApiClient SHALL return two distinct cache keys with overwhelming probability (collision probability ≤ 2⁻⁶⁴).

---

### Requirement 3 — Prompts: Message Builder Tests

**User Story:** As a developer, I want unit tests for `agents/openai/prompts.py` message builders, so that prompt cache stability and structural invariants are verified.

#### Acceptance Criteria

1. THE Prompts module SHALL expose a `SYSTEM_PROMPT` string that contains the JSON output format instruction `{"extractions":[...]}`, the `CACHE WARMUP ONLY` handling instruction, and all four confidence tier labels (`h`, `m`, `l`, `nr`).

2. WHEN `build_cache_warmup_message` is called with any `source_package` string, THEN the Prompts module SHALL return a string whose prefix is byte-identical to `_shared_paper_prefix(source_package)`.

3. WHEN `build_user_message` is called with any `source_package` string and a non-empty `chunk_fields` list, THEN the Prompts module SHALL return a string that starts with `_shared_paper_prefix(source_package)` and contains the JSON-serialised `chunk_fields`.

4. WHEN `build_user_message` is called with `prior_context=None`, THEN the Prompts module SHALL return a string that does not contain the substring `"PRIOR EXTRACTION RESULTS"`.

5. WHEN `build_user_message` is called with a non-None `prior_context` list, THEN the Prompts module SHALL return a string that contains the substring `"PRIOR EXTRACTION RESULTS"` and the JSON-serialised `prior_context`.

6. WHEN `build_user_message` is called twice with the same `source_package` but different `chunk_fields`, THEN the Prompts module SHALL produce two strings that share an identical leading prefix equal to `_shared_paper_prefix(source_package)`.

---

### Requirement 4 — Prompts: build_user_message Structure Invariants (PBT)

**User Story:** As a developer, I want property-based tests for `build_user_message`, so that the shared-prefix invariant holds for all generated inputs.

#### Acceptance Criteria

1. WHEN `build_user_message` is called with any `source_package` string and any `chunk_fields` list, THEN the Prompts module SHALL produce a message whose first `len(_shared_paper_prefix(source_package))` characters are byte-identical to `_shared_paper_prefix(source_package)`.

2. WHEN `build_user_message` is called with any `source_package` string, any `chunk_fields` list, and any `prior_context` list, THEN the Prompts module SHALL produce a message that contains the `source_package` text within the shared prefix section.

---

### Requirement 5 — Manifest: Read/Write Tests

**User Story:** As a developer, I want unit tests for `pipeline/manifest.py`, so that idempotent checkpoint read/write behaviour is verified.

#### Acceptance Criteria

1. WHEN `load_manifest` is called and `MANIFEST_FILE` does not exist, THEN the Manifest module SHALL return an empty dict `{}`.

2. WHEN `load_manifest` is called and `MANIFEST_FILE` contains valid JSON, THEN the Manifest module SHALL return the parsed dict.

3. WHEN `save_manifest` is called with a dict, THEN the Manifest module SHALL write valid JSON to `MANIFEST_FILE` that round-trips back to the original dict via `json.loads`.

4. WHEN `save_manifest` is called followed by `load_manifest`, THEN the Manifest module SHALL return a dict equal to the one that was saved.

---

### Requirement 6 — Manifest: Round-Trip Idempotency (PBT)

**User Story:** As a developer, I want a property-based test for manifest round-trip idempotency, so that save/load correctness holds for arbitrary manifest dicts.

#### Acceptance Criteria

1. WHEN `save_manifest` is called with any dict whose keys are strings and values are JSON-serialisable, THEN the Manifest module SHALL produce a file such that `load_manifest` returns a dict equal to the original.

---

### Requirement 7 — ExtractionMap: Field Grouping Tests

**User Story:** As a developer, I want unit tests for `pipeline/extraction_map.py`, so that field loading, grouping, and lookup are verified against a controlled extraction map.

#### Acceptance Criteria

1. WHEN `_build_field_lookup` is called with a mocked extraction map containing N fields, THEN the ExtractionMap module SHALL return a dict with exactly N entries keyed by `field_index`, each containing `domain_group` and `field_name`.

2. WHEN `load_chunk_fields` is called with a mocked extraction map and a mocked `DOMAIN_TO_CHUNK` config, THEN the ExtractionMap module SHALL return a dict where every field from the extraction map appears in exactly one chunk's field list.

3. WHEN `_infer_chunk_field_ranges` is called and a field's domain group prefix is not present in `DOMAIN_TO_CHUNK`, THEN the ExtractionMap module SHALL raise `ValueError` with a message identifying the missing domain.

4. WHEN `load_chunk_fields` is called with a mocked extraction map, THEN the ExtractionMap module SHALL assign each field to the chunk determined by its `domain_group` prefix according to `DOMAIN_TO_CHUNK`.

---

### Requirement 8 — ExtractionReport: QC Data Aggregation Tests

**User Story:** As a developer, I want unit tests for `pipeline/extraction_report.py`, so that flagged-row aggregation and CSV output are verified.

#### Acceptance Criteria

1. WHEN `_collect_qc_data` is called with a results list containing fields with confidence `"l"` or `"nr"`, THEN the ExtractionReport module SHALL include every such field in the returned `flagged_rows` list.

2. WHEN `_collect_qc_data` is called with a results list containing fields with confidence `"h"` or `"m"`, THEN the ExtractionReport module SHALL not include those fields in `flagged_rows`.

3. WHEN `_collect_qc_data` is called with a results list containing fields whose `extracted_value` is `"nr"`, THEN the ExtractionReport module SHALL increment the `not_reported` count for the corresponding `field_index`.

4. WHEN `_collect_qc_data` is called with any results list, THEN the ExtractionReport module SHALL return `flagged_rows` sorted first by `field_index` ascending, then by `pdf` ascending.

5. WHEN `_write_qc_csv` is called with a list of flagged rows, THEN the ExtractionReport module SHALL write a CSV file whose header row contains exactly the columns `pdf`, `field_index`, `domain_group`, `field_name`, `extracted_value`, `evidence`, `confidence`.

6. WHEN `generate_qc_report` is called with a results list, THEN the ExtractionReport module SHALL write `outputs/qc_report.csv` containing one data row per flagged field.

---

### Requirement 9 — ExtractionReport: _collect_qc_data Completeness (PBT)

**User Story:** As a developer, I want a property-based test for `_collect_qc_data`, so that the completeness invariant holds for all generated result sets.

#### Acceptance Criteria

1. WHEN `_collect_qc_data` is called with any results list, THEN the ExtractionReport module SHALL include in `flagged_rows` every field whose confidence is in `{"l", "nr"}` and SHALL exclude every field whose confidence is in `{"h", "m"}`.

2. WHEN `_collect_qc_data` is called with any results list, THEN the ExtractionReport module SHALL count in `not_reported` every field whose `extracted_value` equals `"nr"`, with the count for each `field_index` equal to the number of such fields across all PDFs.

---

### Requirement 10 — PdfProcessor: Helper Function Tests

**User Story:** As a developer, I want unit tests for `pipeline/pdf_processor.py` helper functions, so that per-PDF checkpointing, output persistence, and chunk orchestration are verified.

#### Acceptance Criteria

1. WHEN `_load_completed_result` is called with a manifest dict where the PDF status is `"complete"` and the output file exists, THEN the PdfProcessor module SHALL return the parsed field list from the output file.

2. WHEN `_load_completed_result` is called with a manifest dict where the PDF status is not `"complete"`, THEN the PdfProcessor module SHALL return `None`.

3. WHEN `_load_completed_result` is called with a manifest dict where the PDF status is `"complete"` but the output file does not exist, THEN the PdfProcessor module SHALL return `None`.

4. WHEN `_save_pdf_output` is called with a pdf_name and a fields list, THEN the PdfProcessor module SHALL write a JSON file at `OUTPUT_DIR/<pdf_name>.extracted.json` whose content round-trips to the original fields list.

5. WHEN `_run_parallel_chunks` is called with mocked `extract_chunk` coroutines that all succeed, THEN the PdfProcessor module SHALL return a list of raw chunk results with length equal to the number of extraction chunks.

6. WHEN `_run_parallel_chunks` is called and at least one mocked `extract_chunk` coroutine raises an exception, THEN the PdfProcessor module SHALL return `None` and update the manifest with `status: "failed_chunks"`.

7. WHEN `process_pdf` is called with a QCContext whose PDF is already marked `"complete"` in the manifest and whose output file exists, THEN the PdfProcessor module SHALL return the cached fields without calling `extract_chunk`.

---

### Requirement 11 — Orchestrator: Concurrency and Error-Handling Tests

**User Story:** As a developer, I want unit tests for `pipeline/orchestrator.py`, so that PDF-level concurrency control, GROBID failure handling, and result collection are verified.

#### Acceptance Criteria

1. WHEN `_build_qc_context` is called and `extract_with_grobid` raises an exception with `failure_behavior` set to `"fallback"` in the QC config, THEN the Orchestrator module SHALL continue with an empty `tei_xml` string and not re-raise the exception.

2. WHEN `_build_qc_context` is called and `extract_with_grobid` raises an exception with `failure_behavior` set to `"manifest_fail"` in the QC config, THEN the Orchestrator module SHALL re-raise the exception.

3. WHEN `run_pipeline` is called with a list of PDF paths and mocked `_build_qc_context` and `process_pdf` functions that succeed for all PDFs, THEN the Orchestrator module SHALL return a list of result dicts with one entry per PDF.

4. WHEN `run_pipeline` is called and the QC pipeline raises an exception for one PDF, THEN the Orchestrator module SHALL record `status: "failed_qc_pipeline"` in the manifest for that PDF and continue processing the remaining PDFs.

5. WHEN `run_pipeline` is called with `pdf_concurrency=1`, THEN the Orchestrator module SHALL process PDFs sequentially, with at most one PDF's `_build_qc_context` call active at a time.

6. WHEN `run_pipeline` is called with `enable_cache_prewarm=False`, THEN the Orchestrator module SHALL pass `enable_cache_prewarm=False` in the runtime config forwarded to `process_pdf`.

---

### Requirement 12 — Test Infrastructure: No Real External Calls

**User Story:** As a developer, I want all new tests to run without real OpenAI, GROBID, or file-system side effects, so that the test suite passes in any environment without credentials or running services.

#### Acceptance Criteria

1. THE test suite SHALL mock the `AsyncOpenAI` client using `AsyncMock` so that no real HTTP requests are made to the OpenAI API during any test in `tests/agents/openai/`.

2. THE test suite SHALL use `tmp_path` fixtures or `patch` to redirect all file I/O (manifest, output JSON, QC CSV) to temporary directories so that no production files are created or modified during tests.

3. THE test suite SHALL mock `extract_with_grobid` and `extract_with_pymupdf` in orchestrator tests so that no real PDF extraction is performed.

4. IF a test module imports `agents.openai.api_client`, THEN the test module SHALL patch `load_openai_config` before import or use `importlib` reload to prevent module-level config loading from requiring a real `config.yaml`.

5. THE test suite SHALL not require `paddleocr`, `faiss`, `torch`, or `sentence-transformers` to be installed for any test in the seven new test files to pass.
