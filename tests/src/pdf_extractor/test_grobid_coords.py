"""GROBID coordinate request form and ``coords`` grammar (risk-remediation 7.1).

Requirements 11.1, 11.2, 11.5 -- the extractor must request coordinates as
repeated ``teiCoordinates`` form fields (one comma-joined value is treated by
GROBID as a single unknown element and yields no coordinates), and must parse
GROBID's real ``page,x,y,w,h;page,x,y,w,h`` grammar rather than the legacy
``page;x0,y0,x1,y1`` shape the parsers assumed before.

The live GROBID test at the bottom is ``slow`` and skipped unless ``GROBID_URL``
is set; nothing in the default suite touches the network.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pdf_extractor.extraction import GROBID as grobid_mod
from pdf_extractor.extraction.GROBID import CoordBox, parse_tei_coords
from tests.helpers.grobid_tei import (
    COORD_CASES,
    GROBID_COORDS_RE,
    MALFORMED_COORD_CASES,
    WELL_FORMED_COORD_CASES,
    load_tei_root,
    tei_figures,
)

_TEI_NS = "http://www.tei-c.org/ns/1.0"
_EXPECTED_TEI_COORDINATES = ["p", "figure", "formula", "head", "biblStruct"]


def _boxes_of(coords: str) -> list[list[str]]:
    """Split a GROBID coords string into raw ``[page, x, y, w, h]`` token lists."""
    return [seg.split(",") for seg in coords.split(";")]


# ---------------------------------------------------------------------------
# parse_tei_coords -- canonical parser (11.2, 11.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", WELL_FORMED_COORD_CASES, ids=lambda c: c.label)
def test_parse_tei_coords_well_formed_returns_every_box(case):
    """11.2: every well-formed box is returned, page as emitted (1-based), in order."""
    boxes = parse_tei_coords(case.coords)
    raw = _boxes_of(case.coords)

    assert len(boxes) == len(raw), "one CoordBox per ';'-separated segment"
    assert boxes[0].page == case.expected_first_page
    for box, tokens in zip(boxes, raw):
        assert isinstance(box, CoordBox)
        assert box.page == int(tokens[0])
        assert (box.x, box.y, box.w, box.h) == pytest.approx(tuple(float(t) for t in tokens[1:]))


def test_parse_tei_coords_single_box_exact_values():
    assert parse_tei_coords("7,211.98,325.41,344.69,11.28") == [
        CoordBox(page=7, x=211.98, y=325.41, w=344.69, h=11.28)
    ]


def test_parse_tei_coords_cross_page_boxes_keep_their_own_page():
    """A multi-page element yields boxes on each page; the first box's page leads."""
    case = next(c for c in COORD_CASES if c.label == "multi_box_two_pages")
    boxes = parse_tei_coords(case.coords)
    assert [b.page for b in boxes] == [3, 3, 4]
    assert boxes[0].page == case.expected_first_page


@pytest.mark.parametrize(
    "coords, expected",
    [
        ("7, 211.98, 325.41, 344.69, 11.28", [CoordBox(7, 211.98, 325.41, 344.69, 11.28)]),
        (" 7,1,2,3,4 ; 7,5,6,7,8 ", [CoordBox(7, 1, 2, 3, 4), CoordBox(7, 5, 6, 7, 8)]),
        ("\t7,1,2,3,4\n", [CoordBox(7, 1, 2, 3, 4)]),
    ],
    ids=["spaces_inside_box", "spaces_around_separator", "surrounding_tabs_newline"],
)
def test_parse_tei_coords_tolerates_whitespace(coords, expected):
    assert parse_tei_coords(coords) == expected


@pytest.mark.parametrize("case", MALFORMED_COORD_CASES, ids=lambda c: c.label)
def test_parse_tei_coords_malformed_or_absent_returns_empty(case):
    """11.5: absent or malformed input yields ``[]`` -- never a partial result, never raises."""
    assert parse_tei_coords(case.coords) == []


@pytest.mark.parametrize(
    "coords",
    [
        "1,1,2,3,4;abc",  # one good box then garbage
        "1,1,2,3,4;",  # trailing separator -> empty segment
        ";1,1,2,3,4",  # leading separator
        "1,1,2,3,4;2,1,2,3",  # second box too short
        "1,1,2,3,4,5",  # too many numbers
        "1.5,1,2,3,4",  # non-integer page
        "x,1,2,3,4",  # alpha page
        "²,1,2,3,4",  # superscript digit: str.isdigit() is True but int() raises
        "1,nan,2,3,4",  # float() accepts 'nan' but it is not a coordinate
        "1,inf,2,3,4",
        ",,,,",
        "   ",
    ],
)
def test_parse_tei_coords_any_malformed_box_discards_all(coords):
    """11.5: one bad box poisons the whole attribute (no half-parsed boxes)."""
    assert parse_tei_coords(coords) == []


def test_parse_tei_coords_never_raises_on_odd_types():
    """Contract from design.md (CoordsParser): never raises."""
    assert parse_tei_coords(None) == []
    assert parse_tei_coords("") == []
    assert parse_tei_coords(123) == []  # type: ignore[arg-type]
    assert parse_tei_coords(["1,2,3,4,5"]) == []  # type: ignore[arg-type]


def test_coordbox_is_frozen():
    box = CoordBox(1, 2.0, 3.0, 4.0, 5.0)
    with pytest.raises(Exception):
        box.page = 2  # type: ignore[misc]


def test_parse_tei_coords_on_every_fixture_coords_attribute():
    """11.2 on real GROBID 0.8.2 output: every fixture ``coords`` attribute parses to >= 1 box."""
    for name in ("biorxiv", "arxiv", "plosone"):
        root = load_tei_root(name)
        seen = 0
        for el in root.iter():
            coords = el.get("coords")
            if coords is None:
                continue
            seen += 1
            assert GROBID_COORDS_RE.match(coords), f"{name}: fixture coords not in GROBID grammar"
            boxes = parse_tei_coords(coords)
            assert boxes, f"{name}: parser returned [] for well-formed {coords[:40]!r}"
            assert boxes[0].page == int(coords.split(",")[0])
        assert seen > 0, f"{name}: fixture carries no coords attributes"


# ---------------------------------------------------------------------------
# _parse_coords -- retained legacy interface, now delegating (11.2, 11.5)
# ---------------------------------------------------------------------------


def test_legacy_parse_coords_single_box_zero_based_page_and_xyxy_bbox():
    page_idx, bbox = grobid_mod._parse_coords("7,211.98,325.41,344.69,11.28")
    assert page_idx == 6
    assert bbox == pytest.approx((211.98, 325.41, 211.98 + 344.69, 325.41 + 11.28))


def test_legacy_parse_coords_unions_boxes_on_first_page():
    coords = "1,200.01,284.75,368.86,8.79;1,200.01,298.75,368.07,8.79"
    page_idx, bbox = grobid_mod._parse_coords(coords)
    assert page_idx == 0
    # x1 = max(200.01+368.86, 200.01+368.07); y1 = max(284.75+8.79, 298.75+8.79)
    assert bbox == pytest.approx((200.01, 284.75, 568.87, 307.54))


def test_legacy_parse_coords_multi_page_uses_first_page_only():
    coords = "3,537.84,676.28,29.27,9.22;3,200.01,689.32,348.20,9.22;4,200.01,72.00,360.76,8.50"
    page_idx, bbox = grobid_mod._parse_coords(coords)
    assert page_idx == 2
    # Union of the two page-3 boxes only; the page-4 box (y=72) must not drag y0 down.
    assert bbox == pytest.approx((200.01, 676.28, 567.11, 698.54))


@pytest.mark.parametrize("case", WELL_FORMED_COORD_CASES, ids=lambda c: c.label)
def test_legacy_parse_coords_agrees_with_helper_zero_based_page(case):
    parsed = grobid_mod._parse_coords(case.coords)
    assert parsed is not None
    assert parsed[0] == case.expected_first_page_zero_based


@pytest.mark.parametrize("case", MALFORMED_COORD_CASES, ids=lambda c: c.label)
def test_legacy_parse_coords_malformed_returns_none(case):
    assert grobid_mod._parse_coords(case.coords) is None


def test_legacy_parse_coords_on_real_fixture_figure():
    """A coordinate-bearing figure from real GROBID output yields a real 0-based page and bbox."""
    root = load_tei_root("arxiv")
    fig = next(f for f in tei_figures(root) if f.get("coords"))
    coords = fig.get("coords")
    parsed = grobid_mod._parse_coords(coords)
    assert parsed is not None
    page_idx, (x0, y0, x1, y1) = parsed
    first = parse_tei_coords(coords)[0]
    assert page_idx == first.page - 1
    assert x1 > x0 and y1 > y0
    assert x0 <= first.x and y0 <= first.y
    assert x1 >= first.x + first.w and y1 >= first.y + first.h


def test_parse_tei_to_blocks_places_blocks_on_real_pages():
    """End-to-end within the module: real-grammar coords land blocks on their page with a bbox."""
    tei = f"""<?xml version="1.0"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader><fileDesc><titleStmt><title>T</title></titleStmt></fileDesc></teiHeader>
  <text><body>
    <div><head coords="7,72.00,80.00,200.00,12.00">Methods</head>
      <p coords="7,72.00,100.00,400.00,10.00;7,72.00,112.00,380.00,10.00">Body text here.</p>
    </div>
  </body></text>
</TEI>"""
    blocks = grobid_mod._parse_tei_to_blocks(tei, with_coordinates=True)
    by_text = {b["text"]: b for b in blocks}
    assert by_text["Methods"]["page_index"] == 6
    assert by_text["Methods"]["block_bbox"] == pytest.approx((72.0, 80.0, 272.0, 92.0))
    assert by_text["Body text here."]["page_index"] == 6
    assert by_text["Body text here."]["block_bbox"] == pytest.approx((72.0, 100.0, 472.0, 122.0))


# ---------------------------------------------------------------------------
# Request form (11.1)
# ---------------------------------------------------------------------------


@pytest.fixture
def _mock_requests():
    """Patch sys.modules['requests'] so the lazy import inside _call_grobid_api
    never touches the real package (mirrors test_grobid_extractor.py)."""
    mock_requests = MagicMock()
    with patch.dict(
        sys.modules,
        {"requests": mock_requests, "requests.exceptions": mock_requests.exceptions},
    ):
        yield mock_requests


def _fake_session_returning(tei_xml: str, status: int = 200):
    sess = MagicMock()
    sess.post.return_value = MagicMock(
        status_code=status, content=tei_xml.encode("utf-8"), text=tei_xml
    )
    return sess


_MINIMAL_TEI = f"""<?xml version="1.0"?>
<TEI xmlns="{_TEI_NS}">
  <teiHeader><fileDesc><titleStmt><title>X</title></titleStmt></fileDesc></teiHeader>
  <text><body><p>hello world</p></body></text>
</TEI>"""


def test_request_sends_tei_coordinates_as_repeated_fields(tmp_path, _mock_requests):
    """11.1: ``teiCoordinates`` is a list under ``data=`` so ``requests`` emits one
    multipart field per element type (GROBID rejects the comma-joined form)."""
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    fake_sess = _fake_session_returning(_MINIMAL_TEI)
    with patch.object(grobid_mod, "_get_session", return_value=fake_sess):
        grobid_mod.extract_with_grobid(str(pdf_path), parse_blocks=False, max_retries=0)

    form_data = fake_sess.post.call_args.kwargs["data"]
    assert form_data["teiCoordinates"] == _EXPECTED_TEI_COORDINATES
    assert isinstance(form_data["teiCoordinates"], list)


def test_request_omits_tei_coordinates_when_disabled(tmp_path, _mock_requests):
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    fake_sess = _fake_session_returning(_MINIMAL_TEI)
    with patch.object(grobid_mod, "_get_session", return_value=fake_sess):
        grobid_mod.extract_with_grobid(
            str(pdf_path), parse_blocks=False, max_retries=0, tei_coordinates=False
        )

    assert "teiCoordinates" not in fake_sess.post.call_args.kwargs["data"]


def test_list_form_value_is_encoded_as_repeated_multipart_fields():
    """Pin the ``requests`` behaviour the fix relies on: a list value under ``data=``
    becomes N ``name="teiCoordinates"`` parts, not one joined string."""
    requests = pytest.importorskip("requests")
    import io

    prepared = requests.Request(
        "POST",
        "http://grobid.invalid/api/processFulltextDocument",
        files={"input": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        data={"teiCoordinates": _EXPECTED_TEI_COORDINATES, "generateIDs": "0"},
    ).prepare()
    body = prepared.body.decode("latin1")
    assert body.count('name="teiCoordinates"') == len(_EXPECTED_TEI_COORDINATES)
    assert "p,figure" not in body


# ---------------------------------------------------------------------------
# Opt-in live test (slow; requires GROBID_URL)
# ---------------------------------------------------------------------------


def _write_minimal_text_pdf(path: Path, lines: list[str]) -> None:
    """Write a valid single-page PDF with Helvetica text lines, no external deps."""
    content = "BT /F1 12 Tf 72 720 Td 16 TL\n" + "\n".join(
        "(%s) Tj T*" % line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in lines
    ) + "\nET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
    ]
    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, obj)).encode("latin1")
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode("ascii")
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += (
        "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    ).encode("ascii")
    path.write_bytes(out)


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("GROBID_URL"), reason="GROBID_URL not set")
def test_live_grobid_returns_coords_attributes(tmp_path):
    """11.1 against a real GROBID: the repeated-field request yields TEI with ``coords``."""
    pdf_path = tmp_path / "coords_probe.pdf"
    _write_minimal_text_pdf(
        pdf_path,
        ["Coordinate Probe: A Study of Page Boxes"]
        + ["We describe a small experiment on page coordinates in scholarly documents."] * 6
        + ["1. Introduction"]
        + ["The coordinate attributes are emitted per paragraph when requested correctly."] * 6,
    )

    tei_xml, _ = grobid_mod.extract_with_grobid(
        str(pdf_path),
        grobid_url=os.environ["GROBID_URL"],
        parse_blocks=False,
        max_retries=0,
        timeout=60,
    )

    coords = re.findall(r'coords="([^"]*)"', tei_xml)
    assert len(coords) >= 1, "live GROBID returned no coords attributes"
    assert all(GROBID_COORDS_RE.match(c) for c in coords), coords
    assert all(parse_tei_coords(c) for c in coords)
