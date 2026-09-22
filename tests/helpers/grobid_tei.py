"""Shared helpers for the real GROBID 0.8.2 TEI fixtures.

Fixture provenance and the structural facts the fixtures encode live in
``tests/fixtures/grobid_tei/README.md``. This module provides:

* the fixture directory and a short-name -> file mapping (``biorxiv``, ``arxiv``, ``plosone``);
* loaders returning the raw XML text or a parsed ``xml.etree.ElementTree`` root;
* small TEI-namespace queries for figures (non-table) and tables;
* ``COORD_CASES``: a table of GROBID-format ``coords`` strings (single box, multi-box,
  multi-page, malformed, empty, ``None``) with the expected 1-based first-box page, for
  reuse by the coordinate-parser tests (risk-remediation tasks 7 and 8, Requirement 11.6).

GROBID emits ``coords`` as ``page,x,y,w,h`` boxes separated by ``;`` with a 1-based page.
Consumers that report 0-based pages should compare against
``CoordCase.expected_first_page_zero_based``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

FIXTURE_DIR: Path = Path(__file__).resolve().parents[1] / "fixtures" / "grobid_tei"

TEI_NS = "http://www.tei-c.org/ns/1.0"
TEI_NSMAP = {"tei": TEI_NS}

FIXTURE_FILES: dict[str, str] = {
    "biorxiv": "biorxiv_2020.03.24.004655.tei.xml",
    "arxiv": "arxiv_2003.10218.tei.xml",
    "plosone": "plosone_journal.pone.0230405.tei.xml",
}

# One GROBID box is "page,x,y,w,h"; several boxes are joined with ";".
_BOX = r"\d+,[\d.]+,[\d.]+,[\d.]+,[\d.]+"
GROBID_COORDS_RE = re.compile(rf"^{_BOX}(;{_BOX})*$")


def fixture_path(name: str) -> Path:
    """Return the path of a fixture by short name; raises ``KeyError`` for unknown names."""
    return FIXTURE_DIR / FIXTURE_FILES[name]


def load_tei_text(name: str) -> str:
    """Return the fixture's XML as text (UTF-8)."""
    return fixture_path(name).read_text(encoding="utf-8")


def load_tei_root(name: str) -> ET.Element:
    """Return the fixture's parsed ``<TEI>`` root element."""
    return ET.parse(fixture_path(name)).getroot()


def tei_figures(root: ET.Element) -> list[ET.Element]:
    """All ``<figure>`` elements that are not tables (``type`` != ``"table"``)."""
    return [el for el in root.iter(f"{{{TEI_NS}}}figure") if el.get("type") != "table"]


def tei_tables(root: ET.Element) -> list[ET.Element]:
    """All ``<figure type="table">`` elements (GROBID wraps tables this way)."""
    return [el for el in root.iter(f"{{{TEI_NS}}}figure") if el.get("type") == "table"]


@dataclass(frozen=True)
class CoordCase:
    """One ``coords`` input and the page a correct parser must report for its first box.

    ``expected_first_page`` is 1-based as GROBID emits it, or ``None`` when the input is
    absent or malformed and a parser must report "unknown" (Requirement 11.5).
    """

    label: str
    coords: Optional[str]
    expected_first_page: Optional[int]

    @property
    def well_formed(self) -> bool:
        return self.expected_first_page is not None

    @property
    def expected_first_page_zero_based(self) -> Optional[int]:
        if self.expected_first_page is None:
            return None
        return self.expected_first_page - 1


COORD_CASES: tuple[CoordCase, ...] = (
    CoordCase("single_box", "7,211.98,325.41,344.69,11.28", 7),
    CoordCase(
        "multi_box_same_page",
        "1,200.01,284.75,368.86,8.79;1,200.01,298.75,368.07,8.79",
        1,
    ),
    CoordCase(
        "multi_box_two_pages",
        "3,537.84,676.28,29.27,9.22;3,200.01,689.32,348.20,9.22;4,200.01,72.00,360.76,8.50",
        3,
    ),
    # Legacy hand-built fixture format (page;x0,y0,x1,y1) -- never emitted by GROBID.
    CoordCase("legacy_semicolon_format", "7;211.98,325.41", None),
    CoordCase("legacy_semicolon_full", "7;211.98,325.41,344.69,400.00", None),
    CoordCase("alpha", "abc", None),
    CoordCase("too_few_numbers", "1,2,3", None),
    CoordCase("empty", "", None),
    CoordCase("none", None, None),
)

WELL_FORMED_COORD_CASES: tuple[CoordCase, ...] = tuple(c for c in COORD_CASES if c.well_formed)
MALFORMED_COORD_CASES: tuple[CoordCase, ...] = tuple(c for c in COORD_CASES if not c.well_formed)
