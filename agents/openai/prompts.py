"""Build system prompts and user messages for OpenAI extraction calls."""
import json
from typing import Optional

# System prompt is identical for all warmup and extraction calls. Keep it stable:
# do not inject PDF names, timestamps, chunk numbers, or run IDs here.
SYSTEM_PROMPT = """You are a precise extractor for academic papers on clinical temporal knowledge graphs.

Input: paper text/PDF + extraction map. Output only JSON:
{"extractions":[{"i":<integer>,"v":"<string>","e":"<string>","c":"<h|m|l|nr>"}]}

If user says "CACHE WARMUP ONLY", return {"extractions":[]}.

For each mapped field, output exactly one object in the same order as the map. Extract only paper-supported values; do not infer. v is always a string. For categories, choose the closest supported allowed value; multi-select uses "; ". Free text concise; quotes ≤25 words.

If absent, set v="nr" and c="nr" and say where you looked in e.

e must directly support v via quote, near-quote, or location anchor. For ambiguity, extract the supported part and explain in e.

c: h=direct; m=minor synthesis; l=ambiguous/weak; nr=not reported."""


def _shared_paper_prefix(pdf_text: str) -> str:
    """
    Shared user-message prefix for warmup, chunks 1-4, and chunk 5.

    OpenAI prompt caching requires exact prefix matches. Everything in this
    function should remain identical for all calls for the same PDF. Put all
    variable call-specific material after this prefix.
    """
    return "\n".join([
        "SHARED SOURCE DOCUMENT",
        "The following full paper text is the only source of evidence for this extraction.",
        "Do not use the filename, prior knowledge, or outside sources.",
        "",
        "PAPER TEXT:",
        pdf_text,
        "",
        "END SHARED SOURCE DOCUMENT",
        "",
    ])


def build_cache_warmup_message(pdf_text: str) -> str:
    """Build the tiny suffix used only to prewarm the shared PDF prefix."""
    return _shared_paper_prefix(pdf_text) + (
        "CACHE WARMUP ONLY. Return the strict JSON object now with an empty "
        "extractions array."
    )


def build_user_message(
    pdf_text: str,
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
    parts: list[str] = [_shared_paper_prefix(pdf_text)]

    parts.append(f"EXTRACTION MAP ({len(chunk_fields)} fields to extract):")
    parts.append(json.dumps(chunk_fields, indent=2, ensure_ascii=False))
    parts.append("")

    if prior_context is not None:
        parts.append("PRIOR EXTRACTION RESULTS from chunks 1-4. Treat these as read-only context for synthesis:")
        parts.append(json.dumps(prior_context, indent=2, ensure_ascii=False))
        parts.append("")

    parts.append("Return the JSON object now.")

    return "\n".join(parts)
