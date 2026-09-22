"""Quality-control mirror of the GROBID ``coords`` grammar.

risk-remediation task 7.3 (Requirements 11.2, 11.5):

* ``_page_from_tei_coords`` is a MODULE-LEVEL function in
  ``quality_control.quality_control`` (hoisted from a closure) that parses
  GROBID's real grammar -- boxes separated by ``;``, each ``page,x,y,w,h`` --
  and returns the first box's page converted to 0-based.
* Absent, empty, or malformed input (including the legacy ``page;x0,y0,x1,y1``
  format) yields ``0`` and never raises.
* ``_extract_tei_payload`` routes GROBID-derived QC blocks from a real fixture to
  their real pages instead of page zero.

The cross-agreement test against ``pdf_extractor.extraction.GROBID.parse_tei_coords``
belongs to task 7.4 and is intentionally not here (``quality_control`` may not
import ``pdf_extractor``; the test file for 7.4 may).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from quality_control import quality_control as qc_module
from quality_control.quality_control import _extract_tei_payload, _page_from_tei_coords
from tests.helpers.grobid_tei import (
    COORD_CASES,
    MALFORMED_COORD_CASES,
    TEI_NS,
    WELL_FORMED_COORD_CASES,
    load_tei_root,
    load_tei_text,
)

_NS = f"{{{TEI_NS}}}"


def _oracle_page(coords: str) -> int:
    """Independent 0-based page of the first box, straight from the raw grammar."""
    return int(coords.split(";")[0].split(",")[0]) - 1


# ---------------------------------------------------------------------------
# Module-level hoist (design: CoordsParser QC mirror)
# ---------------------------------------------------------------------------


def test_page_from_tei_coords_is_module_level():
    assert callable(getattr(qc_module, "_page_from_tei_coords", None))
    assert _page_from_tei_coords.__qualname__ == "_page_from_tei_coords"


# ---------------------------------------------------------------------------
# Shared coordinate table (11.2, 11.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", COORD_CASES, ids=[c.label for c in COORD_CASES])
def test_page_from_tei_coords_matches_shared_table(case):
    expected = case.expected_first_page_zero_based
    assert _page_from_tei_coords(case.coords) == (expected if expected is not None else 0)


@pytest.mark.parametrize(
    "case", WELL_FORMED_COORD_CASES, ids=[c.label for c in WELL_FORMED_COORD_CASES]
)
def test_well_formed_cases_report_first_box_page_zero_based(case):
    page = _page_from_tei_coords(case.coords)
    assert page == _oracle_page(case.coords)
    assert page >= 0


def test_multi_page_value_reports_first_box_not_last():
    coords = "3,537.84,676.28,29.27,9.22;4,200.01,72.00,360.76,8.50"
    assert _page_from_tei_coords(coords) == 2


# ---------------------------------------------------------------------------
# Whitespace tolerance (design: "whitespace tolerated")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "coords",
    [
        " 7,211.98,325.41,344.69,11.28 ",
        "7, 211.98, 325.41, 344.69, 11.28",
        "7 ,211.98 ,325.41 ,344.69 ,11.28",
        "7,211.98,325.41,344.69,11.28 ; 7,200.01,298.75,368.07,8.79",
        "\t7,211.98,325.41,344.69,11.28\n",
    ],
    ids=["outer", "after_commas", "before_commas", "around_semicolon", "tabs_newlines"],
)
def test_whitespace_is_tolerated(coords):
    assert _page_from_tei_coords(coords) == 6


# ---------------------------------------------------------------------------
# Malformed / absent -> 0 without raising (11.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", MALFORMED_COORD_CASES, ids=[c.label for c in MALFORMED_COORD_CASES]
)
def test_malformed_table_cases_yield_zero(case):
    assert _page_from_tei_coords(case.coords) == 0


@pytest.mark.parametrize(
    "coords",
    [
        None,
        "",
        "   ",
        ";",
        "7;211.98,325.41,344.69,400.00",  # legacy grammar; legacy-grammar-negative-case
        "7,211.98,325.41,344.69",  # four numbers
        "7,211.98,325.41,344.69,11.28,99",  # six numbers
        "7,a,b,c,d",
        "x,1,2,3,4",
        "-1,1,2,3,4",
        "7.0,1,2,3,4",
        "٣,1,2,3,4",  # Arabic-Indic digit: isdigit() true, isascii() false
        "7,nan,1,2,3",
        "7,inf,1,2,3",
        "7,-inf,1,2,3",
        "7,211.98,325.41,344.69,11.28;",  # trailing empty box
        "7,211.98,325.41,344.69,11.28;bad",  # one good box, one malformed
        "7,211.98,325.41,344.69,11.28;8,1,2",  # second box too short
        "1e5,1,2,3,4",
    ],
)
def test_malformed_or_absent_yields_zero_without_raising(coords):
    assert _page_from_tei_coords(coords) == 0


def test_any_malformed_box_poisons_the_whole_value_even_when_first_is_good():
    good = "7,211.98,325.41,344.69,11.28"
    assert _page_from_tei_coords(good) == 6
    assert _page_from_tei_coords(good + ";oops") == 0


# ---------------------------------------------------------------------------
# Done-when: real fixture blocks land on their real pages
# ---------------------------------------------------------------------------


def _fixture_page_oracle(root: ET.Element) -> set[int]:
    """Pages derived independently from the raw ``coords`` attributes that
    ``_extract_tei_payload`` reads: abstract/body ``<p>``, body ``<head>``, and
    the ``<figure>`` wrapping each ``<figDesc>``."""
    pages: set[int] = set()
    for p in root.findall(f".//{_NS}abstract//{_NS}p"):
        if p.attrib.get("coords"):
            pages.add(_oracle_page(p.attrib["coords"]))
    body = root.find(f".//{_NS}body")
    assert body is not None
    for p in body.findall(f".//{_NS}p"):
        if p.attrib.get("coords"):
            pages.add(_oracle_page(p.attrib["coords"]))
    for head in body.findall(f".//{_NS}head"):
        if head.attrib.get("coords"):
            pages.add(_oracle_page(head.attrib["coords"]))
    for fig in body.findall(f".//{_NS}figure"):
        if fig.find(f".//{_NS}figDesc") is not None and fig.attrib.get("coords"):
            pages.add(_oracle_page(fig.attrib["coords"]))
    return pages


@pytest.mark.parametrize("fixture", ["plosone", "arxiv", "biorxiv"])
def test_real_fixture_blocks_land_on_real_pages(fixture):
    root = load_tei_root(fixture)
    oracle_pages = _fixture_page_oracle(root)
    assert len(oracle_pages) > 1, "fixture must span several pages for this test to mean anything"

    _, page_texts, blocks = _extract_tei_payload(load_tei_text(fixture))
    block_pages = {b["page_index"] for b in blocks}

    assert len(block_pages) > 1, f"all blocks on {sorted(block_pages)}: coords not parsed"
    # Every page the raw coords name must be reached; elements without coords
    # (or with a nested/uncoordinated figure) may additionally land on page 0.
    assert oracle_pages <= block_pages
    assert block_pages - oracle_pages <= {0}
    assert set(page_texts) == block_pages


def test_plosone_paragraph_blocks_carry_their_own_paragraph_page():
    """Every body sentence block is on the page its enclosing ``<p>`` coords name
    (sentences inherit the parent ``<p>`` page when they carry no coords of their
    own). Compared positionally: the fixture repeats identical sentence texts on
    different pages, so a text-keyed comparison would be ambiguous."""
    root = load_tei_root("plosone")
    body = root.find(f".//{_NS}body")
    assert body is not None

    # Oracle: (text, page) per body sentence in document order.
    oracle: list[tuple[str, int]] = []
    for p in body.findall(f".//{_NS}p"):
        coords = p.attrib.get("coords")
        expected = _oracle_page(coords) if coords else 0
        for s in p.findall(f"{_NS}s"):
            assert not s.attrib.get("coords"), "fixture <s> should carry no coords"
            text = " ".join("".join(s.itertext()).split())
            if text:
                oracle.append((text, expected))
    assert len(oracle) > 100

    n_abstract = sum(
        1 for p in root.findall(f".//{_NS}abstract//{_NS}p") if "".join(p.itertext()).strip()
    )
    _, _, blocks = _extract_tei_payload(load_tei_text("plosone"))
    sentence_blocks = blocks[n_abstract : n_abstract + len(oracle)]
    assert [b["text"] for b in sentence_blocks] == [t for t, _ in oracle]
    assert [(b["text"], b["page_index"]) for b in sentence_blocks] == oracle
    assert len({page for _, page in oracle}) > 1


def test_plosone_heading_blocks_carry_head_page():
    root = load_tei_root("plosone")
    body = root.find(f".//{_NS}body")
    assert body is not None
    _, _, blocks = _extract_tei_payload(load_tei_text("plosone"))
    page_by_text = {b["text"]: b["page_index"] for b in blocks}

    checked = 0
    for head in body.findall(f".//{_NS}head"):
        coords = head.attrib.get("coords")
        text = " ".join("".join(head.itertext()).split())
        if coords and text in page_by_text:
            assert page_by_text[text] == _oracle_page(coords), text
            checked += 1
    assert checked > 5


def test_synthetic_sentence_inherits_parent_paragraph_page():
    tei = f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{TEI_NS}">
  <teiHeader><profileDesc><abstract><div><p coords="1,1,1,1,1">Abstract text.</p></div></abstract></profileDesc></teiHeader>
  <text><body>
    <div>
      <head coords="2,50,50,200,20">Methods</head>
      <p coords="3,50,100,400,40;4,50,100,400,40"><s>Sentence one.</s><s coords="5,1,1,1,1">Sentence two.</s></p>
      <p><s>Orphan sentence.</s></p>
    </div>
    <figure coords="6,10,10,300,200"><head>Figure 1</head><figDesc>A caption.</figDesc></figure>
  </body></text>
</TEI>"""
    _, page_texts, blocks = _extract_tei_payload(tei)
    page_by_text = {b["text"]: b["page_index"] for b in blocks}
    assert page_by_text["Abstract text."] == 0
    assert page_by_text["Methods"] == 1
    assert page_by_text["Sentence one."] == 2  # inherited from parent <p>, first box
    assert page_by_text["Sentence two."] == 4  # own coords win over parent
    assert page_by_text["Orphan sentence."] == 0  # no coords anywhere -> 0
    assert page_by_text["A caption."] == 5
    assert set(page_texts) == {0, 1, 2, 4, 5}
