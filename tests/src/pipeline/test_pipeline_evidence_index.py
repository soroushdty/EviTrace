from pathlib import Path
import importlib.util
import json
import re
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

from quality_control.models import QCBundle, UnifiedRecord

_EVIDENCE_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "evidence_index.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_evidence_index_direct", _EVIDENCE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

build_or_load_evidence_bundle = _MODULE.build_or_load_evidence_bundle
build_chunk_evidence_package = _MODULE.build_chunk_evidence_package
build_paper_evidence_package = _MODULE.build_paper_evidence_package
EvidenceBundle = _MODULE.EvidenceBundle
_build_items_from_tei = _MODULE._build_items_from_tei

# Requirement 3.1 / design.md Property 6: Evidence_IDs are a type prefix
# (S=sentence, T=table, F=figure_caption) followed by a zero-padded 6-digit
# counter.
_EVIDENCE_ID_RE = re.compile(r"^[STF]\d{6}$")


def _qc_context_with_tei(tmp_path: Path) -> QCBundle:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    tei = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Sample Title</title></titleStmt>
      <sourceDesc><biblStruct><monogr><author><surname>Smith</surname></author><imprint><date when="2021"/></imprint></monogr></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <front><abstract><p coords="1;10,10,20,20">Abstract sentence.</p></abstract></front>
    <body>
      <div>
        <head>Introduction</head>
        <p><s coords="1;10,20,30,40">We used MIMIC-III data.</s></p>
      </div>
    </body>
  </text>
</TEI>
"""
    unified = UnifiedRecord(
        document_id="paper1",
        content={
            "exact_text": "Abstract sentence. We used MIMIC-III data.",
            "source_pdf_path": str(pdf_path),
            "grobid_tei_xml": tei,
        },
    )
    return QCBundle(branches=[], unified=unified)


def _qc_context_with_tei_year_fallback(tmp_path: Path) -> QCBundle:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    tei = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Sample Title</title></titleStmt>
      <sourceDesc>
        <biblStruct>
          <monogr>
            <date/>
            <imprint><date when="2022"/></imprint>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <p><s coords="1;10,20,30,40">We used MIMIC-III data.</s></p>
      </div>
    </body>
  </text>
</TEI>
"""
    unified = UnifiedRecord(
        document_id="paper2",
        content={
            "exact_text": "We used MIMIC-III data.",
            "source_pdf_path": str(pdf_path),
            "grobid_tei_xml": tei,
        },
    )
    return QCBundle(branches=[], unified=unified)


def test_build_evidence_bundle_prefills_study_fields(tmp_path: Path):
    ctx = _qc_context_with_tei(tmp_path)
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}
    bundle = build_or_load_evidence_bundle(ctx, cfg)
    assert bundle.prefilled_fields[1] == "Smith"
    assert bundle.prefilled_fields[2] == "2021"
    assert any(item["section_path"] == "Abstract" for item in bundle.evidence_items)


def test_build_evidence_bundle_prefers_publication_date_over_empty_header_date(tmp_path: Path):
    ctx = _qc_context_with_tei_year_fallback(tmp_path)
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}
    bundle = build_or_load_evidence_bundle(ctx, cfg)
    assert bundle.prefilled_fields[2] == "2022"


def test_chunk_package_uses_stable_ids(tmp_path: Path):
    ctx = _qc_context_with_tei(tmp_path)
    cfg = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}
    bundle = build_or_load_evidence_bundle(ctx, cfg)
    fields = [{"field_index": 8, "field_name": "Dataset / database name", "definition": "", "reviewer_question": ""}]
    package = build_chunk_evidence_package(bundle, fields, max_items=10, max_chars=2000)
    assert "S000001" in package or "S000002" in package


# ---------------------------------------------------------------------------
# Requirement 3.1 / Property 6 — Evidence_ID scheme is deterministic and
# matches the required pattern ("S"/"T"/"F" + zero-padded 6-digit counter).
# ---------------------------------------------------------------------------


def _make_tei(n_sentences: int, n_tables: int, n_figures: int) -> str:
    """Build a synthetic TEI document with the requested item counts."""
    sentences = "".join(
        f'<s coords="1;10,{20 + i},30,40">Sentence number {i}.</s>'
        for i in range(n_sentences)
    )
    tables = "".join(
        f'<table coords="1;10,{20 + i},30,40"><row><cell>Table {i} cell</cell></row></table>'
        for i in range(n_tables)
    )
    figures = "".join(
        f'<figure coords="1;10,{20 + i},30,40"><figDesc>Figure {i} caption.</figDesc></figure>'
        for i in range(n_figures)
    )
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
      <div>
        <head>Methods</head>
        <p>{sentences}</p>
        {tables}
        {figures}
      </div>
    </body>
  </text>
</TEI>
"""


@given(
    n_sentences=st.integers(min_value=0, max_value=6),
    n_tables=st.integers(min_value=0, max_value=4),
    n_figures=st.integers(min_value=0, max_value=4),
)
@settings(max_examples=30)
def test_evidence_id_determinism_and_pattern_property(n_sentences, n_tables, n_figures):
    """Parsing the same TEI XML twice SHALL yield identical Evidence_IDs, and
    every Evidence_ID SHALL match ``^[STF]\\d{6}$``.

    Validates: Requirements 3.1; design.md Property 6.
    """
    tei = _make_tei(n_sentences, n_tables, n_figures)

    items_a, _, _ = _build_items_from_tei(tei, "paper_x", "")
    items_b, _, _ = _build_items_from_tei(tei, "paper_x", "")

    ids_a = [item["id"] for item in items_a]
    ids_b = [item["id"] for item in items_b]

    assert ids_a == ids_b, "Parsing identical TEI XML twice produced different Evidence_IDs"
    for evidence_id in ids_a:
        assert _EVIDENCE_ID_RE.match(evidence_id), (
            f"Evidence_ID {evidence_id!r} does not match required pattern ^[STF]\\d{{6}}$"
        )

    # Each type's counter is independent and starts at 1, positionally assigned.
    type_by_id = {item["id"]: item["type"] for item in items_a}
    sentence_ids = sorted(eid for eid, t in type_by_id.items() if t == "sentence")
    table_ids = sorted(eid for eid, t in type_by_id.items() if t == "table")
    figure_ids = sorted(eid for eid, t in type_by_id.items() if t == "figure_caption")
    if sentence_ids:
        assert sentence_ids[0] == "S000001"
    if table_ids:
        assert table_ids[0] == "T000001"
    if figure_ids:
        assert figure_ids[0] == "F000001"


def test_evidence_id_pattern_covers_all_three_types(tmp_path: Path):
    """Example-based check that S/T/F ids are all produced from one document."""
    tei = _make_tei(n_sentences=2, n_tables=1, n_figures=1)
    items, _, _ = _build_items_from_tei(tei, "paper_y", "")
    ids = [item["id"] for item in items]
    assert any(i.startswith("S") for i in ids)
    assert any(i.startswith("T") for i in ids)
    assert any(i.startswith("F") for i in ids)
    for evidence_id in ids:
        assert _EVIDENCE_ID_RE.match(evidence_id)


# ---------------------------------------------------------------------------
# Requirement 3.3 / Property 4 — build_paper_evidence_package() serializes
# evidence items sorted by Evidence_ID in ascending lexicographic order, and
# repeated calls on the same inputs produce byte-identical output.
# ---------------------------------------------------------------------------


def _bundle_with_items(items: list[dict], tmp_path: Path) -> EvidenceBundle:
    evidence_map = {item["id"]: item for item in items}
    return EvidenceBundle(
        paper_id="paper_z",
        tei_xml="",
        evidence_items=items,
        evidence_map=evidence_map,
        prefilled_fields={},
        index_path=tmp_path / "paper_z.evidence.json",
    )


def test_build_paper_evidence_package_sorts_by_evidence_id_ascending(tmp_path: Path):
    # Deliberately out-of-order ids AND deliberately inverse-to-id scores, so
    # that the score-based ranking order and the final id-ascending order
    # disagree. This proves the final serialization applies an explicit
    # Evidence_ID sort rather than incidentally inheriting order from the
    # score-based ranking/tie-break step.
    items = [
        {"id": "S000003", "type": "sentence", "section_path": "Methods", "page": 1,
         "coords": None, "text": "Third sentence about MIMIC-III.", "score": 40, "annotations": {}},
        {"id": "S000001", "type": "sentence", "section_path": "Methods", "page": 1,
         "coords": None, "text": "First sentence about MIMIC-III.", "score": 10, "annotations": {}},
        {"id": "T000001", "type": "table", "section_path": "Methods", "page": 1,
         "coords": None, "text": "Table about MIMIC-III.", "score": 30, "annotations": {}},
        {"id": "F000001", "type": "figure_caption", "section_path": "Methods", "page": 1,
         "coords": None, "text": "Figure about MIMIC-III.", "score": 20, "annotations": {}},
    ]
    bundle = _bundle_with_items(items, tmp_path)
    fields = [{"field_index": 8, "field_name": "Dataset", "definition": "MIMIC-III", "reviewer_question": ""}]

    package_json = build_paper_evidence_package(bundle, fields, max_items=10, max_chars=10000)
    parsed = json.loads(package_json)
    emitted_ids = [item["id"] for item in parsed["evidence"]]

    assert emitted_ids == sorted(emitted_ids), (
        "Evidence items in the paper-level package are not in ascending Evidence_ID order"
    )
    # Ascending lexicographic order means "F..." < "S..." < "T..." by ASCII —
    # note this is the OPPOSITE of the score-descending ranking order
    # (S000003 score=40, T000001 score=30, F000001 score=20, S000001 score=10).
    assert emitted_ids == ["F000001", "S000001", "S000003", "T000001"]


def test_build_paper_evidence_package_is_byte_identical_across_repeated_calls(tmp_path: Path):
    items = [
        {"id": "S000002", "type": "sentence", "section_path": "Methods", "page": 1,
         "coords": None, "text": "Second sentence about MIMIC-III.", "score": 10, "annotations": {}},
        {"id": "S000001", "type": "sentence", "section_path": "Methods", "page": 1,
         "coords": None, "text": "First sentence about MIMIC-III.", "score": 10, "annotations": {}},
    ]
    bundle = _bundle_with_items(items, tmp_path)
    fields = [{"field_index": 8, "field_name": "Dataset", "definition": "MIMIC-III", "reviewer_question": ""}]

    package_a = build_paper_evidence_package(bundle, fields, max_items=10, max_chars=10000)
    package_b = build_paper_evidence_package(bundle, fields, max_items=10, max_chars=10000)

    assert package_a.encode("utf-8") == package_b.encode("utf-8"), (
        "build_paper_evidence_package() is not byte-identical across repeated calls "
        "with identical inputs"
    )


# ---------------------------------------------------------------------------
# Requirement 3.2 / Property 7 — evidence selection respects
# max_evidence_items_per_chunk and max_evidence_chars_per_chunk.
# ---------------------------------------------------------------------------


@given(
    n_items=st.integers(min_value=0, max_value=40),
    text_len=st.integers(min_value=1, max_value=50),
    max_items=st.integers(min_value=0, max_value=20),
    max_chars=st.integers(min_value=0, max_value=400),
)
@settings(max_examples=40)
def test_build_paper_evidence_package_respects_configured_limits_property(
    n_items, text_len, max_items, max_chars, tmp_path_factory
):
    tmp_path = tmp_path_factory.mktemp("evidence_limits")
    items = [
        {
            "id": f"S{i + 1:06d}",
            "type": "sentence",
            "section_path": "Methods",
            "page": 1,
            "coords": None,
            "text": "x" * text_len,
            "score": 10,
            "annotations": {},
        }
        for i in range(n_items)
    ]
    bundle = _bundle_with_items(items, tmp_path)
    fields = [{"field_index": 8, "field_name": "Dataset", "definition": "", "reviewer_question": ""}]

    package_json = build_paper_evidence_package(
        bundle, fields, max_items=max_items, max_chars=max_chars
    )
    parsed = json.loads(package_json)
    selected = parsed["evidence"]

    assert len(selected) <= max_items, (
        f"Selected {len(selected)} items exceeds max_evidence_items_per_chunk={max_items}"
    )
    total_chars = sum(len(item["text"]) for item in selected)
    assert total_chars <= max_chars, (
        f"Selected evidence totals {total_chars} chars, exceeds max_evidence_chars_per_chunk={max_chars}"
    )
