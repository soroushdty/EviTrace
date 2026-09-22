"""
Tests for the reconciler as the sole sentence producer (risk-remediation 5.1).

Covers Requirement 4 criteria 4.2, 4.3, 4.4, 4.9 and the design's
``SentenceProducer`` contract:

  - paragraph dicts copy ``ocr_derived`` / ``source`` from their blocks
    (``.get`` defaults ``False`` / ``""``);
  - ``_build_sentences`` yields sentence dicts with exactly the keys
    ``text, page_index, ocr_derived, source``, positionally aligned with the
    per-sentence source blocks it returns;
  - OCR blocks from the secondary artifact become sentences only on pages
    with no primary paragraph text, visited in page order with native
    sentences before OCR sentences within a page;
  - the document text (``content["exact_text"]``) is the primary blocks plus
    the OCR blocks used for sentences, in page order;
  - an all-scanned document (OCR primary) yields OCR sentences and non-empty
    exact text; a native-only document yields exactly the sentences today's
    fallback loop in ``quality_control.py`` produces; ``text_processor=None``
    leaves sentences empty.

No GROBID / OpenAI / PaddleOCR is called; a fake TextProcessor stands in for
sentence segmentation and mock concern strategies isolate concern routing.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from quality_control.models import SemanticLayer
from quality_control.reconciler import _build_semantic_layer, _build_sentences, reconcile


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTextProcessor:
    """Deterministic sentence splitter: one sentence per terminal punctuation."""

    def tokenize_sentences(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]

    def compare(self, a: str, b: str) -> float:
        return 1.0 if a == b else 0.0


def _mock_strategies() -> dict:
    tf = MagicMock()
    tf.reconcile.return_value = {
        "edit_distance": 0.0,
        "agreement": "full",
        "preferred_reading": "",
        "confidence": 1.0,
    }
    sec = MagicMock()
    sec.reconcile.return_value = 1.0
    tbl = MagicMock()
    tbl.merge.return_value = {"agreement": "present", "merged_text": ""}
    return {
        "text_fidelity_strategy": tf,
        "section_strategy": sec,
        "table_figure_strategy": tbl,
    }


def _block(text: str, page: int, source: str, ocr: bool, bbox=None) -> dict:
    b = {
        "text": text,
        "page_index": page,
        "block_type": "paragraph",
        "source": source,
        "ocr_derived": ocr,
    }
    if bbox is not None:
        b["block_bbox"] = bbox
    return b


def _run(primary_blocks, secondary_blocks, text_processor=FakeTextProcessor()):
    return reconcile(
        primary_artifact={"document_id": "doc", "blocks": primary_blocks},
        secondary_artifact={"document_id": "doc", "blocks": secondary_blocks},
        adjudication_decisions={"primary_extractor": "grobid", "confidence": 1.0, "rationale": "t"},
        text_processor=text_processor,
        **_mock_strategies(),
    )


def _fallback_loop_sentences(paragraphs: list[dict], tp) -> list[dict]:
    """Mirror of the post-reconciliation loop in quality_control.py (pre-5.2)."""
    out: list[dict] = []
    for para in paragraphs:
        for sentence in tp.tokenize_sentences(para.get("text", "")):
            out.append({"text": sentence, "page_index": para.get("page_index", 0), "ocr_derived": False})
    return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

P0_NATIVE = "Native page zero first. Native page zero second."
P2_NATIVE = "Native page two only."
P0_PLUMBER = "Structural page zero first. Structural page zero second."
P1_OCR = "Scanned page one alpha. Scanned page one beta."
P2_OCR = "Scanned page two must not appear."

MIXED_PRIMARY = [
    _block(P0_NATIVE, 0, "grobid", False),
    _block(P2_NATIVE, 2, "grobid", False),
]
MIXED_SECONDARY = [
    _block(P0_PLUMBER, 0, "pdfplumber", False, bbox=[0, 0, 10, 10]),
    _block(P1_OCR, 1, "paddleocr", True, bbox=[5, 5, 50, 20]),
    _block(P2_OCR, 2, "paddleocr", True, bbox=[5, 5, 50, 20]),
]


# ---------------------------------------------------------------------------
# Paragraph provenance (design: _build_semantic_layer copies origin keys)
# ---------------------------------------------------------------------------


def test_paragraphs_copy_ocr_derived_and_source_from_blocks() -> None:
    semantic = _build_semantic_layer(
        [
            _block("Alpha one.", 0, "grobid", False),
            _block("Beta two.", 3, "paddleocr", True),
        ]
    )
    assert [(p["ocr_derived"], p["source"]) for p in semantic.paragraphs] == [
        (False, "grobid"),
        (True, "paddleocr"),
    ]
    assert [p["block_index"] for p in semantic.paragraphs] == [0, 1]


def test_paragraphs_default_origin_keys_for_older_callers() -> None:
    """Blocks without origin keys read as not-OCR with an empty source."""
    semantic = _build_semantic_layer([{"text": "Legacy paragraph.", "page_index": 1, "block_type": "paragraph"}])
    assert semantic.paragraphs[0]["ocr_derived"] is False
    assert semantic.paragraphs[0]["source"] == ""


# ---------------------------------------------------------------------------
# _build_sentences contract
# ---------------------------------------------------------------------------


def test_build_sentences_returns_aligned_sources_and_exact_keys() -> None:
    semantic = _build_semantic_layer(MIXED_PRIMARY)
    sentences, sources = _build_sentences(semantic, MIXED_SECONDARY, FakeTextProcessor())

    assert len(sentences) == len(sources) == 5
    for s in sentences:
        assert set(s.keys()) == {"text", "page_index", "ocr_derived", "source"}
    # OCR sentences point back at the secondary block that produced them.
    ocr_sources = [src for s, src in zip(sentences, sources) if s["ocr_derived"]]
    assert ocr_sources and all(src is MIXED_SECONDARY[1] for src in ocr_sources)
    # Native sentences carry a source record that resolves to their paragraph.
    native_sources = [src for s, src in zip(sentences, sources) if not s["ocr_derived"]]
    assert all(src is not None and "block_index" in src for src in native_sources)


def test_build_sentences_with_no_text_processor_is_empty() -> None:
    semantic = _build_semantic_layer(MIXED_PRIMARY)
    assert _build_sentences(semantic, MIXED_SECONDARY, None) == ([], [])


def test_build_sentences_skips_secondary_blocks_without_ocr_flag() -> None:
    """A non-OCR secondary block on a primary-less page is structural, not text."""
    semantic = SemanticLayer(paragraphs=[])
    sentences, _ = _build_sentences(
        semantic, [_block("Plumber only page.", 4, "pdfplumber", False)], FakeTextProcessor()
    )
    assert sentences == []


# ---------------------------------------------------------------------------
# Mixed document (4.2, 4.3, 4.4, 4.9)
# ---------------------------------------------------------------------------


def test_mixed_document_sentences_carry_both_markings() -> None:
    result = _run(MIXED_PRIMARY, MIXED_SECONDARY)
    sentences = result.semantic.sentences

    markings = {s["ocr_derived"] for s in sentences}
    assert markings == {True, False}, "mixed document must carry both markings (4.4)"

    native = [s for s in sentences if not s["ocr_derived"]]
    ocr = [s for s in sentences if s["ocr_derived"]]
    assert [s["text"] for s in native] == [
        "Native page zero first.",
        "Native page zero second.",
        "Native page two only.",
    ]
    assert all(s["source"] == "grobid" for s in native)
    assert [s["text"] for s in ocr] == ["Scanned page one alpha.", "Scanned page one beta."]
    assert all(s["page_index"] == 1 and s["source"] == "paddleocr" for s in ocr)


def test_mixed_document_page_two_ocr_block_is_not_used() -> None:
    """Page 2 has primary text, so its OCR block must not produce sentences."""
    result = _run(MIXED_PRIMARY, MIXED_SECONDARY)
    texts = [s["text"] for s in result.semantic.sentences]
    assert P2_OCR not in texts
    assert P2_OCR not in result.content["exact_text"]


def test_mixed_document_pdfplumber_block_is_not_used() -> None:
    result = _run(MIXED_PRIMARY, MIXED_SECONDARY)
    texts = " ".join(s["text"] for s in result.semantic.sentences)
    assert "Structural" not in texts
    assert "Structural" not in result.content["exact_text"]


def test_mixed_document_sentence_order_is_page_order_native_first() -> None:
    result = _run(MIXED_PRIMARY, MIXED_SECONDARY)
    order = [(s["page_index"], s["ocr_derived"]) for s in result.semantic.sentences]
    assert order == [(0, False), (0, False), (1, True), (1, True), (2, False)]


def test_within_page_native_sentences_precede_ocr_sentences() -> None:
    """Page order is total; within a page native precedes OCR.

    Construct a page that has no primary *paragraph* (only a section heading)
    so its OCR block is used, and check it lands after page-0 natives and
    before page-2 natives.
    """
    primary = [
        _block(P0_NATIVE, 0, "grobid", False),
        {"text": "Methods", "page_index": 1, "block_type": "section", "source": "grobid", "ocr_derived": False},
        _block(P2_NATIVE, 2, "grobid", False),
    ]
    secondary = [_block(P1_OCR, 1, "paddleocr", True)]
    result = _run(primary, secondary)
    pages = [s["page_index"] for s in result.semantic.sentences]
    assert pages == sorted(pages)
    assert [s["ocr_derived"] for s in result.semantic.sentences] == [False, False, True, True, False]


def test_mixed_document_exact_text_contains_used_ocr_text_in_page_order() -> None:
    result = _run(MIXED_PRIMARY, MIXED_SECONDARY)
    exact = result.content["exact_text"]
    assert P1_OCR in exact
    assert exact.index(P0_NATIVE) < exact.index(P1_OCR) < exact.index(P2_NATIVE)
    assert exact == "\n".join([P0_NATIVE, P1_OCR, P2_NATIVE])


def test_mixed_document_ocr_sentences_are_findable_in_exact_text() -> None:
    """Document-text rule: OCR sentences are searchable like native ones."""
    result = _run(MIXED_PRIMARY, MIXED_SECONDARY)
    exact = result.content["exact_text"]
    for s in result.semantic.sentences:
        assert exact.find(s["text"]) != -1, s


# ---------------------------------------------------------------------------
# All-scanned document (4.9)
# ---------------------------------------------------------------------------


def test_all_scanned_document_yields_ocr_sentences_and_exact_text() -> None:
    primary = [
        _block("Scanned only page zero. Second scanned.", 0, "paddleocr", True, bbox=[0, 0, 1, 1]),
        _block("Scanned page one.", 1, "paddleocr", True, bbox=[0, 0, 1, 1]),
    ]
    result = _run(primary, [])
    sentences = result.semantic.sentences

    assert len(sentences) == 3
    assert all(s["ocr_derived"] is True for s in sentences)
    assert all(s["source"] == "paddleocr" for s in sentences)
    assert [s["page_index"] for s in sentences] == [0, 0, 1]
    assert result.content["exact_text"] == "Scanned only page zero. Second scanned.\nScanned page one."


# ---------------------------------------------------------------------------
# Native-only preservation (4.3) and None processor
# ---------------------------------------------------------------------------


def test_native_only_document_matches_fallback_loop_output() -> None:
    primary = [
        _block("Intro sentence one. Intro sentence two.", 0, "grobid", False),
        {"text": "Methods", "page_index": 1, "block_type": "section", "source": "grobid", "ocr_derived": False},
        _block("Methods sentence.", 1, "grobid", False),
        _block("Results sentence A. Results sentence B! Results sentence C?", 2, "grobid", False),
    ]
    secondary = [_block("Plumber block.", 0, "pdfplumber", False)]
    tp = FakeTextProcessor()
    result = _run(primary, secondary, tp)

    expected = _fallback_loop_sentences(result.semantic.paragraphs, tp)
    assert [(s["text"], s["page_index"]) for s in result.semantic.sentences] == [
        (e["text"], e["page_index"]) for e in expected
    ]
    assert all(s["ocr_derived"] is False for s in result.semantic.sentences)
    assert len(result.semantic.sentences) == 6
    # Document text is unchanged for native-only input.
    assert result.content["exact_text"] == "\n".join(b["text"] for b in primary)


def test_native_only_legacy_blocks_without_origin_keys_still_produce_sentences() -> None:
    primary = [{"text": "Old style block. Two sentences.", "page_index": 0, "block_type": "paragraph"}]
    result = _run(primary, [])
    assert [s["text"] for s in result.semantic.sentences] == ["Old style block.", "Two sentences."]
    assert all(s["ocr_derived"] is False and s["source"] == "" for s in result.semantic.sentences)


def test_reconcile_with_no_text_processor_leaves_sentences_empty() -> None:
    result = _run(MIXED_PRIMARY, MIXED_SECONDARY, text_processor=None)
    assert result.semantic.sentences == []


def test_reconcile_with_no_text_processor_keeps_primary_only_exact_text() -> None:
    """No sentences are produced, so no OCR block is 'used' by the text rule."""
    result = _run(MIXED_PRIMARY, MIXED_SECONDARY, text_processor=None)
    assert result.content["exact_text"] == "\n".join([P0_NATIVE, P2_NATIVE])
