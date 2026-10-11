"""Claim nodes and extraction derivation steps from ``process_pdf`` (provenance-core 6.4).

``process_pdf(..., provenance=DocumentProvenance)`` records, after prompt
assembly and response parsing, one claim node per final field with its
``cites_evidence`` edges, probabilistic per-chunk / synthesis derivation steps
carrying the model identifier, and a deterministic ``prefill`` step for the
locally prefilled author and year (origin ``prefill``, citing the TEI header
metadata node the value came from, or nothing for a fallback). Candidates that
a conflict or merge resolved become earlier revisions of the final claim, so
``revises`` edges exist on the real path. Reference problems in model output are
recorded as findings naming the claim while the field survives.

Everything runs on fabricated chunk results and a real ``ProvenanceRecorder``:
no GROBID, network or OpenAI. With ``provenance=None`` the code path is the
unmodified one.

Requirements: 2.5, 5.1, 5.4, 5.5, 5.7, 6.1, 6.7.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pipeline.pdf_processor as pdf_processor
from pipeline import document_provenance as dp
from pipeline.manifest import ManifestIdentity
from provenance.completeness import compute_completeness
from provenance.graph import attach_completeness, attach_validation, build_graph
from provenance.identity import derive_claim_id, derive_source_id, format_evidence_id
from provenance.models import ClaimNode, DerivationStep
from provenance.recorder import ProvenanceRecorder
from provenance.serialization import read_graph, write_graph
from provenance.validation import validate_chain
from quality_control.models import QCBundle, UnifiedRecord
from tests.helpers.grobid_tei import load_tei_text

_TEI_NS = "http://www.tei-c.org/ns/1.0"
_CHUNK_MODEL = "gpt-chunk"
_SYNTH_MODEL = "gpt-synth"

_TEI = (
    f'<TEI xmlns="{_TEI_NS}"><teiHeader><fileDesc><titleStmt><title>Sample Title</title></titleStmt>'
    "<sourceDesc><biblStruct><analytic><author><persName><surname>Smith</surname></persName></author></analytic>"
    '<monogr><imprint><date when="2021"/></imprint></monogr></biblStruct></sourceDesc></fileDesc></teiHeader>'
    '<text><body><div><head>Methods</head><p><s coords="1,10,20,20,20">We enrolled 120 patients.</s>'
    '<s coords="1,10,40,20,20">This was a randomized trial.</s>'
    '<s coords="2,10,20,20,20">Outcomes were mortality.</s></p></div></body></text></TEI>'
)

_QC_PROVENANCE = {"primary_branch_source": "grobid", "branch_selection": "adjudicated"}

_FIELD_LOOKUP = {
    1: {"domain_group": 1, "field_name": "First author"},
    2: {"domain_group": 1, "field_name": "Publication year"},
    3: {"domain_group": 2, "field_name": "Study design"},
    4: {"domain_group": 2, "field_name": "Sample size"},
    5: {"domain_group": 3, "field_name": "Reviewer notes"},
}


def _def(index: int) -> dict[str, Any]:
    return {"field_index": index, **_FIELD_LOOKUP[index]}


def _chunk_fields(layout: dict[int, list[int]]) -> dict[int, list[dict[str, Any]]]:
    return {chunk: [_def(i) for i in indices] for chunk, indices in layout.items()}


#: chunk 1 -> fields 1 (prefilled), 2 (prefilled), 3; chunk 2 -> 4; synthesis chunk 3 -> 5
_DEFAULT_LAYOUT = {1: [1, 2, 3], 2: [4], 3: [5]}


def _ext(*items: tuple[int, str, list[str], str]) -> str:
    return json.dumps({"extractions": [{"i": i, "v": v, "loc": loc, "c": c} for i, v, loc, c in items]})


def _default_script() -> dict[int, list[Any]]:
    return {
        1: [_ext((3, "RCT", ["S000001"], "h"))],
        2: [_ext((4, "120", ["S000002", "S000003"], "m"))],
        3: [_ext((5, "Sound", ["S000001"], "m"))],
    }


class _Script:
    """``extract_chunk`` stand-in: per-chunk queued responses, every call recorded."""

    def __init__(self, responses: dict[int, list[Any]]) -> None:
        self._responses = {chunk: list(queue) for chunk, queue in responses.items()}
        self.calls: list[Any] = []

    async def call(self, chunk_num: int, source: str, fields: Any, semaphore: Any, **kwargs: Any) -> str:
        self.calls.append(copy.deepcopy(((chunk_num, source, fields), kwargs)))
        queue = self._responses[chunk_num]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item


class _World:
    """One recorder, one run identity, N documents."""

    def __init__(self, tmp_path: Path, names: tuple[str, ...] = ("a",)) -> None:
        self.tmp_path = tmp_path
        self.identities = {
            name: ManifestIdentity(
                pdf_content_hash=hashlib.sha256(name.encode()).hexdigest(),
                config_hash="c" * 64,
                extraction_map_hash="e" * 64,
                model_id=_CHUNK_MODEL,
                schema_version="1.0.0",
                output_path=f"{name}.extracted.json",
            )
            for name in names
        }
        run = dp.build_run_identity(
            self.identities, chunk_model=_CHUNK_MODEL, synthesis_model=_SYNTH_MODEL, created_at="2026-10-11T00:00:00Z"
        )
        assert run is not None
        self.run = run
        self.recorder = ProvenanceRecorder(run)

    def source_id(self, name: str) -> str:
        return derive_source_id(self.identities[name].pdf_content_hash)

    def provenance(self, name: str) -> dp.DocumentProvenance:
        return dp.DocumentProvenance(self.recorder, self.identities[name], self.run.run_id)


def _config(cache_dir: Path, **overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "chunk_model": _CHUNK_MODEL,
        "synthesis_model": _SYNTH_MODEL,
        "enable_cache_prewarm": False,
        "num_chunks": 3,
        "prewarm_synthesis_if_model_diff": False,
        "max_evidence_items_per_chunk": 150,
        "max_evidence_chars_per_chunk": 30000,
        "evidence_cache_dir": str(cache_dir),
        "addons": {},
        "crop_figures": False,
        "crop_tables": False,
    }
    config.update(overrides)
    return config


def _qc_context(tmp_path: Path, name: str, tei: str) -> QCBundle:
    pdf = tmp_path / f"{name}.pdf"
    if not pdf.exists():
        pdf.write_bytes(f"%PDF-1.4 {name}".encode())
    unified = UnifiedRecord(
        document_id=name,
        content={
            "exact_text": "We enrolled 120 patients. This was a randomized trial. Outcomes were mortality.",
            "source_pdf_path": str(pdf),
            "grobid_tei_xml": tei,
            "provenance": copy.deepcopy(_QC_PROVENANCE),
        },
    )
    return QCBundle(branches=[], unified=unified)


def _process(
    tmp_path: Path,
    name: str = "a",
    *,
    world: _World | None = None,
    use_provenance: bool = True,
    tei: str = _TEI,
    layout: dict[int, list[int]] | None = None,
    responses: dict[int, list[Any]] | None = None,
    label: str = "run",
    manifest: dict | None = None,
    **config_overrides: Any,
) -> SimpleNamespace:
    """Run ``process_pdf`` once; return result, calls, manifest and output folder."""
    out_dir = tmp_path / f"out_{label}_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    qc = _qc_context(tmp_path, name, tei)
    script = _Script(responses if responses is not None else _default_script())
    api = MagicMock()
    api.extract_chunk = script.call
    api.warm_pdf_cache = AsyncMock()
    manifest = {} if manifest is None else manifest
    provenance = None
    if use_provenance:
        assert world is not None
        provenance = world.provenance(name)
        dp.emit_document_provenance(
            world.recorder, qc, world.identities[name], pdf_name=name, run_id=world.run.run_id
        )
    config = _config(tmp_path / f"cache_{label}_{name}", **config_overrides)
    kwargs: dict[str, Any] = {"provenance": provenance} if use_provenance else {}

    async def go() -> Any:
        return await pdf_processor.process_pdf(
            qc_context=qc,
            chunk_fields=_chunk_fields(layout or _DEFAULT_LAYOUT),
            field_lookup=_FIELD_LOOKUP,
            api_semaphore=asyncio.Semaphore(5),
            manifest=manifest,
            manifest_lock=asyncio.Lock(),
            openai_config=config,
            **kwargs,
        )

    with patch.object(pdf_processor, "OUTPUT_DIR", out_dir), patch.dict(
        sys.modules, {"agents.openai.api_client": api}
    ), patch.object(pdf_processor, "validate_qc_context_input"), patch.object(
        pdf_processor, "save_manifest"
    ):
        result = asyncio.run(go())
    return SimpleNamespace(result=result, script=script, manifest=manifest, out_dir=out_dir, qc=qc)


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def _claims(snapshot: Any) -> dict[str, ClaimNode]:
    return {nid: n for nid, n in snapshot.nodes.items() if n.kind == "claim"}


def _steps(snapshot: Any, stage: str | None = None) -> list[DerivationStep]:
    return [
        n
        for n in snapshot.nodes.values()
        if n.kind == "derivation" and (stage is None or n.stage == stage)
    ]


def _edges(snapshot: Any, kind: str) -> list[Any]:
    return [e for e in snapshot.edges if e.kind == kind]


def _cid(world: _World, name: str, index: int, revision: int = 0) -> str:
    return derive_claim_id(world.source_id(name), index, revision)


def _eid(world: _World, name: str, local_id: str) -> str:
    return format_evidence_id(world.source_id(name), local_id)


def _graph_reports(world: _World) -> Any:
    """Mirror the orchestrator's run end: validate, mark chain_validation, completeness."""
    snapshot = world.recorder.snapshot()
    graph = build_graph(snapshot)
    report = validate_chain(graph, recorded_findings=snapshot.findings)
    validated = attach_validation(graph, report)
    world.recorder.mark_stage_complete("chain_validation")
    completeness = compute_completeness(validated, world.recorder.snapshot())
    return SimpleNamespace(
        graph=attach_completeness(validated, completeness), report=report, completeness=completeness
    )


# ---------------------------------------------------------------------------
# Claims, edges, origin, steps
# ---------------------------------------------------------------------------


def test_one_claim_node_per_final_field(tmp_path):
    world = _World(tmp_path)
    run = _process(tmp_path, world=world)
    assert run.result is not None
    assert sorted(f["field_index"] for f in run.result) == [1, 2, 3, 4, 5]
    snapshot = world.recorder.snapshot()
    assert sorted(_claims(snapshot)) == sorted(_cid(world, "a", i) for i in range(1, 6))


def test_model_claims_cite_the_evidence_nodes_they_name_and_prefill_claims_are_not_model(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    snapshot = world.recorder.snapshot()
    claims = _claims(snapshot)
    assert claims[_cid(world, "a", 3)].origin == "model"
    assert claims[_cid(world, "a", 3)].cited_evidence_ids == (_eid(world, "a", "S000001"),)
    assert claims[_cid(world, "a", 4)].cited_evidence_ids == (_eid(world, "a", "S000002"), _eid(world, "a", "S000003"))
    assert claims[_cid(world, "a", 5)].origin == "model"
    assert claims[_cid(world, "a", 1)].origin == "prefill"
    assert claims[_cid(world, "a", 2)].origin == "prefill"
    cites = {(e.from_id, e.to_id) for e in _edges(snapshot, "cites_evidence")}
    assert (_cid(world, "a", 3), _eid(world, "a", "S000001")) in cites
    assert (_cid(world, "a", 4), _eid(world, "a", "S000003")) in cites
    for _, evidence_id in cites:
        assert evidence_id in snapshot.nodes  # every cited node exists


def test_model_claims_citing_anchored_evidence_are_supported_and_header_citing_prefill_is_missing_anchor(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    claims = _claims(world.recorder.snapshot())
    assert claims[_cid(world, "a", 3)].support == "supported"
    assert claims[_cid(world, "a", 4)].support == "supported"
    # the header metadata nodes have no usable location, so they are anchorless
    assert claims[_cid(world, "a", 1)].support == "missing_anchor"
    assert claims[_cid(world, "a", 2)].support == "missing_anchor"


def test_chunk_and_synthesis_steps_are_probabilistic_and_carry_the_model_identifier(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    snapshot = world.recorder.snapshot()
    chunk_steps = _steps(snapshot, "extraction_chunk")
    assert len(chunk_steps) == 2
    for step in chunk_steps:
        assert (step.derivation_kind, step.determinism, step.model_id) == ("extract", "probabilistic", _CHUNK_MODEL)
    assert sorted(step.output_ids for step in chunk_steps) == sorted(
        [(_cid(world, "a", 3),), (_cid(world, "a", 4),)]
    )
    (synthesis,) = _steps(snapshot, "synthesis")
    assert (synthesis.derivation_kind, synthesis.determinism, synthesis.model_id) == (
        "synthesize",
        "probabilistic",
        _SYNTH_MODEL,
    )
    assert synthesis.output_ids == (_cid(world, "a", 5),)


def test_step_inputs_and_outputs_are_node_ids_only(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    snapshot = world.recorder.snapshot()
    for step in _steps(snapshot):
        for node_id in (*step.input_ids, *step.output_ids, *step.removed_ids):
            assert node_id in snapshot.nodes, (step.stage, node_id)
    chunk_one = next(s for s in _steps(snapshot, "extraction_chunk") if _cid(world, "a", 3) in s.output_ids)
    assert chunk_one.input_ids == (_eid(world, "a", "S000001"),)


def test_prefill_step_is_deterministic_and_lists_the_claims_it_produced(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    (step,) = _steps(world.recorder.snapshot(), "metadata_prefill")
    assert (step.derivation_kind, step.determinism, step.model_id) == ("prefill", "deterministic", None)
    assert step.output_ids == (_cid(world, "a", 1), _cid(world, "a", 2))
    assert step.input_ids == (_eid(world, "a", "M-first-author"), _eid(world, "a", "M-publication-date"))


def test_header_sourced_prefill_cites_the_header_nodes(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    snapshot = world.recorder.snapshot()
    claims = _claims(snapshot)
    assert claims[_cid(world, "a", 1)].cited_evidence_ids == (_eid(world, "a", "M-first-author"),)
    assert claims[_cid(world, "a", 2)].cited_evidence_ids == (_eid(world, "a", "M-publication-date"),)


def test_fallback_prefill_cites_nothing(tmp_path):
    world = _World(tmp_path)
    no_header = f'<TEI xmlns="{_TEI_NS}"><text><body><div><head>M</head><p><s coords="1,1,1,1,1">One.</s></p></div></body></text></TEI>'
    _process(tmp_path, world=world, tei=no_header)
    snapshot = world.recorder.snapshot()
    claims = _claims(snapshot)
    for index in (1, 2):
        assert claims[_cid(world, "a", index)].origin == "prefill"
        assert claims[_cid(world, "a", index)].cited_evidence_ids == ()
        assert claims[_cid(world, "a", index)].support == "unsupported"
    (step,) = _steps(snapshot, "metadata_prefill")
    assert step.input_ids == ()
    assert step.output_ids == (_cid(world, "a", 1), _cid(world, "a", 2))


def test_header_author_with_a_fallback_year_cites_only_the_author_node(tmp_path):
    world = _World(tmp_path)
    no_date = _TEI.replace('<monogr><imprint><date when="2021"/></imprint></monogr>', "")
    _process(tmp_path, world=world, tei=no_date)
    claims = _claims(world.recorder.snapshot())
    assert claims[_cid(world, "a", 1)].cited_evidence_ids == (_eid(world, "a", "M-first-author"),)
    assert claims[_cid(world, "a", 2)].cited_evidence_ids == ()


@pytest.mark.parametrize(
    "fixture,cites_author,cites_year",
    [("arxiv", True, True), ("plosone", True, True), ("biorxiv", True, False)],
)
def test_real_grobid_fixtures_cite_a_header_node_only_for_header_sourced_values(tmp_path, fixture, cites_author, cites_year):
    world = _World(tmp_path)
    run = _process(tmp_path, world=world, tei=load_tei_text(fixture), responses={
        1: [_ext((3, "RCT", ["S000001"], "h"))],
        2: [_ext((4, "120", ["S000002"], "m"))],
        3: [_ext((5, "Sound", ["S000003"], "m"))],
    })
    assert run.result is not None
    claims = _claims(world.recorder.snapshot())
    assert bool(claims[_cid(world, "a", 1)].cited_evidence_ids) is cites_author
    assert bool(claims[_cid(world, "a", 2)].cited_evidence_ids) is cites_year


def test_claims_describe_the_persisted_values(tmp_path):
    from provenance.identity import derive_value_digest

    world = _World(tmp_path)
    run = _process(tmp_path, world=world, sanitize_extracted_values=True)
    saved = json.loads((run.out_dir / "a.extracted.json").read_text(encoding="utf-8"))
    claims = _claims(world.recorder.snapshot())
    for field in saved:
        assert claims[_cid(world, "a", field["field_index"])].value_digest == derive_value_digest(field["extracted_value"])


# ---------------------------------------------------------------------------
# Stage marks
# ---------------------------------------------------------------------------


def test_each_claim_stage_that_ran_is_complete_with_one_producing_step_per_stage(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    snapshot = world.recorder.snapshot()
    status = snapshot.document_stage_status[world.source_id("a")]
    for stage in ("metadata_prefill", "extraction_chunk", "synthesis", "evidence_index"):
        assert status[stage] == "complete", stage
    assert snapshot.stage_status["extraction_chunk"] == "not_run"  # never run-wide


def test_a_stage_that_did_not_run_is_not_marked(tmp_path):
    world = _World(tmp_path)
    # one chunk: no extraction chunk, the synthesis chunk does everything
    _process(
        tmp_path,
        world=world,
        num_chunks=1,
        layout={1: [1, 2, 3]},
        responses={1: [_ext((3, "RCT", ["S000001"], "h"))]},
    )
    status = world.recorder.snapshot().document_stage_status[world.source_id("a")]
    assert "extraction_chunk" not in status
    assert status["synthesis"] == "complete"
    assert status["metadata_prefill"] == "complete"


def test_extraction_chunk_failure_marks_that_stage_failed_with_the_manifest_reason(tmp_path):
    world = _World(tmp_path)
    run = _process(
        tmp_path,
        world=world,
        responses={1: ["not json"], 2: [_ext((4, "1", ["S000001"], "m"))], 3: [_ext((5, "x", [], "m"))]},
    )
    assert run.result is None
    snapshot = world.recorder.snapshot()
    sid = world.source_id("a")
    assert snapshot.document_stage_status[sid]["extraction_chunk"] == "failed"
    assert snapshot.document_stage_failure_reasons[sid]["extraction_chunk"]
    assert "synthesis" not in snapshot.document_stage_status[sid]
    assert not _claims(snapshot)  # nothing was persisted, nothing is claimed


def test_synthesis_failure_marks_synthesis_failed(tmp_path):
    world = _World(tmp_path)
    responses = _default_script()
    responses[3] = [RuntimeError("synthesis boom")]
    run = _process(tmp_path, world=world, responses=responses)
    assert run.result is None
    sid = world.source_id("a")
    snapshot = world.recorder.snapshot()
    assert snapshot.document_stage_status[sid]["synthesis"] == "failed"
    assert "synthesis boom" in snapshot.document_stage_failure_reasons[sid]["synthesis"][0]


def test_an_output_that_cannot_be_persisted_marks_the_claim_stages_failed(tmp_path):
    world = _World(tmp_path)
    failure = {"stage": "schema_validation", "chunk": None, "error_type": "schema", "last_error": "bad", "attempts": 1}
    with patch.object(pdf_processor, "_save_pdf_output", return_value=(False, failure)):
        run = _process(tmp_path, world=world)
    assert run.result is None
    snapshot = world.recorder.snapshot()
    status = snapshot.document_stage_status[world.source_id("a")]
    assert {status[s] for s in ("metadata_prefill", "extraction_chunk", "synthesis")} == {"failed"}
    assert not _claims(snapshot)


def test_a_failure_in_one_document_leaves_the_other_determinable(tmp_path):
    world = _World(tmp_path, ("a", "b"))
    _process(tmp_path, "a", world=world)
    failing = _default_script()
    failing[3] = [RuntimeError("synthesis boom")]
    run_b = _process(tmp_path, "b", world=world, responses=failing)
    assert run_b.result is None
    reports = _graph_reports(world)
    docs = reports.completeness.documents
    assert docs[world.source_id("a")].state != "undetermined"
    assert docs[world.source_id("b")].state == "undetermined"
    assert "synthesis" in (docs[world.source_id("b")].responsible_stages or ("synthesis",))
    # the first document's claims are still all there and determinable
    claims_a = [c for c in reports.completeness.claims if c.startswith(f"claim:{world.source_id('a')}")]
    assert claims_a
    assert all(reports.completeness.claims[c].state != "undetermined" for c in claims_a)


# ---------------------------------------------------------------------------
# Revisions (Requirement 5.5)
# ---------------------------------------------------------------------------


def _conflict_responses() -> dict[int, list[Any]]:
    return {
        1: [_ext((3, "RCT", ["S000001"], "h"))],
        2: [_ext((3, "Cohort", ["S000002"], "m"))],
        3: [_ext((3, "RCT", ["S000001", "S000002"], "m"))],
    }


_CONFLICT_LAYOUT = {1: [1, 2, 3], 2: [3], 3: []}


def test_a_synthesized_claim_links_to_each_contributing_candidate(tmp_path):
    world = _World(tmp_path)
    run = _process(tmp_path, world=world, layout=_CONFLICT_LAYOUT, responses=_conflict_responses())
    assert run.result is not None
    snapshot = world.recorder.snapshot()
    final, first, second = _cid(world, "a", 3, 2), _cid(world, "a", 3, 0), _cid(world, "a", 3, 1)
    claims = _claims(snapshot)
    assert {final, first, second} <= set(claims)
    assert {(e.from_id, e.to_id) for e in _edges(snapshot, "revises")} == {(final, first), (final, second)}
    assert claims[final].cited_evidence_ids == (_eid(world, "a", "S000001"), _eid(world, "a", "S000002"))
    assert claims[first].cited_evidence_ids == (_eid(world, "a", "S000001"),)
    # the persisted field is the synthesized one
    assert next(f for f in run.result if f["field_index"] == 3)["extracted_value"] == "RCT"


def test_conflict_candidates_come_from_the_chunk_steps_and_feed_the_synthesis_step(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world, layout=_CONFLICT_LAYOUT, responses=_conflict_responses())
    snapshot = world.recorder.snapshot()
    final, first, second = _cid(world, "a", 3, 2), _cid(world, "a", 3, 0), _cid(world, "a", 3, 1)
    produced = {out for step in _steps(snapshot, "extraction_chunk") for out in step.output_ids}
    assert {first, second} <= produced
    (synthesis,) = _steps(snapshot, "synthesis")
    assert synthesis.output_ids == (final,)
    assert {first, second} <= set(synthesis.input_ids)
    assert synthesis.model_id == _SYNTH_MODEL
    reports = _graph_reports(world)
    assert not [f for f in reports.report.findings if f.code in ("graph_cycle", "dangling_reference", "inconsistent_derivation")]


def test_agreeing_candidates_from_two_chunks_are_merged_into_a_revision_with_a_merge_step(tmp_path):
    world = _World(tmp_path)
    responses = {
        1: [_ext((3, "RCT", ["S000001"], "h"))],
        2: [_ext((3, " RCT ", ["S000002"], "m"))],
        3: [_ext((5, "x", [], "m"))],
    }
    run = _process(tmp_path, world=world, layout={1: [1, 2, 3], 2: [3], 3: [5]}, responses=responses)
    assert run.result is not None
    snapshot = world.recorder.snapshot()
    final, first, second = _cid(world, "a", 3, 2), _cid(world, "a", 3, 0), _cid(world, "a", 3, 1)
    assert {(e.from_id, e.to_id) for e in _edges(snapshot, "revises")} == {(final, first), (final, second)}
    merge = [s for s in _steps(snapshot, "extraction_chunk") if s.derivation_kind == "merge"]
    assert len(merge) == 1
    assert (merge[0].determinism, merge[0].model_id) == ("deterministic", None)
    assert set(merge[0].input_ids) == {first, second}
    assert merge[0].output_ids == (final,)


def test_a_candidate_dropped_from_the_synthesis_input_is_recorded_as_a_removal(tmp_path):
    world = _World(tmp_path)
    n = 7  # more extraction chunks than the synthesis candidate cap (5)
    layout: dict[int, list[int]] = {chunk: [3] for chunk in range(1, n + 1)}
    layout[n + 1] = []
    confidences = ["l", "l", "h", "h", "m", "m", "h"]
    responses: dict[int, list[Any]] = {
        chunk: [_ext((3, f"v{chunk}", ["S000001"], confidences[chunk - 1]))] for chunk in range(1, n + 1)
    }
    responses[n + 1] = [_ext((3, "final", ["S000001"], "m"))]
    run = _process(tmp_path, world=world, layout=layout, responses=responses, num_chunks=n + 1)
    assert run.result is not None
    snapshot = world.recorder.snapshot()
    removals = [s for s in _steps(snapshot) if s.derivation_kind == "remove"]
    assert len(removals) == 1
    (removal,) = removals
    assert removal.stage == "synthesis"
    assert removal.removal_reason
    # chunks 1 and 2 (the two "l" candidates) are the ones cut by the cap
    assert set(removal.removed_ids) == {_cid(world, "a", 3, 0), _cid(world, "a", 3, 1)}
    final = _cid(world, "a", 3, n)
    assert {e.to_id for e in _edges(snapshot, "revises") if e.from_id == final} == {
        _cid(world, "a", 3, k) for k in range(2, n)
    }


def test_no_removal_step_is_recorded_on_a_normal_run(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    assert not [s for s in _steps(world.recorder.snapshot()) if s.derivation_kind == "remove"]


# ---------------------------------------------------------------------------
# Reference problems (Requirement 5.4)
# ---------------------------------------------------------------------------


def _unknown_id_responses() -> dict[int, list[Any]]:
    responses = _default_script()
    responses[1] = [_ext((3, "RCT", ["S999999"], "h"))]  # every attempt, including both repairs
    return responses


def test_an_unknown_evidence_id_after_exhaustion_is_a_finding_naming_the_claim_and_the_field_survives(tmp_path):
    world = _World(tmp_path)
    run = _process(tmp_path, world=world, responses=_unknown_id_responses())
    assert run.result is not None
    assert next(f for f in run.result if f["field_index"] == 3)["extracted_value"] == "RCT"
    assert run.manifest["a"]["status"] == "complete"
    saved = json.loads((run.out_dir / "a.extracted.json").read_text(encoding="utf-8"))
    assert {f["field_index"] for f in saved} == {1, 2, 3, 4, 5}
    claim_id = _cid(world, "a", 3)
    snapshot = world.recorder.snapshot()
    assert claim_id in _claims(snapshot)
    dangling = [f for f in snapshot.findings if f.code == "dangling_reference" and f.node_id == claim_id]
    assert dangling
    assert all(f.details["reference"] == "S999999" for f in dangling)
    assert _claims(snapshot)[claim_id].support == "unsupported"


def test_the_merged_finding_is_reported_once_by_chain_validation(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world, responses=_unknown_id_responses())
    reports = _graph_reports(world)
    claim_id = _cid(world, "a", 3)
    matching = [f for f in reports.report.findings if f.code == "dangling_reference" and f.node_id == claim_id]
    assert len(matching) == 1
    assert matching[0].details.get("merged_finding_ids")


def test_the_validator_sink_is_wired_at_every_production_call_site(tmp_path):
    """The validator's own findings ("Item N" from validate_chunk_output, "Field N" from
    reconstruct_fields), not only the claim adapter's, reach the recorder, on the chunk path
    (accepted output, then the merged-fields reconstruction) and the synthesis path."""
    world = _World(tmp_path)
    responses = _default_script()
    responses[1] = [_ext((3, "RCT", ["S999999"], "h"))]
    responses[3] = [_ext((5, "Sound", ["NOPE"], "m"))]
    _process(tmp_path, world=world, responses=responses)
    findings = [f for f in world.recorder.snapshot().findings if f.code == "dangling_reference"]
    for index in (3, 5):
        claim_id = _cid(world, "a", index)
        messages = [f.message for f in findings if f.node_id == claim_id]
        assert any(m.startswith("Item ") for m in messages), (index, messages)
        assert any(m.startswith(f"Field {index}") for m in messages), (index, messages)
        assert any("claim " in m for m in messages), (index, messages)  # the claim adapter's own


def test_a_scoped_reference_to_the_documents_own_evidence_is_cited_not_reported(tmp_path):
    world = _World(tmp_path)
    responses = _default_script()
    responses[1] = [_ext((3, "RCT", [_eid(world, "a", "S000001")], "h"))]
    run = _process(tmp_path, world=world, responses=responses)
    assert run.result is not None  # kept by the sink-bearing acceptance after the repairs
    snapshot = world.recorder.snapshot()
    claim = _claims(snapshot)[_cid(world, "a", 3)]
    assert claim.cited_evidence_ids == (_eid(world, "a", "S000001"),)
    assert not [f for f in snapshot.findings if f.code in ("dangling_reference", "ambiguous_reference")]


def test_the_same_input_without_provenance_still_fails_exactly_as_before(tmp_path):
    run = _process(tmp_path, use_provenance=False, responses=_unknown_id_responses())
    assert run.result is None
    entry = run.manifest["a"]
    assert entry["status"] == "failed_chunks"
    assert entry["failures"][0]["stage"] == "extraction_chunk"
    assert "unknown evidence IDs" in entry["failures"][0]["last_error"]
    assert not (run.out_dir / "a.extracted.json").exists()


def test_an_unknown_id_in_a_synthesized_field_is_a_finding_too(tmp_path):
    world = _World(tmp_path)
    responses = _default_script()
    responses[3] = [_ext((5, "Sound", ["NOPE"], "m"))]
    run = _process(tmp_path, world=world, responses=responses)
    assert run.result is not None
    findings = [f for f in world.recorder.snapshot().findings if f.code == "dangling_reference"]
    assert {f.node_id for f in findings} == {_cid(world, "a", 5)}


def test_an_unknown_id_in_a_conflict_candidate_is_reported_on_the_candidate_by_the_claim_adapter(tmp_path):
    world = _World(tmp_path)
    responses = _conflict_responses()
    responses[2] = [_ext((3, "Cohort", ["S000002", "S777777"], "m"))]  # unknown on every attempt
    run = _process(tmp_path, world=world, layout=_CONFLICT_LAYOUT, responses=responses)
    assert run.result is not None
    dangling = [f for f in world.recorder.snapshot().findings if f.code == "dangling_reference"]
    assert _cid(world, "a", 3, 1) in {f.node_id for f in dangling}


def test_repair_of_an_unknown_id_still_works_with_provenance_and_records_no_finding(tmp_path):
    world = _World(tmp_path)
    responses = _default_script()
    responses[1] = [_ext((3, "RCT", ["S999999"], "h")), _ext((3, "RCT", ["S000001"], "h"))]
    on = _process(tmp_path, world=world, responses=responses, label="on")
    off = _process(
        tmp_path,
        use_provenance=False,
        responses={**_default_script(), 1: list(responses[1])},
        label="off",
    )
    assert on.result is not None and off.result is not None
    assert len(on.script.calls) == len(off.script.calls) == 4  # chunk 1 twice, chunk 2, synthesis
    assert not [f for f in world.recorder.snapshot().findings if f.code == "dangling_reference"]
    assert _claims(world.recorder.snapshot())[_cid(world, "a", 3)].support == "supported"


def test_a_duplicate_reference_in_model_output_is_recorded_and_the_claim_cited_once(tmp_path):
    world = _World(tmp_path)
    responses = _default_script()
    responses[1] = [_ext((3, "RCT", ["S000001", "S000001"], "h"))]
    run = _process(tmp_path, world=world, responses=responses)
    assert run.result is not None
    snapshot = world.recorder.snapshot()
    claim_id = _cid(world, "a", 3)
    assert any(f.code == "duplicate_reference" and f.node_id == claim_id for f in snapshot.findings)
    assert _claims(snapshot)[claim_id].cited_evidence_ids == (_eid(world, "a", "S000001"),)


def test_header_citations_are_the_same_on_a_cache_hit(tmp_path):
    first_world, second_world = _World(tmp_path), _World(tmp_path)
    _process(tmp_path, world=first_world, label="shared")
    cache = tmp_path / "cache_shared_a"
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    assert before  # the first run wrote the evidence cache
    _process(tmp_path, world=second_world, label="shared")  # second run: cache hit
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before
    cited = lambda w: {  # noqa: E731
        c.node_id: c.cited_evidence_ids for c in _claims(w.recorder.snapshot()).values() if c.origin == "prefill"
    }
    assert cited(first_world) == cited(second_world)
    assert cited(second_world)[_cid(second_world, "a", 1)] == (_eid(second_world, "a", "M-first-author"),)
    assert cited(second_world)[_cid(second_world, "a", 2)] == (_eid(second_world, "a", "M-publication-date"),)


# ---------------------------------------------------------------------------
# Provenance changes nothing the pipeline produces
# ---------------------------------------------------------------------------


def _normalise_calls(calls: list[Any]) -> list[Any]:
    return [(args, {k: (sorted(v) if isinstance(v, set) else v) for k, v in kwargs.items()}) for args, kwargs in calls]


def test_provenance_changes_neither_the_calls_nor_the_outputs(tmp_path):
    world = _World(tmp_path)
    on = _process(tmp_path, world=world, label="on")
    off = _process(tmp_path, use_provenance=False, label="off")
    assert _normalise_calls(on.script.calls) == _normalise_calls(off.script.calls)
    assert on.result == off.result
    assert (on.out_dir / "a.extracted.json").read_bytes() == (off.out_dir / "a.extracted.json").read_bytes()
    assert on.manifest == off.manifest
    assert on.manifest["a"]["status"] == "complete"


def test_no_provenance_value_reaches_any_prompt_argument(tmp_path):
    world = _World(tmp_path)
    run = _process(tmp_path, world=world, responses=_unknown_id_responses())
    sid = world.source_id("a")
    banned = ("claim:", "src:", sid, "M-first-author", "M-publication-date", "drv:", "finding:")
    for args, kwargs in run.script.calls:
        text = json.dumps([args, kwargs], default=lambda o: sorted(o) if isinstance(o, set) else str(o))
        for fragment in banned:
            assert fragment not in text, fragment


def test_the_shared_prompt_prefix_is_identical_across_chunks_and_runs(tmp_path):
    world = _World(tmp_path)
    on = _process(tmp_path, world=world, label="on")
    off = _process(tmp_path, use_provenance=False, label="off")
    sources_on = {args[0]: args[1] for args, _ in on.script.calls}
    sources_off = {args[0]: args[1] for args, _ in off.script.calls}
    assert len(set(sources_on.values())) == 1
    assert sources_on == sources_off


def test_a_resumed_document_emits_nothing(tmp_path):
    world = _World(tmp_path)
    first = _process(tmp_path, world=world, label="first")
    assert first.result is not None
    other = _World(tmp_path / "other")
    manifest = {"a": {"status": "complete"}}
    again_dir = tmp_path / "out_again_a"
    again_dir.mkdir()
    (again_dir / "a.extracted.json").write_text(json.dumps([{"field_index": 1}]), encoding="utf-8")
    qc = _qc_context(tmp_path, "a", _TEI)
    with patch.object(pdf_processor, "OUTPUT_DIR", again_dir), patch.object(
        pdf_processor, "validate_qc_context_input"
    ):
        result = asyncio.run(
            pdf_processor.process_pdf(
                qc_context=qc,
                chunk_fields=_chunk_fields(_DEFAULT_LAYOUT),
                field_lookup=_FIELD_LOOKUP,
                api_semaphore=asyncio.Semaphore(1),
                manifest=manifest,
                manifest_lock=asyncio.Lock(),
                openai_config=_config(tmp_path / "cache_again"),
                provenance=other.provenance("a"),
            )
        )
    assert result == [{"field_index": 1}]
    snapshot = other.recorder.snapshot()
    assert not snapshot.nodes and not snapshot.findings and not snapshot.document_stage_status


# ---------------------------------------------------------------------------
# Provenance never changes the outcome
# ---------------------------------------------------------------------------


class _ExplodingRecorder(ProvenanceRecorder):
    def __init__(self, run: Any, *, fail: set[str]) -> None:
        super().__init__(run)
        self._fail = fail

    def record_claim(self, node: Any, edges: Any) -> str:
        if "record_claim" in self._fail:
            raise RuntimeError("claim boom")
        return super().record_claim(node, edges)

    def record_derivation(self, step: Any) -> str:
        if "record_derivation" in self._fail:
            raise RuntimeError("derivation boom")
        return super().record_derivation(step)


@pytest.mark.parametrize("fail", [{"record_claim"}, {"record_derivation"}, {"record_claim", "record_derivation"}])
def test_a_provenance_error_never_changes_the_result_and_marks_the_stage_failed(tmp_path, fail):
    world = _World(tmp_path)
    world.recorder = _ExplodingRecorder(world.run, fail=fail)
    run = _process(tmp_path, world=world)
    baseline = _process(tmp_path, use_provenance=False, label="off")
    assert run.result == baseline.result
    assert run.manifest["a"]["status"] == "complete"
    status = world.recorder.snapshot().document_stage_status[world.source_id("a")]
    assert "failed" in {status[s] for s in ("metadata_prefill", "extraction_chunk", "synthesis")}


def test_a_disabled_recorder_changes_nothing_either(tmp_path):
    world = _World(tmp_path)
    world.recorder = ProvenanceRecorder(world.run, enabled=False)
    run = _process(tmp_path, world=world)
    baseline = _process(tmp_path, use_provenance=False, label="off")
    assert run.result == baseline.result
    assert not world.recorder.snapshot().nodes


# ---------------------------------------------------------------------------
# The graph the real path produces
# ---------------------------------------------------------------------------


def test_the_recorded_graph_validates_without_unexpected_findings_and_round_trips(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world, layout=_CONFLICT_LAYOUT, responses=_conflict_responses())
    reports = _graph_reports(world)
    codes = {f.code for f in reports.report.findings}
    assert reports.report.completed
    assert not codes & {"dangling_reference", "inconsistent_derivation", "graph_cycle", "source_not_represented", "invalid_identifier", "duplicate_identity"}
    assert not reports.graph.missing_segments
    sid = world.source_id("a")
    assert reports.completeness.documents[sid].state == "complete"
    path = write_graph(reports.graph, tmp_path / "artifact")
    assert read_graph(path) == reports.graph


def test_the_support_findings_are_the_expected_ones(tmp_path):
    world = _World(tmp_path)
    _process(tmp_path, world=world)
    reports = _graph_reports(world)
    # header nodes are anchorless -> missing_anchor on the header nodes, never unsupported_claim for the model claims
    assert not [f for f in reports.report.findings if f.code == "unsupported_claim"]
    prefill = reports.completeness.claims[_cid(world, "a", 1)]
    assert prefill.state == "incomplete"
    assert reports.completeness.documents[world.source_id("a")].state == "complete"
