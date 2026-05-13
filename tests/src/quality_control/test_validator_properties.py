"""
Property-based tests for quality_control.validator.Validator (Properties 1–6).

Feature: schema-validator-split
Validates: Requirements 2.2, 2.3, 2.4, 2.6, 2.7, 2.9, 2.10
"""

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from quality_control.validator import Validator, ValidationResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# A minimal JSON-Schema that requires exactly one key "name" of type string.
_SIMPLE_SCHEMA = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
    },
    "additionalProperties": False,
}

# Identity serializer — returns the dict unchanged.
_identity = lambda obj: obj  # noqa: E731


# ---------------------------------------------------------------------------
# Property 1: Validator rejects non-dict schemas
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 1: Validator rejects non-dict schemas
@given(st.one_of(st.none(), st.integers(), st.text(), st.lists(st.integers())))
@settings(max_examples=100)
def test_validator_rejects_non_dict_schema(bad_schema):
    """For any non-dict value (including None), constructing Validator with
    that value as schema must raise TypeError immediately.

    Validates: Requirements 2.2
    """
    with pytest.raises(TypeError):
        Validator(_identity, bad_schema)


# ---------------------------------------------------------------------------
# Property 2: Validator round-trip — validated_object equals serializer output
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 2: Validator round-trip validated_object equals serializer output
@given(st.dictionaries(st.text(), st.integers()))
@settings(max_examples=100)
def test_validator_round_trip_validated_object(obj):
    """For any dict obj and a serializer that does not raise, the
    validated_object in the result must equal serializer(obj).

    Validates: Requirements 2.3, 2.10
    """
    # Use a permissive schema so validation always succeeds (no constraints).
    permissive_schema = {"type": "object"}
    serializer = lambda x: dict(x)  # noqa: E731  — returns a copy

    validator = Validator(serializer, permissive_schema)
    result = validator.validate(obj)

    assert result.validated_object == serializer(obj)


# ---------------------------------------------------------------------------
# Property 3: Validator always returns ValidationResult on every non-raising call
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 3: Validator always returns ValidationResult on every non-raising call
@given(st.dictionaries(st.text(), st.integers()))
@settings(max_examples=100)
def test_validator_always_returns_validation_result(obj):
    """For any dict obj and a serializer that does not raise, validate() must
    return an instance of ValidationResult — never None, a raw dict, or an
    exception.

    Validates: Requirements 2.4
    """
    permissive_schema = {"type": "object"}
    validator = Validator(_identity, permissive_schema)
    result = validator.validate(obj)

    assert isinstance(result, ValidationResult)


# ---------------------------------------------------------------------------
# Property 4: Valid objects produce is_valid=True with empty errors
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 4: Valid objects produce is_valid=True with empty errors
@given(st.text(min_size=1))
@settings(max_examples=100)
def test_validator_valid_input_is_valid_true(name_value):
    """For any object whose serialized form satisfies all schema constraints,
    validate() must return is_valid=True and errors=[].

    Uses _SIMPLE_SCHEMA which requires {"name": <string>}.

    Validates: Requirements 2.6
    """
    obj = {"name": name_value}
    validator = Validator(_identity, _SIMPLE_SCHEMA)
    result = validator.validate(obj)

    assert result.is_valid is True
    assert result.errors == []


# ---------------------------------------------------------------------------
# Property 5: Invalid objects produce is_valid=False with non-empty errors
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 5: Invalid objects produce is_valid=False with non-empty errors
@given(
    st.dictionaries(
        # Keys that are NOT "name", so the required "name" key is always absent.
        st.text().filter(lambda k: k != "name"),
        st.integers(),
    )
)
@settings(max_examples=100)
def test_validator_invalid_input_is_valid_false(obj):
    """For any dict that is missing the required "name" key, validate() must
    return is_valid=False and len(errors) >= 1, with each error string
    non-empty.

    Validates: Requirements 2.7
    """
    # Guarantee the required key is absent (the filter above ensures this,
    # but assume() makes the contract explicit and handles edge cases).
    assume("name" not in obj)

    validator = Validator(_identity, _SIMPLE_SCHEMA)
    result = validator.validate(obj)

    assert result.is_valid is False
    assert len(result.errors) >= 1
    for error_msg in result.errors:
        assert isinstance(error_msg, str)
        assert len(error_msg) > 0


# ---------------------------------------------------------------------------
# Property 6: Serializer exceptions propagate unchanged
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 6: Serializer exceptions propagate unchanged
@given(st.sampled_from([ValueError, TypeError, RuntimeError, KeyError, AttributeError]))
@settings(max_examples=100)
def test_validator_propagates_serializer_exception(exc_class):
    """For any exception type E raised by the serializer callable, validate()
    must propagate an exception of exactly type E unchanged and must NOT return
    a ValidationResult.

    Validates: Requirements 2.9
    """
    sentinel = exc_class("sentinel error from serializer")

    def raising_serializer(obj):
        raise sentinel

    validator = Validator(raising_serializer, _SIMPLE_SCHEMA)

    with pytest.raises(exc_class) as exc_info:
        validator.validate({"anything": 1})

    # The exact exception instance must propagate — not a wrapped copy.
    assert exc_info.value is sentinel


# ---------------------------------------------------------------------------
# Shared fixture for StructureSchemaValidator tests (Properties 7–9)
# ---------------------------------------------------------------------------

from quality_control.structure_validator import StructureSchemaValidator

# Module-level singleton — loads structure_schema.json once for all three
# properties so the file is not re-read on every example.
_structure_validator = StructureSchemaValidator()

# Identity serializer for dicts — the Candidate schema uses plain dicts in
# these tests, so no conversion is needed.
_dict_identity = lambda x: x  # noqa: E731


def _valid_candidate(**overrides) -> dict:
    """Return a minimal valid Candidate-shaped dict.

    The Candidate $defs requires: source (str, minLength 1),
    index (integer), payload (any).  additionalProperties is true.
    """
    base = {
        "source": "test_source",
        "index": 0,
        "payload": None,
    }
    base.update(overrides)
    return base


# Strategy that generates valid Candidate-shaped dicts.
_valid_candidate_strategy = st.fixed_dictionaries(
    {
        "source": st.text(min_size=1),
        "index": st.integers(),
        "payload": st.one_of(st.none(), st.integers(), st.text(), st.booleans()),
    }
)

# Strategy that generates dicts missing at least one required Candidate field.
# The Candidate schema requires: source, index, payload.
# We generate dicts that omit one or more of these keys.
_required_candidate_keys = ["source", "index", "payload"]

_invalid_candidate_strategy = st.one_of(
    # Missing "source"
    st.fixed_dictionaries(
        {"index": st.integers(), "payload": st.one_of(st.none(), st.integers())}
    ),
    # Missing "index"
    st.fixed_dictionaries(
        {"source": st.text(min_size=1), "payload": st.one_of(st.none(), st.integers())}
    ),
    # Missing "payload"
    st.fixed_dictionaries(
        {"source": st.text(min_size=1), "index": st.integers()}
    ),
    # Missing all required fields
    st.fixed_dictionaries({}),
)


# ---------------------------------------------------------------------------
# Property 7: StructureSchemaValidator idempotence
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 7: StructureSchemaValidator idempotence
@given(_valid_candidate_strategy)
@settings(max_examples=100)
def test_structure_validator_idempotence(candidate):
    """Calling validate_candidate twice on the same valid object must return
    ValidationResult instances that are equal in all three fields (is_valid,
    errors, validated_object), regardless of call order.

    Validates: Requirements 3.12
    """
    result_1 = _structure_validator.validate_candidate(candidate, _dict_identity)
    result_2 = _structure_validator.validate_candidate(candidate, _dict_identity)

    assert result_1.is_valid == result_2.is_valid
    assert result_1.errors == result_2.errors
    assert result_1.validated_object == result_2.validated_object


# ---------------------------------------------------------------------------
# Property 8: StructureSchemaValidator valid objects produce is_valid=True
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 8: StructureSchemaValidator valid objects produce is_valid=True
@given(_valid_candidate_strategy)
@settings(max_examples=100)
def test_structure_validator_valid_objects(candidate):
    """For any valid Candidate-shaped dict, validate_candidate must return a
    ValidationResult with is_valid=True and errors=[].

    Validates: Requirements 3.9
    """
    result = _structure_validator.validate_candidate(candidate, _dict_identity)

    assert result.is_valid is True
    assert result.errors == []


# ---------------------------------------------------------------------------
# Property 9: StructureSchemaValidator invalid objects produce is_valid=False
# ---------------------------------------------------------------------------

# Feature: schema-validator-split, Property 9: StructureSchemaValidator invalid objects produce is_valid=False
@given(_invalid_candidate_strategy)
@settings(max_examples=100)
def test_structure_validator_invalid_objects(candidate):
    """For any dict missing one or more required Candidate fields, validate_candidate
    must return a ValidationResult with is_valid=False and len(errors) >= 1.

    Validates: Requirements 3.10
    """
    # Confirm the generated dict is actually missing at least one required key.
    assume(not all(k in candidate for k in _required_candidate_keys))

    result = _structure_validator.validate_candidate(candidate, _dict_identity)

    assert result.is_valid is False
    assert len(result.errors) >= 1
