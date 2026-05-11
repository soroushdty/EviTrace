"""
Bug Condition Exploration Test — Migration Artifact Scrub

**Property 1: Bug Condition** — Migration Artifacts Present in Unfixed Code

CRITICAL: This test MUST FAIL on unfixed code — failure confirms the bug exists.
DO NOT attempt to fix the test or the code when it fails.

NOTE: This test encodes the expected post-scrub state — it will validate the
fix when it passes after implementation.

Each sub-check calls pytest.fail() when the artifact IS present (i.e., the
test fails when the bug condition is true — meaning the artifact still exists).

Validates: REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8, REQ-9,
           REQ-10, REQ-12, REQ-13
"""

from __future__ import annotations

import inspect
import os

import pytest


# ---------------------------------------------------------------------------
# Category A — Dead code in quality_control.quality_control
# ---------------------------------------------------------------------------


def test_no_run_legacy_pipeline():
    """Category A: _run_legacy_pipeline must not exist in quality_control.quality_control."""
    import quality_control.quality_control as qc_module

    if hasattr(qc_module, "_run_legacy_pipeline"):
        pytest.fail(
            "ARTIFACT PRESENT (Category A dead code): "
            "quality_control.quality_control._run_legacy_pipeline still exists. "
            "This function is never called by any live code path and must be deleted (REQ-5)."
        )


def test_no_run_legacy_annotation_path():
    """Category A: _run_legacy_annotation_path must not exist in quality_control.quality_control."""
    import quality_control.quality_control as qc_module

    if hasattr(qc_module, "_run_legacy_annotation_path"):
        pytest.fail(
            "ARTIFACT PRESENT (Category A dead code): "
            "quality_control.quality_control._run_legacy_annotation_path still exists. "
            "This dead inner function is marked '# pragma: no cover' and must be deleted (REQ-5)."
        )


def test_no_derive_document_id():
    """Category A: _derive_document_id must not exist in quality_control.quality_control."""
    import quality_control.quality_control as qc_module

    if hasattr(qc_module, "_derive_document_id"):
        pytest.fail(
            "ARTIFACT PRESENT (Category A dead code): "
            "quality_control.quality_control._derive_document_id still exists. "
            "This helper is only used by _run_legacy_pipeline and must be deleted (REQ-5)."
        )


# ---------------------------------------------------------------------------
# Category B — Hardcoded extractor names in QC modules
# ---------------------------------------------------------------------------


def test_iaa_calculator_no_grobid_observation_param():
    """Category B: iaa_calculator.investigate must not have 'grobid_observation' parameter."""
    from quality_control import iaa_calculator

    source = inspect.getsource(iaa_calculator.investigate)
    if "grobid_observation" in source:
        pytest.fail(
            "ARTIFACT PRESENT (Category B hardcoded extractor name): "
            "iaa_calculator.investigate() source contains 'grobid_observation'. "
            "The parameter must be renamed to 'primary_observation' (REQ-3). "
            f"Counterexample: found 'grobid_observation' in iaa_calculator.investigate source."
        )


def test_rater_no_extractor_name_param():
    """Category B: rater.observe must not have 'extractor_name' as a parameter."""
    from quality_control import rater

    source = inspect.getsource(rater.observe)
    if "extractor_name" in source:
        pytest.fail(
            "ARTIFACT PRESENT (Category B hardcoded extractor name): "
            "rater.observe() source contains 'extractor_name' as a parameter. "
            "The signature must be changed to observe(branch: Candidate, config: dict) (REQ-4). "
            f"Counterexample: found 'extractor_name' in rater.observe source."
        )


# ---------------------------------------------------------------------------
# Category C — Backward-compat shims
# ---------------------------------------------------------------------------


def test_reconciler_no_placeholder_notice():
    """Category C: reconciler.PLACEHOLDER_NOTICE must not exist."""
    from quality_control import reconciler

    if hasattr(reconciler, "PLACEHOLDER_NOTICE"):
        pytest.fail(
            "ARTIFACT PRESENT (Category C backward-compat shim): "
            "quality_control.reconciler.PLACEHOLDER_NOTICE still exists. "
            "This constant claims reconciliation is not yet implemented, which is false. "
            "It must be deleted along with the placeholder path (REQ-6). "
            f"Counterexample: PLACEHOLDER_NOTICE = {reconciler.PLACEHOLDER_NOTICE!r}"
        )


def test_config_utils_no_load_config_alias():
    """Category C: config_utils.load_config backward-compat alias must not exist."""
    from utils import config_utils

    if hasattr(config_utils, "load_config"):
        pytest.fail(
            "ARTIFACT PRESENT (Category C backward-compat alias): "
            "utils.config_utils.load_config still exists. "
            "This alias (load_config = load_local_config) must be removed; "
            "all callers must use load_local_config directly (REQ-9)."
        )


def test_config_utils_no_ocr_text_quality_threshold_in_defaults():
    """Category C: ocr_text_quality_threshold must not be in config_utils._LOCAL_DEFAULTS."""
    from utils import config_utils

    if "ocr_text_quality_threshold" in config_utils._LOCAL_DEFAULTS:
        pytest.fail(
            "ARTIFACT PRESENT (Category C cascade-scoring param): "
            "'ocr_text_quality_threshold' is still present in config_utils._LOCAL_DEFAULTS. "
            "This is a cascade-scoring parameter from the old waterfall architecture. "
            "Routing is now binary (native vs. scanned) via scan_detector.classify_page(). "
            "It must be removed from _LOCAL_DEFAULTS and load_local_config (REQ-2). "
            f"Counterexample: _LOCAL_DEFAULTS['ocr_text_quality_threshold'] = "
            f"{config_utils._LOCAL_DEFAULTS['ocr_text_quality_threshold']!r}"
        )


# ---------------------------------------------------------------------------
# Category D — Old tier naming in quality_control.py
# ---------------------------------------------------------------------------


def test_run_quality_control_no_tier1_key():
    """Category D: run_quality_control source must not contain '\"tier1\"' key literal."""
    from quality_control.quality_control import run_quality_control

    source = inspect.getsource(run_quality_control)
    if '"tier1"' in source:
        pytest.fail(
            'ARTIFACT PRESENT (Category D old tier naming): '
            'run_quality_control() source contains \'"tier1"\'. '
            'The metrics_hierarchy keys must be renamed: '
            '"tier1" -> "local_metrics", "tier2" -> "exact_match", "tier3" -> "semantic_match" (REQ-8). '
            'Counterexample: metrics_hierarchy initialized with "tier1", "tier2", "tier3" keys.'
        )


# ---------------------------------------------------------------------------
# Category F — Stale test files with old-arch naming
# ---------------------------------------------------------------------------


def test_no_stale_steering_drift_bug_condition_file():
    """Category F: tests/steering/test_steering_drift_bug_condition.py must not exist."""
    path = "tests/steering/test_steering_drift_bug_condition.py"
    if os.path.exists(path):
        pytest.fail(
            f"ARTIFACT PRESENT (Category F stale file): "
            f"'{path}' still exists. "
            "All six files in tests/steering/ are migration-era steering tests "
            "that must be deleted along with the tests/steering/ directory (REQ-12). "
            f"Counterexample: os.path.exists('{path}') is True."
        )


def test_no_old_arch_tier1_test_file():
    """Category F: tests/pdf_extractor/test_text_extractor_tier1.py must not exist."""
    path = "tests/pdf_extractor/test_text_extractor_tier1.py"
    if os.path.exists(path):
        pytest.fail(
            f"ARTIFACT PRESENT (Category F old-arch naming): "
            f"'{path}' still exists. "
            "This file uses old cascade tier naming and must be renamed to "
            "'test_pdfplumber_backend.py' with all internal tier1 references replaced (REQ-13). "
            f"Counterexample: os.path.exists('{path}') is True."
        )
