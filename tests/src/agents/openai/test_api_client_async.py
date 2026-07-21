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
    """Return a fresh Semaphore(5) for use in _call_api_with_retries calls.

    On Python 3.9, asyncio.Semaphore() requires a running event loop, so we
    use a MagicMock that satisfies the acquire/release protocol instead.
    """
    mock_sem = MagicMock(spec=asyncio.Semaphore)
    mock_sem.__aenter__ = AsyncMock(return_value=None)
    mock_sem.__aexit__ = AsyncMock(return_value=None)
    return mock_sem


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
                semaphore=_make_semaphore(),
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
                    semaphore=_make_semaphore(),
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
                semaphore=_make_semaphore(),
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
                semaphore=_make_semaphore(),
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



# ---------------------------------------------------------------------------
# Retry-After header parsing + backoff cap
# ---------------------------------------------------------------------------


class _FakeHeaders(dict):
    """Minimal mapping that implements the tiny surface both dicts and
    httpx.Headers expose -- .get() with the header name."""


def _fake_exc_with_retry_after(value):
    """Build an APIStatusError-like mock carrying a Retry-After header."""
    exc = MagicMock(spec=APIStatusError)
    response = MagicMock()
    headers = _FakeHeaders()
    if value is not None:
        headers["retry-after"] = str(value)
    response.headers = headers
    exc.response = response
    return exc


class TestRetryAfterBackoff:
    """Regression tests for commit 11: Retry-After honored, backoff capped."""

    def test_retry_after_integer_seconds_is_honored(self):
        m = _import_api_client()
        exc = _fake_exc_with_retry_after(7)
        secs = m._retry_after_seconds(exc)
        assert secs == 7.0

    def test_retry_after_float_seconds_is_honored(self):
        m = _import_api_client()
        exc = _fake_exc_with_retry_after("0.25")
        secs = m._retry_after_seconds(exc)
        assert secs == 0.25

    def test_retry_after_missing_header_returns_none(self):
        m = _import_api_client()
        exc = _fake_exc_with_retry_after(None)
        assert m._retry_after_seconds(exc) is None

    def test_retry_after_no_response_on_exception_returns_none(self):
        m = _import_api_client()
        exc = MagicMock(spec=APIStatusError)
        # no .response attribute at all
        if hasattr(exc, "response"):
            del exc.response
        assert m._retry_after_seconds(exc) is None

    def test_retry_after_negative_clamped_to_zero(self):
        m = _import_api_client()
        exc = _fake_exc_with_retry_after(-5)
        assert m._retry_after_seconds(exc) == 0.0

    def test_retry_after_huge_value_is_capped(self):
        m = _import_api_client()
        exc = _fake_exc_with_retry_after(9999)
        # Must not return thousands of seconds -- we'd stall the event loop.
        assert m._retry_after_seconds(exc) == m._MAX_RETRY_AFTER_SECONDS

    def test_backoff_prefers_retry_after(self):
        m = _import_api_client()
        # With retry_base_delay=0 from _FAKE_CONFIG, exponential would be 0.
        # Retry-After of 3 must win.
        exc = _fake_exc_with_retry_after(3)
        assert m._backoff_delay(attempt=1, exc=exc) == 3.0

    def test_backoff_falls_back_to_exponential_without_header(self):
        m = _import_api_client()
        # _FAKE_CONFIG sets retry_base_delay=0, so any attempt should give 0.
        exc = _fake_exc_with_retry_after(None)
        assert m._backoff_delay(attempt=4, exc=exc) == 0.0

    def test_backoff_exponential_is_capped(self):
        m = _import_api_client()
        # Manually set RETRY_BASE_DELAY high so the cap kicks in.
        original = m.RETRY_BASE_DELAY
        m.RETRY_BASE_DELAY = 10
        try:
            exc = _fake_exc_with_retry_after(None)
            # 10 * 2^10 == 10240, must be capped.
            assert m._backoff_delay(attempt=11, exc=exc) == m._MAX_RETRY_AFTER_SECONDS
        finally:
            m.RETRY_BASE_DELAY = original


# ---------------------------------------------------------------------------
# Task 8.1 — telemetry integration in extract_chunk() / warm_pdf_cache()
# Requirements: 1.1, 1.2, 1.3, 1.5, 1.6
#
# Feature Flag Protocol: the "flag" is the optional ``collector`` parameter
# (default None). All tests above this section already prove the
# collector-absent (flag OFF) behavior is unchanged -- they call
# extract_chunk()/warm_pdf_cache() without a collector and were written
# before telemetry support existed. The tests below (flag ON) prove
# telemetry is correctly recorded when a collector IS supplied, and that a
# missing usage field is handled gracefully per Requirement 1.6.
#
# NOTE: TelemetryCollector is deliberately NOT imported at module level here
# (e.g. ``from agents.openai.telemetry import TelemetryCollector``). Under
# this project's ``--import-mode=importlib`` pytest configuration, a bare
# top-level ``agents.*`` import in a file under ``tests/src/agents/openai/``
# can resolve against the test-tree's own ``tests/src/agents/__init__.py`` /
# ``tests/src/agents/openai/__init__.py`` package (which mirrors the real
# package name) instead of the real ``src/agents`` package, depending on
# collection order -- the same hazard ``_import_api_client()``'s docstring
# already documents for ``agents.openai.api_client`` itself. Every test
# below instead reaches ``TelemetryCollector`` via the module object
# returned by ``_import_api_client()`` (``api_client_mod.TelemetryCollector``),
# which is guaranteed to be the same class object ``api_client.py`` imported
# from ``.telemetry`` in the same call.
# ---------------------------------------------------------------------------


def _make_response_no_usage(text: str):
    """Response mock with output_text set but usage=None (simulates a
    response payload with a missing usage field)."""
    resp = MagicMock()
    resp.output_text = text
    resp.usage = None
    return resp


class TestExtractChunkTelemetry:
    """Requirements 1.1, 1.2, 1.3, 1.5, 1.6: extract_chunk() telemetry recording."""

    def _chunk_fields(self):
        return [
            {"field_index": 3, "field_name": "Field 3", "definition": "def3"},
            {"field_index": 4, "field_name": "Field 4", "definition": "def4"},
        ]

    def test_no_collector_records_nothing_and_behavior_is_unchanged(self):
        """Flag OFF: collector=None (the default) must execute zero new code
        paths and return the exact same result as every pre-existing test
        above -- no TelemetryCollector needs to even exist for callers that
        don't pass one."""
        api_client_mod = _import_api_client()
        chunk_fields = self._chunk_fields()
        valid_json = _json.dumps({"extractions": [{"i": 3, "v": "v3", "loc": [], "c": "h"}]})

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
                    semaphore=_make_semaphore(),
                )
            )

        assert isinstance(result, str)
        assert "extractions" in result

    def test_collector_present_records_one_telemetry_record(self):
        """Requirements 1.1, 1.5: a TelemetryRecord is appended to the
        collector with usage-derived token counts and a PromptFingerprint."""
        api_client_mod = _import_api_client()
        chunk_fields = self._chunk_fields()
        valid_json = _json.dumps({"extractions": [{"i": 3, "v": "v3", "loc": [], "c": "h"}]})

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_make_response(valid_json))
        api_client_mod._client = mock_client

        collector = api_client_mod.TelemetryCollector()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(
                api_client_mod.extract_chunk(
                    chunk_num=1,
                    source_package="test-package",
                    chunk_fields=chunk_fields,
                    semaphore=_make_semaphore(),
                    collector=collector,
                )
            )

        records = collector.all_records()
        assert len(records) == 1
        record = records[0]
        assert record.stage == "extraction_chunk"
        assert record.model == api_client_mod.CHUNK_MODEL
        assert record.input_tokens == 10
        assert record.output_tokens == 5
        assert record.cached_input_tokens == 0
        assert record.uncached_input_tokens == 10
        assert record.total_tokens == 15
        assert record.field_index_start == 3
        assert record.field_index_end == 4
        assert record.prompt_fingerprint.prompt_version == api_client_mod.PROMPT_VERSION
        assert len(record.prompt_fingerprint.stable_prefix_hash) == 16

    def test_collector_present_synthesis_chunk_labeled_synthesis(self):
        """Requirement 1.3: the final chunk (chunk_num == NUM_CHUNKS) defaults
        to Stage "synthesis" when no explicit stage override is given."""
        api_client_mod = _import_api_client()
        num_chunks = api_client_mod.NUM_CHUNKS
        chunk_fields = [{"field_index": 1, "field_name": "Field 1", "definition": "def1"}]
        valid_json = _json.dumps({"extractions": [{"i": 1, "v": "v1", "loc": [], "c": "h"}]})

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_make_response(valid_json))
        api_client_mod._client = mock_client

        collector = api_client_mod.TelemetryCollector()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(
                api_client_mod.extract_chunk(
                    chunk_num=num_chunks,
                    source_package="test-package",
                    chunk_fields=chunk_fields,
                    semaphore=_make_semaphore(),
                    collector=collector,
                )
            )

        [record] = collector.all_records()
        assert record.stage == "synthesis"
        assert record.model == api_client_mod.SYNTHESIS_MODEL

    def test_collector_present_explicit_stage_override_wins(self):
        """Callers may override the default stage label (e.g. task 8.2's
        repair-loop caller will pass stage="validation_repair" explicitly)."""
        api_client_mod = _import_api_client()
        chunk_fields = self._chunk_fields()
        valid_json = _json.dumps({"extractions": [{"i": 3, "v": "v3", "loc": [], "c": "h"}]})

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_make_response(valid_json))
        api_client_mod._client = mock_client

        collector = api_client_mod.TelemetryCollector()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(
                api_client_mod.extract_chunk(
                    chunk_num=1,
                    source_package="test-package",
                    chunk_fields=chunk_fields,
                    semaphore=_make_semaphore(),
                    collector=collector,
                    stage="validation_repair",
                    repair_attempt=2,
                    error_type="schema",
                    domain_group="study_design",
                )
            )

        [record] = collector.all_records()
        assert record.stage == "validation_repair"
        assert record.repair_attempt == 2
        assert record.error_type == "schema"
        assert record.domain_group == "study_design"

    def test_collector_present_repair_prompt_defaults_to_validation_repair_stage(self):
        """Requirement 1.3: when repair_prompt is set and no explicit stage is
        given, the default stage label is "validation_repair"."""
        api_client_mod = _import_api_client()
        chunk_fields = self._chunk_fields()
        valid_json = _json.dumps({"extractions": [{"i": 3, "v": "v3", "loc": [], "c": "h"}]})

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_make_response(valid_json))
        api_client_mod._client = mock_client

        collector = api_client_mod.TelemetryCollector()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            asyncio.run(
                api_client_mod.extract_chunk(
                    chunk_num=1,
                    source_package="test-package",
                    chunk_fields=chunk_fields,
                    semaphore=_make_semaphore(),
                    collector=collector,
                    repair_prompt="fix field 3",
                )
            )

        [record] = collector.all_records()
        assert record.stage == "validation_repair"

    def test_missing_usage_field_records_zero_counts_and_does_not_raise(self, caplog):
        """Requirement 1.6: a response with usage=None must not raise --
        extract_chunk() still returns the raw text, and a zero-count
        TelemetryRecord is recorded with a WARNING logged."""
        import logging

        api_client_mod = _import_api_client()
        chunk_fields = self._chunk_fields()
        valid_json = _json.dumps({"extractions": [{"i": 3, "v": "v3", "loc": [], "c": "h"}]})

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(
            return_value=_make_response_no_usage(valid_json)
        )
        api_client_mod._client = mock_client

        collector = api_client_mod.TelemetryCollector()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with caplog.at_level(logging.WARNING):
                result = asyncio.run(
                    api_client_mod.extract_chunk(
                        chunk_num=1,
                        source_package="test-package",
                        chunk_fields=chunk_fields,
                        semaphore=_make_semaphore(),
                        collector=collector,
                    )
                )

        # The actual response flow is completely unaffected.
        assert isinstance(result, str)
        assert "extractions" in result

        [record] = collector.all_records()
        assert record.input_tokens == 0
        assert record.output_tokens == 0
        assert record.cached_input_tokens == 0
        assert record.uncached_input_tokens == 0
        assert record.total_tokens == 0
        assert any("usage" in r.message for r in caplog.records)

    def test_telemetry_recording_exception_never_breaks_extract_chunk(self):
        """The telemetry-recording call itself must never break the real
        extraction flow, even if it raises internally (e.g. a malformed
        collector). extract_chunk() must still return the raw response text."""
        api_client_mod = _import_api_client()
        chunk_fields = self._chunk_fields()
        valid_json = _json.dumps({"extractions": [{"i": 3, "v": "v3", "loc": [], "c": "h"}]})

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_make_response(valid_json))
        api_client_mod._client = mock_client

        class ExplodingCollector:
            def record(self, record):
                raise RuntimeError("collector is broken")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(
                api_client_mod.extract_chunk(
                    chunk_num=1,
                    source_package="test-package",
                    chunk_fields=chunk_fields,
                    semaphore=_make_semaphore(),
                    collector=ExplodingCollector(),
                )
            )

        assert isinstance(result, str)
        assert "extractions" in result


class TestWarmPdfCacheTelemetry:
    """Requirements 1.1, 1.3, 1.5, 1.6: warm_pdf_cache() telemetry recording."""

    def test_no_collector_records_nothing_and_behavior_is_unchanged(self):
        api_client_mod = _import_api_client()

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_make_response("warmup ok"))
        api_client_mod._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(
                api_client_mod.warm_pdf_cache(
                    source_package="test-package",
                    semaphore=_make_semaphore(),
                )
            )

        assert result is True

    def test_collector_present_records_cache_warmup_stage(self):
        api_client_mod = _import_api_client()

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(return_value=_make_response("warmup ok"))
        api_client_mod._client = mock_client

        collector = api_client_mod.TelemetryCollector()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(
                api_client_mod.warm_pdf_cache(
                    source_package="test-package",
                    semaphore=_make_semaphore(),
                    collector=collector,
                )
            )

        assert result is True
        [record] = collector.all_records()
        assert record.stage == "cache_warmup"
        assert record.model == api_client_mod.CHUNK_MODEL
        assert record.input_tokens == 10
        assert record.output_tokens == 5

    def test_collector_present_but_all_attempts_failed_records_nothing(self):
        """When warm_pdf_cache() exhausts retries with no successful response
        (required=False), there is no response to attribute usage to -- no
        TelemetryRecord should be fabricated."""
        api_client_mod = _import_api_client()

        mock_client = MagicMock()
        mock_client.responses = MagicMock()
        mock_client.responses.create = AsyncMock(
            side_effect=RateLimitError(
                message="rate limited", response=MagicMock(status_code=429, headers={}), body=None,
            )
        )
        api_client_mod._client = mock_client

        collector = api_client_mod.TelemetryCollector()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = asyncio.run(
                api_client_mod.warm_pdf_cache(
                    source_package="test-package",
                    semaphore=_make_semaphore(),
                    collector=collector,
                )
            )

        assert result is False
        assert collector.all_records() == []
