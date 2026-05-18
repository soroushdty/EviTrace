"""Property-based tests for manifest identity invalidation (Property 17).

**Property 17: Manifest identity invalidation**
For any manifest entry, when any identity component (pdf_content_hash,
config_hash, extraction_map_hash, model_id, schema_version) differs from
the current run's computed identity, the entry SHALL be treated as stale
and the PDF SHALL be re-processed.

**Validates: Requirements 12.1, 12.2**
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Import manifest module directly to avoid triggering heavy pipeline imports
# ---------------------------------------------------------------------------
_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "manifest.py"
_SPEC = importlib.util.spec_from_file_location("pipeline.manifest", _MANIFEST_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MANIFEST_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["pipeline.manifest"] = _MANIFEST_MODULE
_SPEC.loader.exec_module(_MANIFEST_MODULE)

ManifestIdentity = _MANIFEST_MODULE.ManifestIdentity
is_stale = _MANIFEST_MODULE.is_stale
_IDENTITY_FIELDS = _MANIFEST_MODULE._IDENTITY_FIELDS

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for generating valid SHA-256 hex digests (64 hex chars)
st_sha256 = st.text(
    alphabet="0123456789abcdef",
    min_size=64,
    max_size=64,
)

# Strategy for generating model IDs
st_model_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
)

# Strategy for generating schema version strings (semver-like)
st_schema_version = st.from_regex(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", fullmatch=True)

# Strategy for generating output paths
st_output_path = st.from_regex(r"[a-z][a-z0-9_]{0,20}\.extracted\.json", fullmatch=True)

# Strategy for generating a complete ManifestIdentity
st_manifest_identity = st.builds(
    ManifestIdentity,
    pdf_content_hash=st_sha256,
    config_hash=st_sha256,
    extraction_map_hash=st_sha256,
    model_id=st_model_id,
    schema_version=st_schema_version,
    output_path=st_output_path,
)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(identity=st_manifest_identity)
@settings(max_examples=100)
def test_identical_identity_is_not_stale(identity: ManifestIdentity):
    """When all identity fields match, the entry is NOT stale.

    This is the baseline property: an entry whose stored identity fields
    exactly match the current identity should not be treated as stale.

    **Validates: Requirements 12.1, 12.2**
    """
    # Build an entry dict that matches the current identity
    entry = identity.to_dict()

    assert not is_stale(entry, identity), (
        "Entry with identical identity fields should not be stale"
    )


@given(identity=st_manifest_identity, changed_hash=st_sha256)
@settings(max_examples=100)
def test_changed_pdf_content_hash_is_stale(
    identity: ManifestIdentity, changed_hash: str
):
    """When pdf_content_hash differs, the entry SHALL be stale.

    **Validates: Requirements 12.1, 12.2**
    """
    assume(changed_hash != identity.pdf_content_hash)

    entry = identity.to_dict()
    entry["pdf_content_hash"] = changed_hash

    assert is_stale(entry, identity), (
        "Entry with changed pdf_content_hash should be stale"
    )


@given(identity=st_manifest_identity, changed_hash=st_sha256)
@settings(max_examples=100)
def test_changed_config_hash_is_stale(
    identity: ManifestIdentity, changed_hash: str
):
    """When config_hash differs, the entry SHALL be stale.

    **Validates: Requirements 12.1, 12.2**
    """
    assume(changed_hash != identity.config_hash)

    entry = identity.to_dict()
    entry["config_hash"] = changed_hash

    assert is_stale(entry, identity), (
        "Entry with changed config_hash should be stale"
    )


@given(identity=st_manifest_identity, changed_hash=st_sha256)
@settings(max_examples=100)
def test_changed_extraction_map_hash_is_stale(
    identity: ManifestIdentity, changed_hash: str
):
    """When extraction_map_hash differs, the entry SHALL be stale.

    **Validates: Requirements 12.1, 12.2**
    """
    assume(changed_hash != identity.extraction_map_hash)

    entry = identity.to_dict()
    entry["extraction_map_hash"] = changed_hash

    assert is_stale(entry, identity), (
        "Entry with changed extraction_map_hash should be stale"
    )


@given(identity=st_manifest_identity, changed_model=st_model_id)
@settings(max_examples=100)
def test_changed_model_id_is_stale(
    identity: ManifestIdentity, changed_model: str
):
    """When model_id differs, the entry SHALL be stale.

    **Validates: Requirements 12.1, 12.2**
    """
    assume(changed_model != identity.model_id)

    entry = identity.to_dict()
    entry["model_id"] = changed_model

    assert is_stale(entry, identity), (
        "Entry with changed model_id should be stale"
    )


@given(identity=st_manifest_identity, changed_version=st_schema_version)
@settings(max_examples=100)
def test_changed_schema_version_is_stale(
    identity: ManifestIdentity, changed_version: str
):
    """When schema_version differs, the entry SHALL be stale.

    **Validates: Requirements 12.1, 12.2**
    """
    assume(changed_version != identity.schema_version)

    entry = identity.to_dict()
    entry["schema_version"] = changed_version

    assert is_stale(entry, identity), (
        "Entry with changed schema_version should be stale"
    )


@given(
    identity=st_manifest_identity,
    field_to_remove=st.sampled_from(list(_IDENTITY_FIELDS)),
)
@settings(max_examples=100)
def test_missing_identity_field_is_stale(
    identity: ManifestIdentity, field_to_remove: str
):
    """When any identity field is missing from the entry, it SHALL be stale.

    **Validates: Requirements 12.1, 12.2**
    """
    entry = identity.to_dict()
    del entry[field_to_remove]

    assert is_stale(entry, identity), (
        f"Entry missing '{field_to_remove}' should be stale"
    )


@given(
    identity=st_manifest_identity,
    field_to_null=st.sampled_from(list(_IDENTITY_FIELDS)),
)
@settings(max_examples=100)
def test_null_identity_field_is_stale(
    identity: ManifestIdentity, field_to_null: str
):
    """When any identity field is None in the entry, it SHALL be stale.

    The is_stale function checks `stored_value is None` as a staleness
    indicator, so a None value in any identity field means stale.

    **Validates: Requirements 12.1, 12.2**
    """
    entry = identity.to_dict()
    entry[field_to_null] = None

    assert is_stale(entry, identity), (
        f"Entry with None '{field_to_null}' should be stale"
    )


@given(
    identity=st_manifest_identity,
    field_to_change=st.sampled_from(list(_IDENTITY_FIELDS)),
    suffix=st.text(min_size=1, max_size=10, alphabet="xyz123"),
)
@settings(max_examples=100)
def test_any_single_field_change_causes_staleness(
    identity: ManifestIdentity, field_to_change: str, suffix: str
):
    """For any identity component, changing it causes the entry to be stale.

    This is the core property: ANY changed component causes staleness.

    **Validates: Requirements 12.1, 12.2**
    """
    entry = identity.to_dict()
    original_value = entry[field_to_change]
    # Mutate the field by appending a suffix (guaranteed different)
    entry[field_to_change] = original_value + suffix

    assert is_stale(entry, identity), (
        f"Entry with modified '{field_to_change}' should be stale"
    )
