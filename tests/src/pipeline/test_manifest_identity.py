"""Unit tests for manifest identity and staleness detection.

Requirements: 12.1, 12.2, 12.3, 12.4, 12.5
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Import pipeline.manifest directly from its file to avoid triggering
# pipeline/__init__.py, which imports orchestrator → api_client → openai.
_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "manifest.py"
_SPEC = importlib.util.spec_from_file_location("pipeline.manifest", _MANIFEST_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MANIFEST_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["pipeline.manifest"] = _MANIFEST_MODULE
_SPEC.loader.exec_module(_MANIFEST_MODULE)

ManifestIdentity = _MANIFEST_MODULE.ManifestIdentity
compute_identity = _MANIFEST_MODULE.compute_identity
is_stale = _MANIFEST_MODULE.is_stale
load_manifest_with_identity_check = _MANIFEST_MODULE.load_manifest_with_identity_check
save_manifest = _MANIFEST_MODULE.save_manifest
_is_output_valid = _MANIFEST_MODULE._is_output_valid
_compute_file_sha256 = _MANIFEST_MODULE._compute_file_sha256
_compute_config_hash = _MANIFEST_MODULE._compute_config_hash
MANIFEST_SCHEMA_VERSION = _MANIFEST_MODULE.MANIFEST_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_pdf(tmp_path):
    """Create a sample PDF file for testing."""
    pdf_file = tmp_path / "test_paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 sample content for testing")
    return pdf_file


@pytest.fixture
def sample_config():
    """Return a minimal config dict for testing."""
    return {
        "chunk_model": "gpt-4o",
        "openai": {"chunk_model": "gpt-4o", "api_key": "test"},
        "extraction": {"num_chunks": 3},
        "quality_control": {"ocr": {"rasterization_dpi": 150}},
    }


@pytest.fixture
def sample_identity():
    """Return a sample ManifestIdentity for testing."""
    return ManifestIdentity(
        pdf_content_hash="abc123",
        config_hash="def456",
        extraction_map_hash="ghi789",
        model_id="gpt-4o",
        schema_version="1.0.0",
        output_path="test_paper.extracted.json",
    )


# ---------------------------------------------------------------------------
# ManifestIdentity dataclass tests
# ---------------------------------------------------------------------------


class TestManifestIdentity:
    """Tests for the ManifestIdentity dataclass."""

    def test_identity_is_frozen(self, sample_identity):
        """ManifestIdentity instances are immutable."""
        with pytest.raises(Exception):  # FrozenInstanceError
            sample_identity.pdf_content_hash = "new_hash"

    def test_to_dict_returns_all_fields(self, sample_identity):
        """to_dict() returns a dict with all identity fields."""
        d = sample_identity.to_dict()
        assert d == {
            "pdf_content_hash": "abc123",
            "config_hash": "def456",
            "extraction_map_hash": "ghi789",
            "model_id": "gpt-4o",
            "schema_version": "1.0.0",
            "output_path": "test_paper.extracted.json",
        }

    def test_identity_equality(self):
        """Two ManifestIdentity instances with same fields are equal."""
        id1 = ManifestIdentity("a", "b", "c", "d", "1.0.0", "out.json")
        id2 = ManifestIdentity("a", "b", "c", "d", "1.0.0", "out.json")
        assert id1 == id2


# ---------------------------------------------------------------------------
# compute_identity tests
# ---------------------------------------------------------------------------


class TestComputeIdentity:
    """Tests for the compute_identity function."""

    def test_computes_pdf_hash(self, sample_pdf, sample_config):
        """compute_identity includes the SHA-256 of the PDF file."""
        identity = compute_identity(sample_pdf, sample_config)
        expected_hash = _compute_file_sha256(sample_pdf)
        assert identity.pdf_content_hash == expected_hash
        assert identity.pdf_content_hash != "nohash"

    def test_computes_config_hash(self, sample_pdf, sample_config):
        """compute_identity includes a hash of the relevant config."""
        identity = compute_identity(sample_pdf, sample_config)
        expected_hash = _compute_config_hash(sample_config)
        assert identity.config_hash == expected_hash

    def test_uses_chunk_model_from_config(self, sample_pdf, sample_config):
        """compute_identity reads model_id from config when not provided."""
        identity = compute_identity(sample_pdf, sample_config)
        assert identity.model_id == "gpt-4o"

    def test_model_id_override(self, sample_pdf, sample_config):
        """compute_identity uses explicit model_id when provided."""
        identity = compute_identity(sample_pdf, sample_config, model_id="gpt-5")
        assert identity.model_id == "gpt-5"

    def test_output_path_derived_from_pdf_name(self, sample_pdf, sample_config):
        """compute_identity derives output_path from PDF filename."""
        identity = compute_identity(sample_pdf, sample_config)
        assert identity.output_path == "test_paper.extracted.json"

    def test_output_path_override(self, sample_pdf, sample_config):
        """compute_identity uses explicit output_path when provided."""
        identity = compute_identity(
            sample_pdf, sample_config, output_path="custom/output.json"
        )
        assert identity.output_path == "custom/output.json"

    def test_schema_version_is_current(self, sample_pdf, sample_config):
        """compute_identity uses the current MANIFEST_SCHEMA_VERSION."""
        identity = compute_identity(sample_pdf, sample_config)
        assert identity.schema_version == MANIFEST_SCHEMA_VERSION

    def test_different_pdf_content_different_hash(self, tmp_path, sample_config):
        """Different PDF content produces different pdf_content_hash."""
        pdf1 = tmp_path / "paper1.pdf"
        pdf2 = tmp_path / "paper2.pdf"
        pdf1.write_bytes(b"content A")
        pdf2.write_bytes(b"content B")

        id1 = compute_identity(pdf1, sample_config)
        id2 = compute_identity(pdf2, sample_config)
        assert id1.pdf_content_hash != id2.pdf_content_hash

    def test_different_config_different_hash(self, sample_pdf):
        """Different config produces different config_hash."""
        config1 = {"chunk_model": "gpt-4o", "openai": {"chunk_model": "gpt-4o"}}
        config2 = {"chunk_model": "gpt-5", "openai": {"chunk_model": "gpt-5"}}

        id1 = compute_identity(sample_pdf, config1)
        id2 = compute_identity(sample_pdf, config2)
        assert id1.config_hash != id2.config_hash

    def test_missing_pdf_returns_nohash(self, tmp_path, sample_config):
        """compute_identity returns 'nohash' for non-existent PDF."""
        missing_pdf = tmp_path / "nonexistent.pdf"
        identity = compute_identity(missing_pdf, sample_config)
        assert identity.pdf_content_hash == "nohash"


# ---------------------------------------------------------------------------
# is_stale tests
# ---------------------------------------------------------------------------


class TestIsStale:
    """Tests for the is_stale function."""

    def test_matching_entry_is_not_stale(self, sample_identity):
        """An entry with all matching identity fields is not stale."""
        entry = sample_identity.to_dict()
        entry["status"] = "complete"
        assert is_stale(entry, sample_identity) is False

    def test_different_pdf_hash_is_stale(self, sample_identity):
        """An entry with different pdf_content_hash is stale."""
        entry = sample_identity.to_dict()
        entry["pdf_content_hash"] = "different_hash"
        assert is_stale(entry, sample_identity) is True

    def test_different_config_hash_is_stale(self, sample_identity):
        """12.5 — Changing config hash triggers re-processing."""
        entry = sample_identity.to_dict()
        entry["config_hash"] = "changed_config"
        assert is_stale(entry, sample_identity) is True

    def test_different_extraction_map_hash_is_stale(self, sample_identity):
        """An entry with different extraction_map_hash is stale."""
        entry = sample_identity.to_dict()
        entry["extraction_map_hash"] = "new_map_hash"
        assert is_stale(entry, sample_identity) is True

    def test_different_model_id_is_stale(self, sample_identity):
        """An entry with different model_id is stale."""
        entry = sample_identity.to_dict()
        entry["model_id"] = "gpt-5"
        assert is_stale(entry, sample_identity) is True

    def test_different_schema_version_is_stale(self, sample_identity):
        """An entry with different schema_version is stale."""
        entry = sample_identity.to_dict()
        entry["schema_version"] = "2.0.0"
        assert is_stale(entry, sample_identity) is True

    def test_missing_identity_field_is_stale(self, sample_identity):
        """An entry missing an identity field is stale (legacy entry)."""
        entry = {"status": "complete"}  # No identity fields at all
        assert is_stale(entry, sample_identity) is True

    def test_output_path_not_checked_for_staleness(self, sample_identity):
        """output_path difference alone does NOT make an entry stale.

        output_path is checked separately for file existence/validity.
        """
        entry = sample_identity.to_dict()
        entry["output_path"] = "different/path.json"
        # output_path is not in _IDENTITY_FIELDS, so this should not be stale
        assert is_stale(entry, sample_identity) is False


# ---------------------------------------------------------------------------
# _is_output_valid tests
# ---------------------------------------------------------------------------


class TestIsOutputValid:
    """Tests for the _is_output_valid helper."""

    def test_valid_output_file(self, tmp_path):
        """Returns True when output file exists and is valid JSON."""
        out_file = tmp_path / "paper.extracted.json"
        out_file.write_text(json.dumps([{"field_index": 1}]), encoding="utf-8")
        entry = {"output_path": "paper.extracted.json"}
        assert _is_output_valid(entry, output_dir=tmp_path) is True

    def test_missing_output_file(self, tmp_path):
        """Returns False when output file does not exist."""
        entry = {"output_path": "nonexistent.json"}
        assert _is_output_valid(entry, output_dir=tmp_path) is False

    def test_corrupt_output_file(self, tmp_path):
        """Returns False when output file is not valid JSON."""
        out_file = tmp_path / "paper.extracted.json"
        out_file.write_text("not valid json {{{", encoding="utf-8")
        entry = {"output_path": "paper.extracted.json"}
        assert _is_output_valid(entry, output_dir=tmp_path) is False

    def test_no_output_path_in_entry(self, tmp_path):
        """Returns False when entry has no output_path field."""
        entry = {"status": "complete"}
        assert _is_output_valid(entry, output_dir=tmp_path) is False


# ---------------------------------------------------------------------------
# load_manifest_with_identity_check tests
# ---------------------------------------------------------------------------


class TestLoadManifestWithIdentityCheck:
    """Tests for load_manifest_with_identity_check."""

    def test_stale_entry_reset(self, tmp_path):
        """12.4 — Stale entries are logged at INFO and status is reset."""
        identity = ManifestIdentity(
            pdf_content_hash="new_hash",
            config_hash="new_config",
            extraction_map_hash="new_map",
            model_id="gpt-4o",
            schema_version="1.0.0",
            output_path="paper.extracted.json",
        )

        manifest_data = {
            "paper.pdf": {
                "status": "complete",
                "pdf_content_hash": "old_hash",
                "config_hash": "old_config",
                "extraction_map_hash": "old_map",
                "model_id": "gpt-4o",
                "schema_version": "1.0.0",
                "output_path": "paper.extracted.json",
            }
        }

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

        with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
            result = load_manifest_with_identity_check(
                {"paper.pdf": identity}, output_dir=tmp_path
            )

        assert result["paper.pdf"]["status"] == "pending"

    def test_complete_entry_with_missing_output_reset(self, tmp_path):
        """12.3 — Complete entry with missing output file is reset."""
        identity = ManifestIdentity(
            pdf_content_hash="hash1",
            config_hash="cfg1",
            extraction_map_hash="map1",
            model_id="gpt-4o",
            schema_version="1.0.0",
            output_path="paper.extracted.json",
        )

        manifest_data = {
            "paper.pdf": {
                "status": "complete",
                "pdf_content_hash": "hash1",
                "config_hash": "cfg1",
                "extraction_map_hash": "map1",
                "model_id": "gpt-4o",
                "schema_version": "1.0.0",
                "output_path": "paper.extracted.json",
            }
        }

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

        # No output file exists in tmp_path
        with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
            result = load_manifest_with_identity_check(
                {"paper.pdf": identity}, output_dir=tmp_path
            )

        assert result["paper.pdf"]["status"] == "pending"

    def test_valid_complete_entry_preserved(self, tmp_path):
        """A valid complete entry with matching identity is preserved."""
        identity = ManifestIdentity(
            pdf_content_hash="hash1",
            config_hash="cfg1",
            extraction_map_hash="map1",
            model_id="gpt-4o",
            schema_version="1.0.0",
            output_path="paper.extracted.json",
        )

        manifest_data = {
            "paper.pdf": {
                "status": "complete",
                "pdf_content_hash": "hash1",
                "config_hash": "cfg1",
                "extraction_map_hash": "map1",
                "model_id": "gpt-4o",
                "schema_version": "1.0.0",
                "output_path": "paper.extracted.json",
            }
        }

        # Create valid output file
        out_file = tmp_path / "paper.extracted.json"
        out_file.write_text(json.dumps([{"field_index": 1}]), encoding="utf-8")

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

        with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
            result = load_manifest_with_identity_check(
                {"paper.pdf": identity}, output_dir=tmp_path
            )

        assert result["paper.pdf"]["status"] == "complete"

    def test_config_hash_change_triggers_reprocessing(self, tmp_path):
        """12.5 — Changing config hash for a completed PDF triggers re-processing."""
        # Old identity in manifest
        manifest_data = {
            "paper.pdf": {
                "status": "complete",
                "pdf_content_hash": "same_hash",
                "config_hash": "old_config_hash",
                "extraction_map_hash": "same_map",
                "model_id": "gpt-4o",
                "schema_version": "1.0.0",
                "output_path": "paper.extracted.json",
            }
        }

        # New identity with different config_hash
        new_identity = ManifestIdentity(
            pdf_content_hash="same_hash",
            config_hash="new_config_hash",
            extraction_map_hash="same_map",
            model_id="gpt-4o",
            schema_version="1.0.0",
            output_path="paper.extracted.json",
        )

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

        with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
            result = load_manifest_with_identity_check(
                {"paper.pdf": new_identity}, output_dir=tmp_path
            )

        assert result["paper.pdf"]["status"] == "pending"

    def test_entry_not_in_current_identities_skips_staleness_check(self, tmp_path):
        """Entries for PDFs not in current run are not checked for staleness."""
        manifest_data = {
            "old_paper.pdf": {
                "status": "complete",
                "pdf_content_hash": "old_hash",
                "config_hash": "old_cfg",
                "extraction_map_hash": "old_map",
                "model_id": "gpt-3.5",
                "schema_version": "0.9.0",
                "output_path": "old_paper.extracted.json",
            }
        }

        # Create valid output file for old_paper
        out_file = tmp_path / "old_paper.extracted.json"
        out_file.write_text(json.dumps([{"field_index": 1}]), encoding="utf-8")

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

        # current_identities does NOT include old_paper.pdf
        with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
            result = load_manifest_with_identity_check({}, output_dir=tmp_path)

        # Entry should be preserved (not reset) since it's not in current run
        assert result["old_paper.pdf"]["status"] == "complete"

    def test_corrupt_manifest_returns_empty(self, tmp_path):
        """Corrupt manifest file results in empty dict."""
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text("not valid json {{{", encoding="utf-8")

        with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
            result = load_manifest_with_identity_check({}, output_dir=tmp_path)

        assert result == {}
