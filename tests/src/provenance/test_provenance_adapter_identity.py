"""Tests for ``provenance.adapters.identity_adapter`` — checkpoint identity -> SourceNode / RunIdentity.

Covers building a source node from a literal ``ManifestIdentity.to_dict()``-shaped
mapping (3.1, 3.2), a metadata-free mapping still yielding a source identity
(3.4), the run identity recording the configuration fingerprint, extraction
schema fingerprint and model identifiers (3.5), the single fingerprint set per
run with no fingerprint computed by the adapter (3.6 — asserted both by AST scan
and by counting digest calls at runtime), and verbatim copying of source
metadata with no disclosure evaluation (3.7).
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from pathlib import Path

import pytest

from provenance import adapters
from provenance.adapters import identity_adapter
from provenance.adapters.identity_adapter import (
    run_identity_from_identities,
    source_node_from_identity,
)
from provenance.identity import InvalidIdentifierError, derive_run_id
from provenance.models import RunIdentity, SourceNode
from provenance.privacy_carrier import UNKNOWN_PRIVACY

_PDF_A = "a" * 64
_PDF_B = "b" * 64
_CONFIG = "c" * 64
_MAP = "d" * 64
_MODEL = "gpt-5-mini"
_RUN_ID = "run:0123456789abcdef"
_CREATED_AT = "2026-10-08T12:00:00+00:00"


def _identity(pdf_hash: str = _PDF_A, **overrides: str) -> dict[str, str]:
    """A literal mapping in the exact shape of ``ManifestIdentity.to_dict()``."""
    identity = {
        "pdf_content_hash": pdf_hash,
        "config_hash": _CONFIG,
        "extraction_map_hash": _MAP,
        "model_id": _MODEL,
        "schema_version": "1.0.0",
        "output_path": "outputs/paper_a.extracted.json",
    }
    identity.update(overrides)
    return identity


# ---------------------------------------------------------------------------
# Source node (3.1, 3.2, 3.4, 3.7)
# ---------------------------------------------------------------------------


def test_source_node_carries_fingerprints_format_label_and_models() -> None:
    node = source_node_from_identity(
        _identity(), source_label="paper_a.pdf", source_format="pdf", run_id=_RUN_ID
    )

    assert isinstance(node, SourceNode)
    assert node.node_id == f"src:{_PDF_A}"
    assert node.kind == "source"
    assert node.run_id == _RUN_ID
    assert node.document_fingerprint == _PDF_A
    assert node.source_format == "pdf"
    assert node.source_label == "paper_a.pdf"
    assert node.config_fingerprint == _CONFIG
    assert node.schema_fingerprint == _MAP
    assert node.model_ids == (_MODEL,)
    assert node.privacy == UNKNOWN_PRIVACY
    assert node.extensions == {
        "x-manifest-schema_version": "1.0.0",
        "x-manifest-output_path": "outputs/paper_a.extracted.json",
    }


def test_source_id_tracks_document_content_fingerprint() -> None:
    first = source_node_from_identity(_identity(_PDF_A), source_label="x", source_format="pdf", run_id=_RUN_ID)
    same = source_node_from_identity(_identity(_PDF_A), source_label="y", source_format="pdf", run_id=_RUN_ID)
    changed = source_node_from_identity(_identity(_PDF_B), source_label="x", source_format="pdf", run_id=_RUN_ID)

    assert first.node_id == same.node_id  # identity is content-derived, not label-derived
    assert first.node_id != changed.node_id


def test_metadata_free_mapping_still_yields_a_source_identity() -> None:
    fingerprints_only = {
        "pdf_content_hash": _PDF_A,
        "config_hash": _CONFIG,
        "extraction_map_hash": _MAP,
    }

    node = source_node_from_identity(fingerprints_only, source_label="", source_format="", run_id=_RUN_ID)

    assert node.node_id == f"src:{_PDF_A}"
    assert node.document_fingerprint == _PDF_A
    assert node.config_fingerprint == _CONFIG
    assert node.schema_fingerprint == _MAP
    assert node.source_label == ""
    assert node.source_format == ""
    assert node.model_ids == ()
    assert node.extensions == {}


def test_source_metadata_is_copied_verbatim_without_disclosure_evaluation() -> None:
    label = "  Patient-Smith_CONFIDENTIAL/ü.pdf  "
    fmt = "application/pdf; scanned"

    node = source_node_from_identity(_identity(), source_label=label, source_format=fmt, run_id=_RUN_ID)

    assert node.source_label is label
    assert node.source_format is fmt
    # No disclosure decision is made: the privacy carrier stays unknown.
    assert node.privacy == UNKNOWN_PRIVACY


def test_unconsumed_identity_keys_are_kept_as_manifest_extensions() -> None:
    node = source_node_from_identity(
        _identity(future_field="anything", Retry_Count=2, nested={"k": [1, 2]}),
        source_label="a",
        source_format="pdf",
        run_id=_RUN_ID,
    )

    assert node.node_id == f"src:{_PDF_A}"
    assert node.extensions == {
        "x-manifest-schema_version": "1.0.0",
        "x-manifest-output_path": "outputs/paper_a.extracted.json",
        "x-manifest-future_field": "anything",
        "x-manifest-Retry_Count": 2,
        "x-manifest-nested": {"k": [1, 2]},
    }


def test_consumed_identity_keys_never_become_extensions() -> None:
    node = source_node_from_identity(_identity(), source_label="a", source_format="pdf", run_id=_RUN_ID)

    for key in ("pdf_content_hash", "config_hash", "extraction_map_hash", "model_id"):
        assert f"x-manifest-{key}" not in node.extensions
    assert not any(key in node.extensions for key in ("pdf_content_hash", "config_hash"))


def test_output_path_is_the_run_relative_link_to_the_extraction_output() -> None:
    node = source_node_from_identity(
        _identity(output_path="paper_a.extracted.json"), source_label="a", source_format="pdf", run_id=_RUN_ID
    )

    assert node.extensions["x-manifest-output_path"] == "paper_a.extracted.json"


def test_keys_outside_the_extension_charset_go_to_one_fallback_extension() -> None:
    identity: dict[object, object] = dict(_identity())
    identity.update({"bad key": 1, "ü-field": "u", "": "empty", "-lead": "dash", 7: "int", "unnamespaced": "collides"})

    node = source_node_from_identity(identity, source_label="a", source_format="pdf", run_id=_RUN_ID)  # type: ignore[arg-type]

    assert node.extensions["x-manifest-unnamespaced"] == {
        "bad key": 1,
        "ü-field": "u",
        "": "empty",
        "-lead": "dash",
        "7": "int",
        "unnamespaced": "collides",
    }
    assert set(node.extensions) == {
        "x-manifest-schema_version",
        "x-manifest-output_path",
        "x-manifest-unnamespaced",
    }


def test_fallback_extension_is_absent_when_every_key_namespaces() -> None:
    node = source_node_from_identity(_identity(), source_label="a", source_format="pdf", run_id=_RUN_ID)

    assert "x-manifest-unnamespaced" not in node.extensions


def test_unnamespaced_keys_are_logged_once_at_warning() -> None:
    logger = identity_adapter.logger
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    logger.addHandler(handler)
    try:
        source_node_from_identity(
            _identity(**{"bad key": 1}), source_label="a", source_format="pdf", run_id=_RUN_ID
        )
        source_node_from_identity(_identity(), source_label="a", source_format="pdf", run_id=_RUN_ID)
    finally:
        logger.removeHandler(handler)

    warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "x-manifest-unnamespaced" in warnings[0] and "'bad key'" in warnings[0]


@pytest.mark.parametrize("missing", ["pdf_content_hash", "config_hash", "extraction_map_hash"])
def test_missing_fingerprint_raises_invalid_identifier(missing: str) -> None:
    identity = _identity()
    del identity[missing]

    with pytest.raises(InvalidIdentifierError, match=missing):
        source_node_from_identity(identity, source_label="a", source_format="pdf", run_id=_RUN_ID)


@pytest.mark.parametrize("bad", ["", None, 7])
def test_malformed_fingerprint_raises_invalid_identifier(bad: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        source_node_from_identity(
            _identity(config_hash=bad),  # type: ignore[arg-type]
            source_label="a",
            source_format="pdf",
            run_id=_RUN_ID,
        )


def test_non_string_metadata_and_run_id_are_rejected() -> None:
    with pytest.raises(TypeError):
        source_node_from_identity(_identity(), source_label=None, source_format="pdf", run_id=_RUN_ID)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        source_node_from_identity(_identity(), source_label="a", source_format=3, run_id=_RUN_ID)  # type: ignore[arg-type]
    with pytest.raises(InvalidIdentifierError):
        source_node_from_identity(_identity(), source_label="a", source_format="pdf", run_id="")
    with pytest.raises(TypeError):
        source_node_from_identity(["not", "a", "mapping"], source_label="a", source_format="pdf", run_id=_RUN_ID)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Run identity (3.5, 3.6)
# ---------------------------------------------------------------------------


def test_run_identity_records_config_schema_models_and_documents() -> None:
    identities = {
        "paper_a": _identity(_PDF_A),
        "paper_b": _identity(_PDF_B, output_path="outputs/paper_b.extracted.json"),
    }

    run = run_identity_from_identities(identities, created_at=_CREATED_AT)

    assert isinstance(run, RunIdentity)
    assert run.config_fingerprint == _CONFIG
    assert run.schema_fingerprint == _MAP
    assert run.model_ids == (_MODEL,)
    assert run.document_fingerprints == {"paper_a": _PDF_A, "paper_b": _PDF_B}
    assert run.created_at == _CREATED_AT
    assert run.run_id.startswith("run:")


def test_run_id_is_delegated_to_identity_service() -> None:
    run = run_identity_from_identities({"paper_a": _identity()}, created_at=_CREATED_AT)

    expected = derive_run_id(
        {
            "config_fingerprint": _CONFIG,
            "schema_fingerprint": _MAP,
            "model_ids": json.dumps([_MODEL]),
        }
    )
    assert run.run_id == expected


def test_run_id_is_deterministic_and_independent_of_order_and_clock() -> None:
    forward = {"paper_a": _identity(_PDF_A), "paper_b": _identity(_PDF_B)}
    backward = {"paper_b": _identity(_PDF_B), "paper_a": _identity(_PDF_A)}

    one = run_identity_from_identities(forward, created_at=_CREATED_AT)
    two = run_identity_from_identities(backward, created_at="2030-01-01T00:00:00+00:00")

    assert one.run_id == two.run_id
    assert one.document_fingerprints == two.document_fingerprints


def test_run_id_changes_with_configuration_schema_or_model() -> None:
    base = run_identity_from_identities({"p": _identity()}, created_at=_CREATED_AT).run_id
    for override in ({"config_hash": "e" * 64}, {"extraction_map_hash": "f" * 64}, {"model_id": "other-model"}):
        changed = run_identity_from_identities({"p": _identity(**override)}, created_at=_CREATED_AT).run_id
        assert changed != base, override


def test_model_ids_are_unique_and_sorted_across_documents() -> None:
    identities = {
        "p1": _identity(_PDF_A, model_id="model-z"),
        "p2": _identity(_PDF_B, model_id="model-a"),
        "p3": _identity("9" * 64, model_id="model-z"),
    }

    run = run_identity_from_identities(identities, created_at=_CREATED_AT)

    assert run.model_ids == ("model-a", "model-z")


def test_run_identity_from_metadata_free_mappings() -> None:
    identities = {
        "p": {"pdf_content_hash": _PDF_A, "config_hash": _CONFIG, "extraction_map_hash": _MAP},
    }

    run = run_identity_from_identities(identities, created_at=_CREATED_AT)

    assert run.model_ids == ()
    assert run.document_fingerprints == {"p": _PDF_A}


@pytest.mark.parametrize("key", ["config_hash", "extraction_map_hash"])
def test_conflicting_run_wide_fingerprints_are_rejected(key: str) -> None:
    identities = {"p1": _identity(_PDF_A), "p2": _identity(_PDF_B, **{key: "0" * 64})}

    with pytest.raises(InvalidIdentifierError, match=key):
        run_identity_from_identities(identities, created_at=_CREATED_AT)


def test_empty_identities_are_rejected() -> None:
    with pytest.raises(InvalidIdentifierError):
        run_identity_from_identities({}, created_at=_CREATED_AT)


def test_source_nodes_and_run_identity_share_one_fingerprint_set() -> None:
    identities = {"paper_a": _identity(_PDF_A), "paper_b": _identity(_PDF_B)}
    run = run_identity_from_identities(identities, created_at=_CREATED_AT)

    for key, identity in identities.items():
        node = source_node_from_identity(identity, source_label=key, source_format="pdf", run_id=run.run_id)
        assert node.run_id == run.run_id
        assert node.document_fingerprint == run.document_fingerprints[key]
        assert node.config_fingerprint == run.config_fingerprint
        assert node.schema_fingerprint == run.schema_fingerprint
        assert set(node.model_ids) <= set(run.model_ids)


def test_additional_model_ids_default_to_none() -> None:
    plain = run_identity_from_identities({"p": _identity()}, created_at=_CREATED_AT)
    explicit = run_identity_from_identities({"p": _identity()}, created_at=_CREATED_AT, additional_model_ids=())

    assert plain == explicit
    assert plain.model_ids == (_MODEL,)


def test_a_distinct_synthesis_model_is_part_of_the_run_identity() -> None:
    base = run_identity_from_identities({"p": _identity()}, created_at=_CREATED_AT)
    with_synthesis = run_identity_from_identities(
        {"p": _identity()}, created_at=_CREATED_AT, additional_model_ids=("gpt-5",)
    )

    assert with_synthesis.model_ids == ("gpt-5", _MODEL)
    assert with_synthesis.run_id != base.run_id
    assert with_synthesis.run_id == derive_run_id(
        {
            "config_fingerprint": _CONFIG,
            "schema_fingerprint": _MAP,
            "model_ids": json.dumps(["gpt-5", _MODEL]),
        }
    )


def test_additional_model_ids_are_deduplicated_with_document_models() -> None:
    base = run_identity_from_identities({"p": _identity()}, created_at=_CREATED_AT)
    same = run_identity_from_identities(
        {"p": _identity()}, created_at=_CREATED_AT, additional_model_ids=[_MODEL, _MODEL, ""]
    )

    assert same == base


def test_additional_model_ids_extend_a_metadata_free_run() -> None:
    identities = {"p": {"pdf_content_hash": _PDF_A, "config_hash": _CONFIG, "extraction_map_hash": _MAP}}

    run = run_identity_from_identities(identities, created_at=_CREATED_AT, additional_model_ids=("m-b", "m-a"))

    assert run.model_ids == ("m-a", "m-b")


@pytest.mark.parametrize("bad", ["gpt-5", b"gpt-5", None, 5, ("gpt-5", 3), [None]])
def test_malformed_additional_model_ids_raise_type_error(bad: object) -> None:
    with pytest.raises(TypeError):
        run_identity_from_identities(
            {"p": _identity()}, created_at=_CREATED_AT, additional_model_ids=bad  # type: ignore[arg-type]
        )


def test_additional_model_ids_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        run_identity_from_identities({"p": _identity()}, _CREATED_AT, ("gpt-5",))  # type: ignore[misc]


# ---------------------------------------------------------------------------
# No fingerprint computation (3.6) — mechanical checks
# ---------------------------------------------------------------------------

_HASHING_MODULES = {"hashlib", "hmac", "zlib", "binascii", "secrets", "uuid"}
_HASHING_NAMES = {
    "sha1",
    "sha224",
    "sha256",
    "sha384",
    "sha512",
    "sha3_256",
    "sha3_512",
    "md5",
    "blake2b",
    "blake2s",
    "new",
    "file_digest",
    "hexdigest",
    "digest",
    "crc32",
    "adler32",
}


def _module_tree() -> ast.Module:
    return ast.parse(Path(identity_adapter.__file__).read_text(encoding="utf-8"))


def test_module_imports_no_hashing_library() -> None:
    imported: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & _HASHING_MODULES


def test_module_calls_no_digest_function_and_no_builtin_hash() -> None:
    offenders = []
    for node in ast.walk(_module_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _HASHING_NAMES:
            offenders.append(func.attr)
        elif isinstance(func, ast.Name) and (func.id in _HASHING_NAMES or func.id == "hash"):
            offenders.append(func.id)

    assert offenders == []


def test_module_reads_no_files() -> None:
    names = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(_module_tree())
        if isinstance(node, (ast.Name, ast.Attribute))
    }

    assert not names & {"open", "read_bytes", "read_text", "Path"}


def test_runtime_digest_calls_are_limited_to_the_identity_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    real_sha256 = hashlib.sha256

    def counting_sha256(*args: object, **kwargs: object) -> object:
        calls.append(args)
        return real_sha256(*args, **kwargs)

    monkeypatch.setattr(hashlib, "sha256", counting_sha256)

    source_node_from_identity(_identity(), source_label="a", source_format="pdf", run_id=_RUN_ID)
    assert calls == []  # the source node adopts the fingerprint; nothing is hashed

    many = {f"p{i}": _identity(f"{i:064x}") for i in range(5)}
    run_identity_from_identities(many, created_at=_CREATED_AT)
    # Exactly one digest — the run-id derivation inside identity.py — however many documents.
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Re-export
# ---------------------------------------------------------------------------


def test_adapters_package_re_exports_identity_adapter() -> None:
    assert adapters.source_node_from_identity is source_node_from_identity
    assert adapters.run_identity_from_identities is run_identity_from_identities
    assert set(identity_adapter.__all__) == {"source_node_from_identity", "run_identity_from_identities"}
