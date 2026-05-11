"""
tests/quality_control/test_structure_validator.py
==================================================
Unit tests for ``StructureSchemaValidator`` and ``StructureSchemaLoadError``
defined in ``quality_control/structure_validator.py``.

Covers (Requirements 3.9, 3.10, 3.11):
  - ``StructureSchemaLoadError`` raised when the schema file is missing
  - ``StructureSchemaLoadError`` raised when the schema file contains invalid JSON
  - Each of the five methods returns ``is_valid=True`` for a minimal valid object
  - Each of the five methods returns ``is_valid=False`` for an object missing a
    required field

The ``StructureSchemaValidator`` constructor accepts an optional ``schema_path``
parameter, which is used here to point at a temporary schema file written to
``tmp_path`` so tests are hermetic and do not depend on the real
``configs/structure_schema.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quality_control.structure_validator import (
    StructureSchemaLoadError,
    StructureSchemaValidator,
)
from quality_control.validator import ValidationResult


# ---------------------------------------------------------------------------
# Helpers — minimal schema and minimal valid objects
# ---------------------------------------------------------------------------

def _write_schema(tmp_path: Path, schema: dict) -> Path:
    """Write *schema* as JSON to a temp file and return its path."""
    p = tmp_path / "structure_schema.json"
    p.write_text(json.dumps(schema), encoding="utf-8")
    return p


# A minimal but complete structure_schema.json that mirrors the real one.
# Only the $defs and validator_targets sections are needed for these tests.
_MINIMAL_SCHEMA: dict = {
    "version": "1.0.0",
    "type": "structure",
    "$defs": {
        "Candidate": {
            "type": "object",
            "required": ["source", "index", "payload"],
            "additionalProperties": True,
            "properties": {
                "source": {"type": "string", "minLength": 1},
                "index": {"type": "integer"},
                "payload": {},
            },
        },
        "UnifiedRecord": {
            "type": "object",
            "required": ["document_id", "content"],
            "additionalProperties": True,
            "properties": {
                "document_id": {"type": "string", "minLength": 1},
                "content": {
                    "type": "object",
                    "required": ["exact_text"],
                    "additionalProperties": True,
                    "properties": {
                        "exact_text": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "QCBundle": {
            "type": "object",
            "required": ["branches", "unified"],
            "additionalProperties": True,
            "properties": {
                "branches": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Candidate"},
                },
                "unified": {"$ref": "#/$defs/UnifiedRecord"},
            },
        },
        "ExtractionFieldDict": {
            "type": "object",
            "required": ["id", "v", "e", "s", "c", "ra", "note"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "v": {},
                "e": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "s": {
                    "type": "string",
                    "enum": [
                        "reported_explicitly",
                        "inferred_from_text",
                        "not_reported",
                        "not_applicable",
                        "unclear",
                    ],
                },
                "c": {"type": "integer", "minimum": 1, "maximum": 5},
                "ra": {"type": "string"},
                "note": {"type": ["string", "null"]},
            },
        },
        "PdfProcessorOutput": {
            "type": "array",
            "items": {"$ref": "#/$defs/ExtractionFieldDict"},
        },
        "ExtractionMapEntry": {
            "type": "object",
            "required": [
                "field_index",
                "domain_group",
                "field_name",
                "definition",
                "reviewer_question",
                "format",
            ],
            "additionalProperties": True,
            "properties": {
                "field_index": {"type": "integer", "minimum": 1},
                "domain_group": {"type": "string", "minLength": 1},
                "field_name": {"type": "string", "minLength": 1},
                "definition": {"type": "string", "minLength": 1},
                "reviewer_question": {"type": "string", "minLength": 1},
                "format": {"type": "string", "minLength": 1},
            },
        },
        "ExtractionMap": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/ExtractionMapEntry"},
        },
        "ChunkOutput": {
            "type": "object",
            "required": ["extractions"],
            "additionalProperties": False,
            "properties": {
                "extractions": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/ExtractionFieldDict"},
                },
            },
        },
    },
    "validator_targets": {
        "validate_candidate": "#/$defs/Candidate",
        "validate_qc_bundle": "#/$defs/QCBundle",
        "validate_pdf_processor_output": "#/$defs/PdfProcessorOutput",
        "validate_extraction_map": "#/$defs/ExtractionMap",
        "validate_chunk_output": "#/$defs/ChunkOutput",
    },
}

# Identity serializer — the objects passed in tests are already plain dicts.
_identity = lambda obj: obj  # noqa: E731

# ---------------------------------------------------------------------------
# Minimal valid objects for each of the five methods
# ---------------------------------------------------------------------------

_VALID_CANDIDATE: dict = {
    "source": "grobid",
    "index": 0,
    "payload": "<TEI/>",
}

_VALID_UNIFIED_RECORD: dict = {
    "document_id": "doc-001",
    "content": {"exact_text": "Some extracted text."},
}

_VALID_QC_BUNDLE: dict = {
    "branches": [_VALID_CANDIDATE],
    "unified": _VALID_UNIFIED_RECORD,
}

_VALID_EXTRACTION_FIELD_DICT: dict = {
    "id": "field-1",
    "v": "some value",
    "e": ["evidence sentence"],
    "s": "reported_explicitly",
    "c": 3,
    "ra": "rationale text",
    "note": None,
}

_VALID_PDF_PROCESSOR_OUTPUT: list = [_VALID_EXTRACTION_FIELD_DICT]

_VALID_EXTRACTION_MAP_ENTRY: dict = {
    "field_index": 1,
    "domain_group": "1. Study identification",
    "field_name": "First author and year",
    "definition": "The first author's surname and publication year.",
    "reviewer_question": "Who is the first author?",
    "format": "string",
}

_VALID_EXTRACTION_MAP: list = [_VALID_EXTRACTION_MAP_ENTRY]

_VALID_CHUNK_OUTPUT: dict = {
    "extractions": [_VALID_EXTRACTION_FIELD_DICT],
}


# ---------------------------------------------------------------------------
# StructureSchemaLoadError — missing file
# ---------------------------------------------------------------------------

class TestStructureSchemaLoadErrorMissingFile:
    """Requirement 3.11: StructureSchemaLoadError raised when schema file is missing."""

    def test_missing_file_raises_load_error(self, tmp_path: Path):
        """Pointing at a non-existent path must raise StructureSchemaLoadError."""
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(StructureSchemaLoadError):
            StructureSchemaValidator(schema_path=missing)

    def test_missing_file_error_message_contains_path(self, tmp_path: Path):
        """The error message should reference the missing path."""
        missing = tmp_path / "no_such_file.json"
        with pytest.raises(StructureSchemaLoadError, match="no_such_file"):
            StructureSchemaValidator(schema_path=missing)

    def test_missing_file_is_not_file_not_found_error(self, tmp_path: Path):
        """The raised exception must be StructureSchemaLoadError, not FileNotFoundError."""
        missing = tmp_path / "absent.json"
        exc = None
        try:
            StructureSchemaValidator(schema_path=missing)
        except StructureSchemaLoadError as e:
            exc = e
        assert exc is not None, "Expected StructureSchemaLoadError to be raised"
        assert not isinstance(exc, FileNotFoundError)


# ---------------------------------------------------------------------------
# StructureSchemaLoadError — invalid JSON
# ---------------------------------------------------------------------------

class TestStructureSchemaLoadErrorInvalidJson:
    """Requirement 3.11: StructureSchemaLoadError raised when schema file contains invalid JSON."""

    def test_invalid_json_raises_load_error(self, tmp_path: Path):
        """A file with invalid JSON must raise StructureSchemaLoadError."""
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(StructureSchemaLoadError):
            StructureSchemaValidator(schema_path=bad_json)

    def test_empty_file_raises_load_error(self, tmp_path: Path):
        """An empty file (no JSON at all) must raise StructureSchemaLoadError."""
        empty = tmp_path / "empty.json"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(StructureSchemaLoadError):
            StructureSchemaValidator(schema_path=empty)

    def test_truncated_json_raises_load_error(self, tmp_path: Path):
        """A truncated JSON object must raise StructureSchemaLoadError."""
        truncated = tmp_path / "truncated.json"
        truncated.write_text('{"key": "val', encoding="utf-8")
        with pytest.raises(StructureSchemaLoadError):
            StructureSchemaValidator(schema_path=truncated)

    def test_invalid_json_is_not_json_decode_error(self, tmp_path: Path):
        """The raised exception must be StructureSchemaLoadError, not json.JSONDecodeError."""
        import json as _json

        bad = tmp_path / "bad2.json"
        bad.write_text("not json at all", encoding="utf-8")
        exc = None
        try:
            StructureSchemaValidator(schema_path=bad)
        except StructureSchemaLoadError as e:
            exc = e
        assert exc is not None, "Expected StructureSchemaLoadError to be raised"
        assert not isinstance(exc, _json.JSONDecodeError)


# ---------------------------------------------------------------------------
# Fixture: a validator backed by the minimal in-memory schema
# ---------------------------------------------------------------------------

@pytest.fixture()
def validator(tmp_path: Path) -> StructureSchemaValidator:
    """Return a StructureSchemaValidator loaded from the minimal test schema."""
    schema_path = _write_schema(tmp_path, _MINIMAL_SCHEMA)
    return StructureSchemaValidator(schema_path=schema_path)


# ---------------------------------------------------------------------------
# validate_candidate — is_valid=True for minimal valid object
# ---------------------------------------------------------------------------

class TestValidateCandidateValid:
    """Requirement 3.9: validate_candidate returns is_valid=True for a valid Candidate."""

    def test_returns_validation_result(self, validator: StructureSchemaValidator):
        """validate_candidate must return a ValidationResult instance."""
        result = validator.validate_candidate(_VALID_CANDIDATE, _identity)
        assert isinstance(result, ValidationResult)

    def test_is_valid_true(self, validator: StructureSchemaValidator):
        """A minimal valid Candidate dict must produce is_valid=True."""
        result = validator.validate_candidate(_VALID_CANDIDATE, _identity)
        assert result.is_valid is True

    def test_errors_empty(self, validator: StructureSchemaValidator):
        """A minimal valid Candidate dict must produce an empty errors list."""
        result = validator.validate_candidate(_VALID_CANDIDATE, _identity)
        assert result.errors == []

    def test_validated_object_equals_input(self, validator: StructureSchemaValidator):
        """validated_object must equal the serialized dict."""
        result = validator.validate_candidate(_VALID_CANDIDATE, _identity)
        assert result.validated_object == _VALID_CANDIDATE


# ---------------------------------------------------------------------------
# validate_candidate — is_valid=False for object missing a required field
# ---------------------------------------------------------------------------

class TestValidateCandidateInvalid:
    """Requirement 3.10: validate_candidate returns is_valid=False for an invalid Candidate."""

    def test_missing_source_is_valid_false(self, validator: StructureSchemaValidator):
        """A Candidate missing 'source' must produce is_valid=False."""
        obj = {"index": 0, "payload": "<TEI/>"}  # 'source' missing
        result = validator.validate_candidate(obj, _identity)
        assert result.is_valid is False

    def test_missing_source_errors_non_empty(self, validator: StructureSchemaValidator):
        """A Candidate missing 'source' must produce a non-empty errors list."""
        obj = {"index": 0, "payload": "<TEI/>"}
        result = validator.validate_candidate(obj, _identity)
        assert len(result.errors) >= 1

    def test_missing_index_is_valid_false(self, validator: StructureSchemaValidator):
        """A Candidate missing 'index' must produce is_valid=False."""
        obj = {"source": "grobid", "payload": "<TEI/>"}  # 'index' missing
        result = validator.validate_candidate(obj, _identity)
        assert result.is_valid is False

    def test_missing_payload_is_valid_false(self, validator: StructureSchemaValidator):
        """A Candidate missing 'payload' must produce is_valid=False."""
        obj = {"source": "grobid", "index": 0}  # 'payload' missing
        result = validator.validate_candidate(obj, _identity)
        assert result.is_valid is False

    def test_empty_dict_is_valid_false(self, validator: StructureSchemaValidator):
        """An empty dict must produce is_valid=False (all required fields missing)."""
        result = validator.validate_candidate({}, _identity)
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# validate_qc_bundle — is_valid=True for minimal valid object
# ---------------------------------------------------------------------------

class TestValidateQcBundleValid:
    """Requirement 3.9: validate_qc_bundle returns is_valid=True for a valid QCBundle."""

    def test_returns_validation_result(self, validator: StructureSchemaValidator):
        result = validator.validate_qc_bundle(_VALID_QC_BUNDLE, _identity)
        assert isinstance(result, ValidationResult)

    def test_is_valid_true(self, validator: StructureSchemaValidator):
        result = validator.validate_qc_bundle(_VALID_QC_BUNDLE, _identity)
        assert result.is_valid is True

    def test_errors_empty(self, validator: StructureSchemaValidator):
        result = validator.validate_qc_bundle(_VALID_QC_BUNDLE, _identity)
        assert result.errors == []

    def test_validated_object_equals_input(self, validator: StructureSchemaValidator):
        result = validator.validate_qc_bundle(_VALID_QC_BUNDLE, _identity)
        assert result.validated_object == _VALID_QC_BUNDLE


# ---------------------------------------------------------------------------
# validate_qc_bundle — is_valid=False for object missing a required field
# ---------------------------------------------------------------------------

class TestValidateQcBundleInvalid:
    """Requirement 3.10: validate_qc_bundle returns is_valid=False for an invalid QCBundle."""

    def test_missing_branches_is_valid_false(self, validator: StructureSchemaValidator):
        """A QCBundle missing 'branches' must produce is_valid=False."""
        obj = {"unified": _VALID_UNIFIED_RECORD}  # 'branches' missing
        result = validator.validate_qc_bundle(obj, _identity)
        assert result.is_valid is False

    def test_missing_unified_is_valid_false(self, validator: StructureSchemaValidator):
        """A QCBundle missing 'unified' must produce is_valid=False."""
        obj = {"branches": [_VALID_CANDIDATE]}  # 'unified' missing
        result = validator.validate_qc_bundle(obj, _identity)
        assert result.is_valid is False

    def test_missing_branches_errors_non_empty(self, validator: StructureSchemaValidator):
        obj = {"unified": _VALID_UNIFIED_RECORD}
        result = validator.validate_qc_bundle(obj, _identity)
        assert len(result.errors) >= 1

    def test_empty_dict_is_valid_false(self, validator: StructureSchemaValidator):
        result = validator.validate_qc_bundle({}, _identity)
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# validate_pdf_processor_output — is_valid=True for minimal valid object
# ---------------------------------------------------------------------------

class TestValidatePdfProcessorOutputValid:
    """Requirement 3.9: validate_pdf_processor_output returns is_valid=True for valid output."""

    def test_returns_validation_result(self, validator: StructureSchemaValidator):
        result = validator.validate_pdf_processor_output(
            _VALID_PDF_PROCESSOR_OUTPUT, _identity
        )
        assert isinstance(result, ValidationResult)

    def test_is_valid_true(self, validator: StructureSchemaValidator):
        result = validator.validate_pdf_processor_output(
            _VALID_PDF_PROCESSOR_OUTPUT, _identity
        )
        assert result.is_valid is True

    def test_errors_empty(self, validator: StructureSchemaValidator):
        result = validator.validate_pdf_processor_output(
            _VALID_PDF_PROCESSOR_OUTPUT, _identity
        )
        assert result.errors == []

    def test_empty_list_is_valid_true(self, validator: StructureSchemaValidator):
        """An empty list is a valid PdfProcessorOutput (array with no minItems)."""
        result = validator.validate_pdf_processor_output([], _identity)
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# validate_pdf_processor_output — is_valid=False for object missing a required field
# ---------------------------------------------------------------------------

class TestValidatePdfProcessorOutputInvalid:
    """Requirement 3.10: validate_pdf_processor_output returns is_valid=False for invalid output."""

    def test_item_missing_id_is_valid_false(self, validator: StructureSchemaValidator):
        """An ExtractionFieldDict missing 'id' must produce is_valid=False."""
        bad_item = {k: v for k, v in _VALID_EXTRACTION_FIELD_DICT.items() if k != "id"}
        result = validator.validate_pdf_processor_output([bad_item], _identity)
        assert result.is_valid is False

    def test_item_missing_id_errors_non_empty(self, validator: StructureSchemaValidator):
        bad_item = {k: v for k, v in _VALID_EXTRACTION_FIELD_DICT.items() if k != "id"}
        result = validator.validate_pdf_processor_output([bad_item], _identity)
        assert len(result.errors) >= 1

    def test_item_missing_s_is_valid_false(self, validator: StructureSchemaValidator):
        """An ExtractionFieldDict missing 's' must produce is_valid=False."""
        bad_item = {k: v for k, v in _VALID_EXTRACTION_FIELD_DICT.items() if k != "s"}
        result = validator.validate_pdf_processor_output([bad_item], _identity)
        assert result.is_valid is False

    def test_item_missing_c_is_valid_false(self, validator: StructureSchemaValidator):
        """An ExtractionFieldDict missing 'c' must produce is_valid=False."""
        bad_item = {k: v for k, v in _VALID_EXTRACTION_FIELD_DICT.items() if k != "c"}
        result = validator.validate_pdf_processor_output([bad_item], _identity)
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# validate_extraction_map — is_valid=True for minimal valid object
# ---------------------------------------------------------------------------

class TestValidateExtractionMapValid:
    """Requirement 3.9: validate_extraction_map returns is_valid=True for a valid map."""

    def test_returns_validation_result(self, validator: StructureSchemaValidator):
        result = validator.validate_extraction_map(_VALID_EXTRACTION_MAP, _identity)
        assert isinstance(result, ValidationResult)

    def test_is_valid_true(self, validator: StructureSchemaValidator):
        result = validator.validate_extraction_map(_VALID_EXTRACTION_MAP, _identity)
        assert result.is_valid is True

    def test_errors_empty(self, validator: StructureSchemaValidator):
        result = validator.validate_extraction_map(_VALID_EXTRACTION_MAP, _identity)
        assert result.errors == []

    def test_validated_object_equals_input(self, validator: StructureSchemaValidator):
        result = validator.validate_extraction_map(_VALID_EXTRACTION_MAP, _identity)
        assert result.validated_object == _VALID_EXTRACTION_MAP


# ---------------------------------------------------------------------------
# validate_extraction_map — is_valid=False for object missing a required field
# ---------------------------------------------------------------------------

class TestValidateExtractionMapInvalid:
    """Requirement 3.10: validate_extraction_map returns is_valid=False for an invalid map."""

    def test_empty_list_is_valid_false(self, validator: StructureSchemaValidator):
        """An empty list violates minItems=1 on ExtractionMap."""
        result = validator.validate_extraction_map([], _identity)
        assert result.is_valid is False

    def test_empty_list_errors_non_empty(self, validator: StructureSchemaValidator):
        result = validator.validate_extraction_map([], _identity)
        assert len(result.errors) >= 1

    def test_entry_missing_field_index_is_valid_false(
        self, validator: StructureSchemaValidator
    ):
        """An entry missing 'field_index' must produce is_valid=False."""
        bad_entry = {
            k: v
            for k, v in _VALID_EXTRACTION_MAP_ENTRY.items()
            if k != "field_index"
        }
        result = validator.validate_extraction_map([bad_entry], _identity)
        assert result.is_valid is False

    def test_entry_missing_field_name_is_valid_false(
        self, validator: StructureSchemaValidator
    ):
        """An entry missing 'field_name' must produce is_valid=False."""
        bad_entry = {
            k: v
            for k, v in _VALID_EXTRACTION_MAP_ENTRY.items()
            if k != "field_name"
        }
        result = validator.validate_extraction_map([bad_entry], _identity)
        assert result.is_valid is False

    def test_entry_missing_definition_is_valid_false(
        self, validator: StructureSchemaValidator
    ):
        """An entry missing 'definition' must produce is_valid=False."""
        bad_entry = {
            k: v
            for k, v in _VALID_EXTRACTION_MAP_ENTRY.items()
            if k != "definition"
        }
        result = validator.validate_extraction_map([bad_entry], _identity)
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# validate_chunk_output — is_valid=True for minimal valid object
# ---------------------------------------------------------------------------

class TestValidateChunkOutputValid:
    """Requirement 3.9: validate_chunk_output returns is_valid=True for a valid ChunkOutput."""

    def test_returns_validation_result(self, validator: StructureSchemaValidator):
        result = validator.validate_chunk_output(_VALID_CHUNK_OUTPUT, _identity)
        assert isinstance(result, ValidationResult)

    def test_is_valid_true(self, validator: StructureSchemaValidator):
        result = validator.validate_chunk_output(_VALID_CHUNK_OUTPUT, _identity)
        assert result.is_valid is True

    def test_errors_empty(self, validator: StructureSchemaValidator):
        result = validator.validate_chunk_output(_VALID_CHUNK_OUTPUT, _identity)
        assert result.errors == []

    def test_empty_extractions_list_is_valid_true(
        self, validator: StructureSchemaValidator
    ):
        """A ChunkOutput with an empty extractions list is valid (no minItems on array)."""
        result = validator.validate_chunk_output({"extractions": []}, _identity)
        assert result.is_valid is True

    def test_validated_object_equals_input(self, validator: StructureSchemaValidator):
        result = validator.validate_chunk_output(_VALID_CHUNK_OUTPUT, _identity)
        assert result.validated_object == _VALID_CHUNK_OUTPUT


# ---------------------------------------------------------------------------
# validate_chunk_output — is_valid=False for object missing a required field
# ---------------------------------------------------------------------------

class TestValidateChunkOutputInvalid:
    """Requirement 3.10: validate_chunk_output returns is_valid=False for an invalid ChunkOutput."""

    def test_missing_extractions_is_valid_false(
        self, validator: StructureSchemaValidator
    ):
        """A ChunkOutput missing 'extractions' must produce is_valid=False."""
        result = validator.validate_chunk_output({}, _identity)
        assert result.is_valid is False

    def test_missing_extractions_errors_non_empty(
        self, validator: StructureSchemaValidator
    ):
        result = validator.validate_chunk_output({}, _identity)
        assert len(result.errors) >= 1

    def test_extraction_item_missing_id_is_valid_false(
        self, validator: StructureSchemaValidator
    ):
        """A ChunkOutput whose extraction item is missing 'id' must produce is_valid=False."""
        bad_item = {k: v for k, v in _VALID_EXTRACTION_FIELD_DICT.items() if k != "id"}
        result = validator.validate_chunk_output({"extractions": [bad_item]}, _identity)
        assert result.is_valid is False

    def test_extraction_item_missing_ra_is_valid_false(
        self, validator: StructureSchemaValidator
    ):
        """A ChunkOutput whose extraction item is missing 'ra' must produce is_valid=False."""
        bad_item = {k: v for k, v in _VALID_EXTRACTION_FIELD_DICT.items() if k != "ra"}
        result = validator.validate_chunk_output({"extractions": [bad_item]}, _identity)
        assert result.is_valid is False

    def test_additional_property_on_chunk_output_is_valid_false(
        self, validator: StructureSchemaValidator
    ):
        """ChunkOutput has additionalProperties=False; extra keys must produce is_valid=False."""
        obj = {"extractions": [], "unexpected_key": "not allowed"}
        result = validator.validate_chunk_output(obj, _identity)
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# Serializer integration — validate() applies the serializer before validating
# ---------------------------------------------------------------------------

class TestSerializerIntegration:
    """Verify that the serializer is applied before schema validation."""

    def test_serializer_converts_object_to_dict(
        self, validator: StructureSchemaValidator
    ):
        """validate_candidate applies the serializer to a non-dict object."""

        class _FakeCandidate:
            source = "pymupdf"
            index = 1
            payload = []

        serializer = lambda o: {  # noqa: E731
            "source": o.source,
            "index": o.index,
            "payload": o.payload,
        }
        result = validator.validate_candidate(_FakeCandidate(), serializer)
        assert result.is_valid is True
        assert result.validated_object == {
            "source": "pymupdf",
            "index": 1,
            "payload": [],
        }

    def test_serializer_exception_propagates(
        self, validator: StructureSchemaValidator
    ):
        """If the serializer raises, the exception propagates unchanged."""

        def bad_serializer(obj):
            raise RuntimeError("serializer exploded")

        with pytest.raises(RuntimeError, match="serializer exploded"):
            validator.validate_candidate("anything", bad_serializer)
