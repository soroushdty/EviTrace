"""Unit tests for agents.openai.prompts — SYSTEM_PROMPT and message builders.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""
import importlib.util
import json
import sys
from pathlib import Path

# Load agents.openai.prompts directly from its file path so the test works
# regardless of how pytest resolves sys.path (--import-mode=importlib).
_AGENTS_ROOT = Path(__file__).resolve().parents[3]

# Ensure the real `agents` package is registered before loading prompts.py,
# so `from agents import agent_schema_validator` resolves correctly and not
# to the test-side `tests/agents/` package.
if "agents" not in sys.modules or not hasattr(sys.modules["agents"], "agent_schema_validator"):
    import importlib
    _agents_spec = importlib.util.spec_from_file_location(
        "agents",
        _AGENTS_ROOT / "agents" / "__init__.py",
        submodule_search_locations=[str(_AGENTS_ROOT / "agents")],
    )
    assert _agents_spec is not None and _agents_spec.loader is not None
    _agents_mod = importlib.util.module_from_spec(_agents_spec)
    sys.modules["agents"] = _agents_mod
    _agents_spec.loader.exec_module(_agents_mod)

_PROMPTS_PATH = _AGENTS_ROOT / "agents" / "openai" / "prompts.py"
_SPEC = importlib.util.spec_from_file_location("agents.openai.prompts", _PROMPTS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_PROMPTS_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["agents.openai.prompts"] = _PROMPTS_MODULE
_SPEC.loader.exec_module(_PROMPTS_MODULE)

SYSTEM_PROMPT = _PROMPTS_MODULE.get_system_prompt()
_shared_paper_prefix = _PROMPTS_MODULE._shared_paper_prefix
build_cache_warmup_message = _PROMPTS_MODULE.build_cache_warmup_message
build_user_message = _PROMPTS_MODULE.build_user_message


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT content tests
# ---------------------------------------------------------------------------

def test_system_prompt_contains_json_format():
    """SYSTEM_PROMPT must include the compact JSON output schema example."""
    assert '{"extractions":[' in SYSTEM_PROMPT


def test_system_prompt_contains_cache_warmup_instruction():
    """SYSTEM_PROMPT must instruct the model to handle CACHE WARMUP ONLY requests."""
    assert "CACHE WARMUP ONLY" in SYSTEM_PROMPT


def test_system_prompt_contains_confidence_tiers():
    """SYSTEM_PROMPT must instruct the model to follow the agent_schema (which defines confidence scale)."""
    assert "agent_schema" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# build_cache_warmup_message tests
# ---------------------------------------------------------------------------

def test_build_cache_warmup_message_starts_with_prefix():
    """Cache warmup message must start with the shared paper prefix (cache-stable)."""
    pkg = '{"sentences": [{"id": "s1", "text": "Sample evidence."}]}'
    warmup_msg = build_cache_warmup_message(pkg)
    expected_prefix = _shared_paper_prefix(pkg)
    assert warmup_msg.startswith(expected_prefix)


# ---------------------------------------------------------------------------
# build_user_message tests
# ---------------------------------------------------------------------------

def test_build_user_message_no_prior_context():
    """When prior_context is None, the message must not contain PRIOR EXTRACTION RESULTS."""
    pkg = '{"sentences": [{"id": "s1", "text": "Some evidence text."}]}'
    fields = [{"field_index": 3, "field_name": "Study design", "definition": "RCT or observational"}]
    result = build_user_message(pkg, fields, prior_context=None)
    assert "PRIOR EXTRACTION RESULTS" not in result


def test_build_user_message_with_prior_context():
    """When prior_context is provided, the message must include PRIOR EXTRACTION RESULTS and the serialised context."""
    pkg = '{"sentences": [{"id": "s1", "text": "Some evidence text."}]}'
    fields = [{"field_index": 5, "field_name": "Sample size", "definition": "Total N"}]
    prior_context = [{"i": 3, "v": "RCT", "loc": ["s1"], "c": "h"}]
    result = build_user_message(pkg, fields, prior_context=prior_context)
    assert "PRIOR EXTRACTION RESULTS" in result
    # build_user_message serialises prior_context with indent=2, ensure_ascii=False
    assert json.dumps(prior_context, indent=2, ensure_ascii=False) in result


def test_build_user_message_different_fields_same_prefix():
    """Two calls with the same source_package but different chunk_fields must share an identical leading prefix."""
    pkg = '{"sentences": [{"id": "s1", "text": "Evidence sentence one."}]}'
    fields_a = [{"field_index": 1, "field_name": "Author", "definition": "First author and year"}]
    fields_b = [
        {"field_index": 10, "field_name": "Intervention", "definition": "Intervention description"},
        {"field_index": 11, "field_name": "Comparator", "definition": "Comparator description"},
    ]

    msg_a = build_user_message(pkg, fields_a)
    msg_b = build_user_message(pkg, fields_b)

    expected_prefix = _shared_paper_prefix(pkg)
    prefix_len = len(expected_prefix)

    assert msg_a[:prefix_len] == expected_prefix
    assert msg_b[:prefix_len] == expected_prefix
    assert msg_a[:prefix_len] == msg_b[:prefix_len]
