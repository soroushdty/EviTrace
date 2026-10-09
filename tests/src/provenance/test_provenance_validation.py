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

Topology checks (cycles, orphans, inconsistent derivations) and the severity
override / ordering policy belong to task 4.3.

Synthetic identifiers only.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
from pathlib import Path

import pytest

from provenance import validation as validation_module
from provenance.adapters import claim_nodes_from_fields
from provenance.graph import ProvenanceGraph, build_graph
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
