"""Build system prompts and user messages for OpenAI extraction calls."""
import json
from typing import Optional

from agents import agent_schema_validator

# Module-level cache for get_system_prompt(). Populated lazily on first call
# and never reassigned afterward, guaranteeing every caller within the
# process lifetime receives the exact same string object (``is`` identity).
# This makes the invariant explicit at this layer rather than depending on
# agent_schema_validator's internal caching (Requirements: 2.7).
_CACHED_SYSTEM_PROMPT: Optional[str] = None


def get_system_prompt() -> str:
    """Return the system prompt from agent_schema.json via the singleton.

    Cached as a module-level singleton: the same object reference is
    returned on every call within the process lifetime (Requirements: 2.7).
    """
    global _CACHED_SYSTEM_PROMPT
    if _CACHED_SYSTEM_PROMPT is None:
        _CACHED_SYSTEM_PROMPT = agent_schema_validator.get_system_prompt()
    return _CACHED_SYSTEM_PROMPT


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


def _sorted_by_field_index(chunk_fields: list[dict]) -> list[dict]:
    """Return a new list of field definitions sorted by field_index ascending.

    Does not mutate the caller's list. Requirements: 2.3.
    """
    return sorted(chunk_fields, key=lambda field: field["field_index"])


def compute_stable_prefix(system_prompt: str, evidence_package: str, rules: str) -> str:
    """Build the canonical Stable_Prefix string used for cache fingerprinting.

    Concatenates ``system_prompt``, ``evidence_package``, and ``rules`` in a
    fixed order with a stable separator so identical inputs always produce
    byte-identical output. This function performs no hashing itself — callers
    (e.g. telemetry) SHA-256 hash the UTF-8 bytes of the returned string to
    obtain the Stable_Prefix fingerprint (Requirements: 2.4, 2.6, 2.7).

    Callers MUST NOT pass runtime metadata (timestamps, run IDs, chunk
    numbers, PDF file names) in any of the three arguments — those belong in
    the Dynamic_Suffix, never in the Stable_Prefix.
    """
    return "\n".join([system_prompt, evidence_package, rules])


def build_cache_warmup_message(
    source_package: str,
    chunk_fields: Optional[list[dict]] = None,
) -> str:
    """Build the warmup suffix used to seed a chunk's cacheable prefix.

    When ``chunk_fields`` is ``None`` (default), the warmup covers only the
    shared PDF prefix — useful for chunks 1..N-1 whose extraction maps all
    get individually cached by the corresponding chunk call.

    When ``chunk_fields`` is provided, the warmup ALSO emits the extraction-
    map block that the real call would emit, extending the cached prefix
    past the end of the extraction map. This is important for the synthesis
    chunk, whose ``prior_context`` is a data-dependent trailing suffix that
    cannot be cached across runs — warming the prefix up through the
    extraction map is the most the server-side cache can keep.

    The tail ("CACHE WARMUP ONLY ...") is intentionally different from the
    tail of a real ``build_user_message`` call so the two serialisations
    only match up to the end of the shared/mapped prefix.
    """
    parts: list[str] = [_shared_paper_prefix(source_package)]
    if chunk_fields is not None:
        ordered_fields = _sorted_by_field_index(chunk_fields)
        parts.append(f"EXTRACTION MAP ({len(ordered_fields)} fields to extract):")
        parts.append(json.dumps(ordered_fields, indent=2, ensure_ascii=False))
        parts.append("")
    parts.append(
        "CACHE WARMUP ONLY. Return the strict JSON object now with an empty "
        "extractions array."
    )
    return "\n".join(parts)


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

    ordered_fields = _sorted_by_field_index(chunk_fields)
    parts.append(f"EXTRACTION MAP ({len(ordered_fields)} fields to extract):")
    parts.append(json.dumps(ordered_fields, indent=2, ensure_ascii=False))
    parts.append("")

    if prior_context is not None:
        parts.append("PRIOR EXTRACTION RESULTS from chunks 1-4. Treat these as read-only context for synthesis:")
        parts.append(json.dumps(prior_context, indent=2, ensure_ascii=False))
        parts.append("")

    parts.append("Return the JSON object now.")

    return "\n".join(parts)
