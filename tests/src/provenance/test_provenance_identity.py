"""Tests for ``provenance.identity`` — the sole provenance identifier definition.

Covers source identity from a supplied fingerprint (3.1, 3.3), run identity
digests (3.6), evidence ID adoption and scoping (4.2, 4.7, 4.8) with total
round-trip parsing, claim and validation IDs, content-addressed derivation-step
IDs invariant under input/output permutation (6.2, 3.3), the dedicated
``InvalidIdentifierError``, and — by AST scan — that the module computes no
document, configuration or extraction-map fingerprint (3.6).

Example-based only; the Hypothesis property tests are added by task 7.2.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from provenance import identity
from provenance.identity import (
    EVIDENCE_SCOPE_SEPARATOR,
    InvalidIdentifierError,
    derive_claim_id,
    derive_derivation_id,
    derive_run_id,
    derive_source_id,
    derive_validation_id,
    derive_value_digest,
    format_evidence_id,
    parse_evidence_id,
)

_FP_A = "a" * 64
_FP_B = "b" * 64
_SRC_A = f"src:{_FP_A}"
_SRC_B = f"src:{_FP_B}"
_RUN_FIELDS = {
    "config_hash": "c" * 64,
    "extraction_map_hash": "e" * 64,
    "model_id": "gpt-test",
    "schema_version": "1",
}


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


def test_invalid_identifier_error_is_a_value_error() -> None:
    assert issubclass(InvalidIdentifierError, ValueError)


def test_scope_separator_is_hash() -> None:
    assert EVIDENCE_SCOPE_SEPARATOR == "#"


# ---------------------------------------------------------------------------
# Source identity (3.1, 3.3, 3.4)
# ---------------------------------------------------------------------------


def test_source_id_is_prefixed_fingerprint_verbatim() -> None:
    assert derive_source_id(_FP_A) == f"src:{_FP_A}"


def test_source_id_is_deterministic() -> None:
    assert derive_source_id(_FP_A) == derive_source_id(_FP_A)


def test_changed_content_fingerprint_yields_different_source_id() -> None:
    assert derive_source_id(_FP_A) != derive_source_id(_FP_B)


def test_source_id_does_not_rehash_the_fingerprint() -> None:
    # The fingerprint is consumed, never recomputed (3.6).
    assert hashlib.sha256(_FP_A.encode()).hexdigest() not in derive_source_id(_FP_A)


@pytest.mark.parametrize("bad", ["", "   ", "abc#def", "abc def", None, 123, b"abc"])
def test_source_id_rejects_malformed_fingerprint(bad: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        derive_source_id(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Run identity (3.5, 3.6)
# ---------------------------------------------------------------------------


def test_run_id_format_is_run_prefix_and_16_hex() -> None:
    run_id = derive_run_id(_RUN_FIELDS)
    assert run_id.startswith("run:")
    digest = run_id.removeprefix("run:")
    assert len(digest) == 16
    int(digest, 16)  # hex


def test_run_id_is_digest_of_canonical_field_serialization() -> None:
    canonical = json.dumps(
        sorted(_RUN_FIELDS.items()), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert derive_run_id(_RUN_FIELDS) == "run:" + hashlib.sha256(canonical).hexdigest()[:16]


def test_run_id_is_independent_of_mapping_insertion_order() -> None:
    reversed_fields = dict(reversed(list(_RUN_FIELDS.items())))
    assert derive_run_id(reversed_fields) == derive_run_id(_RUN_FIELDS)


def test_run_id_changes_when_any_field_changes() -> None:
    base = derive_run_id(_RUN_FIELDS)
    for key in _RUN_FIELDS:
        changed = {**_RUN_FIELDS, key: _RUN_FIELDS[key] + "x"}
        assert derive_run_id(changed) != base, key


def test_run_id_distinguishes_key_value_boundaries() -> None:
    assert derive_run_id({"ab": "c"}) != derive_run_id({"a": "bc"})


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"config_hash": 1},
        {1: "x"},
        {"config_hash": None},
        [("config_hash", "x")],
        "config_hash",
        None,
    ],
)
def test_run_id_rejects_malformed_identity_fields(bad: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        derive_run_id(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Evidence identity (4.2, 4.7, 4.8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("local_id", ["S000001", "F000012", "T000003", "x", "S 1", "loc:1/2"])
def test_evidence_id_scopes_local_id_verbatim(local_id: str) -> None:
    node_id = format_evidence_id(_SRC_A, local_id)
    assert node_id == f"{_SRC_A}#{local_id}"
    assert node_id.endswith(local_id)


@pytest.mark.parametrize("local_id", ["S000001", "F000012", "T000003", "x", "S 1", "loc:1/2", "é漢"])
@pytest.mark.parametrize("source_id", [_SRC_A, _SRC_B, "src:short", "src:a:b"])
def test_evidence_id_round_trip_is_total(source_id: str, local_id: str) -> None:
    assert parse_evidence_id(format_evidence_id(source_id, local_id)) == (source_id, local_id)


def test_same_local_id_in_two_sources_does_not_collide() -> None:
    assert format_evidence_id(_SRC_A, "S000001") != format_evidence_id(_SRC_B, "S000001")


def test_evidence_id_is_stable_for_the_run_context() -> None:
    assert format_evidence_id(_SRC_A, "S000001") == format_evidence_id(_SRC_A, "S000001")


def test_local_id_containing_scope_separator_is_rejected() -> None:
    with pytest.raises(InvalidIdentifierError):
        format_evidence_id(_SRC_A, "S00#0001")


@pytest.mark.parametrize("bad_local", ["", None, 7])
def test_empty_or_non_string_local_id_is_rejected(bad_local: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        format_evidence_id(_SRC_A, bad_local)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_source", ["", "src:", "abc", "run:abc", "claim:src:a#f1", "src:a#b", None])
def test_malformed_source_id_is_rejected_when_formatting(bad_source: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        format_evidence_id(bad_source, "S000001")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "src:abc",  # no separator
        "src:abc#",  # empty local id
        "#S000001",  # empty source id
        "src:a#b#c",  # local id would contain the separator
        "abc#S000001",  # scope is not a source id
        "claim:src:abc#f1",
        None,
        42,
    ],
)
def test_parse_rejects_malformed_evidence_id(bad: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        parse_evidence_id(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Claim identity
# ---------------------------------------------------------------------------


def test_claim_id_without_revision_has_no_suffix() -> None:
    assert derive_claim_id(_SRC_A, 7) == f"claim:{_SRC_A}#f7"
    assert derive_claim_id(_SRC_A, 7, 0) == f"claim:{_SRC_A}#f7"


def test_claim_id_revision_adds_suffix() -> None:
    assert derive_claim_id(_SRC_A, 7, 2) == f"claim:{_SRC_A}#f7@r2"


def test_claim_ids_are_distinct_across_source_field_and_revision() -> None:
    ids = {
        derive_claim_id(source, field, rev)
        for source in (_SRC_A, _SRC_B)
        for field in (1, 2, 62)
        for rev in (0, 1, 2)
    }
    assert len(ids) == 2 * 3 * 3


@pytest.mark.parametrize(
    ("source_id", "field_index", "revision"),
    [
        ("abc", 1, 0),
        ("", 1, 0),
        (_SRC_A, -1, 0),
        (_SRC_A, True, 0),
        (_SRC_A, 1.0, 0),
        (_SRC_A, "1", 0),
        (_SRC_A, 1, -1),
        (_SRC_A, 1, True),
        (_SRC_A, 1, "1"),
    ],
)
def test_claim_id_rejects_malformed_input(source_id: object, field_index: object, revision: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        derive_claim_id(source_id, field_index, revision)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Validation identity
# ---------------------------------------------------------------------------


def test_validation_id_format() -> None:
    run_id = derive_run_id(_RUN_FIELDS)
    assert derive_validation_id(run_id, "chain") == f"val:{run_id}:chain"


@pytest.mark.parametrize(
    ("run_id", "check_name"),
    [("", "chain"), ("abc", "chain"), ("run:", "chain"), ("run:abc", ""), ("run:abc", None), (None, "chain")],
)
def test_validation_id_rejects_malformed_input(run_id: object, check_name: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        derive_validation_id(run_id, check_name)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Derivation-step identity (6.2, 3.3)
# ---------------------------------------------------------------------------

_INPUTS = (f"{_SRC_A}#S000001", f"{_SRC_A}#S000002", f"{_SRC_A}#T000001")
_OUTPUTS = (f"claim:{_SRC_A}#f3", f"claim:{_SRC_A}#f4")


def test_derivation_id_format_is_drv_prefix_and_16_hex() -> None:
    drv = derive_derivation_id("extraction", "extract", _INPUTS, _OUTPUTS)
    assert drv.startswith("drv:")
    digest = drv.removeprefix("drv:")
    assert len(digest) == 16
    int(digest, 16)


def test_derivation_id_is_invariant_under_input_and_output_permutation() -> None:
    expected = derive_derivation_id("extraction", "extract", _INPUTS, _OUTPUTS)
    for inputs in itertools.permutations(_INPUTS):
        for outputs in itertools.permutations(_OUTPUTS):
            assert derive_derivation_id("extraction", "extract", list(inputs), outputs) == expected


@pytest.mark.parametrize(
    "variant",
    [
        ("synthesis", "extract", _INPUTS, _OUTPUTS),
        ("extraction", "merge", _INPUTS, _OUTPUTS),
        ("extraction", "extract", _INPUTS[:2], _OUTPUTS),
        ("extraction", "extract", _INPUTS, _OUTPUTS[:1]),
        # moving an ID from inputs to outputs is a different step
        ("extraction", "extract", _INPUTS[:2], (*_OUTPUTS, _INPUTS[2])),
    ],
)
def test_derivation_id_changes_with_stage_kind_inputs_or_outputs(variant: tuple) -> None:
    base = derive_derivation_id("extraction", "extract", _INPUTS, _OUTPUTS)
    assert derive_derivation_id(*variant) != base


def test_derivation_id_allows_empty_side() -> None:
    # A step with no inputs or no outputs is identifiable; whether it is
    # consistent is the chain validator's call, not the identity service's.
    assert derive_derivation_id("qc", "remove", _INPUTS, ()).startswith("drv:")
    assert derive_derivation_id("qc", "remove", (), ()).startswith("drv:")


def test_derivation_id_is_stable_across_processes() -> None:
    expected = derive_derivation_id("extraction", "extract", _INPUTS, _OUTPUTS)
    src_dir = Path(identity.__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from provenance.identity import derive_derivation_id as d;"
        f"print(d('extraction', 'extract', {list(reversed(_INPUTS))!r}, {list(_OUTPUTS)!r}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(src_dir)],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": "12345"},
    )
    assert out.stdout.strip() == expected


@pytest.mark.parametrize(
    ("stage", "kind", "inputs", "outputs"),
    [
        ("", "extract", _INPUTS, _OUTPUTS),
        ("extraction", "", _INPUTS, _OUTPUTS),
        (None, "extract", _INPUTS, _OUTPUTS),
        ("extraction", "extract", "src:a#S1", _OUTPUTS),  # bare string, not a sequence of IDs
        ("extraction", "extract", _INPUTS, "claim:x"),
        ("extraction", "extract", ("",), _OUTPUTS),
        ("extraction", "extract", (1,), _OUTPUTS),
        ("extraction", "extract", None, _OUTPUTS),
    ],
)
def test_derivation_id_rejects_malformed_input(stage: object, kind: object, inputs: object, outputs: object) -> None:
    with pytest.raises(InvalidIdentifierError):
        derive_derivation_id(stage, kind, inputs, outputs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Purity and fingerprint boundary (3.6)
# ---------------------------------------------------------------------------


def _module_tree() -> ast.Module:
    return ast.parse(Path(identity.__file__).read_text(encoding="utf-8"))


def test_module_hashes_only_with_sha256() -> None:
    tree = _module_tree()
    hash_attrs = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "hashlib"
    }
    assert hash_attrs == {"sha256"}


def test_module_performs_no_io_clock_or_randomness() -> None:
    tree = _module_tree()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"os", "pathlib", "io", "time", "datetime", "random", "uuid", "secrets", "socket", "pipeline", "quality_control", "agents", "pdf_extractor"}
    assert imported.isdisjoint(forbidden), imported & forbidden
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in calls


def test_module_never_hashes_a_file_or_config() -> None:
    # Fingerprints of documents, configs and extraction maps are consumed from
    # the caller (ManifestIdentity), never computed here (3.6).
    source = Path(identity.__file__).read_text(encoding="utf-8")
    for token in ("read_bytes", "read_text", "pdf_path", "extraction_map.json", "config.yaml"):
        assert token not in source


def test_unencodable_identifier_material_raises_invalid_identifier_error():
    """A lone surrogate must surface as InvalidIdentifierError, not UnicodeEncodeError."""
    with pytest.raises(identity.InvalidIdentifierError):
        identity.derive_run_id({"document_fingerprint": "\ud800"})
    with pytest.raises(identity.InvalidIdentifierError):
        identity.derive_derivation_id("reconcile", "merge", ["\ud800"], ["out"])


# ---------------------------------------------------------------------------
# Claim value digest
# ---------------------------------------------------------------------------


def test_value_digest_is_16_hex_and_deterministic() -> None:
    digest = derive_value_digest("120 patients")
    assert len(digest) == 16 and all(ch in "0123456789abcdef" for ch in digest)
    assert derive_value_digest("120 patients") == digest


def test_value_digest_is_canonical_domain_separated_sha256() -> None:
    material = json.dumps(["claim-value", "nr"], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert derive_value_digest("nr") == hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


@pytest.mark.parametrize(
    "a, b",
    [
        ("120 patients", "121 patients"),
        ("1", 1),
        (None, "nr"),
        ("", None),
        ([1, 2], [2, 1]),
        ({"a": 1}, {"a": 2}),
    ],
)
def test_value_digest_is_value_sensitive(a: object, b: object) -> None:
    assert derive_value_digest(a) != derive_value_digest(b)


def test_value_digest_is_insensitive_to_mapping_key_order() -> None:
    assert derive_value_digest({"a": 1, "b": {"x": 1, "y": 2}}) == derive_value_digest(
        {"b": {"y": 2, "x": 1}, "a": 1}
    )


class _Unserializable:
    def __repr__(self) -> str:
        return "Unserializable()"


class _BrokenRepr:
    def __repr__(self) -> str:
        raise RuntimeError("no repr")


def _cycle() -> list:
    loop: list = []
    loop.append(loop)
    return loop


@pytest.mark.parametrize(
    "value",
    [_Unserializable(), _BrokenRepr(), {1: "a", "b": 2}, _cycle(), "\ud800", b"bytes", {"k": {1, 2}}, float("nan")],
    ids=["object", "broken-repr", "mixed-keys", "cycle", "lone-surrogate", "bytes", "nested-set", "nan"],
)
def test_value_digest_never_raises_on_non_json_values(value: object) -> None:
    digest = derive_value_digest(value)
    assert len(digest) == 16
    assert derive_value_digest(value) == digest


def test_value_digest_of_non_json_value_is_stable_when_its_repr_is() -> None:
    assert derive_value_digest(_Unserializable()) == derive_value_digest(_Unserializable())
    assert derive_value_digest(_Unserializable()) != derive_value_digest("Unserializable()")
