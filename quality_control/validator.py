"""
quality_control.validator
-------------------------
Generic, injectable validation engine.

Classes
-------
ValidationResult
    Frozen dataclass returned by every ``Validator.validate()`` call.
    Carries ``is_valid`` (bool), ``errors`` (list of strings), and
    ``validated_object`` (the serialized dict produced by the injected
    serializer — never the original object).

Validator
    Generic base class that accepts a serializer callable and a JSON-Schema
    dict at construction time.  Knows nothing about PDFs, candidates, agents,
    or any domain-specific construct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import jsonschema


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    """Immutable result of a single ``Validator.validate()`` call.

    Attributes
    ----------
    is_valid:
        ``True`` iff the serialized dict satisfies all schema constraints.
    errors:
        Empty list when ``is_valid`` is ``True``; one non-empty string per
        violated constraint otherwise.  Each string identifies the violated
        constraint by field name and rule.
    validated_object:
        The dict produced by the injected serializer.  Always the serialized
        form — never the original object passed to ``validate()``.
    """

    is_valid: bool
    errors: list[str]
    validated_object: dict


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class Validator:
    """Generic, injectable schema validator.

    Parameters
    ----------
    serializer:
        A callable that accepts any object and returns a ``dict``.  Applied
        to every object passed to ``validate()``.  If the serializer raises,
        the exception propagates unchanged and no ``ValidationResult`` is
        returned.
    schema:
        A JSON-Schema dict (Draft 7).  Must be a ``dict`` and must not be
        ``None``; a ``TypeError`` is raised immediately at construction time
        otherwise.

    Raises
    ------
    TypeError
        If ``schema`` is not a ``dict`` or is ``None``.
    """

    def __init__(
        self,
        serializer: Callable[[Any], dict],
        schema: dict,
    ) -> None:
        if not isinstance(schema, dict):
            raise TypeError(
                f"schema must be a dict, got {type(schema).__name__!r}"
            )
        self._serializer = serializer
        self._schema = schema

    def validate(self, obj: Any) -> ValidationResult:
        """Validate *obj* against the schema.

        Steps
        -----
        1. Call ``self._serializer(obj)`` to produce a dict *d*.
           If the serializer raises, the exception propagates unchanged.
        2. Validate *d* against ``self._schema`` using
           ``jsonschema.Draft7Validator``.
        3. Return a ``ValidationResult``.

        Parameters
        ----------
        obj:
            The object to validate.  May be of any type; the serializer is
            responsible for converting it to a dict.

        Returns
        -------
        ValidationResult
            ``is_valid=True, errors=[], validated_object=d`` when all
            constraints are satisfied.
            ``is_valid=False, errors=[...], validated_object=d`` when one or
            more constraints are violated.

        Raises
        ------
        Any exception raised by the serializer or by the schema-validation
        engine propagates unchanged.  No ``ValidationResult`` is returned in
        that case.
        """
        # Step 1 — serialise; propagate exceptions unchanged
        d: dict = self._serializer(obj)

        # Step 2 — collect all validation errors via Draft7Validator so that
        # every violated constraint is reported (not just the first one).
        validator = jsonschema.Draft7Validator(self._schema)
        raw_errors = sorted(validator.iter_errors(d), key=lambda e: list(e.path))

        if not raw_errors:
            return ValidationResult(
                is_valid=True,
                errors=[],
                validated_object=d,
            )

        # Build one human-readable string per violation.
        error_messages: list[str] = []
        for err in raw_errors:
            # Build a field path string, e.g. "field \"status\""
            if err.path:
                field = " -> ".join(str(p) for p in err.path)
                msg = f'field "{field}": {err.message}'
            else:
                msg = err.message
            error_messages.append(msg)

        return ValidationResult(
            is_valid=False,
            errors=error_messages,
            validated_object=d,
        )
