"""Self-check of the strict expected-failure convention used by test-first sub-tasks.

The ``extraction-contract-hardening`` spec adds its new tests before the code they
exercise, marked ``pytest.mark.xfail(strict=True, reason="task N.M")``. The
implementing sub-task removes the marker. This module holds the one permanent
demonstration of that convention (see ``.kiro/steering/testing.md``, "Expected
Failures for Test-First Sub-Tasks"): a test that asserts something false and is
reported as ``xfailed``, so the suite stays green while it exists.

A strict marker also turns an unexpected pass into a failure; that half of the
convention is documented, not exercised here, because a test that is meant to
fail the suite cannot be part of it.

The marker is deliberately never removed: this is the demonstration, not a
placeholder for pending work.
"""
from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="task 1.1 convention self-check (permanent)")
def test_strict_expected_failure_is_reported_as_xfailed_not_failed():
    # A statement that is false by construction: the strict marker must absorb
    # the failure (reported as xfailed). If this ever started to pass, strict
    # mode would report it as a failure.
    assert 1 + 1 == 3, "deliberately false: demonstrates xfail(strict=True)"
