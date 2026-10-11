"""serialization — the provenance graph as a plain, schema-versioned mapping.

:func:`graph_to_dict` and :func:`graph_from_dict` (provenance-core design,
"Persistence Layer → Serialization") convert a
:class:`~provenance.graph.ProvenanceGraph` to and from the artifact mapping of
the design's "Artifact schema"::

    {"schema_id", "schema_version", "run", "nodes", "edges",
     "missing_segments", "validation", "completeness"}

:func:`write_graph` and :func:`read_graph` (task 5.2) put that mapping on disk
as the run's single artifact and read it back; see "Run artifact" below.

Write direction
    :func:`graph_to_dict` takes the graph as its only data argument: the
    artifact is a pure function of the graph, which already carries the
    validation and completeness reports and every
    :class:`~provenance.models.ValidationRecord` the graph builder attached —
    a record from a new validation method is serialized like any other node,
    with no core type change (10.3). Every node and edge names its ``kind`` and
    the artifact names its schema identity and version, so it is interpretable
    without pipeline internals (7.3, 7.5). Records are emitted field by field
    in declaration order, nodes in ``node_id`` order, edges and missing
    segments in the graph's own order (graph equality depends on it), and
    free-form mappings (``extensions``, ``parser_metadata``, ``attributes``,
    ``details``, report mappings) with sorted keys — so equal graphs yield
    equal, deterministically ordered output. A value JSON cannot represent
    faithfully (a set, a non-string mapping key, an arbitrary object) raises
    ``TypeError`` naming its path rather than being coerced. Free-form values
    follow JSON value semantics: a tuple inside a free-form mapping is written
    as a list and read back as a (read-only) list.

Read direction and the version guard (10.5, 10.6)
    :func:`graph_from_dict` first checks the schema identity, then the schema
    version. A version whose SemVer major is not in
    :data:`SUPPORTED_SCHEMA_MAJORS` — or a version that is absent or not a
    SemVer string — raises :class:`UnrecognizedSchemaVersionError` naming the
    version found and the versions supported; nothing is materialized. A
    matching major is interpreted, and the version found is kept on the graph
    as its recorded version metadata.

    The reader validates the artifact against the closed vocabularies (design,
    "existing readers validate the artifact against the closed union"),
    the declared field types, required fields, non-empty identifiers and
    unique ``node_id`` values. Any violation raises
    :class:`MalformedProvenanceArtifactError` naming the offending path — a
    graph is never partially materialized from an artifact this reader cannot
    interpret. Typed tuple fields (identifier lists, ``coords``,
    ``char_span``, report tuples) are re-tupled, so
    ``graph_from_dict(graph_to_dict(g)) == g`` — also through ``json``.
    Optional fields absent from an artifact take their declared defaults.

Unknown keys of a compatible version (10.4, 10.5)
    Unknown keys are preserved, never dropped, wherever the record has a
    free-form mapping to hold them, under the reserved namespaced key
    :data:`COMPAT_UNKNOWN_KEY` (``x-compat-unknown``), whose value is a
    mapping from each unknown key to its value, verbatim:

    - a node's unknown keys go into its ``extensions``. Unknown keys inside the
      node's ``anchor`` or ``privacy`` object are nested under that field name
      (``{"anchor": {...}}``), and ``extensions`` keys that do not follow the
      ``x-<namespace>-<field>`` convention under ``"extensions"`` — none of
      these names can collide with an unknown top-level key, because they are
      core fields of the node;
    - an edge's unknown keys go into its ``attributes``;
    - a finding's unknown keys go into its ``details``.

    Original key names are kept as they are (no sanitizing), so the fallback
    is lossless whatever charset an unknown key uses. When the target mapping
    already holds ``x-compat-unknown`` the first free key of
    ``x-compat-unknown_2``, ``x-compat-unknown_3``, … is used instead.

    The run identity, missing segments, report containers, completeness
    entries and the artifact's top level have no free-form mapping, and
    adding one is a core type change outside this module. Their unknown keys
    are therefore not materialized; every such key is named, by path, in one
    WARNING per read.

Run artifact (1.3, 7.7)
    :func:`write_graph` writes exactly one file per run,
    ``<directory>/`` :data:`ARTIFACT_FILENAME` (``provenance_graph.json``),
    into the directory its caller passes — it reads no configuration; the
    pipeline resolves the directory. The directory is created when missing.
    The graph is encoded in full *before* the directory or any file is
    touched, then written to a temporary file next to the target and moved
    over it with ``os.replace`` (the ``save_manifest`` pattern, so a reader
    sees the previous artifact or the new one, never a partial file). Any
    failure removes the temporary file and leaves an existing artifact
    untouched. The artifact is strict JSON — ``allow_nan=False``; a NaN or
    infinite float raises ``ValueError`` naming its path, and a set or other
    unrepresentable value raises ``TypeError`` (see Write direction) — and is
    UTF-8, two-space indented, ``ensure_ascii=False``, ``\\n`` line endings with
    one trailing newline. ``sort_keys`` is *not* used: :func:`graph_to_dict`
    already sorts free-form mappings and keeps the schema's key order, and a
    global sort would reorder the top-level keys and record fields. The same
    graph therefore always yields the same bytes. The artifact is the
    package's own storage format: no interoperable or external export format
    and no query language is defined here (7.7).

    :func:`read_graph` takes the artifact file, or a directory holding
    ``provenance_graph.json``. A missing file raises ``FileNotFoundError``;
    content that is not UTF-8 JSON, contains a ``NaN``/``Infinity`` constant,
    or is not a JSON object raises :class:`MalformedProvenanceArtifactError`;
    everything else is :func:`graph_from_dict`'s verdict, including
    :class:`UnrecognizedSchemaVersionError` for an incompatible major.

Imports only the standard library, ``provenance.models``,
``provenance.privacy_carrier``, ``provenance.graph`` and
``utils.logging_utils``.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import math
import os
import re
import types
import typing
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Literal, Union

from provenance.graph import PROVENANCE_SCHEMA_ID, PROVENANCE_SCHEMA_VERSION, ProvenanceGraph
from provenance.models import (
    ClaimNode,
    DerivationStep,
    EvidenceNode,
    ProvenanceEdge,
    ProvenanceNode,
    SourceNode,
    ValidationFinding,
    ValidationRecord,
    is_extension_key,
)
from utils.logging_utils import get_logger

__all__ = [
    "ARTIFACT_FILENAME",
    "COMPAT_UNKNOWN_KEY",
    "SUPPORTED_SCHEMA_MAJORS",
    "UnrecognizedSchemaVersionError",
    "MalformedProvenanceArtifactError",
    "graph_to_dict",
    "graph_from_dict",
    "write_graph",
    "read_graph",
]

logger = get_logger(__name__)

ARTIFACT_FILENAME = "provenance_graph.json"
"""Name of the run's single artifact file inside the directory passed to :func:`write_graph`."""

COMPAT_UNKNOWN_KEY = "x-compat-unknown"
"""Reserved extension key under which unknown keys of a compatible artifact are preserved."""

_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def _major(version: object) -> int | None:
    """The SemVer major of ``version``, or ``None`` when it is not a SemVer string."""
    if not isinstance(version, str):
        return None
    match = _SEMVER.fullmatch(version)
    return int(match.group(1)) if match else None


_WRITER_MAJOR = _major(PROVENANCE_SCHEMA_VERSION)
if _WRITER_MAJOR is None:  # pragma: no cover — guards an edit to PROVENANCE_SCHEMA_VERSION
    raise RuntimeError(f"PROVENANCE_SCHEMA_VERSION {PROVENANCE_SCHEMA_VERSION!r} is not a SemVer string")

SUPPORTED_SCHEMA_MAJORS: tuple[int, ...] = (_WRITER_MAJOR,)
"""SemVer majors this reader interprets: the major of ``PROVENANCE_SCHEMA_VERSION``."""

_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "schema_id",
    "schema_version",
    "run",
    "nodes",
    "edges",
    "missing_segments",
    "validation",
    "completeness",
)

_NODE_CLASSES: Mapping[str, type[ProvenanceNode]] = {
    "source": SourceNode,
    "evidence": EvidenceNode,
    "claim": ClaimNode,
    "derivation": DerivationStep,
    "validation": ValidationRecord,
}
"""The concrete record class each node ``kind`` materializes as."""

_SLOTS: tuple[tuple[type, str], ...] = (
    (ProvenanceNode, "extensions"),
    (ProvenanceEdge, "attributes"),
    (ValidationFinding, "details"),
)
"""The free-form mapping of each record type that holds its unknown keys."""

_NON_EMPTY: tuple[tuple[type, tuple[str, ...]], ...] = (
    (ProvenanceNode, ("node_id",)),
    (ProvenanceEdge, ("edge_id", "from_id", "to_id")),
    (DerivationStep, ("stage",)),
)
"""Identifier fields that must be non-empty strings, as the graph builder requires."""


class UnrecognizedSchemaVersionError(Exception):
    """An artifact declares a schema version this reader does not interpret (10.6).

    ``found_version`` is the value the artifact declared (``None`` when it
    declared none); ``supported_versions`` names the versions this reader
    interprets, one ``"<major>.x.x"`` pattern per supported major.
    """

    def __init__(self, found_version: object) -> None:
        self.found_version = found_version
        self.supported_versions: tuple[str, ...] = tuple(f"{major}.x.x" for major in SUPPORTED_SCHEMA_MAJORS)
        super().__init__(
            f"unrecognized provenance schema version {found_version!r}: this reader supports "
            f"{', '.join(self.supported_versions)} (it writes {PROVENANCE_SCHEMA_VERSION}); "
            "the artifact was not interpreted"
        )


class MalformedProvenanceArtifactError(ValueError):
    """A compatible-version artifact does not conform to the provenance schema."""


# ---------------------------------------------------------------------------
# Paths (for error and warning messages)
# ---------------------------------------------------------------------------


def _field_path(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _index_path(path: str, index: int) -> str:
    return f"{path}[{index}]"


def _key_path(path: str, key: object) -> str:
    return f"{path}[{key!r}]"


def _describe(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


# ---------------------------------------------------------------------------
# Write direction
# ---------------------------------------------------------------------------


def _encode(value: Any, path: str) -> Any:
    """Return the plain JSON value of ``value``; raise ``TypeError`` when it has none."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _encode(getattr(value, field.name), _field_path(path, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        keys = list(value)
        for key in keys:
            if not isinstance(key, str):
                raise TypeError(f"{_key_path(path, key)}: mapping key of type {type(key).__name__} is not a JSON string")
        return {key: _encode(value[key], _key_path(path, key)) for key in sorted(keys)}
    if isinstance(value, (list, tuple)):
        return [_encode(item, _index_path(path, index)) for index, item in enumerate(value)]
    raise TypeError(f"{path}: value of type {type(value).__name__} is not JSON-representable")


def graph_to_dict(graph: ProvenanceGraph) -> dict[str, Any]:
    """Return the artifact mapping of ``graph`` — a pure function of the graph (7.3, 7.5, 10.3).

    Keys follow the design's artifact schema in its order; nodes are listed
    in ``node_id`` order, edges and missing segments in the graph's order.
    Raises ``TypeError`` when ``graph`` is not a ``ProvenanceGraph`` or holds a
    value JSON cannot represent, and ``ValueError`` when a node is keyed under
    an identifier other than its own ``node_id``.
    """
    if not isinstance(graph, ProvenanceGraph):
        raise TypeError(f"graph must be a ProvenanceGraph, got {type(graph).__name__}")
    nodes: list[Any] = []
    for index, key in enumerate(sorted(graph.nodes)):
        node = graph.nodes[key]
        if getattr(node, "node_id", None) != key:
            raise ValueError(f"nodes[{key!r}]: node is keyed under an identifier other than its node_id")
        nodes.append(_encode(node, _index_path("nodes", index)))
    return {
        "schema_id": _encode(graph.schema_id, "schema_id"),
        "schema_version": _encode(graph.schema_version, "schema_version"),
        "run": _encode(graph.run, "run"),
        "nodes": nodes,
        "edges": _encode(graph.edges, "edges"),
        "missing_segments": _encode(graph.missing_segments, "missing_segments"),
        "validation": _encode(graph.validation, "validation"),
        "completeness": _encode(graph.completeness, "completeness"),
    }


# ---------------------------------------------------------------------------
# Read direction
# ---------------------------------------------------------------------------


@functools.cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _has_default(field: dataclasses.Field[Any]) -> bool:
    return field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING


def _construct(cls: type, kwargs: dict[str, Any], path: str) -> Any:
    """Build one record; any construction failure is a malformed artifact."""
    try:
        return cls(**kwargs)
    except (TypeError, ValueError) as exc:
        raise MalformedProvenanceArtifactError(f"{path or 'artifact'}: cannot build {cls.__name__}: {exc}") from exc


def _slot_of(cls: type) -> str | None:
    for owner, slot in _SLOTS:
        if issubclass(cls, owner):
            return slot
    return None


def _merge_unknown(mapping: Mapping[str, Any], unknown: Mapping[str, Any]) -> dict[str, Any]:
    """``mapping`` plus ``unknown`` under the first free reserved key."""
    merged = dict(mapping)
    key, ordinal = COMPAT_UNKNOWN_KEY, 2
    while key in merged:
        key, ordinal = f"{COMPAT_UNKNOWN_KEY}_{ordinal}", ordinal + 1
    merged[key] = dict(unknown)
    return merged


class _Decoder:
    """Type-directed decoding of one artifact; collects the paths of dropped unknown keys."""

    def __init__(self) -> None:
        self.dropped: list[str] = []

    # -- generic values ----------------------------------------------------

    def value(self, hint: Any, raw: Any, path: str, *, bubble: bool) -> tuple[Any, dict[str, Any]]:
        """Decode ``raw`` as ``hint``; return the value and any unknown keys bubbled to the parent."""
        origin = typing.get_origin(hint)
        if hint is Any:
            return raw, {}
        if origin is Union or origin is types.UnionType:
            return self._union(hint, raw, path, bubble=bubble)
        if origin is Literal:
            if not isinstance(raw, str) or raw not in typing.get_args(hint):
                raise MalformedProvenanceArtifactError(
                    f"{path}: {_describe(raw)} is not one of {list(typing.get_args(hint))}"
                )
            return raw, {}
        if origin is tuple:
            return self._tuple(hint, raw, path), {}
        if origin is Mapping:
            return self._mapping(hint, raw, path), {}
        if isinstance(hint, type) and dataclasses.is_dataclass(hint):
            return self.record(hint, raw, path, bubble=bubble)
        if hint is type(None):
            if raw is not None:
                raise MalformedProvenanceArtifactError(f"{path}: expected null, got {_describe(raw)}")
            return None, {}
        return self._scalar(hint, raw, path), {}

    def _union(self, hint: Any, raw: Any, path: str, *, bubble: bool) -> tuple[Any, dict[str, Any]]:
        members = typing.get_args(hint)
        if raw is None and type(None) in members:
            return None, {}
        candidates = [member for member in members if member is not type(None)]
        for member in candidates[:-1]:
            try:
                return self.value(member, raw, path, bubble=bubble)
            except MalformedProvenanceArtifactError:
                continue
        return self.value(candidates[-1], raw, path, bubble=bubble)

    @staticmethod
    def _scalar(hint: Any, raw: Any, path: str) -> Any:
        if hint is str:
            ok = isinstance(raw, str)
        elif hint is bool:
            ok = isinstance(raw, bool)
        elif hint is int:
            ok = isinstance(raw, int) and not isinstance(raw, bool)
        elif hint is float:
            ok = isinstance(raw, (int, float)) and not isinstance(raw, bool)
        else:  # pragma: no cover — every declared field type is handled above
            raise MalformedProvenanceArtifactError(f"{path}: unsupported field type {hint!r}")
        if not ok:
            raise MalformedProvenanceArtifactError(
                f"{path}: expected {getattr(hint, '__name__', hint)}, got {type(raw).__name__} {_describe(raw)}"
            )
        return raw

    def _tuple(self, hint: Any, raw: Any, path: str) -> tuple[Any, ...]:
        if not isinstance(raw, (list, tuple)):
            raise MalformedProvenanceArtifactError(f"{path}: expected an array, got {type(raw).__name__}")
        args = typing.get_args(hint)
        if len(args) == 2 and args[1] is Ellipsis:
            item_hints = [args[0]] * len(raw)
        else:
            if len(raw) != len(args):
                raise MalformedProvenanceArtifactError(f"{path}: expected {len(args)} items, got {len(raw)}")
            item_hints = list(args)
        return tuple(
            self.value(item_hint, item, _index_path(path, index), bubble=False)[0]
            for index, (item_hint, item) in enumerate(zip(item_hints, raw))
        )

    def _mapping(self, hint: Any, raw: Any, path: str) -> dict[Any, Any]:
        if not isinstance(raw, Mapping):
            raise MalformedProvenanceArtifactError(f"{path}: expected an object, got {type(raw).__name__}")
        key_hint, value_hint = typing.get_args(hint)
        decoded: dict[Any, Any] = {}
        for key, item in raw.items():
            item_path = _key_path(path, key)
            decoded_key = self.value(key_hint, key, f"{item_path} (key)", bubble=False)[0]
            decoded[decoded_key] = self.value(value_hint, item, item_path, bubble=False)[0]
        return decoded

    # -- records -----------------------------------------------------------

    def record(self, cls: type, raw: Any, path: str, *, bubble: bool) -> tuple[Any, dict[str, Any]]:
        """Decode one record; preserve or report its unknown keys; return any bubbled to the parent."""
        if not isinstance(raw, Mapping):
            raise MalformedProvenanceArtifactError(f"{path}: expected an object, got {type(raw).__name__}")
        if cls is ProvenanceNode:
            kind = raw.get("kind")
            concrete = _NODE_CLASSES.get(kind) if isinstance(kind, str) else None
            if concrete is None:
                raise MalformedProvenanceArtifactError(
                    f"{_field_path(path, 'kind')}: {_describe(kind)} is not one of {list(_NODE_CLASSES)}"
                )
            cls = concrete

        slot = _slot_of(cls)
        hints = _hints(cls)
        names = [field.name for field in dataclasses.fields(cls)]
        kwargs: dict[str, Any] = {}
        unknown: dict[str, Any] = {}
        for field in dataclasses.fields(cls):
            field_path = _field_path(path, field.name)
            if field.name not in raw:
                if _has_default(field):
                    continue
                raise MalformedProvenanceArtifactError(f"{path or 'artifact'}: missing required key {field.name!r}")
            value, nested = self.value(hints[field.name], raw[field.name], field_path, bubble=slot is not None)
            if nested:
                unknown[field.name] = nested
            kwargs[field.name] = value
        for key in raw:
            if key not in names:
                unknown[key] = raw[key]

        for owner, required in _NON_EMPTY:
            if issubclass(cls, owner):
                for name in required:
                    if not kwargs.get(name):
                        raise MalformedProvenanceArtifactError(
                            f"{_field_path(path, name)}: must be a non-empty string"
                        )

        if slot == "extensions":
            extensions = dict(kwargs["extensions"])
            outside = {key: extensions.pop(key) for key in list(extensions) if not is_extension_key(key)}
            if outside:
                unknown["extensions"] = outside
            kwargs["extensions"] = extensions

        if slot is not None:
            if unknown:
                kwargs[slot] = _merge_unknown(kwargs[slot], unknown)
            return _construct(cls, kwargs, path), {}
        record = _construct(cls, kwargs, path)
        if bubble:
            return record, unknown
        self._drop(unknown, path)
        return record, {}

    def _drop(self, unknown: Mapping[str, Any], path: str) -> None:
        self.dropped.extend(_field_path(path, key) for key in unknown)

    # -- the graph ---------------------------------------------------------

    def graph(self, payload: Mapping[str, Any]) -> ProvenanceGraph:
        hints = _hints(ProvenanceGraph)
        kwargs: dict[str, Any] = {}
        for field in dataclasses.fields(ProvenanceGraph):
            if field.name not in payload:
                if _has_default(field):
                    continue
                raise MalformedProvenanceArtifactError(f"artifact: missing required key {field.name!r}")
            if field.name == "nodes":
                kwargs["nodes"] = self._nodes(payload["nodes"])
            else:
                kwargs[field.name] = self.value(hints[field.name], payload[field.name], field.name, bubble=False)[0]
        self._drop({key: None for key in payload if key not in _TOP_LEVEL_KEYS}, "")
        return _construct(ProvenanceGraph, kwargs, "")

    def _nodes(self, raw: Any) -> dict[str, ProvenanceNode]:
        if not isinstance(raw, list):
            raise MalformedProvenanceArtifactError(f"nodes: expected an array, got {type(raw).__name__}")
        nodes: dict[str, ProvenanceNode] = {}
        for index, entry in enumerate(raw):
            path = _index_path("nodes", index)
            node, _ = self.record(ProvenanceNode, entry, path, bubble=False)
            if node.node_id in nodes:
                raise MalformedProvenanceArtifactError(f"{path}: duplicate node_id {node.node_id!r}")
            nodes[node.node_id] = node
        return {node_id: nodes[node_id] for node_id in sorted(nodes)}


def _check_identity(payload: Any) -> None:
    """Schema identity and version guard; raises before anything is materialized (10.6)."""
    if not isinstance(payload, Mapping):
        raise MalformedProvenanceArtifactError(
            f"a provenance artifact must be a JSON object, got {type(payload).__name__}"
        )
    schema_id = payload.get("schema_id")
    if schema_id != PROVENANCE_SCHEMA_ID:
        raise MalformedProvenanceArtifactError(
            f"schema_id: expected {PROVENANCE_SCHEMA_ID!r}, got {_describe(schema_id)}"
        )
    version = payload.get("schema_version")
    if _major(version) not in SUPPORTED_SCHEMA_MAJORS:
        raise UnrecognizedSchemaVersionError(version)


def graph_from_dict(payload: Mapping[str, Any]) -> ProvenanceGraph:
    """Materialize the graph of an artifact mapping, under the schema version guard (10.4–10.6).

    Raises :class:`UnrecognizedSchemaVersionError` — before anything is
    materialized — when the artifact's major version is not supported or its
    version is absent or not SemVer, and
    :class:`MalformedProvenanceArtifactError` when it is not a provenance
    artifact or does not conform to the schema. Unknown keys of a compatible
    version are preserved under :data:`COMPAT_UNKNOWN_KEY` where the record
    has a free-form mapping, and otherwise named in one WARNING. ``payload``
    is not modified.
    """
    _check_identity(payload)
    decoder = _Decoder()
    graph = decoder.graph(payload)
    if decoder.dropped:
        logger.warning(
            "provenance artifact %s (schema %s): %d unknown key(s) with no extension slot were not "
            "materialized: %s",
            graph.run.run_id,
            graph.schema_version,
            len(decoder.dropped),
            ", ".join(decoder.dropped),
        )
    return graph


# ---------------------------------------------------------------------------
# Run artifact: write and read (1.3, 7.7)
# ---------------------------------------------------------------------------


def _non_finite_paths(value: Any, path: str) -> Iterator[tuple[str, float]]:
    """Yield ``(path, value)`` for every NaN or infinite float in an encoded artifact."""
    if isinstance(value, float):
        if not math.isfinite(value):
            yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _non_finite_paths(item, _key_path(path, key) if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _non_finite_paths(item, _index_path(path, index))


def _encode_artifact(graph: ProvenanceGraph) -> bytes:
    """The artifact bytes of ``graph``: strict, deterministic UTF-8 JSON with a trailing newline."""
    payload = graph_to_dict(graph)
    for path, value in _non_finite_paths(payload, ""):
        raise ValueError(f"{path}: non-finite float {value!r} is not valid JSON; the artifact was not written")
    text = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def write_graph(graph: ProvenanceGraph, path: Path) -> Path:
    """Write ``graph`` as the run's single artifact into directory ``path``; return the file written.

    ``path`` is the directory (created when missing); the file is
    ``path / ARTIFACT_FILENAME``. The write is atomic: a temporary file in the
    same directory is replaced over the target with ``os.replace``, and is
    removed on any failure, so an existing artifact is never clobbered or
    half-written. Rewriting an identical graph produces a byte-identical file.

    Raises ``TypeError`` for a non-graph or a value JSON cannot represent (a
    set, a non-string key), ``ValueError`` for a NaN or infinite float, and
    ``OSError`` for a filesystem failure; none of them leaves a file behind.
    """
    data = _encode_artifact(graph)  # everything that can fail on content fails before the filesystem is touched
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ARTIFACT_FILENAME
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.info("wrote provenance artifact %s (%d bytes)", target, len(data))
    return target


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name} is not allowed")


def read_graph(path: Path) -> ProvenanceGraph:
    """Read the run artifact at ``path`` — the artifact file, or the directory that holds it.

    A missing file raises ``FileNotFoundError``. Content that is not UTF-8
    JSON, uses a ``NaN``/``Infinity`` constant, or is not a JSON object raises
    :class:`MalformedProvenanceArtifactError`; the schema checks are those of
    :func:`graph_from_dict` (so an incompatible major raises
    :class:`UnrecognizedSchemaVersionError`, and nothing is materialized).
    """
    target = Path(path)
    if target.is_dir():
        target = target / ARTIFACT_FILENAME
    raw = target.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError) as exc:  # JSONDecodeError is a ValueError
        raise MalformedProvenanceArtifactError(f"{target}: not a valid UTF-8 JSON provenance artifact: {exc}") from exc
    if not isinstance(payload, dict):
        raise MalformedProvenanceArtifactError(
            f"{target}: a provenance artifact must be a JSON object, got {type(payload).__name__}"
        )
    return graph_from_dict(payload)
