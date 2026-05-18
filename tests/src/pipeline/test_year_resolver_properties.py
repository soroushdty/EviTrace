"""Property-based tests for publication-year multi-source resolver (Property 13).

**Validates: Requirements 8.2, 8.3, 8.5**

Property 13: For any PDF processed by the publication-year resolver, when TEI
metadata contains a year it SHALL be used with confidence `h`. When the year is
resolved from filename pattern only (no corroboration), confidence SHALL be `m`.
Every resolved year SHALL have a non-empty `provenance` field identifying the source.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from pipeline.evidence_index import (
    YearResolution,
    resolve_publication_year,
    _year_from_filename,
    _year_from_tei,
    _FILENAME_YEAR_RE,
)

_TEI_NS = "http://www.tei-c.org/ns/1.0"


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate valid 4-digit years in the 1900–2099 range
_valid_years = st.integers(min_value=1900, max_value=2099).map(str)

# Generate filename-safe author names
_author_names = st.from_regex(r"[A-Z][a-z]{2,10}", fullmatch=True)

# Generate filename separators
_separators = st.sampled_from(["_", "-", ".", " "])

# Generate filenames with year patterns (e.g., "Shahn_2015.pdf")
_filename_with_year = st.builds(
    lambda author, sep, year: f"{author}{sep}{year}.pdf",
    _author_names,
    _separators,
    _valid_years,
)

# Generate filenames WITHOUT year patterns
_filename_without_year = st.from_regex(
    r"[A-Za-z]{3,12}\.pdf", fullmatch=True
).filter(lambda name: not re.search(r"(19|20)\d{2}", name))

# Generate TEI XML with a publication year in the header
_tei_with_year = _valid_years.map(
    lambda year: f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Test Paper</title></titleStmt>
      <sourceDesc>
        <biblStruct>
          <monogr>
            <author><surname>Author</surname></author>
            <imprint><date when="{year}"/></imprint>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><p>Content.</p></body></text>
</TEI>"""
)

# Generate TEI XML WITHOUT a publication year
_tei_without_year = st.just(
    f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Test Paper</title></titleStmt>
      <sourceDesc>
        <biblStruct>
          <monogr>
            <author><surname>Author</surname></author>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><p>Content.</p></body></text>
</TEI>"""
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_fitz_no_year():
    """Create a mock fitz module that returns no year from PDF metadata."""
    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_doc.metadata = {"creationDate": "", "modDate": ""}
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_page = MagicMock()
    mock_page.get_text.return_value = "Some text without any year information."
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_fitz.open.return_value = mock_doc
    return mock_fitz


def _mock_fitz_with_metadata_year(year: str):
    """Create a mock fitz module that returns a year from PDF metadata."""
    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_doc.metadata = {"creationDate": f"D:{year}0101120000", "modDate": ""}
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_page = MagicMock()
    mock_page.get_text.return_value = "Some text without year."
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_fitz.open.return_value = mock_doc
    return mock_fitz


def _mock_fitz_with_first_page_year(year: str):
    """Create a mock fitz module that returns a year from first-page text."""
    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_doc.metadata = {"creationDate": "", "modDate": ""}
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_page = MagicMock()
    mock_page.get_text.return_value = f"Published in {year} by Author et al."
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_fitz.open.return_value = mock_doc
    return mock_fitz


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(tei_xml=_tei_with_year, filename=_filename_without_year)
@settings(max_examples=100)
def test_tei_year_used_with_confidence_h(tei_xml: str, filename: str, tmp_path_factory):
    """When TEI metadata includes a publication year, confidence SHALL be 'h'.

    **Validates: Requirements 8.2**
    """
    tmp_path = tmp_path_factory.mktemp("tei_year")
    pdf_path = tmp_path / filename
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    tei_root = ET.fromstring(tei_xml)

    # Mock fitz so PDF metadata/first-page don't interfere
    mock_fitz = _mock_fitz_no_year()
    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = resolve_publication_year(tei_root, pdf_path, filename)

    assert result.confidence == "h", (
        f"TEI year should yield confidence 'h', got '{result.confidence}'"
    )
    assert result.provenance == "tei_header", (
        f"TEI year provenance should be 'tei_header', got '{result.provenance}'"
    )
    # Year should be a valid 4-digit year
    assert re.fullmatch(r"(19|20)\d{2}", result.year), (
        f"TEI year should be a valid 4-digit year, got '{result.year}'"
    )


@given(tei_xml=_tei_with_year, filename=_filename_with_year)
@settings(max_examples=100)
def test_tei_year_takes_priority_over_filename(
    tei_xml: str, filename: str, tmp_path_factory
):
    """When both TEI and filename have years, TEI takes priority with confidence 'h'.

    **Validates: Requirements 8.2**
    """
    tmp_path = tmp_path_factory.mktemp("tei_priority")
    pdf_path = tmp_path / filename
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    tei_root = ET.fromstring(tei_xml)

    mock_fitz = _mock_fitz_no_year()
    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = resolve_publication_year(tei_root, pdf_path, filename)

    assert result.confidence == "h", (
        f"TEI year should yield confidence 'h' even with filename year, got '{result.confidence}'"
    )
    assert result.provenance == "tei_header", (
        f"TEI should take priority, got provenance '{result.provenance}'"
    )


@given(filename=_filename_with_year)
@settings(max_examples=100)
def test_filename_only_yields_confidence_m(filename: str, tmp_path_factory):
    """When year is resolved from filename only (no corroboration), confidence SHALL be 'm'.

    **Validates: Requirements 8.3**
    """
    tmp_path = tmp_path_factory.mktemp("filename_only")
    pdf_path = tmp_path / filename
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    # No TEI year
    tei_root = ET.fromstring(f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Test</title></titleStmt>
      <sourceDesc><biblStruct><monogr><author><surname>X</surname></author></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><p>Content.</p></body></text>
</TEI>""")

    # Mock fitz to return no year from PDF metadata or first page
    mock_fitz = _mock_fitz_no_year()
    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = resolve_publication_year(tei_root, pdf_path, filename)

    assert result.confidence == "m", (
        f"Filename-only year should yield confidence 'm', got '{result.confidence}'"
    )
    assert result.provenance == "filename_pattern", (
        f"Filename-only provenance should be 'filename_pattern', got '{result.provenance}'"
    )
    # Verify the year matches what's in the filename
    expected_year = _year_from_filename(filename)
    assert result.year == expected_year, (
        f"Year should match filename pattern: expected '{expected_year}', got '{result.year}'"
    )


@given(
    tei_xml=st.one_of(_tei_with_year, _tei_without_year),
    filename=st.one_of(_filename_with_year, _filename_without_year),
    pdf_meta_year=st.one_of(st.just(""), _valid_years),
    first_page_year=st.one_of(st.just(""), _valid_years),
)
@settings(max_examples=100)
def test_every_resolved_year_has_non_empty_provenance(
    tei_xml: str,
    filename: str,
    pdf_meta_year: str,
    first_page_year: str,
    tmp_path_factory,
):
    """Every resolved year (not 'nr') SHALL have a non-empty provenance field.

    **Validates: Requirements 8.5**
    """
    tmp_path = tmp_path_factory.mktemp("provenance")
    pdf_path = tmp_path / filename
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    tei_root = ET.fromstring(tei_xml)

    # Build mock fitz with the specified metadata/first-page year
    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    if pdf_meta_year:
        mock_doc.metadata = {"creationDate": f"D:{pdf_meta_year}0601", "modDate": ""}
    else:
        mock_doc.metadata = {"creationDate": "", "modDate": ""}
    mock_doc.__len__ = MagicMock(return_value=1)
    mock_page = MagicMock()
    if first_page_year:
        mock_page.get_text.return_value = f"Published {first_page_year} by Author."
    else:
        mock_page.get_text.return_value = "No year information here."
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    mock_fitz.open.return_value = mock_doc

    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = resolve_publication_year(tei_root, pdf_path, filename)

    if result.year != "nr":
        # Every resolved year must have non-empty provenance
        assert result.provenance != "", (
            f"Resolved year '{result.year}' has empty provenance"
        )
        # Provenance must be one of the known sources
        valid_provenances = {
            "tei_header", "pdf_metadata", "first_page_text", "filename_pattern"
        }
        assert result.provenance in valid_provenances, (
            f"Provenance '{result.provenance}' is not a recognized source"
        )
        # Confidence must be 'h' or 'm' for resolved years
        assert result.confidence in ("h", "m"), (
            f"Resolved year confidence should be 'h' or 'm', got '{result.confidence}'"
        )
    else:
        # When year is 'nr', confidence should be 'nr' and provenance empty
        assert result.confidence == "nr", (
            f"'nr' year should have confidence 'nr', got '{result.confidence}'"
        )
        assert result.provenance == "", (
            f"'nr' year should have empty provenance, got '{result.provenance}'"
        )


@given(year=_valid_years, filename=_filename_without_year)
@settings(max_examples=100)
def test_pdf_metadata_year_yields_confidence_h(
    year: str, filename: str, tmp_path_factory
):
    """When year comes from PDF metadata (no TEI), confidence SHALL be 'h'.

    **Validates: Requirements 8.2**
    """
    tmp_path = tmp_path_factory.mktemp("pdf_meta")
    pdf_path = tmp_path / filename
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    # No TEI year
    tei_root = ET.fromstring(f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Test</title></titleStmt>
      <sourceDesc><biblStruct><monogr><author><surname>X</surname></author></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><p>Content.</p></body></text>
</TEI>""")

    mock_fitz = _mock_fitz_with_metadata_year(year)
    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = resolve_publication_year(tei_root, pdf_path, filename)

    assert result.confidence == "h", (
        f"PDF metadata year should yield confidence 'h', got '{result.confidence}'"
    )
    assert result.provenance == "pdf_metadata", (
        f"PDF metadata provenance should be 'pdf_metadata', got '{result.provenance}'"
    )
    assert result.year == year, (
        f"Year should be '{year}', got '{result.year}'"
    )


@given(year=_valid_years, filename=_filename_without_year)
@settings(max_examples=100)
def test_first_page_year_without_corroboration_yields_confidence_m(
    year: str, filename: str, tmp_path_factory
):
    """When year comes from first-page text only (no filename match), confidence is 'm'.

    **Validates: Requirements 8.3**
    """
    tmp_path = tmp_path_factory.mktemp("first_page")
    pdf_path = tmp_path / filename
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    # No TEI year
    tei_root = ET.fromstring(f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Test</title></titleStmt>
      <sourceDesc><biblStruct><monogr><author><surname>X</surname></author></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><p>Content.</p></body></text>
</TEI>""")

    mock_fitz = _mock_fitz_with_first_page_year(year)
    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        result = resolve_publication_year(tei_root, pdf_path, filename)

    assert result.confidence == "m", (
        f"First-page year without corroboration should yield 'm', got '{result.confidence}'"
    )
    assert result.provenance == "first_page_text", (
        f"First-page provenance should be 'first_page_text', got '{result.provenance}'"
    )
    assert result.year == year, (
        f"Year should be '{year}', got '{result.year}'"
    )
