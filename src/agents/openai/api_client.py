"""Async OpenAI API client with cache prewarm, extraction, retry, and usage logs."""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Optional

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from utils.config_utils import load_openai_config
from .prompts import get_system_prompt, build_cache_warmup_message, build_user_message
from utils.logging_utils import get_logger, log_cache_usage

_openai_config = load_openai_config()

CACHE_WARMUP_MAX_TOKENS: int = _openai_config["cache_warmup_max_tokens"]
CHUNK_MAX_TOKENS: dict[int, int] = _openai_config["chunk_max_tokens"]
CHUNK_MODEL: str = _openai_config["chunk_model"]
MAX_RETRIES: int = _openai_config["max_retries"]
OPENAI_API_KEY: str = _openai_config["api_key"]
OPENAI_BASE_URL: str | None = _openai_config["base_url"]
PROMPT_CACHE_KEY_PREFIX: str = _openai_config["prompt_cache_key_prefix"]
PROMPT_CACHE_RETENTION: str = _openai_config["prompt_cache_retention"]
RETRY_BASE_DELAY: int = _openai_config["retry_base_delay"]
SYNTHESIS_MODEL: str = _openai_config["synthesis_model"]
NUM_CHUNKS: int = _openai_config["num_chunks"]
TEMPERATURE: float | None = _openai_config["temperature"]

logger = get_logger(__name__)

# One shared async client for the entire run.
_client_kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
if OPENAI_BASE_URL:
    _client_kwargs["base_url"] = OPENAI_BASE_URL
_client = AsyncOpenAI(**_client_kwargs)
logger.debug(
    "OpenAI client initialised: base_url=%s, chunk_model=%s, synthesis_model=%s, "
    "temperature=%s, max_retries=%d, chunk_max_tokens=%s",
    OPENAI_BASE_URL or "default", CHUNK_MODEL, SYNTHESIS_MODEL,
    TEMPERATURE, MAX_RETRIES, CHUNK_MAX_TOKENS,
)


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
                            "loc": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "c": {
                                "type": "string",
                                "enum": ["h", "m", "l", "nr"],
                            },
                        },
                        "required": ["i", "v", "loc", "c"],
                    },
                },
            },
            "required": ["extractions"],
        },
    }


def paper_cache_key(source_package: str) -> str:
    """Return a stable per-paper prompt_cache_key derived from source package."""
    digest = hashlib.sha256(source_package.encode("utf-8")).hexdigest()[:16]
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


def _base_request_kwargs(model: str, source_package: str, user_msg: str, max_output_tokens: int) -> dict[str, Any]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": get_system_prompt()},
            {"role": "user", "content": user_msg},
        ],
        "max_output_tokens": max_output_tokens,
        "text": {"format": _json_schema_format()},
        "prompt_cache_key": paper_cache_key(source_package),
    }
    # Some models reject the temperature parameter entirely. Omit it unless
    # OPENAI_TEMPERATURE is explicitly set in the environment.
    if TEMPERATURE is not None:
        request_kwargs["temperature"] = TEMPERATURE

    if PROMPT_CACHE_RETENTION:
        request_kwargs["prompt_cache_retention"] = PROMPT_CACHE_RETENTION
    return request_kwargs


def _chunk_model_and_tokens(chunk_num: int) -> tuple[str, int]:
    """Return (model, max_output_tokens) for the given chunk number."""
    model = SYNTHESIS_MODEL if chunk_num == NUM_CHUNKS else CHUNK_MODEL
    return model, CHUNK_MAX_TOKENS[chunk_num]


# Max sleep we will honor from a Retry-After header. Beyond this we cap and
# let the next attempt fail fast rather than blocking the event loop for
# minutes on a server that's asking us to back off.
_MAX_RETRY_AFTER_SECONDS = 30.0


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    """Return the ``Retry-After`` value (in seconds) from an OpenAI exception, or None.

    Honors both integer-seconds and HTTP-date forms. Caps extreme values at
    _MAX_RETRY_AFTER_SECONDS so a misconfigured proxy can't freeze us.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    # httpx.Headers and dict both support .get().
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        secs = float(raw)
    except ValueError:
        # HTTP-date form: "Wed, 21 Oct 2026 07:28:00 GMT"
        try:
            from email.utils import parsedate_to_datetime  # noqa: PLC0415
            import datetime as _dt  # noqa: PLC0415
            target = parsedate_to_datetime(raw)
            if target is None:
                return None
            now = _dt.datetime.now(_dt.timezone.utc)
            if target.tzinfo is None:
                target = target.replace(tzinfo=_dt.timezone.utc)
            secs = (target - now).total_seconds()
        except Exception:
            return None
    if secs <= 0:
        return 0.0
    return min(secs, _MAX_RETRY_AFTER_SECONDS)


def _backoff_delay(attempt: int, exc: Exception) -> float:
    """Return the number of seconds to sleep before *attempt* is retried.

    Prefers the server's Retry-After header when present (the authoritative
    signal for 429 and some 503). Otherwise uses exponential backoff with
    RETRY_BASE_DELAY as the base, capped at _MAX_RETRY_AFTER_SECONDS so a
    pathological MAX_RETRIES value can't stall the event loop.
    """
    hinted = _retry_after_seconds(exc)
    if hinted is not None:
        return hinted
    return min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), _MAX_RETRY_AFTER_SECONDS)


async def _call_api_with_retries(
    request_kwargs: dict[str, Any],
    semaphore: asyncio.Semaphore,
    tag: str,
    *,
    required: bool = True,
) -> Any:
    """Execute an OpenAI Responses API call with exponential-backoff retries.

    Args:
        request_kwargs: Fully-built kwargs for _client.responses.create.
        semaphore:      Global API concurrency gate.
        tag:            Log prefix for this call (e.g. "[paper | chunk 2 | gpt-5]").
        required:       When False, log a warning and return None after exhausting
                        retries instead of raising (used for cache warmup).

    Returns:
        Raw API response object, or None if required=False and all attempts failed.

    Raises:
        RuntimeError: If required=True and all retry attempts are exhausted.
    """
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug(
                "%s request: model=%s, max_output_tokens=%s, input_msgs=%d, "
                "cache_key=%s, attempt=%d/%d",
                tag,
                request_kwargs.get("model"),
                request_kwargs.get("max_output_tokens"),
                len(request_kwargs.get("input", []) or []),
                request_kwargs.get("prompt_cache_key"),
                attempt, MAX_RETRIES,
            )
            async with semaphore:
                response = await _client.responses.create(**request_kwargs)
            log_cache_usage(response, tag)
            logger.debug("%s response status=%s", tag, getattr(response, "status", "?"))
            logger.info(f"{tag} ok (attempt {attempt})")
            return response

        except (RateLimitError, APIStatusError, APIConnectionError, APITimeoutError) as exc:
            delay = _backoff_delay(attempt, exc)
            logger.warning(
                f"{tag} API issue (attempt {attempt}/{MAX_RETRIES}), "
                f"sleeping {delay:.2f}s -- {exc}"
            )
            await asyncio.sleep(delay)
            last_exc = exc

        except Exception as exc:
            if required:
                logger.error(f"{tag} unexpected error: {exc}")
                raise
            logger.warning(f"{tag} unexpected failure: {exc}")
            last_exc = exc
            break

    if required:
        raise RuntimeError(
            f"{tag} failed after {MAX_RETRIES} attempts. Last error: {last_exc}"
        ) from last_exc

    logger.warning(f"{tag} failed; continuing without guaranteed prewarm")
    return None


async def warm_pdf_cache(
    source_package: str,
    semaphore: asyncio.Semaphore,
    pdf_name: str = "unknown",
    model: str = CHUNK_MODEL,
    required: bool = False,
    chunk_fields: Optional[list[dict]] = None,
    tag_suffix: str = "warmup",
) -> bool:
    """
    Prewarm the shared PDF prefix for a model.

    Pass ``chunk_fields`` to extend the warmed prefix past the end of the
    extraction map, which is what the synthesis chunk actually needs (its
    real prefix is ``shared_paper_prefix + extraction_map``; the trailing
    ``prior_context`` is data-dependent and cannot be cached).

    Warmup failures are logged and, by default, do not fail the extraction
    run. This preserves output quality and lets the real extraction proceed
    even when cache warmup is unavailable for the selected model/account.
    """
    tag = f"[{pdf_name} | {tag_suffix} | {model}]"
    user_msg = build_cache_warmup_message(source_package, chunk_fields=chunk_fields)
    request_kwargs = _base_request_kwargs(
        model=model,
        source_package=source_package,
        user_msg=user_msg,
        max_output_tokens=CACHE_WARMUP_MAX_TOKENS,
    )
    response = await _call_api_with_retries(request_kwargs, semaphore, tag, required=required)
    return response is not None


async def extract_chunk(
    chunk_num: int,
    source_package: str,
    chunk_fields: list[dict],
    semaphore: asyncio.Semaphore,
    valid_location_ids: set[str] | None = None,
    prior_context: Optional[list[dict]] = None,
    pdf_name: str = "unknown",
    repair_prompt: Optional[str] = None,
) -> str:
    """
    Call OpenAI for a single chunk with up to MAX_RETRIES attempts.

    Args:
        chunk_num:      Chunk number from 1 to NUM_CHUNKS.
        source_package: Compact evidence package extracted once upstream.
        chunk_fields:   Extraction-map objects scoped to this chunk.
        semaphore:      Global API concurrency gate.
        prior_context:  For the final synthesis chunk: combined output of prior chunks.
        pdf_name:       Used in log messages only.
        repair_prompt:  Optional repair prompt appended as a follow-up user message
                        when retrying after a validation failure.

    Returns:
        Raw response text from the API (validation is the caller's responsibility).
    """
    model, max_tokens = _chunk_model_and_tokens(chunk_num)
    user_msg = build_user_message(source_package, chunk_fields, prior_context)
    tag = f"[{pdf_name} | chunk {chunk_num} | {model}]"
    if repair_prompt:
        tag = f"[{pdf_name} | chunk {chunk_num} repair | {model}]"
    logger.debug(
        "%s extract_chunk: fields=%d (indices=%s), source_package_chars=%d, "
        "prior_context_fields=%d, user_msg_chars=%d, max_output_tokens=%d, repair=%s",
        tag,
        len(chunk_fields),
        _expected_indices(chunk_fields),
        len(source_package),
        len(prior_context) if prior_context else 0,
        len(user_msg),
        max_tokens,
        bool(repair_prompt),
    )

    request_kwargs = _base_request_kwargs(
        model=model,
        source_package=source_package,
        user_msg=user_msg,
        max_output_tokens=max_tokens,
    )

    # Append repair prompt as a follow-up user message if provided
    if repair_prompt:
        request_kwargs["input"].append({"role": "user", "content": repair_prompt})

    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug("%s attempt %d/%d: calling responses.create", tag, attempt, MAX_RETRIES)
            async with semaphore:
                response = await _client.responses.create(**request_kwargs)

            log_cache_usage(response, tag)
            raw = _response_text(response)
            logger.debug(
                "%s attempt %d raw response: %d chars, preview=%r",
                tag, attempt, len(raw), raw[:200],
            )
            logger.info(f"{tag} ok (attempt {attempt})")
            return raw

        except RateLimitError as exc:
            delay = _backoff_delay(attempt, exc)
            logger.warning(
                f"{tag} rate-limited (attempt {attempt}/{MAX_RETRIES}), "
                f"sleeping {delay:.2f}s -- {exc}"
            )
            await asyncio.sleep(delay)
            last_exc = exc

        except (APIStatusError, APIConnectionError, APITimeoutError) as exc:
            delay = _backoff_delay(attempt, exc)
            status = getattr(exc, "status_code", "connection")
            logger.warning(
                f"{tag} OpenAI API error {status} (attempt {attempt}/{MAX_RETRIES}), "
                f"sleeping {delay:.2f}s -- {exc}"
            )
            await asyncio.sleep(delay)
            last_exc = exc

        except Exception as exc:
            logger.error(f"{tag} unexpected error: {exc}")
            raise

    raise RuntimeError(
        f"{tag} failed after {MAX_RETRIES} attempts. Last error: {last_exc}"
    ) from last_exc
