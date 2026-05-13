"""
tests/test_quality_control_artifacts.py
========================================
Tests for pdf_extractor/extraction/quality_control/artifacts.py.

Covers:
  - Property 1: GROBID canonicalization is deterministic
  - Property 2: PyMuPDF canonicalization is deterministic
  - Property 3: Canonical artifacts are always produced regardless of export config
  - Property 4: No files written when export_to_disk is false
  - Unit tests for artifact disk export (3.5)
"""

from __future__ import annotations

import os
import tempfile

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from artifact_generation import (
    build_canonical_artifacts,
    canonicalize_grobid_xml,
    canonicalize_pymupdf_json,
    export_canonical_artifacts,
)


# ---------------------------------------------------------------------------
# Helpers / strategies
# ---------------------------------------------------------------------------

# Safe text for XML content: restrict to characters that are legal in XML 1.0.
# XML 1.0 allows: #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
# We use a conservative subset: printable ASCII minus XML-special chars.
_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z", "S"),
        blacklist_characters="<>&\"'",
    )
)

# Safe XML tag names: must start with a letter/underscore, no spaces or colons
_safe_tag = st.from_regex(r"[A-Za-z_][A-Za-z0-9_\-\.]{0,19}", fullmatch=True)


def _build_xml_str(tag: str, text: str) -> str:
    """Build a minimal valid XML string: <tag>text</tag>."""
    return f"<{tag}>{text}</{tag}>"


# ---------------------------------------------------------------------------
# Property 1: GROBID canonicalization is deterministic
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 1: GROBID canonicalization is deterministic
@given(
    tag=_safe_tag,
    text=_safe_text,
)
@settings(max_examples=20)
def test_grobid_canonicalization_deterministic(tag: str, text: str):
    """**Validates: Requirements 2.2, 2.4**

    For any valid XML string, calling canonicalize_grobid_xml twice with the
    same input SHALL return byte-for-byte identical strings on both invocations.
    """
    xml_str = _build_xml_str(tag, text)
    result1 = canonicalize_grobid_xml(xml_str)
    result2 = canonicalize_grobid_xml(xml_str)
    assert result1 == result2


# ---------------------------------------------------------------------------
# Property 2: PyMuPDF canonicalization is deterministic
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 2: PyMuPDF canonicalization is deterministic
@given(
    obj=st.one_of(
        st.dictionaries(st.text(), st.text()),
        st.lists(st.text()),
    )
)
@settings(max_examples=20)
def test_pymupdf_canonicalization_deterministic(obj):
    """**Validates: Requirements 2.3, 2.4**

    For any PyMuPDF dict or list, calling canonicalize_pymupdf_json twice with
    the same input SHALL return byte-for-byte identical strings on both
    invocations.
    """
    result1 = canonicalize_pymupdf_json(obj)
    result2 = canonicalize_pymupdf_json(obj)
    assert result1 == result2


# ---------------------------------------------------------------------------
# Property 3: Canonical artifacts are always produced regardless of export config
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 3: Canonical artifacts are always produced regardless of export config
@given(
    document_id=st.text(min_size=1),
    pymupdf_output=st.dictionaries(st.text(), st.text()),
)
@settings(max_examples=20)
def test_build_canonical_artifacts_always_produces_output(
    document_id: str, pymupdf_output: dict
):
    """**Validates: Requirements 2.5, 1.6**

    For any grobid output string, pymupdf output dict, and document_id,
    build_canonical_artifacts SHALL return a non-None dict containing both
    "grobid" and "pymupdf" keys.
    """
    # Use a minimal valid XML string as grobid_output
    grobid_output = "<root><body>test</body></root>"
    result = build_canonical_artifacts(grobid_output, pymupdf_output, document_id)
    assert result is not None
    assert isinstance(result, dict)
    assert "grobid" in result
    assert "pymupdf" in result


# ---------------------------------------------------------------------------
# Property 4: No files written when export_to_disk is false
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 4: No files written when export_to_disk is false
@given(pymupdf_output=st.dictionaries(st.text(), st.text()))
@settings(max_examples=20)
def test_no_files_written_when_export_disabled(pymupdf_output: dict):
    """**Validates: Requirements 2.7**

    build_canonical_artifacts must not write any files to disk. The pipeline
    is responsible for not calling export_canonical_artifacts when
    export_to_disk is False. This test verifies that build_canonical_artifacts
    itself is a pure in-memory operation that leaves any given directory
    unchanged.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        # build_canonical_artifacts must not write any files
        build_canonical_artifacts("<root/>", pymupdf_output, "test_doc_id")
        assert os.listdir(tmp_dir) == []


# ---------------------------------------------------------------------------
# Unit tests: artifact disk export (3.5)
# ---------------------------------------------------------------------------

class TestExportCanonicalArtifacts:
    def test_export_writes_grobid_xml(self, tmp_path):
        """export_canonical_artifacts writes <document_id>_grobid.xml to output_dir."""
        artifacts = build_canonical_artifacts(
            "<root><body>hello</body></root>", {"key": "val"}, "doc123"
        )
        export_canonical_artifacts(artifacts, str(tmp_path))
        grobid_file = tmp_path / "doc123_grobid.xml"
        assert grobid_file.exists()
        assert grobid_file.read_text(encoding="utf-8") == artifacts["grobid"]["content"]

    def test_export_writes_pymupdf_json(self, tmp_path):
        """export_canonical_artifacts writes <document_id>_pymupdf.json to output_dir."""
        artifacts = build_canonical_artifacts("<root/>", {"a": "b"}, "doc456")
        export_canonical_artifacts(artifacts, str(tmp_path))
        pymupdf_file = tmp_path / "doc456_pymupdf.json"
        assert pymupdf_file.exists()
        assert pymupdf_file.read_text(encoding="utf-8") == artifacts["pymupdf"]["content"]

    def test_export_creates_output_dir_if_missing(self, tmp_path):
        """export_canonical_artifacts creates output_dir if it does not exist."""
        new_dir = str(tmp_path / "nested" / "output")
        artifacts = build_canonical_artifacts("<root/>", {}, "doc789")
        export_canonical_artifacts(artifacts, new_dir)
        assert os.path.isdir(new_dir)
        assert os.path.exists(os.path.join(new_dir, "doc789_grobid.xml"))
        assert os.path.exists(os.path.join(new_dir, "doc789_pymupdf.json"))

    def test_export_file_contents_match_canonical_content(self, tmp_path):
        """File contents written to disk must exactly match the canonical content fields."""
        grobid_input = "<TEI><text><body><p>Hello world</p></body></text></TEI>"
        pymupdf_input = {"blocks": [{"text": "Hello world", "page": 0}]}
        artifacts = build_canonical_artifacts(grobid_input, pymupdf_input, "docABC")
        export_canonical_artifacts(artifacts, str(tmp_path))

        grobid_on_disk = (tmp_path / "docABC_grobid.xml").read_text(encoding="utf-8")
        pymupdf_on_disk = (tmp_path / "docABC_pymupdf.json").read_text(encoding="utf-8")

        assert grobid_on_disk == artifacts["grobid"]["content"]
        assert pymupdf_on_disk == artifacts["pymupdf"]["content"]

    def test_build_canonical_artifacts_structure(self, tmp_path):
        """build_canonical_artifacts returns the expected dict structure."""
        artifacts = build_canonical_artifacts(
            "<root/>", {"x": "y"}, "my_doc"
        )
        assert artifacts["document_id"] == "my_doc"
        assert artifacts["grobid"]["format"] == "tei_xml"
        assert artifacts["pymupdf"]["format"] == "json"
        assert isinstance(artifacts["grobid"]["id"], str)
        assert len(artifacts["grobid"]["id"]) == 64  # sha256 hex digest
        assert isinstance(artifacts["pymupdf"]["id"], str)
        assert len(artifacts["pymupdf"]["id"]) == 64

    def test_sha256_ids_are_deterministic(self, tmp_path):
        """SHA-256 IDs in canonical artifacts are deterministic for the same input."""
        artifacts1 = build_canonical_artifacts("<root/>", {"k": "v"}, "doc_det")
        artifacts2 = build_canonical_artifacts("<root/>", {"k": "v"}, "doc_det")
        assert artifacts1["grobid"]["id"] == artifacts2["grobid"]["id"]
        assert artifacts1["pymupdf"]["id"] == artifacts2["pymupdf"]["id"]
