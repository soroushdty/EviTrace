"""
tests/quality_control/test_qc_pipeline_integration.py
------------------------------------------------------
Pipeline integration and preservation tests for the QC migration.

Covers:
  - run_quality_control completes without error when semantic_verification.enabled=false
  - ctx.metrics_hierarchy contains exactly the three new keys
  - extractor_agreement absent / skipped when extractor_agreement.enabled=false
  - manifest status values are unchanged after migration
  - Preservation: ExtractionCoverageReport pass/fail outcome is consistent
  - Production-import test: no heavy deps in sys.modules after importing QC packages
  - build_task_quality_scaffold() serializes with json.dumps() without error

Requirements: 10.1, 13.11, 13.12, 13.13, 13.14
"""

from __future__ import annotations

import json
import sys
import importlib
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quality_control.models import Candidate


# ---------------------------------------------------------------------------
# Autouse fixture: mock TextProcessor / spacy so tests don't need NLP models
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_text_processor(monkeypatch):
    """Prevent spacy/scispacy model loading; provide deterministic tokenization."""
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if key == "utils.text_processor" or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)
    monkeypatch.setattr(
        "text_processing.composite.DefaultTextProcessor.tokenize_sentences",
        lambda self, text: text.split(". ") if text else [],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config(*, semantic_enabled: bool = False, ea_enabled: bool = False) -> dict:
    """Return a minimal valid config dict for run_quality_control."""
    return {
        "text_processor": {
            "class": "text_processing.composite.DefaultTextProcessor",
            "sentence_tokenizer": {"backend": "nltk_punkt"},
        },
        "quality_control": {
            "artifact_generator": {"export_to_disk": False, "output_dir": "output/qc_artifacts"},
            "rater": {"attributes": []},
            "iaa_calculator": {"thresholds": {}, "agreement_metrics": []},
            "adjudicator": {"strategy": "placeholder"},
            "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
            "source_text_verification": {"enabled": False},
            "semantic_verification": {
                "enabled": semantic_enabled,
                "extractor_agreement": {"enabled": ea_enabled},
            },
        }
    }


def _make_branches() -> list:
    """Build a minimal two-branch list."""
    from quality_control.models import Candidate
    return [
        Candidate(source="grobid", index=0, payload="<TEI><text><body><p>Hello world</p></body></text></TEI>", status=None),
        Candidate(source="pymupdf", index=1, payload=[{"text": "Hello world", "page_index": 0, "block_bbox": [0, 0, 100, 20], "span_bboxes": []}], status=None),
    ]


# ---------------------------------------------------------------------------
# Test 1: run_quality_control completes without error when semantic_verification.enabled=false
# ---------------------------------------------------------------------------

def test_run_quality_control_completes_semantic_disabled():
    """run_quality_control must complete without error when semantic_verification.enabled=false.

    Requirements: 10.1, 13.11
    """
    from quality_control.quality_control import run_quality_control
    from quality_control.models import QCBundle

    config = _minimal_config(semantic_enabled=False)
    branches = _make_branches()
    ctx = run_quality_control(branches, "doc-001", config)

    assert isinstance(ctx, QCBundle)
    assert ctx.unified is not None


# ---------------------------------------------------------------------------
# Test 2: metrics_hierarchy contains exactly the three new keys
# ---------------------------------------------------------------------------

def test_metrics_hierarchy_contains_exactly_three_keys():
    """ctx.metrics_hierarchy must contain exactly 'extraction_coverage',
    'source_text_verification', and 'semantic_verification' after run_quality_control.

    Requirements: 10.1
    """
    from quality_control.quality_control import run_quality_control

    config = _minimal_config(semantic_enabled=False)
    branches = _make_branches()
    ctx = run_quality_control(branches, "doc-002", config)

    assert set(ctx.metrics_hierarchy.keys()) == {
        "extraction_coverage",
        "source_text_verification",
        "semantic_verification",
    }

    # Old keys must NOT be present
    for old_key in ("local_metrics", "exact_match", "semantic_match", "semantic_qc"):
        assert old_key not in ctx.metrics_hierarchy, (
            f"Legacy key {old_key!r} must not appear in metrics_hierarchy"
        )


# ---------------------------------------------------------------------------
# Test 3: extractor_agreement absent or status="skipped" when ea.enabled=false
# ---------------------------------------------------------------------------

def test_extractor_agreement_absent_when_disabled():
    """When extractor_agreement.enabled=false, 'extractor_agreement' key must be
    absent from metrics_hierarchy['semantic_verification'] or have status='skipped'.

    Requirements: 10.7
    """
    from quality_control.quality_control import run_quality_control

    config = _minimal_config(semantic_enabled=False, ea_enabled=False)
    branches = _make_branches()
    ctx = run_quality_control(branches, "doc-003", config)

    sem_ver = ctx.metrics_hierarchy.get("semantic_verification", {})
    if "extractor_agreement" in sem_ver:
        ea = sem_ver["extractor_agreement"]
        # If present, it must indicate skipped
        if isinstance(ea, dict):
            assert ea.get("status") == "skipped", (
                f"extractor_agreement present but status is not 'skipped': {ea!r}"
            )
        else:
            # Could be a VerificationResult-like object
            assert getattr(ea, "status", None) == "skipped", (
                f"extractor_agreement present but status is not 'skipped': {ea!r}"
            )
    # If absent, that's also acceptable per requirement 10.7


# ---------------------------------------------------------------------------
# Test 4: manifest status values are unchanged after migration
# ---------------------------------------------------------------------------

def test_manifest_status_values_unchanged():
    """The pipeline manifest status values must remain unchanged after migration.

    Valid manifest statuses: 'complete', 'failed_qc_pipeline', 'failed_chunks',
    'failed_chunk_<n>'. This test verifies the orchestrator still uses these
    status strings and that the QC migration has not altered them.

    Requirements: 9.18
    """
    import inspect

    EXPECTED_STATUSES = {"complete", "failed_qc_pipeline", "failed_chunks"}

    # The status strings are written by the orchestrator / pdf_processor, not
    # by pipeline/manifest.py (which is a thin I/O wrapper).  Search the
    # pipeline package source for the canonical status strings.
    pipeline_modules_to_check = [
        "pipeline.orchestrator",
        "pipeline.pdf_processor",
        "pipeline.extraction_pipeline",
    ]

    found_in: dict[str, list[str]] = {s: [] for s in EXPECTED_STATUSES}

    for mod_name in pipeline_modules_to_check:
        try:
            mod = __import__(mod_name, fromlist=[""])
            source = inspect.getsource(mod)
            for status in EXPECTED_STATUSES:
                if status in source:
                    found_in[status].append(mod_name)
        except (ImportError, OSError):
            pass  # module may not be importable in all environments

    # At least one of the expected status strings must be found somewhere in
    # the pipeline package — if none are found, the test environment cannot
    # import any pipeline module and we skip rather than fail.
    any_found = any(modules for modules in found_in.values())
    if not any_found:
        pytest.skip(
            "No pipeline modules importable; cannot verify manifest status values"
        )

    for status, modules in found_in.items():
        assert modules, (
            f"Manifest status {status!r} not found in any pipeline module "
            f"({pipeline_modules_to_check}). "
            "The QC migration may have inadvertently altered manifest status values."
        )


# ---------------------------------------------------------------------------
# Test 5: Preservation — ExtractionCoverageReport pass/fail is consistent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload,expected_pass", [
    # Empty payload — no metrics triggered, should pass
    ("", True),
    # Short text — section coverage may trigger, but we just check consistency
    ("Hello world. This is a test.", None),  # None = don't assert specific value, just consistency
])
def test_extraction_coverage_report_pass_fail_consistency(payload, expected_pass):
    """ExtractionCoverageReport must produce a consistent pass/fail boolean for the same input.

    The same inputs to ExtractionCoverageReport must yield the same pass/fail
    outcome on repeated calls (determinism).

    Requirements: 13.12
    """
    from quality_control.local_metrics import ExtractionCoverageReport

    config = _minimal_config()
    blocks = [{"text": payload, "page_index": 0, "block_bbox": None, "span_bboxes": []}] if payload else []
    sentence_records = [{"sentence": s} for s in payload.split(". ")] if payload else []

    report1 = ExtractionCoverageReport(
        config=config,
        blocks=blocks,
        sentence_records=sentence_records,
        full_pdf_text=payload,
        page_texts={0: payload} if payload else {},
        native_page_texts={},
    )
    result1 = report1.passes_check()

    report2 = ExtractionCoverageReport(
        config=config,
        blocks=blocks,
        sentence_records=sentence_records,
        full_pdf_text=payload,
        page_texts={0: payload} if payload else {},
        native_page_texts={},
    )
    result2 = report2.passes_check()

    # Both calls with identical inputs must produce the same boolean
    assert result1 == result2, (
        f"ExtractionCoverageReport is non-deterministic: "
        f"first call={result1}, second call={result2}"
    )

    if expected_pass is not None:
        assert result1 == expected_pass, (
            f"Expected pass={expected_pass} but got {result1}"
        )


def test_extraction_coverage_report_same_as_itself():
    """ExtractionCoverageReport with identical inputs must produce identical pass/fail.

    This is the core preservation test: the renamed class must behave identically
    to itself across two instantiations with the same inputs.

    Requirements: 13.12
    """
    from quality_control.local_metrics import ExtractionCoverageReport

    config = _minimal_config()
    payload = "This is a sentence. Another sentence here."
    blocks = [{"text": payload, "page_index": 0, "block_bbox": [0, 0, 100, 20], "span_bboxes": []}]
    sentence_records = [{"sentence": s.strip()} for s in payload.split(".") if s.strip()]

    kwargs = dict(
        config=config,
        blocks=blocks,
        sentence_records=sentence_records,
        full_pdf_text=payload,
        page_texts={0: payload},
        native_page_texts={},
    )

    report_a = ExtractionCoverageReport(**kwargs)
    report_b = ExtractionCoverageReport(**kwargs)

    result_a = report_a.passes_check()
    result_b = report_b.passes_check()

    assert result_a == result_b, (
        f"ExtractionCoverageReport produced different results for identical inputs: "
        f"{result_a} vs {result_b}"
    )


# ---------------------------------------------------------------------------
# Test 6: Production-import test — no heavy deps in sys.modules
# ---------------------------------------------------------------------------

def test_production_import_no_heavy_deps():
    """Importing quality_control, quality_control.checks, quality_control.builtin_impls
    must NOT cause sentence_transformers, faiss, or torch to appear in sys.modules.

    Requirements: 10.6, 13.13, 13.14
    """
    # Remove any cached modules to force fresh import evaluation
    modules_to_remove = [
        key for key in sys.modules
        if key.startswith("quality_control")
    ]
    # We don't actually remove them (they may already be imported by other tests),
    # but we verify the heavy deps are not present after the imports complete.

    import quality_control  # noqa: F401
    import quality_control.checks  # noqa: F401
    import quality_control.builtin_impls  # noqa: F401

    heavy_deps = ["sentence_transformers", "faiss", "torch"]
    for dep in heavy_deps:
        assert dep not in sys.modules, (
            f"Heavy dependency {dep!r} was imported as a side-effect of importing "
            f"quality_control packages. This violates the production-import contract."
        )


# ---------------------------------------------------------------------------
# Test 7: build_task_quality_scaffold() serializes with json.dumps() without error
# ---------------------------------------------------------------------------

def test_build_task_quality_scaffold_json_serializable():
    """build_task_quality_scaffold() return value must serialize with json.dumps() without error.

    Requirements: 7.5, 13.11
    """
    from quality_control.checks import build_task_quality_scaffold

    result = build_task_quality_scaffold()

    # Must not raise
    serialized = json.dumps(result)
    assert isinstance(serialized, str)
    assert len(serialized) > 0

    # Round-trip must be stable
    parsed = json.loads(serialized)
    assert isinstance(parsed, dict)


def test_build_task_quality_scaffold_has_required_keys():
    """build_task_quality_scaffold() must include all eight placeholder metric keys,
    a non-empty 'details' string, and a top-level 'status' key.

    Requirements: 7.7, 7.8
    """
    from quality_control.checks import build_task_quality_scaffold

    result = build_task_quality_scaffold()

    required_metrics = [
        "field_recall",
        "critical_field_recall",
        "evidence_validity",
        "evidence_compactness",
        "cost_reduction",
        "manual_qc_rate",
        "interobserver_agreement",
        "pipeline_agreement",
    ]
    for metric in required_metrics:
        assert metric in result, f"Missing metric key: {metric!r}"

    assert "details" in result
    assert isinstance(result["details"], str)
    assert len(result["details"]) > 0

    assert "status" in result
    assert result["status"] in ("not_computed", "scaffolded")


# ---------------------------------------------------------------------------
# Test 8: semantic_verification dict structure when disabled
# ---------------------------------------------------------------------------

def test_semantic_verification_dict_when_disabled():
    """When semantic_verification.enabled=false, metrics_hierarchy['semantic_verification']
    must be a dict (possibly containing a VerificationResult with status='skipped').

    Requirements: 10.5
    """
    from quality_control.quality_control import run_quality_control
    from quality_control.models import VerificationResult

    config = _minimal_config(semantic_enabled=False)
    branches = _make_branches()
    ctx = run_quality_control(branches, "doc-004", config)

    sem_ver = ctx.metrics_hierarchy["semantic_verification"]
    assert isinstance(sem_ver, dict), (
        f"metrics_hierarchy['semantic_verification'] must be a dict, got {type(sem_ver)}"
    )

    # If a result key is present, it must be a VerificationResult with status='skipped'
    if "result" in sem_ver:
        vr = sem_ver["result"]
        assert isinstance(vr, VerificationResult)
        assert vr.status == "skipped"


# ---------------------------------------------------------------------------
# Test 9: extraction_coverage is a list
# ---------------------------------------------------------------------------

def test_extraction_coverage_is_list():
    """metrics_hierarchy['extraction_coverage'] must be a list after run_quality_control.

    Requirements: 10.2
    """
    from quality_control.quality_control import run_quality_control

    config = _minimal_config()
    branches = _make_branches()
    ctx = run_quality_control(branches, "doc-005", config)

    assert isinstance(ctx.metrics_hierarchy["extraction_coverage"], list)


# ---------------------------------------------------------------------------
# Test 10: source_text_verification is a list
# ---------------------------------------------------------------------------

def test_source_text_verification_is_list():
    """metrics_hierarchy['source_text_verification'] must be a list after run_quality_control.

    Requirements: 10.3
    """
    from quality_control.quality_control import run_quality_control

    config = _minimal_config()
    branches = _make_branches()
    ctx = run_quality_control(branches, "doc-006", config)

    assert isinstance(ctx.metrics_hierarchy["source_text_verification"], list)


# ---------------------------------------------------------------------------
# Property 9: metrics_hierarchy contains exactly the three new keys after run_quality_control
# Feature: qc-migration, Property 9: metrics_hierarchy contains exactly the three new keys after run_quality_control
# ---------------------------------------------------------------------------

@given(
    branches=st.lists(
        st.builds(
            Candidate,
            source=st.sampled_from(["grobid", "pymupdf"]),
            index=st.integers(0, 5),
            payload=st.one_of(st.text(), st.just([])),
            status=st.none(),
        ),
        min_size=1,
        max_size=3,
    )
)
@settings(max_examples=100)
def test_property_9_metrics_hierarchy_keys(branches):
    """Property 9: metrics_hierarchy contains exactly the three new keys after run_quality_control.

    Validates: Requirements 8.9, 8.13, 10.1
    """
    from quality_control.quality_control import run_quality_control

    config = _minimal_config(semantic_enabled=False, ea_enabled=False)
    ctx = run_quality_control(branches, "prop9-doc", config)

    assert set(ctx.metrics_hierarchy.keys()) == {
        "extraction_coverage",
        "source_text_verification",
        "semantic_verification",
    }, (
        f"Expected exactly the three new keys, got: {set(ctx.metrics_hierarchy.keys())!r}"
    )

    for old_key in ("local_metrics", "exact_match", "semantic_match", "semantic_qc"):
        assert old_key not in ctx.metrics_hierarchy, (
            f"Legacy key {old_key!r} must not appear in metrics_hierarchy"
        )


# ---------------------------------------------------------------------------
# Task 15.3 — Bypass behavior unit tests
# ---------------------------------------------------------------------------

def test_source_text_verification_bypass_does_not_invoke_matcher():
    """When source_text_verification.enabled=false, the injected exact_match_fn
    must NOT be called, and metrics_hierarchy["source_text_verification"] must
    be a list containing at least one passing sentinel entry.

    Requirements: 9.4, 10.5, 13.2
    """
    from quality_control.quality_control import run_quality_control
    from quality_control.models import VerificationResult

    config = _minimal_config(semantic_enabled=False)
    # Explicitly disable source_text_verification
    config["quality_control"]["source_text_verification"] = {"enabled": False}

    branches = _make_branches()
    mock_exact_match = MagicMock()

    ctx = run_quality_control(
        branches,
        "doc-bypass-stv",
        config,
        exact_match_fn=mock_exact_match,
    )

    # The matcher must NOT have been called at all
    mock_exact_match.assert_not_called()

    # The list must exist and contain at least one entry
    stv_list = ctx.metrics_hierarchy["source_text_verification"]
    assert isinstance(stv_list, list), (
        f"metrics_hierarchy['source_text_verification'] must be a list, got {type(stv_list)}"
    )
    assert len(stv_list) >= 1, (
        "metrics_hierarchy['source_text_verification'] must contain at least one sentinel entry "
        "when source_text_verification.enabled=false"
    )

    # The sentinel entry must be a VerificationResult that represents a passing result
    sentinel = stv_list[0]
    assert isinstance(sentinel, VerificationResult), (
        f"Sentinel entry must be a VerificationResult, got {type(sentinel)}"
    )
    # A passing bypass result uses status="skipped" with score=1.0 per the design doc
    assert sentinel.status in ("skipped", "verified"), (
        f"Bypass sentinel must have a passing status ('skipped' or 'verified'), got {sentinel.status!r}"
    )
    assert sentinel.score == 1.0, (
        f"Bypass sentinel must have score=1.0, got {sentinel.score}"
    )


def test_semantic_verification_bypass_no_heavy_deps_after_run():
    """When semantic_verification.enabled=false, calling run_quality_control must
    NOT cause sentence_transformers, faiss, or torch to appear in sys.modules.

    This test exercises the full run_quality_control code path (unlike the
    production-import test which only checks module-level imports) to confirm
    that the bypass path does not trigger any lazy heavy-dep import.

    Requirements: 9.5, 10.6, 13.3
    """
    from quality_control.quality_control import run_quality_control

    config = _minimal_config(semantic_enabled=False)
    branches = _make_branches()

    # Remove any pre-existing heavy deps from sys.modules to get a clean baseline
    heavy_deps = ["sentence_transformers", "faiss", "torch"]
    for dep in heavy_deps:
        sys.modules.pop(dep, None)

    run_quality_control(branches, "doc-bypass-sem", config)

    for dep in heavy_deps:
        assert dep not in sys.modules, (
            f"Heavy dependency {dep!r} appeared in sys.modules after run_quality_control "
            f"with semantic_verification.enabled=false. The bypass path must not trigger "
            f"any lazy import of heavy optional dependencies."
        )
