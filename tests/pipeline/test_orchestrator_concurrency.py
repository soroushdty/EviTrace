"""Unit tests for pipeline/orchestrator.py — concurrency and error handling.

Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 12.3
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Fake configs used by _import_orchestrator()
# ---------------------------------------------------------------------------

_FAKE_OPENAI_CONFIG = {
    "api_key": "test-key",
    "base_url": None,
    "chunk_model": "gpt-test",
    "synthesis_model": "gpt-test",
    "temperature": None,
    "prompt_cache_key_prefix": "test-prefix",
    "prompt_cache_retention": "",
    "max_retries": 3,
    "retry_base_delay": 0,
    "num_chunks": 3,
    "chunk_max_tokens": {1: 4096, 2: 4096, 3: 4096},
    "enable_cache_prewarm": False,
    "global_api_limit": 5,
    "pdf_concurrency": 1,
    "prewarm_synthesis_if_model_diff": False,
    "domain_to_chunk": {1: 1, 2: 1, 3: 2, 4: 2, 5: 3},
    "max_evidence_items_per_chunk": 250,
    "max_evidence_chars_per_chunk": 60000,
    "evidence_cache_dir": "outputs/evidence_cache",
    "grobid_failure_behavior": "fallback",
}

_FAKE_QC_CONFIG_FALLBACK = {
    "quality_control": {
        "grobid_integration": {
            "enabled": True,
            "failure_behavior": "fallback",
            "crop_figures": False,
            "crop_tables": False,
        },
        "grobid": {
            "url": "http://localhost:8070",
            "timeout": 120,
        },
        "addons": {
            "grobid_quantities": {"enabled": False},
            "datastet": {"enabled": False},
            "entity_fishing": {"enabled": False},
        },
        "semantic_qc": {"enabled": False},
        "local_metrics": {
            "min_chars_per_page": 100,
            "grobid_vs_native_ratio_threshold": 0.6,
            "long_sentence_word_threshold": 120,
            "long_sentence_max_fraction": 0.12,
            "expected_sections": ["abstract", "introduction", "methods", "results"],
            "caption_table_figure_check_enabled": True,
            "coordinate_coverage_threshold": 0.1,
            "references_in_body_threshold": 0.05,
            "weird_char_ratio_threshold": 0.05,
        },
        "scan_detection": {
            "text_density_threshold": 50,
            "alpha_ratio_threshold": 0.60,
            "image_dominance_threshold": 0.85,
        },
        "ocr": {"rasterization_dpi": 150},
        "text_fidelity": {"edit_distance_threshold": 0.10},
        "section_verification": {"font_size_tolerance": 1.0},
        "discard_failed_branches": False,
        "status_field_location": "both",
        "artifact_generator": {"export_to_disk": False, "output_dir": "output/qc_artifacts"},
        "rater": {"attributes": []},
        "iaa_calculator": {"thresholds": {}, "agreement_metrics": []},
        "adjudicator": {"strategy": "placeholder"},
        "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
    },
    "text_processor": {
        "class": "utils.text_processor.TextProcessor",
        "sentence_tokenizer": {"backend": "scispacy", "model": "en_core_sci_sm"},
        "word_tokenizer": {"backend": "simple"},
        "normalizer": {"backend": "nfkc"},
        "comparison": {"metric": "levenshtein", "threshold": 0.85},
        "ocr_cleaning": {"weird_char_threshold": 0.05},
    },
}

_FAKE_QC_CONFIG_MANIFEST_FAIL = {
    **_FAKE_QC_CONFIG_FALLBACK,
    "quality_control": {
        **_FAKE_QC_CONFIG_FALLBACK["quality_control"],
        "grobid_integration": {
            "enabled": True,
            "failure_behavior": "manifest_fail",
            "crop_figures": False,
            "crop_tables": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------

def _import_orchestrator():
    """Import pipeline.orchestrator with patched config loaders.

    Uses importlib.util.spec_from_file_location to load orchestrator.py
    directly, bypassing pipeline/__init__.py (which would trigger the full
    import chain including agents.openai.api_client → openai).

    Both load_openai_config and load_qc_config are patched to return fake
    configs so no real config.yaml is required.  The openai package and
    agents.openai.api_client are stubbed in sys.modules so that pdf_processor
    (imported by orchestrator) can be loaded without the real openai package.
    """
    # Remove any cached orchestrator/pipeline modules to force a fresh import.
    for mod_name in list(sys.modules.keys()):
        if mod_name in (
            "pipeline",
            "pipeline.orchestrator",
            "pipeline.pdf_processor",
            "pipeline.extraction_map",
            "pipeline.manifest",
        ):
            del sys.modules[mod_name]

    # Stub the openai package and api_client so the import chain doesn't fail
    # when openai is not installed in the test environment.
    _openai_stub = MagicMock()
    _api_client_stub = MagicMock()
    _api_client_stub.extract_chunk = MagicMock()
    _api_client_stub.warm_pdf_cache = MagicMock()

    # Stub pipeline sub-modules that orchestrator imports via relative imports.
    # This prevents pipeline/__init__.py from being triggered (which would
    # create a circular import since orchestrator isn't fully loaded yet).
    _pipeline_pkg_stub = MagicMock()
    _pdf_processor_stub = MagicMock()
    _extraction_map_stub = MagicMock()
    _manifest_stub = MagicMock()
    _manifest_stub.load_manifest = MagicMock(return_value={})
    _manifest_stub.save_manifest = MagicMock()

    extra_stubs = {
        "openai": _openai_stub,
        "agents": MagicMock(),
        "agents.openai": MagicMock(),
        "agents.openai.api_client": _api_client_stub,
        "pipeline": _pipeline_pkg_stub,
        "pipeline.pdf_processor": _pdf_processor_stub,
        "pipeline.extraction_map": _extraction_map_stub,
        "pipeline.manifest": _manifest_stub,
    }

    _orch_path = Path(__file__).resolve().parents[2] / "pipeline" / "orchestrator.py"
    _spec = importlib.util.spec_from_file_location("pipeline.orchestrator", _orch_path)
    assert _spec is not None and _spec.loader is not None

    with patch.dict(sys.modules, extra_stubs), \
         patch("utils.config_utils.load_openai_config", return_value=_FAKE_OPENAI_CONFIG), \
         patch("utils.config_utils.load_qc_config", return_value=_FAKE_QC_CONFIG_FALLBACK):
        m = importlib.util.module_from_spec(_spec)
        sys.modules["pipeline.orchestrator"] = m
        _spec.loader.exec_module(m)

    return m


# ---------------------------------------------------------------------------
# GROBID fallback tests
# ---------------------------------------------------------------------------

def test_build_qc_context_grobid_fallback():
    """11.1 — When extract_with_grobid raises and failure_behavior='fallback',
    _build_qc_context SHALL not re-raise and the GROBID branch SHALL have an
    empty string payload.

    Requirements: 11.1, 12.3
    """
    orch = _import_orchestrator()

    # Build a minimal QCContext mock that run_quality_control will return.
    from quality_control.models import BranchOutput, QCContext, UnifiedRecord
    mock_ctx = QCContext(
        branches=[
            BranchOutput(extractor="grobid",  branch=0, payload="",  status=None),
            BranchOutput(extractor="pymupdf", branch=1, payload=[], status=None),
        ],
        unified=UnifiedRecord(document_id="test_paper", content={}),
    )

    with patch.object(orch, "extract_with_grobid", side_effect=RuntimeError("GROBID down")), \
         patch.object(orch, "extract_with_pymupdf", return_value=([], {})), \
         patch.object(orch, "run_quality_control", return_value=mock_ctx):

        # Should NOT raise — failure_behavior is "fallback" in _FAKE_QC_CONFIG_FALLBACK
        result = orch._build_qc_context(
            pdf_path=Path("test_paper.pdf"),
            pdf_name="test_paper",
            qc_config=_FAKE_QC_CONFIG_FALLBACK,
        )

    # The GROBID branch in the context should have an empty payload (tei_xml="")
    grobid_branch = next(
        (b for b in result.branches if b.extractor == "grobid"),
        None,
    )
    assert grobid_branch is not None, "GROBID branch should be present in context"
    assert grobid_branch.payload == "", (
        f"GROBID branch payload should be empty string on fallback, got {grobid_branch.payload!r}"
    )


def test_build_qc_context_grobid_manifest_fail():
    """11.2 — When extract_with_grobid raises and failure_behavior='manifest_fail',
    _build_qc_context SHALL re-raise the exception.

    Requirements: 11.2, 12.3
    """
    orch = _import_orchestrator()

    with patch.object(orch, "extract_with_grobid", side_effect=RuntimeError("GROBID down")), \
         patch.object(orch, "extract_with_pymupdf", return_value=([], {})), \
         patch.object(orch, "run_quality_control", return_value=MagicMock()):

        # Should re-raise — failure_behavior is "manifest_fail"
        try:
            orch._build_qc_context(
                pdf_path=Path("test_paper.pdf"),
                pdf_name="test_paper",
                qc_config=_FAKE_QC_CONFIG_MANIFEST_FAIL,
            )
            assert False, "_build_qc_context should have raised RuntimeError"
        except RuntimeError as exc:
            assert "GROBID down" in str(exc), (
                f"Expected 'GROBID down' in exception message, got: {exc}"
            )


# ---------------------------------------------------------------------------
# run_pipeline result collection and error-handling tests
# ---------------------------------------------------------------------------

def test_run_pipeline_all_succeed():
    """11.3 — run_pipeline SHALL collect results for all PDFs when every
    _build_qc_context and process_pdf call succeeds.

    Requirements: 11.3
    """
    import asyncio
    from unittest.mock import AsyncMock

    orch = _import_orchestrator()

    pdf_paths = [Path("paper_a.pdf"), Path("paper_b.pdf"), Path("paper_c.pdf")]

    # Minimal QCContext mock returned by _build_qc_context
    mock_qc_ctx = MagicMock()

    # process_pdf returns a non-empty fields list for each PDF
    fake_fields = [{"field_index": 1, "extracted_value": "Smith 2020"}]

    with patch.object(orch, "_build_qc_context", return_value=mock_qc_ctx), \
         patch.object(
             orch.pdf_processor, "process_pdf",
             new=AsyncMock(return_value=fake_fields),
         ):
        results = asyncio.run(orch.run_pipeline(pdf_paths, pdf_concurrency=3))

    assert len(results) == len(pdf_paths), (
        f"Expected {len(pdf_paths)} results, got {len(results)}"
    )


def test_run_pipeline_one_qc_failure():
    """11.4 — When _build_qc_context raises for one PDF, run_pipeline SHALL
    record status 'failed_qc_pipeline' in the manifest for that PDF and still
    process the remaining PDFs normally.

    Requirements: 11.4
    """
    import asyncio
    from unittest.mock import AsyncMock

    orch = _import_orchestrator()

    pdf_paths = [Path("good_a.pdf"), Path("bad_b.pdf"), Path("good_c.pdf")]
    failing_stem = "bad_b"

    mock_qc_ctx = MagicMock()
    fake_fields = [{"field_index": 1, "extracted_value": "Jones 2021"}]

    # Capture the manifest dict that orchestrator uses internally so we can
    # inspect it after the run.  The manifest stub's load_manifest returns {}
    # by default; we replace it with a real dict and capture save_manifest calls.
    captured_manifest: dict = {}

    def fake_build_qc_context(pdf_path, pdf_name, qc_config):
        if pdf_name == failing_stem:
            raise RuntimeError("QC pipeline exploded")
        return mock_qc_ctx

    def fake_save_manifest(manifest):
        captured_manifest.update(manifest)

    # Patch load_manifest to return a shared mutable dict so the orchestrator's
    # internal manifest variable starts empty and we can observe mutations.
    shared_manifest: dict = {}

    with patch.object(orch, "_build_qc_context", side_effect=fake_build_qc_context), \
         patch.object(
             orch.pdf_processor, "process_pdf",
             new=AsyncMock(return_value=fake_fields),
         ), \
         patch.object(orch, "load_manifest", return_value=shared_manifest), \
         patch.object(orch, "save_manifest", side_effect=fake_save_manifest):

        results = asyncio.run(orch.run_pipeline(pdf_paths, pdf_concurrency=3))

    # The failing PDF should be recorded in the manifest with failed_qc_pipeline
    assert failing_stem in captured_manifest, (
        f"Expected '{failing_stem}' in manifest, got keys: {list(captured_manifest.keys())}"
    )
    assert captured_manifest[failing_stem]["status"] == "failed_qc_pipeline", (
        f"Expected status 'failed_qc_pipeline', got: {captured_manifest[failing_stem]}"
    )

    # The two good PDFs should appear in the results list
    result_pdfs = {r["pdf"] for r in results}
    assert "good_a.pdf" in result_pdfs, f"good_a.pdf missing from results: {result_pdfs}"
    assert "good_c.pdf" in result_pdfs, f"good_c.pdf missing from results: {result_pdfs}"
    assert len(results) == 2, f"Expected 2 successful results, got {len(results)}"


# ---------------------------------------------------------------------------
# Concurrency and config-propagation tests (task 12.3)
# ---------------------------------------------------------------------------

def test_run_pipeline_concurrency_1():
    """11.5 — When pdf_concurrency=1, at most 1 _build_qc_context call SHALL
    run concurrently at any point during run_pipeline execution.

    Requirements: 11.5
    """
    import asyncio
    import threading
    from unittest.mock import AsyncMock

    orch = _import_orchestrator()

    pdf_paths = [
        Path("paper_a.pdf"),
        Path("paper_b.pdf"),
        Path("paper_c.pdf"),
        Path("paper_d.pdf"),
    ]

    # Thread-safe concurrency tracking using a list (avoids closure rebinding).
    counter = [0]          # counter[0] = current concurrent calls
    max_seen = [0]         # max_seen[0] = peak concurrent calls observed
    lock = threading.Lock()

    mock_qc_ctx = MagicMock()
    fake_fields = [{"field_index": 1, "extracted_value": "Smith 2020"}]

    def fake_build_qc_context(pdf_path, pdf_name, qc_config):
        with lock:
            counter[0] += 1
            if counter[0] > max_seen[0]:
                max_seen[0] = counter[0]
        # Yield briefly so other threads have a chance to enter if the semaphore
        # were not limiting them.
        import time
        time.sleep(0.01)
        with lock:
            counter[0] -= 1
        return mock_qc_ctx

    with patch.object(orch, "_build_qc_context", side_effect=fake_build_qc_context), \
         patch.object(
             orch.pdf_processor, "process_pdf",
             new=AsyncMock(return_value=fake_fields),
         ):
        results = asyncio.run(orch.run_pipeline(pdf_paths, pdf_concurrency=1))

    assert len(results) == len(pdf_paths), (
        f"Expected {len(pdf_paths)} results, got {len(results)}"
    )
    assert max_seen[0] <= 1, (
        f"Expected max concurrent _build_qc_context calls == 1, got {max_seen[0]}"
    )


def test_run_pipeline_cache_prewarm_false_propagated():
    """11.6 — When run_pipeline is called with enable_cache_prewarm=False, the
    runtime_config dict passed to process_pdf SHALL have
    runtime_config["enable_cache_prewarm"] is False.

    Requirements: 11.6
    """
    import asyncio
    from unittest.mock import AsyncMock

    orch = _import_orchestrator()

    pdf_paths = [Path("paper_x.pdf")]

    mock_qc_ctx = MagicMock()
    fake_fields = [{"field_index": 1, "extracted_value": "Jones 2021"}]

    # Capture the runtime_config argument passed to process_pdf.
    captured_runtime_configs: list = []

    async def fake_process_pdf(qc_context, chunk_fields, field_lookup,
                                api_semaphore, manifest, manifest_lock,
                                runtime_config):
        captured_runtime_configs.append(runtime_config)
        return fake_fields

    with patch.object(orch, "_build_qc_context", return_value=mock_qc_ctx), \
         patch.object(orch.pdf_processor, "process_pdf", side_effect=fake_process_pdf):
        asyncio.run(orch.run_pipeline(pdf_paths, enable_cache_prewarm=False))

    assert len(captured_runtime_configs) == 1, (
        f"Expected process_pdf to be called once, got {len(captured_runtime_configs)} calls"
    )
    runtime_config = captured_runtime_configs[0]
    assert "enable_cache_prewarm" in runtime_config, (
        "runtime_config should contain 'enable_cache_prewarm' key"
    )
    assert runtime_config["enable_cache_prewarm"] is False, (
        f"Expected runtime_config['enable_cache_prewarm'] is False, "
        f"got {runtime_config['enable_cache_prewarm']!r}"
    )
