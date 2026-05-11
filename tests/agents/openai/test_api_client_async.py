"""Tests for agents/openai/api_client.py — async retry, error handling, and response parsing.

The module calls load_openai_config() at import time, so every test that needs
api_client must import it via _import_api_client() which patches the config
loader before the module is loaded.

Subsequent tasks (2.2, 2.3, 2.4) will add test classes/functions below the
infrastructure section.
"""
import sys
import asyncio
import importlib
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure the project root is on sys.path so that importlib.import_module can
# find agents.openai.api_client after sys.modules entries are cleared.
_PROJECT_ROOT = str(Path(__file__).parent.parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

# ---------------------------------------------------------------------------
# Fake config — mirrors all keys that api_client.py reads at module import time
# ---------------------------------------------------------------------------

_FAKE_CONFIG = {
    # OpenAI credentials / endpoints
    "api_key": "test-key",
    "base_url": None,
    # Models
    "chunk_model": "gpt-test",
    "synthesis_model": "gpt-test",
    "temperature": None,
    # Prompt cache
    "prompt_cache_key_prefix": "test-prefix",
    "prompt_cache_retention": "",
    # Token limits
    "cache_warmup_max_tokens": 32,
    "chunk_max_tokens": {1: 4096, 2: 4096, 3: 4096},
    # Retry / concurrency
    "max_retries": 3,
    "retry_base_delay": 0,  # zero so exponential-backoff tests run instantly
    "num_chunks": 3,
    # Pipeline flags (not read at import time but included for completeness)
    "enable_cache_prewarm": False,
    "global_api_limit": 5,
    "pdf_concurrency": 1,
    "prewarm_synthesis_if_model_diff": False,
    "domain_to_chunk": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
}


# ---------------------------------------------------------------------------
# _import_api_client — fresh module import with patched config
# ---------------------------------------------------------------------------

def _import_api_client():
    """Import agents.openai.api_client with a patched config loader.

    Clears any cached copies of the module (and its package) from sys.modules
    so that module-level constants are re-evaluated against _FAKE_CONFIG on
    every call.  This prevents cross-test state pollution.

    The import chain has a circular dependency:
      api_client → pipeline.validator → pipeline.__init__ → pipeline.orchestrator
      → pipeline.pdf_processor → agents.openai.api_client

    To break the cycle we pre-stub pipeline.pdf_processor in sys.modules before
    the import so that when orchestrator does ``from . import pdf_processor`` it
    gets the stub instead of triggering a re-import of api_client.
    We do NOT stub the pipeline package itself — Python needs the real package
    object to resolve pipeline.validator and other submodules.
    """
    # Remove all agents.* entries so the full package chain is re-imported with
    # the patched config.  Under pytest --import-mode=importlib the 'agents'
    # package object loaded by pytest does not expose the right __path__ for
    # importlib.import_module to find agents.openai.api_client, so we must
    # evict it too and let Python re-discover it from sys.path.
    for mod_name in list(sys.modules):
        if mod_name == "agents" or mod_name.startswith("agents."):
            del sys.modules[mod_name]

    # Pre-stub pipeline.pdf_processor to break the circular import.
    _pdf_processor_stub = MagicMock()
    _pdf_processor_stub.extract_chunk = MagicMock()
    _pdf_processor_stub.warm_pdf_cache = MagicMock()

    saved_pdf_processor = sys.modules.get("pipeline.pdf_processor")
    sys.modules["pipeline.pdf_processor"] = _pdf_processor_stub

    try:
        with (
            patch("utils.config_utils.load_openai_config", return_value=_FAKE_CONFIG),
            patch("utils.config_utils.load_qc_config", return_value={}),
        ):
            m = importlib.import_module("agents.openai.api_client")
    finally:
        # Restore original so subsequent imports of pipeline work normally.
        if saved_pdf_processor is None:
            sys.modules.pop("pipeline.pdf_processor", None)
        else:
            sys.modules["pipeline.pdf_processor"] = saved_pdf_processor

    return m


# ---------------------------------------------------------------------------
# _make_response — minimal OpenAI response mock
# ---------------------------------------------------------------------------

def _make_response(text: str):
    """Return a MagicMock that looks like an OpenAI Responses API response.

    Sets output_text so that _response_text() returns *text* on the fast path,
    and attaches a usage mock with the fields that log_cache_usage() inspects.
    """
    resp = MagicMock()
    resp.output_text = text
    resp.usage = MagicMock(
        input_tokens=10,
        output_tokens=5,
        input_tokens_details=MagicMock(cached_tokens=0),
    )
    return resp


# ---------------------------------------------------------------------------
# Task 2.2 — retry and error-handling tests for _call_api_with_retries
# ---------------------------------------------------------------------------

def _make_semaphore() -> asyncio.Semaphore:
    """Return a fresh Semaphore(5) for use in _call_api_with_retries calls."""
    return asyncio.Semaphore(5)


def test_rate_limit_retries_and_raises():
    """mock raises RateLimitError every call; assert RuntimeError raised;
    assert asyncio.sleep called MAX_RETRIES times.

    Requirements: 1.1, 1.2
    """
    api_client_mod = _import_api_client()
    max_retries = api_client_mod.MAX_RETRIES

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(
        side_effect=RateLimitError(
            message="rate limited",
            response=MagicMock(),
            body={},
        )
    )
    api_client_mod._client = mock_client

    request_kwargs = {"model": "gpt-test", "input": [], "max_output_tokens": 16}

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RuntimeError):
            asyncio.run(
                api_client_mod._call_api_with_retries(
                    request_kwargs,
                    _make_semaphore(),
                    "[test]",
                    required=True,
                )
            )

    assert mock_sleep.call_count == max_retries
    assert mock_client.responses.create.call_count == max_retries


def test_required_false_returns_none():
    """mock raises RateLimitError every call; required=False; assert None returned.

    Requirements: 1.3
    """
    api_client_mod = _import_api_client()

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(
        side_effect=RateLimitError(
            message="rate limited",
            response=MagicMock(),
            body={},
        )
    )
    api_client_mod._client = mock_client

    request_kwargs = {"model": "gpt-test", "input": [], "max_output_tokens": 16}

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(
            api_client_mod._call_api_with_retries(
                request_kwargs,
                _make_semaphore(),
                "[test]",
                required=False,
            )
        )

    assert result is None


@pytest.mark.parametrize(
    "exc_class",
    [APIStatusError, APIConnectionError, APITimeoutError],
)
def test_retryable_exceptions_parametrized(exc_class):
    """@pytest.mark.parametrize over [APIStatusError, APIConnectionError, APITimeoutError];
    assert same retry behaviour as RateLimitError.

    Requirements: 1.1, 1.2
    """
    api_client_mod = _import_api_client()
    max_retries = api_client_mod.MAX_RETRIES

    # APIStatusError requires a `response` argument; APIConnectionError and
    # APITimeoutError require an httpx.Request object.
    if exc_class is APIStatusError:
        exc_instance = exc_class(
            message="api error",
            response=MagicMock(),
            body={},
        )
    elif exc_class is APIConnectionError:
        exc_instance = exc_class(
            message="connection error",
            request=MagicMock(),
        )
    else:  # APITimeoutError
        exc_instance = exc_class(request=MagicMock())

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(side_effect=exc_instance)
    api_client_mod._client = mock_client

    request_kwargs = {"model": "gpt-test", "input": [], "max_output_tokens": 16}

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RuntimeError):
            asyncio.run(
                api_client_mod._call_api_with_retries(
                    request_kwargs,
                    _make_semaphore(),
                    "[test]",
                    required=True,
                )
            )

    assert mock_sleep.call_count == max_retries
    assert mock_client.responses.create.call_count == max_retries


def test_non_retryable_exception_reraises():
    """mock raises ValueError; assert ValueError propagated immediately;
    mock called exactly once.

    Requirements: 1.4
    """
    api_client_mod = _import_api_client()

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(side_effect=ValueError("unexpected"))
    api_client_mod._client = mock_client

    request_kwargs = {"model": "gpt-test", "input": [], "max_output_tokens": 16}

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(ValueError, match="unexpected"):
            asyncio.run(
                api_client_mod._call_api_with_retries(
                    request_kwargs,
                    _make_semaphore(),
                    "[test]",
                    required=True,
                )
            )

    # Non-retryable: called exactly once, no sleep
    assert mock_client.responses.create.call_count == 1
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2.3 — extract_chunk and warm_pdf_cache tests
# Requirements: 1.5, 1.6, 1.7, 1.8
# ---------------------------------------------------------------------------

import json as _json


def test_extract_chunk_happy_path():
    """Requirement 1.5: mock returns valid JSON on first attempt; assert returned
    raw text is a non-empty string.
    """
    api_client_mod = _import_api_client()

    # chunk_fields for chunk 1 — two fields with indices 3 and 4
    chunk_fields = [
        {"field_index": 3, "field_name": "Field 3", "definition": "def3"},
        {"field_index": 4, "field_name": "Field 4", "definition": "def4"},
    ]
    valid_json = _json.dumps({
        "extractions": [
            {"i": 3, "v": "value3", "loc": [], "c": "h"},
            {"i": 4, "v": "value4", "loc": [], "c": "m"},
        ]
    })

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(return_value=_make_response(valid_json))
    api_client_mod._client = mock_client

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(
            api_client_mod.extract_chunk(
                chunk_num=1,
                source_package="test-package",
                chunk_fields=chunk_fields,
                semaphore=asyncio.Semaphore(1),
            )
        )

    # extract_chunk now returns raw text; validation is the caller's responsibility
    assert isinstance(result, str)
    assert valid_json in result or "extractions" in result
    mock_client.responses.create.assert_called_once()


def test_extract_chunk_validation_failure_retries():
    """Requirement 1.6: mock returns invalid JSON every time; assert RuntimeError
    after MAX_RETRIES when the API itself fails (not validation).
    Note: validation is now the caller's responsibility (pipeline/pdf_processor.py).
    extract_chunk returns raw text and retries only on API errors.
    """
    api_client_mod = _import_api_client()

    chunk_fields = [
        {"field_index": 1, "field_name": "Field 1", "definition": "def1"},
        {"field_index": 2, "field_name": "Field 2", "definition": "def2"},
    ]

    from openai import RateLimitError
    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(
        side_effect=RateLimitError("rate limited", response=MagicMock(status_code=429), body=None)
    )
    api_client_mod._client = mock_client

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError):
            asyncio.run(
                api_client_mod.extract_chunk(
                    chunk_num=1,
                    source_package="test-package",
                    chunk_fields=chunk_fields,
                    semaphore=asyncio.Semaphore(1),
                )
            )

    # Should have been called MAX_RETRIES times (API error retries)
    assert mock_client.responses.create.call_count == api_client_mod.MAX_RETRIES


def test_warm_pdf_cache_returns_true():
    """Requirement 1.7: mock returns valid response; assert True."""
    api_client_mod = _import_api_client()

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(return_value=_make_response("warmup ok"))
    api_client_mod._client = mock_client

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(
            api_client_mod.warm_pdf_cache(
                source_package="test-package",
                semaphore=asyncio.Semaphore(1),
            )
        )

    assert result is True


def test_warm_pdf_cache_failure_returns_false():
    """Requirement 1.8: mock raises every call; assert False without raising."""
    api_client_mod = _import_api_client()

    mock_client = MagicMock()
    mock_client.responses = MagicMock()
    mock_client.responses.create = AsyncMock(
        side_effect=RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )
    )
    api_client_mod._client = mock_client

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = asyncio.run(
            api_client_mod.warm_pdf_cache(
                source_package="test-package",
                semaphore=asyncio.Semaphore(1),
            )
        )

    assert result is False


# ---------------------------------------------------------------------------
# Task 2.4 — _response_text tests
# Requirements: 1.9, 1.10, 1.11
# ---------------------------------------------------------------------------

class TestResponseText:
    """Tests for the _response_text helper (synchronous — no asyncio.run needed)."""

    def setup_method(self):
        self.api_client_mod = _import_api_client()

    def test_response_text_output_text_attr(self):
        """Requirement 1.9: response with output_text="hello" returns "hello" directly."""
        resp = MagicMock()
        resp.output_text = "hello"
        result = self.api_client_mod._response_text(resp)
        assert result == "hello"

    def test_response_text_output_list_message_block(self):
        """Requirement 1.10: response with output=[{type:"message", content:[{type:"text", text:"hello"}]}]
        returns "hello".
        """
        from types import SimpleNamespace

        text_block = SimpleNamespace(type="text", text="hello", refusal=None)
        message_item = SimpleNamespace(type="message", content=[text_block])

        resp = MagicMock()
        resp.output_text = None  # force fallthrough to output list path
        resp.output = [message_item]

        result = self.api_client_mod._response_text(resp)
        assert result == "hello"

    def test_response_text_refusal_raises(self):
        """Requirement 1.11: response with refusal="policy" raises RuntimeError with "policy" in message."""
        from types import SimpleNamespace

        refusal_block = SimpleNamespace(type="text", text=None, refusal="policy")
        message_item = SimpleNamespace(type="message", content=[refusal_block])

        resp = MagicMock()
        resp.output_text = None  # force fallthrough to output list path
        resp.output = [message_item]

        with pytest.raises(RuntimeError, match="policy"):
            self.api_client_mod._response_text(resp)
