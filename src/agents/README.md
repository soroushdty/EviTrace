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
        │
        ▼
agents/validator.py          (AgentSchemaValidator — sole reader of configs/agent_schema.json)
```

The orchestrator in [`pipeline/`](../pipeline/README.md) only depends on
the public entry points exposed by the agent sub-package, so swapping
providers is a matter of writing a new sub-package with the same
function signatures.

---

## Contents

| Path | Purpose | README |
| ---- | ------- | ------ |
| `validator.py` | `AgentSchemaValidator` — sole reader of `configs/agent_schema.json` | See below |
| `openai/` | Async OpenAI Responses API client and prompt builders | [openai/README.md](openai/README.md) |

---

## `agents/__init__.py`

Exports the module-level `AgentSchemaValidator` singleton and
`SchemaValidationError`. The singleton `agent_schema_validator` is
instantiated once at import time and shared across the entire process.

```python
from agents import agent_schema_validator, AgentSchemaValidator, SchemaValidationError
```

## `agents/validator.py` — `AgentSchemaValidator`

Sole reader of `configs/agent_schema.json`. Loads and validates the file
exactly once at construction time.

```python
AgentSchemaValidator(schema_path=None)
    .get_system_prompt() -> str
    .get_policies()      -> dict
    .get_extraction_rules() -> dict
```

- `schema_path` defaults to `<project_root>/configs/agent_schema.json`.
- Raises `SchemaValidationError` at construction time when the file is
  missing, contains invalid JSON, or is missing required top-level keys
  (`system_prompt`, `policies`, `extraction_rules`).
- All accessor methods return cached in-memory values — the file is never
  re-read after `__init__` completes, guaranteeing prompt cache stability.
- `version` and `type` metadata keys are intentionally not stored and
  never forwarded to the LLM.

`SchemaValidationError` — raised at construction time when
`agent_schema.json` fails structural validation.

---

## Adding a new provider

When adding a new agent sub-package, mirror the layout under
`agents/openai/`:

1. An `api_client.py` exposing async `extract_chunk(...)` and
   `warm_pdf_cache(...)` functions whose signatures match the ones
   already imported by [`pipeline/pdf_processor.py`](../pipeline/README.md).
2. A `prompts.py` exposing a `get_system_prompt()` callable, a
   `build_cache_warmup_message(...)` builder, and a
   `build_user_message(...)` builder.
3. Add a directory `README.md` documenting the provider-specific
   contract, retry behaviour, and any cache-prefix invariants.

---

## Related

- Root overview: [../README.md](../README.md)
- Orchestrator that consumes these clients: [../pipeline/README.md](../pipeline/README.md)
- Centralised config for provider credentials: [../configs/README.md](../configs/README.md)
- Schema file read by `AgentSchemaValidator`: `configs/agent_schema.json`
