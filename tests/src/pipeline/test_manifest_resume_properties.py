"""
Property-based tests for resume validation (Property 16).

Feature: audit-remediation

Property 16: Resume validates output file integrity
For any manifest entry marked "complete" whose output file either does not
exist or fails JSON parsing, the entry SHALL be treated as incomplete and
the PDF SHALL be re-processed.

**Validates: Requirements 11.5, 12.3**
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Import modules directly from file paths to avoid triggering heavy deps
# via pipeline/__init__.py → orchestrator → api_client → openai.
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parents[3] / "src"

# Import pipeline.manifest
_MANIFEST_PATH = _SRC_DIR / "pipeline" / "manifest.py"
_MANIFEST_SPEC = importlib.util.spec_from_file_location("pipeline.manifest", _MANIFEST_PATH)
assert _MANIFEST_SPEC is not None and _MANIFEST_SPEC.loader is not None
_MANIFEST_MODULE = importlib.util.module_from_spec(_MANIFEST_SPEC)
sys.modules.setdefault("pipeline.manifest", _MANIFEST_MODULE)
_MANIFEST_SPEC.loader.exec_module(_MANIFEST_MODULE)

_is_output_valid = _MANIFEST_MODULE._is_output_valid
load_manifest_with_identity_check = _MANIFEST_MODULE.load_manifest_with_identity_check
ManifestIdentity = _MANIFEST_MODULE.ManifestIdentity
MANIFEST_SCHEMA_VERSION = _MANIFEST_MODULE.MANIFEST_SCHEMA_VERSION

# Import pipeline.pdf_processor (needs pipeline.manifest already loaded)
_PDF_PROC_PATH = _SRC_DIR / "pipeline" / "pdf_processor.py"
_PDF_PROC_SPEC = importlib.util.spec_from_file_location("pipeline.pdf_processor", _PDF_PROC_PATH)
assert _PDF_PROC_SPEC is not None and _PDF_PROC_SPEC.loader is not None
_PDF_PROC_MODULE = importlib.util.module_from_spec(_PDF_PROC_SPEC)
sys.modules.setdefault("pipeline.pdf_processor", _PDF_PROC_MODULE)
_PDF_PROC_SPEC.loader.exec_module(_PDF_PROC_MODULE)

_load_completed_result = _PDF_PROC_MODULE._load_completed_result


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for valid JSON-serializable extraction field data
_valid_field_st = st.fixed_dictionaries({
    "field_index": st.integers(min_value=1, max_value=62),
    "domain_group": st.integers(min_value=1, max_value=13),
    "field_name": st.text(min_size=1, max_size=30),
    "extracted_value": st.text(max_size=100),
    "evidence": st.text(max_size=100),
    "location": st.lists(st.text(min_size=1, max_size=20), max_size=3),
    "location_metadata": st.just([]),
    "confidence": st.sampled_from(["h", "m", "l", "nr"]),
})

# Strategy for valid output data (list of field dicts)
_valid_output_st = st.lists(_valid_field_st, min_size=1, max_size=5)

# Strategy for corrupt/non-JSON content
_corrupt_content_st = st.one_of(
    # Truncated JSON
    st.builds(lambda data: json.dumps(data)[:5], _valid_output_st),
    # Random non-JSON text
    st.text(min_size=1, max_size=200).filter(
        lambda t: _is_not_valid_json(t)
    ),
    # Partial JSON object
    st.just('{"field_index": 1, "domain_group":'),
    # Unbalanced brackets
    st.just('[{"field_index": 1}'),
    # Binary-like garbage
    st.binary(min_size=1, max_size=50).map(
        lambda b: b.decode("latin-1")
    ),
)

# Strategy for pdf names (simple alphanumeric)
_pdf_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=30,
)


def _is_not_valid_json(text: str) -> bool:
    """Return True if text is NOT valid JSON."""
    try:
        json.loads(text)
        return False
    except (json.JSONDecodeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Property 16: Resume validates output file integrity
# ---------------------------------------------------------------------------


@given(
    pdf_name=_pdf_name_st,
    valid_data=_valid_output_st,
)
@settings(max_examples=100)
def test_property_16_valid_output_returns_cached_result(pdf_name, valid_data):
    """For any manifest entry marked "complete" whose output file exists and
    contains valid JSON, _load_completed_result SHALL return the parsed data
    (PDF is NOT re-processed).

    **Validates: Requirements 11.5, 12.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        out_file = output_dir / f"{pdf_name}.extracted.json"
        out_file.write_text(json.dumps(valid_data), encoding="utf-8")

        manifest = {pdf_name: {"status": "complete"}}

        with patch.object(_PDF_PROC_MODULE, "OUTPUT_DIR", output_dir):
            result = _load_completed_result(pdf_name, manifest)

        # Valid output → returns cached data, no re-processing
        assert result is not None
        assert result == valid_data


@given(
    pdf_name=_pdf_name_st,
    corrupt_content=_corrupt_content_st,
)
@settings(max_examples=100)
def test_property_16_corrupt_output_treated_as_absent(pdf_name, corrupt_content):
    """For any manifest entry marked "complete" whose output file exists but
    fails JSON parsing, _load_completed_result SHALL return None — treating
    the file as absent and triggering re-processing.

    **Validates: Requirements 11.5, 12.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        out_file = output_dir / f"{pdf_name}.extracted.json"
        out_file.write_text(corrupt_content, encoding="utf-8")

        manifest = {pdf_name: {"status": "complete"}}

        with patch.object(_PDF_PROC_MODULE, "OUTPUT_DIR", output_dir):
            result = _load_completed_result(pdf_name, manifest)

        # Corrupt output → treated as absent, returns None for re-processing
        assert result is None


@given(pdf_name=_pdf_name_st)
@settings(max_examples=100)
def test_property_16_missing_output_treated_as_absent(pdf_name):
    """For any manifest entry marked "complete" whose output file does not
    exist on disk, _load_completed_result SHALL return None — triggering
    re-processing.

    **Validates: Requirements 11.5, 12.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        # Do NOT create the output file

        manifest = {pdf_name: {"status": "complete"}}

        with patch.object(_PDF_PROC_MODULE, "OUTPUT_DIR", output_dir):
            result = _load_completed_result(pdf_name, manifest)

        # Missing output → treated as absent, returns None for re-processing
        assert result is None


@given(
    pdf_name=_pdf_name_st,
    corrupt_content=_corrupt_content_st,
)
@settings(max_examples=100)
def test_property_16_is_output_valid_rejects_corrupt_files(pdf_name, corrupt_content):
    """For any manifest entry whose output file fails JSON parsing,
    _is_output_valid SHALL return False — the entry is NOT considered
    complete and the PDF SHALL be re-processed.

    **Validates: Requirements 11.5, 12.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        output_filename = f"{pdf_name}.extracted.json"
        out_file = output_dir / output_filename
        out_file.write_text(corrupt_content, encoding="utf-8")

        entry = {"status": "complete", "output_path": output_filename}

        result = _is_output_valid(entry, output_dir=output_dir)

        # Corrupt file → not valid
        assert result is False


@given(
    pdf_name=_pdf_name_st,
    valid_data=_valid_output_st,
)
@settings(max_examples=100)
def test_property_16_is_output_valid_accepts_valid_files(pdf_name, valid_data):
    """For any manifest entry whose output file exists and contains valid JSON,
    _is_output_valid SHALL return True.

    **Validates: Requirements 11.5, 12.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        output_filename = f"{pdf_name}.extracted.json"
        out_file = output_dir / output_filename
        out_file.write_text(json.dumps(valid_data), encoding="utf-8")

        entry = {"status": "complete", "output_path": output_filename}

        result = _is_output_valid(entry, output_dir=output_dir)

        # Valid file → accepted
        assert result is True


@given(pdf_name=_pdf_name_st)
@settings(max_examples=100)
def test_property_16_is_output_valid_rejects_missing_files(pdf_name):
    """For any manifest entry whose output file does not exist,
    _is_output_valid SHALL return False.

    **Validates: Requirements 11.5, 12.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        output_filename = f"{pdf_name}.extracted.json"
        # Do NOT create the file

        entry = {"status": "complete", "output_path": output_filename}

        result = _is_output_valid(entry, output_dir=output_dir)

        # Missing file → not valid
        assert result is False


@given(
    pdf_name=_pdf_name_st,
    corrupt_content=_corrupt_content_st,
)
@settings(max_examples=100)
def test_property_16_identity_check_resets_corrupt_entries(pdf_name, corrupt_content):
    """For any manifest entry marked "complete" with identity fields matching
    the current run, if the output file is corrupt (fails JSON parsing), the
    entry SHALL be reset to "pending" by load_manifest_with_identity_check —
    triggering re-processing.

    **Validates: Requirements 11.5, 12.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir)
        output_filename = f"{pdf_name}.extracted.json"
        out_file = output_dir / output_filename
        out_file.write_text(corrupt_content, encoding="utf-8")

        # Create a manifest file with a "complete" entry
        identity = ManifestIdentity(
            pdf_content_hash="abc123",
            config_hash="def456",
            extraction_map_hash="ghi789",
            model_id="gpt-4",
            schema_version=MANIFEST_SCHEMA_VERSION,
            output_path=output_filename,
        )

        manifest_data = {
            pdf_name: {
                "status": "complete",
                "pdf_content_hash": "abc123",
                "config_hash": "def456",
                "extraction_map_hash": "ghi789",
                "model_id": "gpt-4",
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "output_path": output_filename,
            }
        }

        manifest_file = Path(tmp_dir) / "manifest.json"
        manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

        current_identities = {pdf_name: identity}

        with patch.object(_MANIFEST_MODULE, "MANIFEST_FILE", manifest_file):
            result = load_manifest_with_identity_check(
                current_identities, output_dir=output_dir
            )

        # Corrupt output → entry reset to pending for re-processing
        assert result[pdf_name]["status"] == "pending"
