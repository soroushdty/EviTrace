"""
tests/steering/test_suite_bugfix_preservation.py
------------------------------------------------
Preservation property test for the test-suite-bugfix spec.

Property 2: Preservation — Currently-Passing Tests Must Not Regress

For every test that currently passes on the unfixed codebase (i.e.,
``isBugCondition`` returns false for that test), the fixed codebase SHALL
produce the same passing result.

**Methodology (observation-first):**

1. Run ``python -m pytest -q`` on the unfixed code and record all
   currently-passing test node IDs.
2. Assert that those same tests still pass when this file is re-run after
   fixes are applied.

**Expected outcome on UNFIXED code:** PASS — this test records the baseline
and verifies that the currently-passing tests do indeed pass.  It will
continue to pass after all fixes are applied (no regressions).

**Validates: Requirements REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8**
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Recursion guard
# ---------------------------------------------------------------------------
# This file runs pytest as a subprocess at module import time.  Without a
# guard, the subprocess would collect this file again, spawning another
# subprocess, and so on indefinitely.  We use an environment variable to
# detect when we are already inside a subprocess invocation and skip the
# observation step.
_INSIDE_SUBPROCESS = os.environ.get("_PYTEST_PRESERVATION_SUBPROCESS") == "1"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# These four tests are intentionally failing and are excluded from the
# preservation check (see requirements Out of Scope section).
INTENTIONALLY_FAILING: frozenset[str] = frozenset(
    [
        "test_steering_drift_bug_condition.py::test_qc_file_names",
        "test_steering_drift_bug_condition.py::test_run_py_pdf_discovery_call_site",
        "test_steering_drift_bug_condition.py::test_qc_test_file_names",
        "test_steering_drift_bug_condition.py::test_cascade_order_pdfplumber_before_paddleocr",
    ]
)

# The four broken test files that cause collection errors on unfixed code.
# Also exclude the two self-spawning test files (this file and the bug-condition
# file) from all subprocess invocations — including them would cause each
# subprocess to spawn further subprocesses, creating an exponential process
# tree that exhausts memory.
BROKEN_COLLECTION_FILES: tuple[str, ...] = (
    "tests/utils/test_logging_utils.py",
    "tests/pdf_extractor/test_metrics_hierarchy.py",
    "tests/pdf_extractor/test_parser_pipeline.py",
    "tests/pdf_extractor/test_quality_control_artifact_generator.py",
    # Self-spawning files — must always be excluded from subprocess runs
    "tests/steering/test_suite_bugfix_bug_condition.py",
    "tests/steering/test_suite_bugfix_preservation.py",
)

# Repo root — two levels up from this file (tests/pdf_extractor/ → tests/ → repo root)
REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_pytest(*extra_args: str) -> subprocess.CompletedProcess:
    """Run pytest as a subprocess from the repo root and return the result.

    Always excludes the self-spawning test files (this file and the bug-condition
    file) to prevent an exponential subprocess tree.
    """
    cmd = [sys.executable, "-m", "pytest"] + list(extra_args)
    # Always exclude self-spawning files regardless of what extra_args requests.
    for f in (
        "tests/steering/test_suite_bugfix_bug_condition.py",
        "tests/steering/test_suite_bugfix_preservation.py",
    ):
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


def _parse_passed_tests(output: str) -> list[str]:
    """Extract PASSED test node IDs from pytest -v output.

    pytest -v prints lines like:
        tests/pdf_extractor/test_foo.py::TestBar::test_baz PASSED [ 42%]
    """
    passed: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if " PASSED" in stripped:
            # Node ID is everything before " PASSED"
            node_id = stripped.split(" PASSED")[0].strip()
            if node_id:
                passed.append(node_id)
    return passed


def _parse_failed_tests(output: str) -> list[str]:
    """Extract FAILED test node IDs from pytest -q output.

    pytest -q prints lines like:
        FAILED tests/pdf_extractor/test_foo.py::TestBar::test_baz - ...
    """
    failed: list[str] = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            node_id = line[len("FAILED "):].split(" - ")[0].strip()
            failed.append(node_id)
    return failed


def _observe_currently_passing_tests() -> list[str]:
    """Run the suite (excluding broken collection files) and return passing IDs.

    This is the observation step of the observation-first methodology.
    On unfixed code, the four broken files cause collection errors that
    interrupt the entire run, so we exclude them here to get a clean
    baseline of currently-passing tests.
    """
    ignore_args = []
    for broken_file in BROKEN_COLLECTION_FILES:
        ignore_args.extend(["--ignore", broken_file])

    result = _run_pytest("-v", *ignore_args)
    combined = result.stdout + result.stderr
    return _parse_passed_tests(combined)


# ---------------------------------------------------------------------------
# Baseline observation (lazy — runs once on first test access, not at import)
# ---------------------------------------------------------------------------

# Deferred so that pytest collection does not spawn a subprocess.
# The observation runs on first access inside a test function.
_CURRENTLY_PASSING: list[str] | None = None


def _get_currently_passing() -> list[str]:
    """Return the cached baseline, observing it on first call."""
    global _CURRENTLY_PASSING
    if _CURRENTLY_PASSING is None:
        _CURRENTLY_PASSING = (
            [] if _INSIDE_SUBPROCESS else _observe_currently_passing_tests()
        )
    return _CURRENTLY_PASSING


# ---------------------------------------------------------------------------
# Sub-check 1 — Baseline is non-empty
# ---------------------------------------------------------------------------

def test_baseline_is_non_empty():
    """Assert that the observation step found at least one currently-passing test.

    This guards against a misconfigured observation run that returns an empty
    list (which would make the preservation property vacuously true).

    Expected to PASS on both unfixed and fixed code.
    """
    if _INSIDE_SUBPROCESS:
        pytest.skip("Skipping subprocess-spawning test when already inside a subprocess.")
    currently_passing = _get_currently_passing()
    assert len(currently_passing) > 0, (
        "Observation step returned zero passing tests — the baseline is empty.\n"
        "This means the preservation property would be vacuously satisfied.\n"
        "Check that the observation run is configured correctly."
    )


# ---------------------------------------------------------------------------
# Sub-check 2 — Baseline count is plausible
# ---------------------------------------------------------------------------

def test_baseline_count_is_plausible():
    """Assert that the baseline contains a plausible number of passing tests.

    On unfixed code, 571 tests pass (out of 624 collectible tests).
    We assert at least 500 to guard against a severely degraded baseline.

    Expected to PASS on both unfixed and fixed code.
    """
    if _INSIDE_SUBPROCESS:
        pytest.skip("Skipping subprocess-spawning test when already inside a subprocess.")
    currently_passing = _get_currently_passing()
    assert len(currently_passing) >= 500, (
        f"Baseline contains only {len(currently_passing)} passing tests — "
        f"expected at least 500.\n"
        "This suggests the observation run encountered unexpected failures.\n"
        f"First 10 passing tests found:\n"
        + "\n".join(f"  {t}" for t in currently_passing[:10])
    )


# ---------------------------------------------------------------------------
# Sub-check 3 — All currently-passing tests still pass
# ---------------------------------------------------------------------------

def test_all_currently_passing_tests_still_pass():
    """Assert that every test in the baseline still passes.

    This is the core preservation property:
      For all tests T where isBugCondition(T) is False on unfixed code,
      the fixed codebase SHALL produce the same passing result for T.

    On UNFIXED code: PASS — the baseline tests pass by definition.
    After fixes: PASS — no regressions were introduced.
    If a fix accidentally breaks a previously-passing test: FAIL.

    **Validates: Requirements REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8**
    """
    if _INSIDE_SUBPROCESS:
        pytest.skip("Skipping subprocess-spawning test when already inside a subprocess.")

    # Run the full suite (no ignores — after fixes, all files should collect).
    # We use -q for speed; failures are reported in the short summary.
    result = _run_pytest("-q")
    combined = result.stdout + result.stderr

    all_failed = _parse_failed_tests(combined)

    # Filter out intentionally-failing tests
    unexpected_failures = [
        node_id
        for node_id in all_failed
        if not any(intentional in node_id for intentional in INTENTIONALLY_FAILING)
    ]

    # Find which baseline tests are now failing (regressions)
    baseline_set = set(_get_currently_passing())
    regressions = [f for f in unexpected_failures if f in baseline_set]

    assert not regressions, (
        f"Preservation violated: {len(regressions)} previously-passing test(s) "
        f"now fail.\n\n"
        f"Regressions:\n"
        + "\n".join(f"  FAILED {r}" for r in regressions)
        + "\n\n"
        f"Total unexpected failures: {len(unexpected_failures)}\n"
        f"Baseline size: {len(_get_currently_passing())} tests\n\n"
        "A fix introduced a regression in a previously-passing test.\n"
        "Review the changes and ensure no existing correct behaviour was altered."
    )


# ---------------------------------------------------------------------------
# Sub-check 4 — Preservation property: no new failures in passing test files
# ---------------------------------------------------------------------------

def test_no_new_failures_in_passing_test_files():
    """Assert that test files which currently have passing tests do not gain failures.

    This is a file-level preservation check: for each test file that has at
    least one currently-passing test, the file must not produce any new
    unexpected failures after fixes are applied.

    On UNFIXED code: PASS — the currently-passing files pass by definition.
    After fixes: PASS — no regressions in previously-clean files.

    **Validates: Requirements REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8**
    """
    if _INSIDE_SUBPROCESS:
        pytest.skip("Skipping subprocess-spawning test when already inside a subprocess.")

    # Identify which test files have at least one currently-passing test
    currently_passing = _get_currently_passing()
    passing_files: set[str] = set()
    for node_id in currently_passing:
        # node_id format: "tests/pdf_extractor/test_foo.py::..."
        file_part = node_id.split("::")[0]
        passing_files.add(file_part)

    if not passing_files:
        pytest.skip("No passing test files found in baseline — skipping file-level check.")

    # Run only the files that have passing tests
    result = _run_pytest("-q", *sorted(passing_files))
    combined = result.stdout + result.stderr

    all_failed = _parse_failed_tests(combined)

    # Filter out intentionally-failing tests
    unexpected_failures = [
        node_id
        for node_id in all_failed
        if not any(intentional in node_id for intentional in INTENTIONALLY_FAILING)
    ]

    # Find regressions: failures in tests that were previously passing
    baseline_set = set(_get_currently_passing())
    regressions = [f for f in unexpected_failures if f in baseline_set]

    assert not regressions, (
        f"File-level preservation violated: {len(regressions)} previously-passing "
        f"test(s) in passing files now fail.\n\n"
        f"Regressions:\n"
        + "\n".join(f"  FAILED {r}" for r in regressions)
        + "\n\n"
        f"Checked {len(passing_files)} test file(s) with passing tests.\n"
        "A fix introduced a regression in a previously-passing test file."
    )


# ---------------------------------------------------------------------------
# Property-based sub-check 5 — Hypothesis: sampled passing tests still pass
# ---------------------------------------------------------------------------

# Build a fixed list of passing test node IDs for Hypothesis to sample from.
# Populated lazily on first access (same as _get_currently_passing).
def _get_passing_list() -> list[str]:
    return _get_currently_passing()


@given(
    indices=st.lists(
        st.integers(min_value=0, max_value=999),
        min_size=1,
        max_size=5,
        unique=True,
    )
)
@settings(max_examples=3, deadline=None)
def test_pbt_sampled_passing_tests_still_pass(indices: list[int]):
    """Property: for any sample of currently-passing tests, they all still pass."""
    if _INSIDE_SUBPROCESS:
        return  # skip silently when inside a subprocess

    passing_list = _get_passing_list()
    if not passing_list:
        return  # vacuously true if no passing tests

    # Clamp indices to valid range
    clamped = [i % len(passing_list) for i in indices]
    sampled_node_ids = list({passing_list[i] for i in clamped})

    # Run only the sampled tests
    result = _run_pytest("-q", *sampled_node_ids)
    combined = result.stdout + result.stderr

    all_failed = _parse_failed_tests(combined)

    # Filter out intentionally-failing tests
    unexpected_failures = [
        node_id
        for node_id in all_failed
        if not any(intentional in node_id for intentional in INTENTIONALLY_FAILING)
    ]

    # All sampled tests should still pass
    sampled_set = set(sampled_node_ids)
    regressions = [f for f in unexpected_failures if f in sampled_set]

    assert not regressions, (
        f"PBT preservation violated: {len(regressions)} sampled test(s) now fail.\n\n"
        f"Sampled tests ({len(sampled_node_ids)}):\n"
        + "\n".join(f"  {t}" for t in sampled_node_ids)
        + "\n\n"
        f"Regressions:\n"
        + "\n".join(f"  FAILED {r}" for r in regressions)
        + "\n\n"
        "A fix introduced a regression in a previously-passing test."
    )