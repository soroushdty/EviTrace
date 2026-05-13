"""
tests/quality_control/test_validator_base.py
============================================
Unit tests for the ``ValidationResult`` frozen dataclass and the ``Validator``
base class defined in ``quality_control/validator.py``.

Covers (Requirements 2.2, 2.4, 2.7, 2.9):
  - ``ValidationResult`` is a frozen dataclass with exactly three fields
  - ``Validator`` raises ``TypeError`` for non-dict schemas (None, int, str, list)
  - ``Validator.validate()`` returns a ``ValidationResult`` for a trivially valid schema
  - Serializer exceptions propagate unchanged through ``Validator.validate()``
  - ``Validator.validate()`` returns ``is_valid=False`` for a schema violation
"""

from __future__ import annotations

import dataclasses

import pytest

from quality_control.validator import ValidationResult, Validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A trivially permissive schema — any dict passes.
_EMPTY_SCHEMA: dict = {"type": "object"}

# A schema that requires a field named "name" of type string.
_STRICT_SCHEMA: dict = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
    },
    "additionalProperties": False,
}

# Identity serializer — returns the object unchanged (assumes it is already a dict).
_identity = lambda obj: obj  # noqa: E731


# ---------------------------------------------------------------------------
# ValidationResult — frozen dataclass with exactly three fields
# ---------------------------------------------------------------------------

class TestValidationResult:
    """Tests for the ValidationResult frozen dataclass."""

    def test_is_dataclass(self):
        """ValidationResult must be a dataclass."""
        assert dataclasses.is_dataclass(ValidationResult)

    def test_is_frozen(self):
        """ValidationResult must be frozen (immutable after construction)."""
        result = ValidationResult(is_valid=True, errors=[], validated_object={})
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.is_valid = False  # type: ignore[misc]

    def test_has_exactly_three_fields(self):
        """ValidationResult must have exactly three fields: is_valid, errors, validated_object."""
        fields = {f.name for f in dataclasses.fields(ValidationResult)}
        assert fields == {"is_valid", "errors", "validated_object"}

    def test_field_names_correct(self):
        """Field names must be is_valid, errors, and validated_object."""
        field_names = [f.name for f in dataclasses.fields(ValidationResult)]
        assert "is_valid" in field_names
        assert "errors" in field_names
        assert "validated_object" in field_names

    def test_construction_valid(self):
        """ValidationResult constructs correctly with valid arguments."""
        obj = {"key": "value"}
        result = ValidationResult(is_valid=True, errors=[], validated_object=obj)
        assert result.is_valid is True
        assert result.errors == []
        assert result.validated_object is obj

    def test_construction_invalid(self):
        """ValidationResult constructs correctly for a failed validation."""
        obj = {"bad": 123}
        errors = ['field "name": required property is missing']
        result = ValidationResult(is_valid=False, errors=errors, validated_object=obj)
        assert result.is_valid is False
        assert result.errors == errors
        assert result.validated_object is obj


# ---------------------------------------------------------------------------
# Validator — TypeError for non-dict schemas
# ---------------------------------------------------------------------------

class TestValidatorTypeErrorOnNonDictSchema:
    """Requirement 2.2: Validator raises TypeError immediately for non-dict schemas."""

    def test_none_schema_raises_type_error(self):
        """None schema must raise TypeError at construction time."""
        with pytest.raises(TypeError):
            Validator(_identity, None)  # type: ignore[arg-type]

    def test_int_schema_raises_type_error(self):
        """Integer schema must raise TypeError at construction time."""
        with pytest.raises(TypeError):
            Validator(_identity, 42)  # type: ignore[arg-type]

    def test_str_schema_raises_type_error(self):
        """String schema must raise TypeError at construction time."""
        with pytest.raises(TypeError):
            Validator(_identity, '{"type": "object"}')  # type: ignore[arg-type]

    def test_list_schema_raises_type_error(self):
        """List schema must raise TypeError at construction time."""
        with pytest.raises(TypeError):
            Validator(_identity, [{"type": "object"}])  # type: ignore[arg-type]

    def test_type_error_raised_before_validate_called(self):
        """TypeError must be raised at construction, not deferred to validate()."""
        # If construction raises, we never reach validate().
        with pytest.raises(TypeError):
            v = Validator(_identity, None)  # type: ignore[arg-type]
            v.validate({})  # should never be reached

    def test_dict_schema_does_not_raise(self):
        """A dict schema must NOT raise TypeError — construction succeeds."""
        v = Validator(_identity, _EMPTY_SCHEMA)
        assert v is not None


# ---------------------------------------------------------------------------
# Validator.validate() — returns ValidationResult for a trivially valid schema
# ---------------------------------------------------------------------------

class TestValidatorValidateReturnsValidationResult:
    """Requirement 2.4: validate() returns a ValidationResult on every non-raising call."""

    def test_returns_validation_result_instance(self):
        """validate() must return a ValidationResult instance."""
        v = Validator(_identity, _EMPTY_SCHEMA)
        result = v.validate({})
        assert isinstance(result, ValidationResult)

    def test_valid_object_is_valid_true(self):
        """A dict satisfying the schema must produce is_valid=True."""
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate({"name": "Alice"})
        assert result.is_valid is True

    def test_valid_object_errors_empty(self):
        """A dict satisfying the schema must produce an empty errors list."""
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate({"name": "Alice"})
        assert result.errors == []

    def test_valid_object_validated_object_equals_serializer_output(self):
        """validated_object must equal the dict returned by the serializer."""
        obj = {"name": "Bob"}
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate(obj)
        assert result.validated_object == obj

    def test_empty_schema_any_dict_passes(self):
        """An empty permissive schema accepts any dict."""
        v = Validator(_identity, _EMPTY_SCHEMA)
        result = v.validate({"anything": 123, "goes": True})
        assert isinstance(result, ValidationResult)
        assert result.is_valid is True

    def test_serializer_transforms_object(self):
        """validate() applies the serializer before validating."""
        # Serializer converts an object to a dict with a "name" field.
        class _Obj:
            name = "Charlie"

        serializer = lambda o: {"name": o.name}  # noqa: E731
        v = Validator(serializer, _STRICT_SCHEMA)
        result = v.validate(_Obj())
        assert result.is_valid is True
        assert result.validated_object == {"name": "Charlie"}


# ---------------------------------------------------------------------------
# Validator.validate() — serializer exceptions propagate unchanged
# ---------------------------------------------------------------------------

class TestValidatorSerializerExceptionPropagation:
    """Requirement 2.9: serializer exceptions propagate unchanged; no ValidationResult returned."""

    def test_value_error_propagates(self):
        """ValueError raised by serializer must propagate unchanged."""
        def bad_serializer(obj):
            raise ValueError("serializer failed")

        v = Validator(bad_serializer, _EMPTY_SCHEMA)
        with pytest.raises(ValueError, match="serializer failed"):
            v.validate("anything")

    def test_type_error_propagates(self):
        """TypeError raised by serializer must propagate unchanged."""
        def bad_serializer(obj):
            raise TypeError("wrong type")

        v = Validator(bad_serializer, _EMPTY_SCHEMA)
        with pytest.raises(TypeError, match="wrong type"):
            v.validate(42)

    def test_runtime_error_propagates(self):
        """RuntimeError raised by serializer must propagate unchanged."""
        def bad_serializer(obj):
            raise RuntimeError("unexpected failure")

        v = Validator(bad_serializer, _EMPTY_SCHEMA)
        with pytest.raises(RuntimeError, match="unexpected failure"):
            v.validate(None)

    def test_attribute_error_propagates(self):
        """AttributeError raised by serializer must propagate unchanged."""
        def bad_serializer(obj):
            raise AttributeError("missing attribute")

        v = Validator(bad_serializer, _EMPTY_SCHEMA)
        with pytest.raises(AttributeError, match="missing attribute"):
            v.validate(object())

    def test_no_validation_result_returned_on_serializer_exception(self):
        """When the serializer raises, validate() must NOT return a ValidationResult."""
        def bad_serializer(obj):
            raise ValueError("boom")

        v = Validator(bad_serializer, _EMPTY_SCHEMA)
        try:
            result = v.validate("x")
        except ValueError:
            pass  # expected — exception propagated correctly
        else:
            pytest.fail(
                f"Expected ValueError to propagate, but validate() returned {result!r}"
            )


# ---------------------------------------------------------------------------
# Validator.validate() — is_valid=False for a schema violation
# ---------------------------------------------------------------------------

class TestValidatorSchemaViolation:
    """Requirement 2.7: schema violations produce is_valid=False with non-empty errors."""

    def test_missing_required_field_is_valid_false(self):
        """A dict missing a required field must produce is_valid=False."""
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate({})  # "name" is required but absent
        assert result.is_valid is False

    def test_missing_required_field_errors_non_empty(self):
        """A dict missing a required field must produce a non-empty errors list."""
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate({})
        assert len(result.errors) >= 1

    def test_errors_are_non_empty_strings(self):
        """Each error string must be non-empty."""
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate({})
        for error in result.errors:
            assert isinstance(error, str)
            assert len(error) > 0

    def test_wrong_type_is_valid_false(self):
        """A dict with a field of the wrong type must produce is_valid=False."""
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate({"name": 123})  # name must be a string
        assert result.is_valid is False

    def test_wrong_type_errors_non_empty(self):
        """A dict with a field of the wrong type must produce a non-empty errors list."""
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate({"name": 123})
        assert len(result.errors) >= 1

    def test_additional_property_violation(self):
        """A dict with an extra field (additionalProperties=False) must produce is_valid=False."""
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate({"name": "Alice", "extra": "not allowed"})
        assert result.is_valid is False

    def test_validated_object_present_even_on_failure(self):
        """validated_object must be populated even when is_valid=False."""
        obj = {"wrong_key": "value"}
        v = Validator(_identity, _STRICT_SCHEMA)
        result = v.validate(obj)
        assert result.validated_object == obj

    def test_multiple_violations_multiple_errors(self):
        """Multiple schema violations should produce multiple error strings."""
        schema = {
            "type": "object",
            "required": ["a", "b"],
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "integer"},
            },
        }
        v = Validator(_identity, schema)
        # Both required fields are missing
        result = v.validate({})
        assert result.is_valid is False
        assert len(result.errors) >= 2
