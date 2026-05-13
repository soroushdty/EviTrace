"""
quality_control.structure_validator
------------------------------------
Sole reader of ``configs/structure_schema.json``.

Classes
-------
StructureSchemaLoadError
    Raised when ``structure_schema.json`` is missing or contains invalid JSON.

StructureSchemaValidator
    Loads the structural schema once at construction time and exposes five
    typed validation methods — one per pipeline dataclass family.  Each method
    delegates to the generic ``Validator`` engine using the sub-schema
    identified by ``validator_targets`` in the JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from quality_control.validator import ValidationResult, Validator


# ---------------------------------------------------------------------------
# StructureSchemaLoadError
# ---------------------------------------------------------------------------

class StructureSchemaLoadError(Exception):
    """Raised when structure_schema.json is missing or contains invalid JSON."""


# ---------------------------------------------------------------------------
# StructureSchemaValidator
# ---------------------------------------------------------------------------

class StructureSchemaValidator:
    """Validates pipeline objects against ``configs/structure_schema.json``.

    Parameters
    ----------
    schema_path:
        Explicit path to the schema file.  When ``None`` (the default), the
        path is resolved as
        ``Path(__file__).resolve().parent.parent / "configs" / "structure_schema.json"``
        — i.e. relative to the project root regardless of the working directory.

    Raises
    ------
    StructureSchemaLoadError
        If the resolved file does not exist or contains invalid JSON.
    """

    def __init__(self, schema_path: Path | str | None = None) -> None:
        if schema_path is None:
            resolved = (
                Path(__file__).resolve().parent.parent.parent
                / "configs"
                / "structure_schema.json"
            )
        else:
            resolved = Path(schema_path)

        try:
            text = resolved.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            raise StructureSchemaLoadError(
                f"structure_schema.json not found at {resolved!r}: {exc}"
            ) from exc

        try:
            self._schema: dict = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StructureSchemaLoadError(
                f"structure_schema.json at {resolved!r} contains invalid JSON: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _validate(
        self,
        method_name: str,
        obj: Any,
        serializer: Callable[[Any], dict],
    ) -> ValidationResult:
        """Resolve the sub-schema for *method_name* and run validation.

        Parameters
        ----------
        method_name:
            One of the five ``validator_targets`` keys.
        obj:
            The object to validate.
        serializer:
            Callable that converts *obj* to a ``dict``.  Exceptions propagate
            unchanged.

        Returns
        -------
        ValidationResult
        """
        # Resolve the $defs reference string, e.g. "#/$defs/Candidate"
        ref: str = self._schema["validator_targets"][method_name]

        # Parse the reference to extract the type name after "#/$defs/"
        # Expected format: "#/$defs/<TypeName>"
        prefix = "#/$defs/"
        if not ref.startswith(prefix):
            raise ValueError(
                f"Unexpected $defs reference format {ref!r} for {method_name!r}; "
                f"expected a string starting with {prefix!r}"
            )
        type_name = ref[len(prefix):]

        # Build a wrapper schema that:
        #   1. Uses $ref to point at the target type.
        #   2. Carries the full $defs block so that any internal $ref
        #      entries (e.g. ChunkOutput -> ExtractionFieldDict) resolve
        #      correctly under Draft7Validator.
        wrapper_schema: dict = {
            "$ref": f"#/$defs/{type_name}",
            "$defs": self._schema["$defs"],
        }

        # Delegate to the generic Validator with the wrapper schema so that
        # all $ref resolution happens within the full $defs context.
        validator = Validator(serializer, wrapper_schema)
        return validator.validate(obj)

    # ------------------------------------------------------------------
    # Public validation methods
    # ------------------------------------------------------------------

    def validate_candidate(
        self,
        candidate: Any,
        serializer: Callable[[Any], dict],
    ) -> ValidationResult:
        """Validate a ``Candidate`` object against the structural schema.

        Parameters
        ----------
        candidate:
            The ``Candidate`` instance (or any object) to validate.
        serializer:
            Callable that converts *candidate* to a ``dict``.

        Returns
        -------
        ValidationResult
        """
        return self._validate("validate_candidate", candidate, serializer)

    def validate_qc_bundle(
        self,
        bundle: Any,
        serializer: Callable[[Any], dict],
    ) -> ValidationResult:
        """Validate a ``QCBundle`` object against the structural schema.

        Parameters
        ----------
        bundle:
            The ``QCBundle`` instance (or any object) to validate.
        serializer:
            Callable that converts *bundle* to a ``dict``.

        Returns
        -------
        ValidationResult
        """
        return self._validate("validate_qc_bundle", bundle, serializer)

    def validate_pdf_processor_output(
        self,
        fields: Any,
        serializer: Callable[[Any], dict],
    ) -> ValidationResult:
        """Validate a list of extraction field dicts against the structural schema.

        Parameters
        ----------
        fields:
            The list of ``ExtractionFieldDict``-shaped dicts (or any object)
            to validate.
        serializer:
            Callable that converts *fields* to a ``dict`` (or list, as the
            schema for ``PdfProcessorOutput`` is an array type — the
            serializer must return the appropriate JSON-serialisable form).

        Returns
        -------
        ValidationResult
        """
        return self._validate("validate_pdf_processor_output", fields, serializer)

    def validate_extraction_map(
        self,
        extraction_map: Any,
        serializer: Callable[[Any], dict],
    ) -> ValidationResult:
        """Validate an extraction map against the structural schema.

        Parameters
        ----------
        extraction_map:
            The extraction map object (or any object) to validate.
        serializer:
            Callable that converts *extraction_map* to a ``dict`` (or list).

        Returns
        -------
        ValidationResult
        """
        return self._validate("validate_extraction_map", extraction_map, serializer)

    def validate_chunk_output(
        self,
        chunk_output: Any,
        serializer: Callable[[Any], dict],
    ) -> ValidationResult:
        """Validate a chunk output object against the structural schema.

        Parameters
        ----------
        chunk_output:
            The chunk output object (or any object) to validate.
        serializer:
            Callable that converts *chunk_output* to a ``dict``.

        Returns
        -------
        ValidationResult
        """
        return self._validate("validate_chunk_output", chunk_output, serializer)
