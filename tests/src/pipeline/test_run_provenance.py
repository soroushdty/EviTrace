"""Run-scoped provenance lifecycle (provenance-core 6.5): settings, start, per-document hooks, run end.

``pipeline.run_provenance`` owns everything the orchestrator does with provenance
around the documents: validating the ``provenance`` configuration block, planning
the run and constructing its one recorder, the per-document failure hooks, and the
run-end routine (graph -> validation -> ``chain_validation`` mark -> post-validation
snapshot -> completeness -> artifact). These tests drive that module directly on
fabricated records; ``test_orchestrator_provenance.py`` drives it through
``run_pipeline``.

Provenance never aborts extraction, so most of the interesting cases here are
injected failures: each must be logged, degraded, and leave no exception.

Requirements: 1.3, 1.4, 2.2, 8.5, 8.6, 8.7.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import document_provenance as dp
from pipeline import pdf_processor
from pipeline import run_provenance as rp
from provenance.contracts import UnknownStageError
from provenance.identity import derive_source_id
from provenance.recorder import ProvenanceRecorder
from provenance.serialization import ARTIFACT_FILENAME, read_graph
from quality_control.models import QCBundle, UnifiedRecord
from utils import path_utils

_CREATED_AT = "2026-10-11T12:00:00Z"
_CHUNK_MODEL = "gpt-chunk"
_OPENAI = {
    "api_key": "sk-secret",
    "chunk_model": _CHUNK_MODEL,
    "synthesis_model": "gpt-synth",
    "num_chunks": 3,
}
_QC = {
    "ocr": False,
    "text_processor": {"class": "text_processing.composite.DefaultTextProcessor"},
    "quality_control": {"grobid_integration": {"failure_behavior": "fallback"}},
}
_QC_PROVENANCE = {"primary_branch_source": "grobid", "branch_selection": "adjudicated"}
_DEFAULTS = {
    "enabled": True,
    "output_subdir": "provenance",
    "severity_overrides": {},
    "fail_run_on_severity": None,
    "stage_contracts_strict": True,
}


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.fixture
def run_folder(tmp_path, monkeypatch) -> Path:
    """Point the run-scoped path helpers at ``tmp_path``; return the run folder."""
    monkeypatch.setattr(path_utils, "_LOCAL_SETTINGS", {"main_output_dir": str(tmp_path / "outputs")})
    monkeypatch.setattr(path_utils, "RUN_FOLDER_NAME", "run_test")
    root = tmp_path / "outputs" / "run_test"
    monkeypatch.setattr(path_utils, "OUTPUT_DIR", root)
    monkeypatch.setattr(pdf_processor, "OUTPUT_DIR", root)
    return root


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_the_shipped_configuration_block_yields_the_documented_defaults():
    """``load_local_config`` drops the block, so the loader here must read it from the YAML."""
    block = rp.load_run_provenance_block()
    assert block == _DEFAULTS
    settings = rp.resolve_provenance_settings(block)
    assert settings == rp.ProvenanceSettings()
    assert (settings.enabled, settings.output_subdir, settings.fail_run_on_severity) == (True, "provenance", None)


def test_the_block_loader_falls_back_to_defaults_when_the_yaml_cannot_be_read(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        block = rp.load_run_provenance_block(str(tmp_path / "missing.yaml"))
    assert block == _DEFAULTS
    assert any("cannot read the provenance configuration" in m for m in _warnings(caplog))


def test_a_valid_block_is_taken_as_written(caplog):
    block = {
        "enabled": False,
        "output_subdir": "audit/prov",
        "severity_overrides": {"orphan_node": "warning"},
        "fail_run_on_severity": "error",
        "stage_contracts_strict": False,
    }
    with caplog.at_level(logging.WARNING):
        settings = rp.resolve_provenance_settings(block)
    assert settings == rp.ProvenanceSettings(
        enabled=False,
        output_subdir="audit/prov",
        severity_overrides={"orphan_node": "warning"},
        fail_run_on_severity="error",
        stage_contracts_strict=False,
    )
    assert _warnings(caplog) == []


@pytest.mark.parametrize("bad", ["false", "true", 0, 1, None, [], "no"])
@pytest.mark.parametrize("key", ["enabled", "stage_contracts_strict"])
def test_a_non_boolean_flag_is_a_configuration_error_with_the_default_applied(key, bad, caplog):
    """A YAML string "false" is truthy; it must never be interpreted as a flag."""
    with caplog.at_level(logging.WARNING):
        settings = rp.resolve_provenance_settings({**_DEFAULTS, key: bad})
    value = getattr(settings, key)
    assert value is True and isinstance(value, bool)
    assert any(f"provenance.{key}" in m and repr(bad) in m for m in _warnings(caplog))


@pytest.mark.parametrize("key", ["enabled", "stage_contracts_strict"])
def test_real_booleans_are_kept_as_real_booleans(key):
    settings = rp.resolve_provenance_settings({**_DEFAULTS, key: False})
    assert getattr(settings, key) is False


def test_null_severity_overrides_means_no_overrides_without_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        settings = rp.resolve_provenance_settings({**_DEFAULTS, "severity_overrides": None})
    assert settings.severity_overrides == {}
    assert _warnings(caplog) == []


@pytest.mark.parametrize("bad", ["warning", ["a"], 3])
def test_non_mapping_severity_overrides_are_ignored_with_a_warning(bad, caplog):
    with caplog.at_level(logging.WARNING):
        settings = rp.resolve_provenance_settings({**_DEFAULTS, "severity_overrides": bad})
    assert settings.severity_overrides == {}
    assert any("provenance.severity_overrides" in m for m in _warnings(caplog))


@pytest.mark.parametrize("value", [None, "critical", "error"])
def test_fail_run_on_severity_accepts_null_critical_and_error(value, caplog):
    with caplog.at_level(logging.WARNING):
        settings = rp.resolve_provenance_settings({**_DEFAULTS, "fail_run_on_severity": value})
    assert settings.fail_run_on_severity == value
    assert _warnings(caplog) == []


@pytest.mark.parametrize("bad", ["warning", "info", "CRITICAL", "", 5, True, ["error"]])
def test_fail_run_on_severity_rejects_everything_else_with_a_warning(bad, caplog):
    with caplog.at_level(logging.WARNING):
        settings = rp.resolve_provenance_settings({**_DEFAULTS, "fail_run_on_severity": bad})
    assert settings.fail_run_on_severity is None
    assert any("provenance.fail_run_on_severity" in m for m in _warnings(caplog))


def test_a_missing_block_or_a_non_mapping_block_gives_the_defaults(caplog):
    assert rp.resolve_provenance_settings(None) == rp.ProvenanceSettings()
    with caplog.at_level(logging.WARNING):
        assert rp.resolve_provenance_settings("enabled: false") == rp.ProvenanceSettings()  # type: ignore[arg-type]
    assert any("must be a mapping" in m for m in _warnings(caplog))


def test_resolving_settings_does_not_mutate_the_block():
    block = {**_DEFAULTS, "severity_overrides": {"orphan_node": "warning"}}
    before = {**block, "severity_overrides": dict(block["severity_overrides"])}
    settings = rp.resolve_provenance_settings(block)
    assert block == before
    assert settings.severity_overrides is not block["severity_overrides"]


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "/etc/provenance",
        "/",
        "\\share\\prov",
        "C:\\prov",
        "C:/prov",
        "../x",
        "..",
        "a/../../b",
        "a/..",
        "a\\..\\b",
        "./../x",
        "prov/../../..",
        None,
        5,
        ["provenance"],
        "pro\x00v",
        ".",
        "./",
        "./.",
        ".//",
        "outputs",
        "outputs/",
        " outputs ",
        ".\\",
    ],
)
def test_an_unusable_output_subdir_falls_back_to_the_default_with_a_warning_naming_the_value(bad, caplog):
    with caplog.at_level(logging.WARNING):
        settings = rp.resolve_provenance_settings({**_DEFAULTS, "output_subdir": bad})
    assert settings.output_subdir == "provenance"
    messages = [m for m in _warnings(caplog) if "provenance.output_subdir" in m]
    assert len(messages) == 1
    assert repr(bad) in messages[0] or str(bad) in messages[0]
    assert "'provenance'" in messages[0]


@pytest.mark.parametrize("good", ["provenance", "audit", "a/b/c", "a..b", "..hidden", "dir.d", "x\\y", "./provenance", "provenance/", "./outputs", "outputs/x", "outputs2"])
def test_a_relative_output_subdir_without_a_dotdot_segment_is_accepted(good, caplog):
    with caplog.at_level(logging.WARNING):
        assert rp.validated_output_subdir(good) == good
    assert _warnings(caplog) == []


def test_a_subdir_naming_the_run_folder_is_rejected_by_the_resolver_check_too(run_folder, caplog):
    """Defence in depth: settings built directly (bypassing validation) never put the artifact in the run root."""
    for fragment in (".", "./", "outputs", "outputs/", "./."):
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            directory = rp.artifact_directory(rp.ProvenanceSettings(output_subdir=fragment))
        assert directory == run_folder / "provenance", fragment
        assert any("run folder" in m for m in _warnings(caplog)), fragment


@pytest.mark.parametrize("good, expected", [("./provenance", "provenance"), ("provenance/", "provenance"), ("./outputs", "outputs")])
def test_equivalent_spellings_of_a_real_subdirectory_resolve_under_the_run_folder(good, expected, run_folder):
    directory = rp.artifact_directory(rp.ProvenanceSettings(output_subdir=good))
    assert directory == run_folder / expected and directory != run_folder


def test_the_subdir_is_stripped():
    assert rp.validated_output_subdir("  audit/prov ") == "audit/prov"


def test_the_artifact_directory_is_the_subdir_under_the_run_folder(run_folder):
    assert rp.artifact_directory(rp.ProvenanceSettings()) == run_folder / "provenance"
    assert rp.artifact_directory(rp.ProvenanceSettings(output_subdir="audit/prov")) == run_folder / "audit" / "prov"


def test_the_artifact_directory_for_the_default_is_the_run_scoped_provenance_path():
    assert rp.artifact_directory(rp.ProvenanceSettings()) == path_utils.resolve_run_output_path("provenance")


def test_a_subdir_that_resolves_outside_the_run_folder_falls_back_to_the_default(run_folder, tmp_path, caplog):
    run_folder.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (run_folder / "link").symlink_to(outside, target_is_directory=True)
    with caplog.at_level(logging.WARNING):
        directory = rp.artifact_directory(rp.ProvenanceSettings(output_subdir="link/sub"))
    assert directory == run_folder / "provenance"
    assert any("outside the run folder" in m and "link/sub" in m for m in _warnings(caplog))


# ---------------------------------------------------------------------------
# Fabricated run
# ---------------------------------------------------------------------------


def _pdf(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "pdfs" / f"{name}.pdf"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(f"%PDF-1.4 {name}".encode())
    return path


def _bundle(name: str = "doc") -> QCBundle:
    return QCBundle(
        branches=[],
        unified=UnifiedRecord(document_id=name, content={"exact_text": "x", "provenance": dict(_QC_PROVENANCE)}),
    )


def _start(paths, manifest=None, *, settings=None, openai=None, qc=None, **kwargs):
    return rp.start_run_provenance(
        paths,
        manifest or {},
        openai_config=openai or _OPENAI,
        qc_config=qc or _QC,
        settings=settings or rp.ProvenanceSettings(),
        clock=lambda: _CREATED_AT,
        **kwargs,
    )


def _recorded_plan(tmp_path: Path, names=("a", "b"), settings=None):
    """A started plan whose documents have their source and QC step recorded (as build_qc_bundle does)."""
    paths = [_pdf(tmp_path, n) for n in names]
    plan = _start(paths, settings=settings)
    assert plan is not None
    for index, name in enumerate(names):
        document = plan.document(index)
        assert document is not None
        dp.emit_document_provenance(
            plan.recorder, _bundle(name), document.identity, pdf_name=name, run_id=plan.run_id
        )
    return paths, plan


# ---------------------------------------------------------------------------
# start_run_provenance
# ---------------------------------------------------------------------------


def test_start_builds_one_recorder_with_the_run_identity_and_a_provenance_per_planned_document(tmp_path, run_folder):
    paths = [_pdf(tmp_path, "a"), _pdf(tmp_path, "b")]
    plan = _start(paths)
    assert plan is not None
    snapshot = plan.recorder.snapshot()
    assert snapshot.run.run_id == plan.run_id
    assert snapshot.run.created_at == _CREATED_AT
    assert snapshot.run.model_ids == (_CHUNK_MODEL, "gpt-synth")
    assert sorted(snapshot.run.document_fingerprints) == ["a", "b"]
    for index, name in enumerate(("a", "b")):
        document = plan.document(index)
        assert document is not None and document.recorder is plan.recorder and document.run_id == plan.run_id
        assert document.identity.pdf_content_hash == hashlib.sha256(paths[index].read_bytes()).hexdigest()
    assert plan.document(2) is None


def test_start_passes_a_real_boolean_enable_flag_to_the_recorder(tmp_path, run_folder):
    seen = []
    real = rp.ProvenanceRecorder

    def spy(run, *, enabled):
        seen.append(enabled)
        return real(run, enabled=enabled)

    with patch.object(rp, "ProvenanceRecorder", side_effect=spy):
        assert _start([_pdf(tmp_path, "a")]) is not None
    assert seen == [True] and type(seen[0]) is bool


def test_start_with_provenance_disabled_does_nothing_beyond_one_info_line(tmp_path, run_folder, caplog):
    boom = AssertionError("must not plan when disabled")
    with patch.object(rp, "plan_run_documents", side_effect=boom), patch.object(
        rp, "ProvenanceRecorder", side_effect=boom
    ), caplog.at_level(logging.DEBUG):
        assert _start([_pdf(tmp_path, "a")], settings=rp.ProvenanceSettings(enabled=False)) is None
    records = [r for r in caplog.records if r.name.endswith("run_provenance")]
    assert len(records) == 1 and records[0].levelno == logging.INFO


def test_start_with_every_document_resumed_is_an_empty_run_with_no_artifact(tmp_path, run_folder, caplog):
    paths = [_pdf(tmp_path, "a")]
    run_folder.mkdir(parents=True)
    (run_folder / "a.extracted.json").write_text("[]", encoding="utf-8")
    manifest = {"a": {"status": "complete"}}
    with caplog.at_level(logging.INFO):
        assert _start(paths, manifest) is None
    assert any("no provenance artifact will be produced" in r.getMessage() for r in caplog.records)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_a_resumed_document_gets_no_provenance_and_is_not_in_the_run_identity(tmp_path, run_folder):
    paths = [_pdf(tmp_path, "a"), _pdf(tmp_path, "b")]
    run_folder.mkdir(parents=True)
    (run_folder / "b.extracted.json").write_text("[]", encoding="utf-8")
    plan = _start(paths, {"b": {"status": "complete"}})
    assert plan is not None
    assert plan.document(0) is not None and plan.document(1) is None
    assert sorted(plan.recorder.snapshot().run.document_fingerprints) == ["a"]


def test_the_second_of_two_same_stem_pdfs_gets_no_provenance(tmp_path, run_folder, caplog):
    first = _pdf(tmp_path, "dup")
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    second = other_dir / "dup.pdf"
    second.write_bytes(b"%PDF-1.4 a different body")
    third = _pdf(tmp_path, "solo")
    with caplog.at_level(logging.WARNING):
        plan = _start([first, second, third])
    assert plan is not None
    document = plan.document(0)
    assert document is not None
    assert document.identity.pdf_content_hash == hashlib.sha256(first.read_bytes()).hexdigest()
    assert plan.document(1) is None
    assert plan.document(2) is not None
    assert any("share the document name" in m for m in _warnings(caplog))


def test_an_unreadable_pdf_is_left_out_but_the_rest_of_the_run_is_recorded(tmp_path, run_folder):
    paths = [tmp_path / "gone.pdf", _pdf(tmp_path, "a")]
    plan = _start(paths)
    assert plan is not None
    assert plan.document(0) is None and plan.document(1) is not None


def test_a_run_whose_only_document_is_unreadable_has_no_provenance(tmp_path, run_folder):
    assert _start([tmp_path / "gone.pdf"]) is None


@pytest.mark.parametrize(
    "target, error",
    [
        ("plan_run_documents", RuntimeError("manifest is broken")),
        ("build_run_identity", TypeError("created_at must be a str")),
        ("ProvenanceRecorder", RuntimeError("recorder exploded")),
    ],
)
def test_a_failure_while_starting_runs_the_pipeline_without_provenance(target, error, tmp_path, run_folder, caplog):
    with patch.object(rp, target, side_effect=error), caplog.at_level(logging.WARNING):
        assert _start([_pdf(tmp_path, "a")]) is None
    assert any("cannot start for this run" in m and str(error) in m for m in _warnings(caplog))


def test_a_run_identity_the_adapter_refuses_means_no_provenance(tmp_path, run_folder):
    with patch.object(rp, "build_run_identity", return_value=None):
        assert _start([_pdf(tmp_path, "a")]) is None


def test_start_reads_no_clock_other_than_the_one_it_is_given(tmp_path, run_folder):
    plan = _start([_pdf(tmp_path, "a")])
    assert plan is not None and plan.recorder.snapshot().run.created_at == _CREATED_AT


def test_the_default_clock_is_an_iso_8601_utc_string(tmp_path, run_folder):
    plan = rp.start_run_provenance(
        [_pdf(tmp_path, "a")], {}, openai_config=_OPENAI, qc_config=_QC, settings=rp.ProvenanceSettings()
    )
    assert plan is not None
    from datetime import datetime

    created = plan.recorder.snapshot().run.created_at
    assert created.endswith("Z") and datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Per-document failure hooks
# ---------------------------------------------------------------------------


def test_a_document_whose_bundle_raised_is_represented_with_document_extraction_failed(tmp_path, run_folder):
    paths = [_pdf(tmp_path, "a"), _pdf(tmp_path, "b")]
    plan = _start(paths)
    assert plan is not None
    plan.record_document_failure(0, RuntimeError("QC pipeline exploded"))
    snapshot = plan.recorder.snapshot()
    source_a = derive_source_id(plan.document(0).identity.pdf_content_hash)
    source_b = derive_source_id(plan.document(1).identity.pdf_content_hash)
    assert source_a in snapshot.nodes and source_b not in snapshot.nodes
    assert snapshot.document_stage_status[source_a]["document_extraction"] == "failed"
    assert "QC pipeline exploded" in snapshot.document_stage_failure_reasons[source_a]["document_extraction"][0]
    assert source_b not in snapshot.document_stage_status  # the other document is unaffected


def test_the_failure_hooks_are_no_ops_for_a_document_without_provenance(tmp_path, run_folder):
    plan = _start([_pdf(tmp_path, "a")])
    assert plan is not None
    before = plan.recorder.snapshot()
    plan.record_document_failure(7, RuntimeError("x"))
    plan.record_extraction_failure(7, RuntimeError("x"))
    assert plan.recorder.snapshot() == before


def test_an_exception_from_process_pdf_marks_the_undecided_claim_stages_failed(tmp_path, run_folder):
    paths, plan = _recorded_plan(tmp_path, ("a", "b"))
    source_a = derive_source_id(plan.document(0).identity.pdf_content_hash)
    source_b = derive_source_id(plan.document(1).identity.pdf_content_hash)
    plan.recorder.mark_stage_complete("metadata_prefill", source_id=source_a)  # already decided: stays complete
    plan.record_extraction_failure(0, RuntimeError("synthesis blew up"))
    statuses = plan.recorder.snapshot().document_stage_status
    assert statuses[source_a]["metadata_prefill"] == "complete"
    assert statuses[source_a]["extraction_chunk"] == "failed"
    assert statuses[source_a]["synthesis"] == "failed"
    assert "claim" not in statuses[source_a]
    reasons = plan.recorder.snapshot().document_stage_failure_reasons[source_a]
    assert "synthesis blew up" in reasons["synthesis"][0]
    assert "metadata_prefill" not in reasons
    assert not {"metadata_prefill", "extraction_chunk", "synthesis"} & set(statuses[source_b])
    # document-scoped stages were not touched by the claim-stage hook
    assert statuses[source_a]["document_extraction"] == "complete"
    assert statuses[source_a]["quality_control"] == "complete"


def test_the_failure_hooks_never_raise(tmp_path, run_folder):
    paths, plan = _recorded_plan(tmp_path, ("a",))
    with patch.object(rp, "emit_document_failure", side_effect=RuntimeError("boom")):
        plan.record_document_failure(0, RuntimeError("x"))
    with patch.object(rp.DocumentClaims, "for_document", side_effect=RuntimeError("boom")):
        plan.record_extraction_failure(0, RuntimeError("x"))


# ---------------------------------------------------------------------------
# finish_run_provenance
# ---------------------------------------------------------------------------


def _finish(plan) -> rp.ProvenanceRunSummary:
    return rp.finish_run_provenance(plan)


def _with_critical_finding(plan) -> None:
    """Record a ``dangling_reference`` issue (critical by default) the way the validator sink does."""
    plan.recorder.issue_sink()("dangling_reference", "claim cites an unknown id", {"reference": "S999999"})


def test_the_run_end_writes_a_loadable_artifact_with_validation_and_completeness(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    summary = _finish(plan)
    assert summary.artifact_path == run_folder / "provenance" / ARTIFACT_FILENAME
    assert summary.artifact_path.is_file()
    assert summary.errors == () and summary.validated is True
    graph = read_graph(summary.artifact_path)
    assert graph.run.run_id == plan.run_id
    assert graph.validation is not None and graph.validation.completed
    assert graph.completeness is not None
    assert graph.completeness.run.state == summary.completeness_state
    assert sorted(graph.completeness.documents) == sorted(
        derive_source_id(plan.document(i).identity.pdf_content_hash) for i in (0, 1)
    )
    assert any(n.kind == "validation" for n in graph.nodes.values())


def test_chain_validation_is_marked_complete_run_wide_and_read_as_complete_by_completeness(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    summary = _finish(plan)
    snapshot = plan.recorder.snapshot()
    assert snapshot.stage_status["chain_validation"] == "complete"
    assert snapshot.stage_failure_reasons == {}
    graph = read_graph(summary.artifact_path)
    assert "chain_validation" not in (graph.completeness.run.reason or "")
    assert "chain_validation" not in graph.completeness.run.responsible_stages


def test_the_artifact_directory_follows_the_configured_subdir(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path, settings=rp.ProvenanceSettings(output_subdir="audit/prov"))
    summary = _finish(plan)
    assert summary.artifact_path == run_folder / "audit" / "prov" / ARTIFACT_FILENAME
    assert summary.artifact_path.is_file()
    assert not (run_folder / "provenance").exists()


def test_the_summary_line_reports_node_counts_findings_and_completeness(tmp_path, run_folder, caplog):
    _, plan = _recorded_plan(tmp_path)
    _with_critical_finding(plan)
    with caplog.at_level(logging.INFO):
        summary = _finish(plan)
    lines = [r for r in caplog.records if r.getMessage().startswith("Provenance summary:")]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    text = lines[0].getMessage()
    assert f"run={plan.run_id}" in text
    assert "source=2" in text and "derivation=2" in text and "validation=1" in text
    assert "nodes=5" in text
    assert "critical=1" in text
    assert f"completeness={summary.completeness_state}" in text
    assert "validated=True" in text and str(summary.artifact_path) in text
    assert summary.node_counts == {"source": 2, "derivation": 2, "validation": 1}
    assert summary.finding_counts == {"critical": 1}


def test_severity_overrides_reach_the_validator_and_change_the_written_severity(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path, settings=rp.ProvenanceSettings(severity_overrides={"dangling_reference": "warning"}))
    _with_critical_finding(plan)
    summary = _finish(plan)
    graph = read_graph(summary.artifact_path)
    (finding,) = [f for f in graph.validation.findings if f.code == "dangling_reference"]
    assert finding.severity == "warning"
    assert summary.finding_counts == {"warning": 1}


def test_recorded_findings_reach_the_report(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    _with_critical_finding(plan)
    with patch.object(rp, "validate_chain", wraps=rp.validate_chain) as spy:
        _finish(plan)
    assert [f.code for f in spy.call_args.kwargs["recorded_findings"]] == ["dangling_reference"]
    assert spy.call_args.kwargs["severity_overrides"] == {}


@pytest.mark.parametrize("strict", [True, False])
def test_the_strict_flag_reaches_compute_completeness_as_a_bool(strict, tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path, settings=rp.ProvenanceSettings(stage_contracts_strict=strict))
    with patch.object(rp, "compute_completeness", wraps=rp.compute_completeness) as spy:
        _finish(plan)
    value = spy.call_args.kwargs["stage_contracts_strict"]
    assert value is strict and type(value) is bool


def test_completeness_is_computed_from_a_snapshot_taken_after_chain_validation_was_marked(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(rp, "compute_completeness", wraps=rp.compute_completeness) as spy:
        _finish(plan)
    graph_arg, recorded_arg = spy.call_args.args[:2]
    assert recorded_arg.stage_status["chain_validation"] == "complete"
    assert graph_arg.validation is not None  # the validated graph, not the bare one


@pytest.mark.parametrize(
    "threshold, expect_failed",
    [(None, False), ("critical", True), ("error", True)],
)
def test_fail_run_on_severity_trips_when_the_findings_reach_it(threshold, expect_failed, tmp_path, run_folder, caplog):
    _, plan = _recorded_plan(tmp_path, settings=rp.ProvenanceSettings(fail_run_on_severity=threshold))
    _with_critical_finding(plan)
    with caplog.at_level(logging.INFO):
        summary = _finish(plan)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert summary.failed is expect_failed
    assert summary.artifact_path is not None and summary.artifact_path.is_file()  # extraction/artifact still written
    if expect_failed:
        assert len(errors) == 1 and "Provenance failure" in errors[0].getMessage()
        assert summary.failure_reason and "critical=1" in summary.failure_reason
        assert "provenance_failure=True" in " ".join(r.getMessage() for r in caplog.records)
    else:
        assert errors == [] and summary.failure_reason is None


def test_fail_run_on_error_is_not_tripped_by_warnings_only(tmp_path, run_folder, caplog):
    settings = rp.ProvenanceSettings(
        fail_run_on_severity="error", severity_overrides={"dangling_reference": "warning"}
    )
    _, plan = _recorded_plan(tmp_path, settings=settings)
    _with_critical_finding(plan)
    with caplog.at_level(logging.ERROR):
        summary = _finish(plan)
    assert summary.failed is False and caplog.records == []


def test_fail_run_on_critical_is_not_tripped_by_an_error_finding(tmp_path, run_folder):
    settings = rp.ProvenanceSettings(fail_run_on_severity="critical", severity_overrides={"dangling_reference": "error"})
    _, plan = _recorded_plan(tmp_path, settings=settings)
    _with_critical_finding(plan)
    assert _finish(plan).failed is False


# -- degradation -------------------------------------------------------------


def test_a_validator_that_raises_leaves_an_unvalidated_artifact_and_a_failed_chain_validation(tmp_path, run_folder, caplog):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(rp, "validate_chain", side_effect=RuntimeError("validator exploded")), caplog.at_level(
        logging.WARNING
    ):
        summary = _finish(plan)
    assert summary.validated is False
    assert any("chain validation failed" in e and "validator exploded" in e for e in summary.errors)
    assert any("validator exploded" in m for m in _warnings(caplog))
    snapshot = plan.recorder.snapshot()
    assert snapshot.stage_status["chain_validation"] == "failed"
    assert "validator exploded" in snapshot.stage_failure_reasons["chain_validation"]
    graph = read_graph(summary.artifact_path)
    assert graph.validation is None  # reported as unvalidated, never as valid
    assert graph.completeness.run.state != "complete"


def test_a_validation_that_did_not_complete_marks_chain_validation_failed_with_its_reason(tmp_path, run_folder):
    from provenance.models import ChainValidationReport

    _, plan = _recorded_plan(tmp_path)
    unfinished = ChainValidationReport(
        completed=False, incompletion_reason="check 'graph_cycle' could not run", findings=(), counts_by_severity={}
    )
    with patch.object(rp, "validate_chain", return_value=unfinished):
        summary = _finish(plan)
    assert summary.validated is False
    snapshot = plan.recorder.snapshot()
    assert snapshot.stage_status["chain_validation"] == "failed"
    assert snapshot.stage_failure_reasons["chain_validation"] == "check 'graph_cycle' could not run"
    assert read_graph(summary.artifact_path).completeness.run.state in {"incomplete", "undetermined"}


def test_a_graph_builder_that_raises_writes_nothing_and_does_not_raise(tmp_path, run_folder, caplog):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(rp, "build_graph", side_effect=RuntimeError("builder exploded")), caplog.at_level(logging.WARNING):
        summary = _finish(plan)
    assert summary.artifact_path is None and summary.errors and "builder exploded" in summary.errors[0]
    assert not (run_folder / "provenance").exists()
    assert any("graph assembly failed" in m for m in _warnings(caplog))


@pytest.mark.parametrize(
    "error",
    [ValueError("non-finite float"), TypeError("not JSON"), OSError(28, "No space left on device"), RuntimeError("other")],
)
def test_a_writer_failure_is_logged_and_degraded_with_no_partial_artifact(error, tmp_path, run_folder, caplog):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(rp, "write_graph", side_effect=error), caplog.at_level(logging.WARNING):
        summary = _finish(plan)
    assert summary.artifact_path is None
    assert any("writing the artifact failed" in e for e in summary.errors)
    assert any("writing the artifact failed" in m for m in _warnings(caplog))
    assert list(run_folder.rglob(ARTIFACT_FILENAME)) == []
    assert summary.node_counts  # the summary is still logged and returned


def test_a_real_serialization_error_leaves_no_file_and_no_temp_file(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    real = rp.write_graph

    def unserializable(graph, directory):
        from dataclasses import replace

        node = next(iter(graph.nodes.values()))
        bad = replace(node, extensions={"x-test-bad": float("nan")})
        return real(replace(graph, nodes={**graph.nodes, node.node_id: bad}), directory)

    with patch.object(rp, "write_graph", side_effect=unserializable):
        summary = _finish(plan)
    assert summary.artifact_path is None and any("writing the artifact failed" in e for e in summary.errors)
    assert list(run_folder.rglob("provenance_graph*")) == []


def test_a_write_target_that_is_a_file_is_an_os_error_that_is_degraded(tmp_path, run_folder):
    run_folder.mkdir(parents=True)
    (run_folder / "provenance").write_text("not a directory", encoding="utf-8")
    _, plan = _recorded_plan(tmp_path)
    summary = _finish(plan)
    assert summary.artifact_path is None and any("writing the artifact failed" in e for e in summary.errors)


def test_a_completeness_failure_becomes_an_undetermined_run_with_the_reason(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(rp, "compute_completeness", side_effect=RuntimeError("completeness exploded")):
        summary = _finish(plan)
    assert summary.completeness_state == "undetermined"
    graph = read_graph(summary.artifact_path)
    assert graph.completeness.run.state == "undetermined"
    assert "completeness exploded" in graph.completeness.run.reason


def test_an_attach_failure_is_degraded_and_the_artifact_is_still_written(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(rp, "attach_completeness", side_effect=TypeError("bad report")):
        summary = _finish(plan)
    assert summary.artifact_path is not None and read_graph(summary.artifact_path).completeness is None
    assert any("attaching completeness failed" in e for e in summary.errors)


def test_attach_validation_failing_is_an_unvalidated_run(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(rp, "attach_validation", side_effect=TypeError("bad report")):
        summary = _finish(plan)
    assert summary.validated is False
    assert plan.recorder.snapshot().stage_status["chain_validation"] == "failed"


def test_an_undeclared_stage_error_is_caught_when_marking_chain_validation(tmp_path, run_folder, caplog):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(
        ProvenanceRecorder, "mark_stage_complete", side_effect=UnknownStageError("stage 'chain_validation' drifted")
    ), caplog.at_level(logging.WARNING):
        summary = _finish(plan)
    assert summary.artifact_path is not None
    assert any("marking chain_validation failed" in e for e in summary.errors)


def test_an_invalid_identifier_error_from_the_recorder_is_caught_too(tmp_path, run_folder):
    from provenance.identity import InvalidIdentifierError

    _, plan = _recorded_plan(tmp_path)
    with patch.object(ProvenanceRecorder, "snapshot", side_effect=InvalidIdentifierError("bad id")):
        summary = _finish(plan)
    assert summary.artifact_path is None and summary.errors


def test_the_run_end_never_raises_even_for_an_unexpected_failure(tmp_path, run_folder, caplog):
    _, plan = _recorded_plan(tmp_path)
    with patch.object(rp, "_log_summary", side_effect=[RuntimeError("logging exploded"), None]), caplog.at_level(
        logging.WARNING
    ):
        summary = _finish(plan)
    assert summary.artifact_path is None and "logging exploded" in summary.errors[0]


def test_writing_the_artifact_twice_is_byte_identical(tmp_path, run_folder):
    _, plan = _recorded_plan(tmp_path)
    first = _finish(plan).artifact_path.read_bytes()
    second = _finish(plan).artifact_path.read_bytes()
    assert first == second


def test_the_run_summary_type_is_a_plain_dataclass_of_simple_values():
    summary = rp.ProvenanceRunSummary(run_id=None)
    assert summary.artifact_path is None and summary.failed is False and summary.errors == ()
