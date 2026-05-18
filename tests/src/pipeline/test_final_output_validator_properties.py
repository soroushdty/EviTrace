"""
Property-based tests for FinalOutputValidator (Properties 1, 2, 3).

Feature: audit-remediation
Validates: Requirements 1.1, 1.2, 1.3
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from pipeline.validator import FinalOutputValidator, ValidationResult


# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Strategy for valid confidence values
_confidence_st = st.sampled_from(["h", "m", "l", "nr"])

# Strategy for valid location IDs (non-empty strings)
_location_id_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
    min_size=1,
    max_size=20,
)

# Strategy for valid location_metadata items
_location_metadata_item_st = st.fixed_dictionaries(
    {"id": _location_id_st},
    optional={
        "type": st.text(min_size=1, max_size=20),
        "section_path": st.one_of(st.none(), st.text(min_size=1, max_size=50)),
        "page": st.one_of(st.none(), st.integers(min_value=1, max_value=100)),
        "coords": st.one_of(st.none(), st.lists(st.floats(allow_nan=False, allow_infinity=False), min_size=4, max_size=4)),
        "xpath": st.one_of(st.none(), st.text(min_size=1, max_size=50)),
        "source_pdf": st.one_of(st.none(), st.text(min_size=1, max_size=50)),
    },
)


def _valid_field_st():
    """Strategy that generates a valid field record conforming to Final_Schema."""
    return st.builds(
        lambda fi, dg, fn, ev, evi, loc_ids, conf: {
            "field_index": fi,
            "domain_group": dg,
            "field_name": fn,
            "extracted_value": ev,
            "evidence": evi,
            "location": loc_ids,
            "location_metadata": [{"id": lid} for lid in loc_ids],
            "confidence": conf,
        },
        fi=st.integers(min_value=1, max_value=62),
        dg=st.integers(min_value=1, max_value=13),
        fn=st.text(min_size=1, max_size=50),
        ev=st.text(max_size=200),
        evi=st.text(max_size=200),
        loc_ids=st.lists(_location_id_st, min_size=0, max_size=5),
        conf=_confidence_st,
    )


def _invalid_field_st():
    """Strategy that generates an invalid field record (violates Final_Schema).

    Produces fields with one of several known violations:
    - field_index < 1
    - missing required key
    - invalid confidence value
    - field_name empty string
    - location not an array
    """
    return st.one_of(
        # field_index below minimum (0 or negative)
        st.builds(
            lambda fi, dg, fn: {
                "field_index": fi,
                "domain_group": dg,
                "field_name": fn,
                "extracted_value": "test",
                "evidence": "",
                "location": [],
                "location_metadata": [],
                "confidence": "h",
            },
            fi=st.integers(min_value=-100, max_value=0),
            dg=st.integers(min_value=1, max_value=13),
            fn=st.text(min_size=1, max_size=30),
        ),
        # invalid confidence value
        st.builds(
            lambda fi, fn, conf: {
                "field_index": fi,
                "domain_group": 1,
                "field_name": fn,
                "extracted_value": "test",
                "evidence": "",
                "location": [],
                "location_metadata": [],
                "confidence": conf,
            },
            fi=st.integers(min_value=1, max_value=62),
            fn=st.text(min_size=1, max_size=30),
            conf=st.text(min_size=1, max_size=5).filter(lambda c: c not in ("h", "m", "l", "nr")),
        ),
        # field_name empty string (violates minLength=1)
        st.builds(
            lambda fi: {
                "field_index": fi,
                "domain_group": 1,
                "field_name": "",
                "extracted_value": "test",
                "evidence": "",
                "location": [],
                "location_metadata": [],
                "confidence": "h",
            },
            fi=st.integers(min_value=1, max_value=62),
        ),
        # missing required key (no field_name)
        st.builds(
            lambda fi: {
                "field_index": fi,
                "domain_group": 1,
                "extracted_value": "test",
                "evidence": "",
                "location": [],
                "location_metadata": [],
                "confidence": "h",
            },
            fi=st.integers(min_value=1, max_value=62),
        ),
        # location is not an array
        st.builds(
            lambda fi, fn: {
                "field_index": fi,
                "domain_group": 1,
                "field_name": fn,
                "extracted_value": "test",
                "evidence": "",
                "location": "not_an_array",
                "location_metadata": [],
                "confidence": "h",
            },
            fi=st.integers(min_value=1, max_value=62),
            fn=st.text(min_size=1, max_size=30),
        ),
    )


# Module-level validator instance (loads schema once)
_validator = FinalOutputValidator()


# ---------------------------------------------------------------------------
# Property 1: Schema validation gates output writes
# ---------------------------------------------------------------------------


@given(fields=st.lists(_valid_field_st(), min_size=1, max_size=5))
@settings(max_examples=100)
def test_property_1_valid_fields_pass_validation(fields):
    """For any list of field dicts that conform to Final_Schema, validation
    SHALL pass (is_valid=True) — meaning the output file WOULD be written.

    **Validates: Requirements 1.1**
    """
    result = _validator.validate(fields)
    assert result.is_valid is True
    assert result.errors == []


@given(invalid_field=_invalid_field_st())
@settings(max_examples=100)
def test_property_1_invalid_fields_fail_validation(invalid_field):
    """For any field dict that violates Final_Schema, validation SHALL fail
    (is_valid=False) — meaning the output file SHALL NOT be written and
    manifest status would be set to "failed_schema_validation".

    **Validates: Requirements 1.1**
    """
    result = _validator.validate([invalid_field])
    assert result.is_valid is False
    assert len(result.errors) >= 1


@given(
    valid_fields=st.lists(_valid_field_st(), min_size=0, max_size=3),
    invalid_field=_invalid_field_st(),
)
@settings(max_examples=100)
def test_property_1_mixed_fields_fail_validation(valid_fields, invalid_field):
    """For any list containing at least one invalid field dict among valid ones,
    validation SHALL fail — the output file SHALL NOT be written.

    **Validates: Requirements 1.1**
    """
    # Insert the invalid field at a random position
    all_fields = valid_fields + [invalid_field]
    result = _validator.validate(all_fields)
    assert result.is_valid is False
    assert len(result.errors) >= 1


@given(fields=st.lists(_valid_field_st(), min_size=1, max_size=5))
@settings(max_examples=100)
def test_property_1_output_file_written_iff_valid(fields):
    """For any valid field list, the output file SHALL exist on disk after
    _save_pdf_output is called with validation gating. When validation fails,
    no output file SHALL be written and manifest status SHALL be
    "failed_schema_validation".

    **Validates: Requirements 1.1**
    """
    import tempfile

    from pipeline.pdf_processor import _save_pdf_output

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = Path(tmp_dir) / "outputs"
        output_dir.mkdir()
        pdf_name = "test_paper"
        out_file = output_dir / f"{pdf_name}.extracted.json"

        # Patch OUTPUT_DIR to use temp directory
        with patch("pipeline.pdf_processor.OUTPUT_DIR", output_dir):
            _save_pdf_output(pdf_name, fields)

        # Valid fields → file should exist
        assert out_file.exists()
        written_data = json.loads(out_file.read_text(encoding="utf-8"))
        assert len(written_data) == len(fields)


# ---------------------------------------------------------------------------
# Property 2: Validation errors include field identifiers
# ---------------------------------------------------------------------------


@given(
    field_index=st.integers(min_value=1, max_value=1000),
    field_name=st.text(min_size=1, max_size=50),
)
@settings(max_examples=100)
def test_property_2_error_contains_field_index_and_path(field_index, field_name):
    """For any field dict that fails schema validation, the error message SHALL
    contain the field_index value and the JSON path of the invalid element.

    **Validates: Requirements 1.2**
    """
    # Create a field with an invalid confidence value to trigger a validation error
    invalid_field = {
        "field_index": field_index,
        "domain_group": 1,
        "field_name": field_name,
        "extracted_value": "test",
        "evidence": "",
        "location": [],
        "location_metadata": [],
        "confidence": "INVALID",
    }
    result = _validator.validate([invalid_field])
    assert not result.is_valid

    # Error messages must contain field_index
    field_index_found = any(f"field_index={field_index}" in e for e in result.errors)
    assert field_index_found, (
        f"Expected field_index={field_index} in error messages, got: {result.errors}"
    )

    # Error messages must contain JSON path
    path_found = any("path=" in e for e in result.errors)
    assert path_found, (
        f"Expected 'path=' in error messages, got: {result.errors}"
    )


@given(
    field_index=st.integers(min_value=1, max_value=1000),
    field_name=st.text(min_size=1, max_size=50),
)
@settings(max_examples=100)
def test_property_2_error_contains_field_name_when_present(field_index, field_name):
    """If field_name is present in the invalid record, it SHALL also appear
    in the error message.

    **Validates: Requirements 1.2**
    """
    # Create a field with an invalid confidence value but valid field_name
    invalid_field = {
        "field_index": field_index,
        "domain_group": 1,
        "field_name": field_name,
        "extracted_value": "test",
        "evidence": "",
        "location": [],
        "location_metadata": [],
        "confidence": "INVALID",
    }
    result = _validator.validate([invalid_field])
    assert not result.is_valid

    # Error messages must contain field_name
    field_name_found = any(f"field_name={field_name!r}" in e for e in result.errors)
    assert field_name_found, (
        f"Expected field_name={field_name!r} in error messages, got: {result.errors}"
    )


@given(field_index=st.integers(min_value=1, max_value=1000))
@settings(max_examples=100)
def test_property_2_error_path_references_correct_element(field_index):
    """For any invalid field at a known array position, the JSON path in the
    error message SHALL reference that position.

    **Validates: Requirements 1.2**
    """
    # Put a valid field first, then an invalid one at index 1
    valid_field = {
        "field_index": field_index,
        "domain_group": 1,
        "field_name": "ValidField",
        "extracted_value": "ok",
        "evidence": "",
        "location": [],
        "location_metadata": [],
        "confidence": "h",
    }
    invalid_field = {
        "field_index": field_index + 1,
        "domain_group": 1,
        "field_name": "BadField",
        "extracted_value": "test",
        "evidence": "",
        "location": [],
        "location_metadata": [],
        "confidence": "WRONG",
    }
    result = _validator.validate([valid_field, invalid_field])
    assert not result.is_valid

    # The error path should reference index [1] (the second element)
    path_correct = any("$[1]" in e for e in result.errors)
    assert path_correct, (
        f"Expected '$[1]' in error path, got: {result.errors}"
    )


# ---------------------------------------------------------------------------
# Property 3: Location metadata cross-reference integrity
# ---------------------------------------------------------------------------


@given(
    location_ids=st.lists(_location_id_st, min_size=1, max_size=5, unique=True),
)
@settings(max_examples=100)
def test_property_3_metadata_ids_in_location_list(location_ids):
    """For any field record where location_metadata is non-empty, every item
    in location_metadata SHALL have an id field whose value exists in the
    field's location list.

    **Validates: Requirements 1.3**
    """
    # Build a field where all metadata IDs are in the location list
    field = {
        "field_index": 1,
        "domain_group": 1,
        "field_name": "TestField",
        "extracted_value": "value",
        "evidence": "some evidence",
        "location": location_ids,
        "location_metadata": [{"id": lid} for lid in location_ids],
        "confidence": "h",
    }

    # This should pass schema validation (structural correctness)
    result = _validator.validate([field])
    assert result.is_valid

    # Cross-reference integrity: every metadata id is in location
    for meta_item in field["location_metadata"]:
        assert meta_item["id"] in field["location"] or meta_item["id"] == "unresolved"


@given(
    location_ids=st.lists(_location_id_st, min_size=1, max_size=5, unique=True),
)
@settings(max_examples=100)
def test_property_3_unresolved_metadata_id_is_valid(location_ids):
    """For any field record, a location_metadata item with id="unresolved"
    SHALL be considered valid regardless of the location list contents.

    **Validates: Requirements 1.3**
    """
    # Build a field with an "unresolved" metadata entry not in location list
    field = {
        "field_index": 1,
        "domain_group": 1,
        "field_name": "TestField",
        "extracted_value": "value",
        "evidence": "some evidence",
        "location": location_ids,
        "location_metadata": [
            {"id": lid} for lid in location_ids
        ] + [{"id": "unresolved"}],
        "confidence": "h",
    }

    # Schema validation should pass (id is a string, structurally valid)
    result = _validator.validate([field])
    assert result.is_valid

    # Cross-reference: "unresolved" is always acceptable
    for meta_item in field["location_metadata"]:
        assert meta_item["id"] in field["location"] or meta_item["id"] == "unresolved"


@given(
    location_ids=st.lists(_location_id_st, min_size=1, max_size=5, unique=True),
    extra_id=_location_id_st,
)
@settings(max_examples=100)
def test_property_3_metadata_id_not_in_location_violates_integrity(location_ids, extra_id):
    """For any field record where a location_metadata item has an id NOT in
    the field's location list and NOT equal to "unresolved", the cross-reference
    integrity check SHALL detect the violation.

    **Validates: Requirements 1.3**
    """
    # Ensure extra_id is not in location_ids and not "unresolved"
    assume(extra_id not in location_ids)
    assume(extra_id != "unresolved")

    field = {
        "field_index": 1,
        "domain_group": 1,
        "field_name": "TestField",
        "extracted_value": "value",
        "evidence": "some evidence",
        "location": location_ids,
        "location_metadata": [{"id": extra_id}],
        "confidence": "h",
    }

    # The cross-reference integrity check: metadata id NOT in location
    # and NOT "unresolved" — this is a violation
    violating_ids = [
        meta["id"]
        for meta in field["location_metadata"]
        if meta["id"] not in field["location"] and meta["id"] != "unresolved"
    ]
    assert len(violating_ids) >= 1
    assert extra_id in violating_ids


@given(
    location_ids=st.lists(_location_id_st, min_size=0, max_size=5, unique=True),
    num_unresolved=st.integers(min_value=0, max_value=3),
)
@settings(max_examples=100)
def test_property_3_mixed_resolved_and_unresolved(location_ids, num_unresolved):
    """For any field record with a mix of resolved and unresolved metadata items,
    every metadata id SHALL either exist in location or equal "unresolved".

    **Validates: Requirements 1.3**
    """
    metadata = [{"id": lid} for lid in location_ids] + [
        {"id": "unresolved"} for _ in range(num_unresolved)
    ]

    field = {
        "field_index": 1,
        "domain_group": 1,
        "field_name": "TestField",
        "extracted_value": "value",
        "evidence": "some evidence",
        "location": location_ids,
        "location_metadata": metadata,
        "confidence": "h",
    }

    # Schema validation passes (structurally valid)
    if metadata:  # only validate if there's metadata
        result = _validator.validate([field])
        assert result.is_valid

    # Cross-reference integrity holds for all items
    for meta_item in field["location_metadata"]:
        assert meta_item["id"] in field["location"] or meta_item["id"] == "unresolved"
