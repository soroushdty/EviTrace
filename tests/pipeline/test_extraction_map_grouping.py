"""Unit tests for pipeline/extraction_map.py — field grouping and lookup.

Requirements: 7.1, 7.2, 7.3, 7.4

The module is loaded directly from its file path (bypassing pipeline/__init__.py
which chains to orchestrator → api_client → openai, an optional dependency).
"""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load extraction_map directly, bypassing pipeline/__init__.py
# ---------------------------------------------------------------------------

_EM_PATH = Path(__file__).resolve().parents[2] / "pipeline" / "extraction_map.py"
_EM_SPEC = importlib.util.spec_from_file_location("pipeline_extraction_map_direct", _EM_PATH)
assert _EM_SPEC is not None and _EM_SPEC.loader is not None
_EM_MODULE = importlib.util.module_from_spec(_EM_SPEC)
sys.modules[_EM_SPEC.name] = _EM_MODULE
_EM_SPEC.loader.exec_module(_EM_MODULE)

_build_field_lookup = _EM_MODULE._build_field_lookup
load_chunk_fields = _EM_MODULE.load_chunk_fields
_infer_chunk_field_ranges = _EM_MODULE._infer_chunk_field_ranges

# The module-level name for EXTRACTION_MAP inside the loaded module
_EM_MOD_NAME = _EM_SPEC.name  # "pipeline_extraction_map_direct"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _write_fake_map(tmp_path: Path, fields: list[dict]) -> Path:
    """Write *fields* as a JSON array to a temp file and return its path."""
    p = tmp_path / "extraction_map.json"
    p.write_text(json.dumps(fields), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_FIELDS_TWO_DOMAINS = [
    {"field_index": 1, "domain_group": "1. Study identification", "field_name": "First author"},
    {"field_index": 2, "domain_group": "1. Study identification", "field_name": "Publication year"},
    {"field_index": 3, "domain_group": "2. Clinical context",     "field_name": "Disease area"},
    {"field_index": 4, "domain_group": "2. Clinical context",     "field_name": "Intervention"},
]

# domain_to_chunk: domain prefix (int) → chunk number
_DOMAIN_TO_CHUNK_TWO = {1: 1, 2: 2}


# ---------------------------------------------------------------------------
# _build_field_lookup tests
# ---------------------------------------------------------------------------

def test_build_field_lookup_size(tmp_path):
    """N fields → lookup has exactly N entries keyed by field_index."""
    fake_path = _write_fake_map(tmp_path, _FIELDS_TWO_DOMAINS)
    with patch.object(_EM_MODULE, "EXTRACTION_MAP", fake_path):
        lookup = _build_field_lookup()

    assert len(lookup) == len(_FIELDS_TWO_DOMAINS)
    for field in _FIELDS_TWO_DOMAINS:
        assert field["field_index"] in lookup


def test_build_field_lookup_keys_and_values(tmp_path):
    """Each lookup entry contains domain_group and field_name with correct values."""
    fake_path = _write_fake_map(tmp_path, _FIELDS_TWO_DOMAINS)
    with patch.object(_EM_MODULE, "EXTRACTION_MAP", fake_path):
        lookup = _build_field_lookup()

    for field in _FIELDS_TWO_DOMAINS:
        entry = lookup[field["field_index"]]
        assert "domain_group" in entry
        assert "field_name" in entry
        assert entry["domain_group"] == field["domain_group"]
        assert entry["field_name"] == field["field_name"]


# ---------------------------------------------------------------------------
# load_chunk_fields tests
# ---------------------------------------------------------------------------

def test_load_chunk_fields_partition(tmp_path):
    """Every field appears in exactly one chunk's field list (no duplicates, no omissions)."""
    fake_path = _write_fake_map(tmp_path, _FIELDS_TWO_DOMAINS)
    fake_cfg = {"domain_to_chunk": _DOMAIN_TO_CHUNK_TWO}

    with patch.object(_EM_MODULE, "EXTRACTION_MAP", fake_path), \
         patch("utils.config_utils.load_openai_config", return_value=fake_cfg):
        chunks = load_chunk_fields()

    # Collect all field_index values across all chunks
    all_indices = []
    for chunk_fields in chunks.values():
        for f in chunk_fields:
            all_indices.append(f["field_index"])

    expected_indices = sorted(f["field_index"] for f in _FIELDS_TWO_DOMAINS)

    # Every field appears exactly once
    assert sorted(all_indices) == expected_indices
    assert len(all_indices) == len(set(all_indices)), "Some field appears in more than one chunk"


def test_load_chunk_fields_correct_assignment(tmp_path):
    """Each field lands in the chunk matching its domain_group prefix per DOMAIN_TO_CHUNK."""
    fake_path = _write_fake_map(tmp_path, _FIELDS_TWO_DOMAINS)
    fake_cfg = {"domain_to_chunk": _DOMAIN_TO_CHUNK_TWO}

    with patch.object(_EM_MODULE, "EXTRACTION_MAP", fake_path), \
         patch("utils.config_utils.load_openai_config", return_value=fake_cfg):
        chunks = load_chunk_fields()

    # Domain prefix 1 → chunk 1 (fields 1, 2); domain prefix 2 → chunk 2 (fields 3, 4)
    chunk1_indices = {f["field_index"] for f in chunks[1]}
    chunk2_indices = {f["field_index"] for f in chunks[2]}

    assert chunk1_indices == {1, 2}, f"Chunk 1 should contain fields 1,2 but got {chunk1_indices}"
    assert chunk2_indices == {3, 4}, f"Chunk 2 should contain fields 3,4 but got {chunk2_indices}"


# ---------------------------------------------------------------------------
# _infer_chunk_field_ranges — missing domain raises ValueError
# ---------------------------------------------------------------------------

def test_infer_chunk_field_ranges_missing_domain_raises(tmp_path):
    """Field with unmapped domain prefix → ValueError with domain name in message."""
    # Field belongs to domain 99 which is not in DOMAIN_TO_CHUNK
    fields_with_unknown_domain = [
        {"field_index": 10, "domain_group": "99. Unknown domain", "field_name": "Mystery field"},
    ]
    fake_path = _write_fake_map(tmp_path, fields_with_unknown_domain)
    # domain_to_chunk does not include 99
    fake_cfg = {"domain_to_chunk": {1: 1, 2: 2}}

    with patch.object(_EM_MODULE, "EXTRACTION_MAP", fake_path), \
         patch("utils.config_utils.load_openai_config", return_value=fake_cfg):
        with pytest.raises(ValueError, match="99"):
            _infer_chunk_field_ranges()


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

st_field_dict = st.fixed_dictionaries({
    "field_index": st.integers(min_value=1, max_value=62),
    "domain_group": st.text(min_size=1, max_size=30),
    "field_name": st.text(min_size=1, max_size=30),
})


# ---------------------------------------------------------------------------
# Property 7: _build_field_lookup size and structure invariant
# Validates: Requirements 7.1
# ---------------------------------------------------------------------------

@given(st.lists(st_field_dict, min_size=1, max_size=20))
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_build_field_lookup_pbt(tmp_path, fields):
    """Property 7: _build_field_lookup size and structure invariant.

    For any list of field dicts, _build_field_lookup returns a dict keyed by
    field_index with exactly as many entries as there are unique field_index
    values, and every entry contains domain_group and field_name.

    **Validates: Requirements 7.1**
    """
    fake_path = _write_fake_map(tmp_path, fields)
    with patch.object(_EM_MODULE, "EXTRACTION_MAP", fake_path):
        lookup = _build_field_lookup()

    # Size: one entry per unique field_index (duplicates collapse — last wins)
    unique_indices = {f["field_index"] for f in fields}
    assert len(lookup) == len(unique_indices), (
        f"Expected {len(unique_indices)} entries (unique field_index values), "
        f"got {len(lookup)}"
    )

    # Structure: every entry has domain_group and field_name
    for idx, entry in lookup.items():
        assert "domain_group" in entry, f"Entry for field_index={idx} missing 'domain_group'"
        assert "field_name" in entry, f"Entry for field_index={idx} missing 'field_name'"


# ---------------------------------------------------------------------------
# Property 8: load_chunk_fields partition invariant
# Validates: Requirements 7.2, 7.4
# ---------------------------------------------------------------------------

# load_chunk_fields uses range-based chunk assignment: it computes
# (min_field_index, max_field_index) per chunk across ALL domains assigned to
# that chunk, then filters all fields by those ranges.  For the partition
# invariant to hold, the field_index values of domains assigned to different
# chunks must not interleave — i.e., all fields in chunk A must have lower
# (or higher) field_index values than all fields in chunk B.
#
# Strategy: generate domains in two contiguous groups — group 1 gets the
# lower field_index block, group 2 gets the higher block.  This mirrors how
# the real extraction_map.json is structured (domains are ordered and chunks
# are contiguous ranges).

@st.composite
def _st_contiguous_chunk_fields(draw):
    """Generate field dicts where chunk assignments are contiguous blocks.

    Domains are split into two groups; group 1 fields all have lower
    field_index values than group 2 fields, so the range-based chunk
    assignment produces a clean partition.
    """
    # Number of domains in each chunk group (at least 1 each)
    n_chunk1 = draw(st.integers(min_value=1, max_value=3))
    n_chunk2 = draw(st.integers(min_value=1, max_value=3))

    # Block sizes per domain (1–4 fields each)
    sizes_chunk1 = [draw(st.integers(min_value=1, max_value=4)) for _ in range(n_chunk1)]
    sizes_chunk2 = [draw(st.integers(min_value=1, max_value=4)) for _ in range(n_chunk2)]

    fields = []
    next_fi = 1

    # Chunk 1 domains: domain prefixes 1..n_chunk1
    for domain_idx, block_size in enumerate(sizes_chunk1):
        domain_prefix = domain_idx + 1
        for _ in range(block_size):
            field_name = draw(st.text(min_size=1, max_size=30))
            fields.append({
                "field_index": next_fi,
                "domain_group": f"{domain_prefix}. Domain {domain_prefix}",
                "field_name": field_name,
            })
            next_fi += 1

    # Chunk 2 domains: domain prefixes n_chunk1+1..n_chunk1+n_chunk2
    for domain_idx, block_size in enumerate(sizes_chunk2):
        domain_prefix = n_chunk1 + domain_idx + 1
        for _ in range(block_size):
            field_name = draw(st.text(min_size=1, max_size=30))
            fields.append({
                "field_index": next_fi,
                "domain_group": f"{domain_prefix}. Domain {domain_prefix}",
                "field_name": field_name,
            })
            next_fi += 1

    # Build domain_to_chunk: first n_chunk1 domain prefixes → chunk 1,
    # next n_chunk2 domain prefixes → chunk 2
    domain_to_chunk: dict[int, int] = {}
    for i in range(1, n_chunk1 + 1):
        domain_to_chunk[i] = 1
    for i in range(n_chunk1 + 1, n_chunk1 + n_chunk2 + 1):
        domain_to_chunk[i] = 2

    return fields, domain_to_chunk


@given(_st_contiguous_chunk_fields())
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_load_chunk_fields_partition_pbt(tmp_path, fields_and_mapping):
    """Property 8: load_chunk_fields partition invariant.

    For any extraction map where chunk assignments are contiguous blocks of
    field_index values (mirroring the real extraction_map.json structure),
    load_chunk_fields assigns every field to exactly one chunk — no field
    appears in two chunks and no field is omitted.

    **Validates: Requirements 7.2, 7.4**
    """
    fields, domain_to_chunk = fields_and_mapping

    fake_path = _write_fake_map(tmp_path, fields)
    fake_cfg = {"domain_to_chunk": domain_to_chunk}

    with patch.object(_EM_MODULE, "EXTRACTION_MAP", fake_path), \
         patch("utils.config_utils.load_openai_config", return_value=fake_cfg):
        chunks = load_chunk_fields()

    # Collect all field_index values across all chunks
    all_indices_in_chunks: list[int] = []
    for chunk_fields in chunks.values():
        for entry in chunk_fields:
            all_indices_in_chunks.append(entry["field_index"])

    expected_indices = sorted(f["field_index"] for f in fields)

    # Every field appears in exactly one chunk (no omissions, no duplicates)
    assert sorted(all_indices_in_chunks) == expected_indices, (
        f"Partition mismatch: expected {expected_indices}, got {sorted(all_indices_in_chunks)}"
    )
    assert len(all_indices_in_chunks) == len(fields), (
        "Some field appears in more than one chunk or is missing"
    )
