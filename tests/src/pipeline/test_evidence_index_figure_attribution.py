"""Figure/table section attribution in the evidence index.

risk-remediation task 8.1 (Requirements 7.1-7.5, 11.4; design ``FigureAttribution``):

* a figure or table is attributed to the heading of the **first body ``<div>``
  in document order that cites it** via ``<ref type="figure"|"table" target="#id">``;
* an uncited item, or a sentence in a head-less ``<div>``, gets the neutral
  ``"body"`` label whose section score is 0; the label is decided per ``<div>``
  and never inherited from a preceding ``<div>``;
* exactly one evidence item per ``<figure>``: ``<figure type="table">`` yields
  one ``table`` item (caption + rows, xpath from the figure's ``xml:id``), any
  other captioned ``<figure>`` yields one ``figure_caption`` item; caption-less
  figures are dropped as before;
* ``page`` comes from the figure's ``coords`` via the canonical parser.

Expected attributions for the real GROBID 0.8.2 fixtures are derived here from
the raw XML by an independent walk (``_expected_section_by_id``), not copied
from the implementation.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from pipeline.evidence_index import (
    _build_items_from_tei,
    _figure_section_map,
    _section_heading,
    _section_score,
)
from tests.helpers.grobid_tei import TEI_NS, load_tei_root, load_tei_text, tei_figures, tei_tables

_NS = f"{{{TEI_NS}}}"
_XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_XPATH_ID_RE = re.compile(r"^//\*\[@xml:id='([^']+)'\]$")


# ---------------------------------------------------------------------------
# Synthetic TEI helpers
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


def _body(body_xml: str) -> ET.Element:
    root = ET.fromstring(_wrap_body(body_xml))
    body = root.find(f".//{_NS}body")
    assert body is not None
    return body


def _by_text(items: list[dict], text: str) -> dict:
    return next(i for i in items if i["text"] == text)


def _figures(items: list[dict]) -> list[dict]:
    return [i for i in items if i["type"] == "figure_caption"]


def _tables(items: list[dict]) -> list[dict]:
    return [i for i in items if i["type"] == "table"]


# ---------------------------------------------------------------------------
# Independent derivation of the expected attribution from the raw fixture XML
# ---------------------------------------------------------------------------


def _expected_section_by_id(root: ET.Element) -> dict[str, str]:
    """``xml:id -> heading of the first citing <div>`` walked directly over the
    fixture XML. Uses only ``<div>``/``<head>``/``<ref>`` structure; a
    head-less ``<div>`` contributes the neutral label."""
    body = root.find(f".//{_NS}body")
    assert body is not None
    expected: dict[str, str] = {}
    for div in body.iter(f"{_NS}div"):
        head = div.find(f"./{_NS}head")
        heading = " ".join("".join(head.itertext()).split()) if head is not None else ""
        heading = heading or "body"
        for ref in div.iter(f"{_NS}ref"):
            if ref.get("type") not in ("figure", "table"):
                continue
            target = ref.get("target") or ""
            if not target.startswith("#"):
                continue
            expected.setdefault(target[1:], heading)
    return expected


def _captioned(fig: ET.Element) -> bool:
    desc = fig.find(f"./{_NS}figDesc")
    return desc is not None and bool("".join(desc.itertext()).strip())


# ---------------------------------------------------------------------------
# Unit-level contracts: _section_heading, _figure_section_map, _section_score
# ---------------------------------------------------------------------------


def test_section_heading_is_head_text_or_body():
    body = _body(
        """
      <div><head>  Materials and   Methods </head><p><s>x</s></p></div>
      <div><p><s>y</s></p></div>
      <div><head>   </head><p><s>z</s></p></div>
"""
    )
    divs = body.findall(f"./{_NS}div")
    assert _section_heading(divs[0]) == "Materials and Methods"
    assert _section_heading(divs[1]) == "body"
    assert _section_heading(divs[2]) == "body"


def test_figure_section_map_first_citing_div_wins_in_document_order():
    body = _body(
        """
      <div><head>Introduction</head>
        <p><s>See <ref type="figure" target="#fig_1">Fig. 2</ref>.</s></p>
      </div>
      <div><head>Results</head>
        <p><s>As in <ref type="figure" target="#fig_1">Fig. 2</ref> and
           <ref type="table" target="#tab_0">Table 1</ref> and
           <ref type="figure">Fig. 9</ref> and <ref type="bibr" target="#b3">[3]</ref>.</s></p>
      </div>
      <div><p><s>Uncited-heading section cites <ref type="figure" target="#fig_0">Fig. 1</ref>.</s></p></div>
"""
    )
    mapping = _figure_section_map(body)
    assert mapping == {"fig_1": "Introduction", "tab_0": "Results", "fig_0": "body"}


def test_body_label_has_neutral_section_score():
    assert _section_score("body") == 0


# ---------------------------------------------------------------------------
# Synthetic end-to-end: per-div reset, first-citing attribution, one item each
# ---------------------------------------------------------------------------


def test_headless_div_sentences_get_body_and_following_div_does_not_inherit():
    items = _items(
        """
      <div><head>Methods</head><p><s>Method sentence.</s></p></div>
      <div><p><s>Headless sentence.</s></p></div>
      <div><head>Results</head><p><s>Result sentence.</s></p></div>
      <div><p><s>Second headless sentence.</s></p></div>
"""
    )
    assert _by_text(items, "Method sentence.")["section_path"] == "Methods"
    assert _by_text(items, "Headless sentence.")["section_path"] == "body"
    assert _by_text(items, "Headless sentence.")["score"] == 0
    assert _by_text(items, "Result sentence.")["section_path"] == "Results"
    assert _by_text(items, "Second headless sentence.")["section_path"] == "body"
    # Sentence ids follow document order regardless of attribution.
    assert [i["id"] for i in items if i["type"] == "sentence" and i["section_path"] != "Metadata"] == [
        "S000001", "S000002", "S000003", "S000004",
    ]


def test_figure_cited_from_two_divs_is_attributed_to_the_first():
    items = _items(
        """
      <div><head>Introduction</head>
        <p><s>Overview in <ref type="figure" target="#fig_0">Figure 1</ref>.</s></p>
      </div>
      <div><head>Results</head>
        <p><s>Again <ref type="figure" target="#fig_0">Figure 1</ref>.</s></p>
      </div>
      <figure xml:id="fig_0" coords="5,10,20,30,40"><head>Figure 1</head><figDesc>Twice-cited caption.</figDesc></figure>
"""
    )
    fig = _by_text(items, "Twice-cited caption.")
    assert fig["type"] == "figure_caption"
    assert fig["section_path"] == "Introduction"
    assert fig["score"] == _section_score("Introduction") + 5
    assert fig["page"] == 5
    assert fig["xpath"] == "//*[@xml:id='fig_0']"


def test_uncited_figure_after_last_div_gets_body_not_last_heading():
    items = _items(
        """
      <div><head>Introduction</head><p><s>Intro.</s></p></div>
      <div><head>Discussion</head><p><s>Discussion text.</s></p></div>
      <figure xml:id="fig_0" coords="3,10,20,30,40"><figDesc>Uncited caption.</figDesc></figure>
"""
    )
    fig = _by_text(items, "Uncited caption.")
    assert fig["section_path"] == "body"
    assert fig["score"] == 5
    assert fig["page"] == 3


def test_table_figure_yields_exactly_one_table_item_with_caption_and_rows():
    items = _items(
        """
      <div><head>Calibration</head>
        <p><s>Parameters in <ref type="table" target="#tab_0">Table 1</ref>.</s></p>
      </div>
      <figure type="table" xml:id="tab_0" coords="14,72,125,470,60">
        <head>Table 1</head>
        <figDesc>Model parameters.</figDesc>
        <table><row><cell>beta</cell><cell>0.5</cell></row><row><cell>gamma</cell><cell>0.1</cell></row></table>
      </figure>
"""
    )
    tables = _tables(items)
    figures = _figures(items)
    assert len(tables) == 1
    assert figures == [], "a table's caption must not also produce a figure_caption item"
    table = tables[0]
    assert table["id"] == "T000001"
    assert "Model parameters." in table["text"]
    assert "beta | 0.5" in table["text"]
    assert "gamma | 0.1" in table["text"]
    assert table["section_path"] == "Calibration"
    assert table["score"] == _section_score("Calibration") + 10
    assert table["xpath"] == "//*[@xml:id='tab_0']"
    assert table["page"] == 14
    assert table["coords"] == pytest.approx([72.0, 125.0, 542.0, 185.0])


def test_table_figure_without_rows_still_yields_one_table_item_from_caption():
    items = _items(
        """
      <div><head>Results</head><p><s>Text.</s></p></div>
      <figure type="table" xml:id="tab_0" coords="2,1,1,1,1"><figDesc>Caption only.</figDesc></figure>
"""
    )
    assert [i["type"] for i in _tables(items) + _figures(items)] == ["table"]
    assert _tables(items)[0]["text"] == "Caption only."


def test_captionless_figure_is_dropped_and_id_less_captioned_figure_is_body():
    items = _items(
        """
      <div><head>Results</head><p><s>Text.</s></p></div>
      <figure coords="2,1,1,1,1"><graphic/></figure>
      <figure coords="3,1,1,1,1"><figDesc>No id but captioned.</figDesc></figure>
"""
    )
    figures = _figures(items)
    assert [f["text"] for f in figures] == ["No id but captioned."]
    assert figures[0]["section_path"] == "body"
    assert figures[0]["id"] == "F000001"
    assert figures[0]["page"] == 3


def test_figure_nested_inside_div_without_ref_is_still_emitted_as_body():
    # Real GROBID never nests <figure> under <div> (R7 probe), but hand-built
    # fixtures do; depth tolerance keeps them producing items, attributed by
    # the citation map only (no <ref> -> "body").
    items = _items(
        """
      <div><head>Methods</head>
        <p><s>Text.</s></p>
        <figure coords="1,1,1,1,1"><figDesc>Nested caption.</figDesc></figure>
        <figure type="table" coords="1,1,1,1,1"><table><row><cell>nested cell</cell></row></table></figure>
      </div>
"""
    )
    fig = _by_text(items, "Nested caption.")
    assert fig["type"] == "figure_caption"
    assert fig["section_path"] == "body"
    table = _by_text(items, "nested cell")
    assert table["type"] == "table"
    assert table["section_path"] == "body"
    assert table["page"] == 1


def test_figure_and_table_counters_are_independent_and_positional():
    items = _items(
        """
      <div><head>Results</head><p><s>Text.</s></p></div>
      <figure xml:id="fig_0" coords="1,1,1,1,1"><figDesc>A</figDesc></figure>
      <figure type="table" xml:id="tab_0" coords="1,1,1,1,1"><figDesc>T1</figDesc></figure>
      <figure xml:id="fig_1" coords="1,1,1,1,1"><figDesc>B</figDesc></figure>
      <figure type="table" xml:id="tab_1" coords="1,1,1,1,1"><figDesc>T2</figDesc></figure>
"""
    )
    assert [(i["id"], i["text"]) for i in _figures(items)] == [("F000001", "A"), ("F000002", "B")]
    assert [(i["id"], i["text"]) for i in _tables(items)] == [("T000001", "T1"), ("T000002", "T2")]


# ---------------------------------------------------------------------------
# Real GROBID 0.8.2 fixtures (7.1-7.5, 11.4)
# ---------------------------------------------------------------------------

# name -> (figure_caption items, table items) expected from the evidence index.
# Caption-less figures are dropped, so biorxiv's 12 figures yield 7 items.
EXPECTED_ITEM_COUNTS = {
    "arxiv": (10, 3),
    "biorxiv": (7, 0),
    "plosone": (9, 1),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_ITEM_COUNTS))
def test_real_fixture_item_counts_one_item_per_figure(name):
    root = load_tei_root(name)
    expected_figures, expected_tables = EXPECTED_ITEM_COUNTS[name]
    # Cross-check the expectation against the raw XML: captioned non-table
    # figures and all table-typed figures.
    assert sum(_captioned(f) for f in tei_figures(root)) == expected_figures
    assert len(tei_tables(root)) == expected_tables

    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    figures = _figures(items)
    tables = _tables(items)
    assert len(figures) == expected_figures
    assert len(tables) == expected_tables
    assert [f["id"] for f in figures] == [f"F{i:06d}" for i in range(1, expected_figures + 1)]
    assert [t["id"] for t in tables] == [f"T{i:06d}" for i in range(1, expected_tables + 1)]


@pytest.mark.parametrize("name", sorted(EXPECTED_ITEM_COUNTS))
def test_real_fixture_attribution_matches_first_citing_div(name):
    root = load_tei_root(name)
    expected = _expected_section_by_id(root)
    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")

    for item in _figures(items) + _tables(items):
        match = _XPATH_ID_RE.match(item["xpath"])
        if match is None:
            # Only an id-less figure lacks an addressable xpath; it can never
            # be cited, so it must carry the neutral label.
            assert item["section_path"] == "body"
            continue
        xml_id = match.group(1)
        assert item["section_path"] == expected.get(xml_id, "body"), xml_id

    if name == "arxiv":
        # Derived independently above; sanity-pin the headline expectation.
        assert expected["tab_0"] == "Calibration"
        assert _tables(items)[0]["xpath"] == "//*[@xml:id='tab_0']"
        assert _tables(items)[0]["section_path"] == "Calibration"
        assert any(f["section_path"] == "Baseline" for f in _figures(items))


@pytest.mark.parametrize("name", ["arxiv", "plosone"])
def test_real_fixture_spans_multiple_sections(name):
    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    sections = {i["section_path"] for i in _figures(items) + _tables(items)}
    assert len(sections) >= 2, sections


@pytest.mark.parametrize("name", sorted(EXPECTED_ITEM_COUNTS))
def test_real_fixture_uncited_items_get_body(name):
    root = load_tei_root(name)
    expected = _expected_section_by_id(root)
    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    uncited = [
        i for i in _figures(items) + _tables(items)
        if (m := _XPATH_ID_RE.match(i["xpath"])) is None or m.group(1) not in expected
    ]
    assert uncited, "every fixture has at least one uncited figure"
    assert {i["section_path"] for i in uncited} == {"body"}
    assert all(i["score"] in (5, 10) for i in uncited)


@pytest.mark.parametrize("name", sorted(EXPECTED_ITEM_COUNTS))
def test_real_fixture_figure_and_table_pages_come_from_figure_coords(name):
    root = load_tei_root(name)
    page_by_id = {
        fig.get(_XML_ID): int(fig.get("coords").split(";")[0].split(",")[0])
        for fig in root.iter(f"{_NS}figure")
        if fig.get(_XML_ID) and fig.get("coords")
    }
    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    for item in _figures(items) + _tables(items):
        assert item["page"] is not None, item["text"][:60]
        assert item["coords"] is not None
        match = _XPATH_ID_RE.match(item["xpath"])
        if match is not None:
            assert item["page"] == page_by_id[match.group(1)]


@pytest.mark.parametrize("name", ["arxiv", "plosone"])
def test_real_fixture_table_items_carry_caption_and_rows(name):
    root = load_tei_root(name)
    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    tables = _tables(items)
    assert tables
    for fig, item in zip(tei_tables(root), tables):
        assert item["xpath"] == f"//*[@xml:id='{fig.get(_XML_ID)}']"
        caption = " ".join("".join(fig.find(f"./{_NS}figDesc").itertext()).split())
        assert caption in item["text"]
        first_cell = next(
            " ".join("".join(c.itertext()).split())
            for c in fig.iter(f"{_NS}cell")
            if "".join(c.itertext()).strip()
        )
        assert first_cell in item["text"]
        assert item["text"] != caption, "row text must follow the caption"


@pytest.mark.parametrize("name", sorted(EXPECTED_ITEM_COUNTS))
def test_real_fixture_caption_sentences_inside_figures_are_body(name):
    # GROBID nests <div> inside <figDesc>; those head-less divs' sentences are
    # enumerated as body sentences and must carry the neutral label rather
    # than the heading of the last real section (7.4).
    root = load_tei_root(name)
    body = root.find(f".//{_NS}body")
    in_figure = {id(s) for fig in root.iter(f"{_NS}figure") for s in fig.iter(f"{_NS}s")}

    def _norm(elem: ET.Element) -> str:
        return " ".join("".join(elem.itertext()).split())

    caption_sentences = {_norm(s) for s in body.iter(f"{_NS}s") if id(s) in in_figure}
    outside = {_norm(s) for s in body.iter(f"{_NS}s") if id(s) not in in_figure}
    # Only texts that never occur outside a figure identify a caption sentence
    # unambiguously (fixtures repeat some caption text in the running text).
    caption_only = caption_sentences - outside - {""}
    assert caption_only
    items, _, _ = _build_items_from_tei(load_tei_text(name), name, "")
    hits = [i for i in items if i["type"] == "sentence" and i["text"] in caption_only]
    assert hits
    assert {i["section_path"] for i in hits} == {"body"}
