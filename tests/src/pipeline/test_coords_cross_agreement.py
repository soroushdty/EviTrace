"""Cross-agreement of the three GROBID ``coords`` consumers, plus the 11.6 grammar guard.

risk-remediation task 7.4 (Requirements 11.2, 11.5, 11.6; design "CoordsParser"
validation bullet).

Three consumers parse GROBID's ``page,x,y,w,h;...`` grammar under different page bases:

* ``pdf_extractor.extraction.GROBID.parse_tei_coords`` -- canonical; ``CoordBox.page``
  is 1-based as emitted; ``[]`` on absent/malformed input.
* ``pdf_extractor.extraction.GROBID._parse_coords`` -- retained interface; first box
  page **0-based**; ``None`` on absent/malformed input.
* ``pipeline.evidence_index._parse_coords`` -- ``{"page": 1-based | None, ...}``.
* ``quality_control.quality_control._page_from_tei_coords`` -- dependency-safe mirror
  (``quality_control`` may not import ``pdf_extractor``); first box page **0-based**;
  ``0`` on absent/malformed input.

This file lives under ``tests/src/pipeline`` because ``pipeline`` is the only package
allowed to import both ``pdf_extractor`` and ``quality_control``.

Known deliberate divergence: a page token ``0`` (never emitted by GROBID) yields QC
``0`` (clamped) but GROBID ``_parse_coords`` ``-1``. The shared table therefore carries
no page-0 input and this file does not add one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pdf_extractor.extraction import GROBID as grobid_mod
from pdf_extractor.extraction.GROBID import parse_tei_coords
from pipeline import evidence_index
from quality_control.quality_control import _page_from_tei_coords
from tests.helpers.grobid_tei import (
    COORD_CASES,
    FIXTURE_FILES,
    MALFORMED_COORD_CASES,
    WELL_FORMED_COORD_CASES,
    CoordCase,
    load_tei_root,
)

_TESTS_ROOT = Path(__file__).resolve().parents[2]


def _grobid_zero_based(coords: str | None) -> int:
    """GROBID ``_parse_coords`` first page under the QC convention (0 when unparsable)."""
    parsed = grobid_mod._parse_coords(coords)
    return parsed[0] if parsed is not None else 0


# ---------------------------------------------------------------------------
# (1) Shared table: QC mirror vs GROBID 0-based vs canonical 1-based (11.2, 11.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", COORD_CASES, ids=[c.label for c in COORD_CASES])
def test_qc_mirror_agrees_with_grobid_zero_based_page(case: CoordCase):
    """Identical strings -> identical 0-based first page from both 0-based consumers."""
    expected = case.expected_first_page_zero_based or 0
    qc_page = _page_from_tei_coords(case.coords)
    grobid_page = _grobid_zero_based(case.coords)
    assert qc_page == grobid_page, (case.label, qc_page, grobid_page)
    assert qc_page == expected, (case.label, qc_page, expected)


@pytest.mark.parametrize(
    "case", WELL_FORMED_COORD_CASES, ids=[c.label for c in WELL_FORMED_COORD_CASES]
)
def test_canonical_first_box_page_is_zero_based_page_plus_one(case: CoordCase):
    boxes = parse_tei_coords(case.coords)
    assert boxes, case.label
    zero_based = _page_from_tei_coords(case.coords)
    assert boxes[0].page == zero_based + 1
    assert boxes[0].page == case.expected_first_page


@pytest.mark.parametrize(
    "case", WELL_FORMED_COORD_CASES, ids=[c.label for c in WELL_FORMED_COORD_CASES]
)
def test_evidence_index_page_matches_canonical_one_based_page(case: CoordCase):
    loc = evidence_index._parse_coords(case.coords)
    assert loc["page"] == parse_tei_coords(case.coords)[0].page
    assert loc["page"] == _page_from_tei_coords(case.coords) + 1


@pytest.mark.parametrize(
    "case", MALFORMED_COORD_CASES, ids=[c.label for c in MALFORMED_COORD_CASES]
)
def test_malformed_input_is_unknown_in_every_consumer(case: CoordCase):
    """11.5: absent/malformed -> [] / None / page None / 0, consistently."""
    assert parse_tei_coords(case.coords) == []
    assert grobid_mod._parse_coords(case.coords) is None
    assert evidence_index._parse_coords(case.coords)["page"] is None
    assert _page_from_tei_coords(case.coords) == 0


# Whitespace tolerance is a parser property, not something GROBID emits, so these
# cases stay local: ``GROBID_COORDS_RE`` (the fixture-grammar guard) is deliberately
# strict and the shared ``COORD_CASES`` table must keep matching it.
@pytest.mark.parametrize(
    "coords, expected_one_based",
    [
        (" 7,211.98,325.41,344.69,11.28", 7),
        ("7,211.98,325.41,344.69,11.28 ", 7),
        ("7, 211.98, 325.41, 344.69, 11.28", 7),
        (" 3,1,2,3,4 ; 4,5,6,7,8 ", 3),
        ("\t12,1,2,3,4\n", 12),
    ],
)
def test_whitespace_bearing_strings_agree_across_consumers(coords, expected_one_based):
    canonical = parse_tei_coords(coords)
    assert canonical and canonical[0].page == expected_one_based
    assert _grobid_zero_based(coords) == expected_one_based - 1
    assert _page_from_tei_coords(coords) == expected_one_based - 1
    assert evidence_index._parse_coords(coords)["page"] == expected_one_based


# ---------------------------------------------------------------------------
# (2) Every coords attribute in the three real fixtures (11.2, 11.6)
# ---------------------------------------------------------------------------


def _fixture_coords(name: str) -> list[str]:
    root = load_tei_root(name)
    return [el.get("coords") for el in root.iter() if el.get("coords")]


@pytest.mark.parametrize("name", sorted(FIXTURE_FILES))
def test_fixture_coords_agree_across_all_consumers(name: str):
    coords = _fixture_coords(name)
    assert len(coords) >= 50, f"{name}: only {len(coords)} coords attributes"

    disagreements: list[tuple[str, int, int, int | None, int | None]] = []
    for c in coords:
        canonical = parse_tei_coords(c)
        assert canonical, f"{name}: real GROBID coords rejected by canonical parser: {c!r}"
        canonical_page = canonical[0].page
        grobid_page = _grobid_zero_based(c)
        qc_page = _page_from_tei_coords(c)
        ei_page = evidence_index._parse_coords(c)["page"]
        raw_first_page = int(c.split(";")[0].split(",")[0])
        agree = (
            qc_page == grobid_page
            and canonical_page == qc_page + 1
            and ei_page == canonical_page
            and canonical_page == raw_first_page
            and canonical_page >= 1
        )
        if not agree:
            disagreements.append((c, qc_page, grobid_page, canonical_page, ei_page))

    assert disagreements == [], (
        f"{name}: {len(disagreements)} disagreement(s); first: {disagreements[:3]}"
    )


def test_fixtures_together_cover_several_hundred_coords():
    total = sum(len(_fixture_coords(name)) for name in FIXTURE_FILES)
    assert total >= 300, total


# ---------------------------------------------------------------------------
# (3) 11.6 grammar guard: no test under tests/ asserts pages against the legacy
#     ``page;x0,y0,x1,y1`` form unless the line is an explicit negative case
# ---------------------------------------------------------------------------

LEGACY_NEGATIVE_MARKER = "legacy-grammar-negative-case"
_THIS_FILE = Path(__file__).resolve()

# A TEI attribute in the legacy form, in a Python string or an XML/f-string body.
_LEGACY_ATTRIBUTE_RE = re.compile(r"""coords=\\?["']\s*\d+\s*;""")
# A bare Python string literal in the legacy form ("7;211.98,325.41,...").
_LEGACY_LITERAL_RE = re.compile(r"""["']\s*\d+\s*;\s*[\d.]+\s*,\s*[\d.]+""")


def _test_files_to_scan() -> list[Path]:
    """Every .py under tests/ except this guard (whose self-check must spell the shapes)."""
    return [p for p in sorted(_TESTS_ROOT.rglob("*.py")) if p.resolve() != _THIS_FILE]


def _legacy_grammar_lines() -> list[tuple[str, int, str]]:
    """Every (relative path, line number, line) under tests/ that carries the legacy
    grammar and is not explicitly marked as a negative case."""
    offenders: list[tuple[str, int, str]] = []
    for path in _test_files_to_scan():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if LEGACY_NEGATIVE_MARKER in line:
                continue
            if _LEGACY_ATTRIBUTE_RE.search(line) or _LEGACY_LITERAL_RE.search(line):
                offenders.append((str(path.relative_to(_TESTS_ROOT)), lineno, line.strip()))
    return offenders


def test_legacy_grammar_regexes_recognise_the_legacy_shapes():
    """Self-check so the guard cannot pass vacuously."""
    assert _LEGACY_ATTRIBUTE_RE.search('<p coords="1;10,20,30,40">')
    assert _LEGACY_ATTRIBUTE_RE.search("coords='7;1.5,2,3,4'")
    assert _LEGACY_ATTRIBUTE_RE.search('coords=\\"2;1,2,3,4\\"')
    assert _LEGACY_LITERAL_RE.search('"7;211.98,325.41,344.69,400.00"')
    assert not _LEGACY_ATTRIBUTE_RE.search('<p coords="1,10,20,30,40">')
    assert not _LEGACY_LITERAL_RE.search('"7,211.98,325.41,344.69,11.28;7,1,2,3,4"')
    assert not _LEGACY_LITERAL_RE.search('"page;x0,y0,x1,y1"')


def test_no_unmarked_legacy_coords_grammar_under_tests():
    offenders = _legacy_grammar_lines()
    assert offenders == [], (
        "legacy 'page;x0,y0,x1,y1' coords found in tests/ (Requirement 11.6); "
        f"convert to GROBID's 'page,x,y,w,h' grammar or mark the line with "
        f"'# {LEGACY_NEGATIVE_MARKER}': {offenders}"
    )


def test_marked_negative_cases_exist_and_are_truly_negative():
    """The allow-list is not a loophole: every marked line must still be a legacy
    string, and the consumers must reject that string."""
    marked: list[tuple[str, int, str]] = []
    for path in _test_files_to_scan():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if LEGACY_NEGATIVE_MARKER in line:
                marked.append((str(path.relative_to(_TESTS_ROOT)), lineno, line))
    assert marked, "expected at least one explicitly marked legacy negative case"
    for rel, lineno, line in marked:
        m = _LEGACY_ATTRIBUTE_RE.search(line) or _LEGACY_LITERAL_RE.search(line)
        assert m, f"{rel}:{lineno} carries the marker but no legacy coords string"
        literal = re.search(r"""["'](\d+;[^"']*)["']""", line)
        assert literal, f"{rel}:{lineno}: cannot recover the legacy string"
        s = literal.group(1)
        assert parse_tei_coords(s) == []
        assert grobid_mod._parse_coords(s) is None
        assert evidence_index._parse_coords(s)["page"] is None
        assert _page_from_tei_coords(s) == 0
