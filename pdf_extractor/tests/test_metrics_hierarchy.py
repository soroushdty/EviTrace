"""
Tests for the Metrics Hierarchy wiring in the QC pipeline orchestrator.

Covers Task 5.2 requirements:
  - Req 12.7: Metrics Hierarchy comment block exists in quality_control.py
  - Req 12.7: semantic_qc.enabled=False path never imports faiss/torch/ST
  - Req 12.7: semantic_qc.enabled=True path is scaffolded only (logs, no adjudication change)
"""

from __future__ import annotations

import sys
import importlib
from unittest.mock import patch

import pytest

from evi_trace.extraction.quality_control.quality_control import evi_trace.cli as run_quality_control
from evi_trace.extraction.quality_control import BranchOutput, QCContext, LocalQCMetricRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(semantic_qc_enabled: bool = False) -> dict:
    """Return a minimal config dict with semantic_qc.enabled set as specified."""
    return {
        "quality_control": {
            "artifact_generator": {"export_to_disk": False, "output_dir": "output/qc_artifacts"},
            "rater": {"attributes": []},
            "iaa_calculator": {"thresholds": {}, "agreement_metrics": []},
            "adjudicator": {"strategy": "placeholder"},
            "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
            "semantic_qc": {"enabled": semantic_qc_enabled},
        }
    }


def _make_branches() -> list[BranchOutput]:
    return [
        BranchOutput(extractor="grobid", branch=0, payload="<root/>", status=None),
        BranchOutput(extractor="pymupdf", branch=1, payload={}, status=None),
    ]


# ---------------------------------------------------------------------------
# Test 1: semantic_qc.enabled=False — no faiss/torch/ST imported, pipeline completes
# ---------------------------------------------------------------------------

def test_semantic_qc_disabled_no_heavy_imports():
    """With semantic_qc.enabled=False: pipeline returns QCContext and faiss/torch/ST are not imported."""
    # Ensure the heavy libraries are not present in sys.modules before or after the call.
    heavy_modules = {"faiss", "torch", "sentence_transformers"}

    # Precondition: they should not be imported already (the module itself must not import them).
    for mod in heavy_modules:
        assert mod not in sys.modules, (
            f"Module {mod!r} was unexpectedly already imported before the pipeline ran"
        )

    config = _make_config(semantic_qc_enabled=False)
    branches = _make_branches()
    result = run_quality_control(branches, "test-doc-tier3-disabled", config)

    # Pipeline must return a QCContext.
    assert isinstance(result, QCContext)

    # After the pipeline call, heavy libraries must still not be imported.
    for mod in heavy_modules:
        assert mod not in sys.modules, (
            f"Module {mod!r} was unexpectedly imported during the pipeline run "
            f"when semantic_qc.enabled=False"
        )


# ---------------------------------------------------------------------------
# Test 2: semantic_qc.enabled=True — pipeline completes normally (scaffolded only)
# ---------------------------------------------------------------------------

def test_semantic_qc_enabled_scaffolded_only():
    """With semantic_qc.enabled=True: pipeline completes and returns QCContext without error."""
    config = _make_config(semantic_qc_enabled=True)
    branches = _make_branches()
    result = run_quality_control(branches, "test-doc-tier3-enabled", config)

    # Pipeline must still return a QCContext without raising.
    assert isinstance(result, QCContext)
    # The unified record must be populated (no adjudication change from Tier 3).
    assert result.unified is not None


# ---------------------------------------------------------------------------
# Test 3: Metrics Hierarchy comment block exists in quality_control.py
# ---------------------------------------------------------------------------

def test_metrics_hierarchy_comment_exists():
    """The sentinel phrase 'Embeddings are deliberately NOT used' must appear in quality_control.py."""
    import evi_trace.extraction.quality_control.quality_control as qc_mod
    import inspect

    source = inspect.getsource(qc_mod)
    assert "Embeddings are deliberately NOT used" in source, (
        "Metrics Hierarchy comment block (Req 12.7) is missing from quality_control.py"
    )


def test_metrics_hierarchy_records_tier1_and_scaffolded_tier2_tier3():
    """Tier 1 reports are recorded and borderline branches can reach the Tier 2/3 scaffold."""
    config = _make_config(semantic_qc_enabled=True)
    branches = _make_branches()

    def fake_passes_check(self, pdf=None):  # noqa: ARG001
        self.metric_records = [
            LocalQCMetricRecord(
                metric_name="min_chars_per_page",
                computed_value=1,
                threshold=100,
                triggered=True,
            )
        ]
        self.status = "fail"
        return False

    with patch(
        "evi_trace.extraction.quality_control.quality_control.LocalQCReport.passes_check",
        new=fake_passes_check,
    ), patch(
        "evi_trace.extraction.quality_control.quality_control.exact_match_search",
        return_value=None,
    ) as mock_exact, patch(
        "evi_trace.extraction.quality_control.quality_control.semantic_search",
        return_value=None,
    ) as mock_semantic:
        result = run_quality_control(branches, "tier-hierarchy-doc", config)

    assert isinstance(result, QCContext)
    assert hasattr(result, "metrics_hierarchy")
    assert len(result.metrics_hierarchy["tier1"]) == len(branches)
    assert len(result.metrics_hierarchy["tier2"]) >= 1
    assert len(result.metrics_hierarchy["tier3"]) == 1
    assert mock_exact.called
    assert mock_semantic.called
