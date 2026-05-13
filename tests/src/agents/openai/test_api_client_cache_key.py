"""PBT tests for agents/openai/api_client.py — paper_cache_key properties.

The module calls load_openai_config() at import time, so api_client is imported
via _import_api_client() which patches the config loader before the module loads.

paper_cache_key is a synchronous pure function — no asyncio.run needed.
"""
import re
import sys
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Ensure the project root is on sys.path.
_PROJECT_ROOT = str(Path(__file__).parent.parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Fake config — mirrors all keys that api_client.py reads at module import time
# ---------------------------------------------------------------------------

_FAKE_CONFIG = {
    "api_key": "test-key",
    "base_url": None,
    "chunk_model": "gpt-test",
    "synthesis_model": "gpt-test",
    "temperature": None,
    "prompt_cache_key_prefix": "test-prefix",
    "prompt_cache_retention": "",
    "cache_warmup_max_tokens": 32,
    "chunk_max_tokens": {1: 4096, 2: 4096, 3: 4096},
    "max_retries": 3,
    "retry_base_delay": 0,
    "num_chunks": 3,
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
    so that module-level constants are re-evaluated against _FAKE_CONFIG.
    Pre-stubs pipeline.pdf_processor to break the circular import.
    """
    for mod_name in list(sys.modules):
        if mod_name == "agents" or mod_name.startswith("agents."):
            del sys.modules[mod_name]

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
        if saved_pdf_processor is None:
            sys.modules.pop("pipeline.pdf_processor", None)
        else:
            sys.modules["pipeline.pdf_processor"] = saved_pdf_processor

    return m


# ---------------------------------------------------------------------------
# Module-level import — shared across all three PBT tests
# ---------------------------------------------------------------------------

_api_client = _import_api_client()


# ---------------------------------------------------------------------------
# Property 1: paper_cache_key determinism
# Validates: Requirements 2.1
# ---------------------------------------------------------------------------

@given(st.text(min_size=1))
@settings(max_examples=100)
def test_cache_key_deterministic(s: str):
    """**Property 1: paper_cache_key determinism**

    For any non-empty source_package string, calling paper_cache_key twice
    with the same input SHALL return the same string.

    Validates: Requirements 2.1
    """
    assert _api_client.paper_cache_key(s) == _api_client.paper_cache_key(s)


# ---------------------------------------------------------------------------
# Property 2: paper_cache_key format invariant
# Validates: Requirements 2.2
# ---------------------------------------------------------------------------

_CACHE_KEY_PATTERN = re.compile(r"^[^:]+:[0-9a-f]{16}$")


@given(st.text(min_size=1))
@settings(max_examples=100)
def test_cache_key_format(s: str):
    """**Property 2: paper_cache_key format invariant**

    For any non-empty source_package string, paper_cache_key SHALL return a
    string matching the pattern {prefix}:{16-hex-chars}.

    Validates: Requirements 2.2
    """
    result = _api_client.paper_cache_key(s)
    assert _CACHE_KEY_PATTERN.match(result), (
        f"paper_cache_key({s!r}) = {result!r} does not match "
        r"r'^[^:]+:[0-9a-f]{16}$'"
    )


# ---------------------------------------------------------------------------
# Property 3: paper_cache_key collision resistance
# Validates: Requirements 2.3
# ---------------------------------------------------------------------------

@given(st.text(min_size=1), st.text(min_size=1))
@settings(max_examples=100)
def test_cache_key_distinct_inputs(a: str, b: str):
    """**Property 3: paper_cache_key collision resistance**

    For any two distinct non-empty source_package strings a and b where
    a != b, paper_cache_key(a) SHALL not equal paper_cache_key(b).

    Validates: Requirements 2.3
    """
    assume(a != b)
    assert _api_client.paper_cache_key(a) != _api_client.paper_cache_key(b), (
        f"Collision: paper_cache_key({a!r}) == paper_cache_key({b!r}) == "
        f"{_api_client.paper_cache_key(a)!r}"
    )
