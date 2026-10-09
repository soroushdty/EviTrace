"""Tests for ``provenance.adapters.derivation_adapter`` — QC provenance and stage events -> DerivationStep.

The QC provenance mapping is a literal dict in the exact shape the pipeline
holds in ``unified.content["provenance"]``: ``_build_provenance_dict`` in
``src/quality_control/reconciler.py`` (``primary_artifact_id``,
``secondary_artifact_id``, ``primary_observation``, ``secondary_observation``,
``investigator_object``, ``adjudication_decisions``) plus the branch-role keys
``run_quality_control`` attaches in ``src/quality_control/quality_control.py``
(``primary_branch_source``, ``secondary_branch_source``, ``branch_selection``).

Covers derivation steps carrying stage, kind, inputs and outputs (6.1),
deterministic and probabilistic marking (6.3, 6.4), many-to-one as one step
(6.5), one-to-many without duplicating the input (6.6), removal reason and
removed identifiers (6.7), and the binding ``derivation_from_stage_event``
call contract: closed ``DerivationKind``, ``Determinism`` literal never a
``bool``, undeclared fields only through namespaced ``extensions`` (10.4).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest

from provenance import adapters
from provenance.adapters import derivation_adapter
from provenance.adapters.derivation_adapter import (
    QC_EXTENSION_NAMESPACE,
    derivation_from_stage_event,
    derivations_from_qc_provenance,
)
from provenance.contracts import UnknownStageError
from provenance.identity import InvalidIdentifierError, derive_claim_id, derive_derivation_id, format_evidence_id
from provenance.models import DerivationKind, DerivationStep, RunIdentity
from provenance.privacy_carrier import UNKNOWN_PRIVACY
from provenance.recorder import ProvenanceRecorder

_SOURCE_ID = "src:" + "a" * 64
_RUN_ID = "run:0123456789abcdef"
_MODEL = "gpt-5-mini-2026-01-01"

_S1 = format_evidence_id(_SOURCE_ID, "S000001")
_S2 = format_evidence_id(_SOURCE_ID, "S000002")
_S3 = format_evidence_id(_SOURCE_ID, "S000003")
_C1 = derive_claim_id(_SOURCE_ID, 1)
_C2 = derive_claim_id(_SOURCE_ID, 2)
_C3 = derive_claim_id(_SOURCE_ID, 3)


def _qc_provenance(**overrides: Any) -> dict[str, Any]:
    """The real reconciled-record provenance shape (artifact IDs are empty on the real path)."""
    provenance: dict[str, Any] = {
        "primary_artifact_id": "",
        "secondary_artifact_id": "",
        "primary_observation": {},
        "secondary_observation": {},
        "investigator_object": {},
        "adjudication_decisions": {
            "primary_extractor": "grobid",
            "confidence": 0.9,
            "rationale": "strategy-driven reconciliation",
        },
        "primary_branch_source": "grobid",
        "secondary_branch_source": "pdfplumber",
        "branch_selection": "adjudicated",
    }
    provenance.update(overrides)
    return provenance


def _event(stage: str = "extraction_chunk", kind: Any = "extract", **kwargs: Any) -> DerivationStep:
    kwargs.setdefault("run_id", _RUN_ID)
    kwargs.setdefault("inputs", [_S1])
    kwargs.setdefault("outputs", [_C1])
    return derivation_from_stage_event(stage, kind, **kwargs)


# ---------------------------------------------------------------------------
# Stage events -> derivation steps (6.1, 6.2)
# ---------------------------------------------------------------------------


def test_stage_event_carries_stage_kind_inputs_and_outputs() -> None:
    step = _event("synthesis", "synthesize", inputs=[_C1, _C2], outputs=[_C3], model_id=_MODEL)

    assert isinstance(step, DerivationStep)
    assert step.kind == "derivation"
    assert step.run_id == _RUN_ID
    assert step.stage == "synthesis"
    assert step.derivation_kind == "synthesize"
    assert step.input_ids == (_C1, _C2)
    assert step.output_ids == (_C3,)
    assert step.privacy is UNKNOWN_PRIVACY
    assert step.extensions == {}
    assert step.removal_reason is None
    assert step.removed_ids == ()


def test_stage_event_node_id_is_the_content_addressed_derivation_id() -> None:
    step = _event("extraction_chunk", "extract", inputs=[_S2, _S1], outputs=[_C1])

    assert step.node_id == derive_derivation_id("extraction_chunk", "extract", [_S1, _S2], [_C1])
    assert step.node_id == _event("extraction_chunk", "extract", inputs=[_S1, _S2], outputs=[_C1]).node_id


def test_stage_event_is_pure_and_repeatable() -> None:
    assert _event(model_id=_MODEL) == _event(model_id=_MODEL)


def test_prefill_step_for_the_metadata_prefill_stage() -> None:
    step = _event("metadata_prefill", "prefill", inputs=[], outputs=[_C1, _C2])

    assert step.stage == "metadata_prefill"
    assert step.derivation_kind == "prefill"
    assert step.input_ids == ()
    assert step.output_ids == (_C1, _C2)
    assert step.determinism == "deterministic"


@pytest.mark.parametrize("kind", get_args(DerivationKind))
def test_every_derivation_kind_is_accepted(kind: str) -> None:
    removal = {"removal_reason": "dropped", "removed_ids": [_S2]} if kind == "remove" else {}
    assert _event("quality_control", kind, **removal).derivation_kind == kind


# ---------------------------------------------------------------------------
# Determinism (6.3, 6.4)
# ---------------------------------------------------------------------------


def test_model_performed_step_is_probabilistic_with_the_model_id() -> None:
    step = _event(model_id=_MODEL)

    assert step.determinism == "probabilistic"
    assert step.model_id == _MODEL


def test_rule_based_step_is_deterministic_without_a_model_id() -> None:
    step = _event("quality_control", "reconcile")

    assert step.determinism == "deterministic"
    assert step.model_id is None


def test_explicit_probabilistic_literal_without_a_model_is_kept() -> None:
    step = _event(determinism="probabilistic")

    assert step.determinism == "probabilistic"
    assert step.model_id is None


def test_explicit_literal_matching_the_default_is_accepted() -> None:
    assert _event(model_id=_MODEL, determinism="probabilistic").determinism == "probabilistic"
    assert _event(determinism="deterministic").determinism == "deterministic"


def test_deterministic_literal_with_a_model_id_is_rejected() -> None:
    # 6.3: a model-performed transformation is probabilistic.
    with pytest.raises(ValueError, match="probabilistic"):
        _event(model_id=_MODEL, determinism="deterministic")


@pytest.mark.parametrize("flag", [True, False])
def test_boolean_determinism_is_rejected(flag: bool) -> None:
    with pytest.raises(TypeError, match="bool"):
        _event(determinism=flag)


@pytest.mark.parametrize("value", ["random", "Deterministic", "", 1, 0])
def test_determinism_outside_the_literal_is_rejected(value: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        _event(determinism=value)


@pytest.mark.parametrize("model_id", ["", 3, ["m"]])
def test_malformed_model_id_is_rejected(model_id: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        _event(model_id=model_id)


# ---------------------------------------------------------------------------
# Closed derivation kind, required and positional
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["transform", "Extract", "", None, 1, True])
def test_derivation_kind_outside_the_vocabulary_is_rejected(kind: Any) -> None:
    with pytest.raises(ValueError, match="DerivationKind"):
        _event(kind=kind)


def test_derivation_kind_is_required() -> None:
    with pytest.raises(TypeError):
        derivation_from_stage_event("extraction_chunk", run_id=_RUN_ID, inputs=[_S1], outputs=[_C1])  # type: ignore[call-arg]


def test_signature_matches_the_design_contract() -> None:
    params = inspect.signature(derivation_from_stage_event).parameters

    assert list(params) == [
        "stage", "kind", "run_id", "inputs", "outputs", "model_id",
        "determinism", "removal_reason", "removed_ids", "extensions",
    ]
    assert params["stage"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["kind"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["kind"].default is inspect.Parameter.empty
    assert all(params[name].kind is inspect.Parameter.KEYWORD_ONLY for name in list(params)[2:])
    assert params["model_id"].default is None
    assert params["determinism"].default is None
    assert params["removal_reason"].default is None
    assert params["removed_ids"].default == ()
    assert params["extensions"].default is None


def test_kind_is_never_inferred_from_the_stage() -> None:
    # The same stage yields whatever kind the caller pins.
    assert _event("extraction_chunk", "normalize").derivation_kind == "normalize"
    assert _event("extraction_chunk", "extract").derivation_kind == "extract"


# ---------------------------------------------------------------------------
# Stage names must be declared
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["routing_unknown", "", "Extraction_Chunk"])
def test_undeclared_stage_is_rejected(stage: str) -> None:
    with pytest.raises(UnknownStageError):
        _event(stage=stage)


def test_non_string_stage_is_rejected() -> None:
    with pytest.raises(UnknownStageError):
        _event(stage=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Many-to-one and one-to-many (6.5, 6.6)
# ---------------------------------------------------------------------------


def test_merge_yields_one_step_with_all_inputs() -> None:
    step = _event("synthesis", "merge", inputs=[_C1, _C2, _S1], outputs=[_C3])

    assert step.input_ids == (_C1, _C2, _S1)
    assert step.output_ids == (_C3,)


def test_repeated_input_is_listed_once_in_first_occurrence_order() -> None:
    step = _event("synthesis", "merge", inputs=[_C2, _C1, _C2], outputs=[_C3, _C3])

    assert step.input_ids == (_C2, _C1)
    assert step.output_ids == (_C3,)
    assert step.node_id == derive_derivation_id("synthesis", "merge", [_C1, _C2], [_C3])


def test_fan_out_reuses_a_single_input_identifier_across_steps() -> None:
    steps = [_event("extraction_chunk", "extract", inputs=[_S1], outputs=[claim]) for claim in (_C1, _C2, _C3)]

    assert len({step.node_id for step in steps}) == 3
    assert all(step.input_ids == (_S1,) for step in steps)
    # The input is referenced by identifier only; no step carries a copy of it as an output.
    assert all(_S1 not in step.output_ids for step in steps)


def test_fan_out_in_one_step_keeps_one_input_and_every_output() -> None:
    step = _event("extraction_chunk", "chunk", inputs=[_S1], outputs=[_C1, _C2, _C3])

    assert step.input_ids == (_S1,)
    assert step.output_ids == (_C1, _C2, _C3)


@pytest.mark.parametrize("bad", ["S000001", [""], [None], [1], None])
def test_malformed_inputs_or_outputs_are_rejected(bad: Any) -> None:
    with pytest.raises((InvalidIdentifierError, TypeError)):
        _event(inputs=bad)
    with pytest.raises((InvalidIdentifierError, TypeError)):
        _event(outputs=bad)


# ---------------------------------------------------------------------------
# Removal (6.7)
# ---------------------------------------------------------------------------


def test_removal_step_carries_reason_and_removed_ids() -> None:
    step = _event(
        "extraction_chunk", "remove", inputs=[_S1, _S2], outputs=[_S1],
        removal_reason="below minimum score", removed_ids=[_S2],
    )

    assert step.removal_reason == "below minimum score"
    assert step.removal_reason
    assert step.removed_ids == (_S2,)


def test_removal_on_another_kind_is_recorded() -> None:
    step = _event("synthesis", "merge", inputs=[_C1, _C2], outputs=[_C3], removal_reason="trimmed", removed_ids=[_C2])

    assert step.removal_reason == "trimmed"
    assert step.removed_ids == (_C2,)


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_removed_ids_without_a_non_empty_reason_are_rejected(reason: Any) -> None:
    with pytest.raises(ValueError, match="removal_reason"):
        _event(removal_reason=reason, removed_ids=[_S1])


def test_remove_kind_without_removed_ids_is_rejected() -> None:
    with pytest.raises(ValueError, match="removed_ids"):
        _event("extraction_chunk", "remove", removal_reason="dropped")


@pytest.mark.parametrize("reason", ["below minimum score", "", "   "])
def test_reason_without_removed_ids_records_no_removal(reason: str) -> None:
    # Callers fill a per-stage reason whether or not anything was removed; a
    # step never claims a removal it did not make.
    step = _event("evidence_index", "filter", inputs=[_S1], outputs=[_S1], removal_reason=reason, removed_ids=[])

    assert step.removal_reason is None
    assert step.removed_ids == ()


@pytest.mark.parametrize("bad", ["S000001", [""], [None]])
def test_malformed_removed_ids_are_rejected(bad: Any) -> None:
    with pytest.raises((InvalidIdentifierError, TypeError)):
        _event(removal_reason="dropped", removed_ids=bad)


def test_non_string_reason_is_rejected() -> None:
    with pytest.raises(TypeError):
        _event(removal_reason=7, removed_ids=[_S1])


# ---------------------------------------------------------------------------
# Undeclared fields travel only through namespaced extensions (10.4)
# ---------------------------------------------------------------------------


def test_namespaced_undeclared_fields_round_trip_unchanged() -> None:
    extensions = {"x-multiagent-prompt_version": "v3", "x-multiagent-replaced_field_indices": [4, 7]}

    step = _event("synthesis", "extract", model_id=_MODEL, extensions=extensions)

    assert step.extensions == extensions
    assert json.loads(json.dumps(dataclasses.asdict(step)))["extensions"] == extensions
    assert not hasattr(step, "prompt_version")


@pytest.mark.parametrize("key", ["prompt_version", "replaced_field_indices", "x-prompt_version", "x-Multi-field"])
def test_un_namespaced_undeclared_field_is_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="x-<namespace>-<field>"):
        _event(extensions={key: "v3"})


def test_undeclared_field_as_a_keyword_is_rejected() -> None:
    with pytest.raises(TypeError):
        _event(prompt_version="v3")


def test_extensions_must_be_a_mapping() -> None:
    with pytest.raises(TypeError):
        _event(extensions=[("x-multiagent-prompt_version", "v3")])


def test_extensions_are_copied_not_aliased() -> None:
    extensions: dict[str, Any] = {"x-multiagent-prompt_version": "v3"}
    step = _event(extensions=extensions)
    extensions["x-multiagent-prompt_version"] = "v4"

    assert step.extensions == {"x-multiagent-prompt_version": "v3"}


# ---------------------------------------------------------------------------
# Caller errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_id", ["", None, 5])
def test_malformed_run_id_is_rejected(run_id: Any) -> None:
    with pytest.raises(InvalidIdentifierError):
        _event(run_id=run_id)


# ---------------------------------------------------------------------------
# QC reconciliation provenance -> derivation steps
# ---------------------------------------------------------------------------


def _qc(provenance: Any, **kwargs: Any) -> list[DerivationStep]:
    kwargs.setdefault("source_id", _SOURCE_ID)
    kwargs.setdefault("run_id", _RUN_ID)
    return derivations_from_qc_provenance(provenance, **kwargs)


def test_qc_provenance_yields_one_deterministic_reconcile_step() -> None:
    steps = _qc(_qc_provenance())

    assert len(steps) == 1
    step = steps[0]
    assert step.stage == "quality_control"
    assert step.derivation_kind == "reconcile"
    assert step.determinism == "deterministic"
    assert step.model_id is None
    assert step.input_ids == (_SOURCE_ID,)
    assert step.output_ids == ()
    assert step.run_id == _RUN_ID
    assert step.privacy is UNKNOWN_PRIVACY
    assert step.node_id == derive_derivation_id("quality_control", "reconcile", [_SOURCE_ID], [])


def test_qc_provenance_keys_are_preserved_under_the_qc_namespace() -> None:
    provenance = _qc_provenance()

    step = _qc(provenance)[0]

    assert QC_EXTENSION_NAMESPACE == "qc"
    assert step.extensions == {f"x-qc-{key}": value for key, value in provenance.items()}
    assert step.extensions["x-qc-primary_branch_source"] == "grobid"
    assert step.extensions["x-qc-adjudication_decisions"]["primary_extractor"] == "grobid"
    assert json.loads(json.dumps(dataclasses.asdict(step)))["extensions"] == step.extensions


def test_qc_provenance_without_branch_role_keys_is_tolerated() -> None:
    # reconcile() alone (no run_quality_control branch-role keys), single-branch run.
    provenance = {
        "primary_artifact_id": "",
        "secondary_artifact_id": "",
        "primary_observation": {},
        "secondary_observation": {},
        "investigator_object": {},
        "adjudication_decisions": {},
    }

    step = _qc(provenance)[0]

    assert step.derivation_kind == "reconcile"
    assert set(step.extensions) == {f"x-qc-{key}" for key in provenance}


def test_qc_provenance_unknown_conforming_keys_are_preserved() -> None:
    step = _qc(_qc_provenance(new_backend_note={"pages": [1, 2]}))[0]

    assert step.extensions["x-qc-new_backend_note"] == {"pages": [1, 2]}


def test_qc_provenance_unnamespaceable_keys_are_preserved_not_dropped(adapter_log: _ListHandler) -> None:
    step = _qc(_qc_provenance(**{"odd key!": 1}) | {3: "three"})[0]

    assert step.extensions["x-qc-unnamespaced"] == {"odd key!": 1, "3": "three"}
    assert "x-qc-primary_branch_source" in step.extensions
    assert [r for r in adapter_log.records if r.levelno == logging.WARNING]


def test_empty_qc_provenance_still_yields_the_reconcile_step() -> None:
    step = _qc({})[0]

    assert step.input_ids == (_SOURCE_ID,)
    assert step.extensions == {}


def test_qc_provenance_is_not_mutated() -> None:
    provenance = _qc_provenance()
    before = json.dumps(provenance, sort_keys=True)

    _qc(provenance)

    assert json.dumps(provenance, sort_keys=True) == before


def test_qc_provenance_conversion_is_repeatable() -> None:
    assert _qc(_qc_provenance()) == _qc(_qc_provenance())


def test_qc_steps_for_two_documents_have_distinct_ids() -> None:
    other = "src:" + "b" * 64

    assert _qc(_qc_provenance())[0].node_id != _qc(_qc_provenance(), source_id=other)[0].node_id


@pytest.mark.parametrize("provenance", [None, "provenance", [("primary_artifact_id", "")]])
def test_qc_provenance_must_be_a_mapping(provenance: Any) -> None:
    with pytest.raises(TypeError):
        _qc(provenance)


@pytest.mark.parametrize("source_id", ["paper_a", "", None, "src:"])
def test_qc_malformed_source_id_is_rejected(source_id: Any) -> None:
    with pytest.raises(InvalidIdentifierError):
        _qc(_qc_provenance(), source_id=source_id)


@pytest.mark.parametrize("run_id", ["", None])
def test_qc_malformed_run_id_is_rejected(run_id: Any) -> None:
    with pytest.raises(InvalidIdentifierError):
        _qc(_qc_provenance(), run_id=run_id)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def adapter_log() -> Iterator[_ListHandler]:
    """Capture records directly on the adapter's logger (``caplog`` misses them once logging is configured)."""
    handler = _ListHandler()
    adapter_logger = derivation_adapter.logger
    previous_level = adapter_logger.level
    adapter_logger.addHandler(handler)
    adapter_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        adapter_logger.removeHandler(handler)
        adapter_logger.setLevel(previous_level)


def test_clean_qc_conversion_logs_no_warning(adapter_log: _ListHandler) -> None:
    _qc(_qc_provenance())

    assert not [r for r in adapter_log.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# Outputs feed the recorder unchanged
# ---------------------------------------------------------------------------


def test_steps_record_into_the_recorder() -> None:
    run = RunIdentity(
        run_id=_RUN_ID,
        config_fingerprint="cfg",
        schema_fingerprint="schema",
        model_ids=(_MODEL,),
        document_fingerprints={},
        created_at="2026-01-01T00:00:00Z",
    )
    recorder = ProvenanceRecorder(run)
    steps = [
        *_qc(_qc_provenance()),
        _event("metadata_prefill", "prefill", inputs=[], outputs=[_C1]),
        _event("extraction_chunk", "extract", inputs=[_S1], outputs=[_C2], model_id=_MODEL),
        _event("synthesis", "synthesize", inputs=[_C1, _C2], outputs=[_C3], model_id=_MODEL),
    ]
    ids = [recorder.record_derivation(step) for step in steps]

    snapshot = recorder.snapshot()
    assert ids == [step.node_id for step in steps]
    assert set(snapshot.nodes) == set(ids)
    assert not snapshot.findings


# ---------------------------------------------------------------------------
# Module boundary: re-export, imports, no direct hashing
# ---------------------------------------------------------------------------


def test_adapters_package_re_exports_the_derivation_adapter() -> None:
    assert adapters.derivation_from_stage_event is derivation_from_stage_event
    assert adapters.derivations_from_qc_provenance is derivations_from_qc_provenance


def _imported_modules() -> set[str]:
    tree = ast.parse(Path(derivation_adapter.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_module_imports_only_stdlib_provenance_and_logging() -> None:
    allowed_provenance = {
        "provenance.models",
        "provenance.identity",
        "provenance.contracts",
        "provenance.privacy_carrier",
    }
    for module in _imported_modules():
        root = module.split(".")[0]
        if root == "provenance":
            assert module in allowed_provenance, module
        elif root == "utils":
            assert module == "utils.logging_utils", module
        else:
            assert root in {"__future__", "typing", "collections"}, module


def test_module_imports_no_hashing_library() -> None:
    hashing = {"hashlib", "hmac", "zlib", "binascii", "secrets", "uuid"}
    assert not {module.split(".")[0] for module in _imported_modules()} & hashing
