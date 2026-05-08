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
  `(system_prompt + paper_text)` prefix is byte-identical across all
  calls for the same PDF, maximising prompt-cache hits.
- Validate each chunk's response locally against the expected
  `field_index` set, independent of the API schema, via
  [`pipeline.validator.validate_chunk_output`](../../pipeline/README.md).

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

Inputs: a paper's `exact_text` (from the QC `UnifiedRecord`), the
chunk-scoped extraction-map fields, and an `asyncio.Semaphore` that
caps total concurrent OpenAI calls across all PDFs.

Outputs: a validated list of compact extraction dicts of the form
`{"i": int, "v": str, "e": str, "c": "h"|"m"|"l"|"nr"}`. Expansion to
full `field_index / domain_group / field_name` records happens in
[`pipeline/validator.reconstruct_fields`](../../pipeline/README.md).

---

## Files

### `api_client.py`

Public async functions:

| Function | Description |
| -------- | ----------- |
| `warm_pdf_cache(pdf_text, semaphore, pdf_name, model, required=False)` | Issues a tiny call to prewarm the shared `(system + PDF)` prefix in the prompt cache for `model`. Failure is non-fatal by default. |
| `extract_chunk(chunk_num, pdf_text, chunk_fields, semaphore, prior_context=None, pdf_name=...)` | Calls the chunk model (or synthesis model when `chunk_num == NUM_CHUNKS`) with strict structured-output schema, retries with exponential backoff, validates the response against `chunk_fields`' field indices, and returns the parsed list. |

Internal helpers worth knowing about:

- `paper_cache_key(pdf_text)` — derives a stable `prompt_cache_key` of
  the form `<prefix>:<sha256(pdf_text)[:16]>`.
- `_json_schema_format()` — universal strict structured-output schema
  used for **every** chunk and warmup call. Keeping it identical lets
  the cached prefix include the schema; chunk-specific `field_index`
  validation is done locally rather than via per-chunk schemas.
- `_chunk_model_and_tokens(chunk_num)` — selects `(model, max_tokens)`;
  returns the synthesis model + budget for the last chunk and the
  chunk-model values otherwise.
- `_call_api_with_retries(...)` — common retry loop. Honours
  `max_retries` and `retry_base_delay` from config, retries on
  `RateLimitError`, `APIStatusError`, `APIConnectionError`,
  `APITimeoutError`, and (for `extract_chunk`)
  `pipeline.validator.ValidationError`.

### `prompts.py`

Stable prompt construction:

- `SYSTEM_PROMPT` — fixed across all warmup and extraction calls. Does
  **not** embed PDF names, run IDs, timestamps, chunk numbers, or
  retry counts.
- `_shared_paper_prefix(pdf_text)` — the byte-stable prefix shared by
  warmup, chunks `1..N-1`, and chunk `N`. Anything that varies per
  call (extraction map, prior chunk results) is appended **after** this
  prefix.
- `build_cache_warmup_message(pdf_text)` — minimal suffix that asks the
  model to return an empty `{"extractions": []}`.
- `build_user_message(pdf_text, chunk_fields, prior_context=None)` —
  builds the user message in the order
  `shared paper prefix → extraction map → optional prior-chunk
  outputs`. The synthesis chunk is the only caller that supplies
  `prior_context`.

---

## Configuration

All runtime knobs are read once at import time from
[`utils.config_utils.load_openai_config()`](../../utils/README.md) and
exposed as module-level constants on `api_client`:

| Name | Source key (`config.yaml`) | Notes |
| ---- | -------------------------- | ----- |
| `CHUNK_MODEL` | `openai.chunk_model` (`OPENAI_CHUNK_MODEL`) | Used for chunks `1..N-1`. |
| `SYNTHESIS_MODEL` | `openai.synthesis_model` (`OPENAI_SYNTHESIS_MODEL`) | Used for chunk `N`. |
| `TEMPERATURE` | `openai.temperature` (`OPENAI_TEMPERATURE`) | Omitted from requests when `None`. |
| `PROMPT_CACHE_KEY_PREFIX` | `openai.prompt_cache.key_prefix` | Combined with `sha256(pdf_text)[:16]`. |
| `PROMPT_CACHE_RETENTION` | `openai.prompt_cache.retention` | E.g. `"24h"`, `"in_memory"`. |
| `CACHE_WARMUP_MAX_TOKENS` | `openai.prompt_cache.warmup_max_tokens` | Output cap for the warmup call. |
| `CHUNK_MAX_TOKENS` | derived from `extraction.num_chunks` | Per-chunk output token budget. |
| `MAX_RETRIES` | `retry.max_retries` | Per-call retry attempts. |
| `RETRY_BASE_DELAY` | `retry.base_delay_seconds` | Exponential backoff base. |

The CLI flag `--no-cache-prewarm` and the `enable_cache_prewarm` config
key are **not** applied here; the orchestrator simply skips
`warm_pdf_cache` when prewarm is disabled.

---

## Cache contract

For the prompt cache to be hit consistently:

- The system prompt is fixed.
- The user message starts with `_shared_paper_prefix(pdf_text)` for
  warmup, chunks `1..N-1`, and chunk `N`.
- The structured-output schema returned by `_json_schema_format()` is
  identical for every call (chunk-specific `field_index` validation is
  done locally, not via the API schema).
- The `prompt_cache_key` is derived from a SHA-256 of `pdf_text`
  (truncated to 16 hex chars) and a configured prefix, so calls for
  the same paper share a key while calls for different papers do not.

---

## Example

```python
import asyncio
from agents.openai.api_client import extract_chunk, warm_pdf_cache

async def demo(pdf_text: str, chunk_fields_1: list[dict]):
    sem = asyncio.Semaphore(15)
    await warm_pdf_cache(pdf_text, sem, pdf_name="paper1")
    result = await extract_chunk(
        chunk_num=1,
        pdf_text=pdf_text,
        chunk_fields=chunk_fields_1,
        semaphore=sem,
        pdf_name="paper1",
    )
    print(result)  # list[{"i": int, "v": str, "e": str, "c": "h|m|l|nr"}]
```

---

## Related

- Parent: [../README.md](../README.md)
- Orchestrator that drives this client: [../../pipeline/README.md](../../pipeline/README.md)
- Schema/index validation called inside `extract_chunk`:
  [`pipeline/validator.py`](../../pipeline/README.md)
- Configuration loader: [../../utils/README.md](../../utils/README.md)
- Root overview: [../../README.md](../../README.md)
