"""Unit tests for publication-year multi-source resolver.

Tests cover:
- TEI year extraction with confidence 'h'
- Filename pattern extraction (e.g., Shahn_2015.pdf → 2015, confidence 'm')
- 'nr' returned when no sources available
- PDF metadata extraction (mocked fitz)
- First-page text extraction (mocked fitz)
- Corroboration logic (filename matches first-page → confidence upgrade)

Requirements: 8.1, 8.6
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.evidence_index import (
    YearResolution,
    _year_from_filename,
    _year_from_tei,
    resolve_publication_year,
)


_TEI_NS = "http://www.tei-c.org/ns/1.0"


def _make_tei_with_year(year: str) -> ET.Element:
    """Build a minimal TEI XML root with a publication year in the imprint date."""
    xml_str = f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader>
    <fileDesc>
      <sourceDesc>
        <biblStruct>
          <monogr>
            <imprint>
              <date when="{year}"/>
            </imprint>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><div><p><s>Some text.</s></p></div></body></text>
</TEI>"""
    return ET.fromstring(xml_str)


def _make_tei_without_year() -> ET.Element:
    """Build a minimal TEI XML root with no date information."""
    xml_str = f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader>
    <fileDesc>
      <sourceDesc>
        <biblStruct>
          <monogr>
            <imprint/>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><div><p><s>Some text.</s></p></div></body></text>
</TEI>"""
    return ET.fromstring(xml_str)


# ---------------------------------------------------------------------------
# TEI year extraction tests
# ---------------------------------------------------------------------------


class TestYearFromTei:
    """Tests for _year_from_tei and TEI-based resolution."""

    def test_tei_year_extracted_with_confidence_h(self, tmp_path: Path):
        """TEI metadata year → confidence 'h', provenance 'tei_header'."""
        tei_root = _make_tei_with_year("2019")
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        result = resolve_publication_year(tei_root, pdf_path, "paper.pdf")

        assert result.year == "2019"
        assert result.confidence == "h"
        assert result.provenance == "tei_header"

    def test_tei_year_from_when_attribute(self):
        """_year_from_tei extracts year from date[@when] attribute."""
        tei_root = _make_tei_with_year("2021-03-15")
        year = _year_from_tei(tei_root)
        assert year == "2021"

    def test_tei_year_none_root_returns_empty(self):
        """_year_from_tei returns empty string when root is None."""
        assert _year_from_tei(None) == ""

    def test_tei_year_no_date_returns_empty(self):
        """_year_from_tei returns empty string when no date in TEI."""
        tei_root = _make_tei_without_year()
        assert _year_from_tei(tei_root) == ""

    def test_tei_year_full_date_string(self, tmp_path: Path):
        """TEI with full ISO date '2018-06-01' extracts year '2018'."""
        tei_root = _make_tei_with_year("2018-06-01")
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        result = resolve_publication_year(tei_root, pdf_path, "paper.pdf")

        assert result.year == "2018"
        assert result.confidence == "h"
        assert result.provenance == "tei_header"


# ---------------------------------------------------------------------------
# Filename pattern tests
# ---------------------------------------------------------------------------


class TestYearFromFilename:
    """Tests for _year_from_filename and filename-based resolution."""

    def test_filename_shahn_2015(self, tmp_path: Path):
        """Filename 'Shahn_2015.pdf' → year '2015', confidence 'm', provenance 'filename_pattern'."""
        pdf_path = tmp_path / "Shahn_2015.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        # No TEI, mock fitz to return nothing
        with patch.dict("sys.modules", {"fitz": MagicMock()}):
            # Make fitz.open return a doc with no useful metadata
            mock_fitz = MagicMock()
            mock_doc = MagicMock()
            mock_doc.metadata = {}
            mock_doc.__len__ = lambda self: 0
            mock_fitz.open.return_value = mock_doc
            with patch.dict("sys.modules", {"fitz": mock_fitz}):
                result = resolve_publication_year(None, pdf_path, "Shahn_2015.pdf")

        assert result.year == "2015"
        assert result.confidence == "m"
        assert result.provenance == "filename_pattern"

    def test_filename_with_hyphen_separator(self):
        """Filename 'Author-2020.pdf' extracts year '2020'."""
        assert _year_from_filename("Author-2020.pdf") == "2020"

    def test_filename_with_underscore_separator(self):
        """Filename 'Smith_2017_review.pdf' extracts year '2017'."""
        assert _year_from_filename("Smith_2017_review.pdf") == "2017"

    def test_filename_with_dot_separator(self):
        """Filename 'paper.2019.pdf' extracts year '2019'."""
        assert _year_from_filename("paper.2019.pdf") == "2019"

    def test_filename_no_year(self):
        """Filename without year pattern returns empty string."""
        assert _year_from_filename("paper_final.pdf") == ""

    def test_filename_year_at_start(self):
        """Filename starting with year after separator: '_2016_paper.pdf'."""
        assert _year_from_filename("_2016_paper.pdf") == "2016"

    def test_filename_invalid_century(self):
        """Filename with non-19xx/20xx year (e.g., '1850') returns empty."""
        assert _year_from_filename("paper_1850.pdf") == ""


# ---------------------------------------------------------------------------
# No sources available → 'nr'
# ---------------------------------------------------------------------------


class TestNoSourcesAvailable:
    """Tests for 'nr' returned when no year sources are available."""

    def test_nr_when_no_tei_no_pdf_no_filename(self, tmp_path: Path):
        """Returns 'nr' when TEI is None, PDF has no metadata, and filename has no year."""
        pdf_path = tmp_path / "paper_final.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        # Mock fitz to return no useful data
        mock_fitz = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {}
        mock_doc.__len__ = lambda self: 0
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = resolve_publication_year(None, pdf_path, "paper_final.pdf")

        assert result.year == "nr"
        assert result.confidence == "nr"
        assert result.provenance == ""

    def test_nr_when_tei_has_no_year_and_no_other_sources(self, tmp_path: Path):
        """Returns 'nr' when TEI exists but has no date, and no other sources."""
        tei_root = _make_tei_without_year()
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        mock_fitz = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {}
        mock_doc.__len__ = lambda self: 0
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = resolve_publication_year(tei_root, pdf_path, "paper.pdf")

        assert result.year == "nr"
        assert result.confidence == "nr"
        assert result.provenance == ""

    def test_nr_when_pdf_path_does_not_exist(self):
        """Returns 'nr' when PDF path doesn't exist and no TEI/filename year."""
        pdf_path = Path("/nonexistent/path/paper.pdf")
        result = resolve_publication_year(None, pdf_path, "paper.pdf")

        assert result.year == "nr"
        assert result.confidence == "nr"
        assert result.provenance == ""


# ---------------------------------------------------------------------------
# PDF metadata tests (mocked fitz)
# ---------------------------------------------------------------------------


class TestYearFromPdfMetadata:
    """Tests for PDF metadata extraction with mocked fitz."""

    def test_pdf_creation_date_extracted(self, tmp_path: Path):
        """PDF /CreationDate metadata → confidence 'h', provenance 'pdf_metadata'."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        mock_fitz = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {"creationDate": "D:20200315120000"}
        mock_doc.close = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = resolve_publication_year(None, pdf_path, "paper.pdf")

        assert result.year == "2020"
        assert result.confidence == "h"
        assert result.provenance == "pdf_metadata"

    def test_pdf_mod_date_fallback(self, tmp_path: Path):
        """PDF /ModDate used when /CreationDate has no year."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        mock_fitz = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {"creationDate": "", "modDate": "D:20170801"}
        mock_doc.close = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = resolve_publication_year(None, pdf_path, "paper.pdf")

        assert result.year == "2017"
        assert result.confidence == "h"
        assert result.provenance == "pdf_metadata"


# ---------------------------------------------------------------------------
# Priority and corroboration tests
# ---------------------------------------------------------------------------


class TestPriorityAndCorroboration:
    """Tests for priority chain ordering and corroboration logic."""

    def test_tei_takes_priority_over_filename(self, tmp_path: Path):
        """TEI year is preferred even when filename also has a year."""
        tei_root = _make_tei_with_year("2019")
        pdf_path = tmp_path / "Author_2020.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        result = resolve_publication_year(tei_root, pdf_path, "Author_2020.pdf")

        assert result.year == "2019"
        assert result.confidence == "h"
        assert result.provenance == "tei_header"

    def test_corroboration_upgrades_first_page_confidence(self, tmp_path: Path):
        """When filename year matches first-page year, confidence upgrades to 'h'."""
        pdf_path = tmp_path / "Smith_2016.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        # Mock fitz: no PDF metadata, but first page has year 2016
        mock_fitz = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {}
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Published in 2016 by Smith et al."
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.close = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = resolve_publication_year(None, pdf_path, "Smith_2016.pdf")

        assert result.year == "2016"
        assert result.confidence == "h"
        assert result.provenance == "first_page_text"

    def test_first_page_without_corroboration_gives_medium_confidence(self, tmp_path: Path):
        """First-page year without filename match → confidence 'm'."""
        pdf_path = tmp_path / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        mock_fitz = MagicMock()
        mock_doc = MagicMock()
        mock_doc.metadata = {}
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Published in 2016 by Smith et al."
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)
        mock_doc.close = MagicMock()
        mock_fitz.open.return_value = mock_doc

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = resolve_publication_year(None, pdf_path, "paper.pdf")

        assert result.year == "2016"
        assert result.confidence == "m"
        assert result.provenance == "first_page_text"

    def test_year_resolution_dataclass_fields(self):
        """YearResolution has the expected fields."""
        res = YearResolution(year="2021", confidence="h", provenance="tei_header")
        assert res.year == "2021"
        assert res.confidence == "h"
        assert res.provenance == "tei_header"
