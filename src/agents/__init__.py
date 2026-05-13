"""
agents/
-------
Agent package.  Exposes the module-level AgentSchemaValidator singleton and
the SchemaValidationError exception.

The singleton ``agent_schema_validator`` is instantiated once at import time.
All consumers (``agents/openai/prompts.py``, etc.) import from here — never
from ``agents.validator`` directly.
"""

from agents.validator import AgentSchemaValidator, SchemaValidationError

# Module-level singleton — loaded once, shared across the entire process.
agent_schema_validator = AgentSchemaValidator()

__all__ = ["AgentSchemaValidator", "SchemaValidationError", "agent_schema_validator"]
