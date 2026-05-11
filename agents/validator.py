"""
agents/validator.py
-------------------
AgentSchemaValidator — sole reader of configs/agent_schema.json.

Loads the schema once at construction time, validates its structure, and
exposes typed accessors.  The module-level singleton ``agent_schema_validator``
in ``agents/__init__.py`` is the only instance that should exist in a normal
run.

Public API
----------
AgentSchemaValidator(schema_path)
    .get_system_prompt() -> str
    .get_policies()      -> dict
    .get_extraction_rules() -> dict

SchemaValidationError
    Raised at construction time when agent_schema.json is missing, malformed,
    or violates the required structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SchemaValidationError(Exception):
    """Raised when agent_schema.json fails structural validation."""


_REQUIRED_TOP_LEVEL_KEYS = {"system_prompt", "policies", "extraction_rules"}


class AgentSchemaValidator:
    """Sole reader of agent_schema.json.

    Loads and validates the file exactly once at construction time.
    All accessor methods return cached in-memory values — the file is never
    re-read after ``__init__`` completes, guaranteeing prompt cache stability.

    Parameters
    ----------
    schema_path:
        Path to ``agent_schema.json``.  Defaults to
        ``<project_root>/configs/agent_schema.json``.
    """

    def __init__(self, schema_path: Path | str | None = None) -> None:
        if schema_path is None:
            schema_path = Path(__file__).resolve().parent.parent / "configs" / "agent_schema.json"
        schema_path = Path(schema_path)

        try:
            raw = schema_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise SchemaValidationError(
                f"agent_schema.json not found at {schema_path}"
            ) from exc

        try:
            data: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SchemaValidationError(
                f"agent_schema.json contains invalid JSON: {exc}"
            ) from exc

        missing = _REQUIRED_TOP_LEVEL_KEYS - data.keys()
        if missing:
            raise SchemaValidationError(
                f"agent_schema.json is missing required top-level keys: {sorted(missing)}"
            )

        system_prompt = data["system_prompt"]
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise SchemaValidationError(
                "agent_schema.json: 'system_prompt' must be a non-empty string"
            )

        # Cache only the LLM-facing content in memory — never re-read the file.
        # 'version' and 'type' are intentionally not stored: they are file
        # metadata for tooling and must never be forwarded to the LLM.
        self._system_prompt: str = system_prompt
        self._policies: dict = data["policies"]
        self._extraction_rules: dict = data["extraction_rules"]

    def get_system_prompt(self) -> str:
        """Return the system prompt string, byte-identical on every call."""
        return self._system_prompt

    def get_policies(self) -> dict:
        """Return the policies dict from agent_schema.json."""
        return self._policies

    def get_extraction_rules(self) -> dict:
        """Return the extraction_rules dict from agent_schema.json."""
        return self._extraction_rules
