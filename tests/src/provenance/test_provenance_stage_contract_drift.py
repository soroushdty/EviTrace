"""Regression guard: the stage-contract tables cannot drift from what the recorder accepts (task 7.3).

``provenance.contracts`` declares the pipeline stages twice -- ``STAGE_CONTRACTS`` (what each stage
must emit; completeness is measured against it, Requirement 2.3) and ``STAGE_ORDER`` (the total
order the event-log projection sorts on, Requirement 7.6) -- and ``ProvenanceRecorder`` accepts a
stage only if :func:`~provenance.contracts.require_declared_stage` finds it in *both*.  A stage
added to one table, a stage renamed in one place, or a pipeline call site that records against a
stage nobody declared would silently produce a graph whose completeness or ordering is wrong.
This module makes each such drift fail loudly:

1. **Table consistency** -- ``set(STAGE_CONTRACTS) == set(STAGE_ORDER) == {stages the recorder
   accepts}``.  The accepted set is *probed* from the real recorder (``mark_stage_complete``,
   ``mark_stage_failed``, ``record_derivation``; enabled and disabled), never copied from a
   literal; the three entry points and both modes must agree.  Each stage appears once in the
   ordering and has a non-empty required-kind set.  The pure check is
   :func:`find_stage_drift`, tested against doctored tables (a stage declared in only one table,
   a stage accepted but undeclared, a stage declared but rejected, a duplicate ordering entry).
2. **Call-site scan** -- an AST scan of ``src/pipeline/**`` and ``src/provenance/**`` finds every
   place a stage name is handed to a recording sink (``mark_stage_complete`` /
   ``mark_stage_failed`` / ``require_declared_stage`` / ``derivation_from_stage_event`` /
   ``DerivationStep(stage=)`` and the pipeline-side wrappers around them), resolves string
   literals and module-level ``*_STAGE`` constants, and requires every one to be declared.
   Arguments it cannot resolve statically are inventoried (and each must be justified, with stale
   entries failing too); functions taking a ``stage`` parameter are inventoried so a *new*
   wrapper cannot hide a call site from the scan.  The scanner is exercised on synthetic source
   (``recorder.mark_stage_complete("brand_new_stage")`` is flagged).
3. **No second stage table** -- no provenance module other than ``contracts.py`` holds a
   collection literal of three or more declared stage names, and the few declared-stage literals
   that do appear in ``src/provenance`` are inventoried and judged.
4. **Recorder behaviour** -- recording a derivation (or marking a stage) against an undeclared
   stage raises ``UnknownStageError`` in *both* enabled and disabled mode (documented behaviour:
   contract drift is caught whether provenance is on or off); with doctored module tables, the
   real recorder's accepted set changes and the drift check reports exactly the doctored stage.

What a downstream spec (``evidence-routing``, ``multiagent-extraction``) must do to add stages
while keeping this module green -- and what is deliberately a forcing function:

* Extend BOTH ``STAGE_CONTRACTS`` and ``STAGE_ORDER`` in ``provenance.contracts``, in the same
  commit as the emitting code, with a non-empty required-kind set per stage.  Nothing in this
  module needs editing for that: the table-consistency, decoy, call-site and no-second-table
  checks are table-driven, and ``test_extending_both_tables_in_place_keeps_every_guard_green``
  simulates exactly this extension in-process.  A stage that exists only in a ``Literal``, enum
  or mapping (never as a literal or ``*_STAGE`` constant at a sink) is fine.
* Classify every new function (or renamed parameter) taking a ``stage`` / ``stages`` parameter:
  add ``(path, function)`` to ``_NON_RECORDING_STAGE_FUNCTIONS`` with a one-line reason, or -- if
  it records or forwards a stage name -- add the function name to ``_STAGE_SINKS`` with the
  position of the stage argument so its call sites are scanned.
* Justify every stage argument the scan cannot resolve statically (for example the pass-through
  parameter of a new sink) by adding ``(path, function, argument source)`` to
  ``_DYNAMIC_STAGE_ARGUMENTS`` with a one-line reason; renaming the parameter, the function or the
  file of an inventoried entry means updating its key (a stale key fails too).

``test_every_stage_taking_function_is_classified`` and
``test_dynamic_stage_arguments_match_the_inventory`` therefore fail, with the entry to add spelled
out, when such a function appears.  That is intentional: a new wrapper must not hide a call site
from the scan.  Everything else fails the moment the two tables disagree, the recorder accepts
something undeclared, or a call site names an undeclared stage.
"""

from __future__ import annotations

import ast
import re
import textwrap
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import pytest

from provenance import recorder as recorder_module
from provenance.contracts import (
    STAGE_CONTRACTS,
    STAGE_ORDER,
    UnknownStageError,
    require_declared_stage,
)
from provenance.identity import (
    derive_claim_id,
    derive_derivation_id,
    derive_source_id,
    format_evidence_id,
)
from provenance.models import (
    UNKNOWN_PRIVACY,
    DerivationStep,
    RunIdentity,
    StageContract,
)
from provenance.recorder import ProvenanceRecorder

# tests/src/provenance/<this file> -> repo root is three levels up from the directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
SCANNED_DIRS = (SRC_ROOT / "pipeline", SRC_ROOT / "provenance")
CONTRACTS_PATH = SRC_ROOT / "provenance" / "contracts.py"
PROVENANCE_DIR = SRC_ROOT / "provenance"


# ---------------------------------------------------------------------------
# find_stage_drift: the pure drift check
# ---------------------------------------------------------------------------


def find_stage_drift(
    contracts_table: Mapping[str, StageContract],
    order: Sequence[str],
    accepted: Collection[str],
) -> list[str]:
    """Return one message per way the stage declarations disagree; empty when they are consistent.

    ``accepted`` is the set of stage names the recorder accepts.  Consistent means: the contract
    table and the ordering name the same stages, the ordering lists each once, each contract is
    keyed by its own stage name and requires at least one record kind, and the recorder accepts
    exactly those stages -- no undeclared stage is accepted, no declared stage is rejected.
    """
    problems: list[str] = []
    in_contracts = set(contracts_table)
    in_order = set(order)
    accepted_set = set(accepted)
    for stage in sorted(in_contracts - in_order):
        problems.append(f"stage {stage!r} is in STAGE_CONTRACTS but not in STAGE_ORDER")
    for stage in sorted(in_order - in_contracts):
        problems.append(f"stage {stage!r} is in STAGE_ORDER but not in STAGE_CONTRACTS")
    for stage, count in sorted(Counter(order).items()):
        if count > 1:
            problems.append(f"stage {stage!r} appears {count} times in STAGE_ORDER")
    for key, contract in sorted(contracts_table.items()):
        if contract.stage != key:
            problems.append(f"STAGE_CONTRACTS key {key!r} holds the contract of {contract.stage!r}")
        if not contract.required_record_kinds:
            problems.append(f"stage {key!r} declares no required record kind")
    declared_in_both = in_contracts & in_order
    for stage in sorted(accepted_set - declared_in_both):
        problems.append(
            f"the recorder accepts stage {stage!r}, which is not declared in both "
            "STAGE_CONTRACTS and STAGE_ORDER"
        )
    for stage in sorted(declared_in_both - accepted_set):
        problems.append(f"stage {stage!r} is declared in both tables but the recorder rejects it")
    return problems


# ---------------------------------------------------------------------------
# Probing the real recorder
# ---------------------------------------------------------------------------

_FINGERPRINT = "a" * 64
_RUN_ID = "run:0123456789abcdef"
_SOURCE_ID = derive_source_id(_FINGERPRINT)

#: Names that are not (and must never become) declared stages, probed alongside the declared ones.
#: Obviously synthetic names (a real downstream stage must never collide with these) plus
#: case/whitespace/empty variants of declared stage names.
_DECOY_STAGES = (
    "zz_undeclared_probe_stage",
    "ZZ_Probe",
    "",
    " ",
    " zz_undeclared_probe_stage",
    " synthesis",
    "Synthesis",
    "synthesis ",
)


def _run_identity() -> RunIdentity:
    return RunIdentity(
        run_id=_RUN_ID,
        config_fingerprint="cfg",
        schema_fingerprint="schema",
        model_ids=("m",),
        document_fingerprints={_SOURCE_ID: _FINGERPRINT},
        created_at="2026-10-11T00:00:00+00:00",
    )


def _derivation(stage: str) -> DerivationStep:
    """A well-formed derivation step whose ``stage`` is ``stage`` (the node id is stage-independent here)."""
    inputs = (format_evidence_id(_SOURCE_ID, "S000001"),)
    outputs = (derive_claim_id(_SOURCE_ID, 3),)
    return DerivationStep(
        node_id=derive_derivation_id("extraction_chunk", "extract", inputs, outputs),
        kind="derivation",
        run_id=_RUN_ID,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        stage=stage,
        derivation_kind="extract",
        determinism="deterministic",
        model_id=None,
        input_ids=inputs,
        output_ids=outputs,
    )


_ENTRY_POINTS = (
    ("mark_stage_complete", lambda rec, stage: rec.mark_stage_complete(stage)),
    ("mark_stage_complete(source_id)", lambda rec, stage: rec.mark_stage_complete(stage, source_id=_SOURCE_ID)),
    ("mark_stage_failed", lambda rec, stage: rec.mark_stage_failed(stage, "probe")),
    ("record_derivation", lambda rec, stage: rec.record_derivation(_derivation(stage))),
)


def probe_recorder_acceptance(candidates: Iterable[str]) -> tuple[set[str], list[str]]:
    """``(accepted, inconsistencies)`` observed from the real :class:`ProvenanceRecorder`.

    A stage is accepted when every recorder entry point accepts it in both enabled and disabled
    mode; ``inconsistencies`` names every stage some entry point or mode accepts and another
    rejects with :class:`UnknownStageError`.  Any other exception propagates (it would be a bug in
    the probe, not an answer).
    """
    accepted: set[str] = set()
    inconsistencies: list[str] = []
    for stage in dict.fromkeys(candidates):
        verdicts: dict[str, bool] = {}
        for enabled in (True, False):
            for entry, call in _ENTRY_POINTS:
                rec = ProvenanceRecorder(_run_identity(), enabled=enabled)
                try:
                    call(rec, stage)
                except UnknownStageError:
                    verdicts[f"{entry}[{'enabled' if enabled else 'disabled'}]"] = False
                else:
                    verdicts[f"{entry}[{'enabled' if enabled else 'disabled'}]"] = True
        if all(verdicts.values()):
            accepted.add(stage)
        elif any(verdicts.values()):
            yes = sorted(k for k, v in verdicts.items() if v)
            no = sorted(k for k, v in verdicts.items() if not v)
            inconsistencies.append(f"stage {stage!r}: accepted by {yes} but rejected by {no}")
    return accepted, inconsistencies


def _probe_candidates() -> list[str]:
    return [*STAGE_CONTRACTS, *STAGE_ORDER, *_DECOY_STAGES]


# ---------------------------------------------------------------------------
# 1. Table consistency against the real recorder
# ---------------------------------------------------------------------------


def test_recorder_accepted_stages_equal_both_declared_tables():
    accepted, inconsistencies = probe_recorder_acceptance(_probe_candidates())
    assert not inconsistencies, inconsistencies
    assert set(STAGE_CONTRACTS) == set(STAGE_ORDER) == accepted, (
        f"contracts={sorted(STAGE_CONTRACTS)} order={sorted(STAGE_ORDER)} accepted={sorted(accepted)}"
    )


def test_no_drift_between_the_live_tables_and_the_recorder():
    accepted, _ = probe_recorder_acceptance(_probe_candidates())
    problems = find_stage_drift(STAGE_CONTRACTS, STAGE_ORDER, accepted)
    assert not problems, "stage-contract drift:\n  " + "\n  ".join(problems)


def test_every_stage_appears_exactly_once_in_the_ordering_with_required_kinds():
    counts = Counter(STAGE_ORDER)
    assert all(counts[stage] == 1 for stage in STAGE_CONTRACTS), counts
    assert len(STAGE_ORDER) == len(set(STAGE_ORDER)) == len(STAGE_CONTRACTS)
    for stage, contract in STAGE_CONTRACTS.items():
        assert contract.stage == stage
        assert contract.required_record_kinds, f"stage {stage!r} requires no record kind"


def decoy_problems(
    contracts_table: Mapping[str, StageContract], order: Sequence[str], accepted_decoys: Collection[str]
) -> list[str]:
    """Decoys that are declared or accepted -- either would make the probe a vacuous accept-all."""
    declared = set(contracts_table) | set(order)
    problems = [f"decoy {d!r} is a declared stage; pick another synthetic decoy" for d in _DECOY_STAGES if d in declared]
    problems += [f"the recorder accepts decoy {d!r}" for d in sorted(accepted_decoys)]
    return problems


def test_decoy_stages_are_really_undeclared():
    """Guards the probe: a decoy that became a declared stage would make it a vacuous accept-all."""
    accepted, _ = probe_recorder_acceptance(_DECOY_STAGES)
    assert decoy_problems(STAGE_CONTRACTS, STAGE_ORDER, accepted) == []


# ---------------------------------------------------------------------------
# find_stage_drift on doctored tables (the guard fails when the tables disagree)
# ---------------------------------------------------------------------------


def _contract(stage: str, kinds=("derivation",)) -> StageContract:
    return StageContract(stage=stage, required_record_kinds=tuple(kinds), scope="document")


#: Fixed synthetic tables for the pure self-tests, so they do not move when a downstream spec
#: legitimately extends the live tables.
_SYNTH_ORDER = (
    "document_extraction",
    "quality_control",
    "evidence_index",
    "metadata_prefill",
    "extraction_chunk",
    "synthesis",
    "chain_validation",
)
_SYNTH_CONTRACTS = {stage: _contract(stage) for stage in _SYNTH_ORDER}


def _doctored_acceptance(contracts_table, order, names: Iterable[str]) -> set[str]:
    accepted = set()
    for name in names:
        try:
            require_declared_stage(name, contracts=contracts_table, order=order)
        except UnknownStageError:
            continue
        accepted.add(name)
    return accepted


def test_drift_check_is_silent_on_consistent_tables():
    accepted = _doctored_acceptance(_SYNTH_CONTRACTS, _SYNTH_ORDER, [*_SYNTH_ORDER, *_DECOY_STAGES])
    assert accepted == set(_SYNTH_ORDER)
    assert find_stage_drift(_SYNTH_CONTRACTS, _SYNTH_ORDER, accepted) == []


def test_drift_check_flags_a_stage_added_to_the_contract_table_only():
    contracts_table = {**_SYNTH_CONTRACTS, "new_stage": _contract("new_stage")}
    accepted = _doctored_acceptance(contracts_table, _SYNTH_ORDER, [*_SYNTH_ORDER, "new_stage"])
    assert "new_stage" not in accepted
    assert find_stage_drift(contracts_table, _SYNTH_ORDER, accepted) == [
        "stage 'new_stage' is in STAGE_CONTRACTS but not in STAGE_ORDER"
    ]


def test_drift_check_flags_a_stage_added_to_the_ordering_only():
    order = (*_SYNTH_ORDER, "new_stage")
    accepted = _doctored_acceptance(_SYNTH_CONTRACTS, order, order)
    assert find_stage_drift(_SYNTH_CONTRACTS, order, accepted) == [
        "stage 'new_stage' is in STAGE_ORDER but not in STAGE_CONTRACTS"
    ]


def test_drift_check_flags_a_stage_removed_from_one_table_only():
    order = tuple(s for s in _SYNTH_ORDER if s != "synthesis")
    accepted = _doctored_acceptance(_SYNTH_CONTRACTS, order, _SYNTH_ORDER)
    problems = find_stage_drift(_SYNTH_CONTRACTS, order, accepted)
    assert problems == ["stage 'synthesis' is in STAGE_CONTRACTS but not in STAGE_ORDER"]


def test_drift_check_flags_a_recorded_but_undeclared_stage():
    """The recorder accepting a stage the tables do not declare (guard removed, or a bypass)."""
    problems = find_stage_drift(_SYNTH_CONTRACTS, _SYNTH_ORDER, {*_SYNTH_ORDER, "brand_new_stage"})
    assert problems == [
        "the recorder accepts stage 'brand_new_stage', which is not declared in both "
        "STAGE_CONTRACTS and STAGE_ORDER"
    ]


def test_drift_check_flags_a_declared_stage_the_recorder_rejects():
    problems = find_stage_drift(_SYNTH_CONTRACTS, _SYNTH_ORDER, set(_SYNTH_ORDER) - {"evidence_index"})
    assert problems == ["stage 'evidence_index' is declared in both tables but the recorder rejects it"]


def test_drift_check_flags_duplicates_wrong_keys_and_empty_requirements():
    order = (*_SYNTH_ORDER, "quality_control")
    assert find_stage_drift(_SYNTH_CONTRACTS, order, set(_SYNTH_ORDER)) == [
        "stage 'quality_control' appears 2 times in STAGE_ORDER"
    ]
    mislabelled = {**_SYNTH_CONTRACTS, "synthesis": _contract("extraction_chunk", ("claim",))}
    assert find_stage_drift(mislabelled, _SYNTH_ORDER, set(_SYNTH_ORDER)) == [
        "STAGE_CONTRACTS key 'synthesis' holds the contract of 'extraction_chunk'"
    ]
    empty = {**_SYNTH_CONTRACTS, "synthesis": _contract("synthesis", ())}
    assert find_stage_drift(empty, _SYNTH_ORDER, set(_SYNTH_ORDER)) == [
        "stage 'synthesis' declares no required record kind"
    ]


# ---------------------------------------------------------------------------
# 4. Real-recorder behaviour: undeclared stages raise in both modes; doctored module tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("enabled", [True, False], ids=["enabled", "disabled"])
@pytest.mark.parametrize("stage", ["brand_new_stage", "", "Synthesis"])
def test_recording_an_undeclared_stage_raises_in_enabled_and_disabled_mode(stage, enabled):
    rec = ProvenanceRecorder(_run_identity(), enabled=enabled)
    with pytest.raises(UnknownStageError):
        rec.record_derivation(_derivation(stage))
    with pytest.raises(UnknownStageError):
        rec.mark_stage_complete(stage)
    with pytest.raises(UnknownStageError):
        rec.mark_stage_failed(stage, "reason")
    with pytest.raises(UnknownStageError):
        rec.mark_stage_complete(stage, source_id=_SOURCE_ID)
    assert rec.snapshot().nodes == {}


@pytest.mark.parametrize("enabled", [True, False], ids=["enabled", "disabled"])
def test_recording_against_every_declared_stage_is_accepted(enabled):
    for stage in STAGE_ORDER:
        rec = ProvenanceRecorder(_run_identity(), enabled=enabled)
        rec.record_derivation(_derivation(stage))
        rec.mark_stage_complete(stage)


def _doctor_live_tables(monkeypatch, contracts_table, order) -> None:
    """Point the real recorder's declaration lookups at doctored copies of the tables."""
    monkeypatch.setitem(require_declared_stage.__kwdefaults__, "contracts", contracts_table)
    monkeypatch.setitem(require_declared_stage.__kwdefaults__, "order", order)
    monkeypatch.setattr(recorder_module, "STAGE_ORDER", order)


def test_real_recorder_over_tables_extended_in_only_one_place_is_reported(monkeypatch):
    contracts_table = {**STAGE_CONTRACTS, "new_stage": _contract("new_stage")}
    _doctor_live_tables(monkeypatch, contracts_table, STAGE_ORDER)
    accepted, inconsistencies = probe_recorder_acceptance([*STAGE_ORDER, "new_stage"])
    assert not inconsistencies
    assert "new_stage" not in accepted
    assert find_stage_drift(contracts_table, STAGE_ORDER, accepted) == [
        "stage 'new_stage' is in STAGE_CONTRACTS but not in STAGE_ORDER"
    ]


def test_real_recorder_over_tables_extended_in_both_places_is_consistent(monkeypatch):
    contracts_table = {**STAGE_CONTRACTS, "new_stage": _contract("new_stage")}
    order = (*STAGE_ORDER[:3], "new_stage", *STAGE_ORDER[3:])
    _doctor_live_tables(monkeypatch, contracts_table, order)
    accepted, inconsistencies = probe_recorder_acceptance([*order, *_DECOY_STAGES])
    assert not inconsistencies
    assert accepted == set(order)
    assert find_stage_drift(contracts_table, order, accepted) == []


def test_probe_sees_a_recorder_that_no_longer_checks_declarations(monkeypatch):
    """A recorder that records against any stage is reported as accepting an undeclared one."""
    monkeypatch.setattr(recorder_module, "require_declared_stage", lambda stage, **kw: None)
    accepted, inconsistencies = probe_recorder_acceptance([*STAGE_ORDER, "brand_new_stage"])
    assert inconsistencies == []
    assert accepted == {*STAGE_ORDER, "brand_new_stage"}
    assert find_stage_drift(STAGE_CONTRACTS, STAGE_ORDER, accepted) == [
        "the recorder accepts stage 'brand_new_stage', which is not declared in both "
        "STAGE_CONTRACTS and STAGE_ORDER"
    ]


def test_probe_reports_a_recorder_whose_entry_points_disagree(monkeypatch):
    """Only the derivation path is unguarded: an inconsistency, not a pass."""
    monkeypatch.setattr(
        recorder_module,
        "require_declared_stage",
        lambda stage, **kw: None if stage == "brand_new_stage" else require_declared_stage(stage),
    )
    original = ProvenanceRecorder.mark_stage_failed

    def guarded_failed(self, stage, reason, *, source_id=None):
        require_declared_stage(stage)
        return original(self, stage, reason, source_id=source_id)

    monkeypatch.setattr(ProvenanceRecorder, "mark_stage_failed", guarded_failed)
    accepted, inconsistencies = probe_recorder_acceptance(["brand_new_stage"])
    assert accepted == set()
    assert len(inconsistencies) == 1 and "brand_new_stage" in inconsistencies[0]


# ---------------------------------------------------------------------------
# 2. AST scan of the stage names handed to recording sinks
# ---------------------------------------------------------------------------

#: Callees that receive a stage name, and the positional index of the argument that carries it.
#: ``None`` means keyword ``stage=`` only.  A stage-taking function that is not listed here must be
#: classified in ``_NON_RECORDING_STAGE_FUNCTIONS`` (inventory test below), so a new wrapper cannot
#: hide a call site.
_STAGE_SINKS: dict[str, int | None] = {
    "mark_stage_complete": 0,
    "mark_stage_failed": 0,
    "require_declared_stage": 0,
    "derivation_from_stage_event": 0,
    "DerivationStep": None,
    "mark_failed": 0,  # pipeline.claim_provenance.DocumentClaims.mark_failed
    "_step": 0,  # pipeline.claim_provenance claim-emission step helper
    "_build_step": 0,  # provenance.adapters.derivation_adapter
    "_mark_failed": 1,  # pipeline.document_provenance (recorder, stage, ...)
    "_mark_complete": 1,  # pipeline.document_provenance (recorder, stage, ...)
}
#: Callees that receive a *sequence* of stage names at positional index 0.
_STAGE_SEQUENCE_SINKS = {"mark_failed_all"}
_STAGE_CONSTANT_NAME = re.compile(r"(^|_)STAGE$")


class StageRef(NamedTuple):
    """One place a stage name reaches a recording sink."""

    path: str  # repo-relative, posix
    line: int
    sink: str
    kind: str  # "literal" | "constant" | "dynamic"
    value: str | None  # the resolved stage name; None for "dynamic"
    expr: str  # source text of the argument
    function: str  # enclosing function name ("<module>" at module level)


class StageDefinition(NamedTuple):
    """A module-level ``*_STAGE = "<name>"`` constant."""

    path: str
    line: int
    name: str
    value: str


def _unparse(node: ast.AST) -> str:
    return ast.unparse(node)


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _module_str_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Name)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        ):
            out[target.id] = value.value
    return out


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: str, constants: Mapping[str, str], globals_: Mapping[str, str | None]) -> None:
        self.path = path
        self.constants = constants
        self.globals = globals_
        self.refs: list[StageRef] = []
        self._functions: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def _ref(self, sink: str, arg: ast.AST, line: int) -> None:
        function = self._functions[-1] if self._functions else "<module>"
        expr = _unparse(arg)
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            self.refs.append(StageRef(self.path, line, sink, "literal", arg.value, expr, function))
        elif isinstance(arg, ast.Name) and arg.id in self.constants:
            self.refs.append(StageRef(self.path, line, sink, "constant", self.constants[arg.id], expr, function))
        elif isinstance(arg, ast.Name) and self.globals.get(arg.id) is not None:
            self.refs.append(StageRef(self.path, line, sink, "constant", self.globals[arg.id], expr, function))
        else:
            self.refs.append(StageRef(self.path, line, sink, "dynamic", None, expr, function))

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee_name(node)
        if name in _STAGE_SINKS:
            position = _STAGE_SINKS[name]
            arg = None
            if position is not None and len(node.args) > position:
                arg = node.args[position]
            for keyword in node.keywords:
                if keyword.arg == "stage":
                    arg = keyword.value
            if arg is not None:
                self._ref(name, arg, node.lineno)
        elif name in _STAGE_SEQUENCE_SINKS and node.args:
            seq = node.args[0]
            if isinstance(seq, (ast.List, ast.Tuple, ast.Set)):
                for element in seq.elts:
                    self._ref(name, element, node.lineno)
            else:
                self._ref(name, seq, node.lineno)
        self.generic_visit(node)


def _global_constants(paths: Iterable[Path]) -> dict[str, str | None]:
    """``{constant name: value}`` over all scanned modules; ``None`` when two modules disagree."""
    merged: dict[str, str | None] = {}
    for path in paths:
        for name, value in _module_str_constants(ast.parse(path.read_text(encoding="utf-8"))).items():
            merged[name] = value if merged.get(name, value) == value else None
    return merged


def scan_stage_references(
    source: str, path: str = "<snippet>", globals_: Mapping[str, str | None] | None = None
) -> tuple[list[StageRef], list[StageDefinition]]:
    """Stage names reaching sinks in ``source``, and its module-level ``*_STAGE`` constants."""
    tree = ast.parse(source)
    constants = _module_str_constants(tree)
    scanner = _Scanner(path, constants, globals_ or {})
    scanner.visit(tree)
    definitions = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and _STAGE_CONSTANT_NAME.search(target.id)
                    and target.id in constants
                ):
                    definitions.append(StageDefinition(path, node.lineno, target.id, constants[target.id]))
    return scanner.refs, definitions


def _scanned_files() -> list[Path]:
    return sorted(p for base in SCANNED_DIRS for p in base.rglob("*.py") if p != CONTRACTS_PATH)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _scan_tree() -> tuple[list[StageRef], list[StageDefinition]]:
    files = _scanned_files()
    globals_ = _global_constants(files)
    refs: list[StageRef] = []
    definitions: list[StageDefinition] = []
    for path in files:
        found, defined = scan_stage_references(path.read_text(encoding="utf-8"), _rel(path), globals_)
        refs.extend(found)
        definitions.extend(defined)
    return refs, definitions


def undeclared_stage_findings(
    refs: Iterable[StageRef],
    definitions: Iterable[StageDefinition],
    declared: Collection[str],
) -> list[str]:
    """One message per literal, resolved constant or ``*_STAGE`` definition naming an undeclared stage."""
    out = []
    for ref in refs:
        if ref.kind != "dynamic" and ref.value not in declared:
            out.append(
                f"{ref.path}:{ref.line}: {ref.sink}({ref.expr}) uses stage {ref.value!r}, "
                "which is not declared in STAGE_CONTRACTS and STAGE_ORDER"
            )
    for definition in definitions:
        if definition.value not in declared:
            out.append(
                f"{definition.path}:{definition.line}: {definition.name} = {definition.value!r} "
                "is not a declared stage"
            )
    return out


#: ``*_STAGE`` constants that are deliberately *not* declared stages.
_SENTINEL_STAGE_CONSTANTS: dict[tuple[str, str], str] = {
    ("src/provenance/graph.py", "UNATTRIBUTED_STAGE"): (
        "attribution sentinel for findings no stage produced; sorts after every declared stage and "
        "must never be recordable"
    ),
}


def _real_definitions(definitions: Iterable[StageDefinition]) -> list[StageDefinition]:
    return [d for d in definitions if (d.path, d.name) not in _SENTINEL_STAGE_CONSTANTS]


def test_sentinel_stage_constants_are_inventoried_and_stay_undeclared():
    _, definitions = _scan_tree()
    found = {(d.path, d.name): d.value for d in definitions if (d.path, d.name) in _SENTINEL_STAGE_CONSTANTS}
    assert set(found) == set(_SENTINEL_STAGE_CONSTANTS), "stale or missing sentinel inventory entry"
    from provenance.graph import UNATTRIBUTED_STAGE

    assert found[("src/provenance/graph.py", "UNATTRIBUTED_STAGE")] == UNATTRIBUTED_STAGE
    assert UNATTRIBUTED_STAGE not in STAGE_CONTRACTS and UNATTRIBUTED_STAGE not in STAGE_ORDER
    accepted, _ = probe_recorder_acceptance([UNATTRIBUTED_STAGE])
    assert accepted == set()


def test_every_stage_named_at_a_pipeline_or_provenance_call_site_is_declared():
    refs, definitions = _scan_tree()
    declared = set(STAGE_CONTRACTS) & set(STAGE_ORDER)
    findings = undeclared_stage_findings(refs, _real_definitions(definitions), declared)
    assert not findings, "undeclared stage recorded:\n  " + "\n  ".join(findings)


#: The stages provenance-core itself emits.  Downstream specs add stages but never remove or
#: rename these, so the scan must keep seeing each of them at a sink.  (Newly added stages are not
#: required to be named at a sink: they may live only in a ``Literal`` or a mapping.)
_CORE_STAGES = (
    "document_extraction",
    "quality_control",
    "evidence_index",
    "metadata_prefill",
    "extraction_chunk",
    "synthesis",
    "chain_validation",
)


def call_site_problems(
    refs: Iterable[StageRef],
    definitions: Iterable[StageDefinition],
    declared: Collection[str],
    core_stages: Iterable[str] = _CORE_STAGES,
) -> list[str]:
    """Undeclared stages named at sinks, and core stages the scan no longer sees at any sink."""
    refs = list(refs)
    definitions = _real_definitions(definitions)
    problems = undeclared_stage_findings(refs, definitions, declared)
    resolved = {r.value for r in refs if r.kind != "dynamic"} | {d.value for d in definitions}
    problems += [
        f"core stage {stage!r} is no longer named at any scanned sink (scanner blind, or the stage was renamed)"
        for stage in core_stages
        if stage not in resolved
    ]
    return problems


def test_the_scan_sees_the_known_sinks_and_every_core_stage():
    """Guards the scanner against going blind; newly declared stages are not required to appear."""
    refs, definitions = _scan_tree()
    assert call_site_problems(refs, definitions, set(STAGE_CONTRACTS) & set(STAGE_ORDER)) == []
    sinks_seen = {r.sink for r in refs}
    assert {"mark_stage_complete", "mark_stage_failed", "mark_failed", "_step", "_build_step"} <= sinks_seen
    assert len(refs) >= 20


def test_extending_both_tables_in_place_keeps_every_guard_green(monkeypatch):
    """The documented extension protocol, simulated: add stages to both tables and nothing drifts.

    A stage added to ``STAGE_CONTRACTS`` and ``STAGE_ORDER`` together (non-empty required kinds)
    must be accepted by the real recorder, leave the drift check, the decoy check and the
    call-site completeness check silent, and be a legitimate argument at a sink -- while the same
    call site is still flagged against the unextended tables.
    """
    extra = ("zz_probe_stage_a", "zz_probe_stage_b")
    contracts_table = {**STAGE_CONTRACTS, **{s: _contract(s, ("derivation", "evidence")) for s in extra}}
    order = (*STAGE_ORDER[:3], *extra, *STAGE_ORDER[3:])
    snippet = (
        "def emit(recorder):\n"
        '    recorder.mark_stage_complete("zz_probe_stage_a")\n'
        '    recorder.mark_stage_failed("zz_probe_stage_b", "why")\n'
    )
    snippet_refs, _ = scan_stage_references(snippet)
    assert undeclared_stage_findings(snippet_refs, [], set(STAGE_ORDER))  # flagged before the extension

    _doctor_live_tables(monkeypatch, contracts_table, order)
    accepted, inconsistencies = probe_recorder_acceptance([*order, *_DECOY_STAGES])
    assert not inconsistencies
    assert set(extra) <= accepted
    assert find_stage_drift(contracts_table, order, accepted - set(_DECOY_STAGES)) == []
    assert decoy_problems(contracts_table, order, accepted & set(_DECOY_STAGES)) == []
    real_refs, real_definitions = _scan_tree()
    # Only the real sites that name a stage declared today: a genuine undeclared call site is
    # reported by its own test, not by this simulation.
    live_declared = set(STAGE_CONTRACTS) & set(STAGE_ORDER)
    real_refs = [r for r in real_refs if r.kind == "dynamic" or r.value in live_declared]
    real_definitions = [d for d in real_definitions if d.value in live_declared]
    declared = set(contracts_table) & set(order)
    assert call_site_problems([*real_refs, *snippet_refs], real_definitions, declared) == []
    assert undeclared_stage_findings(snippet_refs, [], declared) == []


# -- scanner self-tests -----------------------------------------------------------------------


def test_scanner_flags_a_literal_stage_that_is_not_declared():
    refs, _ = scan_stage_references('def f(recorder):\n    recorder.mark_stage_complete("brand_new_stage")\n')
    assert [(r.sink, r.kind, r.value) for r in refs] == [("mark_stage_complete", "literal", "brand_new_stage")]
    findings = undeclared_stage_findings(refs, [], set(_SYNTH_ORDER))
    assert len(findings) == 1 and "brand_new_stage" in findings[0] and "<snippet>:2" in findings[0]


@pytest.mark.parametrize(
    "call",
    [
        'recorder.mark_stage_failed("brand_new_stage", "why", source_id=sid)',
        'require_declared_stage("brand_new_stage")',
        'derivation_from_stage_event("brand_new_stage", "extract", run_id=r, inputs=(), outputs=())',
        'DerivationStep(node_id=n, kind="derivation", stage="brand_new_stage")',
        'claims.mark_failed("brand_new_stage", "why")',
        'claims.mark_failed_all(("synthesis", "brand_new_stage"), "why")',
        '_mark_failed(recorder, "brand_new_stage", "why", sid)',
        '_mark_complete(recorder, "brand_new_stage", sid)',
        'self._step("brand_new_stage", "extract")',
        'recorder.mark_stage_complete(stage="brand_new_stage")',
    ],
)
def test_scanner_flags_every_sink_shape(call):
    refs, _ = scan_stage_references(f"def f():\n    {call}\n")
    assert any(r.value == "brand_new_stage" for r in refs), call
    assert undeclared_stage_findings(refs, [], set(_SYNTH_ORDER)), call


def test_scanner_flags_an_undeclared_module_constant_and_resolves_it_at_use_sites():
    source = textwrap.dedent(
        """
        NEW_STAGE = "brand_new_stage"

        def f(recorder):
            recorder.mark_stage_complete(NEW_STAGE, source_id="x")
        """
    )
    refs, definitions = scan_stage_references(source)
    assert [(r.kind, r.value) for r in refs] == [("constant", "brand_new_stage")]
    assert [(d.name, d.value) for d in definitions] == [("NEW_STAGE", "brand_new_stage")]
    assert len(undeclared_stage_findings(refs, definitions, set(_SYNTH_ORDER))) == 2


def test_scanner_accepts_declared_stages_and_reports_non_literals_as_dynamic():
    source = textwrap.dedent(
        """
        def f(recorder, stage, others):
            recorder.mark_stage_complete("synthesis")
            recorder.mark_stage_complete(stage)
            recorder.mark_stage_complete(others[0])
            recorder.mark_stage_complete(stage.lower())
        """
    )
    refs, _ = scan_stage_references(source)
    assert [r.kind for r in refs] == ["literal", "dynamic", "dynamic", "dynamic"]
    assert undeclared_stage_findings(refs, [], set(_SYNTH_ORDER)) == []


def test_scanner_ignores_unrelated_calls_and_stage_keywords():
    source = 'def f(x):\n    x.run(stage="validation_repair")\n    emit("brand_new_stage")\n'
    refs, definitions = scan_stage_references(source)
    assert refs == [] and definitions == []


# -- inventories: dynamic arguments and stage-taking functions ------------------------------------

#: Stage arguments the scan cannot resolve statically, each justified.  Keyed by
#: (file, enclosing function, argument source).  A stale entry fails
#: ``test_dynamic_stage_arguments_match_the_inventory``.
_PASS_THROUGH = "pass-through parameter of a scanned sink; its call sites are scanned"
_DYNAMIC_STAGE_ARGUMENTS: dict[tuple[str, str, str], str] = {
    ("src/pipeline/claim_provenance.py", "_step", "stage"): _PASS_THROUGH,
    ("src/pipeline/claim_provenance.py", "mark_failed", "stage"): _PASS_THROUGH,
    ("src/pipeline/claim_provenance.py", "mark_failed_all", "stage"): (
        "loop variable over the 'stages' parameter of a scanned sequence sink; its call sites are scanned"
    ),
    ("src/pipeline/claim_provenance.py", "emit", "stage"): (
        "loop variable over the literal pairs (PREFILL_STAGE, ...), (CHUNK_STAGE, ...), "
        "(SYNTHESIS_STAGE, ...), whose constants are scanned"
    ),
    ("src/pipeline/claim_provenance.py", "emit", "claim_stages_that_ran(prefilled, chunk_results, synthesized_fields is not None)"): (
        "returns a subset of the three claim-stage constants; pinned by "
        "test_claim_stages_that_ran_only_yields_declared_claim_stages"
    ),
    ("src/pipeline/pdf_processor.py", "process_pdf", "claim_stages"): (
        "the result of claim_stages_that_ran (pinned by test_claim_stages_that_ran_only_yields_declared_claim_stages)"
    ),
    ("src/pipeline/document_provenance.py", "_mark_complete", "stage"): _PASS_THROUGH,
    ("src/pipeline/document_provenance.py", "_mark_failed", "stage"): _PASS_THROUGH,
    ("src/pipeline/run_provenance.py", "record_extraction_failure", "stages"): (
        "built from STAGE_CONTRACTS itself (claim-scope stages not yet decided), declared by construction"
    ),
    ("src/provenance/adapters/derivation_adapter.py", "_build_step", "stage"): (
        "pass-through parameter; derivation_from_stage_event / the QC adapter call require_declared_stage first"
    ),
    ("src/provenance/adapters/derivation_adapter.py", "derivation_from_stage_event", "stage"): (
        "the sink's own parameter, checked by require_declared_stage on its first line"
    ),
    ("src/provenance/recorder.py", "mark_stage_complete", "stage"): "the recorder's own parameter, checked first",
    ("src/provenance/recorder.py", "mark_stage_failed", "stage"): "the recorder's own parameter, checked first",
    ("src/provenance/recorder.py", "record_derivation", "step.stage"): "the recorder's own check of the step's stage",
}

#: Functions taking a ``stage``/``stages`` parameter that are not recording sinks, each judged.
#: Keyed by (file, function).  Anything not in ``_STAGE_SINKS`` / ``_STAGE_SEQUENCE_SINKS`` and not
#: listed here fails ``test_every_stage_taking_function_is_classified``.
_TELEMETRY = "token-budget / telemetry stage vocabulary (extraction_chunk, synthesis, validation_repair, cache_warmup), not a provenance stage"
_FAILURE_RECORD = "manifest failure-record stage vocabulary (extraction_chunk, synthesis, output_write, schema_validation), not a provenance stage"
_NON_RECORDING_STAGE_FUNCTIONS: dict[tuple[str, str], str] = {
    ("src/pipeline/pdf_processor.py", "_check_and_mitigate_budget"): _TELEMETRY,
    ("src/pipeline/pdf_processor.py", "extract_with_repair"): _TELEMETRY,
    ("src/pipeline/pdf_processor.py", "_make_failure_record"): _FAILURE_RECORD,
    ("src/pipeline/pdf_processor.py", "_failure_record_from_exception"): _FAILURE_RECORD,
    ("src/pipeline/token_budget.py", "__init__"): _TELEMETRY,
    ("src/pipeline/token_budget.py", "apply_mitigation"): _TELEMETRY,
    ("src/pipeline/token_budget.py", "check_budget"): _TELEMETRY,
    ("src/provenance/completeness.py", "_contribution"): "consumer: reads stage names already recorded / from STAGE_CONTRACTS",
    ("src/provenance/completeness.py", "_document_hint"): "consumer: reads a stage name from recorded data",
    ("src/provenance/completeness.py", "effective"): "consumer: reads recorded stage status",
    ("src/provenance/graph.py", "stage_sort_key"): "consumer: sort key over STAGE_ORDER (undeclared names sort last)",
    ("src/provenance/identity.py", "derive_derivation_id"): "hashes the stage name into an identifier; declaration is checked by the recording sinks",
    ("src/provenance/recorder.py", "_escalate_locked"): "internal; every caller has already run require_declared_stage",
}


def _stage_taking_functions() -> set[tuple[str, str]]:
    out = set()
    for path in _scanned_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
                if "stage" in names or "stages" in names:
                    out.add((_rel(path), node.name))
    return out


@pytest.mark.parametrize("prefilled", [{}, {1: "x"}], ids=["no-prefill", "prefill"])
@pytest.mark.parametrize("chunks", [[], [object()]], ids=["no-chunks", "chunks"])
@pytest.mark.parametrize("synthesis_ran", [False, True], ids=["no-synthesis", "synthesis"])
def test_claim_stages_that_ran_only_yields_declared_claim_stages(prefilled, chunks, synthesis_ran):
    import importlib

    claims = importlib.import_module("pipeline.claim_provenance")
    stages = claims.claim_stages_that_ran(prefilled, chunks, synthesis_ran)
    assert set(stages) <= {s for s, c in STAGE_CONTRACTS.items() if c.scope == "claim"}
    assert list(stages) == sorted(stages, key=STAGE_ORDER.index)
    assert accepted_by_recorder(stages)


def accepted_by_recorder(stages: Iterable[str]) -> bool:
    accepted, inconsistencies = probe_recorder_acceptance(stages)
    return not inconsistencies and accepted == set(stages)


def test_dynamic_stage_arguments_match_the_inventory():
    refs, _ = _scan_tree()
    dynamic = {(r.path, r.function, r.expr) for r in refs if r.kind == "dynamic"}
    missing = sorted(dynamic - set(_DYNAMIC_STAGE_ARGUMENTS))
    stale = sorted(set(_DYNAMIC_STAGE_ARGUMENTS) - dynamic)
    assert not missing, (
        "stage arguments the scan cannot resolve statically.  For each (path, function, argument) "
        "add the key to _DYNAMIC_STAGE_ARGUMENTS in this module with a one-line reason "
        "(e.g. 'pass-through parameter of a scanned sink'), or pass a literal / *_STAGE constant "
        f"instead: {missing}"
    )
    assert not stale, (
        "_DYNAMIC_STAGE_ARGUMENTS entries that match no call site (code removed, or a function, "
        f"parameter or file was renamed): update the key or delete the entry: {stale}"
    )


def test_every_stage_taking_function_is_classified():
    sinks = set(_STAGE_SINKS) | _STAGE_SEQUENCE_SINKS
    found = _stage_taking_functions()
    unclassified = sorted(
        (path, name) for path, name in found if name not in sinks and (path, name) not in _NON_RECORDING_STAGE_FUNCTIONS
    )
    stale = sorted(key for key in _NON_RECORDING_STAGE_FUNCTIONS if key not in found)
    assert not unclassified, (
        "functions taking a stage/stages parameter that are not classified.  For each (path, function): "
        "if it records or forwards a stage name, add the function name to _STAGE_SINKS "
        "(function name -> position of the stage argument) so its call sites are scanned, and justify its "
        "pass-through parameter in _DYNAMIC_STAGE_ARGUMENTS; otherwise add (path, function) to "
        f"_NON_RECORDING_STAGE_FUNCTIONS with a one-line reason: {unclassified}"
    )
    assert not stale, (
        "_NON_RECORDING_STAGE_FUNCTIONS entries that match no function (removed, or the function, "
        f"parameter or file was renamed): update the key or delete the entry: {stale}"
    )


# ---------------------------------------------------------------------------
# 3. No second stage table in the provenance package
# ---------------------------------------------------------------------------

#: Declared-stage literals that appear in ``src/provenance`` outside ``contracts.py``, each judged.
#: (file, line-independent description) -> reason.  Anything else fails.
_PROVENANCE_STAGE_LITERALS: dict[tuple[str, str, str], str] = {
    ("src/provenance/adapters/derivation_adapter.py", "quality_control", "assigned to _QC_STAGE"): (
        "the QC derivation adapter's own stage name; require_declared_stage(_QC_STAGE) runs before use "
        "and the scan resolves the constant at its call sites"
    ),
    ("src/provenance/graph.py", "chain_validation", "assigned to VALIDATION_CHECK_NAME"): (
        "the ValidationRecord check_name vocabulary (val:<run_id>:chain_validation), not a stage list"
    ),
}


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def declared_stage_collections(source: str, declared: Collection[str], minimum: int = 3) -> list[str]:
    """Collection literals (tuple/list/set/dict) holding ``minimum`` or more declared stage names."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            members = node.elts
        elif isinstance(node, ast.Dict):
            members = [k for k in node.keys if k is not None]
        else:
            continue
        names = {
            m.value for m in members if isinstance(m, ast.Constant) and isinstance(m.value, str) and m.value in declared
        }
        if len(names) >= minimum:
            found.append(f"line {node.lineno}: {sorted(names)}")
    return found


def test_no_provenance_module_other_than_contracts_holds_its_own_stage_table():
    offenders = {}
    for path in sorted(PROVENANCE_DIR.rglob("*.py")):
        if path == CONTRACTS_PATH:
            continue
        found = declared_stage_collections(path.read_text(encoding="utf-8"), set(STAGE_ORDER))
        if found:
            offenders[_rel(path)] = found
    assert not offenders, f"a second stage table: {offenders}"


def test_stage_table_scanner_flags_a_private_stage_list():
    source = 'ORDER = ("document_extraction", "quality_control", "evidence_index")\nOK = ("synthesis", "x")\n'
    assert len(declared_stage_collections(source, set(_SYNTH_ORDER))) == 1
    mapping = '{"document_extraction": 1, "quality_control": 2, "synthesis": 3}'
    assert len(declared_stage_collections(f"X = {mapping}\n", set(_SYNTH_ORDER))) == 1
    assert declared_stage_collections('X = {"synthesis": 1, "quality_control": 2}\n', set(_SYNTH_ORDER)) == []


def _provenance_stage_literals() -> set[tuple[str, str, str]]:
    declared = set(STAGE_ORDER)
    out = set()
    for path in sorted(PROVENANCE_DIR.rglob("*.py")):
        if path == CONTRACTS_PATH:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_node_ids(tree)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in declared
                and id(node) not in docstrings
            ):
                parent = parents.get(node)
                if isinstance(parent, (ast.Assign, ast.AnnAssign)):
                    targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                    context = "assigned to " + ", ".join(_unparse(t) for t in targets)
                else:
                    context = "in " + type(parent).__name__
                out.add((_rel(path), node.value, context))
    return out


def test_declared_stage_literals_in_the_provenance_package_are_inventoried():
    found = _provenance_stage_literals()
    unexpected = sorted(found - set(_PROVENANCE_STAGE_LITERALS))
    stale = sorted(set(_PROVENANCE_STAGE_LITERALS) - found)
    assert not unexpected, (
        "declared-stage literals in src/provenance outside contracts.py.  A stage table must stay in "
        "contracts.py; if the literal is a legitimate read of a declared name, add (path, value, context) "
        f"to _PROVENANCE_STAGE_LITERALS with a one-line reason: {unexpected}"
    )
    assert not stale, (
        f"_PROVENANCE_STAGE_LITERALS entries that match nothing (code removed or renamed): update or delete: {stale}"
    )


def test_no_other_module_redefines_the_stage_tables():
    """``contracts.py`` defines the tables; every other module only reads them."""
    tree = ast.parse(CONTRACTS_PATH.read_text(encoding="utf-8"))
    assigned = {
        t.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(t, ast.Name)
    }
    assert {"STAGE_CONTRACTS", "STAGE_ORDER"} <= assigned
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path == CONTRACTS_PATH:
            continue
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in module.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    assert not (
                        isinstance(target, ast.Name) and target.id in {"STAGE_CONTRACTS", "STAGE_ORDER"}
                    ), f"{_rel(path)} redefines {target.id}"
