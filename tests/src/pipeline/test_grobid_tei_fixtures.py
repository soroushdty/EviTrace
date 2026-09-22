"""Smoke tests for the real GROBID 0.8.2 TEI fixtures and their shared helpers.

Guards the structural facts ``tests/fixtures/grobid_tei/`` exists to encode
(risk-remediation Requirement 11.6): figures and tables are ``<body>`` siblings,
tables are ``<figure type="table">``, and ``coords`` use GROBID's real
``page,x,y,w,h[;...]`` grammar. The counts asserted here were measured against
the checked-in files on 2026-09-21 and match ``tests/fixtures/grobid_tei/README.md``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from tests.helpers.grobid_tei import (
    COORD_CASES,
    FIXTURE_DIR,
    FIXTURE_FILES,
    GROBID_COORDS_RE,
    TEI_NS,
    CoordCase,
    fixture_path,
    load_tei_root,
    load_tei_text,
    tei_figures,
    tei_tables,
)

# name -> (figures excluding tables, caption-less figures, tables)
EXPECTED_COUNTS = {
    "biorxiv": (12, 5, 0),
    "arxiv": (10, 0, 3),
    "plosone": (9, 0, 1),
}

FIGURE_TAG = f"{{{TEI_NS}}}figure"
BODY_TAG = f"{{{TEI_NS}}}body"
FIGDESC_TAG = f"{{{TEI_NS}}}figDesc"


def test_fixture_directory_and_files_exist():
    assert FIXTURE_DIR.is_dir()
    assert set(FIXTURE_FILES) == set(EXPECTED_COUNTS)
    for name in FIXTURE_FILES:
        path = fixture_path(name)
        assert path.parent == FIXTURE_DIR
        assert path.is_file(), path


def test_unknown_fixture_name_is_rejected():
    with pytest.raises(KeyError):
        fixture_path("not-a-fixture")


@pytest.mark.parametrize("name", sorted(EXPECTED_COUNTS))
def test_text_and_root_loaders_agree(name):
    text = load_tei_text(name)
    root = load_tei_root(name)
    assert text.lstrip().startswith("<?xml")
    assert root.tag == f"{{{TEI_NS}}}TEI"
    assert ET.fromstring(text).tag == root.tag


@pytest.mark.parametrize("name", sorted(EXPECTED_COUNTS))
def test_figure_and_table_counts_match_readme(name):
    root = load_tei_root(name)
    expected_figures, expected_captionless, expected_tables = EXPECTED_COUNTS[name]

    figures = tei_figures(root)
    tables = tei_tables(root)
    assert len(figures) == expected_figures
    assert len(tables) == expected_tables
    assert all(fig.get("type") != "table" for fig in figures)
    assert all(tab.get("type") == "table" for tab in tables)

    def _captionless(fig: ET.Element) -> bool:
        desc = fig.find(FIGDESC_TAG)
        return desc is None or not "".join(desc.itertext()).strip()

    assert sum(_captionless(fig) for fig in figures) == expected_captionless

    # The helpers partition the full <figure> set.
    all_figures = root.findall(f".//{FIGURE_TAG}")
    assert len(all_figures) == expected_figures + expected_tables


@pytest.mark.parametrize("name", sorted(EXPECTED_COUNTS))
def test_every_figure_is_a_direct_child_of_body(name):
    root = load_tei_root(name)
    body = root.find(f".//{BODY_TAG}")
    assert body is not None
    direct_children = {id(child) for child in body}
    all_figures = root.findall(f".//{FIGURE_TAG}")
    assert all_figures, "fixture has no <figure> elements"
    nested = [fig for fig in all_figures if id(fig) not in direct_children]
    assert nested == [], f"{len(nested)} <figure> elements are nested below <body>"


@pytest.mark.parametrize("name", sorted(EXPECTED_COUNTS))
def test_coords_attributes_present_and_use_grobid_grammar(name):
    root = load_tei_root(name)
    coords = [el.get("coords") for el in root.iter() if el.get("coords")]
    assert coords, "fixture carries no coords attributes"
    bad = [c for c in coords if not GROBID_COORDS_RE.match(c)]
    assert bad == [], f"coords not in GROBID grammar: {bad[:3]}"
    # No fixture may carry the legacy page;x0,y0,x1,y1 form.
    assert not any(c.split(";")[0].count(",") == 0 for c in coords)
    # Every figure/table in the real output carries coordinates.
    assert all(fig.get("coords") for fig in root.findall(f".//{FIGURE_TAG}"))


def test_coord_table_covers_required_shapes():
    labels = {case.label for case in COORD_CASES}
    required = {
        "single_box",
        "multi_box_same_page",
        "multi_box_two_pages",
        "legacy_semicolon_format",
        "alpha",
        "too_few_numbers",
        "empty",
        "none",
    }
    assert required <= labels, required - labels
    assert len(labels) == len(COORD_CASES), "duplicate labels in COORD_CASES"
    assert all(isinstance(case, CoordCase) for case in COORD_CASES)


@pytest.mark.parametrize("case", COORD_CASES, ids=lambda c: c.label)
def test_coord_table_is_internally_consistent(case: CoordCase):
    if case.well_formed:
        assert isinstance(case.coords, str)
        assert GROBID_COORDS_RE.match(case.coords)
        first_token = case.coords.split(";")[0].split(",")[0]
        assert case.expected_first_page == int(first_token)
        assert case.expected_first_page >= 1
        assert case.expected_first_page_zero_based == case.expected_first_page - 1
    else:
        assert case.expected_first_page is None
        assert case.expected_first_page_zero_based is None
        if case.coords:
            assert not GROBID_COORDS_RE.match(case.coords)


def test_coord_table_has_a_multi_page_entry():
    case = next(c for c in COORD_CASES if c.label == "multi_box_two_pages")
    pages = {box.split(",")[0] for box in case.coords.split(";")}
    assert len(pages) == 2
    assert case.expected_first_page == int(case.coords.split(",")[0])
