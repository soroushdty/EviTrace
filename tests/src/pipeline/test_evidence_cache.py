"""Unit tests for content-hash evidence caching (Requirement 7.4).

Validates that replacing a file with same-size different content causes a
cache miss — proving that stat-based caching (same size, same mtime) would
fail but content-hash catches the change.
"""

from pathlib import Path
import importlib.util
import sys

_EVIDENCE_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "evidence_index.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_evidence_index_cache", _EVIDENCE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

_pdf_sha256 = _MODULE._pdf_sha256
_EXTRACTION_MAP_HASH = _MODULE._EXTRACTION_MAP_HASH


def test_same_size_different_content_cache_miss(tmp_path: Path):
    """Replacing a file with same-size different content returns a cache miss.

    This proves that stat-based caching (same size, same mtime) would fail
    but content-hash (SHA-256) catches the change.
    """
    pdf_file = tmp_path / "paper.pdf"

    # Write initial content (17 bytes)
    content_a = b"hello world 12345"
    pdf_file.write_bytes(content_a)

    # Compute cache key components for content A
    sha256_a = _pdf_sha256(str(pdf_file))
    paper_id = "test_paper"
    cache_key_a = f"{paper_id}_{sha256_a}_{_EXTRACTION_MAP_HASH}"

    # Replace with same-size different content (17 bytes)
    content_b = b"world hello 54321"
    assert len(content_a) == len(content_b), "Test precondition: contents must be same size"
    pdf_file.write_bytes(content_b)

    # Compute cache key components for content B
    sha256_b = _pdf_sha256(str(pdf_file))
    cache_key_b = f"{paper_id}_{sha256_b}_{_EXTRACTION_MAP_HASH}"

    # The SHA-256 hashes must differ despite same file size
    assert sha256_a != sha256_b, (
        "SHA-256 hashes should differ for same-size different content"
    )

    # The full cache keys must differ — this is the cache miss
    assert cache_key_a != cache_key_b, (
        "Cache keys should differ when file content changes, "
        "even if file size remains the same"
    )
