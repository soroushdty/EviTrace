"""Unit tests for agents.openai.prompts — SYSTEM_PROMPT and message builders.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""
import importlib.util
import json
import sys
from pathlib import Path

# Load agents.openai.prompts directly from its file path so the test works
# regardless of how pytest resolves sys.path (--import-mode=importlib).
_AGENTS_ROOT = Path(__file__).resolve().parents[4] / "src"

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
get_system_prompt = _PROMPTS_MODULE.get_system_prompt
compute_stable_prefix = _PROMPTS_MODULE.compute_stable_prefix


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


# ---------------------------------------------------------------------------
# get_system_prompt() singleton caching (Requirements: 2.7)
# ---------------------------------------------------------------------------

def test_get_system_prompt_returns_same_object_reference():
    """get_system_prompt() must return the identical cached object (``is``,
    not just ``==``) on every call within the process lifetime."""
    first = get_system_prompt()
    second = get_system_prompt()
    third = get_system_prompt()
    assert first is second
    assert second is third


def test_get_system_prompt_module_level_cache_populated():
    """The module-level cache variable must be populated after a call and
    must be the same object returned by get_system_prompt()."""
    result = get_system_prompt()
    assert _PROMPTS_MODULE._CACHED_SYSTEM_PROMPT is result


# ---------------------------------------------------------------------------
# Field definitions sorted by field_index (Requirements: 2.3)
# ---------------------------------------------------------------------------

def test_build_user_message_sorts_field_definitions_by_field_index():
    """build_user_message must serialize chunk_fields sorted by field_index
    ascending, regardless of the order the caller supplies them in."""
    pkg = '{"sentences": [{"id": "s1", "text": "Evidence."}]}'
    fields_out_of_order = [
        {"field_index": 20, "field_name": "Outcome", "definition": "Primary outcome"},
        {"field_index": 3, "field_name": "Study design", "definition": "RCT or observational"},
        {"field_index": 11, "field_name": "Comparator", "definition": "Comparator description"},
    ]
    result = build_user_message(pkg, fields_out_of_order)

    sorted_fields = sorted(fields_out_of_order, key=lambda f: f["field_index"])
    assert json.dumps(sorted_fields, indent=2, ensure_ascii=False) in result

    # The unsorted serialization must NOT appear (would indicate no sorting happened).
    unsorted_json = json.dumps(fields_out_of_order, indent=2, ensure_ascii=False)
    if unsorted_json != json.dumps(sorted_fields, indent=2, ensure_ascii=False):
        assert unsorted_json not in result


def test_build_cache_warmup_message_sorts_field_definitions_by_field_index():
    """build_cache_warmup_message must also sort chunk_fields by field_index
    ascending when chunk_fields is provided."""
    pkg = '{"sentences": [{"id": "s1", "text": "Evidence."}]}'
    fields_out_of_order = [
        {"field_index": 45, "field_name": "Adverse events", "definition": "AE summary"},
        {"field_index": 1, "field_name": "Author", "definition": "First author and year"},
    ]
    result = build_cache_warmup_message(pkg, chunk_fields=fields_out_of_order)

    sorted_fields = sorted(fields_out_of_order, key=lambda f: f["field_index"])
    assert json.dumps(sorted_fields, indent=2, ensure_ascii=False) in result


def test_build_user_message_does_not_mutate_caller_chunk_fields():
    """Sorting must not mutate the caller's original list in place."""
    pkg = '{"sentences": []}'
    fields = [
        {"field_index": 9, "field_name": "B", "definition": ""},
        {"field_index": 2, "field_name": "A", "definition": ""},
    ]
    original_order = list(fields)
    build_user_message(pkg, fields)
    assert fields == original_order


# ---------------------------------------------------------------------------
# compute_stable_prefix() helper (Requirements: 2.4, 2.6, 2.7)
# ---------------------------------------------------------------------------

def test_compute_stable_prefix_deterministic():
    """The same inputs must produce byte-identical output across calls."""
    result_a = compute_stable_prefix("SYSTEM", "EVIDENCE", "RULES")
    result_b = compute_stable_prefix("SYSTEM", "EVIDENCE", "RULES")
    assert result_a == result_b


def test_compute_stable_prefix_contains_all_three_inputs_in_order():
    """The stable prefix must contain system_prompt, then evidence_package,
    then rules, in that fixed order (so it can be used as a canonical
    fingerprinting input for the Stable_Prefix hash)."""
    result = compute_stable_prefix("SYS_PART", "EVID_PART", "RULES_PART")
    assert "SYS_PART" in result
    assert "EVID_PART" in result
    assert "RULES_PART" in result
    assert result.index("SYS_PART") < result.index("EVID_PART") < result.index("RULES_PART")


def test_compute_stable_prefix_differs_when_evidence_differs():
    """Changing any one input must change the computed stable prefix."""
    base = compute_stable_prefix("SYS", "EVIDENCE_A", "RULES")
    changed = compute_stable_prefix("SYS", "EVIDENCE_B", "RULES")
    assert base != changed


def test_compute_stable_prefix_returns_str():
    result = compute_stable_prefix("s", "e", "r")
    assert isinstance(result, str)
