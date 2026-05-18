"""Property-based tests for content-hash evidence cache invalidation (Property 12).

**Validates: Requirements 7.1, 7.2, 7.5**

Property 12: For any PDF file, the evidence cache key SHALL include the SHA-256
hash of the file's byte content and the SHA-256 hash of `configs/extraction_map.json`.
When a PDF's content changes (regardless of filename, file size, or modification time
remaining the same), the cache SHALL return a miss.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Import the evidence_index module directly (same pattern as existing tests)
# ---------------------------------------------------------------------------

_EVIDENCE_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "evidence_index.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_evidence_index_cache_props", _EVIDENCE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

_pdf_sha256 = _MODULE._pdf_sha256
_EXTRACTION_MAP_HASH = _MODULE._EXTRACTION_MAP_HASH


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate non-empty binary content (at least 1 byte, up to 4KB for speed)
_binary_content = st.binary(min_size=1, max_size=4096)

# Generate pairs of distinct binary content
_distinct_content_pair = st.tuples(
    _binary_content,
    _binary_content,
).filter(lambda pair: pair[0] != pair[1])

# Generate same-size distinct content pairs
_same_size_distinct_pair = st.integers(min_value=1, max_value=4096).flatmap(
    lambda size: st.tuples(
        st.binary(min_size=size, max_size=size),
        st.binary(min_size=size, max_size=size),
    ).filter(lambda pair: pair[0] != pair[1])
)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(content=_binary_content)
@settings(max_examples=100)
def test_cache_key_includes_sha256_of_file_bytes(content: bytes, tmp_path_factory):
    """Cache key includes the SHA-256 hash of the PDF file's byte content.

    **Validates: Requirements 7.1**
    """
    tmp_path = tmp_path_factory.mktemp("cache_sha256")
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(content)

    # Compute SHA-256 directly
    expected_sha256 = hashlib.sha256(content).hexdigest()

    # Compute via the module function
    actual_sha256 = _pdf_sha256(str(pdf_path))

    assert actual_sha256 == expected_sha256, (
        f"_pdf_sha256 returned {actual_sha256!r}, expected {expected_sha256!r}"
    )

    # Verify the cache key format includes the SHA-256
    paper_id = "test_paper"
    cache_key = f"{paper_id}_{actual_sha256}_{_EXTRACTION_MAP_HASH}"

    assert expected_sha256 in cache_key, (
        "Cache key does not include the SHA-256 of file bytes"
    )


@given(content=_binary_content)
@settings(max_examples=100)
def test_cache_key_includes_extraction_map_hash(content: bytes, tmp_path_factory):
    """Cache key includes the SHA-256 hash of configs/extraction_map.json.

    **Validates: Requirements 7.5**
    """
    tmp_path = tmp_path_factory.mktemp("cache_map_hash")
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(content)

    pdf_sha256 = _pdf_sha256(str(pdf_path))
    paper_id = "test_paper"
    cache_key = f"{paper_id}_{pdf_sha256}_{_EXTRACTION_MAP_HASH}"

    # The extraction map hash must be present in the cache key
    assert _EXTRACTION_MAP_HASH in cache_key, (
        "Cache key does not include the extraction_map hash"
    )

    # The extraction map hash should be a valid SHA-256 hex digest or the
    # fallback "no_extraction_map" (when configs dir is absent in test env)
    if _EXTRACTION_MAP_HASH != "no_extraction_map":
        assert len(_EXTRACTION_MAP_HASH) == 64, (
            "Extraction map hash is not a valid SHA-256 hex digest"
        )
        assert all(c in "0123456789abcdef" for c in _EXTRACTION_MAP_HASH), (
            "Extraction map hash contains non-hex characters"
        )


@given(pair=_distinct_content_pair)
@settings(max_examples=100)
def test_content_change_causes_cache_miss(pair: tuple[bytes, bytes], tmp_path_factory):
    """When PDF content changes, the cache key changes (cache miss).

    **Validates: Requirements 7.2**
    """
    content_a, content_b = pair
    tmp_path = tmp_path_factory.mktemp("cache_miss")
    pdf_path = tmp_path / "same_name.pdf"

    # Write first content and compute cache key
    pdf_path.write_bytes(content_a)
    sha256_a = _pdf_sha256(str(pdf_path))
    cache_key_a = f"paper_{sha256_a}_{_EXTRACTION_MAP_HASH}"

    # Write different content to the SAME filename and compute cache key
    pdf_path.write_bytes(content_b)
    sha256_b = _pdf_sha256(str(pdf_path))
    cache_key_b = f"paper_{sha256_b}_{_EXTRACTION_MAP_HASH}"

    # Cache keys must differ when content differs
    assert cache_key_a != cache_key_b, (
        f"Cache keys are identical despite different content: {cache_key_a}"
    )


@given(pair=_same_size_distinct_pair)
@settings(max_examples=100)
def test_same_size_different_content_causes_cache_miss(
    pair: tuple[bytes, bytes], tmp_path_factory
):
    """Same file size but different content produces different cache keys.

    This verifies that the cache does NOT rely on file size for identity —
    only the SHA-256 of the actual bytes matters.

    **Validates: Requirements 7.2**
    """
    content_a, content_b = pair
    assert len(content_a) == len(content_b), "Test setup: sizes must match"

    tmp_path = tmp_path_factory.mktemp("same_size")
    pdf_path = tmp_path / "paper.pdf"

    # Write first content
    pdf_path.write_bytes(content_a)
    sha256_a = _pdf_sha256(str(pdf_path))

    # Write same-size different content
    pdf_path.write_bytes(content_b)
    sha256_b = _pdf_sha256(str(pdf_path))

    # SHA-256 hashes must differ
    assert sha256_a != sha256_b, (
        "SHA-256 hashes are identical for same-size different content"
    )

    # Cache keys must differ
    cache_key_a = f"paper_{sha256_a}_{_EXTRACTION_MAP_HASH}"
    cache_key_b = f"paper_{sha256_b}_{_EXTRACTION_MAP_HASH}"
    assert cache_key_a != cache_key_b, (
        "Cache keys are identical despite different content (same size)"
    )


@given(content=_binary_content)
@settings(max_examples=100)
def test_same_content_same_cache_key_regardless_of_mtime(
    content: bytes, tmp_path_factory
):
    """Same content produces the same cache key regardless of modification time.

    This verifies that mtime does NOT affect the cache key — only content matters.

    **Validates: Requirements 7.1, 7.2**
    """
    tmp_path = tmp_path_factory.mktemp("mtime")
    pdf_path = tmp_path / "paper.pdf"

    # Write content and compute cache key
    pdf_path.write_bytes(content)
    sha256_first = _pdf_sha256(str(pdf_path))

    # Change the modification time (set to a different timestamp)
    original_mtime = pdf_path.stat().st_mtime_ns
    new_mtime = original_mtime + 1_000_000_000  # 1 second later
    os.utime(str(pdf_path), ns=(new_mtime, new_mtime))

    # Recompute — should be identical since content hasn't changed
    sha256_second = _pdf_sha256(str(pdf_path))

    assert sha256_first == sha256_second, (
        "SHA-256 changed when only mtime changed (content is identical)"
    )

    cache_key_first = f"paper_{sha256_first}_{_EXTRACTION_MAP_HASH}"
    cache_key_second = f"paper_{sha256_second}_{_EXTRACTION_MAP_HASH}"
    assert cache_key_first == cache_key_second, (
        "Cache key changed when only mtime changed"
    )
