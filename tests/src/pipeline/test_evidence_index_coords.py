"""Evidence-index coordinate parsing and sentence page inheritance.

risk-remediation task 7.2 (Requirements 11.2, 11.3, 11.5):

* ``_parse_coords`` delegates to the canonical GROBID parser
  (``pdf_extractor.extraction.GROBID.parse_tei_coords``) and keeps the page
  1-based as the evidence index always has; ``coords`` is the ``[x0, y0, x1, y1]``
  union of the first page's boxes (mirrors ``GROBID._parse_coords``).
* A ``<s>`` without its own ``coords`` inherits the page of its enclosing ``<p>``.
* Absent or malformed coordinates yield ``page None`` / ``coords None`` and the
  item is still emitted.
"""

from __future__ import annotations

import pytest

from pipeline.evidence_index import _build_items_from_tei, _parse_coords
from tests.helpers.grobid_tei import (
    COORD_CASES,
    MALFORMED_COORD_CASES,
    TEI_NS,
    WELL_FORMED_COORD_CASES,
    load_tei_root,
    load_tei_text,
)

_NS = f"{{{TEI_NS}}}"


# ---------------------------------------------------------------------------
# _parse_coords over the shared coordinate table (11.2, 11.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", COORD_CASES, ids=[c.label for c in COORD_CASES])
def test_parse_coords_reports_first_box_page_one_based(case):
    loc = _parse_coords(case.coords)
    assert set(loc) == {"page", "coords"}
    assert loc["page"] == case.expected_first_page


@pytest.mark.parametrize(
    "case", WELL_FORMED_COORD_CASES, ids=[c.label for c in WELL_FORMED_COORD_CASES]
)
def test_parse_coords_well_formed_yields_four_number_bbox(case):
    loc = _parse_coords(case.coords)
    assert loc["page"] is not None
    assert isinstance(loc["coords"], list)
    assert len(loc["coords"]) == 4
    assert all(isinstance(v, float) for v in loc["coords"])
    x0, y0, x1, y1 = loc["coords"]
    assert x1 >= x0 and y1 >= y0


@pytest.mark.parametrize(
    "case", MALFORMED_COORD_CASES, ids=[c.label for c in MALFORMED_COORD_CASES]
)
def test_parse_coords_malformed_or_absent_is_unknown(case):
    assert _parse_coords(case.coords) == {"page": None, "coords": None}


def test_parse_coords_single_box_converts_wh_to_x1y1():
    # page,x,y,w,h -> [x, y, x+w, y+h]
    loc = _parse_coords("7,211.98,325.41,344.69,11.28")
    assert loc["page"] == 7
    assert loc["coords"] == pytest.approx([211.98, 325.41, 211.98 + 344.69, 325.41 + 11.28])


def test_parse_coords_unions_boxes_on_first_page_only():
    # Two boxes on page 3 and one on page 4: page is 3, bbox is the union of
    # the page-3 boxes only (same rule as GROBID._parse_coords).
    loc = _parse_coords("3,100,600,50,10;3,100,620,80,10;4,10,10,10,10")
    assert loc["page"] == 3
    assert loc["coords"] == pytest.approx([100.0, 600.0, 180.0, 630.0])


def test_parse_coords_legacy_fixture_grammar_is_rejected():
    # The old hand-built "page;x0,y0,x1,y1" strings must no longer parse (11.6).
    assert _parse_coords("1;10,20,30,40") == {"page": None, "coords": None}  # legacy-grammar-negative-case


# ---------------------------------------------------------------------------
# Sentence page inheritance on synthetic TEI (11.3, 11.4, 11.5)
# ---------------------------------------------------------------------------


def _wrap_body(body_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Synthetic</title></titleStmt>
      <sourceDesc><biblStruct><monogr><author><surname>Doe</surname></author><imprint><date when="2020"/></imprint></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
{body_xml}
    </body>
  </text>
</TEI>
"""


def _items(body_xml: str) -> list[dict]:
    items, _, _ = _build_items_from_tei(_wrap_body(body_xml), "paper", "")
    return items


def _by_text(items: list[dict], text: str) -> dict:
    return next(i for i in items if i["text"] == text)


def test_sentence_without_coords_inherits_paragraph_page():
    items = _items(
        """
      <div>
        <head>Methods</head>
        <p coords="2,100,200,300,10;2,100,212,300,10">
          <s>First sentence.</s>
          <s>Second sentence.</s>
        </p>
      </div>
"""
    )
    first = _by_text(items, "First sentence.")
    second = _by_text(items, "Second sentence.")
    assert first["page"] == 2
    assert second["page"] == 2
    assert first["coords"] == pytest.approx([100.0, 200.0, 400.0, 222.0])
    assert second["coords"] == first["coords"]


def test_sentence_own_coords_win_over_paragraph():
    items = _items(
        """
      <div>
        <head>Methods</head>
        <p coords="2,100,200,300,10">
          <s coords="5,10,20,30,40">Own coords sentence.</s>
          <s>Inherits sentence.</s>
        </p>
      </div>
"""
    )
    own = _by_text(items, "Own coords sentence.")
    inherits = _by_text(items, "Inherits sentence.")
    assert own["page"] == 5
    assert own["coords"] == pytest.approx([10.0, 20.0, 40.0, 60.0])
    assert inherits["page"] == 2


def test_sentence_in_paragraph_without_coords_has_unknown_page_and_is_emitted():
    items = _items(
        """
      <div>
        <head>Methods</head>
        <p><s>No coords anywhere.</s></p>
      </div>
"""
    )
    item = _by_text(items, "No coords anywhere.")
    assert item["page"] is None
    assert item["coords"] is None


def test_malformed_paragraph_coords_yield_unknown_page_and_indexing_continues():
    items = _items(
        """
      <div>
        <head>Methods</head>
        <p coords="garbage"><s>Malformed paragraph.</s></p>
        <p coords="1;10,20,30,40"><s>Legacy grammar paragraph.</s></p><!-- legacy-grammar-negative-case -->
        <p coords="3,10,20,30,40"><s>Good paragraph.</s></p>
      </div>
"""
    )
    assert _by_text(items, "Malformed paragraph.")["page"] is None
    assert _by_text(items, "Legacy grammar paragraph.")["page"] is None
    assert _by_text(items, "Good paragraph.")["page"] == 3
    texts = [i["text"] for i in items if i["type"] == "sentence"]
    assert texts[:3] == ["Malformed paragraph.", "Legacy grammar paragraph.", "Good paragraph."]


def test_paragraph_without_sentences_uses_its_own_coords():
    items = _items(
        """
      <div>
        <head>Methods</head>
        <p coords="4,10,20,30,40">Sentence-less paragraph.</p>
      </div>
"""
    )
    item = _by_text(items, "Sentence-less paragraph.")
    assert item["page"] == 4
    assert item["coords"] == pytest.approx([10.0, 20.0, 40.0, 60.0])


def test_figure_with_coords_carries_page_and_malformed_figure_is_still_emitted():
    items = _items(
        """
      <div>
        <head>Results</head>
        <p coords="1,10,20,30,40"><s>Body.</s></p>
      </div>
      <figure xml:id="fig_0" coords="6,50,60,200,100"><head>Figure 1</head><figDesc>Good figure caption.</figDesc></figure>
      <figure xml:id="fig_1" coords="not,a,box"><head>Figure 2</head><figDesc>Malformed figure caption.</figDesc></figure>
      <figure xml:id="fig_2"><head>Figure 3</head><figDesc>Coordless figure caption.</figDesc></figure>
"""
    )
    good = _by_text(items, "Good figure caption.")
    bad = _by_text(items, "Malformed figure caption.")
    none = _by_text(items, "Coordless figure caption.")
    assert good["type"] == "figure_caption"
    assert good["page"] == 6
    assert good["coords"] == pytest.approx([50.0, 60.0, 250.0, 160.0])
    assert bad["page"] is None and bad["coords"] is None
    assert none["page"] is None and none["coords"] is None


def test_abstract_paragraph_page_comes_from_its_coords():
    tei = _wrap_body(
        """
      <div><head>Intro</head><p coords="2,1,1,1,1"><s>Body.</s></p></div>
"""
    ).replace(
        "<text>",
        '<text><front><abstract><p coords="1,72,100,400,30">Abstract text.</p></abstract></front>',
        1,
    )
    items, _, _ = _build_items_from_tei(tei, "paper", "")
    abstract = _by_text(items, "Abstract text.")
    assert abstract["section_path"] == "Abstract"
    assert abstract["page"] == 1


# ---------------------------------------------------------------------------
# Real GROBID 0.8.2 fixtures (11.3, 11.4, 11.6)
# ---------------------------------------------------------------------------


def _expected_sentence_pages(name: str) -> list[tuple[str, int | None]]:
    """``(text, page)`` for every body ``<s>`` in document order.

    ``page`` is the first-box page of the enclosing ``<p>``'s coords (the
    fixture ``<s>`` elements carry none), or ``None`` when the paragraph has
    no coords.
    """
    root = load_tei_root(name)
    body = root.find(f".//{_NS}body")
    expected: list[tuple[str, int | None]] = []
    for div in body.findall(f".//{_NS}div"):
        for p in div.findall(f".//{_NS}p"):
            coords = p.get("coords")
            page = int(coords.split(";")[0].split(",")[0]) if coords else None
            for s in p.findall(f".//{_NS}s"):
                text = " ".join("".join(s.itertext()).split())
                if text:
                    expected.append((text, page))
    return expected


@pytest.mark.parametrize("name", ["arxiv", "biorxiv", "plosone"])
def test_real_fixture_sentences_inside_coords_paragraphs_have_pages(name):
    expected = _expected_sentence_pages(name)
    with_coords = [e for e in expected if e[1] is not None]
    assert with_coords, "fixture must contain coords-bearing paragraphs with sentences"

    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    body_sentences = [
        i for i in items
        if i["type"] == "sentence" and i["section_path"] not in ("Abstract", "Metadata")
    ]
    # The evidence index emits body sentences in document order, one per <s>.
    assert [i["text"] for i in body_sentences] == [t for t, _ in expected]
    for item, (_, page) in zip(body_sentences, expected):
        assert item["page"] == page, item["text"][:60]
        assert (item["coords"] is not None) == (page is not None)
    # No sentence inside a coords-bearing paragraph is left without a page.
    assert all(i["page"] is not None for i, (_, p) in zip(body_sentences, expected) if p is not None)


@pytest.mark.parametrize("name", ["arxiv", "biorxiv", "plosone"])
def test_real_fixture_figures_with_coords_have_pages(name):
    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    figures = [i for i in items if i["type"] == "figure_caption"]
    assert figures
    for fig in figures:
        assert fig["page"] is not None, fig["text"][:60]
        assert fig["coords"] is not None
