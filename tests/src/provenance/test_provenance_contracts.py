"""Tests for ``provenance.contracts`` — the stage-contract table and stage ordering.

Covers the declaration-as-data of what each pipeline stage must emit and the
artifact scope it covers (2.1, 2.3), the deterministic stage ordering consumed
by the event-log projection (7.6), the rejection of an undeclared stage name
with the dedicated ``UnknownStageError`` (the lookup the recorder uses at
record time), the documented extension protocol for downstream specs, and the
module's position in the provenance dependency order.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from types import MappingProxyType
from typing import get_args

import pytest

from provenance import contracts
from provenance.contracts import (
    STAGE_CONTRACTS,
    STAGE_ORDER,
    UnknownStageError,
    require_declared_stage,
)
from provenance.models import NodeKind, StageContract

_SCOPES = {"run", "document", "claim"}

# The stages provenance-core itself declares, in pipeline order. Downstream
# specs insert their own rows between these; they never remove or reorder them.
_CORE_STAGES_IN_ORDER = (
    "document_extraction",
    "quality_control",
    "evidence_index",
    "metadata_prefill",
    "extraction_chunk",
    "synthesis",
    "chain_validation",
)


# ---------------------------------------------------------------------------
# STAGE_CONTRACTS — declaration table (2.1, 2.3)
# ---------------------------------------------------------------------------


def test_stage_contracts_is_a_non_empty_read_only_mapping():
    assert isinstance(STAGE_CONTRACTS, MappingProxyType)
    assert len(STAGE_CONTRACTS) > 0
    with pytest.raises(TypeError):
        STAGE_CONTRACTS["rogue_stage"] = StageContract(  # type: ignore[index]
            stage="rogue_stage", required_record_kinds=("claim",), scope="claim"
        )


def test_every_entry_is_a_stage_contract_keyed_by_its_own_stage_name():
    for name, contract in STAGE_CONTRACTS.items():
        assert isinstance(name, str) and name
        assert isinstance(contract, StageContract)
        assert contract.stage == name


def test_every_declared_stage_has_a_non_empty_required_record_kind_set():
    for name, contract in STAGE_CONTRACTS.items():
        kinds = contract.required_record_kinds
        assert isinstance(kinds, tuple), name
        assert len(kinds) > 0, f"stage {name!r} declares no required record kind"
        assert len(set(kinds)) == len(kinds), f"stage {name!r} repeats a record kind"


def test_required_record_kinds_are_drawn_from_the_closed_node_kind_vocabulary():
    node_kinds = set(get_args(NodeKind))
    for name, contract in STAGE_CONTRACTS.items():
        assert set(contract.required_record_kinds) <= node_kinds, name


def test_every_scope_is_run_document_or_claim():
    for name, contract in STAGE_CONTRACTS.items():
        assert contract.scope in _SCOPES, name


def test_every_node_kind_has_at_least_one_responsible_stage():
    # Absence is only detectable against a declaration: a record kind no stage
    # declares could go missing without any stage being reported responsible.
    declared = {k for c in STAGE_CONTRACTS.values() for k in c.required_record_kinds}
    assert declared == set(get_args(NodeKind))


def test_core_stage_rows_match_the_pipeline_emission_points():
    expected = {
        "document_extraction": (("source",), "document"),
        "quality_control": (("derivation",), "document"),
        "evidence_index": (("evidence",), "document"),
        "metadata_prefill": (("claim", "derivation"), "claim"),
        "extraction_chunk": (("claim", "derivation"), "claim"),
        "synthesis": (("claim", "derivation"), "claim"),
        "chain_validation": (("validation",), "run"),
    }
    for name, (kinds, scope) in expected.items():
        contract = STAGE_CONTRACTS[name]
        assert contract.required_record_kinds == kinds, name
        assert contract.scope == scope, name


# ---------------------------------------------------------------------------
# STAGE_ORDER — deterministic ordering for the projection (7.6)
# ---------------------------------------------------------------------------


def test_stage_order_is_a_tuple_of_strings():
    assert isinstance(STAGE_ORDER, tuple)
    assert all(isinstance(s, str) and s for s in STAGE_ORDER)


def test_every_declared_stage_appears_exactly_once_in_the_ordering():
    counts = Counter(STAGE_ORDER)
    for name in STAGE_CONTRACTS:
        assert counts[name] == 1, f"stage {name!r} appears {counts[name]} times"


def test_ordering_and_contract_table_cover_the_same_stages():
    assert set(STAGE_ORDER) == set(STAGE_CONTRACTS)
    assert len(STAGE_ORDER) == len(STAGE_CONTRACTS)


def test_core_stages_keep_their_relative_pipeline_order():
    positions = [STAGE_ORDER.index(s) for s in _CORE_STAGES_IN_ORDER]
    assert positions == sorted(positions)


def test_ordering_gives_a_total_deterministic_rank():
    ranks = {s: STAGE_ORDER.index(s) for s in STAGE_ORDER}
    assert sorted(ranks.values()) == list(range(len(STAGE_ORDER)))


# ---------------------------------------------------------------------------
# Unknown-stage rejection (consumed by ProvenanceRecorder at record time)
# ---------------------------------------------------------------------------


def test_unknown_stage_error_is_a_value_error():
    assert issubclass(UnknownStageError, ValueError)


@pytest.mark.parametrize("stage", list(_CORE_STAGES_IN_ORDER))
def test_require_declared_stage_returns_the_contract_for_a_declared_stage(stage):
    assert require_declared_stage(stage) is STAGE_CONTRACTS[stage]


@pytest.mark.parametrize(
    "stage",
    ["not_a_stage", "", " synthesis", "synthesis ", "Synthesis", "zz_undeclared_probe_stage"],
)
def test_stage_absent_from_both_tables_is_rejected_not_silently_accepted(stage):
    assert stage not in STAGE_CONTRACTS and stage not in STAGE_ORDER
    with pytest.raises(UnknownStageError) as excinfo:
        require_declared_stage(stage)
    assert repr(stage) in str(excinfo.value)


@pytest.mark.parametrize("stage", [None, 3, ("synthesis",)])
def test_non_string_stage_is_rejected(stage):
    with pytest.raises(UnknownStageError):
        require_declared_stage(stage)  # type: ignore[arg-type]


def test_stage_declared_in_only_one_table_is_rejected():
    only_contract = StageContract(
        stage="half_declared", required_record_kinds=("derivation",), scope="document"
    )
    contracts_table = {**STAGE_CONTRACTS, "half_declared": only_contract}
    with pytest.raises(UnknownStageError, match="STAGE_ORDER"):
        require_declared_stage("half_declared", contracts=contracts_table, order=STAGE_ORDER)

    order = (*STAGE_ORDER, "order_only")
    with pytest.raises(UnknownStageError, match="STAGE_CONTRACTS"):
        require_declared_stage("order_only", contracts=STAGE_CONTRACTS, order=order)


def test_extended_tables_accept_the_new_stage():
    # Mirrors how a downstream spec extends both tables in place.
    new = StageContract(
        stage="routing_locator", required_record_kinds=("derivation",), scope="document"
    )
    contracts_table = {**STAGE_CONTRACTS, "routing_locator": new}
    order = (*STAGE_ORDER, "routing_locator")
    assert (
        require_declared_stage("routing_locator", contracts=contracts_table, order=order)
        is new
    )


# ---------------------------------------------------------------------------
# Documentation and dependency order
# ---------------------------------------------------------------------------


def test_module_documents_the_downstream_extension_protocol():
    doc = contracts.__doc__ or ""
    for phrase in (
        "STAGE_CONTRACTS",
        "STAGE_ORDER",
        "ProvenanceRecorder",
        "UnknownStageError",
        "in place",
        "drift",
    ):
        assert phrase in doc, phrase


def test_module_imports_only_stdlib_and_models():
    tree = ast.parse(Path(contracts.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    provenance_imports = {m for m in imported if m.split(".")[0] == "provenance"}
    assert provenance_imports <= {"provenance.models"}
    forbidden = {"pipeline", "quality_control", "agents", "pdf_extractor"}
    assert not {m.split(".")[0] for m in imported} & forbidden
