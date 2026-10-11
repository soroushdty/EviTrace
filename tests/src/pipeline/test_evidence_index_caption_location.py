"""Caption sentences inherit the enclosing figure's location (provenance-core 6.6).

GROBID nests a figure's caption as ``<figure><figDesc><div><p><s>``; those ``<s>``
(and ``<p>``) carry no ``coords`` of their own, so the evidence index used to emit
them with ``page None`` / ``coords None`` and the provenance evidence adapter
recorded them as anchor-less (Requirements 4.3, 4.4, 4.5).  The index now copies the
enclosing ``<figure>``'s page and coordinates onto such an item **only when neither
the sentence nor its paragraph carries coordinates**.

Contract pinned here:

* item identifiers are untouched -- no item added, removed, renumbered or reordered
  (golden digest of the id sequence per fixture, captured from the code before the
  change);
* every other field of every item is unchanged -- a golden digest of the whole item
  list with the caption items' ``page``/``coords`` reset to ``None`` equals the
  digest captured before the change, so non-caption items are byte-identical;
* the caption items are identified here by an independent walk of the raw TEI
  (a ``<s>`` with a ``<figure>`` ancestor), not by asking the implementation;
* page and coordinates are prompt bytes (``select_paper_evidence`` ->
  ``serialise_evidence_package`` -> ``_shared_paper_prefix``): only the caption items'
  serialised bytes change, and the shared prefix stays byte-identical across chunks.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from pipeline.evidence_index import (
    EvidenceBundle,
    _build_items_from_tei,
    _parse_coords,
    _safe_text,
    select_paper_evidence,
    serialise_evidence_package,
)
from provenance.adapters.evidence_adapter import evidence_nodes_from_items
from tests.helpers.grobid_tei import (
    FIXTURE_FILES,
    TEI_NS,
    load_tei_root,
    load_tei_text,
)

_NS = f"{{{TEI_NS}}}"
_PAPER_ID = "paper"
_SOURCE_ID = "src:" + "a" * 64
_RUN_ID = "run:0123456789abcdef"

#: Captured from the unmodified ``_build_items_from_tei`` (HEAD c13a8d3) with
#: ``paper_id="paper"`` and ``source_pdf=""``.  ``ids``: sha256 of the ids joined by
#: newline; ``legacy``: sha256 of ``json.dumps(items, sort_keys=True, ensure_ascii=False)``.
_GOLDEN = {
    "biorxiv": {
        "count": 184,
        "ids": "2781a5c0c883c55484fc189d48553330f657de7f0c559cf510f214739e1ea957",
        "legacy": "35dd1e133fbc3e8b500eba4bb0363ea11274a2bfade961bda56771061ab76a30",
        "captions": 62,
    },
    "arxiv": {
        "count": 496,
        "ids": "aae1329f86ecaf46ff98ccd92b44e2f26f66430027c269426620f6679bb0fea0",
        "legacy": "46a2810b735b7d6a99d3680701e38ef0836dcd4765a4e97f834a629330a69c6d",
        "captions": 64,
    },
    "plosone": {
        "count": 241,
        "ids": "c6935c5e2b54c8692a34f3170f3161763bed0527078f0612487ba741d699a263",
        "legacy": "917cb30fad2646e24e98690673a9774bb97c3e82272979a73a1412c7a08fcb2a",
        "captions": 47,
    },
}
_FIXTURES = list(FIXTURE_FILES)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _items(name: str) -> list[dict]:
    items, _, _ = _build_items_from_tei(load_tei_text(name), _PAPER_ID, "")
    return items


def _caption_ids_by_independent_walk(name: str) -> dict[str, ET.Element]:
    """``{item id: enclosing <figure>}`` for every sentence item that sits in a figure.

    Mirrors only the *numbering* of sentence items (document order over body
    ``<div>``s, empty text skipped); figure membership comes from the TEI parent chain.
    """
    root = load_tei_root(name)
    parent = {child: par for par in root.iter() for child in par}
    body = root.find(f".//{_NS}body")
    assert body is not None
    out: dict[str, ET.Element] = {}
    counter = 1
    for div in body.findall(f".//{_NS}div"):
        for sent in div.findall(f".//{_NS}s"):
            if not _safe_text("".join(sent.itertext())):
                continue
            sid = f"S{counter:06d}"
            counter += 1
            node = parent.get(sent)
            while node is not None:
                if node.tag == f"{_NS}figure":
                    out[sid] = node
                    break
                node = parent.get(node)
    return out


# ---------------------------------------------------------------------------
# Real GROBID fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", _FIXTURES)
def test_item_identifier_sequence_is_unchanged(name):
    items = _items(name)
    ids = [i["id"] for i in items]
    assert len(items) == _GOLDEN[name]["count"]
    assert _sha("\n".join(ids)) == _GOLDEN[name]["ids"]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("name", _FIXTURES)
def test_every_caption_sentence_carries_its_figures_page_and_coords(name):
    items = _items(name)
    by_id = {i["id"]: i for i in items}
    captions = _caption_ids_by_independent_walk(name)
    assert len(captions) == _GOLDEN[name]["captions"]
    for sid, fig in captions.items():
        expected = _parse_coords(fig.attrib.get("coords"))
        item = by_id[sid]
        assert item["type"] == "sentence"
        assert isinstance(item["page"], int) and item["page"] >= 1
        assert isinstance(item["coords"], list) and len(item["coords"]) == 4
        assert item["coords"] == expected["coords"]
        assert item["page"] == expected["page"]


@pytest.mark.parametrize("name", _FIXTURES)
def test_nothing_but_caption_page_and_coords_differs_from_the_legacy_output(name):
    """Resetting the caption items' location reproduces the pre-change items exactly."""
    items = _items(name)
    caption_ids = set(_caption_ids_by_independent_walk(name))
    legacy_view = [
        {**i, "page": None, "coords": None} if i["id"] in caption_ids else i for i in items
    ]
    digest = _sha(json.dumps(legacy_view, sort_keys=True, ensure_ascii=False))
    assert digest == _GOLDEN[name]["legacy"]


def test_no_non_metadata_item_is_left_without_a_location_on_the_fixtures():
    for name in _FIXTURES:
        for item in _items(name):
            if item["section_path"] == "Metadata":
                continue
            assert isinstance(item["page"], int), item["id"]
            assert isinstance(item["coords"], list) and len(item["coords"]) == 4, item["id"]


def test_evidence_adapter_precision_counts_over_the_three_fixtures():
    """745 + 173 exact; the 3 title items stay approximate (page 1 only); none absent."""
    counts: Counter[str] = Counter()
    for name in _FIXTURES:
        nodes = evidence_nodes_from_items(
            _items(name), source_id=_SOURCE_ID, run_id=_RUN_ID, producing_backend="grobid"
        )
        counts.update(n.anchor.precision for n in nodes)
    assert counts["exact"] == 745 + 173
    assert counts["approximate"] == 3
    assert counts["absent"] == 0


# ---------------------------------------------------------------------------
# Prompt bytes (declared prompt-content change)
# ---------------------------------------------------------------------------


def _bundle(paper_id: str, items: list[dict]) -> EvidenceBundle:
    return EvidenceBundle(
        paper_id=paper_id,
        tei_xml="",
        evidence_items=items,
        evidence_map={i["id"]: i for i in items},
        prefilled_fields={},
        index_path=Path("unused.evidence.json"),
    )


_ALL_FIELDS = [
    {"field_index": 8, "field_name": "Dataset", "definition": "sample population cohort", "reviewer_question": ""},
    {"field_index": 20, "field_name": "Model", "definition": "network architecture", "reviewer_question": ""},
]


def _prompts():
    from agents.openai import prompts

    return prompts


def test_only_caption_items_change_serialised_bytes_and_prefix_is_stable_across_chunks():
    name = "plosone"
    items = _items(name)
    caption_ids = set(_caption_ids_by_independent_walk(name))
    legacy_items = [
        {**i, "page": None, "coords": None} if i["id"] in caption_ids else i for i in items
    ]

    new_sel, _ = select_paper_evidence(
        _bundle(_PAPER_ID, items), _ALL_FIELDS, max_items=10_000, max_chars=10_000_000
    )
    old_sel, _ = select_paper_evidence(
        _bundle(_PAPER_ID, legacy_items), _ALL_FIELDS, max_items=10_000, max_chars=10_000_000
    )
    # Location plays no part in ranking: same selection, same order.
    assert [s["id"] for s in new_sel] == [s["id"] for s in old_sel]

    changed = {
        n["id"]
        for n, o in zip(new_sel, old_sel)
        if json.dumps(n, ensure_ascii=False) != json.dumps(o, ensure_ascii=False)
    }
    assert changed == caption_ids

    new_pkg = serialise_evidence_package(_bundle(_PAPER_ID, items), new_sel)
    old_pkg = serialise_evidence_package(_bundle(_PAPER_ID, legacy_items), old_sel)
    assert new_pkg != old_pkg

    prompts = _prompts()
    chunk_a = [{"field_index": 8, "field_name": "Dataset", "definition": "", "reviewer_question": ""}]
    chunk_b = [{"field_index": 20, "field_name": "Model", "definition": "", "reviewer_question": ""}]
    prefix = prompts._shared_paper_prefix(new_pkg)
    for message in (
        prompts.build_user_message(new_pkg, chunk_a),
        prompts.build_user_message(new_pkg, chunk_b),
        prompts.build_user_message(new_pkg, chunk_b, prior_context=[{"field_index": 8}]),
        prompts.build_cache_warmup_message(new_pkg),
        prompts.build_cache_warmup_message(new_pkg, chunk_b),
    ):
        assert message.startswith(prefix)
    assert prompts.build_user_message(new_pkg, chunk_a).encode().startswith(prefix.encode())


# ---------------------------------------------------------------------------
# Synthetic TEI: the inheritance rule
# ---------------------------------------------------------------------------


def _tei(body_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<TEI xmlns="{TEI_NS}"><teiHeader/><text><body>{body_xml}</body></text></TEI>'
    )


_FIG = "{pg},10.0,20.0,100.0,40.0"


def _by_text(items: list[dict]) -> dict[str, dict]:
    return {i["text"]: i for i in items if i["type"] == "sentence"}


def test_sentence_with_own_coords_or_paragraph_coords_keeps_them():
    xml = _tei(
        '<figure xml:id="fig_0" coords="4,10.0,20.0,100.0,40.0"><figDesc><div>'
        '<p><s coords="4,1.0,2.0,3.0,4.0">Own coords.</s><s>Bare sentence.</s></p>'
        '<p coords="5,5.0,6.0,7.0,8.0"><s>Paragraph coords.</s></p>'
        "</div></figDesc></figure>"
    )
    items, _, _ = _build_items_from_tei(xml, "p", "")
    by_text = _by_text(items)
    assert by_text["Own coords."]["page"] == 4
    assert by_text["Own coords."]["coords"] == [1.0, 2.0, 4.0, 6.0]
    assert by_text["Paragraph coords."]["page"] == 5
    assert by_text["Paragraph coords."]["coords"] == [5.0, 6.0, 12.0, 14.0]
    # Only the bare sentence (neither it nor its <p> has coords) inherits.
    assert by_text["Bare sentence."]["page"] == 4
    assert by_text["Bare sentence."]["coords"] == [10.0, 20.0, 110.0, 60.0]


def test_paragraph_without_sentences_inside_a_caption_inherits_the_figure_location():
    xml = _tei(
        '<figure xml:id="fig_0" coords="3,10.0,20.0,100.0,40.0"><figDesc><div>'
        "<p>Plain caption paragraph.</p></div></figDesc></figure>"
    )
    items, _, _ = _build_items_from_tei(xml, "p", "")
    item = _by_text(items)["Plain caption paragraph."]
    assert item["id"] == "S000001"
    assert item["page"] == 3
    assert item["coords"] == [10.0, 20.0, 110.0, 60.0]


def test_figure_without_coords_leaves_caption_sentences_unlocated():
    xml = _tei(
        '<figure xml:id="fig_0"><figDesc><div><p><s>No location anywhere.</s></p></div></figDesc></figure>'
    )
    items, _, _ = _build_items_from_tei(xml, "p", "")
    item = _by_text(items)["No location anywhere."]
    assert item["page"] is None
    assert item["coords"] is None


def test_body_sentences_outside_figures_never_borrow_a_figure_location():
    xml = _tei(
        '<div><head>Methods</head><p><s>Plain body sentence.</s></p></div>'
        '<figure xml:id="fig_0" coords="2,10.0,20.0,100.0,40.0"><figDesc><div>'
        "<p><s>Caption sentence.</s></p></div></figDesc></figure>"
    )
    items, _, _ = _build_items_from_tei(xml, "p", "")
    by_text = _by_text(items)
    assert by_text["Plain body sentence."]["page"] is None
    assert by_text["Plain body sentence."]["coords"] is None
    assert by_text["Caption sentence."]["page"] == 2


def test_nearest_enclosing_figure_wins_and_ids_are_unchanged():
    xml = _tei(
        '<figure xml:id="outer" coords="1,0.0,0.0,50.0,50.0"><figDesc><div><p><s>Outer sentence.</s></p></div></figDesc>'
        '<figure xml:id="inner" coords="2,5.0,5.0,10.0,10.0"><figDesc><div><p><s>Inner sentence.</s></p></div></figDesc></figure>'
        "</figure>"
    )
    items, _, _ = _build_items_from_tei(xml, "p", "")
    by_text = _by_text(items)
    assert by_text["Inner sentence."]["page"] == 2
    assert by_text["Inner sentence."]["coords"] == [5.0, 5.0, 15.0, 15.0]
    sentence_ids = sorted(i["id"] for i in items if i["type"] == "sentence")
    assert sentence_ids == [f"S{n:06d}" for n in range(1, len(sentence_ids) + 1)]
