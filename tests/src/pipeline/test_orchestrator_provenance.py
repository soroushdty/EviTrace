"""``run_pipeline`` and the provenance lifecycle (provenance-core 6.5).

Drives the real ``orchestrator.run_pipeline`` over fabricated PDFs with
``build_qc_bundle`` replaced by a fake that builds a bundle and calls the same
``emit_document_provenance`` hook the real one ends with, and with either a fake
``process_pdf`` (call-argument and failure tests) or the REAL ``process_pdf`` driven
by scripted chunk responses (end-to-end artifact test). No GROBID, network or OpenAI.

What is pinned here:

* the artifact ``provenance_graph.json`` is written under the configured
  subdirectory of the run folder, carries validation records, a complete
  ``chain_validation`` stage and computed completeness, and lists only the
  documents actually processed (a resumed one has no source, no fingerprint);
* an invalid ``output_subdir`` falls back to ``provenance`` with a WARNING;
* provenance disabled is the unchanged baseline: ``build_qc_bundle`` and
  ``process_pdf`` receive exactly the arguments they received before provenance
  existed (no ``provenance`` keyword), no recorder is built, and no artifact appears;
* a provenance error never changes the extraction results and never escapes;
* ``process_pdf`` raising marks that document's claim stages failed and the
  exception still reaches the unchanged ``gather(return_exceptions=True)`` handling;
* ``build_qc_bundle`` raising represents the document as failed, others unaffected;
* ``fail_run_on_severity`` logs at ERROR while the extraction output is untouched.

Requirements: 1.3, 1.4, 2.2, 8.5, 8.6, 8.7.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pipeline.pdf_processor as pdf_processor
from pipeline import document_provenance as dp
from pipeline import run_provenance as rp
from provenance.identity import derive_claim_id
from provenance.serialization import ARTIFACT_FILENAME, read_graph
from quality_control.models import QCBundle, UnifiedRecord
from utils import path_utils

_SRC = Path(__file__).resolve().parents[3] / "src"
_TEI_NS = "http://www.tei-c.org/ns/1.0"
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
_CHUNK_FIELDS = {
    1: [{"field_index": i, **_FIELD_LOOKUP[i]} for i in (1, 2, 3)],
    2: [{"field_index": 4, **_FIELD_LOOKUP[4]}],
    3: [{"field_index": 5, **_FIELD_LOOKUP[5]}],
}


def _ext(*items: tuple[int, str, list[str], str]) -> str:
    return json.dumps({"extractions": [{"i": i, "v": v, "loc": loc, "c": c} for i, v, loc, c in items]})


def _scripted_responses() -> dict[int, str]:
    return {
        1: _ext((3, "RCT", ["S000001"], "h")),
        2: _ext((4, "120", ["S000002", "S000003"], "m")),
        3: _ext((5, "Sound", ["S000001"], "m")),
    }


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _load_orchestrator() -> ModuleType:
    """A fresh copy of the REAL orchestrator (the concurrency tests leave a stub-built one in sys.modules)."""
    spec = importlib.util.spec_from_file_location("pipeline._orchestrator_under_test", _SRC / "pipeline" / "orchestrator.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Env:
    """Everything one ``run_pipeline`` call needs, rooted in ``tmp_path``."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.run_root = tmp_path / "outputs" / "run_test"
        self.run_root.mkdir(parents=True)
        monkeypatch.setattr(path_utils, "_LOCAL_SETTINGS", {"main_output_dir": str(tmp_path / "outputs")})
        monkeypatch.setattr(path_utils, "RUN_FOLDER_NAME", "run_test")
        monkeypatch.setattr(path_utils, "OUTPUT_DIR", self.run_root)
        monkeypatch.setattr(pdf_processor, "OUTPUT_DIR", self.run_root)
        # Other test modules evict and re-import ``pipeline.*`` (test_pdf_processor_helpers does it at
        # import time), so a freshly loaded orchestrator would otherwise bind module copies different
        # from the ones this file imported and patches. Pin the copies this file holds: the orchestrator's
        # lazy ``from .run_provenance import ...`` resolves through sys.modules, and its pdf_processor is
        # set explicitly.
        monkeypatch.setitem(sys.modules, "pipeline.run_provenance", rp)
        self.orch = _load_orchestrator()
        monkeypatch.setattr(self.orch, "pdf_processor", pdf_processor)
        monkeypatch.setattr(self.orch, "OUTPUT_DIR", self.run_root)
        monkeypatch.setattr(
            self.orch,
            "_openai_config",
            {
                **self.orch._openai_config,
                "chunk_model": "gpt-chunk",
                "synthesis_model": "gpt-synth",
                "num_chunks": 3,
                "enable_cache_prewarm": False,
                "prewarm_synthesis_if_model_diff": False,
                "evidence_cache_dir": str(tmp_path / "evidence_cache"),
            },
        )
        monkeypatch.setattr(self.orch, "load_chunk_fields", lambda: copy.deepcopy(_CHUNK_FIELDS))
        monkeypatch.setattr(self.orch, "_build_field_lookup", lambda: copy.deepcopy(_FIELD_LOOKUP))
        self.manifest: dict[str, Any] = {}
        monkeypatch.setattr(self.orch, "load_manifest", lambda: self.manifest)
        monkeypatch.setattr(self.orch, "save_manifest", lambda manifest: None)
        monkeypatch.setattr(pdf_processor, "save_manifest", lambda manifest: None)
        monkeypatch.setattr(pdf_processor, "validate_qc_context_input", lambda ctx: None)
        self.bundle_calls: list[tuple[tuple, dict]] = []
        self.process_calls: list[tuple[tuple, dict]] = []
        self.monkeypatch = monkeypatch

    # -- inputs -----------------------------------------------------------------

    def pdf(self, name: str, *, directory: str = "pdfs", body: bytes | None = None) -> Path:
        path = self.tmp_path / directory / f"{name}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if body is not None else f"%PDF-1.4 {name}".encode())
        return path

    def resumed(self, name: str) -> None:
        """A document the manifest and its output file already mark complete."""
        (self.run_root / f"{name}.extracted.json").write_text(json.dumps([{"field_index": 1}]), encoding="utf-8")
        self.manifest[name] = {"status": "complete"}

    # -- fakes ------------------------------------------------------------------

    @staticmethod
    def bundle(name: str, pdf_path: Path) -> QCBundle:
        return QCBundle(
            branches=[],
            unified=UnifiedRecord(
                document_id=name,
                content={
                    "exact_text": "We enrolled 120 patients. This was a randomized trial. Outcomes were mortality.",
                    "source_pdf_path": str(pdf_path),
                    "grobid_tei_xml": _TEI,
                    "provenance": copy.deepcopy(_QC_PROVENANCE),
                },
            ),
        )

    def fake_build_qc_bundle(self, fail: set[str] = frozenset()):
        """``build_qc_bundle`` stand-in that ends like the real one: it emits the document's provenance."""

        def build(pdf_path, pdf_name, qc_config, *, provenance=None):
            self.bundle_calls.append(((pdf_path, pdf_name, qc_config), {"provenance": provenance} if provenance else {}))
            if pdf_name in fail:
                raise RuntimeError(f"QC pipeline exploded for {pdf_name}")
            bundle = self.bundle(pdf_name, Path(pdf_path))
            if provenance is not None:
                dp.emit_document_provenance(
                    provenance.recorder, bundle, provenance.identity, pdf_name=pdf_name, run_id=provenance.run_id
                )
            return bundle

        return build

    def use_real_process_pdf(self, responses: dict[int, str] | None = None) -> MagicMock:
        """Run the real ``process_pdf`` on scripted chunk responses."""
        scripted = responses if responses is not None else _scripted_responses()

        async def extract_chunk(chunk_num, source, fields, semaphore, **kwargs):
            return scripted[chunk_num]

        api = MagicMock()
        api.extract_chunk = extract_chunk
        api.warm_pdf_cache = AsyncMock()
        self.monkeypatch.setitem(sys.modules, "agents.openai.api_client", api)
        real = pdf_processor.process_pdf

        async def recording(*args, **kwargs):
            self.process_calls.append((args, {k: v for k, v in kwargs.items() if k == "provenance"}))
            return await real(*args, **kwargs)

        self.monkeypatch.setattr(pdf_processor, "process_pdf", recording)
        return api

    def use_fake_process_pdf(self, *, raises: dict[str, BaseException] | None = None, fields=None):
        raises = raises or {}
        result = fields if fields is not None else [{"field_index": 1, "extracted_value": "Smith"}]

        async def fake(qc_context, chunk_fields, field_lookup, api_semaphore, manifest, manifest_lock,
                       runtime_config, collector=None, **kwargs):
            self.process_calls.append(((qc_context.unified.document_id,), dict(kwargs)))
            name = qc_context.unified.document_id
            if name in raises:
                raise raises[name]
            return result

        self.monkeypatch.setattr(pdf_processor, "process_pdf", fake)

    def run(self, paths: list[Path], *, provenance: dict | None = None, fail_bundle: set[str] = frozenset(), **kwargs):
        self.monkeypatch.setattr(self.orch, "build_qc_bundle", self.fake_build_qc_bundle(fail_bundle))
        self.monkeypatch.setattr(self.orch, "generate_token_report", MagicMock(return_value=MagicMock(status="ok", telemetry_records=[])))
        return asyncio.run(
            self.orch.run_pipeline(paths, pdf_concurrency=2, provenance_config=provenance, **kwargs)
        )

    @property
    def artifact(self) -> Path:
        return self.run_root / "provenance" / ARTIFACT_FILENAME


@pytest.fixture
def env(tmp_path, monkeypatch) -> _Env:
    return _Env(tmp_path, monkeypatch)


def _cfg(**overrides: Any) -> dict:
    return {**rp.load_run_provenance_block(), **overrides}


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# End to end: the artifact
# ---------------------------------------------------------------------------


def test_a_run_over_fabricated_inputs_writes_the_artifact_into_the_run_folder(env):
    a, b, resumed = env.pdf("a"), env.pdf("b"), env.pdf("c")
    env.resumed("c")
    env.use_real_process_pdf()
    results = env.run([a, b, resumed], provenance=_cfg())

    assert sorted(r["pdf"] for r in results) == ["a.pdf", "b.pdf", "c.pdf"]  # extraction results unchanged
    assert env.artifact.is_file(), "the artifact lives in <run folder>/provenance/"
    graph = read_graph(env.artifact)

    sources = {n.source_label: n for n in graph.nodes.values() if n.kind == "source"}
    assert sorted(sources) == ["a", "b"], "a resumed document has no source node"
    assert sorted(graph.run.document_fingerprints) == ["a", "b"], "the run identity lists the documents processed"

    # the source is linked to the extraction output (1.3) and claims lead to evidence and source (1.4)
    assert sources["a"].extensions["x-manifest-output_path"] == "a.extracted.json"
    claim = graph.nodes[derive_claim_id(sources["a"].node_id, 3)]
    path = graph.path_from_claim(claim.node_id)
    assert [n.kind for n in path][0] == "claim" and "evidence" in {n.kind for n in path} and "source" in {n.kind for n in path}

    # validation records and a completed chain-validation stage (8.7)
    assert graph.validation is not None and graph.validation.completed
    validation_records = [n for n in graph.nodes.values() if n.kind == "validation"]
    assert [n.check_name for n in validation_records] == ["chain_validation"]
    assert validation_records[0].completed is True

    # completeness is computed at run, document and claim level (2.2) and chain_validation is not 'not run'
    assert graph.completeness is not None
    assert sorted(graph.completeness.documents) == sorted(n.node_id for n in sources.values())
    assert graph.completeness.claims
    assert "chain_validation" not in (graph.completeness.run.reason or "")
    assert not any("chain_validation" in (c.reason or "") for c in graph.completeness.documents.values())


def test_the_real_process_pdf_receives_provenance_only_for_planned_documents(env):
    a, resumed = env.pdf("a"), env.pdf("c")
    env.resumed("c")
    env.use_real_process_pdf()
    env.run([a, resumed], provenance=_cfg())
    by_name = {ctx_args[1]: kw for ctx_args, kw in env.bundle_calls}
    assert by_name["a"]["provenance"] is not None
    assert by_name["c"] == {}, "a resumed document is called exactly as before provenance existed"
    provenance_values = [kw.get("provenance") for _, kw in env.process_calls]
    assert sum(v is not None for v in provenance_values) == 1


def test_the_same_stem_duplicate_gets_no_provenance_and_the_first_keeps_its_identity(env):
    first = env.pdf("dup", directory="d1")
    second = env.pdf("dup", directory="d2", body=b"%PDF-1.4 different")
    env.use_fake_process_pdf()
    env.run([first, second], provenance=_cfg())
    calls = {str(args[0]): kw for args, kw in env.bundle_calls}
    assert calls[str(first)]["provenance"] is not None
    assert calls[str(second)] == {}
    import hashlib

    assert calls[str(first)]["provenance"].identity.pdf_content_hash == hashlib.sha256(first.read_bytes()).hexdigest()


def test_the_artifact_is_written_after_the_extraction_outputs_and_before_the_token_report(env):
    order: list[str] = []
    a = env.pdf("a")
    env.use_fake_process_pdf()
    env.monkeypatch.setattr(env.orch, "build_qc_bundle", env.fake_build_qc_bundle())
    real_finish = rp.finish_run_provenance

    def finish(plan):
        order.append("provenance")
        return real_finish(plan)

    report = MagicMock(side_effect=lambda collector, directory: order.append("token_report") or MagicMock(status="ok", telemetry_records=[]))
    env.monkeypatch.setattr(env.orch, "generate_token_report", report)
    with patch.object(rp, "finish_run_provenance", side_effect=finish):
        asyncio.run(env.orch.run_pipeline([a], pdf_concurrency=1, provenance_config=_cfg()))
    assert order == ["provenance", "token_report"]


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "/abs/prov", "../x", "a/../../b"])
def test_an_invalid_output_subdir_falls_back_to_the_default_with_a_warning(bad, env, caplog):
    env.use_fake_process_pdf()
    with caplog.at_level(logging.WARNING):
        env.run([env.pdf("a")], provenance=_cfg(output_subdir=bad))
    assert env.artifact.is_file()
    assert any("provenance.output_subdir" in m for m in _warnings(caplog))
    assert not (env.tmp_path / "x").exists() and not Path("/abs/prov").exists()


@pytest.mark.parametrize("run_root_spelling", [".", "./", "./.", "outputs", "outputs/"])
def test_a_subdir_naming_the_run_folder_never_puts_the_artifact_in_the_run_root(run_root_spelling, env, caplog):
    env.use_fake_process_pdf()
    with caplog.at_level(logging.WARNING):
        env.run([env.pdf("a")], provenance=_cfg(output_subdir=run_root_spelling))
    assert not (env.run_root / ARTIFACT_FILENAME).exists(), "the artifact must not sit beside the extraction outputs"
    assert env.artifact.is_file(), "it falls back to <run folder>/provenance/"
    assert any("provenance.output_subdir" in m for m in _warnings(caplog))


@pytest.mark.parametrize("spelling", ["./provenance", "provenance/"])
def test_equivalent_spellings_of_the_default_subdir_work(spelling, env, caplog):
    env.use_fake_process_pdf()
    with caplog.at_level(logging.WARNING):
        env.run([env.pdf("a")], provenance=_cfg(output_subdir=spelling))
    assert env.artifact.is_file()
    assert not any("provenance.output_subdir" in m for m in _warnings(caplog))


def test_a_valid_nested_output_subdir_is_used(env):
    env.use_fake_process_pdf()
    env.run([env.pdf("a")], provenance=_cfg(output_subdir="audit/prov"))
    assert (env.run_root / "audit" / "prov" / ARTIFACT_FILENAME).is_file()
    assert not env.artifact.exists()


# ---------------------------------------------------------------------------
# Disabled: the unchanged baseline
# ---------------------------------------------------------------------------


def test_disabled_provenance_calls_the_collaborators_exactly_as_before_and_writes_nothing(env, caplog):
    paths = [env.pdf("a"), env.pdf("b")]
    env.use_fake_process_pdf()
    with patch.object(rp, "plan_run_documents", side_effect=AssertionError("planned while disabled")), patch.object(
        rp, "ProvenanceRecorder", side_effect=AssertionError("recorder built while disabled")
    ), caplog.at_level(logging.INFO):
        results = env.run(paths, provenance=_cfg(enabled=False))
    assert [r["pdf"] for r in results] == ["a.pdf", "b.pdf"]
    # call arguments identical to the pre-provenance call: positional (pdf_path, pdf_name, qc_config), no extras
    assert all(len(args) == 3 and kwargs == {} for args, kwargs in env.bundle_calls)
    assert all(kwargs == {} for _, kwargs in env.process_calls)
    assert list(env.run_root.rglob(ARTIFACT_FILENAME)) == []
    assert not (env.run_root / "provenance").exists()
    noise = [r for r in caplog.records if r.name.endswith("run_provenance")]
    assert len(noise) == 1 and noise[0].levelno == logging.INFO


def test_disabled_and_enabled_runs_return_identical_extraction_results(env):
    paths = [env.pdf("a"), env.pdf("b")]
    env.use_real_process_pdf()
    off = env.run(paths, provenance=_cfg(enabled=False))
    for name in ("a", "b"):
        (env.run_root / f"{name}.extracted.json").unlink()
    env.manifest.clear()
    env.bundle_calls.clear()
    on = env.run(paths, provenance=_cfg())
    assert on == off
    assert env.artifact.is_file()


def test_the_enabled_flag_is_a_real_boolean_a_string_false_does_not_disable(env, caplog):
    env.use_fake_process_pdf()
    with caplog.at_level(logging.WARNING):
        env.run([env.pdf("a")], provenance=_cfg(enabled="false"))
    assert env.artifact.is_file()  # invalid value -> documented default (enabled), with a warning
    assert any("provenance.enabled" in m for m in _warnings(caplog))


def test_a_fully_resumed_run_produces_no_artifact_and_no_warning(env, caplog):
    env.resumed("a")
    env.use_fake_process_pdf()
    with caplog.at_level(logging.WARNING):
        env.run([env.pdf("a")], provenance=_cfg())
    assert list(env.run_root.rglob(ARTIFACT_FILENAME)) == []
    assert _warnings(caplog) == []


# ---------------------------------------------------------------------------
# Provenance errors never touch extraction
# ---------------------------------------------------------------------------


_INJECTIONS = [
    ("plan_run_documents", RuntimeError("plan exploded"), False),
    ("build_run_identity", TypeError("bad created_at"), False),
    ("ProvenanceRecorder", RuntimeError("recorder exploded"), False),
    ("build_graph", RuntimeError("builder exploded"), False),
    ("validate_chain", RuntimeError("validator exploded"), True),
    ("compute_completeness", RuntimeError("completeness exploded"), True),
    ("write_graph", ValueError("non-finite float"), False),
    ("write_graph", OSError(28, "No space left on device"), False),
    ("write_graph", TypeError("not JSON"), False),
    ("finish_run_provenance", RuntimeError("everything exploded"), False),
]


@pytest.mark.parametrize("target, error, artifact_expected", _INJECTIONS, ids=lambda v: v if isinstance(v, str) else None)
def test_an_injected_provenance_error_is_logged_and_extraction_results_are_unchanged(
    target, error, artifact_expected, env, caplog
):
    paths = [env.pdf("a"), env.pdf("b")]
    env.use_real_process_pdf()
    baseline = env.run(paths, provenance=_cfg(enabled=False))
    for name in ("a", "b"):
        (env.run_root / f"{name}.extracted.json").unlink()
    env.manifest.clear()

    # the orchestrator imports run_provenance lazily, so patching the module attribute is seen by it
    with patch.object(rp, target, side_effect=error), caplog.at_level(logging.WARNING):
        results = env.run(paths, provenance=_cfg())
    assert results == baseline
    assert any(str(error) in m for m in _warnings(caplog)), _warnings(caplog)
    assert env.artifact.is_file() is artifact_expected
    assert list(env.run_root.rglob("*.tmp")) == [], "no partial artifact"
    for name in ("a", "b"):
        assert (env.run_root / f"{name}.extracted.json").is_file()


# ---------------------------------------------------------------------------
# Failure hooks in _bounded
# ---------------------------------------------------------------------------


def test_process_pdf_raising_marks_the_claim_stages_failed_and_the_exception_is_unchanged(env, caplog):
    a, b = env.pdf("a"), env.pdf("b")
    boom = RuntimeError("OpenAI is down")
    env.use_fake_process_pdf(raises={"a": boom})
    with caplog.at_level(logging.ERROR):
        results = env.run([a, b], provenance=_cfg())
    # the existing gather(return_exceptions=True) handling: logged as unhandled, other PDF still produced
    assert [r["pdf"] for r in results] == ["b.pdf"]
    assert any("Unhandled error for a.pdf: OpenAI is down" in r.getMessage() for r in caplog.records)

    graph = read_graph(env.artifact)
    sources = {n.source_label: n.node_id for n in graph.nodes.values() if n.kind == "source"}
    completeness = graph.completeness
    assert completeness.documents[sources["a"]].state == "undetermined"
    assert "OpenAI is down" in (completeness.documents[sources["a"]].reason or "")
    assert any(
        s.record_kind == "stage" and s.affected_ids[:1] == (sources["a"],) and "OpenAI is down" in s.reason
        for s in graph.missing_segments
    )
    # the other document's claim stages were not touched by the failure
    assert not any(s.affected_ids[:1] == (sources["b"],) and s.record_kind == "stage" for s in graph.missing_segments)


def test_the_exception_object_reaching_gather_is_the_one_process_pdf_raised(env):
    seen: list[BaseException] = []
    boom = RuntimeError("same object")
    env.use_fake_process_pdf(raises={"a": boom})
    real_debug = env.orch.logger.debug

    def spy(message, *args, **kwargs):
        if "Unhandled exception" in str(message) and isinstance(kwargs.get("exc_info"), BaseException):
            seen.append(kwargs["exc_info"])
        return real_debug(message, *args, **kwargs)

    with patch.object(env.orch.logger, "debug", side_effect=spy):
        env.run([env.pdf("a")], provenance=_cfg())
    assert seen == [boom] and seen[0] is boom


def test_build_qc_bundle_raising_represents_that_document_as_failed_and_leaves_the_other_alone(env):
    a, b = env.pdf("a"), env.pdf("b")
    env.use_real_process_pdf()
    results = env.run([a, b], provenance=_cfg(), fail_bundle={"a"})
    assert [r["pdf"] for r in results] == ["b.pdf"]
    assert env.manifest["a"]["status"] == "failed_qc_pipeline"  # existing behaviour intact

    graph = read_graph(env.artifact)
    sources = {n.source_label: n.node_id for n in graph.nodes.values() if n.kind == "source"}
    assert sorted(sources) == ["a", "b"], "the failed document is still represented"
    states = {name: graph.completeness.documents[sid].state for name, sid in sources.items()}
    assert states["a"] == "undetermined"
    assert "QC pipeline exploded for a" in graph.completeness.documents[sources["a"]].reason
    assert states["b"] in {"complete", "incomplete"}
    assert states["b"] != "undetermined"


# ---------------------------------------------------------------------------
# fail_run_on_severity
# ---------------------------------------------------------------------------


def _inject_critical_finding(env: _Env):
    """Make the first document record one critical finding while its bundle is built."""
    real = env.fake_build_qc_bundle()

    def build(pdf_path, pdf_name, qc_config, *, provenance=None):
        bundle = real(pdf_path, pdf_name, qc_config, provenance=provenance)
        if provenance is not None and pdf_name == "a":
            provenance.recorder.issue_sink()("dangling_reference", "synthetic", {"reference": "S404"})
        return bundle

    env.fake_build_qc_bundle = lambda fail=frozenset(): build  # type: ignore[method-assign]


@pytest.mark.parametrize("threshold, tripped", [(None, False), ("critical", True), ("error", True)])
def test_fail_run_on_severity_logs_an_error_but_the_extraction_output_is_untouched(threshold, tripped, env, caplog):
    _inject_critical_finding(env)
    env.use_real_process_pdf()
    with caplog.at_level(logging.INFO):
        results = env.run([env.pdf("a")], provenance=_cfg(fail_run_on_severity=threshold))
    assert [r["pdf"] for r in results] == ["a.pdf"]
    assert (env.run_root / "a.extracted.json").is_file()
    assert env.artifact.is_file()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and "Provenance failure" in r.getMessage()]
    assert bool(errors) is tripped
    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Provenance summary:")]
    assert len(summary) == 1 and f"provenance_failure={tripped}" in summary[0]


def test_severity_overrides_from_the_configuration_change_the_written_severity(env):
    _inject_critical_finding(env)
    env.use_real_process_pdf()
    env.run([env.pdf("a")], provenance=_cfg(severity_overrides={"dangling_reference": "warning"}))
    graph = read_graph(env.artifact)
    assert {f.severity for f in graph.validation.findings if f.code == "dangling_reference"} == {"warning"}


def test_a_null_severity_overrides_block_is_tolerated(env):
    env.use_fake_process_pdf()
    env.run([env.pdf("a")], provenance=_cfg(severity_overrides=None))
    assert env.artifact.is_file()


def test_the_strict_flag_from_the_configuration_reaches_completeness_as_a_bool(env):
    env.use_fake_process_pdf()
    with patch.object(rp, "compute_completeness", wraps=rp.compute_completeness) as spy:
        env.run([env.pdf("a")], provenance=_cfg(stage_contracts_strict=False))
    assert spy.call_args.kwargs["stage_contracts_strict"] is False


def test_the_summary_line_is_logged_once_with_counts_and_state(env, caplog):
    env.use_real_process_pdf()
    with caplog.at_level(logging.INFO):
        env.run([env.pdf("a"), env.pdf("b")], provenance=_cfg())
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Provenance summary:")]
    assert len(lines) == 1
    assert "source=2" in lines[0] and "claim=10" in lines[0] and "completeness=" in lines[0]
    assert f"artifact={env.artifact}" in lines[0]


def test_the_orchestrator_is_backward_compatible_without_the_new_parameter(env):
    """``provenance_config`` is optional: the shipped YAML block is read when it is omitted."""
    env.use_fake_process_pdf()
    env.run([env.pdf("a")])
    assert env.artifact.is_file()
