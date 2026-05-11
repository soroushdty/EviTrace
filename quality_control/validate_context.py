"""
quality_control.validate_context
---------------------------------
Entry-point guard for the QC-to-LLM handoff.

This module exposes :func:`validate_qc_context_input`, which verifies that a
:class:`~quality_control.models.QCBundle` is fully reconciled and structurally
sound before field extraction begins.

The function was migrated from ``pipeline/validator.py`` so that all
``quality_control``-related validation lives inside the ``quality_control``
package, respecting the dependency-direction rule:
``quality_control`` → (no ``agents``, no ``pipeline``, no ``pdf_extractor``).

Classes
-------
ValidationError
    Raised when ``validate_qc_context_input`` detects a structural or
    semantic problem with the supplied ``QCBundle``.  Defined here (not
    imported from ``pipeline``) to keep the dependency graph clean.

Functions
---------
validate_qc_context_input(ctx)
    Perform five pre-flight checks on *ctx* and delegate the structural
    schema check to the module-level ``_structure_validator`` singleton.
"""

from __future__ import annotations

from typing import Any

from quality_control.models import QCBundle
from quality_control.structure_validator import StructureSchemaValidator


# ---------------------------------------------------------------------------
# ValidationError
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """Raised when a QCBundle fails pre-flight validation.

    Attributes
    ----------
    errors:
        Non-empty list of human-readable strings, one per failed check.
    """

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = errors if errors is not None else [message]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_structure_validator = StructureSchemaValidator()


# ---------------------------------------------------------------------------
# Serializer helper
# ---------------------------------------------------------------------------

def _qc_bundle_serializer(ctx: Any) -> dict:
    """Convert a ``QCBundle`` to a JSON-serialisable dict for schema validation.

    Only the fields required by the ``QCBundle`` JSON schema are included:
    ``branches`` (list of candidate dicts) and ``unified`` (the
    ``UnifiedRecord`` dict).  Optional fields are included when present.

    Parameters
    ----------
    ctx:
        The object to serialise.  Expected to be a ``QCBundle`` instance;
        the serialiser does not re-check the type — that guard runs before
        this function is called.

    Returns
    -------
    dict
        A JSON-serialisable representation of *ctx*.
    """
    # Serialise branches
    branches_out: list[dict] = []
    for candidate in ctx.branches:
        branches_out.append(
            {
                "source": candidate.source,
                "index": candidate.index,
                "payload": candidate.payload if isinstance(candidate.payload, (str, int, float, bool, list, dict, type(None))) else str(candidate.payload),
                "status": candidate.status,
            }
        )

    # Serialise unified record
    unified = ctx.unified
    unified_out: dict = {
        "document_id": unified.document_id,
        "content": unified.content,
    }

    result: dict = {
        "branches": branches_out,
        "unified": unified_out,
    }

    # Include optional top-level fields so the schema's additionalProperties
    # pass-through does not strip them.
    if ctx.reports is not None:
        result["reports"] = []  # list of QualityMetrics — not schema-validated here
    if ctx.iaa_metrics is not None:
        result["iaa_metrics"] = {}
    if ctx.decision is not None:
        result["decision"] = {}
    if ctx.metrics_hierarchy:
        result["metrics_hierarchy"] = ctx.metrics_hierarchy

    return result


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def validate_qc_context_input(ctx: object) -> None:
    """Guard: verify *ctx* is a fully-reconciled ``QCBundle`` before field extraction.

    Performs five sequential checks in order.  The first failing check raises
    :class:`ValidationError` immediately; subsequent checks are not evaluated.
    After all five checks pass, the structural schema is verified via the
    module-level :data:`_structure_validator` singleton.

    Checks
    ------
    1. *ctx* is an instance of :class:`~quality_control.models.QCBundle`.
    2. ``ctx.unified`` is not ``None``.
    3. ``ctx.unified.document_id`` is a non-empty ``str``.
    4. ``ctx.unified.content`` is a ``dict``.
    5. ``ctx.unified.content['exact_text']`` is a non-empty ``str``.
    6. Structural schema check via ``_structure_validator.validate_qc_bundle``.

    Parameters
    ----------
    ctx:
        The object to validate.

    Raises
    ------
    ValidationError
        With a non-empty ``errors`` list if any check fails.
    """
    # Check 1 — type guard
    if not isinstance(ctx, QCBundle):
        msg = f"pdf_processor input must be a QCBundle, got {type(ctx).__name__!r}"
        raise ValidationError(msg, errors=[msg])

    # Check 2 — unified must be set
    if ctx.unified is None:
        msg = "QCBundle.unified is None — reconciler has not run yet"
        raise ValidationError(msg, errors=[msg])

    # Check 3 — document_id must be a non-empty str
    if not isinstance(ctx.unified.document_id, str) or not ctx.unified.document_id:
        msg = (
            f"QCBundle.unified.document_id must be a non-empty str, "
            f"got {ctx.unified.document_id!r}"
        )
        raise ValidationError(msg, errors=[msg])

    # Check 4 — content must be a dict
    if not isinstance(ctx.unified.content, dict):
        msg = (
            f"QCBundle.unified.content must be a dict, "
            f"got {type(ctx.unified.content).__name__!r}"
        )
        raise ValidationError(msg, errors=[msg])

    # Check 5 — exact_text must be a non-empty str
    exact_text = ctx.unified.content.get("exact_text")
    if not isinstance(exact_text, str) or not exact_text.strip():
        msg = "QCBundle.unified.content['exact_text'] must be a non-empty string"
        raise ValidationError(msg, errors=[msg])

    # Check 6 — structural schema validation
    result = _structure_validator.validate_qc_bundle(ctx, _qc_bundle_serializer)
    if not result.is_valid:
        raise ValidationError(
            "QCBundle structural validation failed:\n" + "\n".join(result.errors),
            errors=result.errors,
        )
