"""Evidence nodes from the evidence index, on both cache paths (provenance-core 6.2).

``build_or_load_evidence_bundle`` takes an optional ``recorder`` (plus the
document's ``source_id``, supplied by the caller) and, after the bundle is built
-- on the cache-miss path *and* the cache-hit path -- records one evidence node
per index item through ``evidence_node_from_item``:

* an item with no usable identifier becomes a recorded ``invalid_identifier``
  finding, never a silent skip (Requirement 4.7, 8.4);
* the title item (``section_path == "Metadata"``) is recorded with evidence kind
  ``metadata`` by a pipeline-side override on a *copy* (Requirement 4.1);
* ``extract_header_metadata`` (pure) yields the first-author value and the
  publication year with their TEI header elements; the author prefill and the
  TEI-header branch of year resolution read their values from it, and one
  ``metadata`` evidence item per found element (``M-first-author``,
  ``M-publication-date``) exists in provenance only;
* the ``evidence_index`` stage is marked per document;
* nothing the bundle, the evidence cache or any prompt sees changes.

Pinned against goldens captured from the unmodified code (HEAD f0ffdeb): the
prefilled values and year provenance for the three real GROBID fixtures and for
synthetic header edge cases.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from pipeline import evidence_index as ei
from pipeline.evidence_index import (
    EvidenceBundle,
    _build_items_from_tei,
    build_or_load_evidence_bundle,
    select_paper_evidence,
    serialise_evidence_package,
)
from provenance.contracts import STAGE_CONTRACTS
from provenance.graph import build_graph
from provenance.completeness import compute_completeness
from provenance.identity import derive_source_id, format_evidence_id
from provenance.models import RunIdentity, SourceNode, UNKNOWN_PRIVACY
from provenance.recorder import ProvenanceRecorder
from quality_control.models import QCBundle, UnifiedRecord
from tests.helpers.grobid_tei import FIXTURE_FILES, TEI_NS, load_tei_text

_NS = f"{{{TEI_NS}}}"
_FP = "a" * 64
_SOURCE_ID = derive_source_id(_FP)
_RUN_ID = "run:0123456789abcdef"
_FIXTURES = list(FIXTURE_FILES)


# ---------------------------------------------------------------------------
# Goldens captured from HEAD f0ffdeb (before the header helper existed):
# (prefilled[1], prefilled[2], year_provenance, year_confidence) for
# ``_build_items_from_tei(tei, "pid", "")``.
# ---------------------------------------------------------------------------

_HEAD_GOLDEN = {
    "biorxiv": ("Blanco-Melo", "nr", "", "nr"),
    "arxiv": ("Chang", "2020", "tei_header", "h"),
    "plosone": ("Anastassopoulou", "2020", "tei_header", "h"),
    "no_header": ("pid", "nr", "", "nr"),
    "no_author_date_when": ("pid", "2019", "tei_header", "h"),
    "no_date_author": ("Smith", "nr", "", "nr"),
    "multi_author": ("Smith", "2020", "tei_header", "h"),
    "date_text_only": ("pid", "2018", "tei_header", "h"),
    "when_without_year_text_with_year": ("pid", "1999", "tei_header", "h"),
    "whitespace_surname": ("Van Der", "nr", "", "nr"),
    "empty_surname": ("pid", "2011", "tei_header", "h"),
    "priority_imprint_over_earlier": ("pid", "2020", "tei_header", "h"),
    "date_outside_header": ("pid", "nr", "", "nr"),
    "first_date_no_year_second_has": ("pid", "2014", "tei_header", "h"),
    "creation_only": ("pid", "2016", "tei_header", "h"),
    "fallback_any_header_date": ("pid", "2013", "tei_header", "h"),
    "nested_surname_children": ("O'Brien", "nr", "", "nr"),
}


def _tei(header: str = "", body: str = "<body/>") -> str:
    return f'<TEI xmlns="{TEI_NS}">{header}<text>{body}</text></TEI>'


def _hdr(inner: str) -> str:
    return f"<teiHeader>{inner}</teiHeader>"


def _src(biblstruct: str) -> str:
    return _hdr(f"<fileDesc><sourceDesc><biblStruct>{biblstruct}</biblStruct></sourceDesc></fileDesc>")


_CASES = {
    "no_header": _tei(),
    "no_author_date_when": _tei(_src('<monogr><imprint><date when="2019-05-01"/></imprint></monogr>')),
    "no_date_author": _tei(
        _src("<analytic><author><persName><surname>Smith</surname></persName></author></analytic>")
    ),
    "multi_author": _tei(
        _src(
            "<analytic><author><persName><forename>A</forename><surname>Smith</surname></persName></author>"
            "<author><persName><surname>Jones</surname></persName></author></analytic>"
            '<monogr><imprint><date when="2020-01-01"/></imprint></monogr>'
        )
    ),
    "date_text_only": _tei(_src("<monogr><imprint><date>Published March 2018</date></imprint></monogr>")),
    "when_without_year_text_with_year": _tei(
        _src('<monogr><imprint><date when="--" type="published">in 1999 spring</date></imprint></monogr>')
    ),
    "whitespace_surname": _tei(
        _src("<analytic><author><persName><surname>  Van\n   Der  </surname></persName></author></analytic>")
    ),
    "empty_surname": _tei(
        _src(
            "<analytic><author><persName><surname/></persName></author></analytic>"
            '<monogr><imprint><date when="2011"/></imprint></monogr>'
        )
    ),
    "priority_imprint_over_earlier": _tei(
        _hdr(
            '<fileDesc><sourceDesc><biblStruct><analytic><date when="2017"/></analytic>'
            '<monogr><imprint><date when="2020"/></imprint></monogr></biblStruct></sourceDesc></fileDesc>'
            '<profileDesc><creation><date when="2019"/></creation></profileDesc>'
        )
    ),
    "date_outside_header": _tei(_hdr("<fileDesc/>"), '<body><div><p><date when="2001"/></p></div></body>'),
    "first_date_no_year_second_has": _tei(
        _src('<monogr><imprint><date when="abc"/></imprint></monogr><analytic><date when="2014"/></analytic>')
    ),
    "creation_only": _tei(_hdr('<profileDesc><creation><date when="2016-02"/></creation></profileDesc>')),
    "fallback_any_header_date": _tei(_hdr('<fileDesc><publicationStmt><date when="2013"/></publicationStmt></fileDesc>')),
    "nested_surname_children": _tei(
        _src("<analytic><author><persName><surname>O<hi>'</hi>Brien</surname></persName></author></analytic>")
    ),
}

#: ``when`` attribute (or text) of the date element the year must come from.
_EXPECTED_DATE_SOURCE = {
    "no_author_date_when": "2019-05-01",
    "multi_author": "2020-01-01",
    "date_text_only": "Published March 2018",
    "when_without_year_text_with_year": "in 1999 spring",
    "empty_surname": "2011",
    "priority_imprint_over_earlier": "2020",
    "first_date_no_year_second_has": "2014",
    "creation_only": "2016-02",
    "fallback_any_header_date": "2013",
}

_TEI_SOURCES: dict[str, str] = {**{n: load_tei_text(n) for n in _FIXTURES}, **_CASES}


def _date_source(elem: ET.Element) -> str:
    """The ``when`` attribute when it carries a year, else the element text."""
    when = elem.attrib.get("when", "")
    return when if re.search(r"(19|20)\d{2}", when) else "".join(elem.itertext())


# ---------------------------------------------------------------------------
# Helpers for bundle construction
# ---------------------------------------------------------------------------

_SAMPLE_TEI = f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{TEI_NS}">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Sample Title</title></titleStmt>
      <sourceDesc><biblStruct><monogr><author><surname>Smith</surname></author><imprint><date when="2021"/></imprint></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <head>Introduction</head>
        <p><s coords="1,10,20,20,20">We used MIMIC-III data.</s></p>
      </div>
    </body>
  </text>
</TEI>
"""


def _ctx(tmp_path: Path, tei: str = _SAMPLE_TEI, *, name: str = "paper.pdf") -> QCBundle:
    pdf_path = tmp_path / name
    if not pdf_path.exists():
        pdf_path.write_bytes(b"%PDF-1.4 fake")
    unified = UnifiedRecord(
        document_id="paper1",
        content={
            "exact_text": "We used MIMIC-III data. Second sentence here.",
            "source_pdf_path": str(pdf_path),
            "grobid_tei_xml": tei,
        },
    )
    return QCBundle(branches=[], unified=unified)


def _cfg(root: Path) -> dict:
    return {"evidence_cache_dir": str(root), "addons": {}}


def _run_identity() -> RunIdentity:
    return RunIdentity(
        run_id=_RUN_ID,
        config_fingerprint="cfg",
        schema_fingerprint="schema",
        model_ids=("m",),
        document_fingerprints={_SOURCE_ID: _FP},
        created_at="2026-10-11T00:00:00+00:00",
    )


def _recorder(*, enabled: bool = True) -> ProvenanceRecorder:
    return ProvenanceRecorder(_run_identity(), enabled=enabled)


def _evidence_nodes(recorder: ProvenanceRecorder) -> dict:
    return {nid: n for nid, n in recorder.snapshot().nodes.items() if n.kind == "evidence"}


def _cache_file(bundle: EvidenceBundle) -> Path:
    return bundle.index_path


# ---------------------------------------------------------------------------
# Header helper: goldens, element selection, edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(_HEAD_GOLDEN))
def test_prefill_and_year_provenance_equal_head_golden(name):
    items, prefilled, meta = _build_items_from_tei(_TEI_SOURCES[name], "pid", "")
    author, year, provenance, confidence = _HEAD_GOLDEN[name]
    assert prefilled == {1: author, 2: year}
    assert meta == {"year_provenance": provenance, "year_confidence": confidence}


@pytest.mark.parametrize("name", list(_HEAD_GOLDEN))
def test_header_helper_agrees_with_the_head_golden(name):
    header = ei.extract_header_metadata(_TEI_SOURCES[name])
    author, year, provenance, _ = _HEAD_GOLDEN[name]
    assert header.first_author == ("" if author == "pid" else author)
    assert header.publication_year == (year if provenance == "tei_header" else "")
    # elements are present exactly when the value is
    assert (header.first_author_element is not None) == bool(header.first_author)
    assert (header.publication_date_element is not None) == bool(header.publication_year)
    if header.first_author_element is not None:
        assert header.first_author_element.tag == f"{_NS}surname"
    if header.publication_date_element is not None:
        assert header.publication_date_element.tag == f"{_NS}date"
        assert header.publication_year in _date_source(header.publication_date_element)


@pytest.mark.parametrize("name,source", sorted(_EXPECTED_DATE_SOURCE.items()))
def test_header_helper_selects_the_date_element_year_resolution_uses(name, source):
    header = ei.extract_header_metadata(_TEI_SOURCES[name])
    assert header.publication_date_element is not None
    assert _date_source(header.publication_date_element) == source


def test_header_helper_selects_the_first_surname_of_multiple_authors():
    header = ei.extract_header_metadata(_CASES["multi_author"])
    assert header.first_author == "Smith"
    assert "".join(header.first_author_element.itertext()) == "Smith"


@pytest.mark.parametrize("name", _FIXTURES)
def test_header_helper_on_real_fixtures(name):
    header = ei.extract_header_metadata(_TEI_SOURCES[name])
    assert header.first_author == _HEAD_GOLDEN[name][0]
    assert header.publication_year == ("" if _HEAD_GOLDEN[name][2] == "" else _HEAD_GOLDEN[name][1])


@pytest.mark.parametrize("bad", ["", "   ", "<TEI", "not xml at all", "<a><b></a>", None, 5, b"<x/>x"])
def test_header_helper_never_raises_and_finds_nothing_on_unparseable_input(bad):
    header = ei.extract_header_metadata(bad)
    assert header.first_author == ""
    assert header.first_author_element is None
    assert header.publication_year == ""
    assert header.publication_date_element is None


def test_malformed_tei_keeps_the_existing_parse_error_path_in_the_bundle(tmp_path):
    ctx = _ctx(tmp_path, tei="<TEI xmlns='http://www.tei-c.org/ns/1.0'><teiHeader>")
    with pytest.raises(ET.ParseError):
        _build_items_from_tei(ctx.unified.content["grobid_tei_xml"], "paper1", "")
    bundle = build_or_load_evidence_bundle(ctx, _cfg(tmp_path / "c"))
    assert bundle.evidence_items == []
    assert bundle.prefilled_fields == {1: "paper1", 2: "nr"}


def test_header_helper_is_a_frozen_dataclass():
    header = ei.extract_header_metadata(_CASES["multi_author"])
    with pytest.raises(Exception):
        header.first_author = "x"  # type: ignore[misc]


def test_resolve_publication_year_keeps_its_signature_and_reads_the_helper():
    root = ET.fromstring(_CASES["priority_imprint_over_earlier"])
    result = ei.resolve_publication_year(root, Path(""), "")
    assert (result.year, result.confidence, result.provenance) == ("2020", "h", "tei_header")
    assert ei._year_from_tei(root) == "2020"
    assert ei._year_from_tei(None) == ""


# ---------------------------------------------------------------------------
# Miss and hit record the same evidence nodes; nothing else changes
# ---------------------------------------------------------------------------


def _expected_ids(local_ids):
    return {format_evidence_id(_SOURCE_ID, i) for i in local_ids}


def test_miss_then_hit_record_identical_evidence_nodes(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)

    plain = build_or_load_evidence_bundle(ctx, _cfg(tmp_path / "plain"))
    plain_bytes = _cache_file(plain).read_bytes()

    rec_miss = _recorder()
    miss = build_or_load_evidence_bundle(
        ctx, _cfg(tmp_path / "with"), recorder=rec_miss, source_id=_SOURCE_ID
    )

    calls = {"n": 0}
    real = ei._build_items_from_tei

    def spy(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(ei, "_build_items_from_tei", spy)
    rec_hit = _recorder()
    hit = build_or_load_evidence_bundle(
        ctx, _cfg(tmp_path / "with"), recorder=rec_hit, source_id=_SOURCE_ID
    )
    assert calls["n"] == 0, "the second run must be a cache hit"

    # bundle contents and cache file are exactly what a run without a recorder produces
    for bundle in (miss, hit):
        assert bundle.evidence_items == plain.evidence_items
        assert bundle.evidence_map == plain.evidence_map
        assert bundle.prefilled_fields == plain.prefilled_fields
        assert bundle.tei_xml == plain.tei_xml
    assert _cache_file(miss).read_bytes() == plain_bytes
    assert _cache_file(hit).read_bytes() == plain_bytes

    miss_nodes, hit_nodes = _evidence_nodes(rec_miss), _evidence_nodes(rec_hit)
    assert set(miss_nodes) == set(hit_nodes)
    assert miss_nodes == hit_nodes
    # one S item (sentence), the title (S000002) and the two header nodes
    assert set(miss_nodes) == _expected_ids(["S000001", "S000002", "M-first-author", "M-publication-date"])

    title = miss_nodes[format_evidence_id(_SOURCE_ID, "S000002")]
    assert title.evidence_kind == "metadata"
    assert title.anchor.precision == "approximate"  # page 1 only
    assert miss_nodes[format_evidence_id(_SOURCE_ID, "S000001")].evidence_kind == "sentence"
    assert miss_nodes[format_evidence_id(_SOURCE_ID, "S000001")].anchor.precision == "exact"
    assert miss_nodes[format_evidence_id(_SOURCE_ID, "S000001")].producing_backend == "grobid"

    for local_id, text in (("M-first-author", "Smith"), ("M-publication-date", "2021")):
        node = miss_nodes[format_evidence_id(_SOURCE_ID, local_id)]
        assert node.evidence_kind == "metadata"
        assert node.local_id == local_id
        assert node.anchor.precision == "absent"
        assert node.anchor.page is None and node.anchor.coords is None
        assert node.producing_backend == "grobid"
        assert node.parser_metadata["text_chars"] == len(text)
        assert "text" not in node.parser_metadata
    assert not rec_miss.snapshot().findings


def test_header_items_exist_only_in_provenance(tmp_path):
    rec = _recorder()
    bundle = build_or_load_evidence_bundle(
        _ctx(tmp_path), _cfg(tmp_path / "c"), recorder=rec, source_id=_SOURCE_ID
    )
    reserved = {"M-first-author", "M-publication-date"}
    assert reserved.isdisjoint(i["id"] for i in bundle.evidence_items)
    assert reserved.isdisjoint(bundle.evidence_map)
    cached = json.loads(bundle.index_path.read_text(encoding="utf-8"))
    assert reserved.isdisjoint(i["id"] for i in cached["evidence_items"])
    assert "M-first" not in bundle.index_path.read_text(encoding="utf-8")
    selected, _ = select_paper_evidence(
        bundle, _ALL_FIELDS, max_items=10_000, max_chars=10_000_000
    )
    package = serialise_evidence_package(bundle, selected)
    for needle in ("M-first-author", "M-publication-date", "src:", "provenance", _SOURCE_ID):
        assert needle not in package


def test_title_override_leaves_the_bundle_item_untouched(tmp_path):
    bundle = build_or_load_evidence_bundle(
        _ctx(tmp_path), _cfg(tmp_path / "c"), recorder=_recorder(), source_id=_SOURCE_ID
    )
    titles = [i for i in bundle.evidence_items if i["section_path"] == "Metadata"]
    assert len(titles) == 1
    assert titles[0]["type"] == "sentence"
    assert json.loads(bundle.index_path.read_text(encoding="utf-8"))["evidence_items"] == bundle.evidence_items


def test_fallback_without_tei_records_items_without_header_nodes_or_backend(tmp_path):
    rec = _recorder()
    bundle = build_or_load_evidence_bundle(
        _ctx(tmp_path, tei=""), _cfg(tmp_path / "c"), recorder=rec, source_id=_SOURCE_ID
    )
    nodes = _evidence_nodes(rec)
    assert set(nodes) == _expected_ids(i["id"] for i in bundle.evidence_items)
    assert len(nodes) == 2
    assert all(n.producing_backend is None for n in nodes.values())
    assert all(not n.local_id.startswith("M-") for n in nodes.values())
    assert rec.snapshot().document_stage_status == {_SOURCE_ID: {"evidence_index": "complete"}}


def test_tei_parse_error_marks_the_stage_complete_so_completeness_reports_missing_records(tmp_path):
    rec = _recorder()
    bundle = build_or_load_evidence_bundle(
        _ctx(tmp_path, tei="<TEI xmlns='x'><broken"), _cfg(tmp_path / "c"), recorder=rec, source_id=_SOURCE_ID
    )
    assert bundle.evidence_items == []
    snap = rec.snapshot()
    assert not [n for n in snap.nodes.values() if n.kind == "evidence"]
    assert snap.document_stage_status == {_SOURCE_ID: {"evidence_index": "complete"}}


# ---------------------------------------------------------------------------
# Real fixtures: counts and anchor precision
# ---------------------------------------------------------------------------


def test_fixture_evidence_nodes_precision_kinds_and_header_nodes(tmp_path):
    totals: Counter[str] = Counter()
    header_totals: Counter[str] = Counter()
    for index, name in enumerate(_FIXTURES):
        fp = f"{index:01x}" * 64
        source_id = derive_source_id(fp)
        run = RunIdentity(
            run_id=_RUN_ID,
            config_fingerprint="cfg",
            schema_fingerprint="schema",
            model_ids=("m",),
            document_fingerprints={source_id: fp},
            created_at="2026-10-11T00:00:00+00:00",
        )
        rec = ProvenanceRecorder(run)
        ctx = _ctx(tmp_path, tei=_TEI_SOURCES[name], name=f"{name}.pdf")
        bundle = build_or_load_evidence_bundle(
            ctx, _cfg(tmp_path / f"c-{name}"), recorder=rec, source_id=source_id
        )
        nodes = _evidence_nodes(rec)
        item_ids = {i["id"] for i in bundle.evidence_items}
        header = {n.local_id: n for n in nodes.values() if n.local_id.startswith("M-")}
        body = {n.local_id: n for n in nodes.values() if not n.local_id.startswith("M-")}
        assert set(body) == item_ids

        author, year, provenance, _ = _HEAD_GOLDEN[name]
        expected_header = {"M-first-author"} | ({"M-publication-date"} if provenance == "tei_header" else set())
        assert set(header) == expected_header
        assert all(n.evidence_kind == "metadata" and n.anchor.precision == "absent" for n in header.values())

        metadata_body = [n for n in body.values() if n.evidence_kind == "metadata"]
        assert len(metadata_body) == 1  # the title
        title_items = [i for i in bundle.evidence_items if i["section_path"] == "Metadata"]
        assert [n.local_id for n in metadata_body] == [i["id"] for i in title_items]

        totals.update(n.anchor.precision for n in body.values())
        header_totals.update(n.anchor.precision for n in header.values())
        assert rec.snapshot().document_stage_status == {source_id: {"evidence_index": "complete"}}
    assert totals["exact"] == 918
    assert totals["approximate"] == 3
    assert totals["absent"] == 0
    assert header_totals == Counter({"absent": 5})  # 3 authors + 2 dates (bioRxiv has no date)


def test_xml_id_locator_form_is_a_string_contract_with_the_adapter():
    elem = ET.Element(f"{_NS}figure", {"{http://www.w3.org/XML/1998/namespace}id": "fig_0"})
    assert ei._tei_xpath(elem) == "//*[@xml:id='fig_0']"
    from provenance.adapters.evidence_adapter import evidence_node_from_item

    node = evidence_node_from_item(
        {"id": "F000001", "type": "figure_caption", "xpath": ei._tei_xpath(elem), "page": None, "coords": None},
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        producing_backend="grobid",
    )
    assert node.anchor.precision == "approximate"
    weak = evidence_node_from_item(
        {"id": "F000002", "type": "figure_caption", "xpath": ei._tei_xpath(ET.Element(f"{_NS}figure"))},
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        producing_backend="grobid",
    )
    assert weak.anchor.precision == "absent"


# ---------------------------------------------------------------------------
# Items without a usable identifier
# ---------------------------------------------------------------------------

_BAD_ITEMS = [
    {"type": "sentence", "text": "no id at all", "section_path": "Body", "page": 1, "coords": None, "xpath": ""},
    {"id": 5, "type": "sentence", "text": "non-str id", "section_path": "Body", "page": 1, "coords": None, "xpath": ""},
    {"id": "", "type": "sentence", "text": "empty id", "section_path": "Body", "page": 1, "coords": None, "xpath": ""},
    {"id": "S#9", "type": "sentence", "text": "hash id", "section_path": "Body", "page": 1, "coords": None, "xpath": ""},
]
_GOOD_ITEM = {
    "id": "S000001",
    "type": "sentence",
    "section_path": "Body",
    "page": 2,
    "coords": [1.0, 2.0, 3.0, 4.0],
    "xpath": ".//s",
    "text": "ok",
    "source_pdf": "",
    "score": 1,
    "annotations": {},
}


def _assert_invalid_identifier_findings(rec: ProvenanceRecorder, positions):
    findings = [f for f in rec.snapshot().findings if f.code == "invalid_identifier"]
    assert sorted(f.finding_id for f in findings) == sorted(
        f"finding:invalid_identifier:{_SOURCE_ID}:evidence[{p}]" for p in positions
    )
    for finding in findings:
        assert finding.severity == "critical"
        assert finding.node_id is None
        assert finding.details["source_id"] == _SOURCE_ID
        assert finding.details["position"] in positions
        assert "text" not in finding.details
        assert "no id at all" not in finding.message and "empty id" not in finding.message


def test_malformed_identifiers_become_recorded_issues_on_the_miss_path(tmp_path, monkeypatch):
    items = [_GOOD_ITEM, *_BAD_ITEMS]
    monkeypatch.setattr(
        ei, "_build_items_from_tei", lambda tei, paper_id, pdf: (list(items), {1: "x", 2: "nr"}, {})
    )
    rec = _recorder()
    bundle = build_or_load_evidence_bundle(
        _ctx(tmp_path), _cfg(tmp_path / "c"), recorder=rec, source_id=_SOURCE_ID
    )
    # the bundle is unchanged: the malformed items are still in it
    assert bundle.evidence_items == items
    _assert_invalid_identifier_findings(rec, {1, 2, 3, 4})
    nodes = _evidence_nodes(rec)
    assert format_evidence_id(_SOURCE_ID, "S000001") in nodes
    assert {n.local_id for n in nodes.values() if not n.local_id.startswith("M-")} == {"S000001"}
    assert rec.snapshot().document_stage_status == {_SOURCE_ID: {"evidence_index": "complete"}}


def test_malformed_identifiers_become_recorded_issues_on_the_hit_path(tmp_path):
    ctx = _ctx(tmp_path)
    bundle = build_or_load_evidence_bundle(ctx, _cfg(tmp_path / "c"))
    cached = json.loads(bundle.index_path.read_text(encoding="utf-8"))
    cached["evidence_items"] = [_GOOD_ITEM, *_BAD_ITEMS, "not a mapping"]
    bundle.index_path.write_text(json.dumps(cached), encoding="utf-8")

    rec = _recorder()
    hit = build_or_load_evidence_bundle(ctx, _cfg(tmp_path / "c"), recorder=rec, source_id=_SOURCE_ID)
    assert hit.evidence_items == cached["evidence_items"]
    _assert_invalid_identifier_findings(rec, {1, 2, 3, 4, 5})
    assert {n.local_id for n in _evidence_nodes(rec).values() if not n.local_id.startswith("M-")} == {"S000001"}
    # the header nodes still come from the TEI
    assert format_evidence_id(_SOURCE_ID, "M-first-author") in _evidence_nodes(rec)


# ---------------------------------------------------------------------------
# Prompt bytes and prefilled values are unchanged
# ---------------------------------------------------------------------------

_ALL_FIELDS = [
    {"field_index": 8, "field_name": "Dataset", "definition": "sample population cohort data", "reviewer_question": ""},
    {"field_index": 20, "field_name": "Model", "definition": "network architecture", "reviewer_question": ""},
]


@pytest.mark.parametrize("name", _FIXTURES)
def test_prefilled_values_and_shared_prompt_prefix_bytes_are_unchanged(tmp_path, name):
    from agents.openai import prompts

    tei = _TEI_SOURCES[name]
    plain = build_or_load_evidence_bundle(_ctx(tmp_path, tei=tei), _cfg(tmp_path / "plain"))
    rec = _recorder()
    with_rec = build_or_load_evidence_bundle(
        _ctx(tmp_path, tei=tei), _cfg(tmp_path / "with"), recorder=rec, source_id=_SOURCE_ID
    )
    hit_rec = _recorder()
    hit = build_or_load_evidence_bundle(
        _ctx(tmp_path, tei=tei), _cfg(tmp_path / "with"), recorder=hit_rec, source_id=_SOURCE_ID
    )

    author, year, _, _ = _HEAD_GOLDEN[name]
    assert plain.prefilled_fields == {1: author, 2: year}
    prefixes = []
    for bundle in (plain, with_rec, hit):
        assert bundle.prefilled_fields == plain.prefilled_fields
        selected, _ = select_paper_evidence(bundle, _ALL_FIELDS, max_items=10_000, max_chars=10_000_000)
        package = serialise_evidence_package(bundle, selected)
        prefixes.append(prompts._shared_paper_prefix(package).encode("utf-8"))
        assert "M-first-author" not in package and "M-publication-date" not in package
    assert prefixes[0] == prefixes[1] == prefixes[2]
    assert _evidence_nodes(rec).keys() == _evidence_nodes(hit_rec).keys()


# ---------------------------------------------------------------------------
# Off / misuse / failure: provenance never aborts extraction
# ---------------------------------------------------------------------------


def test_no_recorder_behaves_as_before(tmp_path):
    bundle = build_or_load_evidence_bundle(_ctx(tmp_path), _cfg(tmp_path / "c"))
    assert bundle.prefilled_fields == {1: "Smith", 2: "2021"}
    # a source id without a recorder is simply unused
    again = build_or_load_evidence_bundle(_ctx(tmp_path), _cfg(tmp_path / "c"), source_id=_SOURCE_ID)
    assert again.evidence_items == bundle.evidence_items


def test_disabled_recorder_stores_nothing_and_changes_nothing(tmp_path):
    plain = build_or_load_evidence_bundle(_ctx(tmp_path), _cfg(tmp_path / "plain"))
    rec = _recorder(enabled=False)
    bundle = build_or_load_evidence_bundle(
        _ctx(tmp_path), _cfg(tmp_path / "off"), recorder=rec, source_id=_SOURCE_ID
    )
    snap = rec.snapshot()
    assert not snap.nodes and not snap.findings
    assert snap.document_stage_status == {}
    assert bundle.evidence_items == plain.evidence_items
    assert bundle.prefilled_fields == plain.prefilled_fields
    assert bundle.index_path.read_bytes() == plain.index_path.read_bytes()


@pytest.mark.parametrize("bad", [None, "", "paper.pdf", "src:", "src:a#b", "src:a b", 5])
def test_missing_or_malformed_source_id_warns_and_emits_nothing(tmp_path, caplog, bad):
    rec = _recorder()
    plain = build_or_load_evidence_bundle(_ctx(tmp_path), _cfg(tmp_path / "plain"))
    with caplog.at_level(logging.WARNING):
        bundle = build_or_load_evidence_bundle(
            _ctx(tmp_path), _cfg(tmp_path / "c"), recorder=rec, source_id=bad
        )
    assert bundle.evidence_items == plain.evidence_items
    snap = rec.snapshot()
    assert not snap.nodes and not snap.findings
    assert snap.document_stage_status == {}
    assert any(r.levelno == logging.WARNING and "source_id" in r.getMessage() for r in caplog.records)


class _FailingRecorder(ProvenanceRecorder):
    def record_evidence(self, nodes):  # noqa: D401
        raise RuntimeError("boom in record_evidence")


def test_a_recorder_that_raises_never_aborts_and_the_stage_is_marked_failed(tmp_path, caplog):
    plain = build_or_load_evidence_bundle(_ctx(tmp_path), _cfg(tmp_path / "plain"))
    rec = _FailingRecorder(_run_identity())
    with caplog.at_level(logging.WARNING):
        bundle = build_or_load_evidence_bundle(
            _ctx(tmp_path), _cfg(tmp_path / "c"), recorder=rec, source_id=_SOURCE_ID
        )
    assert bundle.evidence_items == plain.evidence_items
    assert bundle.prefilled_fields == plain.prefilled_fields
    snap = rec.snapshot()
    assert snap.document_stage_status == {_SOURCE_ID: {"evidence_index": "failed"}}
    reasons = snap.document_stage_failure_reasons[_SOURCE_ID]["evidence_index"]
    assert len(reasons) == 1 and "boom in record_evidence" in reasons[0]
    assert snap.stage_status["evidence_index"] == "not_run"  # run-wide mark untouched
    assert any(r.levelno == logging.WARNING for r in caplog.records)


class _TotallyBrokenRecorder(ProvenanceRecorder):
    def record_evidence(self, nodes):
        raise RuntimeError("boom")

    def mark_stage_failed(self, stage, reason, *, source_id=None):
        raise RuntimeError("cannot mark either")

    def snapshot(self):
        raise RuntimeError("no snapshot")


def test_a_recorder_that_fails_everywhere_still_returns_the_bundle(tmp_path):
    plain = build_or_load_evidence_bundle(_ctx(tmp_path), _cfg(tmp_path / "plain"))
    bundle = build_or_load_evidence_bundle(
        _ctx(tmp_path), _cfg(tmp_path / "c"), recorder=_TotallyBrokenRecorder(_run_identity()), source_id=_SOURCE_ID
    )
    assert bundle.evidence_items == plain.evidence_items


def test_stage_is_marked_per_document_and_not_run_wide(tmp_path):
    rec = _recorder()
    other_fp = "b" * 64
    other_id = derive_source_id(other_fp)
    build_or_load_evidence_bundle(_ctx(tmp_path), _cfg(tmp_path / "c"), recorder=rec, source_id=_SOURCE_ID)
    build_or_load_evidence_bundle(
        _ctx(tmp_path, name="other.pdf"), _cfg(tmp_path / "c2"), recorder=rec, source_id=other_id
    )
    snap = rec.snapshot()
    assert snap.document_stage_status == {
        _SOURCE_ID: {"evidence_index": "complete"},
        other_id: {"evidence_index": "complete"},
    }
    assert snap.stage_status["evidence_index"] == "not_run"
    assert STAGE_CONTRACTS["evidence_index"].required_record_kinds == ("evidence",)


# ---------------------------------------------------------------------------
# Completeness smoke
# ---------------------------------------------------------------------------


def test_recorded_evidence_builds_a_graph_and_the_evidence_stage_is_complete(tmp_path):
    rec = _recorder()
    rec.record_source(
        SourceNode(
            node_id=_SOURCE_ID,
            kind="source",
            run_id=_RUN_ID,
            privacy=UNKNOWN_PRIVACY,
            extensions={},
            document_fingerprint=_FP,
            source_format="pdf",
            source_label="paper.pdf",
            config_fingerprint="cfg",
            schema_fingerprint="schema",
            model_ids=("m",),
        )
    )
    build_or_load_evidence_bundle(
        _ctx(tmp_path, tei=_TEI_SOURCES["arxiv"]), _cfg(tmp_path / "c"), recorder=rec, source_id=_SOURCE_ID
    )
    snapshot = rec.snapshot()
    graph = build_graph(snapshot)
    evidence = graph.nodes_of_kind("evidence")
    assert len(evidence) == 496 + 2
    header = [n for n in evidence if n.local_id.startswith("M-")]
    assert {n.local_id for n in header} == {"M-first-author", "M-publication-date"}
    assert all(n.anchor.precision == "absent" for n in header)
    assert not graph.missing_segments
    report = compute_completeness(graph, snapshot)
    assert report.documents[_SOURCE_ID].state == "complete"
    assert not report.documents[_SOURCE_ID].missing_record_kinds
