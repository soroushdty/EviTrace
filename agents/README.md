# `agents/` — External Agent Integrations

External-LLM and agent integrations used by the EviTrace pipeline.

This directory exists so the rest of the repository can talk to remote
inference providers through a single, swappable layer. Today it
contains exactly one provider — **OpenAI** — but the layout is designed
so additional providers (Anthropic, Azure OpenAI, local vLLM, etc.) can
be added side-by-side as new sub-packages without touching the
[`pipeline/`](../pipeline/README.md) orchestrator.

---

## Where it fits

```text
pipeline/pdf_processor.py
        │
        │  (imports extract_chunk, warm_pdf_cache)
        ▼
agents/openai/api_client.py  ──►  OpenAI Responses API
        │
        ▼
agents/openai/prompts.py     (system prompt + cache-stable user message)
```

The orchestrator in [`pipeline/`](../pipeline/README.md) only depends on
the public entry points exposed by the agent sub-package, so swapping
providers is a matter of writing a new sub-package with the same
function signatures.

---

## Contents

| Path | Purpose | README |
| ---- | ------- | ------ |
| `openai/` | Async OpenAI Responses API client and prompt builders | [openai/README.md](openai/README.md) |

---

## Adding a new provider

When adding a new agent sub-package, mirror the layout under
`agents/openai/`:

1. An `api_client.py` exposing async `extract_chunk(...)` and
   `warm_pdf_cache(...)` functions whose signatures match the ones
   already imported by [`pipeline/pdf_processor.py`](../pipeline/README.md).
2. A `prompts.py` exposing a stable `SYSTEM_PROMPT`, a
   `build_cache_warmup_message(...)` builder, and a
   `build_user_message(...)` builder.
3. Add a directory `README.md` documenting the provider-specific
   contract, retry behaviour, and any cache-prefix invariants.

---

## Related

- Root overview: [../README.md](../README.md)
- Orchestrator that consumes these clients: [../pipeline/README.md](../pipeline/README.md)
- Centralised config for provider credentials: [../config/README.md](../config/README.md)
