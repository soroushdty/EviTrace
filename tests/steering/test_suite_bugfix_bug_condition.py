"""
tests/steering/test_suite_bugfix_bug_condition.py
-------------------------------------------------
Bug condition exploration test for the test-suite-bugfix spec.

Property 1: Bug Condition — Clean Collection and Zero Failures

This test encodes the EXPECTED POST-FIX state:
  - ``python -m pytest --collect-only -q`` produces zero collection errors.
  - ``python -m pytest -q`` produces zero failures outside the four
    intentionally-failing steering-drift tests.

On UNFIXED code this test FAILS — that failure is the intended outcome for
Task 1 and confirms the bugs exist.  After all fixes are applied (Tasks 3–10),
re-running this file should produce a fully green result (Task 11).

**Validates: Requirements REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8**
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Recursion guard
# ---------------------------------------------------------------------------
# When this file is collected inside a subprocess spawned by the preservation
# or bug-condition tests, we must not spawn further subprocesses — that would
# create an exponential process tree that exhausts memory.
_INSIDE_SUBPROCESS = os.environ.get("_PYTEST_PRESERVATION_SUBPROCESS") == "1"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# These four tests are intentionally failing and are excluded from the
# "zero failures" assertion (see requirements Out of Scope section).
INTENTIONALLY_FAILING: frozenset[str] = frozenset(
    [
        "test_steering_drift_bug_condition.py::test_qc_file_names",
        "test_steering_drift_bug_condition.py::test_run_py_pdf_discovery_call_site",
        "test_steering_drift_bug_condition.py::test_qc_test_file_names",
        "test_steering_drift_bug_condition.py::test_cascade_order_pdfplumber_before_paddleocr",
    ]
)

# Repo root — two levels up from this file (tests/pdf_extractor/ → tests/ → repo root)
REPO_ROOT = Path(__file__).parent.parent.parent

# These two files spawn subprocesses themselves and must be excluded from all
# subprocess invocations to prevent an exponential process tree.
_SELF_SPAWNING_FILES = (
    "tests/steering/test_suite_bugfix_bug_condition.py",
    "tests/steering/test_suite_bugfix_preservation.py",
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run_pytest(*extra_args: str) -> subprocess.CompletedProcess:
    """Run pytest as a subprocess from the repo root and return the result."""
    cmd = [sys.executable, "-m", "pytest"] + list(extra_args)
    # Exclude self-spawning files so the subprocess does not re-enter this
    # file and spawn further subprocesses.
    for f in _SELF_SPAWNING_FILES:
        cmd += ["--ignore", f]
    env = os.environ.copy()
    env["_PYTEST_PRESERVATION_SUBPROCESS"] = "1"
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _parse_collection_errors(output: str) -> list[str]:
    """Extract collection error lines from pytest output.

    pytest reports collection errors in the short summary section as:
        ERROR tests/pdf_extractor/test_foo.py
    and also in the detailed section as:
        _________ ERROR collecting tests/pdf_extractor/test_foo.py __________
    We look for both patterns.
    """
    errors = []
    for line in output.splitlines():
        stripped = line.strip()
        # Short summary format: "ERROR tests/..."
        if stripped.startswith("ERROR ") and "::" not in stripped and not stripped.startswith("ERROR:"):
            # Exclude lines like "ERROR: ..." (pytest internal errors)
            # Include lines like "ERROR tests/pdf_extractor/test_foo.py"
            path_part = stripped[len("ERROR "):].strip()
            if path_part and not path_part.startswith(":"):
                errors.append(stripped)
        # Detailed section format: "_____ ERROR collecting tests/..."
        elif "ERROR collecting" in stripped:
            errors.append(stripped)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique


def _parse_failed_tests(output: str) -> list[str]:
    """Extract FAILED test node IDs from pytest -q output.

    pytest -q prints lines like:
        FAILED tests/pdf_extractor/test_foo.py::TestBar::test_baz - ...
    """
    failed: list[str] = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            # Extract the node ID (everything before the first " - ")
            node_id = line[len("FAILED "):].split(" - ")[0].strip()
            failed.append(node_id)
    return failed


# ---------------------------------------------------------------------------
# Sub-check 1 — Zero collection errors
# ---------------------------------------------------------------------------

def test_zero_collection_errors():
    """Assert ``python -m pytest --collect-only -q`` produces zero collection errors.

    Expected to FAIL on unfixed code with 4 collection errors:
      - test_logging_utils.py       — ImportError: setup_logger (REQ-1)
      - test_parser_pipeline.py     — ImportError: parse_document (REQ-1)
      - test_quality_control_artifact_generator.py — ModuleNotFoundError (REQ-1)
      - test_metrics_hierarchy.py   — ImportError: build_metrics_hierarchy (REQ-1)
    """
    if _INSIDE_SUBPROCESS:
        pytest.skip("Skipping subprocess-spawning test when already inside a subprocess.")

    result = _run_pytest("--collect-only", "-q")

    combined_output = result.stdout + result.stderr
    collection_errors = _parse_collection_errors(combined_output)

    # Also check the return code: pytest exits with code 2 when there are
    # collection errors (code 0 = all passed, 1 = some failed, 2 = interrupted).
    has_collection_error_exit = result.returncode == 2

    assert not collection_errors and not has_collection_error_exit, (
        f"Found {len(collection_errors)} collection error(s) — expected zero.\n\n"
        f"Collection errors:\n"
        + "\n".join(f"  {e}" for e in collection_errors)
        + "\n\n"
        f"pytest exit code: {result.returncode} (2 = interrupted by collection errors)\n\n"
        f"Full output (last 40 lines):\n"
        + "\n".join(combined_output.splitlines()[-40:])
        + "\n\n"
        "Bug condition confirmed (REQ-1): stale import paths prevent collection."
    )


# ---------------------------------------------------------------------------
# Sub-check 2 — Zero unexpected failures
# ---------------------------------------------------------------------------

def test_zero_unexpected_failures():
    """Assert ``python -m pytest -q`` produces zero failures outside the four
    intentionally-failing steering-drift tests.

    Expected to FAIL on unfixed code with ~51 failures across 8 root causes:
      - REQ-1:  4 collection errors (ImportError / ModuleNotFoundError)
      - REQ-2: 18 failures — AttributeError: extract_with_pymupdf
      - REQ-3: 10 failures — test_text_extractor_orchestrator.py patching removed symbols
      - REQ-4: 14 failures — OSError: Can't find model 'en_core_sci_sm'
      - REQ-5:  7 failures — ModuleNotFoundError: pdf2image
      - REQ-6:  2 failures — NameError: MockFaiss
      - REQ-7:  2 failures — logic errors in test_quality_control_pipeline.py
      - REQ-8:  1 failure  — false-positive grep in test_domain_agnosticism.py
    """
    if _INSIDE_SUBPROCESS:
        pytest.skip("Skipping subprocess-spawning test when already inside a subprocess.")

    result = _run_pytest("-q")

    combined_output = result.stdout + result.stderr

    # Check for collection errors first — if pytest was interrupted, that is
    # itself a bug condition (REQ-1).
    collection_errors = _parse_collection_errors(combined_output)
    if collection_errors or result.returncode == 2:
        pytest.fail(
            f"pytest was interrupted by {len(collection_errors)} collection error(s).\n\n"
            f"Collection errors:\n"
            + "\n".join(f"  {e}" for e in collection_errors)
            + "\n\n"
            f"pytest exit code: {result.returncode}\n\n"
            "Bug condition confirmed (REQ-1): collection errors prevent the full "
            "suite from running. Fix REQ-1 first, then re-run to surface REQ-2 "
            "through REQ-8 failures."
        )

    all_failed = _parse_failed_tests(combined_output)

    # Filter out the intentionally-failing tests
    unexpected_failures = [
        node_id
        for node_id in all_failed
        if not any(
            intentional in node_id
            for intentional in INTENTIONALLY_FAILING
        )
    ]

    assert unexpected_failures == [], (
        f"Found {len(unexpected_failures)} unexpected failure(s) — expected zero.\n\n"
        f"Unexpected failures:\n"
        + "\n".join(f"  FAILED {f}" for f in unexpected_failures)
        + "\n\n"
        f"(Intentionally-failing tests excluded: {len(INTENTIONALLY_FAILING)})\n\n"
        "Bug condition confirmed (REQ-1 through REQ-8): test suite has failures "
        "that must be fixed."
    )
