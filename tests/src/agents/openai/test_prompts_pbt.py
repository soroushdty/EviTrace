"""Property-based tests for agents.openai.prompts — build_user_message structural invariants.

**Property 4: build_user_message shared-prefix invariant** — Validates: Requirements 3.2, 3.3, 4.1
**Property 5: build_user_message source_package presence in prefix** — Validates: Requirements 4.2

Requirements: 4.1, 4.2
"""
import importlib.util
import sys
from pathlib import Path

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Load agents.openai.prompts directly from its file path so the test works
# regardless of how pytest resolves sys.path (--import-mode=importlib).
# This mirrors the pattern used in test_prompts_builders.py.
# ---------------------------------------------------------------------------
_AGENTS_ROOT = Path(__file__).resolve().parents[4] / "src"

# Ensure the real `agents` package is registered before loading prompts.py.
if "agents" not in sys.modules or not hasattr(sys.modules["agents"], "agent_schema_validator"):
    import importlib as _importlib
    _agents_spec = _importlib.util.spec_from_file_location(
        "agents",
        _AGENTS_ROOT / "agents" / "__init__.py",
        submodule_search_locations=[str(_AGENTS_ROOT / "agents")],
    )
    assert _agents_spec is not None and _agents_spec.loader is not None
    _agents_mod = _importlib.util.module_from_spec(_agents_spec)
    sys.modules["agents"] = _agents_mod
    _agents_spec.loader.exec_module(_agents_mod)

_PROMPTS_PATH = _AGENTS_ROOT / "agents" / "openai" / "prompts.py"
_SPEC = importlib.util.spec_from_file_location("agents.openai.prompts", _PROMPTS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_PROMPTS_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["agents.openai.prompts"] = _PROMPTS_MODULE
_SPEC.loader.exec_module(_PROMPTS_MODULE)

_shared_paper_prefix = _PROMPTS_MODULE._shared_paper_prefix
build_user_message = _PROMPTS_MODULE.build_user_message

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

st_source_package = st.text(min_size=1, max_size=500)

st_chunk_fields = st.lists(
    st.fixed_dictionaries({
        "field_index": st.integers(min_value=1, max_value=62),
        "field_name": st.text(min_size=1, max_size=50),
        "definition": st.text(max_size=100),
    }),
    min_size=1,
    max_size=10,
)

# ---------------------------------------------------------------------------
# Property 4: build_user_message shared-prefix invariant
# ---------------------------------------------------------------------------

@given(st_source_package, st_chunk_fields)
@settings(max_examples=100)
def test_shared_prefix_invariant(source_package: str, chunk_fields: list) -> None:
    """
    **Property 4: build_user_message shared-prefix invariant**

    For any source_package string and any chunk_fields list, the first
    len(_shared_paper_prefix(source_package)) characters of
    build_user_message(source_package, chunk_fields) SHALL be byte-identical
    to _shared_paper_prefix(source_package).

    Validates: Requirements 3.2, 3.3, 4.1
    """
    expected_prefix = _shared_paper_prefix(source_package)
    message = build_user_message(source_package, chunk_fields)

    prefix_len = len(expected_prefix)
    assert len(message) >= prefix_len, (
        f"Message is shorter than the expected prefix. "
        f"Message length: {len(message)}, prefix length: {prefix_len}"
    )
    assert message[:prefix_len] == expected_prefix, (
        f"First {prefix_len} chars of build_user_message are not byte-identical "
        f"to _shared_paper_prefix(source_package)."
    )


# ---------------------------------------------------------------------------
# Property 5: build_user_message source_package presence in prefix
# ---------------------------------------------------------------------------

@given(st_source_package, st_chunk_fields, st.lists(st.text()))
@settings(max_examples=100)
def test_source_package_in_prefix(
    source_package: str,
    chunk_fields: list,
    prior_context: list,
) -> None:
    """
    **Property 5: build_user_message source_package presence in prefix**

    For any source_package string, any chunk_fields list, and any prior_context
    list, the source_package text SHALL appear within the shared prefix section
    of the message produced by build_user_message.

    Validates: Requirements 4.2
    """
    # Build the shared prefix independently to know its boundaries
    shared_prefix = _shared_paper_prefix(source_package)

    # Build the full message (with prior_context as a list of text items)
    message = build_user_message(source_package, chunk_fields, prior_context=prior_context)

    # The source_package must appear within the shared prefix portion of the message
    prefix_section = message[:len(shared_prefix)]
    assert source_package in prefix_section, (
        f"source_package not found within the shared prefix section of the message. "
        f"source_package (first 80 chars): {source_package[:80]!r}"
    )
