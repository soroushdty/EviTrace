"""End-to-end OCR provenance through ``build_qc_bundle`` (integration).

Unlike the routing tests (``test_page_routing.py``,
``test_page_classification_cache.py``), which mock ``run_quality_control``
and the annotation chain, this module mocks **only the extraction backends**
and lets the real chain run:

    build_qc_bundle
      -> _build_branches_for_classifications (block tagging, page merge)
      -> run_quality_control (rater, IAA, adjudication, _select_branch_roles)
      -> reconciler.reconcile (_build_sentences, alignment entries)
      -> w3c_annotation.project + generate_w3c_jsonld

for a mixed PDF (pages 0 and 2 native, page 1 scanned), twice: a TEI-cache
miss and then a TEI + sidecar hit.  It asserts, from the emitted artifact
alone, that every annotation's OCR marking equals its sentence's marking
(4.8), that the OCR annotation's region comes from its own OCR block, that
the ``paddleocr`` secondary populates the structural layer and provenance
names both branches (2.4), and that branches, routing, sentences and
annotation ids are byte-identical across the miss and the hit (6.4, 3.1).

Feature: risk-remediation, task 12.1.
Requirements: 4.8, 6.4, 2.4 (also touches 2.x, 3.1, 4.5, 5.1).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pdf_extractor.extraction.scan_detector import PageScanClassification


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_TEI_NS = "http://www.tei-c.org/ns/1.0"

#: Small GROBID-style TEI.  The QC TEI parser routes each ``<s>`` to the page
#: of its parent ``<p>`` ``coords`` (1-based), so the first div's sentences
#: land on page 0 and the second div's on page 2 -- the two native pages.
_TEI_XML = (
    f'<TEI xmlns="{_TEI_NS}"><text><body>'
    '<div><head>Methods</head>'
    '<p coords="1,10,10,100,20"><s>Native one.</s><s>Native two.</s></p></div>'
    '<div><head>Results</head>'
    '<p coords="3,10,10,100,20"><s>Native three.</s></p></div>'
    "</body></text></TEI>"
)

_NATIVE_SENTENCES = ["Native one.", "Native two.", "Native three."]

#: Two OCR blocks on the scanned page with different boxes, so "region from
#: the sentence's own block" is distinguishable from "first block on the page".
_OCR_TEXT = "Scanned sentence here. Another scanned one."
_OCR_SENTENCES = ["Scanned sentence here", "Another scanned one."]
_OCR_BBOX = (10, 20, 110, 60)
_OCR_XYWH = "10,20,100,40"  # (x0, y0, x1 - x0, y1 - y0) of _OCR_BBOX
_OCR_TEXT_2 = "Third scanned line."
_OCR_BBOX_2 = (15, 70, 215, 90)
_OCR_XYWH_2 = "15,70,200,20"
_SCANNED_PAGE = 1

_UUID5_ID_RE = re.compile(
    r"^urn:evitrace:anno:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _mixed_classifications() -> list[PageScanClassification]:
    """Pages 0 and 2 native; page 1 scanned via stage 1 (empty text)."""
    return [
        PageScanClassification(page_index=0, is_native=True, triggered_stages=[],
                               stage_values={"word_count": 300.0}),
        PageScanClassification(page_index=1, is_native=False, triggered_stages=[1],
                               stage_values={"word_count": 0.0}),
        PageScanClassification(page_index=2, is_native=True, triggered_stages=[],
                               stage_values={"word_count": 280.0}),
    ]


def _plumber_blocks() -> list[dict]:
    return [
        {"text": f"plumber page {i}", "page_index": i, "block_bbox": None, "spans": []}
        for i in range(3)
    ]


def _paddle_blocks() -> list[dict]:
    return [
        {
            "text": _OCR_TEXT,
            "page_index": _SCANNED_PAGE,
            "block_bbox": _OCR_BBOX,
            "span_bboxes": [],
            "ocr_confidence": 0.97,
        },
        {
            "text": _OCR_TEXT_2,
            "page_index": _SCANNED_PAGE,
            "block_bbox": _OCR_BBOX_2,
            "span_bboxes": [],
            "ocr_confidence": 0.91,
        },
    ]


def _qc_config(tei_cache_dir: Path) -> dict:
    return {
        "ocr": True,
        "quality_control": {
            "ocr": {"rasterization_dpi": 150},
            "grobid_integration": {"failure_behavior": "fallback"},
            "grobid": {
                "url": "http://localhost:8070",
                "timeout": 300,
                "segment_sentences": True,
                "tei_cache_dir": str(tei_cache_dir),
            },
            "scan_detection": {
                "text_density_threshold": 50,
                "alpha_ratio_threshold": 0.60,
                "image_dominance_threshold": 0.85,
            },
            "artifact_generator": {"export_to_disk": False, "output_dir": "output/qc_artifacts"},
            "rater": {"attributes": []},
            "iaa_calculator": {"thresholds": {}, "agreement_metrics": []},
            "adjudicator": {"strategy": "placeholder"},
            "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
        },
        "text_processor": {
            "class": "text_processing.composite.DefaultTextProcessor",
            "sentence_tokenizer": {"backend": "nltk_punkt"},
        },
    }


def _mock_fitz(page_count: int) -> MagicMock:
    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter([MagicMock() for _ in range(page_count)]))
    mock_fitz.open = MagicMock(return_value=mock_doc)
    return mock_fitz


@pytest.fixture(autouse=True)
def _fake_sentence_tokenizer(monkeypatch):
    """Deterministic sentence splitting: no NLTK/spaCy model is ever loaded.

    ``build_qc_bundle`` and ``run_quality_control`` each instantiate the
    configured ``DefaultTextProcessor``; patching the class method covers
    both (and the pipeline's cached instance).
    """
    monkeypatch.setattr(
        "text_processing.composite.DefaultTextProcessor.tokenize_sentences",
        lambda self, text: [s for s in text.split(". ") if s],
    )


# ---------------------------------------------------------------------------
# Harness: backends mocked, QC + reconciler + annotation chain real
# ---------------------------------------------------------------------------


class _Run:
    def __init__(self) -> None:
        self.ctx = None
        self.classify_page = MagicMock()
        self.grobid = MagicMock(return_value=(_TEI_XML, []))
        self.pdfplumber = MagicMock(return_value=_plumber_blocks())
        self.paddleocr = MagicMock(return_value=_paddle_blocks())
        self.pymupdf = MagicMock(return_value=([], []))
        self.fitz = _mock_fitz(3)


def _run_bundle(pdf: Path, qc_config: dict, *, classifications: list) -> _Run:
    run = _Run()
    run.classify_page.side_effect = list(classifications)

    with patch("pipeline.extraction_pipeline.extract_with_grobid", run.grobid), \
         patch("pipeline.extraction_pipeline.extract_with_pdfplumber", run.pdfplumber), \
         patch("pipeline.extraction_pipeline.extract_with_paddleocr", run.paddleocr), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", run.pymupdf), \
         patch("pipeline.extraction_pipeline.scan_detector") as mock_scan_mod, \
         patch.dict(sys.modules, {"fitz": run.fitz}):
        mock_scan_mod.classify_page = run.classify_page
        mock_scan_mod.PageScanClassification = PageScanClassification

        from pipeline.extraction_pipeline import build_qc_bundle

        run.ctx = build_qc_bundle(pdf_path=pdf, pdf_name=pdf.stem, qc_config=qc_config)
    return run


def _selector(anno: dict, selector_type: str) -> dict | None:
    return next(
        (s for s in anno["target"]["selector"] if s["type"] == selector_type), None
    )


def _fragment_page_and_xywh(anno: dict) -> tuple[int, str]:
    """Parse ``page=<n>&xywh=<x,y,w,h>`` from the FragmentSelector value."""
    frag = _selector(anno, "FragmentSelector")
    assert frag is not None, anno
    assert frag["conformsTo"] == "http://www.w3.org/TR/media-frags/"
    m = re.fullmatch(r"page=(\d+)&xywh=([0-9.,-]+)", frag["value"])
    assert m, frag["value"]
    return int(m.group(1)), m.group(2)


@pytest.fixture
def two_runs(tmp_path):
    """Run 1: TEI miss (classification computed, TEI + sidecar written).
    Run 2: TEI + sidecar hit with fresh mocks and no classifications."""
    pdf = tmp_path / "mixed.pdf"
    pdf.write_bytes(b"%PDF-1.4 mixed native/scanned fixture")
    qc_config = _qc_config(tmp_path / "tei_cache")

    miss = _run_bundle(pdf, qc_config, classifications=_mixed_classifications())
    hit = _run_bundle(pdf, qc_config, classifications=[])
    return pdf, miss, hit


# ---------------------------------------------------------------------------
# 4.8 -- annotation OCR marking equals the sentence marking, end to end
# ---------------------------------------------------------------------------


def test_every_annotation_marking_equals_its_sentence_marking(two_runs):
    """Requirements: risk-remediation 4.8, 4.2, 4.3, 4.4, 4.9, 3.1"""
    pdf, miss, _hit = two_runs
    ctx = miss.ctx
    unified = ctx.unified

    sentences = unified.semantic.sentences
    annotations = unified.content["annotations"]

    assert annotations, "the annotation chain must emit records for a mixed document"
    assert len(annotations) == len(sentences)

    # 4.4: a mixed document carries both markings.
    assert [s["text"] for s in sentences] == [
        "Native one.", "Native two.", *_OCR_SENTENCES, _OCR_TEXT_2, "Native three.",
    ]
    assert [s["ocr_derived"] for s in sentences] == [False, False, True, True, True, False]
    assert [s["page_index"] for s in sentences] == [0, 0, 1, 1, 1, 2]

    for anno, sent in zip(annotations, sentences):
        assert anno["type"] == "Annotation"
        assert anno["body"]["ocr_derived"] is sent["ocr_derived"]
        assert anno["body"]["value"] == sent["text"]
        quote = _selector(anno, "TextQuoteSelector")
        assert quote is not None and quote["exact"] == sent["text"]
        assert _UUID5_ID_RE.match(anno["id"]), anno["id"]
        assert anno["target"]["source"] == f"urn:evitrace:document:{pdf.stem}"

    assert len({a["id"] for a in annotations}) == len(annotations)


def test_ocr_annotation_region_is_its_own_block_and_native_ones_have_offsets(two_runs):
    """Requirements: risk-remediation 4.8, 4.5, 4.6, 4.3"""
    _pdf, miss, _hit = two_runs
    annotations = miss.ctx.unified.content["annotations"]

    ocr = [a for a in annotations if a["body"]["ocr_derived"] is True]
    native = [a for a in annotations if a["body"]["ocr_derived"] is False]
    assert len(ocr) == 3 and len(native) == 3

    # 4.5: each OCR annotation's region is its *own* block's box, not the
    # first box on the page.
    expected_xywh = {
        _OCR_SENTENCES[0]: _OCR_XYWH,
        _OCR_SENTENCES[1]: _OCR_XYWH,
        _OCR_TEXT_2: _OCR_XYWH_2,
    }
    for anno in ocr:
        page, xywh = _fragment_page_and_xywh(anno)
        assert page == _SCANNED_PAGE
        assert xywh == expected_xywh[anno["body"]["value"]], anno["body"]["value"]
        assert _selector(anno, "TextPositionSelector") is None
    assert {xywh for _, xywh in map(_fragment_page_and_xywh, ocr)} == {_OCR_XYWH, _OCR_XYWH_2}

    for anno in native:
        pos = _selector(anno, "TextPositionSelector")
        assert pos is not None, anno
        assert pos["start"] >= 0 and pos["end"] > pos["start"]
        assert _selector(anno, "FragmentSelector") is None

    # 4.6: the alignment entry for each OCR sentence carries its region.
    entries = miss.ctx.unified.alignment.sentence_to_char_range
    assert len(entries) == len(annotations)
    ocr_entries = [e for e in entries if e["ocr_derived"]]
    assert [e["bbox"] for e in ocr_entries] == [list(_OCR_BBOX)] * 2 + [list(_OCR_BBOX_2)]
    assert all(e["bbox"] is None for e in entries if not e["ocr_derived"])


# ---------------------------------------------------------------------------
# 2.4 -- branches used are named; paddleocr secondary reaches structural
# ---------------------------------------------------------------------------


def test_provenance_names_branches_and_structural_layer_is_populated(two_runs):
    """Requirements: risk-remediation 2.4, 2.1, 2.2, 4.1, 5.1"""
    _pdf, miss, _hit = two_runs
    ctx = miss.ctx

    assert [(b.source, b.index) for b in ctx.branches] == [("grobid", 0), ("paddleocr", 1)]
    assert ctx.decision.primary_extractor == "grobid"

    provenance = ctx.unified.content["provenance"]
    assert provenance["primary_branch_source"] == "grobid"
    assert provenance["secondary_branch_source"] == "paddleocr"
    assert provenance["branch_selection"] == "adjudicated"
    assert provenance["adjudication_decisions"]["primary_extractor"] == "grobid"
    assert provenance["adjudication_decisions"]["rationale"]

    blocks = ctx.unified.structural.blocks
    assert blocks, "paddleocr secondary must populate structural.blocks"
    assert [(b["source"], b["ocr_derived"], b["page_index"]) for b in blocks] == [
        ("pdfplumber", False, 0), ("paddleocr", True, 1), ("paddleocr", True, 1),
        ("pdfplumber", False, 2),
    ]
    assert [b["block_bbox"] for b in blocks[1:3]] == [_OCR_BBOX, _OCR_BBOX_2]

    # 5.1: document text = primary text plus the OCR blocks that produced sentences.
    exact_text = ctx.unified.content["exact_text"]
    assert _OCR_TEXT in exact_text
    assert _OCR_TEXT_2 in exact_text
    for text in _NATIVE_SENTENCES:
        assert text in exact_text
    assert "plumber page" not in exact_text


# ---------------------------------------------------------------------------
# 6.4 -- cache hit reproduces the miss exactly
# ---------------------------------------------------------------------------


def test_cache_hit_reproduces_branches_routing_sentences_and_annotation_ids(two_runs):
    """Requirements: risk-remediation 6.4, 6.1, 6.2, 6.6, 3.1"""
    _pdf, miss, hit = two_runs

    # Run 1 really was a miss; run 2 really was a hit.
    assert miss.classify_page.call_count == 3
    miss.grobid.assert_called_once()
    miss.paddleocr.assert_called_once()

    hit.classify_page.assert_not_called()
    hit.grobid.assert_not_called()
    hit.fitz.open.assert_not_called()
    hit.pdfplumber.assert_called_once()
    hit.paddleocr.assert_called_once()

    assert [(b.source, b.index) for b in hit.ctx.branches] == \
        [(b.source, b.index) for b in miss.ctx.branches]
    assert [b.payload for b in hit.ctx.branches] == [b.payload for b in miss.ctx.branches]

    routing = hit.ctx.unified.content["page_routing"]
    assert routing == miss.ctx.unified.content["page_routing"]
    assert [r["routing_reason"] for r in routing] == [
        "mixed_native_page", "stage_1_empty_text", "mixed_native_page",
    ]
    assert [r["selected_extractor"] for r in routing] == [
        "grobid+pdfplumber", "paddleocr+pymupdf", "grobid+pdfplumber",
    ]

    assert hit.ctx.unified.semantic.sentences == miss.ctx.unified.semantic.sentences
    assert hit.ctx.unified.alignment.sentence_to_char_range == \
        miss.ctx.unified.alignment.sentence_to_char_range
    assert hit.ctx.unified.content["provenance"] == miss.ctx.unified.content["provenance"]
    assert hit.ctx.unified.content["exact_text"] == miss.ctx.unified.content["exact_text"]

    hit_annotations = hit.ctx.unified.content["annotations"]
    miss_annotations = miss.ctx.unified.content["annotations"]
    assert hit_annotations, "hit run must still emit annotations"
    assert hit_annotations == miss_annotations
    assert [a["id"] for a in hit_annotations] == [a["id"] for a in miss_annotations]
