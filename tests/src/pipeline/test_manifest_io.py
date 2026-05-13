"""Unit tests for pipeline/manifest.py — load_manifest and save_manifest.

Requirements: 5.1, 5.2, 5.3, 5.4
"""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Import pipeline.manifest directly from its file to avoid triggering
# pipeline/__init__.py, which imports orchestrator → api_client → openai.
_MANIFEST_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "manifest.py"
_SPEC = importlib.util.spec_from_file_location("pipeline.manifest", _MANIFEST_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MANIFEST_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules["pipeline.manifest"] = _MANIFEST_MODULE
_SPEC.loader.exec_module(_MANIFEST_MODULE)

load_manifest = _MANIFEST_MODULE.load_manifest
save_manifest = _MANIFEST_MODULE.save_manifest


def test_load_manifest_missing_file(tmp_path):
    """5.1 — load_manifest returns {} when the manifest file does not exist."""
    non_existent = tmp_path / "does_not_exist.json"
    with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", non_existent):
        result = load_manifest()
    assert result == {}


def test_load_manifest_valid_json(tmp_path):
    """5.2 — load_manifest returns the parsed dict when the file contains valid JSON."""
    manifest_path = tmp_path / "manifest.json"
    data = {"paper1.pdf": {"status": "complete"}, "paper2.pdf": {"status": "failed_chunks"}}
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
        result = load_manifest()

    assert result == data


def test_save_manifest_writes_valid_json(tmp_path):
    """5.3 — save_manifest writes a valid JSON file equal to the input dict."""
    manifest_path = tmp_path / "manifest.json"
    data = {"paper1.pdf": {"status": "complete"}, "paper2.pdf": {"status": "failed_qc_pipeline"}}

    with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
        save_manifest(data)

    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written == data


def test_save_load_round_trip(tmp_path):
    """5.4 — save_manifest followed by load_manifest returns the original dict."""
    manifest_path = tmp_path / "manifest.json"
    data = {
        "paper_a.pdf": {"status": "complete", "chunks": 3},
        "paper_b.pdf": {"status": "failed_chunks"},
    }

    with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
        save_manifest(data)
        result = load_manifest()

    assert result == data

# ---------------------------------------------------------------------------
# Property-Based Tests
# ---------------------------------------------------------------------------
import tempfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Strategy for generating arbitrary JSON-serialisable manifest dicts.
st_json_manifest = st.dictionaries(
    keys=st.text(min_size=1, max_size=50),
    values=st.one_of(
        st.text(max_size=100),
        st.integers(),
        st.booleans(),
        st.none(),
    ),
    max_size=20,
)


@given(manifest=st_json_manifest)
@settings(max_examples=50)
def test_manifest_round_trip_pbt(manifest):
    """Property 6: manifest save/load round-trip.

    For any dict with string keys and JSON-serialisable values, calling
    save_manifest followed by load_manifest SHALL return a dict equal to
    the original.

    **Validates: Requirements 5.3, 5.4, 6.1**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        manifest_path = Path(tmp_dir) / "manifest.json"
        with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_path):
            save_manifest(manifest)
            result = load_manifest()
    assert result == manifest
