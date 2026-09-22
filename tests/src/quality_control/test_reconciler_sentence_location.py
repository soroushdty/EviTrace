"""
Tests for per-sentence location entries in the reconciler (risk-remediation 5.3).

Covers Requirement 3 criterion 3.3 and Requirement 4 criteria 4.3, 4.6 and
the design's ``SentenceLocation`` contract:

  - ``_compute_sentence_to_char_range(sentences, full_text, source_blocks,
    reconciliation_flags)`` returns one entry per sentence, in order, with
    the shape ``{sentence, start, end, page_index, ocr_derived, bbox,
    occurrence}``;
  - offsets come from a monotonic cursor, so repeated text yields distinct,
    increasing ranges and ``occurrence`` counts prior identical sentences;
  - a multi-sentence paragraph yields one range per sentence (the 4.3
    defect: the old map was keyed by paragraph text);
  - the real ``page_index`` is carried (never a hard-coded 0);
  - an OCR sentence's entry carries ``bbox`` from its source block and
    ``ocr_derived: True``;
  - a miss records ``start == end == -1`` and appends a reconciliation flag
    through the existing ``AlignmentRecord(source="reconciler")`` path;
  - ``len(alignment.sentence_to_char_range) == len(semantic.sentences)``
    always, including ``text_processor=None`` (both empty).

No GROBID / OpenAI / PaddleOCR is called; a fake TextProcessor stands in for
sentence segmentation and mock concern strategies isolate concern routing.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from quality_control.models import AlignmentRecord
from quality_control.reconciler import _compute_sentence_to_char_range, reconcile


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


def _sentence(text: str, page: int = 0, ocr: bool = False, source: str = "grobid") -> dict:
    return {"text": text, "page_index": page, "ocr_derived": ocr, "source": source}


ENTRY_KEYS = {"sentence", "start", "end", "page_index", "ocr_derived", "bbox", "occurrence"}


# ---------------------------------------------------------------------------
# Unit: _compute_sentence_to_char_range
# ---------------------------------------------------------------------------


def test_entry_shape_and_positional_alignment() -> None:
    sentences = [_sentence("Alpha one.", 0), _sentence("Beta two.", 3, ocr=True, source="paddleocr")]
    full_text = "Alpha one.\nBeta two."
    flags: list = []

    entries = _compute_sentence_to_char_range(sentences, full_text, [None, None], flags)

    assert len(entries) == len(sentences)
    assert [set(e) for e in entries] == [ENTRY_KEYS, ENTRY_KEYS]
    assert entries[0] == {
        "sentence": "Alpha one.",
        "start": 0,
        "end": 10,
        "page_index": 0,
        "ocr_derived": False,
        "bbox": None,
        "occurrence": 0,
    }
    assert entries[1] == {
        "sentence": "Beta two.",
        "start": 11,
        "end": 20,
        "page_index": 3,
        "ocr_derived": True,
        "bbox": None,
        "occurrence": 0,
    }
    assert flags == []


def test_duplicate_sentence_text_gets_distinct_increasing_ranges_and_occurrences() -> None:
    dup = "Same sentence here."
    sentences = [_sentence(dup, 0), _sentence("Middle one.", 0), _sentence(dup, 1)]
    full_text = "\n".join([dup, "Middle one.", dup])
    flags: list = []

    entries = _compute_sentence_to_char_range(sentences, full_text, [None, None, None], flags)

    first, _middle, second = entries
    assert (first["start"], first["end"]) == (0, len(dup))
    assert second["start"] > first["end"]
    assert (second["start"], second["end"]) == (full_text.rfind(dup), full_text.rfind(dup) + len(dup))
    assert [e["occurrence"] for e in entries] == [0, 0, 1]
    assert [e["start"] for e in entries] == sorted(e["start"] for e in entries)
    assert flags == []


def test_occurrence_counts_only_identical_text() -> None:
    sentences = [_sentence("A."), _sentence("B."), _sentence("A."), _sentence("A."), _sentence("B.")]
    full_text = "A.\nB.\nA.\nA.\nB."
    entries = _compute_sentence_to_char_range(sentences, full_text, [None] * 5, [])
    assert [e["occurrence"] for e in entries] == [0, 0, 1, 2, 1]


def test_miss_records_minus_one_and_appends_reconciler_flag() -> None:
    sentences = [_sentence("Present."), _sentence("Absent sentence."), _sentence("Also present.")]
    full_text = "Present.\nAlso present."
    flags: list = []

    entries = _compute_sentence_to_char_range(sentences, full_text, [None] * 3, flags)

    assert len(entries) == 3
    assert (entries[1]["start"], entries[1]["end"]) == (-1, -1)
    assert entries[1]["sentence"] == "Absent sentence."
    # A miss never yields a silent zero range.
    assert (entries[1]["start"], entries[1]["end"]) != (0, 0)
    # The sentences around the miss still resolve; the cursor is not advanced by a miss.
    assert (entries[0]["start"], entries[0]["end"]) == (0, 8)
    assert (entries[2]["start"], entries[2]["end"]) == (9, 22)
    assert len(flags) == 1
    flag = flags[0]
    assert isinstance(flag, AlignmentRecord)
    assert flag.source == "reconciler"
    assert flag.agreement == "one_engine_only"
    assert flag.preferred_reading == "Absent sentence."


def test_bbox_copied_from_source_block_when_present() -> None:
    ocr_block = {"text": "Scanned.", "page_index": 1, "block_bbox": [5, 6, 50, 20]}
    sentences = [_sentence("Native.", 0), _sentence("Scanned.", 1, ocr=True, source="paddleocr")]
    full_text = "Native.\nScanned."

    entries = _compute_sentence_to_char_range(
        sentences, full_text, [{"text": "Native.", "block_bbox": None}, ocr_block], []
    )

    assert entries[0]["bbox"] is None
    assert entries[1]["bbox"] == [5, 6, 50, 20]
    # A copy, not the block's own list.
    assert entries[1]["bbox"] is not ocr_block["block_bbox"]


def test_empty_inputs_yield_empty_entries() -> None:
    assert _compute_sentence_to_char_range([], "", [], []) == []


# ---------------------------------------------------------------------------
# Integration: reconcile() feeds sentences (not paragraphs) to the locator
# ---------------------------------------------------------------------------


def test_multi_sentence_paragraph_yields_per_sentence_ranges() -> None:
    para = "First sentence here. Second sentence there."
    record = _run([_block(para, 0, "grobid", False)], [])

    sentences = record.semantic.sentences
    entries = record.alignment.sentence_to_char_range
    assert [s["text"] for s in sentences] == ["First sentence here.", "Second sentence there."]
    assert len(entries) == len(sentences)
    assert [e["sentence"] for e in entries] == [s["text"] for s in sentences]
    full_text = record.content["exact_text"]
    assert (entries[0]["start"], entries[0]["end"]) == (0, len("First sentence here."))
    assert (entries[1]["start"], entries[1]["end"]) == (
        full_text.index("Second sentence there."),
        full_text.index("Second sentence there.") + len("Second sentence there."),
    )
    for entry in entries:
        assert full_text[entry["start"]:entry["end"]] == entry["sentence"]


def test_reconcile_carries_real_page_index_per_entry() -> None:
    record = _run(
        [
            _block("Page zero text.", 0, "grobid", False),
            _block("Page two text.", 2, "grobid", False),
        ],
        [],
    )
    entries = record.alignment.sentence_to_char_range
    assert [e["page_index"] for e in entries] == [0, 2]
    assert [e["ocr_derived"] for e in entries] == [False, False]


def test_reconcile_duplicate_sentences_across_pages_are_distinct() -> None:
    dup = "Repeated sentence."
    record = _run(
        [
            _block(dup, 0, "grobid", False),
            _block(dup, 1, "grobid", False),
        ],
        [],
    )
    entries = record.alignment.sentence_to_char_range
    assert len(entries) == 2
    assert entries[0]["start"] < entries[1]["start"]
    assert [e["occurrence"] for e in entries] == [0, 1]
    assert [e["page_index"] for e in entries] == [0, 1]
    assert record.alignment.reconciliation_flags == []


def test_reconcile_ocr_sentence_entry_carries_bbox_and_ocr_flag() -> None:
    record = _run(
        [_block("Native page zero.", 0, "grobid", False)],
        [
            _block("Structural page zero.", 0, "pdfplumber", False, bbox=[0, 0, 10, 10]),
            _block("Scanned alpha. Scanned beta.", 1, "paddleocr", True, bbox=[5, 5, 50, 20]),
        ],
    )
    sentences = record.semantic.sentences
    entries = record.alignment.sentence_to_char_range
    assert [s["text"] for s in sentences] == ["Native page zero.", "Scanned alpha.", "Scanned beta."]
    assert len(entries) == len(sentences)

    native, ocr_a, ocr_b = entries
    assert native["ocr_derived"] is False
    assert native["bbox"] is None
    assert native["page_index"] == 0

    for entry in (ocr_a, ocr_b):
        assert entry["ocr_derived"] is True
        assert entry["page_index"] == 1
        assert entry["bbox"] == [5, 5, 50, 20]
    # Both OCR sentences resolve inside the document text, at increasing offsets.
    full_text = record.content["exact_text"]
    assert ocr_a["start"] < ocr_b["start"]
    for entry in (ocr_a, ocr_b):
        assert full_text[entry["start"]:entry["end"]] == entry["sentence"]
    assert record.alignment.reconciliation_flags == []


def test_reconcile_native_sentence_gets_bbox_from_its_primary_block() -> None:
    """An all-scanned primary (PaddleOCR as primary) has bboxes on its blocks."""
    record = _run(
        [_block("Scanned primary one. Scanned primary two.", 0, "paddleocr", True, bbox=[1, 2, 3, 4])],
        [],
    )
    entries = record.alignment.sentence_to_char_range
    assert len(entries) == 2
    assert all(e["bbox"] == [1, 2, 3, 4] for e in entries)
    assert all(e["ocr_derived"] is True for e in entries)


def test_reconcile_miss_flags_and_keeps_alignment_length() -> None:
    class NormalisingProcessor(FakeTextProcessor):
        """Rewrites one sentence so it cannot be found in the document text."""

        def tokenize_sentences(self, text: str) -> list[str]:
            return [s.replace("Second", "SECOND") for s in super().tokenize_sentences(text)]

    record = _run(
        [_block("First sentence. Second sentence.", 0, "grobid", False)],
        [],
        text_processor=NormalisingProcessor(),
    )
    sentences = record.semantic.sentences
    entries = record.alignment.sentence_to_char_range
    assert len(entries) == len(sentences) == 2
    assert (entries[0]["start"], entries[0]["end"]) == (0, len("First sentence."))
    assert (entries[1]["start"], entries[1]["end"]) == (-1, -1)
    flags = [f for f in record.alignment.reconciliation_flags if getattr(f, "source", "") == "reconciler"]
    assert len(flags) == 1
    assert flags[0].preferred_reading == "SECOND sentence."


def test_reconcile_without_text_processor_yields_empty_sentences_and_entries() -> None:
    record = _run([_block("Some text here.", 0, "grobid", False)], [], text_processor=None)
    assert record.semantic.sentences == []
    assert record.alignment.sentence_to_char_range == []
    assert len(record.alignment.sentence_to_char_range) == len(record.semantic.sentences)
