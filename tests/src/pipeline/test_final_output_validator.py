"""Unit tests for FinalOutputValidator and ValidationResult."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_VALIDATOR_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "validator.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_validator_direct", _VALIDATOR_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

FinalOutputValidator = _MODULE.FinalOutputValidator
ValidationResult = _MODULE.ValidationResult
reconstruct_fields = _MODULE.reconstruct_fields

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def validator() -> "FinalOutputValidator":
    return FinalOutputValidator()


def _make_valid_field(
    field_index: int = 1,
    domain_group: int = 1,
    field_name: str = "Author",
    extracted_value: str = "Smith et al.",
    evidence: str = "Found in header",
    location: list | None = None,
    location_metadata: list | None = None,
    confidence: str = "h",
) -> dict:
    """Helper to build a valid field record with overridable defaults."""
    return {
        "field_index": field_index,
        "domain_group": domain_group,
        "field_name": field_name,
        "extracted_value": extracted_value,
        "evidence": evidence,
        "location": location if location is not None else ["ev1"],
        "location_metadata": location_metadata
        if location_metadata is not None
        else [
            {
                "id": "ev1",
                "type": "sentence",
                "section_path": None,
                "page": 1,
                "coords": None,
                "xpath": None,
                "source_pdf": None,
            }
        ],
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Valid data tests
# ---------------------------------------------------------------------------


class TestValidData:
    def test_single_valid_field(self, validator):
        result = validator.validate([_make_valid_field()])
        assert result.is_valid
        assert result.errors == []

    def test_multiple_valid_fields(self, validator):
        fields = [
            _make_valid_field(field_index=1, field_name="Author"),
            _make_valid_field(field_index=2, field_name="Year", extracted_value="2020"),
            _make_valid_field(field_index=3, field_name="Title", confidence="m"),
        ]
        result = validator.validate(fields)
        assert result.is_valid

    def test_empty_array_is_valid(self, validator):
        result = validator.validate([])
        assert result.is_valid

    def test_all_confidence_values_accepted(self, validator):
        for conf in ("h", "m", "l", "nr"):
            result = validator.validate([_make_valid_field(confidence=conf)])
            assert result.is_valid, f"confidence={conf!r} should be valid"

    def test_empty_location_and_metadata(self, validator):
        field = _make_valid_field(location=[], location_metadata=[])
        result = validator.validate([field])
        assert result.is_valid

    def test_empty_extracted_value(self, validator):
        """extracted_value can be empty string (no minLength constraint)."""
        field = _make_valid_field(extracted_value="")
        result = validator.validate([field])
        assert result.is_valid


# ---------------------------------------------------------------------------
# Invalid data tests
# ---------------------------------------------------------------------------


class TestInvalidData:
    def test_missing_required_key(self, validator):
        field = _make_valid_field()
        del field["field_name"]
        result = validator.validate([field])
        assert not result.is_valid
        assert any("field_index=1" in e for e in result.errors)
        assert any("'field_name' is a required property" in e for e in result.errors)

    def test_invalid_confidence_value(self, validator):
        field = _make_valid_field(field_index=2, field_name="Year", confidence="x")
        result = validator.validate([field])
        assert not result.is_valid
        assert any("field_index=2" in e for e in result.errors)
        assert any("field_name='Year'" in e for e in result.errors)

    def test_additional_properties_rejected(self, validator):
        field = _make_valid_field(field_index=3, field_name="Title")
        field["extra_key"] = "unexpected"
        result = validator.validate([field])
        assert not result.is_valid
        assert any("field_index=3" in e for e in result.errors)

    def test_field_index_below_minimum(self, validator):
        field = _make_valid_field(field_index=0)
        result = validator.validate([field])
        assert not result.is_valid

    def test_domain_group_below_minimum(self, validator):
        field = _make_valid_field(domain_group=0)
        result = validator.validate([field])
        assert not result.is_valid

    def test_field_name_empty_string(self, validator):
        """field_name has minLength=1, so empty string is invalid."""
        field = _make_valid_field(field_name="")
        result = validator.validate([field])
        assert not result.is_valid

    def test_location_not_array(self, validator):
        field = _make_valid_field()
        field["location"] = "not_an_array"
        result = validator.validate([field])
        assert not result.is_valid

    def test_location_metadata_missing_id(self, validator):
        field = _make_valid_field(
            field_index=5,
            field_name="Outcome",
            location_metadata=[{"type": "sentence"}],
        )
        result = validator.validate([field])
        assert not result.is_valid
        assert any("field_index=5" in e for e in result.errors)
        assert any("path=" in e for e in result.errors)

    def test_not_an_array_at_top_level(self, validator):
        """Top-level must be an array."""
        result = validator.validate({"field_index": 1})  # type: ignore[arg-type]
        assert not result.is_valid


# ---------------------------------------------------------------------------
# format_error tests
# ---------------------------------------------------------------------------


class TestFormatError:
    def test_error_includes_json_path(self, validator):
        field = _make_valid_field(field_index=7, field_name="Intervention", confidence="bad")
        result = validator.validate([field])
        assert not result.is_valid
        # Path should reference the confidence property
        assert any("$[0].confidence" in e for e in result.errors)

    def test_error_includes_field_index_and_field_name(self, validator):
        field = _make_valid_field(field_index=10, field_name="Comparator", confidence="z")
        result = validator.validate([field])
        assert not result.is_valid
        assert any("field_index=10" in e for e in result.errors)
        assert any("field_name='Comparator'" in e for e in result.errors)

    def test_error_without_field_name_still_shows_field_index(self, validator):
        """When field_name is missing from the record, field_index is still shown."""
        field = _make_valid_field(field_index=4)
        del field["field_name"]
        result = validator.validate([field])
        assert not result.is_valid
        assert any("field_index=4" in e for e in result.errors)

    def test_nested_location_metadata_error_path(self, validator):
        field = _make_valid_field(
            field_index=6,
            field_name="Setting",
            location_metadata=[{"id": "ev1"}, {"type": "table"}],
        )
        result = validator.validate([field])
        assert not result.is_valid
        # Should reference the second metadata item
        assert any("location_metadata[1]" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Schema loading tests
# ---------------------------------------------------------------------------


class TestSchemaLoading:
    def test_loads_from_default_path(self):
        """Default schema_path resolves relative to project root."""
        v = FinalOutputValidator()
        assert v._schema is not None
        assert v._schema["type"] == "array"

    def test_loads_from_absolute_path(self, tmp_path):
        """Can load schema from an absolute path."""
        import json

        schema = {"type": "array", "items": {"type": "object"}}
        schema_file = tmp_path / "test_schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        v = FinalOutputValidator(schema_path=str(schema_file))
        result = v.validate([{}])
        assert result.is_valid

    def test_missing_schema_raises(self):
        """Non-existent schema path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            FinalOutputValidator(schema_path="/nonexistent/schema.json")


# ---------------------------------------------------------------------------
# Schema file existence and structure tests (Requirement 1.5)
# ---------------------------------------------------------------------------


class TestSchemaFileIntegrity:
    def test_schema_file_exists_at_expected_path(self):
        """configs/final_output_schema.json exists at the project root."""
        schema_path = _PROJECT_ROOT / "configs" / "final_output_schema.json"
        assert schema_path.exists(), f"Schema file not found at {schema_path}"

    def test_schema_file_is_valid_json(self):
        """configs/final_output_schema.json is parseable JSON."""
        schema_path = _PROJECT_ROOT / "configs" / "final_output_schema.json"
        content = schema_path.read_text(encoding="utf-8")
        schema = json.loads(content)
        assert isinstance(schema, dict)

    def test_schema_declares_draft7(self):
        """Schema declares JSON Schema Draft 7."""
        schema_path = _PROJECT_ROOT / "configs" / "final_output_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema.get("$schema") == "http://json-schema.org/draft-07/schema#"

    def test_schema_defines_array_of_objects_with_required_keys(self):
        """Schema top-level is array; items require the 8 canonical keys."""
        schema_path = _PROJECT_ROOT / "configs" / "final_output_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["type"] == "array"
        items = schema["items"]
        assert items["type"] == "object"
        expected_required = {
            "field_index", "domain_group", "field_name", "extracted_value",
            "evidence", "location", "location_metadata", "confidence",
        }
        assert set(items["required"]) == expected_required


# ---------------------------------------------------------------------------
# Representative document validation (Requirement 1.4)
# ---------------------------------------------------------------------------


class TestRepresentativeDocumentValidation:
    """Validate representative compact chunk output and final extraction documents."""

    def test_compact_chunk_reconstructed_to_valid_final_output(self, validator):
        """A representative compact chunk output, when reconstructed via
        reconstruct_fields, produces a valid final extraction document."""
        compact_chunk = [
            {"i": 1, "v": "Smith et al.", "loc": ["S000001", "S000002"], "c": "h"},
            {"i": 2, "v": "2020", "loc": ["S000001"], "c": "h"},
            {"i": 3, "v": "Randomized controlled trial", "loc": ["S000003"], "c": "m"},
        ]
        field_lookup = {
            1: {"domain_group": 1, "field_name": "Author"},
            2: {"domain_group": 1, "field_name": "Publication year"},
            3: {"domain_group": 2, "field_name": "Study design"},
        }
        evidence_map = {
            "S000001": {
                "id": "S000001",
                "text": "Smith et al. (2020) conducted a study.",
                "type": "sentence",
                "section_path": "abstract",
                "page": 1,
                "coords": None,
                "xpath": None,
                "source_pdf": None,
            },
            "S000002": {
                "id": "S000002",
                "text": "The authors reported findings.",
                "type": "sentence",
                "section_path": "introduction",
                "page": 1,
                "coords": None,
                "xpath": None,
                "source_pdf": None,
            },
            "S000003": {
                "id": "S000003",
                "text": "This randomized controlled trial enrolled 200 patients.",
                "type": "sentence",
                "section_path": "methods",
                "page": 2,
                "coords": None,
                "xpath": None,
                "source_pdf": None,
            },
        }

        final_fields = reconstruct_fields(compact_chunk, field_lookup, evidence_map)
        result = validator.validate(final_fields)
        assert result.is_valid, f"Reconstructed fields failed validation: {result.errors}"

    def test_multi_domain_final_extraction_document(self, validator):
        """A representative multi-domain final extraction document validates."""
        fields = [
            _make_valid_field(field_index=1, domain_group=1, field_name="Author"),
            _make_valid_field(field_index=2, domain_group=1, field_name="Publication year",
                             extracted_value="2019"),
            _make_valid_field(field_index=5, domain_group=2, field_name="Study design",
                             extracted_value="RCT", confidence="h"),
            _make_valid_field(field_index=10, domain_group=3, field_name="Sample size",
                             extracted_value="150", confidence="m"),
            _make_valid_field(field_index=20, domain_group=5, field_name="Primary outcome",
                             extracted_value="Mortality", confidence="h",
                             location=["ev1", "ev2"],
                             location_metadata=[
                                 {"id": "ev1", "type": "sentence", "section_path": "results",
                                  "page": 5, "coords": None, "xpath": None, "source_pdf": None},
                                 {"id": "ev2", "type": "table", "section_path": "results",
                                  "page": 6, "coords": None, "xpath": None, "source_pdf": None},
                             ]),
            _make_valid_field(field_index=30, domain_group=7, field_name="Intervention",
                             extracted_value="nr", confidence="nr",
                             location=[], location_metadata=[]),
        ]
        result = validator.validate(fields)
        assert result.is_valid, f"Multi-domain document failed validation: {result.errors}"

    def test_compact_chunk_with_empty_loc_reconstructs_validly(self, validator):
        """Compact chunk with empty loc (field not reported) reconstructs to valid output."""
        compact_chunk = [
            {"i": 5, "v": "nr", "loc": [], "c": "nr"},
        ]
        field_lookup = {
            5: {"domain_group": 2, "field_name": "Study design"},
        }

        final_fields = reconstruct_fields(compact_chunk, field_lookup, evidence_map={})
        result = validator.validate(final_fields)
        assert result.is_valid, f"Empty-loc reconstruction failed: {result.errors}"
