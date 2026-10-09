"""evidence_adapter — evidence-index item mappings into ``EvidenceNode`` records.

Consumes the plain item mappings built by ``_build_items_from_tei`` in
``src/pipeline/evidence_index.py`` without importing ``pipeline``. The item
keys are mapped field-for-field:

==================  ==========================================================
Item key            ``EvidenceNode`` field
==================  ==========================================================
``id``              ``local_id``, adopted verbatim (4.7); scoped into
                    ``node_id`` by :func:`provenance.identity.format_evidence_id`
``type``            ``evidence_kind`` (4.1)
``page``            ``anchor.page`` (4.3)
``coords``          ``anchor.coords`` — ``[x0, y0, x1, y1]`` (4.3)
``section_path``    ``anchor.section_path`` (4.3)
``char_span``       ``anchor.char_span`` — ``[start, end]`` (4.3)
``xpath``           ``anchor.structural_path`` (4.3)
``score``           ``rank_score``
any other key       ``parser_metadata``, copied verbatim (10.2)
==================  ==========================================================

The producing backend is supplied by the caller (4.6): the items do not name it.

Anchor precision (4.4, 4.5):

- ``exact`` — ``page`` and ``coords`` both usable;
- ``approximate`` — not exact, but at least one anchor is usable: a page or a
  section path without coordinates, or only a structural path — whether the
  weak ``.//{tag}`` fallback or an ``xml:id`` path, neither of which places the
  item on the page;
- ``absent`` — no usable anchor at all.

Whenever precision is not ``exact``, ``anchor.missing`` names every anchor
kind (``SourceAnchor`` field name) that is absent, in ``SourceAnchor`` field
order (4.4).

Totality. A malformed item never raises: an anchor value of the wrong shape is
treated as absent, and an unknown or non-string ``type`` falls back to
``"sentence"`` (``EvidenceKind`` is closed). Such a value is not lost — it is
copied into ``parser_metadata`` under its original key; a ``None`` or empty
string value simply means "absent" and is not copied. The one thing an item
cannot lack is a usable identifier: without a non-empty string ``id`` free of
``#`` no evidence identity can be formed (4.7, 4.8), so
:func:`evidence_node_from_item` raises
:class:`~provenance.identity.InvalidIdentifierError` — for the caller to
convert into a missing segment — and :func:`evidence_nodes_from_items` skips
that item and converts the rest.

Nothing is dropped or coerced silently. Each skipped item and each ``type``
fallback is logged at DEBUG (item index and the ``id`` value only — never the
item's text), and a batch call that skipped or coerced anything logs one
WARNING with the counts and the ``source_id`` (design "Monitoring":
individual issues at DEBUG, no per-item WARNING).

Every node carries :data:`~provenance.privacy_carrier.UNKNOWN_PRIVACY` and no
extensions. The module computes no fingerprint or digest.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, get_args

from provenance.identity import InvalidIdentifierError, format_evidence_id
from provenance.models import EvidenceKind, EvidenceNode, SourceAnchor
from provenance.privacy_carrier import UNKNOWN_PRIVACY
from utils.logging_utils import get_logger

logger = get_logger(__name__)

__all__ = ["evidence_nodes_from_items", "evidence_node_from_item"]

# Key names emitted by the evidence index (src/pipeline/evidence_index.py).
_ID_KEY = "id"
_TYPE_KEY = "type"
_PAGE_KEY = "page"
_COORDS_KEY = "coords"
_SECTION_PATH_KEY = "section_path"
_CHAR_SPAN_KEY = "char_span"
_STRUCTURAL_PATH_KEY = "xpath"
_SCORE_KEY = "score"

_RECOGNIZED_KEYS = frozenset(
    {
        _ID_KEY,
        _TYPE_KEY,
        _PAGE_KEY,
        _COORDS_KEY,
        _SECTION_PATH_KEY,
        _CHAR_SPAN_KEY,
        _STRUCTURAL_PATH_KEY,
        _SCORE_KEY,
    }
)

_EVIDENCE_KINDS: frozenset[str] = frozenset(get_args(EvidenceKind))
_FALLBACK_EVIDENCE_KIND: EvidenceKind = "sentence"

# Anchor kinds as SourceAnchor field names, in SourceAnchor field order.
_ANCHOR_KINDS = ("page", "coords", "section_path", "char_span", "structural_path")

# A syntactically valid local id, used only to validate ``source_id`` up front.
_PROBE_LOCAL_ID = "probe"

_REPR_LIMIT = 80
"""Longest ``repr`` of an item value written to the log."""

_UNUSABLE = object()
"""Sentinel: the key holds a value of the wrong shape (kept in parser_metadata)."""


# ---------------------------------------------------------------------------
# Field parsers — each returns the usable value, None (absent) or _UNUSABLE
# ---------------------------------------------------------------------------


def _short_repr(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _REPR_LIMIT else text[: _REPR_LIMIT - 3] + "..."


def _is_absent(value: object) -> bool:
    return value is None or value == ""


def _is_int(value: object) -> bool:
    # ``bool`` is an ``int`` subclass; a flag is never a page, offset or score.
    return type(value) is int


def _is_real(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)  # type: ignore[arg-type]


def _parse_page(value: object) -> Any:
    if _is_absent(value):
        return None
    if _is_int(value) and value >= 0:  # type: ignore[operator]
        return value
    return _UNUSABLE


def _parse_coords(value: object) -> Any:
    if _is_absent(value):
        return None
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == 4
        and all(_is_real(part) for part in value)
    ):
        return tuple(float(part) for part in value)
    return _UNUSABLE


def _parse_char_span(value: object) -> Any:
    if _is_absent(value):
        return None
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and len(value) == 2
        and all(_is_int(part) for part in value)
        and 0 <= value[0] <= value[1]
    ):
        return (value[0], value[1])
    return _UNUSABLE


def _parse_text(value: object) -> Any:
    if _is_absent(value):
        return None
    return value if isinstance(value, str) else _UNUSABLE


def _parse_score(value: object) -> Any:
    if _is_absent(value):
        return None
    return value if _is_int(value) else _UNUSABLE


def _parse_kind(value: object) -> tuple[EvidenceKind, bool]:
    """Return ``(evidence_kind, coerced)``: ``coerced`` when the fallback kind was used."""
    if isinstance(value, str) and value in _EVIDENCE_KINDS:
        return value, False  # type: ignore[return-value]
    return _FALLBACK_EVIDENCE_KIND, True


_ANCHOR_PARSERS = (
    ("page", _PAGE_KEY, _parse_page),
    ("coords", _COORDS_KEY, _parse_coords),
    ("section_path", _SECTION_PATH_KEY, _parse_text),
    ("char_span", _CHAR_SPAN_KEY, _parse_char_span),
    ("structural_path", _STRUCTURAL_PATH_KEY, _parse_text),
)


# ---------------------------------------------------------------------------
# Anchor
# ---------------------------------------------------------------------------


def _anchor(item: Mapping[Any, Any], parser_metadata: dict[Any, Any]) -> SourceAnchor:
    """Build the anchor, deriving precision and the missing anchor kinds (4.3–4.5)."""
    fields: dict[str, Any] = {}
    for field_name, key, parse in _ANCHOR_PARSERS:
        value = parse(item.get(key))
        if value is _UNUSABLE:
            parser_metadata[key] = item[key]
            value = None
        fields[field_name] = value

    if fields["page"] is not None and fields["coords"] is not None:
        return SourceAnchor(precision="exact", **fields)
    missing = tuple(kind for kind in _ANCHOR_KINDS if fields[kind] is None)
    precision = "absent" if len(missing) == len(_ANCHOR_KINDS) else "approximate"
    return SourceAnchor(precision=precision, missing=missing, **fields)


# ---------------------------------------------------------------------------
# Argument checks (caller errors raise; item defects do not)
# ---------------------------------------------------------------------------


def _check_context(source_id: object, run_id: object, producing_backend: object) -> None:
    format_evidence_id(source_id, _PROBE_LOCAL_ID)  # type: ignore[arg-type]  # raises on a malformed source_id
    if not isinstance(run_id, str) or not run_id:
        raise InvalidIdentifierError(f"run_id must be a non-empty string, got {run_id!r}")
    if producing_backend is not None:
        if not isinstance(producing_backend, str):
            raise TypeError(f"producing_backend must be a str or None, got {type(producing_backend).__name__}")
        if not producing_backend:
            raise ValueError("producing_backend must be a non-empty string or None")


def _convert(
    item: object, *, source_id: str, run_id: str, producing_backend: str | None
) -> tuple[EvidenceNode, bool]:
    """Return ``(node, kind_coerced)``; raise ``InvalidIdentifierError`` when no id is usable."""
    if not isinstance(item, Mapping):
        raise InvalidIdentifierError(
            f"evidence item must be a mapping with an {_ID_KEY!r}, got {type(item).__name__}"
        )
    local_id = item.get(_ID_KEY)
    if not isinstance(local_id, str):
        raise InvalidIdentifierError(f"evidence item {_ID_KEY!r} must be a non-empty string, got {local_id!r}")
    node_id = format_evidence_id(source_id, local_id)  # rejects "" and "#"

    parser_metadata: dict[Any, Any] = {key: value for key, value in item.items() if key not in _RECOGNIZED_KEYS}

    raw_type = item.get(_TYPE_KEY)
    evidence_kind, kind_coerced = _parse_kind(raw_type)
    if kind_coerced:
        if not _is_absent(raw_type):
            parser_metadata[_TYPE_KEY] = raw_type
        logger.debug(
            "evidence item id=%s in %s: type %s is not an evidence kind; recorded as %r",
            _short_repr(local_id),
            source_id,
            _short_repr(raw_type) if _TYPE_KEY in item else "missing",
            evidence_kind,
        )

    rank_score = _parse_score(item.get(_SCORE_KEY))
    if rank_score is _UNUSABLE:
        parser_metadata[_SCORE_KEY] = item[_SCORE_KEY]
        rank_score = None

    anchor = _anchor(item, parser_metadata)

    node = EvidenceNode(
        node_id=node_id,
        kind="evidence",
        run_id=run_id,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        local_id=local_id,
        evidence_kind=evidence_kind,
        source_id=source_id,
        anchor=anchor,
        producing_backend=producing_backend,
        rank_score=rank_score,
        parser_metadata=parser_metadata,
    )
    return node, kind_coerced


# ---------------------------------------------------------------------------
# Public adapters
# ---------------------------------------------------------------------------


def evidence_node_from_item(
    item: Mapping[str, Any],
    *,
    source_id: str,
    run_id: str,
    producing_backend: str | None,
) -> EvidenceNode:
    """Convert one evidence-index item into an evidence node (4.1, 4.3–4.7, 10.2).

    Total over malformed anchors, kinds, scores and extra keys. Raises
    :class:`~provenance.identity.InvalidIdentifierError` only when the item has
    no usable identifier (not a mapping; ``id`` missing, not a string, empty, or
    containing ``#``) or ``source_id`` / ``run_id`` is malformed; raises
    ``TypeError`` / ``ValueError`` for a malformed ``producing_backend``.
    """
    _check_context(source_id, run_id, producing_backend)
    node, _ = _convert(item, source_id=source_id, run_id=run_id, producing_backend=producing_backend)
    return node


def evidence_nodes_from_items(
    items: Sequence[Mapping[str, Any]],
    *,
    source_id: str,
    run_id: str,
    producing_backend: str | None,
) -> list[EvidenceNode]:
    """Convert evidence-index items into evidence nodes, in item order (4.1, 4.3–4.7, 10.2).

    Never raises on an item: a malformed item yields a node (with
    ``precision="absent"`` when no anchor is usable), and an item without a
    usable identifier — which cannot become a node — is skipped. Each skip and
    each ``type`` fallback is logged at DEBUG, and one WARNING per call reports
    the skipped and coerced counts. Duplicates are kept; the recorder decides
    how to treat a repeated ``node_id``.
    Raises only on caller errors: ``items`` not a sequence (``TypeError``), a
    malformed ``source_id`` or ``run_id`` (``InvalidIdentifierError``), or a
    malformed ``producing_backend`` (``TypeError`` / ``ValueError``).
    """
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise TypeError(f"items must be a sequence of mappings, got {type(items).__name__}")
    _check_context(source_id, run_id, producing_backend)

    nodes: list[EvidenceNode] = []
    dropped = 0
    coerced = 0
    for index, item in enumerate(items):
        try:
            node, kind_coerced = _convert(
                item, source_id=source_id, run_id=run_id, producing_backend=producing_backend
            )
        except InvalidIdentifierError as exc:
            dropped += 1
            item_id = _short_repr(item.get(_ID_KEY)) if isinstance(item, Mapping) else "n/a"
            logger.debug(
                "evidence item %d in %s skipped (id=%s): %s", index, source_id, item_id, exc
            )
            continue
        coerced += kind_coerced
        nodes.append(node)

    if dropped or coerced:
        logger.warning(
            "evidence adapter for %s: %d of %d items skipped for lack of a usable id, "
            "%d recorded with fallback evidence kind %r",
            source_id,
            dropped,
            len(items),
            coerced,
            _FALLBACK_EVIDENCE_KIND,
        )
    return nodes
