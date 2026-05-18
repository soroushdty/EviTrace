"""Property-based tests for atomic write integrity (Property 15).

**Validates: Requirements 11.1, 11.2, 11.4**

Property 15: For any call to `_save_pdf_output()` or `save_manifest()`, the write
SHALL use a temporary file followed by `os.replace()`. If the write fails before
rename, the final output path SHALL either not exist or contain the previous valid
content — never a partial write.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Import _atomic_write_json from pdf_processor
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from pipeline.pdf_processor import _atomic_write_json


# ---------------------------------------------------------------------------
# Strategies — generate random JSON-serializable data
# ---------------------------------------------------------------------------

_json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10000, max_value=10000),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.text(min_size=0, max_size=100),
)

_json_values = st.recursive(
    _json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(data=_json_values)
@settings(max_examples=100)
def test_atomic_write_uses_temp_then_rename(data, tmp_path_factory):
    """Atomic write produces valid JSON at the final path via temp+rename.

    Verifies that after a successful _atomic_write_json call, the final path
    contains the exact JSON data and no .tmp file remains.

    **Validates: Requirements 11.1, 11.2**
    """
    tmp_path = tmp_path_factory.mktemp("atomic_write")
    final_path = tmp_path / "output.json"

    _atomic_write_json(final_path, data)

    # Final path must exist and contain valid JSON matching the input
    assert final_path.exists(), "Final path does not exist after atomic write"
    with open(final_path, encoding="utf-8") as f:
        written = json.load(f)
    assert written == data, (
        f"Written data does not match input: {written!r} != {data!r}"
    )

    # No temp file should remain
    tmp_file = final_path.with_suffix(final_path.suffix + ".tmp")
    assert not tmp_file.exists(), "Temp file still exists after successful write"


@given(data=_json_values)
@settings(max_examples=100)
def test_failure_before_rename_preserves_absent_state(data, tmp_path_factory):
    """If write fails before rename, final path remains absent (never partial).

    Simulates a failure during os.replace by patching it to raise an OSError.
    The final path should not exist after the failure.

    **Validates: Requirements 11.2, 11.4**
    """
    tmp_path = tmp_path_factory.mktemp("atomic_fail_absent")
    final_path = tmp_path / "output.json"

    # Final path does not exist initially
    assert not final_path.exists()

    # Patch os.replace to simulate failure after temp file is written
    with patch("pipeline.pdf_processor.os.replace", side_effect=OSError("simulated rename failure")):
        with pytest.raises(OSError, match="simulated rename failure"):
            _atomic_write_json(final_path, data)

    # Final path must still not exist — never a partial write
    assert not final_path.exists(), (
        "Final path exists after failed atomic write (should remain absent)"
    )

    # Temp file should be cleaned up
    tmp_file = final_path.with_suffix(final_path.suffix + ".tmp")
    assert not tmp_file.exists(), (
        "Temp file was not cleaned up after failure"
    )


@given(
    original_data=_json_values,
    new_data=_json_values,
)
@settings(max_examples=100)
def test_failure_before_rename_preserves_previous_content(
    original_data, new_data, tmp_path_factory
):
    """If write fails before rename, final path retains previous valid content.

    First writes valid data successfully, then attempts a second write that
    fails during os.replace. The final path should still contain the original
    valid content.

    **Validates: Requirements 11.2, 11.4**
    """
    tmp_path = tmp_path_factory.mktemp("atomic_fail_preserve")
    final_path = tmp_path / "output.json"

    # Write initial valid content
    _atomic_write_json(final_path, original_data)
    assert final_path.exists()

    # Attempt a second write that fails at rename
    with patch("pipeline.pdf_processor.os.replace", side_effect=OSError("simulated failure")):
        with pytest.raises(OSError, match="simulated failure"):
            _atomic_write_json(final_path, new_data)

    # Final path must still contain the ORIGINAL valid content
    with open(final_path, encoding="utf-8") as f:
        preserved = json.load(f)
    assert preserved == original_data, (
        f"Previous content was corrupted: got {preserved!r}, expected {original_data!r}"
    )

    # Temp file should be cleaned up
    tmp_file = final_path.with_suffix(final_path.suffix + ".tmp")
    assert not tmp_file.exists(), (
        "Temp file was not cleaned up after failure"
    )


@given(data=_json_values)
@settings(max_examples=100)
def test_failure_during_json_dump_preserves_state(data, tmp_path_factory):
    """If json.dump fails (e.g. unserializable data injected), final path is unchanged.

    Simulates a failure during the json.dump step by patching it to raise.
    The final path should remain absent or contain previous content.

    **Validates: Requirements 11.4**
    """
    tmp_path = tmp_path_factory.mktemp("atomic_dump_fail")
    final_path = tmp_path / "output.json"

    # Write initial content first
    _atomic_write_json(final_path, data)
    assert final_path.exists()

    # Now attempt a write where json.dump itself fails
    with patch("pipeline.pdf_processor.json.dump", side_effect=TypeError("not serializable")):
        with pytest.raises(TypeError, match="not serializable"):
            _atomic_write_json(final_path, {"bad": "data"})

    # Final path must still contain the original valid content
    with open(final_path, encoding="utf-8") as f:
        preserved = json.load(f)
    assert preserved == data, (
        "Previous content was corrupted after json.dump failure"
    )

    # Temp file should be cleaned up
    tmp_file = final_path.with_suffix(final_path.suffix + ".tmp")
    assert not tmp_file.exists(), (
        "Temp file was not cleaned up after json.dump failure"
    )


@given(data=_json_values)
@settings(max_examples=100)
def test_final_path_never_contains_partial_json(data, tmp_path_factory):
    """The final path never contains partial/truncated JSON content.

    After any successful write, the file must parse as valid JSON. This
    property ensures the atomic pattern prevents partial writes from being
    visible at the final path.

    **Validates: Requirements 11.1, 11.2**
    """
    tmp_path = tmp_path_factory.mktemp("atomic_no_partial")
    final_path = tmp_path / "output.json"

    _atomic_write_json(final_path, data)

    # The file at final_path must always be valid JSON
    raw = final_path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        pytest.fail(f"Final path contains invalid JSON: {e}")

    assert parsed == data
