"""Tests for ``provenance.validation`` — reference and structural chain-validation checks.

Covers task 4.2 of provenance-core (design "Analysis Layer → ChainValidator"):

- one test per finding code of this group — ``dangling_reference`` (8.1),
  ``source_not_represented`` (8.2), ``evidence_out_of_run_context`` (8.3),
  ``missing_anchor`` and ``invalid_identifier`` (8.4), ``duplicate_reference``,
  ``ambiguous_reference`` and ``unsupported_claim`` (5.3, 5.4) and
  ``privacy_carrier_invalid`` (9.6) — each building the minimal graph that
  triggers it;
- the not-completed path: a check that cannot run yields ``completed=False``
  with a recorded reason, never a valid run (8.6);
- the ``recorded_findings`` merge: recorder / issue-sink / claim-adapter
  findings reach the report, are de-duplicated by ``finding_id`` and by the
  reference-problem key, and are counted per severity (5.4);
- the module declares no dataclass and validation is read-only.

Task 4.3 adds the topology checks — ``graph_cycle`` (iterative, over
derivation and revision edges), ``orphan_node`` and ``inconsistent_derivation``
(8.4) — and the severity policy: the documented default severity per code,
per-code overrides from configuration, and the deterministic
``(severity_rank, code, node_id)`` order (8.5).

Synthetic identifiers only.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import sys
import typing
from collections import Counter
from pathlib import Path

import pytest

from provenance import validation as validation_module
from provenance.adapters import claim_nodes_from_fields
from provenance.graph import (
    PROVENANCE_SCHEMA_ID,
    PROVENANCE_SCHEMA_VERSION,
    ProvenanceGraph,
    attach_validation,
    build_graph,
)
from provenance.identity import (
    derive_claim_id,
    derive_derivation_id,
    derive_source_id,
    format_evidence_id,
)
from provenance.models import (
    DEFAULT_SEVERITIES,
    UNKNOWN_PRIVACY,
    ChainValidationReport,
    ClaimNode,
    DerivationStep,
    EvidenceNode,
    PrivacyCarrier,
    ProvenanceEdge,
    ProvenanceNode,
    RecordedRun,
    RunIdentity,
    SourceAnchor,
    SourceNode,
    ValidationFinding,
)
from provenance.recorder import ProvenanceRecorder
from provenance.validation import validate_chain

_RUN = "run:0123456789abcdef"
_OTHER_RUN = "run:fedcba9876543210"
_FP = "aaaabbbbccccdddd"
_SRC = derive_source_id(_FP)
_FP2 = "eeeeffff00001111"
_SRC2 = derive_source_id(_FP2)
_CLAIM = derive_claim_id(_SRC, 7)
_E1 = format_evidence_id(_SRC, "e1")
_E2 = format_evidence_id(_SRC, "e2")
_MODULE_PATH = Path(validation_module.__file__)


# ---------------------------------------------------------------------------
# Minimal record builders
# ---------------------------------------------------------------------------


def _run_identity() -> RunIdentity:
    return RunIdentity(
        run_id=_RUN,
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
        document_fingerprints={_SRC: _FP},
        created_at="2026-10-08T00:00:00+00:00",
    )


def _source(fp: str = _FP, *, privacy: PrivacyCarrier = UNKNOWN_PRIVACY) -> SourceNode:
    return SourceNode(
        node_id=derive_source_id(fp),
        kind="source",
        run_id=_RUN,
        privacy=privacy,
        extensions={},
        document_fingerprint=fp,
        source_format="pdf",
        source_label="lorem.pdf",
        config_fingerprint="cfg123",
        schema_fingerprint="sch456",
        model_ids=("gpt-x",),
    )


def _anchor(precision: str = "exact") -> SourceAnchor:
    if precision == "exact":
        return SourceAnchor(precision="exact", page=1, coords=(1.0, 2.0, 3.0, 4.0), section_path="Methods")
    if precision == "approximate":
        return SourceAnchor(precision="approximate", page=1, missing=("coords",))
    return SourceAnchor(precision="absent", missing=("page", "coords", "section_path", "structural_path"))


def _evidence(
    local_id: str,
    *,
    source_id: str = _SRC,
    run_id: str = _RUN,
    precision: str = "exact",
    node_id: str | None = None,
    privacy: PrivacyCarrier = UNKNOWN_PRIVACY,
) -> EvidenceNode:
    return EvidenceNode(
        node_id=node_id if node_id is not None else format_evidence_id(source_id, local_id),
        kind="evidence",
        run_id=run_id,
        privacy=privacy,
        extensions={},
        local_id=local_id,
        evidence_kind="sentence",
        source_id=source_id,
        anchor=_anchor(precision),
        producing_backend="grobid",
        rank_score=3,
        parser_metadata={},
    )


def _claim(
    cited: tuple[str, ...] = (_E1,),
    *,
    field_index: int = 7,
    source_id: str = _SRC,
    support: str | None = None,
    node_id: str | None = None,
    privacy: PrivacyCarrier = UNKNOWN_PRIVACY,
) -> ClaimNode:
    return ClaimNode(
        node_id=node_id if node_id is not None else derive_claim_id(source_id, field_index),
        kind="claim",
        run_id=_RUN,
        privacy=privacy,
        extensions={},
        source_id=source_id,
        field_index=field_index,
        field_name="sample_size",
        value_digest="0123456789abcdef",
        origin="model",
        support=support if support is not None else ("supported" if cited else "unsupported"),
        cited_evidence_ids=cited,
        confidence="high",
    )


def _edge(kind: str, from_id: str, to_id: str) -> ProvenanceEdge:
    return ProvenanceEdge(edge_id=f"{kind}:{from_id}->{to_id}", kind=kind, from_id=from_id, to_id=to_id, attributes={})


def _cites(claim: ClaimNode) -> list[ProvenanceEdge]:
    return [_edge("cites_evidence", claim.node_id, target) for target in dict.fromkeys(claim.cited_evidence_ids)]


def _step(
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
    *,
    stage: str = "extraction_chunk",
    kind: str = "extract",
    node_id: str | None = None,
) -> DerivationStep:
    return DerivationStep(
        node_id=node_id if node_id is not None else derive_derivation_id(stage, kind, inputs, outputs),
        kind="derivation",
        run_id=_RUN,
        privacy=UNKNOWN_PRIVACY,
        extensions={},
        stage=stage,
        derivation_kind=kind,
        determinism="probabilistic",
        model_id="gpt-x",
        input_ids=inputs,
        output_ids=outputs,
    )


def _graph(*nodes: ProvenanceNode, edges: list[ProvenanceEdge] | tuple[ProvenanceEdge, ...] = ()) -> ProvenanceGraph:
    recorded = RecordedRun(
        run=_run_identity(),
        nodes={node.node_id: node for node in nodes},
        edges=tuple(edges),
        findings=(),
        stage_status={},
        stage_failure_reasons={},
    )
    return build_graph(recorded)


def _clean_nodes() -> tuple[list[ProvenanceNode], list[ProvenanceEdge]]:
    claim = _claim((_E1, _E2))
    nodes: list[ProvenanceNode] = [
        _source(),
        _evidence("e1"),
        _evidence("e2", precision="approximate"),
        claim,
        _step((_E1, _E2), (claim.node_id,)),
    ]
    return nodes, _cites(claim)


def _clean_graph() -> ProvenanceGraph:
    nodes, edges = _clean_nodes()
    return _graph(*nodes, edges=edges)


def _codes(report: ChainValidationReport) -> list[str]:
    return [finding.code for finding in report.findings]


def _only(report: ChainValidationReport, code: str) -> list[ValidationFinding]:
    return [finding for finding in report.findings if finding.code == code]


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def test_clean_graph_validates_with_no_findings() -> None:
    report = validate_chain(_clean_graph())

    assert isinstance(report, ChainValidationReport)
    assert report.completed is True
    assert report.incompletion_reason is None
    assert report.findings == ()
    assert dict(report.counts_by_severity) == {}


def test_graph_without_privacy_carriers_still_validates() -> None:
    """9.7: every node carrying UNKNOWN_PRIVACY yields no privacy finding."""
    graph = _clean_graph()
    assert all(node.privacy == UNKNOWN_PRIVACY for node in graph.nodes.values())

    report = validate_chain(graph)

    assert report.completed is True
    assert "privacy_carrier_invalid" not in _codes(report)


# ---------------------------------------------------------------------------
# One test per finding code
# ---------------------------------------------------------------------------


def test_dangling_reference_for_edge_to_a_node_absent_from_the_graph() -> None:
    """8.1: a derivation step outputs an identifier that is no node."""
    nodes, edges = _clean_nodes()
    ghost = derive_claim_id(_SRC, 99)
    step = _step((_E1,), (ghost,))
    graph = _graph(*nodes, step, edges=edges)

    report = validate_chain(graph)

    dangling = _only(report, "dangling_reference")
    assert len(dangling) == 1
    finding = dangling[0]
    assert finding.severity == DEFAULT_SEVERITIES["dangling_reference"]
    assert finding.node_id == step.node_id
    assert finding.edge_id == f"has_output:{step.node_id}->{ghost}"
    assert finding.details["reference"] == ghost
    assert report.completed is True


def test_dangling_reference_for_claim_citing_unknown_evidence() -> None:
    """8.1 / 5.3: the claim cites an evidence ID with no node; the claim is retained."""
    ghost = format_evidence_id(_SRC, "zz9")
    claim = _claim((_E1, ghost))
    graph = _graph(_source(), _evidence("e1"), claim, edges=_cites(claim))

    report = validate_chain(graph)

    dangling = _only(report, "dangling_reference")
    assert [(f.node_id, f.edge_id, f.details["reference"]) for f in dangling] == [
        (claim.node_id, f"cites_evidence:{claim.node_id}->{ghost}", ghost)
    ]
    assert claim.node_id in graph.nodes


def test_dangling_reference_skips_debris_of_an_unmaterialized_record() -> None:
    """A recorded edge leaving a record that became a missing segment is not re-reported."""
    broken = dataclasses.replace(_claim((_E1,)), kind="evidence")  # kind disagrees with type → missing segment
    graph = _graph(_source(), _evidence("e1"), broken, edges=_cites(_claim((_E1,))))
    assert graph.missing_segments
    assert _CLAIM not in graph.nodes

    report = validate_chain(graph)

    assert "dangling_reference" not in _codes(report)


def test_source_not_represented_for_evidence_of_an_absent_source() -> None:
    """8.2: the evidence's source is not in the graph; reported once, not also as dangling."""
    orphan = _evidence("e9", source_id=_SRC2)
    graph = _graph(_source(), orphan)

    report = validate_chain(graph)

    findings = _only(report, "source_not_represented")
    assert [(f.node_id, f.details["reference"]) for f in findings] == [(orphan.node_id, _SRC2)]
    assert findings[0].edge_id == f"belongs_to_source:{orphan.node_id}->{_SRC2}"
    assert findings[0].severity == DEFAULT_SEVERITIES["source_not_represented"]
    assert "dangling_reference" not in _codes(report)


def test_evidence_out_of_run_context_for_evidence_of_another_run() -> None:
    """8.3: the cited evidence node exists but belongs to a different run."""
    foreign = _evidence("e1", run_id=_OTHER_RUN)
    claim = _claim((_E1,))
    graph = _graph(_source(), foreign, claim, edges=_cites(claim))

    report = validate_chain(graph)

    findings = _only(report, "evidence_out_of_run_context")
    assert [(f.node_id, f.details["reference"]) for f in findings] == [(claim.node_id, _E1)]
    assert "foreign_run" in findings[0].details["reasons"]
    assert findings[0].severity == DEFAULT_SEVERITIES["evidence_out_of_run_context"]


def test_evidence_out_of_run_context_for_evidence_of_another_source() -> None:
    """8.3: a claim cites evidence belonging to a different document of the run."""
    foreign_id = format_evidence_id(_SRC2, "e1")
    claim = _claim((foreign_id,))
    graph = _graph(_source(), _source(_FP2), _evidence("e1", source_id=_SRC2), claim, edges=_cites(claim))

    report = validate_chain(graph)

    findings = _only(report, "evidence_out_of_run_context")
    assert len(findings) == 1
    assert findings[0].details["reasons"] == ["foreign_source"]


def test_missing_anchor_for_evidence_with_absent_anchor() -> None:
    """8.4: an evidence node whose anchor precision is absent; approximate is not reported."""
    anchorless = _evidence("e3", precision="absent")
    graph = _graph(_source(), _evidence("e2", precision="approximate"), anchorless)

    report = validate_chain(graph)

    findings = _only(report, "missing_anchor")
    assert [f.node_id for f in findings] == [anchorless.node_id]
    assert findings[0].severity == DEFAULT_SEVERITIES["missing_anchor"]
    assert list(findings[0].details["missing"]) == list(anchorless.anchor.missing)


def test_invalid_identifier_for_nodes_and_edges() -> None:
    """8.4: identifiers that do not match their record's content are reported."""
    bad_evidence = _evidence("e2", node_id=format_evidence_id(_SRC, "e-other"))
    bad_step = _step((_E1,), (_CLAIM,), node_id="drv:0000000000000000")
    claim = _claim((_E1,))
    bad_edge = ProvenanceEdge(
        edge_id="cites:wrong", kind="cites_evidence", from_id=claim.node_id, to_id=_E1, attributes={}
    )
    graph = _graph(_source(), _evidence("e1"), bad_evidence, claim, bad_step, edges=[bad_edge])

    report = validate_chain(graph)

    findings = _only(report, "invalid_identifier")
    assert {(f.node_id, f.edge_id) for f in findings} == {
        (bad_evidence.node_id, None),
        (bad_step.node_id, None),
        (None, "cites:wrong"),
    }
    assert all(f.severity == DEFAULT_SEVERITIES["invalid_identifier"] for f in findings)
    assert all(f.details["reason"] for f in findings)


def test_invalid_identifier_accepts_revision_suffixed_claim_ids() -> None:
    revised = _claim((_E1,), node_id=derive_claim_id(_SRC, 7, 2))
    graph = _graph(_source(), _evidence("e1"), revised, edges=_cites(revised))

    report = validate_chain(graph)

    assert "invalid_identifier" not in _codes(report)


def test_privacy_carrier_invalid_for_rejected_and_malformed_carriers() -> None:
    """9.6: a rejected carrier, and a labeled carrier of the wrong structure, are findings; nodes kept."""
    rejected = PrivacyCarrier(state="rejected", supplied_by="privacy-core", supplied_at="t", rejection_reason="bad")
    malformed = PrivacyCarrier(state="labeled", label=3, supplied_by="privacy-core", supplied_at="t")  # type: ignore[arg-type]
    ok = PrivacyCarrier(state="labeled", label="opaque", decision=None, supplied_by="privacy-core", supplied_at="t")
    e1 = _evidence("e1", privacy=rejected)
    e2 = _evidence("e2", privacy=malformed)
    graph = _graph(_source(privacy=ok), e1, e2)

    report = validate_chain(graph)

    findings = _only(report, "privacy_carrier_invalid")
    assert sorted(f.node_id for f in findings) == sorted([e1.node_id, e2.node_id])
    assert all(f.severity == DEFAULT_SEVERITIES["privacy_carrier_invalid"] for f in findings)
    assert all(f.details["reason"] for f in findings)
    assert e1.node_id in graph.nodes and e2.node_id in graph.nodes


def test_duplicate_reference_for_claim_citing_one_evidence_twice() -> None:
    claim = _claim((_E1, _E1))
    graph = _graph(_source(), _evidence("e1"), claim, edges=_cites(claim))

    report = validate_chain(graph)

    findings = _only(report, "duplicate_reference")
    assert [(f.node_id, f.details["reference"]) for f in findings] == [(claim.node_id, _E1)]
    assert list(findings[0].details["positions"]) == [0, 1]
    assert findings[0].severity == DEFAULT_SEVERITIES["duplicate_reference"]


def test_ambiguous_reference_for_case_variant_of_a_known_evidence_id() -> None:
    """5.4: an inexact reference that may mean a known node is ambiguous, not dangling."""
    variant = format_evidence_id(_SRC, "E1")
    claim = _claim((variant,))
    graph = _graph(_source(), _evidence("e1"), claim, edges=_cites(claim))

    report = validate_chain(graph)

    findings = _only(report, "ambiguous_reference")
    assert [(f.node_id, f.details["reference"]) for f in findings] == [(claim.node_id, variant)]
    assert list(findings[0].details["candidates"]) == [_E1]
    assert findings[0].severity == DEFAULT_SEVERITIES["ambiguous_reference"]
    assert "dangling_reference" not in _codes(report)


def test_unsupported_claim_for_claim_without_references() -> None:
    """5.2 / 5.3: a claim citing nothing is reported unsupported and retained."""
    bare = _claim((), field_index=8)
    graph = _graph(_source(), _evidence("e1"), bare)

    report = validate_chain(graph)

    findings = _only(report, "unsupported_claim")
    assert [f.node_id for f in findings] == [bare.node_id]
    assert findings[0].severity == DEFAULT_SEVERITIES["unsupported_claim"]


# ---------------------------------------------------------------------------
# Not-completed path (8.6)
# ---------------------------------------------------------------------------


def test_unavailable_node_index_reports_unvalidated_with_reason() -> None:
    graph = _clean_graph()
    object.__setattr__(graph, "nodes", None)  # simulate a graph whose node index is unavailable

    report = validate_chain(graph)

    assert report.completed is False
    assert isinstance(report.incompletion_reason, str) and report.incompletion_reason


def test_non_graph_input_reports_unvalidated_without_raising() -> None:
    report = validate_chain(object())  # type: ignore[arg-type]

    assert report.completed is False
    assert report.incompletion_reason


def test_a_check_that_cannot_run_marks_the_run_unvalidated_and_keeps_other_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken(_ctx: object) -> list[ValidationFinding]:
        raise RuntimeError("index exploded")

    monkeypatch.setattr(validation_module, "_CHECKS", (*validation_module._CHECKS, ("broken_check", _broken)))
    bare = _claim((), field_index=8)
    graph = _graph(_source(), bare)

    report = validate_chain(graph)

    assert report.completed is False
    assert "broken_check" in report.incompletion_reason
    assert "index exploded" in report.incompletion_reason
    assert "unsupported_claim" in _codes(report)


# ---------------------------------------------------------------------------
# recorded_findings merge (5.4)
# ---------------------------------------------------------------------------


def test_recorder_findings_reach_the_report_and_are_counted() -> None:
    recorder = ProvenanceRecorder(_run_identity())
    recorder.record_source(_source())
    recorder.record_evidence([_evidence("e1")])
    recorder.record_evidence([_evidence("e1", precision="approximate")])  # duplicate_identity
    recorded = recorder.snapshot()
    assert [f.code for f in recorded.findings] == ["duplicate_identity"]

    report = validate_chain(build_graph(recorded), recorded_findings=recorded.findings)

    assert report.completed is True
    assert recorded.findings[0] in report.findings
    assert report.counts_by_severity == {"warning": 1}


def test_recorded_findings_are_deduplicated_by_finding_id() -> None:
    finding = ValidationFinding(
        finding_id="finding:duplicate_identity:x:1",
        code="duplicate_identity",
        severity="warning",
        node_id=None,
        edge_id=None,
        message="m",
        details={},
    )

    report = validate_chain(_clean_graph(), recorded_findings=(finding, finding))

    assert report.findings == (finding,)
    assert report.counts_by_severity == {"warning": 1}


def test_issue_sink_and_claim_adapter_report_one_reference_problem_once() -> None:
    """The same unknown ``loc`` reported by the sink (full ID) and the adapter (local ID) merges."""
    evidence = _evidence("e1")
    claims, edges, adapter_findings = claim_nodes_from_fields(
        [{"field_index": 7, "field_name": "n", "extracted_value": "1", "location": ["e1", "s99"]}],
        source_id=_SRC,
        run_id=_RUN,
        known_evidence_ids={evidence.node_id},
        origin="model",
        evidence_anchors={evidence.node_id: evidence.anchor},
    )
    assert [f.code for f in adapter_findings] == ["dangling_reference"]

    recorder = ProvenanceRecorder(_run_identity())
    recorder.record_source(_source())
    recorder.record_evidence([evidence])
    recorder.record_claim(claims[0], edges)
    for finding in adapter_findings:
        recorder.record_issue(finding)
    recorder.issue_sink()(
        "dangling_reference",
        "loc s99 is not a known evidence id",
        {"node_id": claims[0].node_id, "reference": format_evidence_id(_SRC, "s99")},
    )
    recorded = recorder.snapshot()
    assert len(recorded.findings) == 2

    report = validate_chain(build_graph(recorded), recorded_findings=recorded.findings)

    dangling = _only(report, "dangling_reference")
    assert len(dangling) == 1
    kept = dangling[0]
    other_ids = sorted(f.finding_id for f in recorded.findings if f.finding_id != kept.finding_id)
    assert list(kept.details["merged_finding_ids"]) == other_ids
    assert report.counts_by_severity == {"critical": 1}


def test_graph_and_recorded_findings_for_one_problem_merge() -> None:
    claim = _claim((_E1, _E1))
    graph = _graph(_source(), _evidence("e1"), claim, edges=_cites(claim))
    recorded = ValidationFinding(
        finding_id=f"finding:duplicate_reference:{claim.node_id}:loc[1]",
        code="duplicate_reference",
        severity="warning",
        node_id=claim.node_id,
        edge_id=None,
        message="cited twice",
        details={"reference": "e1", "positions": [0, 1], "reference_kind": "evidence"},
    )

    report = validate_chain(graph, recorded_findings=[recorded])

    assert len(_only(report, "duplicate_reference")) == 1
    assert report.counts_by_severity == {"warning": 1}


def test_distinct_reference_problems_are_not_merged() -> None:
    claim_a = derive_claim_id(_SRC, 1)
    claim_b = derive_claim_id(_SRC, 2)
    findings = [
        ValidationFinding(
            finding_id=f"finding:dangling_reference:{claim}:loc[{position}]",
            code="dangling_reference",
            severity="critical",
            node_id=claim,
            edge_id=None,
            message="m",
            details={"reference": ref},
        )
        for claim, ref, position in ((claim_a, "s1", 0), (claim_a, "s2", 1), (claim_b, "s1", 0))
    ]

    report = validate_chain(_clean_graph(), recorded_findings=findings)

    assert len(_only(report, "dangling_reference")) == 3
    assert report.counts_by_severity == {"critical": 3}


def test_malformed_recorded_finding_reports_unvalidated() -> None:
    report = validate_chain(_clean_graph(), recorded_findings=("not a finding",))  # type: ignore[arg-type]

    assert report.completed is False
    assert report.incompletion_reason


# ---------------------------------------------------------------------------
# Determinism, purity and module shape
# ---------------------------------------------------------------------------


def _messy_graph() -> ProvenanceGraph:
    claim = _claim((_E1, _E1, format_evidence_id(_SRC, "zz")))
    return _graph(
        _source(),
        _evidence("e1", precision="absent"),
        _evidence("e9", source_id=_SRC2),
        claim,
        _claim((), field_index=9),
        edges=_cites(claim),
    )


def test_validation_is_deterministic_and_independent_of_recorded_order() -> None:
    graph = _messy_graph()
    recorded = [
        ValidationFinding(
            finding_id=f"finding:duplicate_identity:n{i}:1",
            code="duplicate_identity",
            severity="warning",
            node_id=None,
            edge_id=None,
            message="m",
            details={},
        )
        for i in range(3)
    ]

    first = validate_chain(graph, recorded_findings=recorded)
    second = validate_chain(graph, recorded_findings=list(reversed(recorded)))

    assert first == second
    assert len(first.findings) == len({f.finding_id for f in first.findings})
    assert sum(first.counts_by_severity.values()) == len(first.findings)


def test_validation_does_not_modify_the_graph() -> None:
    graph = _messy_graph()
    before = copy.deepcopy(graph)

    validate_chain(graph)

    assert graph == before


def test_module_declares_no_dataclass() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                assert name != "dataclass", f"{node.name} is a dataclass"


# ---------------------------------------------------------------------------
# Review round 1: malformed cited entries and per-edge dangling findings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", 123])
def test_malformed_cited_entry_is_a_dangling_reference(bad: object) -> None:
    """5.3, 5.4, 8.1: a cited entry that is not a non-empty string is reported, never silently skipped."""
    claim = _claim((bad,), support="supported")  # type: ignore[arg-type]
    graph = _graph(_source(), _evidence("e1"), claim)

    report = validate_chain(graph)

    dangling = _only(report, "dangling_reference")
    assert [(f.node_id, f.details["reason"], f.details["reference_kind"]) for f in dangling] == [
        (claim.node_id, "malformed", "evidence")
    ]
    assert dangling[0].details["reference"] == repr(bad)
    assert dangling[0].finding_id.endswith("[pos0]")
    assert [f.node_id for f in _only(report, "unsupported_claim")] == [claim.node_id]
    assert report.completed is True


def test_malformed_cited_entry_beside_valid_evidence_keeps_claim_supported() -> None:
    claim = _claim((_E1, ""), support="supported")
    graph = _graph(_source(), _evidence("e1"), claim, edges=[_edge("cites_evidence", claim.node_id, _E1)])

    report = validate_chain(graph)

    assert len(_only(report, "dangling_reference")) == 1
    assert "unsupported_claim" not in _codes(report)
    assert report.completed is True


def test_input_and_output_edges_to_one_ghost_are_two_findings() -> None:
    ghost = derive_claim_id(_SRC, 99)
    step = _step((ghost,), (ghost,))
    graph = _graph(_source(), step)

    report = validate_chain(graph)

    dangling = _only(report, "dangling_reference")
    assert sorted(f.edge_id for f in dangling) == sorted(
        [f"has_input:{step.node_id}->{ghost}", f"has_output:{step.node_id}->{ghost}"]
    )


def test_edges_leaving_one_absent_node_are_one_finding_each() -> None:
    ghost = derive_claim_id(_SRC, 99)
    edges = [_edge("cites_evidence", ghost, _E1), _edge("revises", ghost, _CLAIM)]
    graph = _graph(_source(), _evidence("e1"), _claim((_E1,)), edges=[*edges, *_cites(_claim((_E1,)))])
    assert not graph.missing_segments

    report = validate_chain(graph)

    dangling = _only(report, "dangling_reference")
    assert sorted(f.edge_id for f in dangling) == sorted(edge.edge_id for edge in edges)


def test_cites_and_revises_edges_to_one_ghost_are_two_findings() -> None:
    ghost = derive_claim_id(_SRC, 99)
    claim = _claim((_E1,))
    edges = [*_cites(claim), _edge("cites_evidence", claim.node_id, ghost), _edge("revises", claim.node_id, ghost)]
    graph = _graph(_source(), _evidence("e1"), claim, edges=edges)

    report = validate_chain(graph)

    dangling = _only(report, "dangling_reference")
    assert sorted(f.edge_id for f in dangling) == sorted(
        [f"cites_evidence:{claim.node_id}->{ghost}", f"revises:{claim.node_id}->{ghost}"]
    )


def test_unknown_carrier_holding_supplier_metadata_is_invalid() -> None:
    carrier = PrivacyCarrier(state="unknown", supplied_by="privacy-core", supplied_at="t")
    graph = _graph(_source(privacy=carrier))

    report = validate_chain(graph)

    assert [f.node_id for f in _only(report, "privacy_carrier_invalid")] == [_SRC]


# ===========================================================================
# Task 4.3 — topology checks and severity classification policy (8.4, 8.5)
# ===========================================================================

_DOCUMENTED_SEVERITIES: dict[str, str] = {
    # design.md "Analysis Layer → ChainValidator" default-severity table
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
    # recorder-only code; severity set in models.py (tasks.md 2.2 note)
    "duplicate_identity": "warning",
}
_RANK = {"critical": 0, "error": 1, "warning": 2, "info": 3}


def _revision(field_index: int, revision: int, *, cited: tuple[str, ...] = (_E1,)) -> ClaimNode:
    return _claim(cited, field_index=field_index, node_id=derive_claim_id(_SRC, field_index, revision))


def _direct_graph(nodes: list[ProvenanceNode], edges: list[ProvenanceEdge]) -> ProvenanceGraph:
    """A graph built without ``build_graph`` — for very large synthetic graphs."""
    return ProvenanceGraph(
        schema_id=PROVENANCE_SCHEMA_ID,
        schema_version=PROVENANCE_SCHEMA_VERSION,
        run=_run_identity(),
        nodes={node.node_id: node for node in nodes},
        edges=tuple(edges),
        missing_segments=(),
    )


# --- graph_cycle -----------------------------------------------------------


def test_graph_cycle_for_a_derivation_cycle() -> None:
    """8.4: claim A → step → claim B → step → claim A is one critical cycle finding."""
    a = _claim((_E1,), field_index=1)
    b = _claim((_E1,), field_index=2)
    forward = _step((a.node_id,), (b.node_id,), kind="normalize")
    backward = _step((b.node_id,), (a.node_id,), kind="normalize")
    graph = _graph(_source(), _evidence("e1"), a, b, forward, backward, edges=[*_cites(a), *_cites(b)])

    report = validate_chain(graph)

    cycles = _only(report, "graph_cycle")
    assert len(cycles) == 1
    cycle = cycles[0]
    members = sorted([a.node_id, b.node_id, forward.node_id, backward.node_id])
    assert cycle.severity == "critical"
    assert list(cycle.details["members"]) == members
    assert cycle.node_id == members[0]
    assert set(cycle.details["edge_ids"]) == {
        f"has_input:{forward.node_id}->{a.node_id}",
        f"has_output:{forward.node_id}->{b.node_id}",
        f"has_input:{backward.node_id}->{b.node_id}",
        f"has_output:{backward.node_id}->{a.node_id}",
    }
    assert report.completed is True


def test_graph_cycle_for_a_revision_cycle() -> None:
    first = _revision(7, 1)
    second = _revision(7, 2)
    edges = [
        *_cites(first),
        *_cites(second),
        _edge("revises", second.node_id, first.node_id),
        _edge("revises", first.node_id, second.node_id),
    ]
    graph = _graph(_source(), _evidence("e1"), first, second, edges=edges)

    report = validate_chain(graph)

    cycles = _only(report, "graph_cycle")
    assert [list(c.details["members"]) for c in cycles] == [sorted([first.node_id, second.node_id])]


@pytest.mark.parametrize(
    ("outputs", "removed"),
    [(("e1", "e2", "e3"), ()), (("e1", "e2"), ("e3",))],
    ids=["nothing-removed", "some-removed"],
)
def test_an_in_place_step_is_not_a_cycle(outputs: tuple[str, ...], removed: tuple[str, ...]) -> None:
    """An id that is both input and output of one step is an in-place transform (filter), not a cycle."""
    from provenance.adapters import derivation_from_stage_event

    ids = {local: format_evidence_id(_SRC, local) for local in ("e1", "e2", "e3")}
    step = derivation_from_stage_event(
        "evidence_index",
        "filter",
        run_id=_RUN,
        inputs=list(ids.values()),
        outputs=[ids[local] for local in outputs],
        removal_reason="below minimum score" if removed else None,
        removed_ids=[ids[local] for local in removed],
    )
    graph = _graph(_source(), *(_evidence(local) for local in ids), step)

    report = validate_chain(graph)

    assert "graph_cycle" not in _codes(report)
    assert report.findings == ()


def test_a_cross_step_cycle_through_an_in_place_step_is_found() -> None:
    """Step A keeps e1 in place and writes claim c; step B reads c and writes e1 — a real cycle."""
    claim = _claim((_E1,))
    keep_and_extract = _step((_E1,), (_E1, claim.node_id))
    back = _step((claim.node_id,), (_E1,), kind="normalize")
    graph = _graph(_source(), _evidence("e1"), claim, keep_and_extract, back, edges=_cites(claim))

    report = validate_chain(graph)

    cycles = _only(report, "graph_cycle")
    assert [list(c.details["members"]) for c in cycles] == [
        sorted([_E1, claim.node_id, keep_and_extract.node_id, back.node_id])
    ]
    assert f"has_output:{keep_and_extract.node_id}->{_E1}" not in cycles[0].details["edge_ids"]


def test_acyclic_revision_and_derivation_chain_has_no_cycle() -> None:
    """A synthesis step reading claim r0 and writing its revision r1, which revises r0, is acyclic."""
    base = _claim((_E1,))
    revised = _revision(7, 1)
    extract = _step((_E1,), (base.node_id,))
    synthesize = _step((base.node_id,), (revised.node_id,), stage="synthesis", kind="synthesize")
    edges = [*_cites(base), *_cites(revised), _edge("revises", revised.node_id, base.node_id)]
    graph = _graph(_source(), _evidence("e1"), base, revised, extract, synthesize, edges=edges)

    report = validate_chain(graph)

    assert report.findings == ()


def test_cites_and_belongs_edges_never_form_a_cycle() -> None:
    """Only derivation and revision edges are walked; citation/membership edges are not."""
    claim = _claim((_E1,))
    back = _edge("cites_evidence", _E1, claim.node_id)  # evidence "citing" a claim: odd, but not a derivation cycle
    graph = _graph(_source(), _evidence("e1"), claim, edges=[*_cites(claim), back])

    report = validate_chain(graph)

    assert "graph_cycle" not in _codes(report)


def _claim_chain(length: int, *, closed: bool) -> ProvenanceGraph:
    claims = [_revision(7, n) for n in range(length)]
    edges = [*_cites(claims[0])]
    for n in range(1, length):
        edges.append(_edge("revises", claims[n].node_id, claims[n - 1].node_id))
    if closed:
        edges.append(_edge("revises", claims[0].node_id, claims[-1].node_id))
    for claim in claims[1:]:
        edges.extend(_cites(claim))
    return _direct_graph([_source(), _evidence("e1"), *claims], edges)


_CHAIN_LENGTH = 5_000
"""Well above the default recursion limit (1000): a recursive search would raise RecursionError."""


def test_a_very_long_acyclic_chain_does_not_exhaust_the_stack() -> None:
    """Cycle detection is iterative: a chain deeper than the recursion limit neither recurses nor reports a cycle."""
    assert _CHAIN_LENGTH > 4 * sys.getrecursionlimit()
    report = validate_chain(_claim_chain(_CHAIN_LENGTH, closed=False))

    assert report.completed is True, report.incompletion_reason
    assert "graph_cycle" not in _codes(report)


def test_a_very_long_cycle_is_found_without_exhausting_the_stack() -> None:
    report = validate_chain(_claim_chain(_CHAIN_LENGTH, closed=True))

    assert report.completed is True, report.incompletion_reason
    cycles = _only(report, "graph_cycle")
    assert len(cycles) == 1
    assert len(cycles[0].details["members"]) == _CHAIN_LENGTH


# --- orphan_node -----------------------------------------------------------


def test_orphan_node_for_a_source_nothing_links_to() -> None:
    """8.4: a node connected to no other node of the graph is an info-level orphan."""
    nodes, edges = _clean_nodes()
    lonely = _source(_FP2)
    graph = _graph(*nodes, lonely, edges=edges)

    report = validate_chain(graph)

    orphans = _only(report, "orphan_node")
    assert [(f.node_id, f.severity) for f in orphans] == [(lonely.node_id, "info")]
    assert orphans[0].edge_id is None


def test_orphan_node_for_a_step_whose_every_link_dangles() -> None:
    ghost_in = derive_claim_id(_SRC, 98)
    ghost_out = derive_claim_id(_SRC, 99)
    nodes, edges = _clean_nodes()
    step = _step((ghost_in,), (ghost_out,))
    graph = _graph(*nodes, step, edges=edges)

    report = validate_chain(graph)

    assert [f.node_id for f in _only(report, "orphan_node")] == [step.node_id]


def test_uncited_evidence_of_a_represented_source_is_not_an_orphan() -> None:
    nodes, edges = _clean_nodes()
    graph = _graph(*nodes, _evidence("e3"), edges=edges)

    report = validate_chain(graph)

    assert "orphan_node" not in _codes(report)


def test_validation_record_and_validates_edges_never_change_orphan_findings() -> None:
    """A clean report's validation record has no edge, and validates edges do not adopt an orphan."""
    nodes, edges = _clean_nodes()
    graph = _graph(*nodes, _source(_FP2), edges=edges)
    report = validate_chain(graph)
    attached = attach_validation(graph, report)
    clean = attach_validation(_clean_graph(), validate_chain(_clean_graph()))

    assert validate_chain(attached).findings == report.findings
    assert validate_chain(clean).findings == ()


# --- inconsistent_derivation ----------------------------------------------


def test_inconsistent_derivation_for_a_step_with_neither_inputs_nor_outputs() -> None:
    nodes, edges = _clean_nodes()
    empty = _step((), (), stage="quality_control", kind="reconcile")
    graph = _graph(*nodes, empty, edges=edges)

    report = validate_chain(graph)

    findings = _only(report, "inconsistent_derivation")
    assert [(f.node_id, f.severity) for f in findings] == [(empty.node_id, "error")]


@pytest.mark.parametrize(
    ("inputs", "outputs"),
    [((_SRC,), ()), ((), (_CLAIM,))],
    ids=["inputs-only", "outputs-only"],
)
def test_a_step_with_only_inputs_or_only_outputs_is_consistent(inputs: tuple[str, ...], outputs: tuple[str, ...]) -> None:
    nodes, edges = _clean_nodes()
    graph = _graph(*nodes, _step(inputs, outputs, kind="filter"), edges=edges)

    report = validate_chain(graph)

    assert "inconsistent_derivation" not in _codes(report)


def test_a_step_whose_identifiers_are_all_unusable_is_inconsistent() -> None:
    nodes, edges = _clean_nodes()
    step = _step(("",), ("",), node_id="drv:00000000000000ff")
    graph = _direct_graph([*nodes, step], edges)

    report = validate_chain(graph)

    assert [f.node_id for f in _only(report, "inconsistent_derivation")] == [step.node_id]


# --- a realistic run yields no topology finding ----------------------------


def _realistic_graph() -> ProvenanceGraph:
    from provenance.adapters import (
        derivation_from_stage_event,
        derivations_from_qc_provenance,
        evidence_nodes_from_items,
        run_identity_from_identities,
        source_node_from_identity,
    )

    identities = {
        name: {"pdf_content_hash": fp, "config_hash": "cfg123", "extraction_map_hash": "sch456", "model_id": "gpt-x"}
        for name, fp in (("a.pdf", _FP), ("b.pdf", _FP2))
    }
    run = run_identity_from_identities(identities, created_at="2026-10-08T00:00:00+00:00")
    recorder = ProvenanceRecorder(run)
    for name, identity in identities.items():
        source = source_node_from_identity(identity, source_label=name, source_format="pdf", run_id=run.run_id)
        source_id = source.node_id
        recorder.record_source(source)
        recorder.mark_stage_complete("document_extraction")
        for step in derivations_from_qc_provenance({"branch": "grobid", "decision": "keep"}, source_id=source_id, run_id=run.run_id):
            recorder.record_derivation(step)
        recorder.mark_stage_complete("quality_control")
        items = [
            {"id": "title", "type": "sentence", "page": 1, "coords": [1.0, 1.0, 2.0, 2.0], "section_path": "Metadata",
             "xpath": ".//title", "score": 9},
            *(
                {"id": f"s{n}", "type": "sentence", "page": n, "coords": [1.0, 2.0, 3.0, 4.0],
                 "section_path": "Methods", "xpath": f".//s[@xml:id='s{n}']", "score": 3}
                for n in range(1, 6)
            ),
        ]
        evidence = evidence_nodes_from_items(items, source_id=source_id, run_id=run.run_id, producing_backend="grobid")
        recorder.record_evidence(evidence)
        evidence_ids = [node.node_id for node in evidence]
        recorder.record_derivation(  # in-place filter: kept ids are both input and output
            derivation_from_stage_event("evidence_index", "filter", run_id=run.run_id, inputs=evidence_ids,
                                        outputs=evidence_ids[:-1], removal_reason="below minimum score",
                                        removed_ids=evidence_ids[-1:])
        )
        recorder.record_derivation(
            derivation_from_stage_event("evidence_index", "annotate", run_id=run.run_id, inputs=evidence_ids,
                                        outputs=evidence_ids)
        )
        recorder.mark_stage_complete("evidence_index")
        known = {node.node_id for node in evidence}
        anchors = {node.node_id: node.anchor for node in evidence}

        def _claims(fields: list[dict], origin: str) -> tuple[list[ClaimNode], list[ProvenanceEdge]]:
            claims, claim_edges, findings = claim_nodes_from_fields(
                fields, source_id=source_id, run_id=run.run_id, known_evidence_ids=known,
                origin=origin, evidence_anchors=anchors,  # type: ignore[arg-type]
            )
            assert findings == []
            return claims, claim_edges

        def _record(claims: list[ClaimNode], claim_edges: list[ProvenanceEdge]) -> None:
            for claim in claims:
                recorder.record_claim(claim, [e for e in claim_edges if e.from_id == claim.node_id])

        prefilled, prefill_edges = _claims(
            [{"field_index": 1, "field_name": "author", "extracted_value": "Lorem", "location": ["title"]}], "prefill"
        )
        _record(prefilled, prefill_edges)
        recorder.record_derivation(
            derivation_from_stage_event("metadata_prefill", "prefill", run_id=run.run_id,
                                        inputs=[format_evidence_id(source_id, "title")],
                                        outputs=[c.node_id for c in prefilled])
        )
        extracted, extract_edges = _claims(
            [
                {"field_index": 7, "field_name": "sample_size", "extracted_value": "42", "location": ["s1", "s2"], "confidence": "high"},
                {"field_index": 8, "field_name": "design", "extracted_value": "RCT", "location": ["s3"], "confidence": "medium"},
            ],
            "model",
        )
        _record(extracted, extract_edges)
        recorder.record_derivation(
            derivation_from_stage_event("extraction_chunk", "extract", run_id=run.run_id,
                                        inputs=[format_evidence_id(source_id, f"s{n}") for n in (1, 2, 3)],
                                        outputs=[c.node_id for c in extracted], model_id="gpt-x")
        )
        base_id = derive_claim_id(source_id, 7)
        synthesized, synth_edges = _claims(
            [{"field_index": 7, "revision": 1, "field_name": "sample_size", "extracted_value": "42",
              "location": ["s1"], "confidence": "high", "contributing_claim_ids": [base_id]}],
            "model",
        )
        _record(synthesized, synth_edges)
        recorder.record_derivation(
            derivation_from_stage_event("synthesis", "synthesize", run_id=run.run_id, inputs=[base_id],
                                        outputs=[c.node_id for c in synthesized], model_id="gpt-x")
        )
        for stage in ("metadata_prefill", "extraction_chunk", "synthesis"):
            recorder.mark_stage_complete(stage)
    return build_graph(recorder.snapshot())


def test_realistic_run_has_no_cycle_orphan_or_inconsistent_derivation() -> None:
    """Real adapters + recorder + build_graph: uncited evidence, QC inputs-only step, prefill and
    synthesis revisions — and the validation record attach_validation adds — yield no finding."""
    graph = _realistic_graph()
    assert any(edge.kind == "revises" for edge in graph.edges)
    in_place = [
        node for node in graph.nodes.values()
        if isinstance(node, DerivationStep) and set(node.input_ids) & set(node.output_ids)
    ]
    assert len(in_place) == 4  # a filter and an annotate step per document

    report = validate_chain(graph)
    attached = attach_validation(graph, report)
    again = validate_chain(attached)

    assert report.completed is True and again.completed is True
    assert report.findings == ()
    assert again.findings == ()


# --- severity classification (8.5) -----------------------------------------


def _ghost_step_graph() -> ProvenanceGraph:
    nodes, edges = _clean_nodes()
    return _graph(*nodes, _step((), ()), edges=edges)


_TRIGGERS: dict[str, typing.Callable[[], ProvenanceGraph]] = {}


def _trigger(code: str) -> typing.Callable[[typing.Callable[[], ProvenanceGraph]], typing.Callable[[], ProvenanceGraph]]:
    def register(factory: typing.Callable[[], ProvenanceGraph]) -> typing.Callable[[], ProvenanceGraph]:
        _TRIGGERS[code] = factory
        return factory

    return register


@_trigger("dangling_reference")
def _g_dangling() -> ProvenanceGraph:
    claim = _claim((format_evidence_id(_SRC, "zz9"),))
    return _graph(_source(), _evidence("e1"), claim, edges=_cites(claim))


def _two_step_cycle() -> tuple[list[ProvenanceNode], list[ProvenanceEdge]]:
    """claim a → step → claim b → step → claim a."""
    a = _claim((_E1,), field_index=1)
    b = _claim((_E1,), field_index=2)
    nodes: list[ProvenanceNode] = [
        a,
        b,
        _step((a.node_id,), (b.node_id,), kind="normalize"),
        _step((b.node_id,), (a.node_id,), kind="normalize"),
    ]
    return nodes, [*_cites(a), *_cites(b)]


@_trigger("graph_cycle")
def _g_cycle() -> ProvenanceGraph:
    nodes, edges = _two_step_cycle()
    return _graph(_source(), _evidence("e1"), *nodes, edges=edges)


@_trigger("invalid_identifier")
def _g_invalid() -> ProvenanceGraph:
    return _graph(_source(), _evidence("e2", node_id=format_evidence_id(_SRC, "e-other")))


@_trigger("source_not_represented")
def _g_source() -> ProvenanceGraph:
    return _graph(_source(), _evidence("e9", source_id=_SRC2))


@_trigger("evidence_out_of_run_context")
def _g_context() -> ProvenanceGraph:
    claim = _claim((_E1,))
    return _graph(_source(), _evidence("e1", run_id=_OTHER_RUN), claim, edges=_cites(claim))


@_trigger("inconsistent_derivation")
def _g_inconsistent() -> ProvenanceGraph:
    return _ghost_step_graph()


@_trigger("privacy_carrier_invalid")
def _g_privacy() -> ProvenanceGraph:
    return _graph(_source(privacy=PrivacyCarrier(state="unknown", supplied_by="privacy-core", supplied_at="t")))


@_trigger("unsupported_claim")
def _g_unsupported() -> ProvenanceGraph:
    return _graph(_source(), _evidence("e1"), _claim((), field_index=8))


@_trigger("missing_anchor")
def _g_anchor() -> ProvenanceGraph:
    return _graph(_source(), _evidence("e3", precision="absent"))


@_trigger("duplicate_reference")
def _g_duplicate() -> ProvenanceGraph:
    claim = _claim((_E1, _E1))
    return _graph(_source(), _evidence("e1"), claim, edges=_cites(claim))


@_trigger("ambiguous_reference")
def _g_ambiguous() -> ProvenanceGraph:
    claim = _claim((format_evidence_id(_SRC, "E1"),))
    return _graph(_source(), _evidence("e1"), claim, edges=_cites(claim))


@_trigger("orphan_node")
def _g_orphan() -> ProvenanceGraph:
    nodes, edges = _clean_nodes()
    return _graph(*nodes, _source(_FP2), edges=edges)


def test_every_documented_graph_code_has_a_trigger() -> None:
    assert set(_TRIGGERS) == set(_DOCUMENTED_SEVERITIES) - {"duplicate_identity"}
    assert dict(DEFAULT_SEVERITIES) == _DOCUMENTED_SEVERITIES


@pytest.mark.parametrize("code", sorted(_TRIGGERS))
def test_each_finding_code_carries_its_documented_default_severity(code: str) -> None:
    report = validate_chain(_TRIGGERS[code]())

    findings = _only(report, code)
    assert findings, f"{code} not produced"
    assert {f.severity for f in findings} == {_DOCUMENTED_SEVERITIES[code]}
    assert report.completed is True


def _recorded_finding(code: str, severity: str, n: int = 0) -> ValidationFinding:
    return ValidationFinding(
        finding_id=f"finding:{code}:rec:{n}",
        code=code,
        severity=severity,  # type: ignore[arg-type]
        node_id=_E1,
        edge_id=None,
        message="m",
        details={},
    )


def test_recorded_known_code_is_stamped_with_its_default_severity() -> None:
    report = validate_chain(_clean_graph(), recorded_findings=[_recorded_finding("duplicate_identity", "info")])

    assert [(f.code, f.severity) for f in report.findings] == [("duplicate_identity", "warning")]
    assert report.counts_by_severity == {"warning": 1}


def test_recorded_unknown_code_keeps_its_own_severity_and_is_reported() -> None:
    report = validate_chain(_clean_graph(), recorded_findings=[_recorded_finding("x_future_code", "error")])

    assert [(f.code, f.severity) for f in report.findings] == [("x_future_code", "error")]
    assert report.completed is True


@pytest.mark.parametrize("code", sorted(_TRIGGERS))
def test_an_override_changes_exactly_that_codes_severity(code: str) -> None:
    graph = _messy_graph_with_topology()
    if code not in _codes(validate_chain(graph)):
        graph = _TRIGGERS[code]()
    target = "info" if _DOCUMENTED_SEVERITIES[code] != "info" else "critical"

    baseline = validate_chain(graph)
    overridden = validate_chain(graph, severity_overrides={code: target})

    before = {f.finding_id: f.severity for f in baseline.findings}
    after = {f.finding_id: f.severity for f in overridden.findings}
    assert set(before) == set(after)
    for finding_id, severity in after.items():
        code_of = next(f.code for f in overridden.findings if f.finding_id == finding_id)
        assert severity == (target if code_of == code else before[finding_id])
    assert sum(overridden.counts_by_severity.values()) == len(overridden.findings)
    assert dict(overridden.counts_by_severity) == dict(Counter(after.values()))


def test_override_applies_to_recorded_findings() -> None:
    recorded = [_recorded_finding("duplicate_identity", "warning"), _recorded_finding("x_future_code", "info", 1)]

    report = validate_chain(
        _clean_graph(),
        recorded_findings=recorded,
        severity_overrides={"duplicate_identity": "critical", "x_future_code": "error"},
    )

    assert {(f.code, f.severity) for f in report.findings} == {("duplicate_identity", "critical"), ("x_future_code", "error")}
    assert report.counts_by_severity == {"critical": 1, "error": 1}


def test_none_and_empty_overrides_are_the_default_policy() -> None:
    graph = _messy_graph_with_topology()

    default = validate_chain(graph)

    assert validate_chain(graph, severity_overrides=None) == default
    assert validate_chain(graph, severity_overrides={}) == default


@pytest.mark.parametrize(
    "overrides",
    [
        "missing_anchor=critical",
        ["missing_anchor", "critical"],
        42,
        {"missing_anchor": "fatal"},
        {"missing_anchor": "CRITICAL"},
        {"missing_anchor": None},
        {7: "critical"},
    ],
    ids=["string", "list", "int", "unknown-severity", "wrong-case", "none-severity", "non-str-code"],
)
def test_unusable_overrides_are_ignored_with_a_warning_and_never_raise(
    overrides: object, caplog: pytest.LogCaptureFixture
) -> None:
    graph = _messy_graph_with_topology()
    default = validate_chain(graph)

    with caplog.at_level("WARNING"):
        report = validate_chain(graph, severity_overrides=overrides)  # type: ignore[arg-type]

    assert report == default
    assert any("severity override" in record.getMessage() for record in caplog.records)


def test_a_bad_override_entry_does_not_disable_the_good_ones(caplog: pytest.LogCaptureFixture) -> None:
    graph = _messy_graph_with_topology()

    with caplog.at_level("WARNING"):
        report = validate_chain(graph, severity_overrides={"missing_anchor": "fatal", "orphan_node": "error"})

    assert {f.severity for f in _only(report, "orphan_node")} == {"error"}
    assert {f.severity for f in _only(report, "missing_anchor")} == {"warning"}
    assert any("fatal" in record.getMessage() for record in caplog.records)


def test_an_override_naming_an_unknown_code_warns_and_changes_nothing_else(caplog: pytest.LogCaptureFixture) -> None:
    graph = _messy_graph_with_topology()
    default = validate_chain(graph)

    with caplog.at_level("WARNING"):
        report = validate_chain(graph, severity_overrides={"orphan_nodes": "critical"})

    assert report == default
    assert any("orphan_nodes" in record.getMessage() for record in caplog.records)


# --- deterministic ordering ------------------------------------------------


def _messy_graph_with_topology() -> ProvenanceGraph:
    claim = _claim((_E1, _E1, format_evidence_id(_SRC, "zz")))
    cycle_nodes, cycle_edges = _two_step_cycle()
    return _graph(
        _source(),
        _source(_FP2),
        _evidence("e1", precision="absent"),
        _evidence("e9", source_id=_SRC2),
        _evidence("e7", source_id=derive_source_id("9999999999999999")),
        claim,
        *cycle_nodes,
        _claim((), field_index=9),
        _step((), ()),
        edges=[*_cites(claim), *cycle_edges],
    )


def _order_key(finding: ValidationFinding) -> tuple[int, str, str]:
    return (_RANK[finding.severity], finding.code, finding.node_id or "")


def test_findings_are_ordered_by_severity_rank_then_code_then_node() -> None:
    report = validate_chain(
        _messy_graph_with_topology(),
        recorded_findings=[_recorded_finding("duplicate_identity", "warning"), _recorded_finding("x_future_code", "info", 1)],
    )

    keys = [_order_key(f) for f in report.findings]
    assert keys == sorted(keys)
    assert {f.severity for f in report.findings} == {"critical", "error", "warning", "info"}
    assert report.findings[0].severity == "critical"
    assert report.findings[-1].severity == "info"


def test_ordering_follows_overridden_severity() -> None:
    report = validate_chain(_messy_graph_with_topology(), severity_overrides={"orphan_node": "critical"})

    keys = [_order_key(f) for f in report.findings]
    assert keys == sorted(keys)
    assert report.findings[0].severity == "critical"
    assert any(f.code == "orphan_node" and f.severity == "critical" for f in report.findings)


def test_repeated_validation_returns_findings_in_identical_order() -> None:
    graph = _messy_graph_with_topology()
    shuffled = ProvenanceGraph(
        schema_id=graph.schema_id,
        schema_version=graph.schema_version,
        run=graph.run,
        nodes=dict(reversed(list(graph.nodes.items()))),
        edges=tuple(reversed(graph.edges)),
        missing_segments=graph.missing_segments,
    )

    runs = [validate_chain(graph) for _ in range(3)] + [validate_chain(shuffled)]

    orders = [[f.finding_id for f in report.findings] for report in runs]
    assert all(order == orders[0] for order in orders)
    assert all(report == runs[0] for report in runs)
    assert len(orders[0]) == len(set(orders[0]))
