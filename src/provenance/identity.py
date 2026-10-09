"""identity — the sole derivation and parsing point for every provenance identifier.

Identifier shapes (provenance-core design, "IdentityService" and "Logical Data
Model"):

=================  =============================================
Record             Identifier
=================  =============================================
SourceNode         ``src:<document_fingerprint>``
RunIdentity        ``run:<sha256[:16]>`` over the identity fields
EvidenceNode       ``<source_id>#<local_id>``
ClaimNode          ``claim:<source_id>#f<field_index>[@r<n>]``
DerivationStep     ``drv:<sha256[:16]>`` over stage, kind, sorted
                   inputs and sorted outputs
ValidationRecord   ``val:<run_id>:<check_name>``
=================  =============================================

Evidence identifiers adopt the pipeline-assigned local identifier verbatim and
only scope it by source (4.7), so no second evidence numbering scheme exists;
:func:`format_evidence_id` is the only sanctioned constructor and
:func:`parse_evidence_id` its total inverse (4.8).

Document, configuration and extraction-map fingerprints are **consumed** from
the caller (``ManifestIdentity``) and never computed here (3.6).
``hashlib.sha256`` is used only to digest identifier material — the run
identity fields and the derivation-step content address — and this module is
the only provenance module that imports ``hashlib``.

Every function is pure: no clock, no randomness, no filesystem or network
access. Malformed input raises :class:`InvalidIdentifierError`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

__all__ = [
    "InvalidIdentifierError",
    "EVIDENCE_SCOPE_SEPARATOR",
    "SOURCE_ID_PREFIX",
    "RUN_ID_PREFIX",
    "CLAIM_ID_PREFIX",
    "DERIVATION_ID_PREFIX",
    "VALIDATION_ID_PREFIX",
    "derive_source_id",
    "derive_run_id",
    "format_evidence_id",
    "parse_evidence_id",
    "derive_claim_id",
    "derive_derivation_id",
    "derive_validation_id",
]

EVIDENCE_SCOPE_SEPARATOR = "#"
"""Separates the source scope from the pipeline-assigned local evidence ID."""

SOURCE_ID_PREFIX = "src:"
RUN_ID_PREFIX = "run:"
CLAIM_ID_PREFIX = "claim:"
DERIVATION_ID_PREFIX = "drv:"
VALIDATION_ID_PREFIX = "val:"

_DIGEST_LENGTH = 16


class InvalidIdentifierError(ValueError):
    """Raised when identifier input or an identifier string is malformed."""


# ---------------------------------------------------------------------------
# Internal checks and digest
# ---------------------------------------------------------------------------


def _require_text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidIdentifierError(f"{what} must be a non-empty string, got {value!r}")
    return value


def _require_source_id(source_id: object) -> str:
    """Accept exactly the shape :func:`derive_source_id` produces."""
    source_id = _require_text(source_id, "source_id")
    if not source_id.startswith(SOURCE_ID_PREFIX):
        raise InvalidIdentifierError(f"source_id must start with {SOURCE_ID_PREFIX!r}, got {source_id!r}")
    _check_fingerprint(source_id[len(SOURCE_ID_PREFIX) :], f"source_id {source_id!r}")
    return source_id


def _check_fingerprint(fingerprint: object, what: str) -> str:
    fingerprint = _require_text(fingerprint, what)
    if EVIDENCE_SCOPE_SEPARATOR in fingerprint:
        raise InvalidIdentifierError(
            f"{what} must not contain the scope separator {EVIDENCE_SCOPE_SEPARATOR!r}"
        )
    if any(ch.isspace() for ch in fingerprint):
        raise InvalidIdentifierError(f"{what} must not contain whitespace")
    return fingerprint


def _require_non_negative_int(value: object, what: str) -> int:
    # ``bool`` is an ``int`` subclass; a flag passed as an index is a bug.
    if type(value) is not int or value < 0:
        raise InvalidIdentifierError(f"{what} must be a non-negative int, got {value!r}")
    return value


def _require_id_sequence(ids: object, what: str) -> list[str]:
    # A bare string is a Sequence of characters; reject it explicitly.
    if isinstance(ids, (str, bytes)) or not isinstance(ids, Sequence):
        raise InvalidIdentifierError(f"{what} must be a sequence of identifiers, got {ids!r}")
    for item in ids:
        _require_text(item, f"each of {what}")
    return list(ids)


def _digest(material: object) -> str:
    """First 16 hex chars of SHA-256 over the canonical JSON of *material*."""
    canonical = json.dumps(material, ensure_ascii=False, separators=(",", ":"))
    try:
        encoded = canonical.encode("utf-8")
    except UnicodeEncodeError as exc:  # e.g. a lone surrogate in an identifier field
        raise InvalidIdentifierError(f"identifier material is not valid UTF-8 text: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()[:_DIGEST_LENGTH]


# ---------------------------------------------------------------------------
# Source and run identity (3.1, 3.3, 3.5, 3.6)
# ---------------------------------------------------------------------------


def derive_source_id(document_fingerprint: str) -> str:
    """Return ``src:<document_fingerprint>``, adopting the supplied fingerprint verbatim.

    Different content fingerprints yield different source IDs (3.3). The
    fingerprint is not re-hashed (3.6).
    """
    _check_fingerprint(document_fingerprint, "document_fingerprint")
    return f"{SOURCE_ID_PREFIX}{document_fingerprint}"


def derive_run_id(identity_fields: Mapping[str, str]) -> str:
    """Return ``run:<sha256[:16]>`` over the supplied identity fields.

    The digest covers the fields sorted by key, so it is independent of mapping
    insertion order. The fields are fingerprints and identifiers supplied by
    the caller; none is computed here (3.6).
    """
    if not isinstance(identity_fields, Mapping) or not identity_fields:
        raise InvalidIdentifierError(
            f"identity_fields must be a non-empty mapping, got {identity_fields!r}"
        )
    for key, value in identity_fields.items():
        if not isinstance(key, str) or not key:
            raise InvalidIdentifierError(f"identity field names must be non-empty strings, got {key!r}")
        if not isinstance(value, str):
            raise InvalidIdentifierError(f"identity field {key!r} must be a string, got {value!r}")
    return f"{RUN_ID_PREFIX}{_digest(sorted(identity_fields.items()))}"


# ---------------------------------------------------------------------------
# Evidence identity (4.2, 4.7, 4.8)
# ---------------------------------------------------------------------------


def format_evidence_id(source_id: str, local_id: str) -> str:
    """Return ``<source_id>#<local_id>``; ``local_id`` is adopted unaltered (4.7).

    The only sanctioned constructor of an evidence node identifier (4.8).
    """
    _require_source_id(source_id)
    _require_text(local_id, "local_id")
    if EVIDENCE_SCOPE_SEPARATOR in local_id:
        raise InvalidIdentifierError(
            f"local_id must not contain the scope separator {EVIDENCE_SCOPE_SEPARATOR!r}, got {local_id!r}"
        )
    return f"{source_id}{EVIDENCE_SCOPE_SEPARATOR}{local_id}"


def parse_evidence_id(node_id: str) -> tuple[str, str]:
    """Return ``(source_id, local_id)``; the inverse of :func:`format_evidence_id`."""
    _require_text(node_id, "evidence node_id")
    source_id, separator, local_id = node_id.partition(EVIDENCE_SCOPE_SEPARATOR)
    if not separator:
        raise InvalidIdentifierError(
            f"evidence node_id {node_id!r} lacks the scope separator {EVIDENCE_SCOPE_SEPARATOR!r}"
        )
    try:
        format_evidence_id(source_id, local_id)
    except InvalidIdentifierError as exc:
        raise InvalidIdentifierError(f"malformed evidence node_id {node_id!r}: {exc}") from exc
    return source_id, local_id


# ---------------------------------------------------------------------------
# Claim and validation identity
# ---------------------------------------------------------------------------


def derive_claim_id(source_id: str, field_index: int, revision: int = 0) -> str:
    """Return ``claim:<source_id>#f<field_index>``, suffixed ``@r<revision>`` when revision > 0."""
    _require_source_id(source_id)
    _require_non_negative_int(field_index, "field_index")
    _require_non_negative_int(revision, "revision")
    claim_id = f"{CLAIM_ID_PREFIX}{source_id}{EVIDENCE_SCOPE_SEPARATOR}f{field_index}"
    return f"{claim_id}@r{revision}" if revision else claim_id


def derive_validation_id(run_id: str, check_name: str) -> str:
    """Return ``val:<run_id>:<check_name>``."""
    _require_text(run_id, "run_id")
    if not run_id.startswith(RUN_ID_PREFIX) or len(run_id) == len(RUN_ID_PREFIX):
        raise InvalidIdentifierError(f"run_id must be {RUN_ID_PREFIX!r} plus a digest, got {run_id!r}")
    _require_text(check_name, "check_name")
    return f"{VALIDATION_ID_PREFIX}{run_id}:{check_name}"


# ---------------------------------------------------------------------------
# Derivation-step identity (6.2, 3.3)
# ---------------------------------------------------------------------------


def derive_derivation_id(
    stage: str,
    kind: str,
    input_ids: Sequence[str],
    output_ids: Sequence[str],
) -> str:
    """Return ``drv:<sha256[:16]>`` over ``(stage, kind, sorted inputs, sorted outputs)``.

    Content-addressed: invariant under permutation of inputs and of outputs,
    identical across runs and processes for identical content (3.3), so a
    retried stage records the same step. Either side may be empty; whether such
    a step is consistent is decided by the chain validator.
    """
    _require_text(stage, "stage")
    _require_text(kind, "kind")
    inputs = _require_id_sequence(input_ids, "input_ids")
    outputs = _require_id_sequence(output_ids, "output_ids")
    return f"{DERIVATION_ID_PREFIX}{_digest([stage, kind, sorted(inputs), sorted(outputs)])}"
