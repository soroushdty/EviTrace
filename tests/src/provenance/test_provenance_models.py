"""Tests for ``provenance.models`` — record types, closed vocabularies, cross-layer types.

Covers: every closed vocabulary enumerated member-by-member (7.1, 7.2, 4.1, 4.3,
5.1, 6.2, 10.1); one instance of every record type and every cross-layer type,
each frozen, hashable and equal by value (1.1, 3.2); every node embedding the
privacy carrier (9.1); the namespaced extension-key convention enforced at
construction (10.4); and a default severity for every finding code the design
names.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle
from typing import Any, get_args

import pytest

import provenance
from provenance import models
from provenance.models import (
    DEFAULT_SEVERITIES,
    AnchorPrecision,
    ArtifactCompleteness,
    ChainValidationReport,
    ClaimNode,
    ClaimOrigin,
    ClaimSupport,
    CompletenessReport,
    CompletenessState,
    DerivationKind,
    DerivationStep,
    Determinism,
    EdgeKind,
    EvidenceKind,
    EvidenceNode,
    MissingSegment,
    NodeKind,
    ProvenanceEdge,
    ProvenanceNode,
    RecordedRun,
    RunIdentity,
    Severity,
    SourceAnchor,
    SourceNode,
    StageContract,
    ValidationFinding,
    ValidationRecord,
)
from provenance.privacy_carrier import UNKNOWN_PRIVACY, PrivacyCarrier

_RUN = "run:0123456789abcdef"
_SRC = "src:aaaabbbbccccdddd"
_EXT = {"x-multiagent-prompt_version": "v3"}


# ---------------------------------------------------------------------------
# Closed vocabularies (7.1, 7.2, 4.1, 4.3, 5.1, 6.2, 10.1)
# ---------------------------------------------------------------------------

_VOCABULARIES: dict[str, tuple[Any, tuple[str, ...]]] = {
    "NodeKind": (NodeKind, ("source", "evidence", "claim", "derivation", "validation")),
    "EdgeKind": (
        EdgeKind,
        (
            "cites_evidence",
            "anchored_in",
            "derived_from",
            "has_input",
            "has_output",
            "revises",
            "validates",
            "belongs_to_source",
        ),
    ),
    "EvidenceKind": (EvidenceKind, ("sentence", "figure_caption", "table", "metadata")),
    "AnchorPrecision": (AnchorPrecision, ("exact", "approximate", "absent")),
    "DerivationKind": (
        DerivationKind,
        (
            "extract",
            "normalize",
            "chunk",
            "filter",
            "merge",
            "synthesize",
            "reconcile",
            "remove",
            "prefill",
            "annotate",
        ),
    ),
    "Determinism": (Determinism, ("deterministic", "probabilistic")),
    "Severity": (Severity, ("critical", "error", "warning", "info")),
    "CompletenessState": (CompletenessState, ("complete", "incomplete", "undetermined")),
    "ClaimSupport": (ClaimSupport, ("supported", "unsupported", "missing_anchor")),
    "ClaimOrigin": (ClaimOrigin, ("model", "prefill", "human", "import")),
}


@pytest.mark.parametrize("name", sorted(_VOCABULARIES))
def test_vocabulary_members_are_exactly_the_declared_set(name: str) -> None:
    vocabulary, expected = _VOCABULARIES[name]
    assert get_args(vocabulary) == expected


def test_every_vocabulary_is_exported_by_models() -> None:
    for name in _VOCABULARIES:
        assert name in models.__all__


def test_node_kind_distinguishes_all_five_node_types() -> None:
    # 7.2: sources, evidence, claims, derivation steps and validation results are
    # distinct node types.
    assert len(set(get_args(NodeKind))) == 5


# ---------------------------------------------------------------------------
# Instance factories — one of every record type and every cross-layer type
# ---------------------------------------------------------------------------


def _anchor() -> SourceAnchor:
    return SourceAnchor(
        precision="exact",
        page=3,
        coords=(10.0, 20.0, 110.0, 40.0),
        section_path="Methods/Participants",
        char_span=(120, 188),
        structural_path="//s[@xml:id='s12']",
    )


def _source() -> SourceNode:
    return SourceNode(
        node_id=_SRC,
        kind="source",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        document_fingerprint="aaaabbbbccccdddd",
        source_format="pdf",
        source_label="paper_001.pdf",
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
    )


def _evidence() -> EvidenceNode:
    return EvidenceNode(
        node_id=f"{_SRC}#s12",
        kind="evidence",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={"x-routing-score": 0.5},
        local_id="s12",
        evidence_kind="sentence",
        source_id=_SRC,
        anchor=_anchor(),
        producing_backend="grobid",
        rank_score=7,
        parser_metadata={"tei_tag": "s", "spans": [1, 2]},
    )


def _claim() -> ClaimNode:
    return ClaimNode(
        node_id=f"claim:{_SRC}#f3",
        kind="claim",
        run_id=_RUN,
        privacy=PrivacyCarrier(
            state="labeled",
            label="L",
            decision="D",
            supplied_by="privacy-core",
            supplied_at="2026-10-08T00:00:00+00:00",
        ),
        extensions={},
        source_id=_SRC,
        field_index=3,
        field_name="sample_size",
        value_digest="d1g3st",
        origin="model",
        support="supported",
        cited_evidence_ids=(f"{_SRC}#s12",),
        confidence="high",
    )


def _derivation() -> DerivationStep:
    return DerivationStep(
        node_id="drv:0123456789abcdef",
        kind="derivation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions=dict(_EXT),
        stage="extraction_chunk",
        derivation_kind="extract",
        determinism="probabilistic",
        model_id="gpt-x",
        input_ids=(f"{_SRC}#s12",),
        output_ids=(f"claim:{_SRC}#f3",),
    )


def _validation_record() -> ValidationRecord:
    return ValidationRecord(
        node_id=f"val:{_RUN}:chain",
        kind="validation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        check_name="chain",
        finding_ids=("f-1",),
        completed=True,
    )


def _edge() -> ProvenanceEdge:
    return ProvenanceEdge(
        edge_id=f"cites_evidence:claim:{_SRC}#f3->{_SRC}#s12",
        kind="cites_evidence",
        from_id=f"claim:{_SRC}#f3",
        to_id=f"{_SRC}#s12",
        attributes={"rank": 1},
    )


def _run_identity() -> RunIdentity:
    return RunIdentity(
        run_id=_RUN,
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
        document_fingerprints={_SRC: "aaaabbbbccccdddd"},
        created_at="2026-10-08T00:00:00+00:00",
    )


def _finding() -> ValidationFinding:
    return ValidationFinding(
        finding_id="f-1",
        code="dangling_reference",
        severity="critical",
        node_id=f"claim:{_SRC}#f3",
        edge_id=None,
        message="cited evidence does not exist",
        details={"reference": f"{_SRC}#missing"},
    )


def _missing_segment() -> MissingSegment:
    return MissingSegment(
        stage="evidence_index",
        record_kind="evidence",
        reason="invalid local_id",
        affected_ids=(f"{_SRC}#bad",),
    )


def _stage_contract() -> StageContract:
    return StageContract(stage="evidence_index", required_record_kinds=("evidence",), scope="document")


def _artifact_completeness() -> ArtifactCompleteness:
    return ArtifactCompleteness(
        state="incomplete",
        reason="stage emitted no evidence",
        missing_record_kinds=("evidence",),
        responsible_stages=("evidence_index",),
    )


def _recorded_run() -> RecordedRun:
    source = _source()
    return RecordedRun(
        run=_run_identity(),
        nodes={source.node_id: source},
        edges=(_edge(),),
        findings=(_finding(),),
        stage_status={"evidence_index": "complete", "extraction_chunk": "failed"},
        stage_failure_reasons={"extraction_chunk": "timeout"},
        document_stage_status={source.node_id: {"synthesis": "failed"}},
        document_stage_failure_reasons={source.node_id: {"synthesis": ("timeout", "invalid JSON")}},
    )


def _chain_report() -> ChainValidationReport:
    return ChainValidationReport(
        completed=True,
        incompletion_reason=None,
        findings=(_finding(),),
        counts_by_severity={"critical": 1},
    )


def _completeness_report() -> CompletenessReport:
    return CompletenessReport(
        run=_artifact_completeness(),
        documents={_SRC: _artifact_completeness()},
        claims={f"claim:{_SRC}#f3": ArtifactCompleteness("complete", None, (), ())},
    )


_FACTORIES = {
    "SourceAnchor": _anchor,
    "SourceNode": _source,
    "EvidenceNode": _evidence,
    "ClaimNode": _claim,
    "DerivationStep": _derivation,
    "ValidationRecord": _validation_record,
    "ProvenanceEdge": _edge,
    "RunIdentity": _run_identity,
    "ValidationFinding": _finding,
    "MissingSegment": _missing_segment,
    "StageContract": _stage_contract,
    "ArtifactCompleteness": _artifact_completeness,
    "RecordedRun": _recorded_run,
    "ChainValidationReport": _chain_report,
    "CompletenessReport": _completeness_report,
}

_NODE_FACTORIES = {
    "SourceNode": _source,
    "EvidenceNode": _evidence,
    "ClaimNode": _claim,
    "DerivationStep": _derivation,
    "ValidationRecord": _validation_record,
}


def _mapping_fields(instance: Any) -> list[str]:
    return [
        f.name
        for f in dataclasses.fields(instance)
        if isinstance(getattr(instance, f.name), dict)
    ]


# ---------------------------------------------------------------------------
# Every record and cross-layer type: frozen, equal by value, hashable (1.1, 3.2)
# ---------------------------------------------------------------------------


def test_factories_cover_every_dataclass_declared_in_models() -> None:
    declared = {
        name
        for name, obj in vars(models).items()
        if isinstance(obj, type) and dataclasses.is_dataclass(obj) and obj.__module__ == models.__name__
    }
    assert declared == set(_FACTORIES) | {"ProvenanceNode"}


@pytest.mark.parametrize("name", sorted(_FACTORIES))
def test_record_is_a_frozen_dataclass(name: str) -> None:
    instance = _FACTORIES[name]()
    assert type(instance).__dataclass_params__.frozen  # type: ignore[attr-defined]
    first_field = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, first_field, "changed")


@pytest.mark.parametrize("name", sorted(_FACTORIES))
def test_record_is_equal_by_value_and_hashable(name: str) -> None:
    first, second = _FACTORIES[name](), _FACTORIES[name]()
    assert first is not second
    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1


@pytest.mark.parametrize("name", sorted(_FACTORIES))
def test_differing_records_are_unequal(name: str) -> None:
    instance = _FACTORIES[name]()
    first_field = dataclasses.fields(instance)[0]
    current = getattr(instance, first_field.name)
    if isinstance(current, str):
        changed = dataclasses.replace(instance, **{first_field.name: current + "-other"})
    elif isinstance(current, bool):
        changed = dataclasses.replace(instance, **{first_field.name: not current})
    else:
        changed = dataclasses.replace(instance, **{first_field.name: None})
    assert changed != instance


@pytest.mark.parametrize("name", sorted(_FACTORIES))
def test_mapping_fields_cannot_be_mutated_after_construction(name: str) -> None:
    instance = _FACTORIES[name]()
    for field_name in _mapping_fields(instance):
        value = getattr(instance, field_name)
        with pytest.raises(TypeError):
            value["x-late-key"] = 1
        with pytest.raises(TypeError):
            value.update({"x-late-key": 1})
        with pytest.raises(TypeError):
            value.clear()


def test_mapping_fields_are_detached_from_the_caller_dict() -> None:
    supplied = {"x-routing-score": 0.5}
    node = dataclasses.replace(_evidence(), extensions=supplied)
    supplied["unnamespaced"] = 1
    assert node.extensions == {"x-routing-score": 0.5}


def test_nested_values_in_mapping_fields_are_frozen_but_equal_to_plain_values() -> None:
    node = _evidence()
    assert node.parser_metadata == {"tei_tag": "s", "spans": [1, 2]}
    with pytest.raises((TypeError, AttributeError)):
        node.parser_metadata["spans"].append(3)
    nested = dataclasses.replace(node, parser_metadata={"outer": {"inner": [1]}})
    with pytest.raises(TypeError):
        nested.parser_metadata["outer"]["inner2"] = 2
    assert hash(nested) == hash(dataclasses.replace(node, parser_metadata={"outer": {"inner": [1]}}))


@pytest.mark.parametrize("name", sorted(_FACTORIES))
def test_record_survives_copy_pickle_asdict_and_json(name: str) -> None:
    instance = _FACTORIES[name]()
    assert copy.deepcopy(instance) == instance
    assert copy.copy(instance) == instance
    assert pickle.loads(pickle.dumps(instance)) == instance
    as_dict = dataclasses.asdict(instance)
    json.dumps(as_dict)  # frozen mappings remain JSON-serializable


def test_records_expose_no_mutation_method() -> None:
    for name, factory in _FACTORIES.items():
        cls = type(factory())
        methods = {
            attr for attr in dir(cls) if not attr.startswith("_") and callable(getattr(cls, attr))
        }
        assert methods == set(), name


# ---------------------------------------------------------------------------
# Node structure: shared base fields and embedded privacy carrier (9.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_NODE_FACTORIES))
def test_every_node_type_is_a_provenance_node_with_base_fields_first(name: str) -> None:
    node = _NODE_FACTORIES[name]()
    assert isinstance(node, ProvenanceNode)
    names = [f.name for f in dataclasses.fields(node)]
    assert names[:5] == ["node_id", "kind", "run_id", "privacy", "extensions"]


@pytest.mark.parametrize("name", sorted(_NODE_FACTORIES))
def test_every_node_embeds_the_single_privacy_carrier(name: str) -> None:
    node = _NODE_FACTORIES[name]()
    assert isinstance(node.privacy, PrivacyCarrier)
    privacy_field = next(f for f in dataclasses.fields(node) if f.name == "privacy")
    assert privacy_field.type in (PrivacyCarrier, "PrivacyCarrier")


def test_models_reuses_the_privacy_carrier_rather_than_redefining_it() -> None:
    assert models.PrivacyCarrier is PrivacyCarrier
    assert models.UNKNOWN_PRIVACY is UNKNOWN_PRIVACY


def test_node_field_lists_match_the_design() -> None:
    def own(cls: type) -> list[str]:
        base = {f.name for f in dataclasses.fields(ProvenanceNode)}
        return [f.name for f in dataclasses.fields(cls) if f.name not in base]

    assert own(SourceNode) == [
        "document_fingerprint",
        "source_format",
        "source_label",
        "config_fingerprint",
        "schema_fingerprint",
        "model_ids",
    ]
    assert own(EvidenceNode) == [
        "local_id",
        "evidence_kind",
        "source_id",
        "anchor",
        "producing_backend",
        "rank_score",
        "parser_metadata",
    ]
    assert own(ClaimNode) == [
        "source_id",
        "field_index",
        "field_name",
        "value_digest",
        "origin",
        "support",
        "cited_evidence_ids",
        "confidence",
    ]
    assert own(DerivationStep) == [
        "stage",
        "derivation_kind",
        "determinism",
        "model_id",
        "input_ids",
        "output_ids",
        "removal_reason",
        "removed_ids",
    ]
    assert own(ValidationRecord) == ["check_name", "finding_ids", "completed"]


def test_source_anchor_defaults_record_nothing_but_precision() -> None:
    anchor = SourceAnchor(precision="absent", missing=("page", "coords"))
    assert anchor.page is None
    assert anchor.coords is None
    assert anchor.section_path is None
    assert anchor.char_span is None
    assert anchor.structural_path is None
    assert anchor.missing == ("page", "coords")


def test_recorded_run_per_document_stage_fields_default_to_empty() -> None:
    """Task 4.6: a snapshot built without per-document marks behaves exactly as before."""
    full = _recorded_run()
    run = RecordedRun(
        run=full.run,
        nodes=full.nodes,
        edges=full.edges,
        findings=full.findings,
        stage_status=full.stage_status,
        stage_failure_reasons=full.stage_failure_reasons,
    )
    assert run.document_stage_status == {}
    assert run.document_stage_failure_reasons == {}
    names = [field.name for field in dataclasses.fields(RecordedRun)]
    assert names[-2:] == ["document_stage_status", "document_stage_failure_reasons"]
    # The default is not a shared mutable object.
    with pytest.raises(TypeError):
        run.document_stage_status["x"] = {}  # type: ignore[index]
    assert full.document_stage_failure_reasons[_SRC]["synthesis"] == ("timeout", "invalid JSON")


def test_derivation_step_removal_fields_default_to_empty() -> None:
    step = _derivation()
    assert step.removal_reason is None
    assert step.removed_ids == ()


# ---------------------------------------------------------------------------
# Namespaced extension keys (10.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "x-multiagent-prompt_version",
        "x-multiagent-replaced_field_indices",
        "x-routing-score",
        "x-grobid2-teiVersion",
    ],
)
def test_namespaced_extension_key_is_accepted_and_preserved(key: str) -> None:
    node = dataclasses.replace(_derivation(), extensions={key: [1, "two"]})
    assert node.extensions == {key: [1, "two"]}


@pytest.mark.parametrize(
    "key",
    [
        "prompt_version",  # bare core-looking field
        "x-prompt_version",  # no namespace segment
        "x--prompt_version",  # empty namespace
        "x-multiagent-",  # empty field
        "X-multiagent-prompt_version",  # wrong prefix case
        "y-multiagent-prompt_version",  # wrong prefix
        "x-Multi-field",  # namespace must be lower-case
        "x-multi agent-field",  # whitespace
        "",
    ],
)
@pytest.mark.parametrize("name", sorted(_NODE_FACTORIES))
def test_non_namespaced_extension_key_is_rejected_at_construction(name: str, key: str) -> None:
    node = _NODE_FACTORIES[name]()
    with pytest.raises(ValueError, match="extension"):
        dataclasses.replace(node, extensions={"x-ok-field": 1, key: "v"})


def test_non_string_extension_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="extension"):
        dataclasses.replace(_source(), extensions={1: "v"})


def test_extension_check_applies_to_direct_base_construction() -> None:
    with pytest.raises(ValueError, match="extension"):
        ProvenanceNode(
            node_id="n", kind="source", run_id=_RUN, privacy=UNKNOWN_PRIVACY, extensions={"bare": 1}
        )


# ---------------------------------------------------------------------------
# Default severity table
# ---------------------------------------------------------------------------

# Every finding code named anywhere in design.md: the ChainValidator check set
# and default table, the privacy-carrier rejection path, and the recorder's
# duplicate-identity finding.
_DESIGN_FINDING_CODES = {
    "dangling_reference": "critical",
    "graph_cycle": "critical",
    "invalid_identifier": "critical",
    "source_not_represented": "error",
    "evidence_out_of_run_context": "error",
    "inconsistent_derivation": "error",
    "privacy_carrier_invalid": "error",
    "unsupported_claim": "warning",
    "missing_anchor": "warning",
    "duplicate_reference": "warning",
    "ambiguous_reference": "warning",
    "orphan_node": "info",
}


@pytest.mark.parametrize("code", sorted(_DESIGN_FINDING_CODES))
def test_default_severity_table_matches_the_design_table(code: str) -> None:
    assert DEFAULT_SEVERITIES[code] == _DESIGN_FINDING_CODES[code]


def test_default_severity_table_covers_the_recorder_duplicate_identity_finding() -> None:
    assert DEFAULT_SEVERITIES["duplicate_identity"] in get_args(Severity)


def test_default_severity_table_has_no_undeclared_codes() -> None:
    assert set(DEFAULT_SEVERITIES) == set(_DESIGN_FINDING_CODES) | {"duplicate_identity"}


def test_every_default_severity_is_a_severity_member() -> None:
    assert set(DEFAULT_SEVERITIES.values()) <= set(get_args(Severity))


def test_default_severity_table_is_immutable() -> None:
    with pytest.raises(TypeError):
        DEFAULT_SEVERITIES["dangling_reference"] = "info"  # type: ignore[index]
    assert DEFAULT_SEVERITIES["dangling_reference"] == "critical"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_package_reexports_every_public_model() -> None:
    for name in models.__all__:
        assert getattr(provenance, name) is getattr(models, name)
        assert name in provenance.__all__
