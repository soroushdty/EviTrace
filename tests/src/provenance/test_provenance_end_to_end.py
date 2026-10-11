"""End-to-end provenance integration test (provenance-core task 7.4).

Drives the PUBLIC provenance API over one fabricated multi-document run, in the
order the design documents for the orchestrator ("PipelineIntegration -> Run
end"):

    recorder -> snapshot -> build_graph -> validate_chain(recorded_findings=...)
    -> attach_validation -> mark ``chain_validation`` -> post-validation snapshot
    -> compute_completeness -> attach_completeness -> write_graph -> read_graph

Records are built the way production builds them (the real identity, evidence,
claim and derivation adapters) and recorded per document with the per-document
stage marks the pipeline uses. Nothing here touches GROBID, a model provider,
the network or a real PDF; identifiers and text are synthetic.

Scenarios (requirements in brackets):

* happy path: the artifact round-trips, carries the validation record and the
  completeness report, ``chain_validation`` reads complete, and the injected
  findings appear exactly once with their default severity [1.3, 7.5, 8.7];
* traversal: claim -> evidence -> source resolves in the WRITTEN artifact, through
  the graph API and independently over the raw JSON; the unsupported and
  missing-anchor claims resolve to their documented partial paths [1.4];
* degraded runs: a stage failed for one document or for the whole run, a stage
  that completed without emitting and a stage that never ran keep every other
  stage's nodes, name the missing segment and the recorded reason, and leave the
  other documents determinable [1.6, 2.1, 2.5, 7.4];
* no privacy component: every node carries the unknown carrier and the graph
  assembles, validates, completes and persists; a supplied carrier is stored
  verbatim and a malformed one is rejected with a finding [9.2, 9.6, 9.7];
* a disabled recorder, duplicate recording and concurrent recording.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import os
import socket
import sys
import threading
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from provenance import ProvenanceGraph, ProvenanceRecorder, build_graph
from provenance.adapters import (
    claim_nodes_from_fields,
    derivation_from_stage_event,
    derivations_from_qc_provenance,
    evidence_node_from_item,
    run_identity_from_identities,
    source_node_from_identity,
)
from provenance.completeness import compute_completeness
from provenance.graph import attach_completeness, attach_validation, step_documents
from provenance.identity import derive_claim_id
from provenance.models import (
    UNKNOWN_PRIVACY,
    ChainValidationReport,
    ClaimNode,
    CompletenessReport,
    DerivationStep,
    EvidenceNode,
    RecordedRun,
    RunIdentity,
)
from provenance.privacy_carrier import attach_privacy
from provenance.serialization import ARTIFACT_FILENAME, read_graph, write_graph
from provenance.validation import validate_chain

# ---------------------------------------------------------------------------
# The fabricated run: three documents, synthetic identity, synthetic text
# ---------------------------------------------------------------------------

_CREATED_AT = "2026-10-11T00:00:00Z"
_CHUNK_MODEL = "model-chunk-synthetic"
_SYNTHESIS_MODEL = "model-synthesis-synthetic"
_CHAIN_VALIDATION = "chain_validation"

ALPHA, BETA, GAMMA = "alpha.pdf", "beta.pdf", "gamma.pdf"
DOCUMENTS = (ALPHA, BETA, GAMMA)
_IDENTITIES: dict[str, dict[str, str]] = {
    name: {
        "pdf_content_hash": fingerprint * 64,
        "config_hash": "cfg-synthetic-0001",
        "extraction_map_hash": "schema-synthetic-0001",
        "model_id": _CHUNK_MODEL,
        "schema_version": "1",
        "output_path": f"{name[:-4]}.extracted.json",
    }
    for name, fingerprint in ((ALPHA, "a"), (BETA, "b"), (GAMMA, "c"))
}
SOURCE_ID = {name: f"src:{identity['pdf_content_hash']}" for name, identity in _IDENTITIES.items()}

# Evidence items in the shape ``evidence_index`` produces. Anchor precision, by the adapter's rule:
# page + coords -> exact; a page (or an xml:id path) alone -> approximate; a section path alone -> absent.
_EXACT = {"page": 1, "coords": [10.0, 20.0, 30.0, 40.0], "section_path": "Methods"}
_APPROXIMATE = {"page": 2, "section_path": "Results"}
_ABSENT = {"section_path": "Results"}


def _item(local_id: str, kind: str = "sentence", **anchor: Any) -> dict[str, Any]:
    return {"id": local_id, "type": kind, "score": 5, "text": f"lorem ipsum {local_id}", **anchor}


_TITLE_APPROXIMATE = {"section_path": "Metadata", "xpath": "//*[@xml:id='title']"}
_TITLE_ABSENT = {"section_path": "Metadata"}

_ITEMS: dict[str, list[dict[str, Any]]] = {
    ALPHA: [
        _item("title", "metadata", **_TITLE_APPROXIMATE),
        _item("s1", **_EXACT),
        _item("s2", **_APPROXIMATE),
        _item("s3", **_EXACT),
        _item("f1", "figure_caption", **_APPROXIMATE),
        _item("t1", "table", **_EXACT),
    ],
    BETA: [
        _item("title", "metadata", **_TITLE_APPROXIMATE),
        _item("s1", **_EXACT),
        _item("s2", **_EXACT),
        _item("s5", **_ABSENT),
    ],
    GAMMA: [
        _item("title", "metadata", **_TITLE_ABSENT),
        _item("s1", **_EXACT),
        _item("s2", **_EXACT),
        _item("s3", **_APPROXIMATE),
    ],
}


def _field(index: int, name: str, value: str, location: list[str], **extra: Any) -> dict[str, Any]:
    return {"field_index": index, "field_name": name, "extracted_value": value, "location": location, **extra}


@dataclass(frozen=True)
class DocumentPlan:
    """The fields each claim-emitting stage produces for one document."""

    prefill: tuple[dict[str, Any], ...] = ()
    chunk: tuple[dict[str, Any], ...] = ()
    synthesis: tuple[dict[str, Any], ...] = ()
    removed: tuple[tuple[int, int], ...] = ()  # (field_index, revision) of candidates synthesis dropped


def _contributors(source_id: str, *keys: tuple[int, int]) -> list[str]:
    return [derive_claim_id(source_id, index, revision) for index, revision in keys]


_PLANS: dict[str, DocumentPlan] = {
    # Clean document, plus one claim citing a locator that resolves to no evidence node (dangling, retained).
    ALPHA: DocumentPlan(
        prefill=(_field(1, "first_author", "Lorem", ["title"]),),
        chunk=(
            _field(7, "sample_size", "42", ["s1", "s2"], confidence="high"),
            _field(8, "design", "synthetic", ["s3", "s99"], confidence="medium"),
            _field(9, "outcome", "ipsum", ["f1", "t1"], confidence="high"),
        ),
        synthesis=(
            _field(
                7, "sample_size", "42", ["s1"], revision=1, confidence="high",
                contributing_claim_ids=_contributors(SOURCE_ID[ALPHA], (7, 0)),
            ),
        ),
    ),
    # Defective document: a claim citing only an anchorless sentence and a claim citing nothing.
    # No synthesis (the stage never ran for it).
    BETA: DocumentPlan(
        prefill=(_field(1, "first_author", "Lorem", ["title"]),),
        chunk=(
            _field(7, "sample_size", "17", ["s1", "s2"], confidence="high"),
            _field(9, "outcome", "ipsum", ["s5"], confidence="low"),
            _field(10, "notes", "dolor", [], confidence="low"),
        ),
    ),
    # Prefill gaps (header without a locator, prefill without a citation: claim-level only), a repeated
    # citation, two candidates for one field, a merge and a removal.
    GAMMA: DocumentPlan(
        prefill=(
            _field(1, "first_author", "Lorem", ["title"]),
            _field(2, "publication_year", "2021", []),
        ),
        chunk=(
            _field(7, "sample_size", "99", ["s1", "s1"], confidence="high"),
            _field(9, "outcome", "ipsum", ["s1"], revision=0, confidence="medium"),
            _field(9, "outcome", "dolor", ["s2"], revision=1, confidence="low"),
        ),
        synthesis=(
            _field(
                9, "outcome", "ipsum", ["s1", "s2"], revision=2, confidence="medium",
                contributing_claim_ids=_contributors(SOURCE_ID[GAMMA], (9, 0), (9, 1)),
            ),
        ),
        removed=((9, 1),),
    ),
}

# What the happy-path run must report, derived from the plans above (one line of reasoning each):
# alpha  s99 resolves to no evidence node                          -> dangling_reference (critical) on claim 8
# beta   s5 has only a section path                                  -> missing_anchor on that evidence node
#        field 10 cites nothing                                      -> unsupported_claim on that claim
# gamma  the title has no locator                                    -> missing_anchor on that evidence node
#        field 2 prefilled with no citation                          -> prefill_unsupported on that claim
#        field 7 cites s1 twice                                      -> duplicate_reference on that claim


# ---------------------------------------------------------------------------
# Recording helpers (the way the pipeline emits, minus the pipeline)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOptions:
    """How one fabricated run deviates from the all-stages-complete baseline."""

    fail_documents: Mapping[tuple[str, str], str] = field(default_factory=dict)  # (document, stage) -> reason
    fail_run_wide: Mapping[str, str] = field(default_factory=dict)  # stage -> reason (source_id=None)
    silent_complete: frozenset[str] = frozenset()  # stage marked complete although it emitted nothing
    silent_unmarked: frozenset[str] = frozenset()  # stage that emitted nothing and was never marked
    emit_on_failure: bool = True  # a failed stage still recorded what it produced before it failed
    carrier_inputs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # node_id -> carrier input
    enabled: bool = True


def make_run_identity() -> RunIdentity:
    return run_identity_from_identities(_IDENTITIES, created_at=_CREATED_AT, additional_model_ids=[_SYNTHESIS_MODEL])


def make_recorder(options: RunOptions = RunOptions(), run: RunIdentity | None = None) -> ProvenanceRecorder:
    return ProvenanceRecorder(run or make_run_identity(), enabled=options.enabled)


def _carry(node: Any, options: RunOptions) -> Any:
    """Apply a privacy carrier supplied for ``node`` (opaque strings, via the public carrier builder)."""
    if node.node_id not in options.carrier_inputs:
        return node
    carrier = attach_privacy(
        options.carrier_inputs[node.node_id], supplied_by="privacy-component-synthetic", supplied_at=_CREATED_AT
    )
    return dataclasses.replace(node, privacy=carrier)


def _settle(recorder: ProvenanceRecorder, stage: str, document: str, options: RunOptions) -> None:
    """Mark ``stage`` the way the options say: failed (run-wide or for this document), complete, or not at all."""
    source_id = SOURCE_ID[document]
    if stage in options.fail_run_wide:
        recorder.mark_stage_failed(stage, options.fail_run_wide[stage])  # idempotent: first reason kept
    elif (document, stage) in options.fail_documents:
        recorder.mark_stage_failed(stage, options.fail_documents[(document, stage)], source_id=source_id)
    elif stage not in options.silent_unmarked:
        recorder.mark_stage_complete(stage, source_id=source_id)


def _emits(stage: str, document: str, options: RunOptions) -> bool:
    if stage in options.silent_complete or stage in options.silent_unmarked:
        return False
    failed = stage in options.fail_run_wide or (document, stage) in options.fail_documents
    return options.emit_on_failure or not failed


def _record_claims(
    recorder: ProvenanceRecorder,
    run: RunIdentity,
    document: str,
    fields: Sequence[Mapping[str, Any]],
    origin: str,
    evidence: Sequence[EvidenceNode],
    options: RunOptions,
) -> list[ClaimNode]:
    """Convert ``fields`` with the claim adapter and record findings, then each claim with its own edges."""
    claims, edges, findings = claim_nodes_from_fields(
        fields,
        source_id=SOURCE_ID[document],
        run_id=run.run_id,
        known_evidence_ids={node.node_id for node in evidence},
        origin=origin,  # type: ignore[arg-type]
        evidence_anchors={node.node_id: node.anchor for node in evidence},
    )
    for finding in findings:
        recorder.record_issue(finding)
    recorded: list[ClaimNode] = []
    for claim in claims:
        claim = _carry(claim, options)
        recorder.record_claim(claim, [edge for edge in edges if edge.from_id == claim.node_id])
        recorded.append(claim)
    return recorded


def _cited(claims: Sequence[ClaimNode]) -> list[str]:
    return list(dict.fromkeys(evidence_id for claim in claims for evidence_id in claim.cited_evidence_ids))


def record_document(
    recorder: ProvenanceRecorder, run: RunIdentity, document: str, options: RunOptions = RunOptions()
) -> None:
    """Record one document the way the pipeline's per-document hooks do, honouring ``options``."""
    plan = _PLANS[document]
    source_id = SOURCE_ID[document]

    source = source_node_from_identity(
        _IDENTITIES[document], source_label=document, source_format="pdf", run_id=run.run_id
    )
    recorder.record_source(_carry(source, options))
    _settle(recorder, "document_extraction", document, options)

    if _emits("quality_control", document, options):
        qc = {"primary_branch_source": "grobid", "branch_selection": "adjudicated"}
        for step in derivations_from_qc_provenance(qc, source_id=source_id, run_id=run.run_id):
            recorder.record_derivation(_carry(step, options))
    _settle(recorder, "quality_control", document, options)

    evidence: list[EvidenceNode] = []
    if _emits("evidence_index", document, options):
        evidence = [
            _carry(
                evidence_node_from_item(item, source_id=source_id, run_id=run.run_id, producing_backend="grobid"),
                options,
            )
            for item in _ITEMS[document]
        ]
        recorder.record_evidence(evidence)
    _settle(recorder, "evidence_index", document, options)

    def step(stage: str, kind: str, inputs: Sequence[str], outputs: Sequence[str], **kwargs: Any) -> None:
        built = derivation_from_stage_event(stage, kind, run_id=run.run_id, inputs=inputs, outputs=outputs, **kwargs)  # type: ignore[arg-type]
        recorder.record_derivation(_carry(built, options))

    if _emits("metadata_prefill", document, options) and plan.prefill:
        claims = _record_claims(recorder, run, document, plan.prefill, "prefill", evidence, options)
        step("metadata_prefill", "prefill", _cited(claims), [c.node_id for c in claims])
    _settle(recorder, "metadata_prefill", document, options)

    if _emits("extraction_chunk", document, options) and plan.chunk:
        claims = _record_claims(recorder, run, document, plan.chunk, "model", evidence, options)
        step(
            "extraction_chunk", "extract", _cited(claims), [c.node_id for c in claims],
            model_id=_CHUNK_MODEL, extensions={"x-e2e-chunk": 1},
        )
    _settle(recorder, "extraction_chunk", document, options)

    if not plan.synthesis and "synthesis" not in options.fail_run_wide and (document, "synthesis") not in options.fail_documents:
        return  # the stage never ran for this document: no records, no mark (num_chunks=1 style)
    if _emits("synthesis", document, options) and plan.synthesis:
        claims = _record_claims(recorder, run, document, plan.synthesis, "model", evidence, options)
        contributors = list(
            dict.fromkeys(cid for planned in plan.synthesis for cid in planned.get("contributing_claim_ids", ()))
        )
        step(
            "synthesis", "synthesize", [*contributors, *_cited(claims)], [c.node_id for c in claims],
            model_id=_SYNTHESIS_MODEL,
        )
        if plan.removed:
            dropped = _contributors(source_id, *plan.removed)
            step(
                "synthesis", "remove", dropped, [],
                removal_reason="candidate not shown to synthesis", removed_ids=dropped,
            )
    _settle(recorder, "synthesis", document, options)


def record_run(recorder: ProvenanceRecorder, run: RunIdentity, options: RunOptions = RunOptions()) -> None:
    for document in DOCUMENTS:
        record_document(recorder, run, document, options)


@dataclass(frozen=True)
class Finished:
    """Everything the run-end sequence produced."""

    recorder: ProvenanceRecorder
    recorded: RecordedRun
    unvalidated_graph: ProvenanceGraph
    report: ChainValidationReport
    completeness: CompletenessReport
    graph: ProvenanceGraph
    path: Path


def finish_run(recorder: ProvenanceRecorder, directory: Path, *, strict: bool = True) -> Finished:
    """The documented run-end sequence, in the documented order, over the public API only."""
    recorded = recorder.snapshot()
    graph = build_graph(recorded)
    report = validate_chain(graph, recorded_findings=recorded.findings)
    unvalidated = graph
    graph = attach_validation(graph, report)
    if report.completed:
        recorder.mark_stage_complete(_CHAIN_VALIDATION)
    else:
        recorder.mark_stage_failed(_CHAIN_VALIDATION, report.incompletion_reason or "chain validation did not complete")
    post_validation = recorder.snapshot()
    completeness = compute_completeness(graph, post_validation, stage_contracts_strict=strict)
    graph = attach_completeness(graph, completeness)
    path = write_graph(graph, directory)
    return Finished(recorder, post_validation, unvalidated, report, completeness, graph, path)


def run_end_to_end(directory: Path, options: RunOptions = RunOptions()) -> Finished:
    run = make_run_identity()
    recorder = make_recorder(options, run)
    record_run(recorder, run, options)
    return finish_run(recorder, directory)


# ---------------------------------------------------------------------------
# Expectations and artifact helpers
# ---------------------------------------------------------------------------


def claim_id(document: str, index: int, revision: int = 0) -> str:
    return derive_claim_id(SOURCE_ID[document], index, revision)


def evidence_id(document: str, local_id: str) -> str:
    return f"{SOURCE_ID[document]}#{local_id}"


# The findings the happy-path run must report, each once, at its default severity (8.4, 8.5).
EXPECTED_FINDINGS = sorted(
    [
        ("dangling_reference", "critical", claim_id(ALPHA, 8)),
        ("duplicate_reference", "warning", claim_id(GAMMA, 7)),
        ("missing_anchor", "warning", evidence_id(BETA, "s5")),
        ("missing_anchor", "warning", evidence_id(GAMMA, "title")),
        ("prefill_unsupported", "warning", claim_id(GAMMA, 2)),
        ("unsupported_claim", "warning", claim_id(BETA, 10)),
    ]
)

_UNKNOWN_CARRIER = {
    "state": "unknown",
    "label": None,
    "decision": None,
    "supplied_by": None,
    "supplied_at": None,
    "rejection_reason": None,
}


def _raw(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_nodes(raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["node_id"]: node for node in raw["nodes"]}


def _findings_of(graph: ProvenanceGraph) -> list[tuple[str, str, str | None]]:
    assert graph.validation is not None
    return sorted((f.code, f.severity, f.node_id) for f in graph.validation.findings)  # type: ignore[type-var]


def _raw_path_by_edges(raw: Mapping[str, Any], claim: str) -> list[str]:
    """claim -> evidence -> source followed over the raw JSON edge list only."""
    nodes = _raw_nodes(raw)

    def targets(origin: str, kind: str, node_kind: str) -> list[str]:
        found = {e["to_id"] for e in raw["edges"] if e["from_id"] == origin and e["kind"] == kind}
        return sorted(t for t in found if nodes.get(t, {}).get("kind") == node_kind)

    evidence = targets(claim, "cites_evidence", "evidence")
    sources = sorted({s for e in evidence for s in targets(e, "belongs_to_source", "source")})
    return [claim, *evidence, *sources]


def _raw_path_by_fields(raw: Mapping[str, Any], claim: str) -> list[str]:
    """The same chain followed over the node fields (``cited_evidence_ids``, ``source_id``)."""
    nodes = _raw_nodes(raw)
    cited = sorted(i for i in nodes[claim]["cited_evidence_ids"] if nodes.get(i, {}).get("kind") == "evidence")
    sources = sorted({nodes[i]["source_id"] for i in cited if nodes[i]["source_id"] in nodes})
    return [claim, *cited, *sources]


@pytest.fixture(autouse=True)
def _offline_and_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provenance needs no model-provider key and no network (1.5): prove it by taking both away."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the end-to-end provenance run attempted network access")

    monkeypatch.setattr(socket, "socket", _refuse)


@pytest.fixture
def baseline(tmp_path: Path) -> Finished:
    """The all-stages-complete run, taken through the whole sequence and written under ``tmp_path``."""
    return run_end_to_end(tmp_path / "baseline")


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_written_artifact_reads_back_equal_and_rewrites_byte_identically(baseline: Finished, tmp_path: Path) -> None:
    on_disk = read_graph(baseline.path)

    # component by component first, so a lossy writer is named rather than reported as "graphs differ"
    assert on_disk.validation == baseline.graph.validation, "the validation report did not survive the write"
    assert on_disk.completeness == baseline.graph.completeness, "the completeness report did not survive the write"
    assert on_disk.missing_segments == baseline.graph.missing_segments, "missing segments did not survive the write"
    assert on_disk.nodes == baseline.graph.nodes and on_disk.edges == baseline.graph.edges
    assert on_disk == baseline.graph
    assert read_graph(baseline.path.parent) == baseline.graph  # the directory form of the path
    rewritten = write_graph(on_disk, tmp_path / "rewrite")
    assert rewritten.read_bytes() == baseline.path.read_bytes()
    # an independent run over the same input yields the same artifact, byte for byte
    again = run_end_to_end(tmp_path / "again")
    assert again.path.read_bytes() == baseline.path.read_bytes()
    # exactly one file per run, and nothing left behind by the atomic write
    assert [entry.name for entry in baseline.path.parent.iterdir()] == [ARTIFACT_FILENAME]
    assert baseline.path.name == ARTIFACT_FILENAME


def test_artifact_carries_schema_metadata_run_identity_validation_and_completeness(baseline: Finished) -> None:
    raw = _raw(baseline.path)

    assert list(raw) == [
        "schema_id", "schema_version", "run", "nodes", "edges", "missing_segments", "validation", "completeness",
    ]
    assert raw["schema_id"] == baseline.graph.schema_id and raw["schema_version"] == baseline.graph.schema_version
    assert raw["run"]["run_id"] == baseline.graph.run.run_id
    assert raw["run"]["created_at"] == _CREATED_AT
    assert raw["run"]["document_fingerprints"] == {name: _IDENTITIES[name]["pdf_content_hash"] for name in DOCUMENTS}
    assert raw["run"]["model_ids"] == sorted({_CHUNK_MODEL, _SYNTHESIS_MODEL})

    nodes = _raw_nodes(raw)
    assert Counter(node["kind"] for node in nodes.values()) == {
        "source": 3, "evidence": 14, "claim": 15, "derivation": 12, "validation": 1,
    }
    # 1.3: the source record links to its extraction output by a run-relative path, not an absolute one
    for name in DOCUMENTS:
        assert nodes[SOURCE_ID[name]]["extensions"]["x-manifest-output_path"] == f"{name[:-4]}.extracted.json"
    # every edge joins two recorded nodes, and carries a declared kind
    assert all(edge["from_id"] in nodes and edge["to_id"] in nodes for edge in raw["edges"])

    # 8.7: the validation result is a record of the graph, listing the findings and linking the nodes they name
    (validation,) = [node for node in nodes.values() if node["kind"] == "validation"]
    assert validation["node_id"] == f"val:{raw['run']['run_id']}:chain_validation"
    assert validation["completed"] is True
    assert validation["finding_ids"] == [finding["finding_id"] for finding in raw["validation"]["findings"]]
    validated = {e["to_id"] for e in raw["edges"] if e["kind"] == "validates"}
    assert validated == {f["node_id"] for f in raw["validation"]["findings"]}

    # 2.2: completeness at run, document and claim level, queryable as data
    completeness = raw["completeness"]
    assert set(completeness) == {"run", "documents", "claims"}
    assert set(completeness["documents"]) == set(SOURCE_ID.values())
    assert set(completeness["claims"]) == {i for i, n in nodes.items() if n["kind"] == "claim"}


def test_chain_validation_is_complete_run_wide_and_visible_in_the_artifact(baseline: Finished) -> None:
    assert baseline.recorded.stage_status["chain_validation"] == "complete"
    assert "chain_validation" not in baseline.recorded.stage_failure_reasons

    on_disk = read_graph(baseline.path)
    assert on_disk.validation is not None and on_disk.validation.completed is True
    (record,) = on_disk.nodes_of_kind("validation")
    assert record.completed is True  # type: ignore[attr-defined]
    # the stage's required record kind is present, so the run is not reported as missing it
    assert on_disk.completeness is not None
    assert "validation" not in on_disk.completeness.run.missing_record_kinds
    assert "chain_validation" not in on_disk.completeness.run.responsible_stages
    assert on_disk.missing_segments == ()


def test_completeness_states_follow_the_policy(baseline: Finished) -> None:
    on_disk = read_graph(baseline.path)
    completeness = on_disk.completeness
    assert completeness is not None

    # documents: the worst of their claims, except that prefill gaps stay on the claim (5.7)
    assert completeness.documents[SOURCE_ID[ALPHA]].state == "complete"
    assert completeness.documents[SOURCE_ID[GAMMA]].state == "complete"
    beta = completeness.documents[SOURCE_ID[BETA]]
    assert (beta.state, beta.reason) == ("incomplete", "missing_anchor; unsupported")
    assert beta.missing_record_kinds == ("anchor", "evidence")
    assert beta.responsible_stages == ("evidence_index", "extraction_chunk")
    run = completeness.run
    assert (run.state, run.reason) == ("incomplete", "missing_anchor; unsupported")
    assert run.missing_record_kinds == beta.missing_record_kinds and run.responsible_stages == beta.responsible_stages

    # claims: the two non-prefill gaps are incomplete with distinct reasons (2.4); the prefill gaps are claim-level only
    incomplete = {
        claim: (entry.reason, entry.missing_record_kinds, entry.responsible_stages)
        for claim, entry in completeness.claims.items()
        if entry.state != "complete"
    }
    assert incomplete == {
        claim_id(BETA, 9): ("missing_anchor", ("anchor",), ("evidence_index",)),
        claim_id(BETA, 10): ("unsupported", ("evidence",), ("extraction_chunk",)),
        claim_id(GAMMA, 1): ("missing_anchor", ("anchor",), ("evidence_index",)),
        claim_id(GAMMA, 2): ("unsupported", ("evidence",), ("metadata_prefill",)),
    }
    assert {entry.state for entry in completeness.claims.values()} == {"complete", "incomplete"}
    # the claim states agree with the support the claim records carry
    for claim in on_disk.nodes_of_kind("claim"):
        state = completeness.claims[claim.node_id].state
        assert (claim.support == "supported") == (state == "complete"), claim.node_id  # type: ignore[attr-defined]


def test_injected_findings_appear_exactly_once_with_their_default_severity(baseline: Finished) -> None:
    on_disk = read_graph(baseline.path)

    assert _findings_of(on_disk) == EXPECTED_FINDINGS
    assert on_disk.validation is not None
    assert dict(on_disk.validation.counts_by_severity) == {"critical": 1, "warning": 5}
    ids = [finding.finding_id for finding in on_disk.validation.findings]
    assert len(ids) == len(set(ids)) == len(EXPECTED_FINDINGS)
    # nothing at error or critical beyond the single deliberately dangling citation
    severe = [f for f in on_disk.validation.findings if f.severity in ("error", "critical")]
    assert [(f.code, f.node_id) for f in severe] == [("dangling_reference", claim_id(ALPHA, 8))]
    assert severe[0].details["reference"] == "s99"
    # the dangling claim is retained (5.4): it exists, cites what resolved, and is still supported
    retained = on_disk.nodes[claim_id(ALPHA, 8)]
    assert retained.support == "supported"  # type: ignore[attr-defined]
    assert retained.cited_evidence_ids == (evidence_id(ALPHA, "s3"),)  # type: ignore[attr-defined]


def test_every_document_keeps_the_nodes_every_stage_emitted(baseline: Finished) -> None:
    graph = read_graph(baseline.path)

    stages_by_document: dict[str, Counter[str]] = {name: Counter() for name in DOCUMENTS}
    by_source = {source_id: name for name, source_id in SOURCE_ID.items()}
    for node in graph.nodes_of_kind("derivation"):
        assert isinstance(node, DerivationStep)
        (source_id,) = step_documents(node, graph.nodes)
        stages_by_document[by_source[source_id]][node.stage] += 1
    assert stages_by_document == {
        ALPHA: Counter(quality_control=1, metadata_prefill=1, extraction_chunk=1, synthesis=1),
        BETA: Counter(quality_control=1, metadata_prefill=1, extraction_chunk=1),
        GAMMA: Counter(quality_control=1, metadata_prefill=1, extraction_chunk=1, synthesis=2),
    }
    for name in DOCUMENTS:
        evidence = [n for n in graph.nodes_of_kind("evidence") if n.source_id == SOURCE_ID[name]]  # type: ignore[attr-defined]
        assert sorted(n.local_id for n in evidence) == sorted(item["id"] for item in _ITEMS[name])  # type: ignore[attr-defined]
    # metadata evidence keeps its own kind and each anchor precision came out as the items were built
    assert graph.nodes[evidence_id(ALPHA, "title")].evidence_kind == "metadata"  # type: ignore[attr-defined]
    precisions = {n.local_id: n.anchor.precision for n in graph.nodes_of_kind("evidence") if n.source_id == SOURCE_ID[BETA]}  # type: ignore[attr-defined]
    assert precisions == {"title": "approximate", "s1": "exact", "s2": "exact", "s5": "absent"}


def test_revisions_merges_removals_and_model_attribution_are_in_the_artifact(baseline: Finished) -> None:
    raw = _raw(baseline.path)
    nodes = _raw_nodes(raw)

    revises = sorted((e["from_id"], e["to_id"]) for e in raw["edges"] if e["kind"] == "revises")
    assert revises == sorted(
        [
            (claim_id(ALPHA, 7, 1), claim_id(ALPHA, 7, 0)),
            (claim_id(GAMMA, 9, 2), claim_id(GAMMA, 9, 0)),
            (claim_id(GAMMA, 9, 2), claim_id(GAMMA, 9, 1)),
        ]
    )
    steps = [node for node in nodes.values() if node["kind"] == "derivation"]
    (removal,) = [step for step in steps if step["derivation_kind"] == "remove"]
    assert removal["removal_reason"] == "candidate not shown to synthesis"
    assert removal["removed_ids"] == [claim_id(GAMMA, 9, 1)] and removal["output_ids"] == []
    assert Counter((s["stage"], s["derivation_kind"], s["determinism"], s["model_id"]) for s in steps) == {
        ("quality_control", "reconcile", "deterministic", None): 3,
        ("metadata_prefill", "prefill", "deterministic", None): 3,
        ("extraction_chunk", "extract", "probabilistic", _CHUNK_MODEL): 3,
        ("synthesis", "synthesize", "probabilistic", _SYNTHESIS_MODEL): 2,
        ("synthesis", "remove", "deterministic", None): 1,
    }
    assert all(step["extensions"] == {"x-e2e-chunk": 1} for step in steps if step["stage"] == "extraction_chunk")


# ---------------------------------------------------------------------------
# 2. Claim -> evidence -> source traversal in the WRITTEN artifact (1.4)
# ---------------------------------------------------------------------------

_SUPPORTED_CLAIMS = [
    pytest.param(ALPHA, 7, 0, ("s1", "s2"), id="alpha-two-citations"),
    pytest.param(ALPHA, 8, 0, ("s3",), id="alpha-dangling-citation-dropped"),
    pytest.param(ALPHA, 7, 1, ("s1",), id="alpha-revised"),
    pytest.param(BETA, 7, 0, ("s1", "s2"), id="beta-supported"),
    pytest.param(GAMMA, 7, 0, ("s1",), id="gamma-repeated-citation-cited-once"),
    pytest.param(GAMMA, 9, 2, ("s1", "s2"), id="gamma-merged"),
]


@pytest.mark.parametrize(("document", "index", "revision", "locals_cited"), _SUPPORTED_CLAIMS)
def test_supported_claim_resolves_to_its_evidence_and_source_in_the_written_artifact(
    baseline: Finished, document: str, index: int, revision: int, locals_cited: tuple[str, ...]
) -> None:
    claim = claim_id(document, index, revision)
    expected = [claim, *sorted(evidence_id(document, local) for local in locals_cited), SOURCE_ID[document]]

    on_disk = read_graph(baseline.path)
    assert [node.node_id for node in on_disk.path_from_claim(claim)] == expected  # the graph's traversal API

    raw = _raw(baseline.path)  # independent traversals over the raw JSON must agree with it
    assert _raw_path_by_edges(raw, claim) == expected
    assert _raw_path_by_fields(raw, claim) == expected

    path = on_disk.path_from_claim(claim)
    assert [node.kind for node in path] == ["claim", *["evidence"] * len(locals_cited), "source"]
    assert path[-1].source_label == document  # type: ignore[attr-defined]  # the source of the right document
    assert path[0].support == "supported"  # type: ignore[attr-defined]
    for node in path[1:-1]:
        assert node.anchor.precision in ("exact", "approximate")  # type: ignore[attr-defined]


def test_every_claim_of_the_artifact_resolves_the_same_way_through_all_three_traversals(baseline: Finished) -> None:
    on_disk = read_graph(baseline.path)
    raw = _raw(baseline.path)

    claims = on_disk.nodes_of_kind("claim")
    assert len(claims) == 15
    for claim in claims:
        via_graph = [node.node_id for node in on_disk.path_from_claim(claim.node_id)]
        assert via_graph == _raw_path_by_edges(raw, claim.node_id) == _raw_path_by_fields(raw, claim.node_id)
        assert via_graph[0] == claim.node_id
        sources = [node for node in on_disk.path_from_claim(claim.node_id) if node.kind == "source"]
        if claim.cited_evidence_ids:  # type: ignore[attr-defined]
            assert [s.node_id for s in sources] == [claim.source_id]  # type: ignore[attr-defined]  # never another document's


def test_unsupported_and_missing_anchor_claims_resolve_to_their_documented_partial_paths(baseline: Finished) -> None:
    on_disk = read_graph(baseline.path)
    raw = _raw(baseline.path)

    # a claim citing nothing: the path is the claim alone, and the claim says why
    unsupported = claim_id(BETA, 10)
    assert on_disk.nodes[unsupported].support == "unsupported"  # type: ignore[attr-defined]
    assert [n.node_id for n in on_disk.path_from_claim(unsupported)] == [unsupported]
    assert _raw_path_by_edges(raw, unsupported) == _raw_path_by_fields(raw, unsupported) == [unsupported]

    # a claim citing only an anchorless sentence: classified missing-anchor, NOT unsupported (2.4);
    # the path reaches the evidence and the source, and the evidence has no usable anchor
    anchorless = claim_id(BETA, 9)
    node = on_disk.nodes[anchorless]
    assert node.support == "missing_anchor"  # type: ignore[attr-defined]
    path = on_disk.path_from_claim(anchorless)
    assert [n.node_id for n in path] == [anchorless, evidence_id(BETA, "s5"), SOURCE_ID[BETA]]
    assert path[1].anchor.precision == "absent"  # type: ignore[attr-defined]
    assert path[1].anchor.missing  # type: ignore[attr-defined]
    assert _raw_path_by_edges(raw, anchorless) == _raw_path_by_fields(raw, anchorless) == [n.node_id for n in path]

    # ids that are not claims never raise: they resolve to nothing
    for not_a_claim in (SOURCE_ID[ALPHA], evidence_id(ALPHA, "s1"), "claim:src:missing#f1", ""):
        assert on_disk.path_from_claim(not_a_claim) == ()


# ---------------------------------------------------------------------------
# 3. Degraded runs: failed, silent and never-run stages (1.6, 2.1, 2.5, 7.4)
# ---------------------------------------------------------------------------

_SYNTHESIS_FAILURE = "synthetic provider error during synthesis"


def _non_validation_nodes(graph: ProvenanceGraph) -> set[str]:
    return {node_id for node_id, node in graph.nodes.items() if node.kind != "validation"}


def _synthesis_node_ids(graph: ProvenanceGraph, document: str) -> set[str]:
    steps = [n for n in graph.nodes_of_kind("derivation") if n.stage == "synthesis"]  # type: ignore[attr-defined]
    owned = [s for s in steps if step_documents(s, graph.nodes) == {SOURCE_ID[document]}]  # type: ignore[arg-type]
    return {s.node_id for s in owned} | {out for s in owned for out in s.output_ids}  # type: ignore[attr-defined]


@pytest.mark.parametrize("emit_on_failure", [True, False], ids=["failed-after-emitting", "failed-before-emitting"])
def test_a_stage_failed_for_one_document_leaves_only_that_document_undetermined(
    baseline: Finished, tmp_path: Path, emit_on_failure: bool
) -> None:
    options = RunOptions(
        fail_documents={(ALPHA, "synthesis"): _SYNTHESIS_FAILURE}, emit_on_failure=emit_on_failure
    )
    degraded = run_end_to_end(tmp_path / "degraded", options)
    on_disk = read_graph(degraded.path)
    assert on_disk == degraded.graph  # the whole degraded graph survives the write

    # 1.6 / 7.4: every other stage's nodes (and every other document's) are in the graph
    lost = _synthesis_node_ids(baseline.graph, ALPHA) if not emit_on_failure else set()
    assert _non_validation_nodes(on_disk) == _non_validation_nodes(baseline.graph) - lost
    assert emit_on_failure or lost  # the failure-before-emitting variant really did drop the claim and its step
    assert degraded.report.completed  # a failed stage does not make the chain unvalidated

    # 7.4 / 2.5: one stage segment names the stage, the document and the recorded reason
    assert len(on_disk.missing_segments) == 1, "a stage marked failed must leave one missing segment in the graph"
    (segment,) = on_disk.missing_segments
    assert (segment.stage, segment.record_kind) == ("synthesis", "stage")
    assert segment.reason == f"stage 'synthesis' failed for document '{SOURCE_ID[ALPHA]}': {_SYNTHESIS_FAILURE}"
    assert segment.affected_ids[0] == SOURCE_ID[ALPHA]
    alpha_steps = {n for n in _synthesis_node_ids(baseline.graph, ALPHA) if n.startswith("drv:")}
    assert set(segment.affected_ids[1:]) == (alpha_steps if emit_on_failure else set())

    # 2.5: undetermined with the recorded reason for the affected document and the run, nowhere else
    assert on_disk.completeness is not None and baseline.graph.completeness is not None
    after, before = on_disk.completeness, baseline.graph.completeness
    expected_reason = segment.reason
    for entry in (after.documents[SOURCE_ID[ALPHA]], after.run):
        assert entry.state == "undetermined" and entry.reason == expected_reason
        assert entry.responsible_stages == ("synthesis",)
    for other in (BETA, GAMMA):  # determinable, and exactly as without the failure
        assert after.documents[SOURCE_ID[other]] == before.documents[SOURCE_ID[other]]
    assert after.documents[SOURCE_ID[GAMMA]].state == "complete"
    assert after.documents[SOURCE_ID[BETA]].state == "incomplete"
    undetermined_claims = {c for c, e in after.claims.items() if e.state == "undetermined"}
    assert undetermined_claims == ({claim_id(ALPHA, 7, 1)} if emit_on_failure else set())
    for claim, entry in after.claims.items():  # every claim the failure does not cover is as before
        if claim not in undetermined_claims and claim in before.claims:
            assert entry == before.claims[claim], claim


def test_a_stage_failed_run_wide_makes_every_document_undetermined_and_keeps_the_nodes(
    baseline: Finished, tmp_path: Path
) -> None:
    reason = "synthetic quality-control crash"
    degraded = run_end_to_end(tmp_path / "degraded", RunOptions(fail_run_wide={"quality_control": reason}))
    on_disk = read_graph(degraded.path)

    assert on_disk == degraded.graph
    assert set(on_disk.nodes) == set(baseline.graph.nodes)  # nothing the other stages emitted is lost
    assert len(on_disk.missing_segments) == 1, "a stage marked failed run-wide must leave one missing segment"
    (segment,) = on_disk.missing_segments
    assert (segment.stage, segment.record_kind) == ("quality_control", "stage")
    assert segment.reason == f"stage 'quality_control' failed: {reason}"
    qc_steps = {n.node_id for n in on_disk.nodes_of_kind("derivation") if n.stage == "quality_control"}  # type: ignore[attr-defined]
    assert len(qc_steps) == 3 and set(segment.affected_ids) == qc_steps

    assert on_disk.completeness is not None
    for entry in (on_disk.completeness.run, *on_disk.completeness.documents.values()):
        assert entry.state == "undetermined" and entry.reason == segment.reason
        assert entry.responsible_stages == ("quality_control",)
    # claims are not covered by a stage that produced none of them: their own states stand
    assert on_disk.completeness.claims == baseline.graph.completeness.claims  # type: ignore[union-attr]
    assert degraded.report.completed


def test_a_failed_chain_validation_stage_leaves_the_run_undetermined_and_the_documents_untouched(
    baseline: Finished, tmp_path: Path
) -> None:
    run = make_run_identity()
    recorder = make_recorder(RunOptions(), run)
    record_run(recorder, run)
    recorder.mark_stage_failed(_CHAIN_VALIDATION, "synthetic validator crash")  # run-wide, before the sequence ends
    finished = finish_run(recorder, tmp_path / "degraded")
    on_disk = read_graph(finished.path)

    assert recorder.snapshot().stage_status[_CHAIN_VALIDATION] == "failed"  # a later completion cannot erase it
    assert len(on_disk.missing_segments) == 1, "a failed chain_validation stage must leave one missing segment"
    (segment,) = on_disk.missing_segments
    assert (segment.stage, segment.record_kind, segment.affected_ids) == (_CHAIN_VALIDATION, "stage", ())
    assert segment.reason == "stage 'chain_validation' failed: synthetic validator crash"
    assert _non_validation_nodes(on_disk) == _non_validation_nodes(baseline.graph)
    assert on_disk.completeness is not None and baseline.graph.completeness is not None
    assert on_disk.completeness.run.state == "undetermined"
    assert on_disk.completeness.run.reason == segment.reason
    assert on_disk.completeness.documents == baseline.graph.completeness.documents
    assert on_disk.completeness.claims == baseline.graph.completeness.claims


def test_a_stage_that_completed_without_emitting_is_reported_missing_not_aborted(
    baseline: Finished, tmp_path: Path
) -> None:
    """1.6 / 2.1 / 2.3: the other stages still assemble; the silent stage is named with the kinds it owed."""
    degraded = run_end_to_end(tmp_path / "degraded", RunOptions(silent_complete=frozenset({"synthesis"})))
    on_disk = read_graph(degraded.path)

    assert on_disk == degraded.graph and on_disk.missing_segments == ()  # nothing failed; nothing was lost to assembly
    synthesis_nodes = _synthesis_node_ids(baseline.graph, ALPHA) | _synthesis_node_ids(baseline.graph, GAMMA)
    assert _non_validation_nodes(on_disk) == _non_validation_nodes(baseline.graph) - synthesis_nodes
    assert degraded.report.completed

    assert on_disk.completeness is not None
    documents = on_disk.completeness.documents
    for silent_document in (ALPHA, GAMMA):  # beta's synthesis never ran: that is not a gap (see the next test)
        entry = documents[SOURCE_ID[silent_document]]
        assert (entry.state, entry.reason) == ("incomplete", "missing_records")
        assert entry.missing_record_kinds == ("claim", "derivation")
        assert entry.responsible_stages == ("synthesis",)
    assert documents[SOURCE_ID[BETA]] == baseline.graph.completeness.documents[SOURCE_ID[BETA]]  # type: ignore[union-attr]
    run = on_disk.completeness.run
    assert run.state == "incomplete"
    assert {"claim", "derivation"} <= set(run.missing_record_kinds) and "synthesis" in run.responsible_stages


def test_a_stage_that_never_ran_is_not_reported_missing(baseline: Finished, tmp_path: Path) -> None:
    """2.4 / design: ``not_run`` is never "missing"; the other stages' records assemble unchanged."""
    degraded = run_end_to_end(tmp_path / "degraded", RunOptions(silent_unmarked=frozenset({"synthesis"})))
    on_disk = read_graph(degraded.path)

    assert on_disk == degraded.graph and on_disk.missing_segments == ()
    assert degraded.recorded.stage_status["synthesis"] == "not_run"
    assert degraded.recorded.document_stage_status[SOURCE_ID[ALPHA]].get("synthesis") is None
    synthesis_nodes = _synthesis_node_ids(baseline.graph, ALPHA) | _synthesis_node_ids(baseline.graph, GAMMA)
    assert _non_validation_nodes(on_disk) == _non_validation_nodes(baseline.graph) - synthesis_nodes
    assert on_disk.completeness is not None and baseline.graph.completeness is not None
    assert on_disk.completeness.documents == baseline.graph.completeness.documents
    assert on_disk.completeness.run == baseline.graph.completeness.run


def test_a_run_that_emitted_nothing_but_sources_still_assembles_and_validates(tmp_path: Path) -> None:
    """1.6 at its limit: every stage after extraction is silent."""
    silent = frozenset({"quality_control", "evidence_index", "metadata_prefill", "extraction_chunk", "synthesis"})

    # never run, never marked: nothing was declared complete, so nothing is reported missing (see above)
    unmarked = run_end_to_end(tmp_path / "unmarked", RunOptions(silent_unmarked=silent))
    on_disk = read_graph(unmarked.path)
    assert on_disk == unmarked.graph and on_disk.missing_segments == ()
    assert Counter(n.kind for n in on_disk.nodes.values()) == {"source": 3, "validation": 1}
    assert unmarked.report.completed
    assert {(f.code, f.severity) for f in unmarked.report.findings} == {("orphan_node", "info")}  # lone sources only
    assert on_disk.completeness is not None
    assert {e.state for e in on_disk.completeness.documents.values()} == {"complete"}

    # the same run with every stage marked complete although it emitted nothing: every owed kind is named
    marked = run_end_to_end(tmp_path / "marked", RunOptions(silent_complete=silent))
    on_disk = read_graph(marked.path)
    assert on_disk == marked.graph and on_disk.missing_segments == ()
    assert marked.report.completed
    assert on_disk.completeness is not None
    for entry in (on_disk.completeness.run, *on_disk.completeness.documents.values()):
        assert (entry.state, entry.reason) == ("incomplete", "missing_records")
        assert entry.missing_record_kinds == ("claim", "derivation", "evidence")
        assert set(entry.responsible_stages) >= {"quality_control", "evidence_index", "metadata_prefill"}


# ---------------------------------------------------------------------------
# 4. The privacy carrier: no component present, supplied verbatim, malformed (9.2, 9.6, 9.7)
# ---------------------------------------------------------------------------


def _every_node_dict(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(raw["nodes"])


def test_without_a_privacy_component_every_node_carries_the_unknown_carrier(baseline: Finished) -> None:
    """9.7 / 9.3: nothing supplied a label or decision, none was invented, and the run still went through."""
    on_disk = read_graph(baseline.path)

    assert on_disk.nodes
    assert {node.kind for node in on_disk.nodes.values()} == {"source", "evidence", "claim", "derivation", "validation"}
    for node in (*baseline.graph.nodes.values(), *on_disk.nodes.values()):
        assert node.privacy == UNKNOWN_PRIVACY and node.privacy.state == "unknown"
        assert (node.privacy.label, node.privacy.decision) == (None, None)
        assert (node.privacy.supplied_by, node.privacy.supplied_at, node.privacy.rejection_reason) == (None, None, None)
    raw_nodes = _every_node_dict(_raw(baseline.path))
    assert len(raw_nodes) == len(on_disk.nodes)
    assert all(node["privacy"] == _UNKNOWN_CARRIER for node in raw_nodes)

    # the graph assembled, validated and completed without a single carrier complaint
    assert baseline.report.completed
    assert not [f for f in baseline.report.findings if f.code == "privacy_carrier_invalid"]
    assert baseline.completeness.run.state == "incomplete"  # for the defects of beta only, not for privacy
    assert on_disk.missing_segments == ()


def test_a_supplied_label_and_decision_are_stored_verbatim_and_change_nothing_else(
    baseline: Finished, tmp_path: Path
) -> None:
    """9.2 / 9.4: opaque strings are kept as given, never interpreted by validation or completeness."""
    supplied = {
        SOURCE_ID[ALPHA]: {"label": "opaque-label-1", "decision": "opaque-decision-1"},
        evidence_id(ALPHA, "s1"): {"label": "PUBLIC"},  # words with a policy ring to them mean nothing here
        claim_id(BETA, 9): {"label": "restricted", "decision": "deny"},
        evidence_id(GAMMA, "title"): {"label": "", "decision": ""},  # even empty strings are stored, not defaulted
    }
    finished = run_end_to_end(tmp_path / "labeled", RunOptions(carrier_inputs=supplied))
    on_disk = read_graph(finished.path)
    raw_nodes = _raw_nodes(_raw(finished.path))

    for node_id, given in supplied.items():
        carrier = on_disk.nodes[node_id].privacy
        assert carrier.state == "labeled"
        assert (carrier.label, carrier.decision) == (given["label"], given.get("decision"))
        assert (carrier.supplied_by, carrier.supplied_at) == ("privacy-component-synthetic", _CREATED_AT)  # 9.5
        assert raw_nodes[node_id]["privacy"] == {
            "state": "labeled", "label": given["label"], "decision": given.get("decision"),
            "supplied_by": "privacy-component-synthetic", "supplied_at": _CREATED_AT, "rejection_reason": None,
        }
    others = [n for node_id, n in raw_nodes.items() if node_id not in supplied]
    assert others and all(node["privacy"] == _UNKNOWN_CARRIER for node in others)

    # no interpretation: findings, completeness and structure are exactly those of the unlabelled run
    assert on_disk == finished.graph
    assert _findings_of(on_disk) == EXPECTED_FINDINGS
    assert on_disk.completeness == baseline.graph.completeness
    assert set(on_disk.nodes) == set(baseline.graph.nodes) and on_disk.edges == baseline.graph.edges


def test_a_malformed_carrier_input_is_rejected_with_a_finding_and_the_node_is_retained(
    baseline: Finished, tmp_path: Path
) -> None:
    """9.6: a non-conforming supply is not stored partially; it is rejected with a reason and a validation finding."""
    malformed = {
        evidence_id(ALPHA, "s2"): {"label": 5},  # label of the wrong type
        claim_id(ALPHA, 7): {"decision": "only-a-decision"},  # a decision without its label
        evidence_id(BETA, "s1"): {"label": "ok", "unexpected": "key"},  # an undeclared key
    }
    finished = run_end_to_end(tmp_path / "rejected", RunOptions(carrier_inputs=malformed))
    on_disk = read_graph(finished.path)
    raw_nodes = _raw_nodes(_raw(finished.path))

    for node_id in malformed:
        assert node_id in on_disk.nodes  # retained
        carrier = on_disk.nodes[node_id].privacy
        assert carrier.state == "rejected" and (carrier.label, carrier.decision) == (None, None)
        assert carrier.rejection_reason
        assert raw_nodes[node_id]["privacy"]["state"] == "rejected"
        assert raw_nodes[node_id]["privacy"]["rejection_reason"] == carrier.rejection_reason
    assert on_disk.nodes[evidence_id(ALPHA, "s2")].privacy.rejection_reason == "'label' must be a str, got int"

    privacy_findings = sorted((f.node_id, f.severity) for f in on_disk.validation.findings if f.code == "privacy_carrier_invalid")  # type: ignore[union-attr]
    assert privacy_findings == sorted((node_id, "error") for node_id in malformed)  # one each, none for the rest
    assert sorted(_findings_of(on_disk)) == sorted(
        [*EXPECTED_FINDINGS, *(("privacy_carrier_invalid", "error", node_id) for node_id in malformed)]
    )
    assert finished.report.completed and on_disk == finished.graph
    assert on_disk.completeness == baseline.graph.completeness  # a rejected carrier is a finding, not a completeness gap


# ---------------------------------------------------------------------------
# 5. A disabled recorder
# ---------------------------------------------------------------------------


def test_a_disabled_recorder_records_nothing_and_the_whole_sequence_still_runs(tmp_path: Path) -> None:
    run = make_run_identity()
    recorder = make_recorder(RunOptions(enabled=False), run)
    source = source_node_from_identity(
        _IDENTITIES[ALPHA], source_label=ALPHA, source_format="pdf", run_id=run.run_id
    )

    assert recorder.record_source(source) == source.node_id  # the id comes back so callers never branch on it
    record_run(recorder, run, RunOptions(enabled=False))  # nothing raises, whatever is emitted
    recorder.mark_stage_failed("synthesis", "ignored while disabled")
    recorder.issue_sink()("dangling_reference", "ignored while disabled", {"node_id": source.node_id})

    snapshot = recorder.snapshot()
    assert (snapshot.nodes, snapshot.edges, snapshot.findings) == ({}, (), ())
    assert snapshot.run == run
    assert set(snapshot.stage_status.values()) == {"not_run"} and not snapshot.document_stage_status

    finished = finish_run(recorder, tmp_path / "disabled")  # the run-end sequence is skippable but also safe to run
    assert finished.unvalidated_graph.nodes == {} and finished.unvalidated_graph.edges == ()
    assert finished.unvalidated_graph.missing_segments == ()
    assert finished.recorded.stage_status[_CHAIN_VALIDATION] == "not_run"  # the disabled recorder ignores the mark
    assert finished.report.completed and finished.report.findings == ()
    on_disk = read_graph(finished.path)
    assert on_disk == finished.graph
    assert [node.kind for node in on_disk.nodes.values()] == ["validation"]  # only the attached validation record
    # never defaulted to complete (2.5): the run names its documents, and with nothing recorded for them
    # the run and every document read undetermined, each with a reason
    assert on_disk.completeness is not None and on_disk.completeness.claims == {}
    assert set(on_disk.completeness.documents) == set(SOURCE_ID.values())
    for entry in (on_disk.completeness.run, *on_disk.completeness.documents.values()):
        assert entry.state == "undetermined" and entry.reason


# ---------------------------------------------------------------------------
# 6. Hostile but well-typed use: duplicates and concurrency
# ---------------------------------------------------------------------------


def test_recording_a_document_twice_is_a_no_op(baseline: Finished, tmp_path: Path) -> None:
    run = make_run_identity()
    recorder = make_recorder(RunOptions(), run)
    record_run(recorder, run)
    before = recorder.snapshot()

    record_document(recorder, run, ALPHA)  # an idempotent retry of a whole document
    record_document(recorder, run, GAMMA)

    after = recorder.snapshot()
    assert after.nodes == before.nodes and after.edges == before.edges and after.findings == before.findings
    assert not [f for f in after.findings if f.code == "duplicate_identity"]
    assert finish_run(recorder, tmp_path / "retried").path.read_bytes() == baseline.path.read_bytes()


def test_a_conflicting_duplicate_keeps_the_first_record_and_surfaces_an_error_finding(
    baseline: Finished, tmp_path: Path
) -> None:
    run = make_run_identity()
    recorder = make_recorder(RunOptions(), run)
    record_run(recorder, run)
    conflicting = source_node_from_identity(
        _IDENTITIES[ALPHA], source_label="not-alpha.pdf", source_format="pdf", run_id=run.run_id
    )
    assert recorder.record_source(conflicting) == SOURCE_ID[ALPHA]
    recorder.record_source(conflicting)  # the same conflict again is not a second finding

    finished = finish_run(recorder, tmp_path / "conflict")
    on_disk = read_graph(finished.path)

    assert on_disk.nodes[SOURCE_ID[ALPHA]].source_label == ALPHA  # type: ignore[attr-defined]  # the first record is kept
    duplicates = [f for f in on_disk.validation.findings if f.code == "duplicate_identity"]  # type: ignore[union-attr]
    assert [(f.severity, f.node_id) for f in duplicates] == [("error", SOURCE_ID[ALPHA])]
    assert duplicates[0].details["differing_fields"] == ["source_label"]
    assert _findings_of(on_disk) == sorted([*EXPECTED_FINDINGS, ("duplicate_identity", "error", SOURCE_ID[ALPHA])])
    assert finished.report.completed
    assert set(on_disk.nodes) == set(baseline.graph.nodes)  # the conflicting record added nothing
    structural = [e for e in on_disk.edges if e.kind != "validates"]  # the new finding adds one ``validates`` edge
    assert structural == [e for e in baseline.graph.edges if e.kind != "validates"]


def test_concurrent_recording_from_several_threads_yields_the_sequential_artifact(
    baseline: Finished, tmp_path: Path
) -> None:
    for attempt in range(5):
        run = make_run_identity()
        recorder = make_recorder(RunOptions(), run)
        # one thread per document, plus a second alpha writer racing the first (an idempotent retry)
        workers = [ALPHA, BETA, GAMMA, ALPHA]
        barrier = threading.Barrier(len(workers))
        errors: list[BaseException] = []

        def work(document: str) -> None:
            try:
                barrier.wait(timeout=10)
                record_document(recorder, run, document)
            except BaseException as exc:  # noqa: BLE001 -- reported on the main thread below
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(document,)) for document in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not errors and not any(thread.is_alive() for thread in threads)

        finished = finish_run(recorder, tmp_path / f"threads-{attempt}")
        assert finished.graph == baseline.graph
        assert finished.path.read_bytes() == baseline.path.read_bytes()
        assert not [f for f in finished.report.findings if f.code == "duplicate_identity"]


# ---------------------------------------------------------------------------
# 7. Environment: no network, no model key, quick, deterministic, light imports (1.5)
# ---------------------------------------------------------------------------


def test_the_run_needs_neither_a_model_key_nor_the_network(baseline: Finished) -> None:
    assert "OPENAI_API_KEY" not in os.environ
    with pytest.raises(AssertionError, match="network access"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # the guard is live for every test of this module
    assert read_graph(baseline.path) == baseline.graph  # ... and the whole sequence ran under it


def test_the_whole_sequence_is_quick_and_leaves_only_the_artifact_under_tmp_path(tmp_path: Path) -> None:
    started = time.perf_counter()
    finished = run_end_to_end(tmp_path / "timed")
    read_graph(finished.path)
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0, f"the fabricated run took {elapsed:.2f}s"
    written = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    assert written == [f"timed/{ARTIFACT_FILENAME}"]


def test_artifact_bytes_do_not_depend_on_recording_order_of_documents(baseline: Finished, tmp_path: Path) -> None:
    run = make_run_identity()
    recorder = make_recorder(RunOptions(), run)
    for document in reversed(DOCUMENTS):
        record_document(recorder, run, document)

    assert finish_run(recorder, tmp_path / "reversed").path.read_bytes() == baseline.path.read_bytes()


def test_this_module_imports_nothing_heavy_and_uses_no_private_provenance_name() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    private: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            roots.add(root)
            if root == "provenance":
                private.extend(alias.name for alias in node.names if alias.name.startswith("_"))
    assert roots <= {
        "__future__", "ast", "collections", "dataclasses", "importlib", "json", "os", "pathlib", "provenance",
        "pytest", "socket", "sys", "threading", "time", "typing",
    }
    assert private == []


# ---------------------------------------------------------------------------
# 8. The production run-end routine writes the same artifact as the documented sequence
# ---------------------------------------------------------------------------


@pytest.fixture
def run_provenance_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """``pipeline.run_provenance``, imported fresh and lazily, then the previous ``pipeline*`` modules restored.

    Other test modules evict ``pipeline*`` from ``sys.modules`` at import time or leave stubs of it behind
    (``test_pdf_processor_helpers``, ``test_orchestrator_concurrency``); a fresh import here does not depend on
    what they left, and nothing this test imports leaks into the modules they or later tests hold.
    """

    def is_pipeline(name: str) -> bool:
        return name == "pipeline" or name.startswith("pipeline.")

    saved = {name: module for name, module in sys.modules.items() if is_pipeline(name)}
    for name in saved:
        monkeypatch.delitem(sys.modules, name)
    try:
        yield importlib.import_module("pipeline.run_provenance")
    finally:
        for name in [name for name in sys.modules if is_pipeline(name)]:
            del sys.modules[name]
        sys.modules.update(saved)


def test_finish_run_provenance_writes_the_artifact_the_documented_sequence_writes(
    baseline: Finished, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_provenance_module: Any
) -> None:
    rp = run_provenance_module
    artifact_dir = tmp_path / "production"
    monkeypatch.setattr(rp, "artifact_directory", lambda settings: artifact_dir)

    run = make_run_identity()
    recorder = make_recorder(RunOptions(), run)
    record_run(recorder, run)
    plan = rp.RunProvenance(rp.ProvenanceSettings(), recorder, run.run_id, {}, {})

    summary = rp.finish_run_provenance(plan)

    assert summary.errors == () and summary.validated is True
    # the stage mark is not recoverable from the artifact (a recorded validation record reads the same), so
    # the recorder is where "chain_validation completed" is observable
    assert recorder.snapshot().stage_status[_CHAIN_VALIDATION] == "complete"
    assert summary.artifact_path == artifact_dir / ARTIFACT_FILENAME
    # created_at comes from the run identity, so nothing differs: the artifacts are equal byte for byte
    assert summary.artifact_path.read_bytes() == baseline.path.read_bytes()
    assert read_graph(summary.artifact_path) == baseline.graph
    assert summary.completeness_state == baseline.graph.completeness.run.state  # type: ignore[union-attr]
    assert dict(summary.finding_counts) == {"critical": 1, "warning": 5}
