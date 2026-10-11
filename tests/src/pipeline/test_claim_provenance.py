"""Unit tests for ``pipeline.claim_provenance`` (provenance-core 6.4).

The helpers behind ``process_pdf``'s claim emission, tested without the
processor around them:

* ``claim_issue_sink`` -- the per-document wrapper around the recorder's issue
  sink: injects the claim identifier, drops scoped references the validator
  cannot judge, forwards only the three reference codes, never raises;
* ``header_citations`` -- which prefilled field cites which header metadata
  node (only a header-sourced value cites; a fallback cites nothing);
* ``plan_field_candidates`` -- which chunk candidates become earlier revisions
  of a synthesized or merged claim (Requirement 5.5).

Requirements: 5.4, 5.5, 5.7.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from pipeline import claim_provenance as cp
from pipeline.evidence_index import EvidenceBundle
from provenance.identity import derive_claim_id, derive_source_id, format_evidence_id

_FP = "b" * 64
_SOURCE_ID = derive_source_id(_FP)
_HEADER_TEI = (
    '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><sourceDesc><biblStruct>'
    "<analytic><author><persName><surname>Smith</surname></persName></author></analytic>"
    '<monogr><imprint><date when="2021-01-01"/></imprint></monogr>'
    "</biblStruct></sourceDesc></fileDesc></teiHeader><text><body/></text></TEI>"
)


class _Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, code: str, message: str, details: Any) -> None:
        self.calls.append((code, message, dict(details)))


def _details(**extra: Any) -> dict[str, Any]:
    return {"reference": "S9", "reference_kind": "evidence", "position": 0, "field_index": 7, **extra}


# ---------------------------------------------------------------------------
# claim_issue_sink
# ---------------------------------------------------------------------------


def test_sink_injects_the_claim_identifier_and_keeps_the_raw_reference():
    inner = _Sink()
    wrapped = cp.claim_issue_sink(inner, _SOURCE_ID)
    wrapped("dangling_reference", "msg", _details())
    assert len(inner.calls) == 1
    code, message, details = inner.calls[0]
    assert (code, message) == ("dangling_reference", "msg")
    assert details["node_id"] == derive_claim_id(_SOURCE_ID, 7)
    assert details["reference"] == "S9"
    assert details["field_index"] == 7


def test_sink_does_not_mutate_the_details_it_received():
    inner = _Sink()
    original = _details()
    cp.claim_issue_sink(inner, _SOURCE_ID)("dangling_reference", "m", original)
    assert "node_id" not in original


def test_sink_uses_the_revision_of_a_field_with_candidates():
    inner = _Sink()
    revisions = {7: 2}
    wrapped = cp.claim_issue_sink(inner, _SOURCE_ID, revisions=revisions)
    wrapped("dangling_reference", "m", _details())
    assert inner.calls[0][2]["node_id"] == derive_claim_id(_SOURCE_ID, 7, 2)
    revisions[7] = 3  # the mapping is read at call time, not copied
    wrapped("dangling_reference", "m", _details(reference="S8"))
    assert inner.calls[1][2]["node_id"] == derive_claim_id(_SOURCE_ID, 7, 3)


@pytest.mark.parametrize("code", ["dangling_reference", "ambiguous_reference"])
def test_sink_drops_dangling_and_ambiguous_calls_about_scoped_references(code):
    inner = _Sink()
    scoped = format_evidence_id(_SOURCE_ID, "S1")
    cp.claim_issue_sink(inner, _SOURCE_ID)(code, "m", _details(reference=scoped))
    assert inner.calls == []


def test_sink_keeps_a_duplicate_call_about_a_scoped_reference():
    inner = _Sink()
    scoped = format_evidence_id(_SOURCE_ID, "S1")
    cp.claim_issue_sink(inner, _SOURCE_ID)(
        "duplicate_reference", "m", _details(reference=scoped, positions=[0, 1])
    )
    assert [c for c, _, _ in inner.calls] == ["duplicate_reference"]


@pytest.mark.parametrize("code", ["duplicate_identity", "invalid_identifier", "unsupported_claim", "nonsense", ""])
def test_sink_forwards_only_the_three_reference_codes(code):
    inner = _Sink()
    cp.claim_issue_sink(inner, _SOURCE_ID)(code, "m", _details())
    assert inner.calls == []


@pytest.mark.parametrize("code", ["dangling_reference", "ambiguous_reference", "duplicate_reference"])
def test_sink_forwards_each_allowed_code(code):
    inner = _Sink()
    cp.claim_issue_sink(inner, _SOURCE_ID)(code, "m", _details())
    assert [c for c, _, _ in inner.calls] == [code]


@pytest.mark.parametrize("field_index", [None, -1, True, 1.5, "7"])
def test_sink_forwards_an_unidentifiable_item_without_a_node_id(field_index):
    inner = _Sink()
    details = _details()
    details["field_index"] = field_index
    cp.claim_issue_sink(inner, _SOURCE_ID)("dangling_reference", "m", details)
    assert len(inner.calls) == 1
    assert "node_id" not in inner.calls[0][2]


def test_sink_forwards_when_field_index_is_missing_altogether():
    inner = _Sink()
    details = _details()
    del details["field_index"]
    cp.claim_issue_sink(inner, _SOURCE_ID)("dangling_reference", "m", details)
    assert len(inner.calls) == 1
    assert "node_id" not in inner.calls[0][2]


def test_sink_swallows_an_exception_from_the_inner_sink(caplog):
    def boom(code, message, details):
        raise RuntimeError("recorder exploded")

    wrapped = cp.claim_issue_sink(boom, _SOURCE_ID)
    with caplog.at_level(logging.WARNING):
        wrapped("dangling_reference", "m", _details())
    assert any("recorder exploded" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("details", [None, 5, "x", ["a"]])
def test_sink_swallows_non_mapping_details(details):
    inner = _Sink()
    cp.claim_issue_sink(inner, _SOURCE_ID)("dangling_reference", "m", details)
    assert inner.calls == []


def test_sink_swallows_a_malformed_source_id():
    inner = _Sink()
    cp.claim_issue_sink(inner, "not-a-source-id")("dangling_reference", "m", _details())
    # the claim cannot be named, so the finding is still forwarded without it
    assert len(inner.calls) == 1
    assert "node_id" not in inner.calls[0][2]


# ---------------------------------------------------------------------------
# header_citations
# ---------------------------------------------------------------------------


def _bundle(prefilled: dict[int, str], *, tei: str = _HEADER_TEI, year_provenance: str = "tei_header") -> EvidenceBundle:
    from pathlib import Path

    return EvidenceBundle(
        paper_id="paper",
        tei_xml=tei,
        evidence_items=[],
        evidence_map={},
        prefilled_fields=prefilled,
        index_path=Path("unused"),
        year_provenance=year_provenance,
    )


_BOTH = {format_evidence_id(_SOURCE_ID, "M-first-author"), format_evidence_id(_SOURCE_ID, "M-publication-date")}


def test_header_citations_cite_both_header_nodes_for_header_sourced_values():
    cites = cp.header_citations(_bundle({1: "Smith", 2: "2021"}), _SOURCE_ID, _BOTH)
    assert cites == {1: "M-first-author", 2: "M-publication-date"}


def test_header_citations_cite_nothing_for_a_fallback_author():
    cites = cp.header_citations(_bundle({1: "paper", 2: "2021"}), _SOURCE_ID, _BOTH)
    assert 1 not in cites and cites[2] == "M-publication-date"


def test_header_citations_cite_nothing_for_a_non_header_year():
    for provenance in ("pdf_metadata", "first_page_text", "filename_pattern", ""):
        cites = cp.header_citations(_bundle({1: "Smith", 2: "2021"}, year_provenance=provenance), _SOURCE_ID, _BOTH)
        assert cites == {1: "M-first-author"}, provenance


def test_header_citations_cite_nothing_when_the_year_is_nr():
    cites = cp.header_citations(_bundle({1: "Smith", 2: "nr"}), _SOURCE_ID, _BOTH)
    assert 2 not in cites


def test_header_citations_cite_nothing_when_the_header_node_is_not_a_known_evidence_node():
    cites = cp.header_citations(_bundle({1: "Smith", 2: "2021"}), _SOURCE_ID, set())
    assert cites == {}


def test_header_citations_cite_nothing_when_the_current_tei_disagrees_with_the_prefill():
    cites = cp.header_citations(_bundle({1: "Jones", 2: "1999"}), _SOURCE_ID, _BOTH)
    assert cites == {}


def test_header_citations_without_tei_cite_nothing():
    assert cp.header_citations(_bundle({1: "paper", 2: "nr"}, tei=""), _SOURCE_ID, _BOTH) == {}


# ---------------------------------------------------------------------------
# plan_field_candidates
# ---------------------------------------------------------------------------


def _entry(i: int, v: str, c: str = "h", loc: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"i": i, "v": v, "loc": list(loc), "c": c}


def test_plan_conflict_makes_every_candidate_a_revision_and_the_final_the_next_one():
    a, b = _entry(3, "x"), _entry(3, "y")
    plan = cp.plan_field_candidates([(1, [a]), (2, [b])], [3], {})
    assert set(plan) == {3}
    entry = plan[3]
    assert entry.kind == "conflict"
    assert [chunk for chunk, _ in entry.candidates] == [1, 2]
    assert entry.final_revision == 2
    assert entry.contributor_positions == (0, 1)
    assert cp.revisions_of(plan) == {3: 2}


def test_plan_merge_needs_two_non_empty_candidates():
    plan = cp.plan_field_candidates(
        [(1, [_entry(3, "x"), _entry(4, "z")]), (2, [_entry(3, " x "), _entry(4, "")])], [], {}
    )
    assert set(plan) == {3}
    assert plan[3].kind == "merge"
    assert plan[3].final_revision == 2
    assert plan[3].contributor_positions == (0, 1)


def test_plan_ignores_a_field_with_one_non_empty_candidate_and_empty_fields():
    plan = cp.plan_field_candidates([(1, [_entry(3, "x")]), (2, [_entry(3, ""), _entry(5, "nr", "nr")])], [], {})
    assert plan == {}


def test_plan_conflict_contributors_are_the_candidates_shown_to_synthesis():
    entries = [_entry(3, f"v{n}") for n in range(4)]
    chunks = [(n + 1, [e]) for n, e in enumerate(entries)]
    plan = cp.plan_field_candidates(chunks, [3], {3: [entries[1], entries[3]]})
    assert plan[3].contributor_positions == (1, 3)
    assert plan[3].dropped_positions == (0, 2)
    assert plan[3].final_revision == 4


def test_plan_conflict_without_any_candidate_entry_is_skipped():
    assert cp.plan_field_candidates([(1, [_entry(4, "x")])], [3], {}) == {}


def test_plan_tolerates_entries_without_a_usable_index():
    plan = cp.plan_field_candidates([(1, [{"v": "x"}, {"i": "3", "v": "y"}, _entry(3, "x")])], [], {})
    assert plan == {}


# ---------------------------------------------------------------------------
# EvidenceBundle: the two provenance-only companions
# ---------------------------------------------------------------------------


def _bundle_inputs(tmp_path):
    from pipeline.evidence_index import build_or_load_evidence_bundle
    from provenance.models import RunIdentity
    from provenance.recorder import ProvenanceRecorder
    from quality_control.models import QCBundle, UnifiedRecord

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    ctx = QCBundle(
        branches=[],
        unified=UnifiedRecord(
            document_id="paper1",
            content={
                "exact_text": "One sentence.",
                "source_pdf_path": str(pdf),
                "grobid_tei_xml": _HEADER_TEI.replace(
                    "<text><body/></text>",
                    '<text><body><div><head>M</head><p><s coords="1,1,1,1,1">One sentence.</s></p></div></body></text>',
                ),
            },
        ),
    )
    run = RunIdentity(
        run_id="run:0123456789abcdef",
        config_fingerprint="cfg",
        schema_fingerprint="schema",
        model_ids=("m",),
        document_fingerprints={_SOURCE_ID: _FP},
        created_at="2026-10-11T00:00:00+00:00",
    )
    return build_or_load_evidence_bundle, ctx, ProvenanceRecorder(run)


def test_year_provenance_is_on_the_bundle_on_the_miss_and_the_hit_path(tmp_path):
    build, ctx, _ = _bundle_inputs(tmp_path)
    config = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}
    miss = build(ctx, config)
    hit = build(ctx, config)
    assert miss.year_provenance == hit.year_provenance == "tei_header"
    assert miss == hit  # the companions do not take part in equality


def test_the_evidence_nodes_are_on_the_bundle_only_when_a_recorder_recorded_them(tmp_path):
    build, ctx, recorder = _bundle_inputs(tmp_path)
    config = {"evidence_cache_dir": str(tmp_path / "cache"), "addons": {}}
    plain = build(ctx, config)
    assert plain.provenance_evidence_nodes == ()
    recorded = build(ctx, config, recorder=recorder, source_id=_SOURCE_ID)
    ids = {node.node_id for node in recorded.provenance_evidence_nodes}
    assert ids == {n for n, node in recorder.snapshot().nodes.items() if node.kind == "evidence"}
    assert format_evidence_id(_SOURCE_ID, "M-first-author") in ids
    assert recorded == plain


def test_the_cache_bytes_do_not_change_with_a_recorder(tmp_path):
    build, ctx, recorder = _bundle_inputs(tmp_path)
    plain = build(ctx, {"evidence_cache_dir": str(tmp_path / "plain"), "addons": {}})
    recorded = build(ctx, {"evidence_cache_dir": str(tmp_path / "rec"), "addons": {}}, recorder=recorder, source_id=_SOURCE_ID)
    assert plain.index_path.read_bytes() == recorded.index_path.read_bytes()
