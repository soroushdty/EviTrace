"""identity_adapter — checkpoint-identity mappings into ``SourceNode`` and ``RunIdentity``.

Consumes the plain mapping produced by ``ManifestIdentity.to_dict()``
(``pdf_content_hash``, ``config_hash``, ``extraction_map_hash``, ``model_id``,
``schema_version``, ``output_path``) without importing ``pipeline``.

- The adapter computes **no** fingerprint of its own (3.6). Document,
  configuration and extraction-map fingerprints are adopted verbatim from the
  mapping, so the run carries exactly one fingerprint set — the manifest's.
  Identifiers are derived only through :mod:`provenance.identity`.
- A source identity needs only the content fingerprints (3.4): the descriptive
  ``source_label`` and ``source_format`` may be empty strings and ``model_id``
  may be absent.
- Source metadata is copied verbatim and never inspected (3.7); every node
  carries :data:`~provenance.privacy_carrier.UNKNOWN_PRIVACY`. Disclosure is
  decided elsewhere.
- The adapter consumes ``pdf_content_hash``, ``config_hash``,
  ``extraction_map_hash`` and ``model_id``. Every other key
  (``schema_version``, ``output_path``, any future key) is kept on the source
  node, never rejected or dropped: as extension ``x-manifest-<key>`` when that
  satisfies the extension-key charset, otherwise inside the single fallback
  extension ``x-manifest-unnamespaced`` (a mapping of stringified raw key ->
  value, logged once at WARNING), mirroring the derivation adapter's
  ``x-qc-unnamespaced``. Values are copied unchanged and must be JSON-native.
  ``x-manifest-output_path`` is relative to the run output directory and is
  Requirement 1.3's resolvable link from the source record to the extraction
  output.
- :func:`run_identity_from_identities` adopts the per-document model
  identifiers plus ``additional_model_ids`` — through which the caller supplies
  a synthesis model that differs from the chunk model (3.5) — so the run
  identifier changes with the synthesis model.

A missing or malformed fingerprint raises
:class:`~provenance.identity.InvalidIdentifierError`; the caller converts it
into a missing segment. Wrong argument types raise ``TypeError``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from provenance.identity import InvalidIdentifierError, derive_run_id, derive_source_id
from provenance.models import RunIdentity, SourceNode, is_extension_key
from provenance.privacy_carrier import UNKNOWN_PRIVACY
from utils.logging_utils import get_logger

logger = get_logger(__name__)

__all__ = ["source_node_from_identity", "run_identity_from_identities"]

# Key names emitted by ``ManifestIdentity.to_dict()`` (src/pipeline/manifest.py).
_DOCUMENT_FINGERPRINT_KEY = "pdf_content_hash"
_CONFIG_FINGERPRINT_KEY = "config_hash"
_SCHEMA_FINGERPRINT_KEY = "extraction_map_hash"
_MODEL_ID_KEY = "model_id"

_CONSUMED_KEYS = frozenset(
    {_DOCUMENT_FINGERPRINT_KEY, _CONFIG_FINGERPRINT_KEY, _SCHEMA_FINGERPRINT_KEY, _MODEL_ID_KEY}
)

MANIFEST_EXTENSION_NAMESPACE = "manifest"
"""Extension namespace for unconsumed checkpoint-identity keys (``x-manifest-<key>``)."""

_MANIFEST_UNNAMESPACED_KEY = f"x-{MANIFEST_EXTENSION_NAMESPACE}-unnamespaced"

_REPR_LIMIT = 80


def _short_repr(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _REPR_LIMIT else text[: _REPR_LIMIT - 3] + "..."


def _require_mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def _require_str(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} must be a str, got {type(value).__name__}")
    return value


def _fingerprint(identity: Mapping[str, object], key: str) -> str:
    """Return the fingerprint stored under *key*, adopted verbatim."""
    value = identity.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidIdentifierError(f"identity mapping needs a non-empty string {key!r}, got {value!r}")
    return value


def _model_ids(identity: Mapping[str, object]) -> tuple[str, ...]:
    """The model identifier in effect, or ``()`` when the mapping carries none."""
    value = identity.get(_MODEL_ID_KEY)
    if value is None or value == "":
        return ()
    if not isinstance(value, str):
        raise InvalidIdentifierError(f"{_MODEL_ID_KEY!r} must be a string, got {value!r}")
    return (value,)


def _manifest_extensions(identity: Mapping[Any, Any], document_fingerprint: str) -> dict[str, Any]:
    """Keep every unconsumed key as ``x-manifest-<key>``; others go to ``x-manifest-unnamespaced``."""
    extensions: dict[str, Any] = {}
    unnamespaced: dict[str, Any] = {}
    for key, value in identity.items():
        if key in _CONSUMED_KEYS:
            continue
        candidate = f"x-{MANIFEST_EXTENSION_NAMESPACE}-{key}" if isinstance(key, str) else None
        if candidate is not None and is_extension_key(candidate) and candidate != _MANIFEST_UNNAMESPACED_KEY:
            extensions[candidate] = value
        else:
            unnamespaced[str(key)] = value
    if unnamespaced:
        extensions[_MANIFEST_UNNAMESPACED_KEY] = unnamespaced
        logger.warning(
            "checkpoint identity for src:%s: %d key(s) cannot form an 'x-%s-<field>' extension key; "
            "kept under %s: %s",
            document_fingerprint,
            len(unnamespaced),
            MANIFEST_EXTENSION_NAMESPACE,
            _MANIFEST_UNNAMESPACED_KEY,
            ", ".join(_short_repr(key) for key in unnamespaced),
        )
    return extensions


def _additional_model_ids(additional_model_ids: object) -> tuple[str, ...]:
    """Validate the caller-supplied extra model identifiers; empty strings mean "none"."""
    if isinstance(additional_model_ids, (str, bytes)) or not isinstance(additional_model_ids, Sequence):
        raise TypeError(
            f"additional_model_ids must be a sequence of str, got {type(additional_model_ids).__name__}"
        )
    for model_id in additional_model_ids:
        if not isinstance(model_id, str):
            raise TypeError(f"additional_model_ids entries must be str, got {type(model_id).__name__}")
    return tuple(model_id for model_id in additional_model_ids if model_id)


def source_node_from_identity(
    identity: Mapping[str, str],
    *,
    source_label: str,
    source_format: str,
    run_id: str,
) -> SourceNode:
    """Build the source node for one document from its checkpoint identity (3.1, 3.2, 3.4, 3.7).

    ``node_id`` is ``src:<pdf_content_hash>``; the configuration and schema
    fingerprints and model identifier are copied from the same mapping.
    ``source_label`` and ``source_format`` are stored as given (empty is valid).
    Every unconsumed key becomes an ``x-manifest-*`` extension (1.3).
    """
    identity = _require_mapping(identity, "identity")  # type: ignore[assignment]
    _require_str(source_label, "source_label")
    _require_str(source_format, "source_format")
    if not isinstance(run_id, str) or not run_id:
        raise InvalidIdentifierError(f"run_id must be a non-empty string, got {run_id!r}")

    document_fingerprint = _fingerprint(identity, _DOCUMENT_FINGERPRINT_KEY)
    return SourceNode(
        node_id=derive_source_id(document_fingerprint),
        kind="source",
        run_id=run_id,
        privacy=UNKNOWN_PRIVACY,
        extensions=_manifest_extensions(identity, document_fingerprint),
        document_fingerprint=document_fingerprint,
        source_format=source_format,
        source_label=source_label,
        config_fingerprint=_fingerprint(identity, _CONFIG_FINGERPRINT_KEY),
        schema_fingerprint=_fingerprint(identity, _SCHEMA_FINGERPRINT_KEY),
        model_ids=_model_ids(identity),
    )


def _single_run_value(values: Mapping[str, str], key: str) -> str:
    """The one value every document shares for a run-wide fingerprint (3.6)."""
    distinct = set(values.values())
    if len(distinct) != 1:
        listed = ", ".join(f"{doc!r}: {value!r}" for doc, value in sorted(values.items()))
        raise InvalidIdentifierError(
            f"a run has exactly one {key!r}, but the documents disagree ({listed})"
        )
    return next(iter(distinct))


def run_identity_from_identities(
    identities: Mapping[str, Mapping[str, str]],
    *,
    created_at: str,
    additional_model_ids: Sequence[str] = (),
) -> RunIdentity:
    """Build the run identity from the per-document checkpoint identities (3.5, 3.6).

    *identities* maps a document key (as the caller names documents) to that
    document's ``ManifestIdentity.to_dict()``. The configuration and extraction
    schema fingerprints must agree across documents — one set per run — and are
    adopted verbatim; ``model_ids`` is the sorted set of the per-document model
    identifiers plus *additional_model_ids* (e.g. a synthesis model distinct
    from the chunk model; empty strings are ignored, a non-``str`` entry or a
    bare string raises ``TypeError``). ``run_id`` is derived by
    :func:`provenance.identity.derive_run_id` over those three run-wide fields,
    so it is independent of document order and of ``created_at``, which is
    recorded verbatim, and changes when an additional model is added.
    """
    identities = _require_mapping(identities, "identities")  # type: ignore[assignment]
    _require_str(created_at, "created_at")
    extra_model_ids = _additional_model_ids(additional_model_ids)
    if not identities:
        raise InvalidIdentifierError("identities must contain at least one document identity")

    document_fingerprints: dict[str, str] = {}
    config_fingerprints: dict[str, str] = {}
    schema_fingerprints: dict[str, str] = {}
    model_ids: set[str] = set()
    for document_key, identity in identities.items():
        _require_str(document_key, "document key")
        identity = _require_mapping(identity, f"identity of {document_key!r}")
        document_fingerprints[document_key] = _fingerprint(identity, _DOCUMENT_FINGERPRINT_KEY)
        config_fingerprints[document_key] = _fingerprint(identity, _CONFIG_FINGERPRINT_KEY)
        schema_fingerprints[document_key] = _fingerprint(identity, _SCHEMA_FINGERPRINT_KEY)
        model_ids.update(_model_ids(identity))

    config_fingerprint = _single_run_value(config_fingerprints, _CONFIG_FINGERPRINT_KEY)
    schema_fingerprint = _single_run_value(schema_fingerprints, _SCHEMA_FINGERPRINT_KEY)
    model_ids.update(extra_model_ids)
    sorted_model_ids = tuple(sorted(model_ids))

    run_id = derive_run_id(
        {
            "config_fingerprint": config_fingerprint,
            "schema_fingerprint": schema_fingerprint,
            # derive_run_id takes str values; serialize the tuple deterministically.
            "model_ids": json.dumps(list(sorted_model_ids)),
        }
    )
    return RunIdentity(
        run_id=run_id,
        config_fingerprint=config_fingerprint,
        schema_fingerprint=schema_fingerprint,
        model_ids=sorted_model_ids,
        document_fingerprints=document_fingerprints,
        created_at=created_at,
    )
