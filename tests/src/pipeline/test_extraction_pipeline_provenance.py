"""Source nodes and QC derivation steps from the extraction pipeline (provenance-core 6.3).

``pipeline.document_provenance`` is the pipeline-side emission module:

* the checkpoint identity of every document is computed on the pipeline side by
  ``manifest.compute_identity`` (provenance code never hashes content), over a
  config mapping that actually carries the ``openai`` / ``extraction`` /
  ``quality_control`` sections the config hash reads (Requirement 3.5);
* resumed documents (manifest ``complete`` and the output file parses -- the
  exact test ``process_pdf`` applies) and documents without a usable fingerprint
  (the manifest's ``"nohash"`` / ``"no_extraction_map"`` sentinels) get no
  identity, no source node and no place in the run identity (Requirements 1.1,
  3.1, 3.5);
* the source node is built from the identity mapping, so the run-relative output
  path lands as the ``x-manifest-output_path`` extension (Requirement 1.3);
* two same-content PDFs share one source id: recorded once, one WARNING naming
  both labels, the conflicting later record surfaces as ``duplicate_identity``;
* the reconciled record's provenance mapping becomes derivation steps on the
  pipeline side (``quality_control`` is untouched), and the
  ``document_extraction`` / ``quality_control`` stages are marked per document
  (Requirement 6.1);
* nothing here ever raises into extraction.

Everything runs on fabricated bundles: no GROBID, network or OpenAI.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline import document_provenance as dp
from pipeline import manifest as manifest_mod
from pipeline import pdf_processor
from provenance.completeness import compute_completeness
from provenance.graph import build_graph
from provenance.identity import InvalidIdentifierError, derive_source_id
from provenance.models import RunIdentity
from provenance.recorder import ProvenanceRecorder
from provenance.validation import validate_chain
from quality_control.models import QCBundle, UnifiedRecord
from utils import path_utils

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CREATED_AT = "2026-10-11T12:00:00Z"

_OPENAI = {
    "api_key": "sk-secret-1",
    "chunk_model": "gpt-chunk",
    "synthesis_model": "gpt-synth",
    "num_chunks": 4,
    "domain_to_chunk": {1: 1, 2: 2},
    "pdf_concurrency": 2,
}
_QC = {
    "ocr": False,
    "text_processor": {"class": "text_processing.composite.DefaultTextProcessor"},
    "quality_control": {"grobid_integration": {"failure_behavior": "fallback"}, "len_filter": 40},
}
_QC_PROVENANCE = {
    "primary_artifact_id": "",
    "secondary_artifact_id": "",
    "primary_observation": {},
    "secondary_observation": {},
    "investigator_object": {},
    "adjudication_decisions": {"primary_extractor": "grobid", "confidence": 0.9, "rationale": "r"},
    "primary_branch_source": "grobid",
    "secondary_branch_source": "pdfplumber",
    "branch_selection": "adjudicated",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pdf(tmp_path: Path, name: str, content: bytes | None = None) -> Path:
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(content if content is not None else f"%PDF-1.4 {name}".encode())
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan(paths, manifest=None, *, openai=None, qc=None, tmp_path: Path | None = None):
    out = (tmp_path / "run") if tmp_path is not None else Path("/nonexistent-run")
    return dp.plan_run_documents(
        paths,
        manifest or {},
        openai_config=openai or _OPENAI,
        qc_config=qc or _QC,
        output_dir=out,
        run_root=out,
    )


def _bundle(provenance="default", *, unified=True) -> QCBundle:
    content = {"exact_text": "x"}
    if provenance == "default":
        content["provenance"] = copy.deepcopy(_QC_PROVENANCE)
    elif provenance != "absent":
        content["provenance"] = provenance
    return QCBundle(
        branches=[],
        unified=UnifiedRecord(document_id="doc", content=content) if unified else None,
    )


def _run_for(identities, synthesis="gpt-synth") -> RunIdentity:
    run = dp.build_run_identity(
        identities, chunk_model="gpt-chunk", synthesis_model=synthesis, created_at=_CREATED_AT
    )
    assert run is not None
    return run


def _setup(tmp_path: Path, names=("a", "b")):
    paths = [_pdf(tmp_path, n) for n in names]
    identities = _plan(paths, tmp_path=tmp_path)
    run = _run_for(identities)
    return paths, identities, run, ProvenanceRecorder(run)


# ---------------------------------------------------------------------------
# Config mapping and output path
# ---------------------------------------------------------------------------


def test_identity_config_carries_the_sections_the_config_hash_reads():
    cfg = dp.identity_config(_OPENAI, _QC)
    assert {"openai", "extraction", "quality_control"} <= set(cfg)
    assert cfg["openai"]["chunk_model"] == "gpt-chunk"
    assert cfg["quality_control"] == _QC["quality_control"]
    # pipeline-level extraction settings that live outside those two sections
    assert cfg["extraction"]["ocr"] is False
    assert cfg["extraction"]["text_processor"] == _QC["text_processor"]


def test_identity_config_never_includes_the_api_key():
    assert "sk-secret-1" not in json.dumps(dp.identity_config(_OPENAI, _QC), default=str)
    rotated = dict(_OPENAI, api_key="sk-secret-2")
    assert dp.identity_config(rotated, _QC) == dp.identity_config(_OPENAI, _QC)


def test_config_fingerprint_changes_with_chunk_model_and_other_relevant_config(tmp_path):
    pdf = _pdf(tmp_path, "a")
    base = _plan([pdf], tmp_path=tmp_path)["a"].config_hash
    other_model = _plan([pdf], openai=dict(_OPENAI, chunk_model="gpt-other"), tmp_path=tmp_path)["a"]
    assert other_model.config_hash != base
    other_qc = copy.deepcopy(_QC)
    other_qc["quality_control"]["len_filter"] = 41
    assert _plan([pdf], qc=other_qc, tmp_path=tmp_path)["a"].config_hash != base
    other_ocr = dict(_QC, ocr=True)
    assert _plan([pdf], qc=other_ocr, tmp_path=tmp_path)["a"].config_hash != base


def test_config_fingerprint_is_meaningful_not_the_empty_section_hash(tmp_path):
    """A flat dict without the three sections would hash to one constant; this must not."""
    pdf = _pdf(tmp_path, "a")
    empty_hash = manifest_mod._compute_config_hash({"chunk_model": "x"})
    assert _plan([pdf], tmp_path=tmp_path)["a"].config_hash != empty_hash


def test_config_fingerprint_is_identical_across_documents(tmp_path):
    paths = [_pdf(tmp_path, n) for n in ("a", "b", "c")]
    identities = _plan(paths, tmp_path=tmp_path)
    assert len({i.config_hash for i in identities.values()}) == 1
    assert len({i.extraction_map_hash for i in identities.values()}) == 1


def test_run_relative_output_path_is_relative_to_the_run_folder(tmp_path):
    run_root = tmp_path / "run_x"
    assert dp.run_relative_output_path("paper", output_dir=run_root, run_root=run_root) == "paper.extracted.json"
    assert (
        dp.run_relative_output_path("paper", output_dir=run_root / "sub", run_root=run_root)
        == "sub/paper.extracted.json"
    )


def test_run_relative_output_path_defaults_resolve_to_the_real_output_file():
    rel = dp.run_relative_output_path("paper")
    assert not Path(rel).is_absolute()
    run_root = path_utils.resolve_run_output_path("")
    assert (run_root / rel) == (path_utils.OUTPUT_DIR / "paper.extracted.json")


def test_run_relative_output_path_outside_the_run_folder_degrades_to_the_file_name(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        rel = dp.run_relative_output_path("paper", output_dir=tmp_path / "elsewhere", run_root=tmp_path / "run")
    assert rel == "paper.extracted.json"
    assert any("not under the run folder" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Resumed predicate: identical to the test process_pdf applies
# ---------------------------------------------------------------------------


def _manifest_complete(name: str) -> dict:
    return {name: {"status": "complete"}}


def test_is_resumed_matches_load_completed_result_in_every_case(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "ok.extracted.json").write_text('[{"field_index": 1}]', encoding="utf-8")
    (out / "bad.extracted.json").write_text("{not json", encoding="utf-8")
    (out / "null.extracted.json").write_text("null", encoding="utf-8")
    manifest = {n: {"status": "complete"} for n in ("ok", "bad", "null", "missing")}
    manifest["pending"] = {"status": "pending"}
    (out / "pending.extracted.json").write_text("[]", encoding="utf-8")
    with patch.object(pdf_processor, "OUTPUT_DIR", out):
        for name in ("ok", "bad", "null", "missing", "pending", "unknown"):
            expected = pdf_processor._load_completed_result(name, manifest) is not None
            assert pdf_processor.is_resumed(name, manifest) is expected, name
        assert pdf_processor.is_resumed("ok", manifest) is True
        assert pdf_processor.is_resumed("bad", manifest) is False
        assert pdf_processor.is_resumed("missing", manifest) is False
        assert pdf_processor.is_resumed("pending", manifest) is False


def test_load_completed_result_behaviour_is_unchanged(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "ok.extracted.json").write_text('[{"a": 1}]', encoding="utf-8")
    with patch.object(pdf_processor, "OUTPUT_DIR", out):
        assert pdf_processor._load_completed_result("ok", _manifest_complete("ok")) == [{"a": 1}]
        assert pdf_processor._load_completed_result("ok", {"ok": {"status": "failed"}}) is None


# ---------------------------------------------------------------------------
# plan_run_documents
# ---------------------------------------------------------------------------


def test_plan_computes_identity_on_the_pipeline_side_for_every_processed_document(tmp_path):
    paths = [_pdf(tmp_path, "a"), _pdf(tmp_path, "b")]
    identities = _plan(paths, tmp_path=tmp_path)
    assert list(identities) == ["a", "b"]
    for path in paths:
        ident = identities[path.stem]
        assert ident.pdf_content_hash == _sha(path)
        assert ident.model_id == "gpt-chunk"
        assert ident.output_path == f"{path.stem}.extracted.json"
        assert ident.extraction_map_hash not in ("", "no_extraction_map")
        assert ident == manifest_mod.compute_identity(
            path,
            dp.identity_config(_OPENAI, _QC),
            model_id="gpt-chunk",
            output_path=f"{path.stem}.extracted.json",
        )


def test_plan_excludes_a_resumed_document_and_keeps_its_neighbour(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    (out / "a.extracted.json").write_text("[]", encoding="utf-8")
    paths = [_pdf(tmp_path, "a"), _pdf(tmp_path, "b")]
    with patch.object(pdf_processor, "OUTPUT_DIR", out):
        identities = _plan(paths, _manifest_complete("a"), tmp_path=tmp_path)
    assert list(identities) == ["b"]
    run = _run_for(identities)
    assert set(run.document_fingerprints) == {"b"}
    assert _sha(paths[0]) not in run.document_fingerprints.values()


@pytest.mark.parametrize("output", ["missing", "corrupt"])
def test_plan_keeps_a_complete_document_whose_output_is_missing_or_corrupt(tmp_path, output):
    out = tmp_path / "run"
    out.mkdir()
    if output == "corrupt":
        (out / "a.extracted.json").write_text("{broken", encoding="utf-8")
    paths = [_pdf(tmp_path, "a")]
    with patch.object(pdf_processor, "OUTPUT_DIR", out):
        identities = _plan(paths, _manifest_complete("a"), tmp_path=tmp_path)
    assert list(identities) == ["a"]


def test_plan_treats_an_unreadable_pdf_as_having_no_fingerprint(tmp_path, caplog):
    paths = [_pdf(tmp_path, "a"), tmp_path / "gone.pdf"]
    with caplog.at_level(logging.WARNING):
        identities = _plan(paths, tmp_path=tmp_path)
    assert list(identities) == ["a"]
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("gone" in m and "no usable content fingerprint" in m for m in messages)
    assert "nohash" not in {i.pdf_content_hash for i in identities.values()}


def test_plan_treats_a_missing_extraction_map_as_a_missing_run_fingerprint(tmp_path, caplog):
    paths = [_pdf(tmp_path, "a"), _pdf(tmp_path, "b")]
    with patch.object(manifest_mod, "_compute_extraction_map_hash", return_value="no_extraction_map"):
        with caplog.at_level(logging.WARNING):
            identities = _plan(paths, tmp_path=tmp_path)
    assert identities == {}
    assert any("extraction map" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_missing_fingerprint_reason_names_each_sentinel():
    good = manifest_mod.ManifestIdentity("a" * 64, "c" * 64, "e" * 64, "m", "1.0.0", "x.extracted.json")
    assert dp.missing_fingerprint_reason(good) is None
    import dataclasses

    assert "content" in dp.missing_fingerprint_reason(dataclasses.replace(good, pdf_content_hash="nohash"))
    assert "extraction map" in dp.missing_fingerprint_reason(
        dataclasses.replace(good, extraction_map_hash="no_extraction_map")
    )
    assert dp.missing_fingerprint_reason(dataclasses.replace(good, config_hash="")) is not None


# ---------------------------------------------------------------------------
# build_run_identity
# ---------------------------------------------------------------------------


def test_run_identity_lists_the_synthesis_model_when_it_differs(tmp_path):
    identities = _plan([_pdf(tmp_path, "a")], tmp_path=tmp_path)
    run = _run_for(identities, synthesis="gpt-synth")
    assert run.model_ids == ("gpt-chunk", "gpt-synth")
    assert run.created_at == _CREATED_AT
    assert run.config_fingerprint == identities["a"].config_hash
    assert run.schema_fingerprint == identities["a"].extraction_map_hash


def test_run_identity_omits_the_synthesis_model_when_it_equals_the_chunk_model(tmp_path):
    identities = _plan([_pdf(tmp_path, "a")], tmp_path=tmp_path)
    same = _run_for(identities, synthesis="gpt-chunk")
    assert same.model_ids == ("gpt-chunk",)
    assert same.run_id != _run_for(identities, synthesis="gpt-synth").run_id
    assert _run_for(identities, synthesis="").model_ids == ("gpt-chunk",)
    assert _run_for(identities, synthesis=None).run_id == same.run_id


def test_run_identity_is_independent_of_document_order(tmp_path):
    paths = [_pdf(tmp_path, "a"), _pdf(tmp_path, "b")]
    forward = _run_for(_plan(paths, tmp_path=tmp_path))
    backward = _run_for(_plan(paths[::-1], tmp_path=tmp_path))
    assert forward.run_id == backward.run_id


def test_run_identity_of_no_documents_is_none_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        run = dp.build_run_identity({}, chunk_model="m", synthesis_model="s", created_at=_CREATED_AT)
    assert run is None
    assert any("no run identity" in r.getMessage() for r in caplog.records)


def test_run_identity_with_disagreeing_run_wide_fingerprints_is_none_with_a_warning(tmp_path, caplog):
    import dataclasses

    identities = _plan([_pdf(tmp_path, "a"), _pdf(tmp_path, "b")], tmp_path=tmp_path)
    identities["b"] = dataclasses.replace(identities["b"], config_hash="f" * 64)
    with caplog.at_level(logging.WARNING):
        run = dp.build_run_identity(identities, chunk_model="m", synthesis_model="s", created_at=_CREATED_AT)
    assert run is None
    assert any("no run identity" in r.getMessage() for r in caplog.records)


def test_run_identity_still_raises_type_errors_for_caller_bugs(tmp_path):
    identities = _plan([_pdf(tmp_path, "a")], tmp_path=tmp_path)
    with pytest.raises(TypeError):
        dp.build_run_identity(identities, chunk_model="m", synthesis_model="s", created_at=123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# emit_document_provenance: source node
# ---------------------------------------------------------------------------


def test_one_source_node_per_processed_document_with_the_output_path_extension(tmp_path):
    paths, identities, run, rec = _setup(tmp_path)
    ids = [
        dp.emit_document_provenance(rec, _bundle(), identities[p.stem], pdf_name=p.stem, run_id=run.run_id)
        for p in paths
    ]
    snap = rec.snapshot()
    sources = [n for n in snap.nodes.values() if n.kind == "source"]
    assert sorted(n.node_id for n in sources) == sorted(ids) == sorted(derive_source_id(_sha(p)) for p in paths)
    for node in sources:
        label = "a" if node.document_fingerprint == _sha(paths[0]) else "b"
        ident = identities[label]
        assert node.extensions["x-manifest-output_path"] == f"{label}.extracted.json"
        assert node.extensions["x-manifest-schema_version"] == ident.schema_version
        assert node.source_label == label
        assert node.source_format == "pdf"
        assert node.run_id == run.run_id
        assert node.config_fingerprint == ident.config_hash
        assert node.schema_fingerprint == ident.extraction_map_hash
        assert node.model_ids == ("gpt-chunk",)


def test_resumed_document_gets_no_source_node_and_no_fingerprint(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    (out / "a.extracted.json").write_text("[]", encoding="utf-8")
    paths = [_pdf(tmp_path, "a"), _pdf(tmp_path, "b")]
    with patch.object(pdf_processor, "OUTPUT_DIR", out):
        identities = _plan(paths, _manifest_complete("a"), tmp_path=tmp_path)
    run = _run_for(identities)
    rec = ProvenanceRecorder(run)
    # The orchestrator hands only planned documents an identity; a resumed one has none.
    assert dp.emit_document_provenance(rec, _bundle(), identities.get("a"), pdf_name="a", run_id=run.run_id) is None
    dp.emit_document_provenance(rec, _bundle(), identities["b"], pdf_name="b", run_id=run.run_id)
    snap = rec.snapshot()
    resumed_id = derive_source_id(_sha(paths[0]))
    assert resumed_id not in snap.nodes
    assert resumed_id not in snap.document_stage_status
    assert [n.node_id for n in snap.nodes.values() if n.kind == "source"] == [derive_source_id(_sha(paths[1]))]
    assert all(resumed_id not in getattr(n, "input_ids", ()) for n in snap.nodes.values())


def test_two_same_content_pdfs_share_one_source_and_warn_naming_both(tmp_path, caplog):
    paths = [_pdf(tmp_path, "first", b"%PDF same"), _pdf(tmp_path, "second", b"%PDF same")]
    identities = _plan(paths, tmp_path=tmp_path)
    run = _run_for(identities)
    rec = ProvenanceRecorder(run)
    with caplog.at_level(logging.WARNING):
        ids = [
            dp.emit_document_provenance(rec, _bundle(), identities[n], pdf_name=n, run_id=run.run_id)
            for n in ("first", "second")
        ]
    assert ids[0] == ids[1] == derive_source_id(_sha(paths[0]))
    snap = rec.snapshot()
    sources = [n for n in snap.nodes.values() if n.kind == "source"]
    assert len(sources) == 1 and sources[0].source_label == "first"
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "identical content" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "first" in warnings[0] and "second" in warnings[0]
    conflicts = [f for f in snap.findings if f.code == "duplicate_identity"]
    assert len(conflicts) == 1
    assert conflicts[0].severity == "error"
    assert conflicts[0].node_id == ids[0]


def test_the_same_document_emitted_twice_is_idempotent_and_silent(tmp_path, caplog):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            dp.emit_document_provenance(rec, _bundle(), identities["a"], pdf_name="a", run_id=run.run_id)
    snap = rec.snapshot()
    assert not snap.findings
    assert len([n for n in snap.nodes.values() if n.kind == "source"]) == 1
    assert not [r for r in caplog.records if "identical content" in r.getMessage()]


@pytest.mark.parametrize("field,value", [("pdf_content_hash", "nohash"), ("extraction_map_hash", "no_extraction_map")])
def test_emission_with_a_sentinel_identity_records_nothing_and_warns(tmp_path, caplog, field, value):
    import dataclasses

    paths, identities, run, rec = _setup(tmp_path, ("a",))
    bad = dataclasses.replace(identities["a"], **{field: value})
    with caplog.at_level(logging.WARNING):
        assert dp.emit_document_provenance(rec, _bundle(), bad, pdf_name="a", run_id=run.run_id) is None
    snap = rec.snapshot()
    assert not snap.nodes and not snap.document_stage_status
    assert any(r.levelno == logging.WARNING and "a" in r.getMessage() for r in caplog.records)


def test_emission_without_a_recorder_or_identity_is_a_noop(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    assert dp.emit_document_provenance(None, _bundle(), identities["a"], pdf_name="a", run_id=run.run_id) is None
    assert dp.emit_document_provenance(rec, _bundle(), None, pdf_name="a", run_id=run.run_id) is None
    assert not rec.snapshot().nodes


def test_a_disabled_recorder_stores_nothing_and_does_not_fail(tmp_path):
    paths = [_pdf(tmp_path, "a")]
    identities = _plan(paths, tmp_path=tmp_path)
    run = _run_for(identities)
    rec = ProvenanceRecorder(run, enabled=False)
    sid = dp.emit_document_provenance(rec, _bundle(), identities["a"], pdf_name="a", run_id=run.run_id)
    assert sid == derive_source_id(_sha(paths[0]))
    snap = rec.snapshot()
    assert not snap.nodes and not snap.findings and not snap.document_stage_status


# ---------------------------------------------------------------------------
# emit_document_provenance: derivations and stage marks
# ---------------------------------------------------------------------------


def _steps(snap):
    return [n for n in snap.nodes.values() if n.kind == "derivation"]


def test_a_reconcile_step_is_recorded_per_document_carrying_the_qc_mapping(tmp_path):
    paths, identities, run, rec = _setup(tmp_path)
    ids = {
        p.stem: dp.emit_document_provenance(rec, _bundle(), identities[p.stem], pdf_name=p.stem, run_id=run.run_id)
        for p in paths
    }
    steps = _steps(rec.snapshot())
    assert len(steps) == 2
    for step in steps:
        assert step.stage == "quality_control"
        assert step.derivation_kind == "reconcile"
        assert step.determinism == "deterministic"
        assert step.model_id is None
        assert step.extensions["x-qc-primary_branch_source"] == "grobid"
    assert {s.input_ids for s in steps} == {(ids["a"],), (ids["b"],)}


@pytest.mark.parametrize("provenance", ["absent", None, {}])
def test_missing_none_or_empty_qc_provenance_still_yields_a_reconcile_step(tmp_path, provenance):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    dp.emit_document_provenance(rec, _bundle(provenance), identities["a"], pdf_name="a", run_id=run.run_id)
    snap = rec.snapshot()
    [step] = _steps(snap)
    assert step.derivation_kind == "reconcile"
    sid = derive_source_id(_sha(paths[0]))
    assert snap.document_stage_status[sid]["quality_control"] == "complete"


@pytest.mark.parametrize("provenance", ["a string", ["a", "list"], 7])
def test_non_mapping_qc_provenance_is_tolerated_with_a_warning(tmp_path, caplog, provenance):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    with caplog.at_level(logging.WARNING):
        dp.emit_document_provenance(rec, _bundle(provenance), identities["a"], pdf_name="a", run_id=run.run_id)
    snap = rec.snapshot()
    assert len(_steps(snap)) == 1
    assert any("not a mapping" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert snap.document_stage_status[derive_source_id(_sha(paths[0]))]["quality_control"] == "complete"


def test_stages_are_marked_per_document_and_run_wide_status_is_untouched(tmp_path):
    paths, identities, run, rec = _setup(tmp_path)
    for p in paths:
        dp.emit_document_provenance(rec, _bundle(), identities[p.stem], pdf_name=p.stem, run_id=run.run_id)
    snap = rec.snapshot()
    for p in paths:
        sid = derive_source_id(_sha(p))
        assert snap.document_stage_status[sid] == {"document_extraction": "complete", "quality_control": "complete"}
    assert snap.stage_status["document_extraction"] == "not_run"
    assert snap.stage_status["quality_control"] == "not_run"


def test_a_bundle_without_a_unified_record_marks_quality_control_failed(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    sid = dp.emit_document_provenance(
        rec, _bundle(unified=False), identities["a"], pdf_name="a", run_id=run.run_id
    )
    snap = rec.snapshot()
    assert snap.document_stage_status[sid] == {"document_extraction": "complete", "quality_control": "failed"}
    assert snap.document_stage_failure_reasons[sid]["quality_control"]
    assert not _steps(snap)


class _FlakyRecorder:
    """Delegates to a real recorder but raises from the named methods."""

    def __init__(self, real: ProvenanceRecorder, *, fail: set[str]):
        self._real = real
        self._fail = fail

    def __getattr__(self, name):
        target = getattr(self._real, name)
        if name in self._fail:

            def _boom(*args, **kwargs):
                raise RuntimeError(f"{name} exploded")

            return _boom
        return target


def test_a_derivation_failure_marks_quality_control_failed_and_never_raises(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    flaky = _FlakyRecorder(rec, fail={"record_derivation"})
    sid = dp.emit_document_provenance(flaky, _bundle(), identities["a"], pdf_name="a", run_id=run.run_id)
    snap = rec.snapshot()
    assert sid == derive_source_id(_sha(paths[0]))
    assert snap.document_stage_status[sid]["document_extraction"] == "complete"
    assert snap.document_stage_status[sid]["quality_control"] == "failed"
    [reason] = snap.document_stage_failure_reasons[sid]["quality_control"]
    assert "RuntimeError" in reason and "record_derivation exploded" in reason


def test_non_json_native_qc_values_are_stringified_so_the_artifact_stays_writable(tmp_path, caplog):
    from provenance.serialization import graph_to_dict

    paths, identities, run, rec = _setup(tmp_path, ("a",))
    odd = {"primary_branch_source": object(), "confidence": float("nan"), "nested": {"ok": 1, "set": {1}}}
    with caplog.at_level(logging.WARNING):
        sid = dp.emit_document_provenance(rec, _bundle(odd), identities["a"], pdf_name="a", run_id=run.run_id)
    snap = rec.snapshot()
    [step] = _steps(snap)
    assert isinstance(step.extensions["x-qc-primary_branch_source"], str)
    assert step.extensions["x-qc-confidence"] == "nan"
    assert step.extensions["x-qc-nested"]["ok"] == 1
    assert snap.document_stage_status[sid]["quality_control"] == "complete"
    assert any("not JSON-native" in r.getMessage() for r in caplog.records)
    json.dumps(graph_to_dict(build_graph(snap)), allow_nan=False)


def test_json_native_qc_provenance_is_passed_through_unchanged(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    dp.emit_document_provenance(rec, _bundle(), identities["a"], pdf_name="a", run_id=run.run_id)
    [step] = _steps(rec.snapshot())
    assert step.extensions["x-qc-adjudication_decisions"] == _QC_PROVENANCE["adjudication_decisions"]


def test_a_source_recording_failure_returns_none_and_never_raises(tmp_path, caplog):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    flaky = _FlakyRecorder(rec, fail={"record_source"})
    with caplog.at_level(logging.WARNING):
        assert dp.emit_document_provenance(flaky, _bundle(), identities["a"], pdf_name="a", run_id=run.run_id) is None
    assert not rec.snapshot().nodes
    assert any("record_source exploded" in r.getMessage() for r in caplog.records)


def test_a_failing_stage_mark_is_swallowed_too(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    flaky = _FlakyRecorder(rec, fail={"record_derivation", "mark_stage_failed", "mark_stage_complete"})
    assert dp.emit_document_provenance(flaky, _bundle(), identities["a"], pdf_name="a", run_id=run.run_id)


def test_a_malformed_run_id_never_raises_and_records_nothing(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    assert dp.emit_document_provenance(rec, _bundle(), identities["a"], pdf_name="a", run_id="") is None
    snap = rec.snapshot()
    assert not snap.nodes and not snap.document_stage_status


def test_emit_document_failure_records_the_source_and_fails_the_extraction_stage(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    sid = dp.emit_document_failure(
        rec, identities["a"], RuntimeError("qc blew up"), pdf_name="a", run_id=run.run_id
    )
    snap = rec.snapshot()
    assert sid in snap.nodes
    assert snap.document_stage_status[sid] == {"document_extraction": "failed"}
    assert "qc blew up" in snap.document_stage_failure_reasons[sid]["document_extraction"][0]


# ---------------------------------------------------------------------------
# Contract / completeness smoke
# ---------------------------------------------------------------------------


def test_emitted_records_satisfy_the_stage_contracts_end_to_end(tmp_path):
    paths, identities, run, rec = _setup(tmp_path)
    for p in paths:
        dp.emit_document_provenance(rec, _bundle(), identities[p.stem], pdf_name=p.stem, run_id=run.run_id)
    snap = rec.snapshot()
    graph = build_graph(snap)
    assert not graph.missing_segments
    report = validate_chain(graph, recorded_findings=snap.findings)
    codes = {f.code for f in report.findings}
    assert not codes & {"source_not_represented", "inconsistent_derivation", "dangling_reference"}
    assert not [f for f in report.findings if f.severity in ("critical", "error")]
    completeness = compute_completeness(graph, snap)
    for p in paths:
        doc = completeness.documents[derive_source_id(_sha(p))]
        assert doc.state == "complete", doc
        assert doc.missing_record_kinds == ()
        assert doc.responsible_stages == ()
    assert all(v == "not_run" for v in snap.stage_status.values())


def test_the_reconcile_step_is_what_satisfies_the_quality_control_contract(tmp_path):
    """Requirement 6.1: with the step the document is complete; without it, ``derivation`` is missing."""
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    dp.emit_document_provenance(rec, _bundle(), identities["a"], pdf_name="a", run_id=run.run_id)
    sid = derive_source_id(_sha(paths[0]))
    snap = rec.snapshot()
    assert compute_completeness(build_graph(snap), snap).documents[sid].state == "complete"

    bare = ProvenanceRecorder(run)
    bare.record_source(next(n for n in snap.nodes.values() if n.kind == "source"))
    bare.mark_stage_complete("document_extraction", source_id=sid)
    bare.mark_stage_complete("quality_control", source_id=sid)
    bare_snap = bare.snapshot()
    doc = compute_completeness(build_graph(bare_snap), bare_snap).documents[sid]
    assert doc.state != "complete"
    assert "derivation" in doc.missing_record_kinds
    assert "quality_control" in doc.responsible_stages


# ---------------------------------------------------------------------------
# build_qc_bundle hook: default path unchanged, provenance opt-in
# ---------------------------------------------------------------------------


def _run_build_qc_bundle(pdf: Path, *, provenance=None, unified=None):
    from pipeline import extraction_pipeline as ep

    bundle = QCBundle(branches=[], unified=unified)
    kwargs = {} if provenance is None else {"provenance": provenance}
    with patch.object(ep, "_get_text_processor", return_value=MagicMock()), \
         patch.object(ep, "_get_lexical_matcher", return_value=MagicMock()), \
         patch.object(ep, "_get_semantic_matcher", return_value=MagicMock()), \
         patch.object(ep, "_grobid_cache_read", return_value=("<TEI/>", "d" * 64)), \
         patch.object(ep, "extract_with_pdfplumber", return_value=[]), \
         patch.object(ep, "_page_class_cache_read", return_value=[]), \
         patch.object(ep, "_build_branches_for_classifications", return_value=([], [])), \
         patch.object(ep, "run_quality_control", return_value=bundle), \
         patch.object(ep, "w3c_project", return_value=[]), \
         patch.object(ep, "generate_w3c_jsonld", return_value={}):
        return ep.build_qc_bundle(pdf, pdf.stem, {"quality_control": {}}, **kwargs)


def _unified() -> UnifiedRecord:
    return UnifiedRecord(document_id="a", content={"exact_text": "x", "provenance": copy.deepcopy(_QC_PROVENANCE)})


def test_build_qc_bundle_emits_source_and_reconcile_step_when_given_a_provenance_context(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    ctx = dp.DocumentProvenance(recorder=rec, identity=identities["a"], run_id=run.run_id)
    result = _run_build_qc_bundle(paths[0], provenance=ctx, unified=_unified())
    assert result.unified is not None
    snap = rec.snapshot()
    sid = derive_source_id(_sha(paths[0]))
    assert sid in snap.nodes
    assert len(_steps(snap)) == 1
    assert snap.document_stage_status[sid] == {"document_extraction": "complete", "quality_control": "complete"}


def test_build_qc_bundle_default_path_records_nothing_and_returns_the_same_bundle(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    with_ctx = _run_build_qc_bundle(
        paths[0], provenance=dp.DocumentProvenance(rec, identities["a"], run.run_id), unified=_unified()
    )
    without = _run_build_qc_bundle(paths[0], unified=_unified())
    assert with_ctx.unified.content.keys() == without.unified.content.keys()
    assert with_ctx.unified.content["provenance"] == without.unified.content["provenance"]
    other = ProvenanceRecorder(run)
    assert not other.snapshot().nodes


def test_build_qc_bundle_never_fails_because_of_provenance(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    flaky = _FlakyRecorder(rec, fail={"record_source", "record_derivation", "mark_stage_complete"})
    ctx = dp.DocumentProvenance(recorder=flaky, identity=identities["a"], run_id=run.run_id)
    assert _run_build_qc_bundle(paths[0], provenance=ctx, unified=_unified()).unified is not None


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


def test_document_provenance_module_has_no_heavy_top_level_imports():
    import ast

    source = (_REPO_ROOT / "src" / "pipeline" / "document_provenance.py").read_text(encoding="utf-8")
    top_level = {
        alias.name.split(".")[0]
        for node in ast.parse(source).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(name=node.module or "")])
    }
    assert not top_level & {"torch", "faiss", "paddleocr", "sentence_transformers", "fitz", "openai"}


# ---------------------------------------------------------------------------
# Real configs: the fingerprint must not depend on the run (review round 1)
# ---------------------------------------------------------------------------


def _real_configs(monkeypatch, run_folder: str):
    """Load the REAL resolved configs as a run started under ``run_folder`` would."""
    from utils import config_utils

    monkeypatch.setattr(config_utils, "RUN_FOLDER_NAME", run_folder)
    return config_utils.load_openai_config(), config_utils.load_qc_config()


def _real_plan(tmp_path, monkeypatch, run_folder: str, *, mutate=None):
    openai, qc = _real_configs(monkeypatch, run_folder)
    if mutate is not None:
        openai, qc = mutate(copy.deepcopy(openai), copy.deepcopy(qc))
    pdf = tmp_path / "paper.pdf"
    if not pdf.exists():
        pdf.write_bytes(b"%PDF-1.4 real-config")
    return dp.plan_run_documents([pdf], {}, openai_config=openai, qc_config=qc, output_dir=tmp_path, run_root=tmp_path)


def test_real_config_fingerprint_and_run_id_are_stable_across_run_folders(tmp_path, monkeypatch):
    first = _real_plan(tmp_path, monkeypatch, "run_01-01-26_00-00-01")
    second = _real_plan(tmp_path, monkeypatch, "run_02-02-26_23-59-59")
    assert first["paper"].config_hash == second["paper"].config_hash
    assert _run_for(first).run_id == _run_for(second).run_id


def test_real_config_fingerprint_ignores_operational_keys(tmp_path, monkeypatch):
    base = _real_plan(tmp_path, monkeypatch, "run_01-01-26_00-00-01")["paper"].config_hash

    def operational(openai, qc):
        openai["pdf_concurrency"] += 7
        openai["global_api_limit"] += 7
        openai["enable_cache_prewarm"] = not openai["enable_cache_prewarm"]
        openai["max_retries"] += 1
        openai["api_key"] = "sk-another"
        return openai, qc

    assert _real_plan(tmp_path, monkeypatch, "run_01-01-26_00-00-01", mutate=operational)["paper"].config_hash == base


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o, q: (o.update(chunk_model="gpt-other") or o, q),
        lambda o, q: (o.update(synthesis_model="gpt-other") or o, q),
        lambda o, q: (o.update(num_chunks=o["num_chunks"] + 1) or o, q),
        lambda o, q: (o.update(max_evidence_items_per_chunk=o["max_evidence_items_per_chunk"] + 1) or o, q),
        lambda o, q: (o, q["quality_control"]["local_metrics"].update(min_chars_per_page=1) or q),
        lambda o, q: (o, q["quality_control"]["grobid"].update(consolidate_header=1) or q),
        lambda o, q: (o, q.update(ocr=not q["ocr"]) or q),
    ],
    ids=["chunk_model", "synthesis_model", "num_chunks", "evidence_limit", "qc_local_metric", "grobid_consolidate", "ocr"],
)
def test_real_config_fingerprint_changes_with_extraction_affecting_config(tmp_path, monkeypatch, mutate):
    base = _real_plan(tmp_path, monkeypatch, "run_01-01-26_00-00-01")["paper"].config_hash
    changed = _real_plan(tmp_path, monkeypatch, "run_01-01-26_00-00-01", mutate=mutate)["paper"].config_hash
    assert changed != base


def test_real_config_qc_infrastructure_keys_do_not_change_the_fingerprint(tmp_path, monkeypatch):
    base = _real_plan(tmp_path, monkeypatch, "run_01-01-26_00-00-01")["paper"].config_hash

    def infrastructure(openai, qc):
        qc["quality_control"]["grobid"].update(url="http://elsewhere:1", tei_cache_dir="other", concurrency=99)
        qc["quality_control"]["addons"]["datastet"]["url"] = "http://elsewhere:2"
        return openai, qc

    assert _real_plan(tmp_path, monkeypatch, "run_01-01-26_00-00-01", mutate=infrastructure)["paper"].config_hash == base


def test_every_real_openai_config_key_is_classified_exactly_once(monkeypatch):
    openai, _ = _real_configs(monkeypatch, "run_01-01-26_00-00-01")
    kept, excluded = dp.OPENAI_EXTRACTION_AFFECTING_KEYS, dp.OPENAI_EXCLUDED_KEYS
    assert not kept & excluded
    unclassified = set(openai) - kept - excluded
    assert not unclassified, f"classify new load_openai_config() keys in document_provenance: {sorted(unclassified)}"
    assert (kept | excluded) <= set(openai), "stale key in a classification set"


def _leaf_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaf_strings(item)
    elif isinstance(value, str):
        yield value


def test_real_qc_exclusions_name_real_paths_and_leave_no_run_scoped_value(monkeypatch):
    openai, qc = _real_configs(monkeypatch, "run_01-01-26_00-00-01")
    for path in dp.QC_EXCLUDED_PATHS:
        node = qc["quality_control"]
        for part in path.split("."):
            assert part in node, f"QC_EXCLUDED_PATHS names a path the real config lacks: {path}"
            node = node[part]
    hashed = dp.identity_config(openai, qc)
    leaves = list(_leaf_strings(hashed))
    assert not [v for v in leaves if v.startswith(("/", "~")) or "run_01-01-26" in v], leaves
    assert "evidence_cache_dir" not in hashed["openai"]
    assert "tei_cache_dir" not in hashed["quality_control"]["grobid"]


# ---------------------------------------------------------------------------
# JSON-native conversion agrees with the artifact writer
# ---------------------------------------------------------------------------


def _round_trip(tmp_path, mapping):
    """Emit a document whose QC provenance is ``mapping``; write, read back, return (step, graph, read)."""
    from provenance.serialization import read_graph, write_graph

    paths, identities, run, rec = _setup(tmp_path, ("a",))
    dp.emit_document_provenance(rec, _bundle(mapping), identities["a"], pdf_name="a", run_id=run.run_id)
    snap = rec.snapshot()
    graph = build_graph(snap)
    target = write_graph(graph, tmp_path / "artifact")
    return _steps(snap)[0], graph, read_graph(target)


@pytest.mark.parametrize(
    "mapping,expected",
    [
        ({"a": {1: "x"}}, {"x-qc-a": {"1": "x"}}),
        ({"t": (1, 2, (3, 4))}, {"x-qc-t": [1, 2, [3, 4]]}),
        ({"n": float("nan"), "i": float("-inf")}, {"x-qc-n": "nan", "x-qc-i": "-inf"}),
        ({"s": {3, 1, 2}, "n": {"deep": [frozenset({"b", "a"})]}}, {"x-qc-s": [1, 2, 3], "x-qc-n": {"deep": [["a", "b"]]}}),
        ({"o": object}, None),
    ],
    ids=["non_str_key", "tuple", "non_finite", "sets", "arbitrary_object"],
)
def test_non_native_qc_values_become_what_the_writer_stores_and_round_trip(tmp_path, mapping, expected):
    step, graph, read = _round_trip(tmp_path, mapping)
    if expected is not None:
        got = {k: v for k, v in step.extensions.items() if k in expected}
        assert got == expected
    assert read == graph


def test_json_native_qc_values_pass_through_unchanged_and_round_trip(tmp_path, caplog):
    native = {
        "s": "x",
        "i": 3,
        "f": 0.25,
        "b": True,
        "z": None,
        "l": [1, "a", [2], {"k": "v"}],
        "m": {"deep": {"er": [1, 2]}},
    }
    with caplog.at_level(logging.WARNING):
        step, graph, read = _round_trip(tmp_path, native)
    assert {k[len("x-qc-") :]: v for k, v in step.extensions.items()} == native
    assert not [r for r in caplog.records if "JSON-native" in r.getMessage()]
    assert read == graph


def test_json_native_conversion_is_idempotent():
    changed = [False]
    once = dp._json_native({"a": {1: (1, {2})}, "b": float("nan")}, changed)
    assert changed[0]
    again = [False]
    assert dp._json_native(once, again) == once
    assert again[0] is False


# ---------------------------------------------------------------------------
# Never-raise for any identity argument
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [object(), {"pdf_content_hash": "a" * 64}, "identity", 7], ids=["object", "dict", "str", "int"])
def test_a_wrong_typed_identity_is_logged_and_returns_none(tmp_path, caplog, bad):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    with caplog.at_level(logging.WARNING):
        assert dp.emit_document_provenance(rec, _bundle(), bad, pdf_name="a", run_id=run.run_id) is None
        assert dp.emit_document_failure(rec, bad, "boom", pdf_name="a", run_id=run.run_id) is None
    assert not rec.snapshot().nodes
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_none_identity_is_a_silent_noop(tmp_path, caplog):
    paths, identities, run, rec = _setup(tmp_path, ("a",))
    with caplog.at_level(logging.WARNING):
        assert dp.emit_document_provenance(rec, _bundle(), None, pdf_name="a", run_id=run.run_id) is None
        assert dp.emit_document_failure(rec, None, "boom", pdf_name="a", run_id=run.run_id) is None
    assert not caplog.records


def test_base_exceptions_still_propagate(tmp_path):
    paths, identities, run, rec = _setup(tmp_path, ("a",))

    class _Interrupting:
        def record_source(self, node):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        dp.emit_document_provenance(_Interrupting(), _bundle(), identities["a"], pdf_name="a", run_id=run.run_id)
    with pytest.raises(KeyboardInterrupt):
        dp.emit_document_failure(_Interrupting(), identities["a"], "boom", pdf_name="a", run_id=run.run_id)


# ---------------------------------------------------------------------------
# plan_run_documents: output path and repeated names
# ---------------------------------------------------------------------------


def test_plan_carries_a_non_default_run_relative_output_path_into_the_source_node(tmp_path, monkeypatch):
    run_folder = "run_01-01-26_00-00-01"
    monkeypatch.setattr(path_utils, "RUN_FOLDER_NAME", run_folder)
    run_root = path_utils.resolve_run_output_path("")
    monkeypatch.setattr(path_utils, "OUTPUT_DIR", run_root / "nested")
    pdf = _pdf(tmp_path, "paper")
    identities = dp.plan_run_documents([pdf], {}, openai_config=_OPENAI, qc_config=_QC)
    assert identities["paper"].output_path == "nested/paper.extracted.json"
    run = _run_for(identities)
    rec = ProvenanceRecorder(run)
    dp.emit_document_provenance(rec, _bundle(), identities["paper"], pdf_name="paper", run_id=run.run_id)
    [source] = [n for n in rec.snapshot().nodes.values() if n.kind == "source"]
    assert source.extensions["x-manifest-output_path"] == "nested/paper.extracted.json"


def test_plan_keeps_the_first_of_two_pdfs_with_the_same_stem_and_warns(tmp_path, caplog):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    first = tmp_path / "x" / "paper.pdf"
    second = tmp_path / "y" / "paper.pdf"
    first.write_bytes(b"%PDF first")
    second.write_bytes(b"%PDF second")
    with caplog.at_level(logging.WARNING):
        identities = _plan([first, second], tmp_path=tmp_path)
    assert list(identities) == ["paper"]
    assert identities["paper"].pdf_content_hash == _sha(first)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len([m for m in warnings if "share the document name" in m and "'paper'" in m]) == 1


def test_a_repeated_stem_is_skipped_even_when_the_first_one_was_not_planned(tmp_path, caplog):
    gone = tmp_path / "x" / "paper.pdf"  # unreadable: left out of provenance
    second = _pdf(tmp_path, "paper")
    with caplog.at_level(logging.WARNING):
        identities = _plan([gone, second], tmp_path=tmp_path)
    assert identities == {}
    assert any("share the document name" in r.getMessage() for r in caplog.records)
