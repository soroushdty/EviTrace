"""Async OpenAI API client with cache prewarm, extraction, retry, and usage logs."""
import asyncio
import hashlib
import json
from typing import Any, Optional

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from config import (
    CACHE_WARMUP_MAX_TOKENS,
    CHUNK_MAX_TOKENS,
    CHUNK_MODEL,
    MAX_RETRIES,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    PROMPT_CACHE_KEY_PREFIX,
    PROMPT_CACHE_RETENTION,
    RETRY_BASE_DELAY,
    SYNTHESIS_MODEL,
    TEMPERATURE,
)
from prompts import SYSTEM_PROMPT, build_cache_warmup_message, build_user_message
from validator import ValidationError, validate_chunk_output
from utils.logging_utils import get_logger, log_cache_usage

logger = get_logger(__name__)

# One shared async client for the entire run.
_client_kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
if OPENAI_BASE_URL:
    _client_kwargs["base_url"] = OPENAI_BASE_URL
_client = AsyncOpenAI(**_client_kwargs)


def _expected_indices(chunk_fields: list[dict]) -> list[int]:
    """Extract expected field indices from chunk_fields list."""
    return sorted([field["field_index"] for field in chunk_fields])


def _json_schema_format() -> dict[str, Any]:
    """
    Universal strict structured-output schema for all chunks and warmups.

    Keep the name and schema identical across chunk numbers. The validator still
    enforces chunk-specific field_index values locally after the model call.
    """
    return {
        "type": "json_schema",
        "name": "paper_extractions",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "extractions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "i": {"type": "integer"},
                            "v": {"type": "string"},
                            "e": {"type": "string"},
                            "c": {
                                "type": "string",
                                "enum": ["h", "m", "l", "nr"],
                            },
                        },
                        "required": ["i", "v", "e", "c"],
                    },
                },
            },
            "required": ["extractions"],
        },
    }


def paper_cache_key(pdf_text: str) -> str:
    """Return a stable per-paper prompt_cache_key derived from extracted text."""
    digest = hashlib.sha256(pdf_text.encode("utf-8")).hexdigest()[:16]
    prefix = PROMPT_CACHE_KEY_PREFIX.strip() or "scoping-review-v1"
    return f"{prefix}:{digest}"


def _response_text(response: Any) -> str:
    """Extract assistant text from OpenAI Responses API objects robustly."""
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    # SDK objects expose response.output as typed objects; dicts may appear in tests.
    for item in getattr(response, "output", []) or []:
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if item_type != "message":
            continue
        content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else [])
        for block in content or []:
            block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            if block_type in {"output_text", "text"}:
                text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
                if text:
                    return text
            refusal = getattr(block, "refusal", None) or (block.get("refusal") if isinstance(block, dict) else None)
            if refusal:
                raise RuntimeError(f"OpenAI refusal: {refusal}")

    # Last-resort debugging aid.
    try:
        return response.model_dump_json()
    except Exception:
        return json.dumps(response, default=str)
def _base_request_kwargs(model: str, pdf_text: str, user_msg: str, max_output_tokens: int) -> dict[str, Any]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "max_output_tokens": max_output_tokens,
        "text": {"format": _json_schema_format()},
        "prompt_cache_key": paper_cache_key(pdf_text),
    }
    # Some models reject the temperature parameter entirely. Omit it unless
    # OPENAI_TEMPERATURE is explicitly set in the environment.
    if TEMPERATURE is not None:
        request_kwargs["temperature"] = TEMPERATURE

    if PROMPT_CACHE_RETENTION:
        request_kwargs["prompt_cache_retention"] = PROMPT_CACHE_RETENTION
    return request_kwargs


async def warm_pdf_cache(
    pdf_text: str,
    semaphore: asyncio.Semaphore,
    pdf_name: str = "unknown",
    model: str = CHUNK_MODEL,
    required: bool = False,
) -> bool:
    """
    Prewarm the shared PDF prefix for a model.

    Warmup failures are logged and, by default, do not fail the extraction run.
    This preserves output quality and lets the real extraction proceed even when
    cache warmup is unavailable for the selected model/account.
    """
    tag = f"[{pdf_name} | warmup | {model}]"
    user_msg = build_cache_warmup_message(pdf_text)
    request_kwargs = _base_request_kwargs(
        model=model,
        pdf_text=pdf_text,
        user_msg=user_msg,
        max_output_tokens=CACHE_WARMUP_MAX_TOKENS,
    )

    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with semaphore:
                response = await _client.responses.create(**request_kwargs)
            log_cache_usage(response, tag)
            logger.info(f"{tag} ok (attempt {attempt})")
            return True
        except (RateLimitError, APIStatusError, APIConnectionError, APITimeoutError) as exc:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"{tag} OpenAI/API issue (attempt {attempt}/{MAX_RETRIES}), "
                f"sleeping {delay}s -- {exc}"
            )
            await asyncio.sleep(delay)
            last_exc = exc
        except Exception as exc:
            logger.warning(f"{tag} unexpected warmup failure: {exc}")
            last_exc = exc
            break

    if required:
        raise RuntimeError(f"{tag} failed after warmup attempts: {last_exc}") from last_exc

    logger.warning(f"{tag} failed; continuing without guaranteed prewarm")
    return False


async def extract_chunk(
    chunk_num: int,
    pdf_text: str,
    chunk_fields: list[dict],
    semaphore: asyncio.Semaphore,
    prior_context: Optional[list[dict]] = None,
    pdf_name: str = "unknown",
) -> list[dict]:
    """
    Call OpenAI for a single chunk with up to MAX_RETRIES attempts.

    Args:
        chunk_num:     Chunk number from 1 to NUM_CHUNKS.
        pdf_text:      Full paper text extracted once upstream.
        chunk_fields:  Extraction-map objects scoped to this chunk.
        semaphore:     Global API concurrency gate.
        prior_context: For the final synthesis chunk: combined output of prior chunks.
        pdf_name:      Used in log messages only.

    Returns:
        Validated list of extraction dicts for this chunk's fields.
    """
    model = SYNTHESIS_MODEL if chunk_num == 5 else CHUNK_MODEL
    max_tokens = CHUNK_MAX_TOKENS[chunk_num]
    expected_idx = _expected_indices(chunk_fields)
    user_msg = build_user_message(pdf_text, chunk_fields, prior_context)
    tag = f"[{pdf_name} | chunk {chunk_num} | {model}]"

    request_kwargs = _base_request_kwargs(
        model=model,
        pdf_text=pdf_text,
        user_msg=user_msg,
        max_output_tokens=max_tokens,
    )

    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with semaphore:
                response = await _client.responses.create(**request_kwargs)

            log_cache_usage(response, tag)
            raw = _response_text(response)
            result = validate_chunk_output(raw, expected_idx)
            logger.info(f"{tag} ok (attempt {attempt})")
            return result

        except RateLimitError as exc:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"{tag} rate-limited (attempt {attempt}/{MAX_RETRIES}), "
                f"sleeping {delay}s -- {exc}"
            )
            await asyncio.sleep(delay)
            last_exc = exc

        except (APIStatusError, APIConnectionError, APITimeoutError) as exc:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            status = getattr(exc, "status_code", "connection")
            logger.warning(
                f"{tag} OpenAI API error {status} (attempt {attempt}/{MAX_RETRIES}), "
                f"sleeping {delay}s -- {exc}"
            )
            await asyncio.sleep(delay)
            last_exc = exc

        except ValidationError as exc:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                f"{tag} validation failed (attempt {attempt}/{MAX_RETRIES}), "
                f"sleeping {delay}s -- {exc}"
            )
            await asyncio.sleep(delay)
            last_exc = exc

        except Exception as exc:
            logger.error(f"{tag} unexpected error: {exc}")
            raise

    raise RuntimeError(
        f"{tag} failed after {MAX_RETRIES} attempts. Last error: {last_exc}"
    ) from last_exc
