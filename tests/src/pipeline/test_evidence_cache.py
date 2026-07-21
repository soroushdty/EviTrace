"""Unit tests for content-hash evidence caching (Requirement 7.4).

Validates that replacing a file with same-size different content causes a
cache miss — proving that stat-based caching (same size, same mtime) would
fail but content-hash catches the change.
"""

from pathlib import Path
import importlib.util
import sys

from quality_control.models import QCBundle, UnifiedRecord

_EVIDENCE_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "evidence_index.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_evidence_index_cache", _EVIDENCE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

_pdf_sha256 = _MODULE._pdf_sha256
_EXTRACTION_MAP_HASH = _MODULE._EXTRACTION_MAP_HASH
build_or_load_evidence_bundle = _MODULE.build_or_load_evidence_bundle


def _qc_context_with_tei(tmp_path: Path, pdf_bytes: bytes = b"%PDF-1.4 fake") -> QCBundle:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(pdf_bytes)
    tei = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Sample Title</title></titleStmt>
      <sourceDesc><biblStruct><monogr><author><surname>Smith</surname></author><imprint><date when="2021"/></imprint></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <head>Introduction</head>
        <p><s coords="1;10,20,30,40">We used MIMIC-III data.</s></p>
      </div>
    </body>
  </text>
</TEI>
"""
    unified = UnifiedRecord(
        document_id="paper1",
        content={
            "exact_text": "We used MIMIC-III data.",
            "source_pdf_path": str(pdf_path),
            "grobid_tei_xml": tei,
        },
    )
    return QCBundle(branches=[], unified=unified)


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


# ---------------------------------------------------------------------------
# Requirement 3.5 — if the evidence index cache file already exists and the
# PDF content hash matches, THE Pipeline SHALL reuse the cached evidence
# index without re-parsing the TEI XML.
# ---------------------------------------------------------------------------


def test_cache_hit_reuses_index_without_reparsing_tei(tmp_path: Path, monkeypatch):
    ctx = _qc_context_with_tei(tmp_path)
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}

    bundle_first = build_or_load_evidence_bundle(ctx, cfg)
    assert bundle_first.index_path.exists()

    call_count = {"n": 0}
    original_build_items = _MODULE._build_items_from_tei

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return original_build_items(*args, **kwargs)

    monkeypatch.setattr(_MODULE, "_build_items_from_tei", _spy)

    bundle_second = build_or_load_evidence_bundle(ctx, cfg)

    assert call_count["n"] == 0, (
        "build_or_load_evidence_bundle() re-parsed the TEI XML on a cache hit "
        "instead of reusing the cached evidence index"
    )
    assert bundle_second.evidence_items == bundle_first.evidence_items
    assert bundle_second.prefilled_fields == bundle_first.prefilled_fields


def test_cache_miss_on_pdf_content_change_triggers_reparse(tmp_path: Path, monkeypatch):
    ctx = _qc_context_with_tei(tmp_path, pdf_bytes=b"%PDF-1.4 original bytes")
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}

    build_or_load_evidence_bundle(ctx, cfg)

    # Replace the PDF content underneath the same path -> different SHA-256 ->
    # the on-disk cache file for the OLD hash is not reused for the new hash.
    pdf_path = Path(ctx.unified.content["source_pdf_path"])
    pdf_path.write_bytes(b"%PDF-1.4 completely different bytes now")

    call_count = {"n": 0}
    original_build_items = _MODULE._build_items_from_tei

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return original_build_items(*args, **kwargs)

    monkeypatch.setattr(_MODULE, "_build_items_from_tei", _spy)

    build_or_load_evidence_bundle(ctx, cfg)

    assert call_count["n"] == 1, (
        "build_or_load_evidence_bundle() did not re-parse the TEI XML after the "
        "PDF content (and therefore its hash) changed"
    )
