"""Build system prompts and user messages for OpenAI extraction calls."""
import json
from typing import Optional

from agents import agent_schema_validator


def get_system_prompt() -> str:
    """Return the system prompt from agent_schema.json via the singleton."""
    return agent_schema_validator.get_system_prompt()


def _shared_paper_prefix(source_package: str) -> str:
    """
    Shared user-message prefix for warmup, chunks 1-4, and chunk 5.

    OpenAI prompt caching requires exact prefix matches. Everything in this
    function should remain identical for all calls for the same PDF. Put all
    variable call-specific material after this prefix.
    """
    return "\n".join([
        "SHARED EVIDENCE PACKAGE",
        "The following compact evidence package is the only source of evidence for this extraction.",
        "Do not use prior knowledge or outside sources.",
        "",
        "EVIDENCE PACKAGE JSON:",
        source_package,
        "",
        "END SHARED EVIDENCE PACKAGE",
        "",
    ])


def build_cache_warmup_message(source_package: str) -> str:
    """Build the tiny suffix used only to prewarm the shared PDF prefix."""
    return _shared_paper_prefix(source_package) + (
        "CACHE WARMUP ONLY. Return the strict JSON object now with an empty "
        "extractions array."
    )


def build_user_message(
    source_package: str,
    chunk_fields: list[dict],
    prior_context: Optional[list[dict]] = None,
) -> str:
    """
    Build the user message for a chunk API call.

    Order: shared PDF prefix → extraction map → prior chunk outputs (chunk 5 only).

    The extraction map is placed immediately after the shared PDF prefix so the
    cached prefix for chunk 5 extends as far as it does for chunks 1–4. Prior
    chunk outputs are a trailing suffix — strictly after the full PDF text and
    never interleaved between the PDF and the extraction task.
    """
    parts: list[str] = [_shared_paper_prefix(source_package)]

    parts.append(f"EXTRACTION MAP ({len(chunk_fields)} fields to extract):")
    parts.append(json.dumps(chunk_fields, indent=2, ensure_ascii=False))
    parts.append("")

    if prior_context is not None:
        parts.append("PRIOR EXTRACTION RESULTS from chunks 1-4. Treat these as read-only context for synthesis:")
        parts.append(json.dumps(prior_context, indent=2, ensure_ascii=False))
        parts.append("")

    parts.append("Return the JSON object now.")

    return "\n".join(parts)
