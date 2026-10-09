"""Tests for ``provenance.privacy_carrier`` — the inert privacy carrier (Requirement 9).

Covers verbatim storage (9.2), the unknown state on absent input (9.3), the
supplier/timestamp exposure (9.5), structural-only rejection (9.6), the single
carrier structure (9.1), and — mechanically, by AST scan — that the module
never compares against or normalizes a label or decision value (9.4).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from collections import OrderedDict
from pathlib import Path
from types import MappingProxyType

import pytest

from provenance import privacy_carrier
from provenance.privacy_carrier import (
    UNKNOWN_PRIVACY,
    PrivacyCarrier,
    attach_privacy,
    check_carrier_conformance,
)

_SUPPLIER = "privacy-core.labeler"
_WHEN = "2026-10-08T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Carrier structure (9.1, 9.5)
# ---------------------------------------------------------------------------


def test_carrier_has_exactly_the_declared_fields() -> None:
    names = [f.name for f in dataclasses.fields(PrivacyCarrier)]
    assert names == [
        "state",
        "label",
        "decision",
        "supplied_by",
        "supplied_at",
        "rejection_reason",
    ]


def test_carrier_is_frozen_and_hashable() -> None:
    carrier = PrivacyCarrier(state="labeled", label="L", decision="D")
    with pytest.raises(dataclasses.FrozenInstanceError):
        carrier.label = "other"  # type: ignore[misc]
    assert hash(carrier) == hash(PrivacyCarrier(state="labeled", label="L", decision="D"))


def test_unknown_constant_is_the_all_default_carrier() -> None:
    assert UNKNOWN_PRIVACY == PrivacyCarrier()
    assert UNKNOWN_PRIVACY.state == "unknown"
    assert UNKNOWN_PRIVACY.label is None
    assert UNKNOWN_PRIVACY.decision is None
    assert UNKNOWN_PRIVACY.supplied_by is None
    assert UNKNOWN_PRIVACY.supplied_at is None
    assert UNKNOWN_PRIVACY.rejection_reason is None


def test_module_declares_exactly_one_dataclass() -> None:
    declared = [
        obj
        for _, obj in inspect.getmembers(privacy_carrier, inspect.isclass)
        if dataclasses.is_dataclass(obj) and obj.__module__ == privacy_carrier.__name__
    ]
    assert declared == [PrivacyCarrier]


# ---------------------------------------------------------------------------
# attach_privacy — absent input (9.3)
# ---------------------------------------------------------------------------


def test_absent_input_returns_the_unknown_constant() -> None:
    result = attach_privacy(None, supplied_by=_SUPPLIER, supplied_at=_WHEN)
    assert result is UNKNOWN_PRIVACY
    assert result.state == "unknown"
    assert result.label is None
    assert result.decision is None


# ---------------------------------------------------------------------------
# attach_privacy — verbatim storage (9.2, 9.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, decision",
    [
        ("PHI", "withhold"),
        ("  Mixed Case  With Spaces ", "Release-With-Redaction"),
        ("ünïcödé-label", "décision"),
        ("", ""),
        ("restricted", None),
        ("x" * 4096, "y" * 4096),
    ],
)
def test_conforming_input_is_stored_verbatim(label: str, decision: str | None) -> None:
    supplied: dict[str, object] = {"label": label}
    if decision is not None:
        supplied["decision"] = decision
    result = attach_privacy(supplied, supplied_by=_SUPPLIER, supplied_at=_WHEN)

    assert result.state == "labeled"
    assert result.label == label
    assert result.label is label
    assert result.label.encode("utf-8") == label.encode("utf-8")
    assert result.decision == decision
    if decision is not None:
        assert result.decision is decision
    assert result.rejection_reason is None


def test_explicit_none_decision_is_accepted_as_absent() -> None:
    result = attach_privacy(
        {"label": "L", "decision": None}, supplied_by=_SUPPLIER, supplied_at=_WHEN
    )
    assert result.state == "labeled"
    assert result.decision is None


def test_supplier_and_timestamp_are_exposed_verbatim() -> None:
    result = attach_privacy({"label": "L"}, supplied_by=_SUPPLIER, supplied_at=_WHEN)
    assert result.supplied_by is _SUPPLIER
    assert result.supplied_at is _WHEN


@pytest.mark.parametrize(
    "mapping_factory",
    [dict, OrderedDict, lambda d: MappingProxyType(dict(d))],
    ids=["dict", "OrderedDict", "MappingProxyType"],
)
def test_any_mapping_type_is_accepted(mapping_factory) -> None:
    supplied = mapping_factory({"label": "L", "decision": "D"})
    result = attach_privacy(supplied, supplied_by=_SUPPLIER, supplied_at=_WHEN)
    assert (result.state, result.label, result.decision) == ("labeled", "L", "D")


def test_attach_does_not_mutate_the_input() -> None:
    supplied = {"label": "L", "decision": "D"}
    snapshot = dict(supplied)
    attach_privacy(supplied, supplied_by=_SUPPLIER, supplied_at=_WHEN)
    assert supplied == snapshot


def test_distinct_label_values_never_change_the_resulting_state() -> None:
    """No label or decision value is special: every string yields ``labeled``."""
    probes = ["public", "private", "PHI", "phi", "none", "None", "unknown", "rejected", "0"]
    for value in probes:
        result = attach_privacy(
            {"label": value, "decision": value}, supplied_by=_SUPPLIER, supplied_at=_WHEN
        )
        assert result.state == "labeled", value
        assert result.label is value
        assert result.decision is value


# ---------------------------------------------------------------------------
# attach_privacy / check_carrier_conformance — structural rejection (9.6)
# ---------------------------------------------------------------------------

_MALFORMED = [
    pytest.param({}, id="empty-mapping"),
    pytest.param({"decision": "D"}, id="decision-without-label"),
    pytest.param({"label": None}, id="label-none"),
    pytest.param({"label": 7}, id="label-int"),
    pytest.param({"label": b"L"}, id="label-bytes"),
    pytest.param({"label": ["L"]}, id="label-list"),
    pytest.param({"label": "L", "decision": 1}, id="decision-int"),
    pytest.param({"label": "L", "decision": {"x": 1}}, id="decision-mapping"),
    pytest.param({"label": "L", "sensitivity": "high"}, id="unknown-key"),
    pytest.param({"label": "L", 3: "x"}, id="non-string-key"),
]


@pytest.mark.parametrize("supplied", _MALFORMED)
def test_conformance_check_rejects_malformed_input_with_reason(supplied) -> None:
    ok, reason = check_carrier_conformance(supplied)
    assert ok is False
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("supplied", _MALFORMED)
def test_malformed_input_yields_rejected_carrier_with_label_unset(supplied) -> None:
    result = attach_privacy(supplied, supplied_by=_SUPPLIER, supplied_at=_WHEN)
    assert result.state == "rejected"
    assert result.label is None
    assert result.decision is None
    assert isinstance(result.rejection_reason, str) and result.rejection_reason
    # 9.5: who supplied the malformed value, and when, is still exposed.
    assert result.supplied_by == _SUPPLIER
    assert result.supplied_at == _WHEN


def test_rejection_reason_matches_the_conformance_check() -> None:
    supplied = {"label": 7}
    _, reason = check_carrier_conformance(supplied)
    result = attach_privacy(supplied, supplied_by=_SUPPLIER, supplied_at=_WHEN)
    assert result.rejection_reason == reason


@pytest.mark.parametrize(
    "supplied",
    [pytest.param("PHI", id="str"), pytest.param(["label"], id="list"), pytest.param(3, id="int")],
)
def test_non_mapping_input_is_rejected_not_raised(supplied) -> None:
    ok, reason = check_carrier_conformance(supplied)  # type: ignore[arg-type]
    assert ok is False and reason
    result = attach_privacy(supplied, supplied_by=_SUPPLIER, supplied_at=_WHEN)  # type: ignore[arg-type]
    assert result.state == "rejected"
    assert result.label is None


@pytest.mark.parametrize(
    "supplied",
    [{"label": "L"}, {"label": "L", "decision": "D"}, {"label": "L", "decision": None}],
)
def test_conforming_input_passes_the_check(supplied) -> None:
    assert check_carrier_conformance(supplied) == (True, None)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"supplied_by": None, "supplied_at": _WHEN}, id="supplied_by-none"),
        pytest.param({"supplied_by": _SUPPLIER, "supplied_at": 1700000000}, id="supplied_at-int"),
    ],
)
def test_mistyped_supplier_metadata_is_rejected(kwargs) -> None:
    result = attach_privacy({"label": "L"}, **kwargs)
    assert result.state == "rejected"
    assert result.label is None
    assert result.rejection_reason


# ---------------------------------------------------------------------------
# Inertness guard (9.4) — mechanical AST scan of the module source
# ---------------------------------------------------------------------------

_VALUE_NAMES = frozenset({"label", "decision"})
_NORMALIZING_METHODS = frozenset(
    {
        "lower", "upper", "casefold", "strip", "lstrip", "rstrip", "title",
        "capitalize", "swapcase", "replace", "startswith", "endswith",
        "removeprefix", "removesuffix", "normalize", "split", "find", "index",
    }
)


def _is_value_ref(node: ast.AST) -> bool:
    """True when *node* reads a label/decision value (name, attribute, key or .get)."""
    if isinstance(node, ast.Name):
        return node.id in _VALUE_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _VALUE_NAMES
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value in _VALUE_NAMES
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "get" and node.args:
            key = node.args[0]
            return isinstance(key, ast.Constant) and key.value in _VALUE_NAMES
    return False


def _contains_str_literal(node: ast.AST) -> bool:
    return any(
        isinstance(sub, ast.Constant) and isinstance(sub.value, str) for sub in ast.walk(node)
    )


def _inertness_violations(source: str) -> list[str]:
    """Return every place *source* interprets a label/decision value.

    Forbidden: any comparison with a string literal operand; any comparison of
    a label/decision value other than ``is None`` / ``is not None``; any
    ``match`` statement; any string-normalizing method called on a value.
    """
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Match):
            violations.append(f"line {node.lineno}: match statement")
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_contains_str_literal(op) for op in operands):
                violations.append(f"line {node.lineno}: comparison against a string literal")
            if any(_is_value_ref(op) for op in operands):
                is_none_check = all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops) and any(
                    isinstance(op, ast.Constant) and op.value is None for op in operands
                )
                if not is_none_check:
                    violations.append(f"line {node.lineno}: comparison on a label/decision value")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _NORMALIZING_METHODS and _is_value_ref(node.func.value):
                violations.append(f"line {node.lineno}: normalizing a label/decision value")
    return violations


@pytest.mark.parametrize(
    "probe",
    [
        'if label == "PHI":\n    pass\n',
        'if carrier.decision != "release":\n    pass\n',
        'ok = carrier_input["label"] in ("a", "b")\n',
        'ok = carrier_input.get("decision") == other\n',
        "ok = label < other\n",
        'x = label.lower()\n',
        'match label:\n    case "PHI":\n        pass\n',
        'ok = value == "restricted"\n',
    ],
)
def test_inertness_guard_detects_interpretation(probe: str) -> None:
    """The guard is not vacuous: each interpreting construct is flagged."""
    assert _inertness_violations(probe)


@pytest.mark.parametrize(
    "probe",
    [
        "ok = label is None\n",
        "ok = carrier.decision is not None\n",
        "ok = isinstance(label, str)\n",
        "extra = set(carrier_input) - KEYS\n",
    ],
)
def test_inertness_guard_allows_structural_checks(probe: str) -> None:
    assert _inertness_violations(probe) == []


def test_privacy_carrier_module_contains_no_label_interpretation() -> None:
    source = Path(privacy_carrier.__file__).read_text(encoding="utf-8")
    assert _inertness_violations(source) == []
