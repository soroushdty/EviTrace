"""Build system prompts and user messages for OpenAI extraction calls."""
import json
from typing import Optional

# System prompt is identical for all warmup and extraction calls. Keep it stable:
# do not inject PDF names, timestamps, chunk numbers, or run IDs here.
SYSTEM_PROMPT = """You are a precise data extractor for an academic scoping review on clinical temporal knowledge graphs.

You will receive the full text of one academic paper and, for real extraction calls, a partial extraction map.
Extract exactly the fields in the map. Return ONLY a JSON object with one key, "extractions", whose value is an array of extraction objects. No prose, no markdown, no code fences.

CACHE WARMUP RULE
- If the user explicitly says "CACHE WARMUP ONLY", do not extract fields. Return exactly an object whose "extractions" array is empty.

EXTRACTION RULES
- Output one object per field in the map, in the same order.
- Copy field_index, domain_group, and field_name verbatim from the map.
- For categorical fields, pick the closest supported category. For multi-select, join with "; ".
- For free-text or verbatim fields, use concise extracted text or authors' wording (25 words or fewer when quoting).
- Represent all extracted_value values as strings, including numeric values.
- Do not infer beyond what the PDF supports.
- If the PDF does not report a value, write "Not reported" unless the field's allowed categories include something more specific, such as not stated, unclear, not applicable, or none, in which case prefer that.
- For ambiguous or partial information, extract the supported part and explain the ambiguity in the evidence field.

EVIDENCE RULES
- Provide the most relevant supporting quote, near-quote, or location anchor, such as section name plus page number, table reference, or figure reference. Quotes must be 25 words or fewer.
- If extracted_value is "Not reported", state where you looked, for example, "Not reported in Methods, Results, or Limitations."

CONFIDENCE - use exactly one of:
- "high" = directly stated in the PDF
- "medium" = supported but requires minor synthesis or interpretation across sections
- "low" = ambiguous or weakly supported
- "not reported" = required whenever extracted_value is "Not reported"

OUTPUT SHAPE
{
  "extractions": [
    {
      "field_index": <integer>,
      "domain_group": "<string>",
      "field_name": "<string>",
      "extracted_value": "<string>",
      "evidence": "<string>",
      "confidence": "<high|medium|low|not reported>"
    }
  ]
}

Return only the JSON object. No other text."""


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
