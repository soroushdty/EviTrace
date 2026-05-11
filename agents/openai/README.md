# `agents/openai/` — OpenAI Responses API Client

Async OpenAI client and prompt builders used by the chunked extraction
pipeline.

This sub-package is the **only** module in the repository that talks to
the OpenAI API directly. Everything else interacts with OpenAI through
the two functions exported here: `extract_chunk` and `warm_pdf_cache`.

---

## Purpose

- Encapsulate the OpenAI Responses API call (model selection, retries,
  structured-output schema, prompt-cache key derivation).
- Provide cache-stable prompt builders so the
  `(system_prompt + evidence package)` prefix is byte-identical across all
  calls for the same PDF, maximising prompt-cache hits.
- Return raw response text from `extract_chunk`; validation against the
  expected `field_index` set is the caller's responsibility
  (`pipeline/pdf_processor.py` calls `pipeline.validator.validate_chunk_output`).

---

## Where it fits

```text
pipeline/pdf_processor._run_parallel_chunks
        │
        ├──► warm_pdf_cache(...)              # tiny prewarm call (optional)
        ├──► extract_chunk(1, ...)            ┐
        ├──► extract_chunk(2, ...)            ├── parallel
        └──► extract_chunk(N-1, ...)          ┘

pipeline/pdf_processor.process_pdf
        │
        └──► extract_chunk(N, prior_context=...)   # synthesis chunk
```

Inputs: a compact evidence package JSON string (from
`pipeline.evidence_index.build_chunk_evidence_package`), the chunk-scoped
extraction-map fields, and an `asyncio.Semaphore` that caps total
concurrent OpenAI calls across all PDFs.

Outputs: raw response text string. Expansion to full field records happens
in [`pipeline/validator.reconstruct_fields`](../../pipeline/README.md).

---

## Files

### `api_client.py`

Module-level constants (loaded once at import time from
`utils.config_utils.load_openai_config()`):

`CACHE_WARMUP_MAX_TOKENS`, `CHUNK_MAX_TOKENS`, `CHUNK_MODEL`,
`MAX_RETRIES`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`PROMPT_CACHE_KEY_PREFIX`, `PROMPT_CACHE_RETENTION`, `RETRY_BASE_DELAY`,
`SYNTHESIS_MODEL`, `NUM_CHUNKS`, `TEMPERATURE`.

Public async functions:

| Function | Description |
| -------- | ----------- |
| `warm_pdf_cache(source_package, semaphore, pdf_name="unknown", model=CHUNK_MODEL, required=False) -> bool` | Issues a tiny call to prewarm the shared `(system + evidence package)` prefix in the prompt cache for `model`. Failure is non-fatal by default (`required=False`). Returns `True` on success. |
| `extract_chunk(chunk_num, source_package, chunk_fields, semaphore, valid_location_ids=None, prior_context=None, pdf_name="unknown") -> str` | Calls the chunk model (or synthesis model when `chunk_num == NUM_CHUNKS`) with strict structured-output schema, retries with exponential backoff, and returns the raw response text. |

Internal helpers:

- `paper_cache_key(source_package) -> str` — derives a stable
  `prompt_cache_key` of the form `<prefix>:<sha256(source_package)[:16]>`.
- `_json_schema_format() -> dict` — universal strict structured-output
  schema used for **every** chunk and warmup call. Keeping it identical
  lets the cached prefix include the schema; chunk-specific `field_index`
  validation is done locally rather than via per-chunk schemas.
- `_chunk_model_and_tokens(chunk_num) -> tuple[str, int]` — selects
  `(model, max_tokens)`; returns the synthesis model + budget for the last
  chunk and the chunk-model values otherwise.
- `_call_api_with_retries(request_kwargs, semaphore, tag, *, required=True) -> Any`
  — common retry loop. Honours `max_retries` and `retry_base_delay` from
  config, retries on `RateLimitError`, `APIStatusError`,
  `APIConnectionError`, `APITimeoutError`.
- `_response_text(response) -> str` — extracts assistant text from OpenAI
  Responses API objects robustly (handles SDK objects and dicts in tests).
- `_base_request_kwargs(model, source_package, user_msg, max_output_tokens) -> dict`
  — builds the full kwargs dict for `_client.responses.create`, including
  `prompt_cache_key`, `prompt_cache_retention`, and optionally
  `temperature` (omitted when `TEMPERATURE is None`).

### `prompts.py`

Stable prompt construction:

| Function | Description |
| -------- | ----------- |
| `get_system_prompt() -> str` | Returns the system prompt from `agent_schema.json` via the `agent_schema_validator` singleton. Fixed across all warmup and extraction calls. |
| `_shared_paper_prefix(source_package) -> str` | The byte-stable prefix shared by warmup, chunks `1..N-1`, and chunk `N`. Contains only the evidence package. Never embeds PDF names, run IDs, timestamps, chunk numbers, or retry counts. |
| `build_cache_warmup_message(source_package) -> str` | Minimal suffix that asks the model to return an empty `{"extractions": []}`. |
| `build_user_message(source_package, chunk_fields, prior_context=None) -> str` | Builds the user message in the order: `shared evidence prefix → extraction map → optional prior-chunk outputs`. The synthesis chunk is the only caller that supplies `prior_context`. |

---

## Configuration

All runtime knobs are read once at import time from
[`utils.config_utils.load_openai_config()`](../../utils/README.md):

| Name | Source key (`configs/config.yaml`) | Notes |
| ---- | ---------------------------------- | ----- |
| `CHUNK_MODEL` | `openai.chunk_model` (`OPENAI_CHUNK_MODEL`) | Used for chunks `1..N-1`. |
| `SYNTHESIS_MODEL` | `openai.synthesis_model` (`OPENAI_SYNTHESIS_MODEL`) | Used for chunk `N`. |
| `TEMPERATURE` | `openai.temperature` (`OPENAI_TEMPERATURE`) | Omitted from requests when `None`. |
| `PROMPT_CACHE_KEY_PREFIX` | `openai.prompt_cache.key_prefix` | Combined with `sha256(source_package)[:16]`. |
| `PROMPT_CACHE_RETENTION` | `openai.prompt_cache.retention` | E.g. `"24h"`, `"in_memory"`. |
| `CACHE_WARMUP_MAX_TOKENS` | `openai.prompt_cache.warmup_max_tokens` | Output cap for the warmup call. |
| `CHUNK_MAX_TOKENS` | derived from `extraction.num_chunks` | Per-chunk output token budget dict. |
| `MAX_RETRIES` | `retry.max_retries` | Per-call retry attempts. |
| `RETRY_BASE_DELAY` | `retry.base_delay_seconds` | Exponential backoff base. Actual delay = `base * 2^(attempt-1)`. |

The CLI flag `--no-cache-prewarm` and the `enable_cache_prewarm` config
key are **not** applied here; the orchestrator simply skips
`warm_pdf_cache` when prewarm is disabled.

---

## Cache contract

For the prompt cache to be hit consistently:

- The system prompt is fixed (read from `agent_schema.json` via singleton).
- The user message starts with `_shared_paper_prefix(source_package)` for
  warmup, chunks `1..N-1`, and chunk `N`.
- The structured-output schema returned by `_json_schema_format()` is
  identical for every call.
- The `prompt_cache_key` is derived from a SHA-256 of `source_package`
  (truncated to 16 hex chars) and a configured prefix, so calls for the
  same paper share a key while calls for different papers do not.

---

## Example

```python
import asyncio
from agents.openai.api_client import extract_chunk, warm_pdf_cache

async def demo(source_package: str, chunk_fields_1: list[dict]):
    sem = asyncio.Semaphore(15)
    await warm_pdf_cache(source_package, sem, pdf_name="paper1")
    raw_text = await extract_chunk(
        chunk_num=1,
        source_package=source_package,
        chunk_fields=chunk_fields_1,
        semaphore=sem,
        pdf_name="paper1",
    )
    # raw_text is a JSON string; validate with pipeline.validator.validate_chunk_output
    print(raw_text)
```

---

## Related

- Parent: [../README.md](../README.md)
- Orchestrator that drives this client: [../../pipeline/README.md](../../pipeline/README.md)
- Schema/index validation called after `extract_chunk`:
  [`pipeline/validator.py`](../../pipeline/README.md)
- Configuration loader: [../../utils/README.md](../../utils/README.md)
- Agent schema file: `configs/agent_schema.json`
- Root overview: [../../README.md](../../README.md)
