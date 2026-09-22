"""
tests/pdf_extractor/test_w3c_annotation.py
------------------------------------------
TDD tests for tasks 8.1, 8.2, and 8.3.

Covers:
  - project() return type is list[AnnotationRecord], not list[dict]
  - Born-digital record: selector_type == "TextPositionSelector", quote_selector populated
  - Scanned record: selector_type == "FragmentSelector", ocr_derived == True
  - Mixed document: both selector types appear in one projection call
  - generate_w3c_jsonld([]) returns [] without raising
  - Born-digital serialization: all five required JSON-LD keys, TextPositionSelector present
  - Scanned serialization: FragmentSelector present, "ocr_derived": True in body
  - Each "id" field matches "urn:evitrace:anno:<uuid5>"
  - AnnotationIdentity (risk-remediation 6.2, requirements 3.1-3.4): ids are
    name-based, reproducible, paper-scoped via base_uri, and distinct per
    page / text / occurrence.
  - RegionSelector (risk-remediation 6.1, requirements 4.5 / 4.7): selectors
    come from each sentence's own alignment entry; project() never reads the
    structural layer; missing OCR regions warn and continue.
"""
import logging
import re

import pytest

from quality_control.models import (
    DocumentAlignment,
    SemanticLayer,
    StructuralLayer,
    UnifiedRecord,
)


# ---------------------------------------------------------------------------
# Helpers: build minimal UnifiedRecord fixtures
# ---------------------------------------------------------------------------

def _born_digital_unified() -> UnifiedRecord:
    """UnifiedRecord with one born-digital sentence."""
    semantic = SemanticLayer(
        sentences=[
            {"text": "Hello world.", "page_index": 0, "ocr_derived": False},
        ]
    )
    # sentence_to_char_range is a list of dicts
    alignment = DocumentAlignment(
        sentence_to_char_range=[
            {"sentence": "Hello world.", "start": 0, "end": 12, "page_index": 0},
        ]
    )
    return UnifiedRecord(
        document_id="doc-bd",
        semantic=semantic,
        structural=StructuralLayer(),
        alignment=alignment,
    )


def _scanned_unified() -> UnifiedRecord:
    """UnifiedRecord with one OCR-derived sentence whose region comes from
    its own alignment entry (``bbox``), never from ``structural``."""
    semantic = SemanticLayer(
        sentences=[
            {"text": "Scanned text.", "page_index": 1, "ocr_derived": True},
        ]
    )
    alignment = DocumentAlignment(
        sentence_to_char_range=[
            {
                "sentence": "Scanned text.",
                "start": 0,
                "end": 13,
                "page_index": 1,
                "ocr_derived": True,
                "bbox": [10, 20, 110, 50],
                "occurrence": 0,
            },
        ]
    )
    return UnifiedRecord(
        document_id="doc-scan",
        semantic=semantic,
        structural=StructuralLayer(),
        alignment=alignment,
    )


def _mixed_unified() -> UnifiedRecord:
    """UnifiedRecord with one born-digital and one scanned sentence."""
    semantic = SemanticLayer(
        sentences=[
            {"text": "Born digital.", "page_index": 0, "ocr_derived": False},
            {"text": "Scanned page.", "page_index": 1, "ocr_derived": True},
        ]
    )
    alignment = DocumentAlignment(
        sentence_to_char_range=[
            {"sentence": "Born digital.", "start": 0, "end": 13, "page_index": 0},
            {
                "sentence": "Scanned page.",
                "start": 14,
                "end": 27,
                "page_index": 1,
                "ocr_derived": True,
                "bbox": [5, 10, 105, 40],
                "occurrence": 0,
            },
        ]
    )
    return UnifiedRecord(
        document_id="doc-mixed",
        semantic=semantic,
        structural=StructuralLayer(),
        alignment=alignment,
    )


class _ForbiddenLayer:
    """Sentinel standing in for ``unified.structural``: any attribute access
    fails the test, proving ``project()`` reads nothing from that layer."""

    def __getattr__(self, name: str):
        raise AssertionError(f"project() must not read unified.structural.{name}")


def _entry(text: str, page: int, *, start: int, end: int, ocr: bool = False,
           bbox: list | None = None, occurrence: int = 0) -> dict:
    return {
        "sentence": text,
        "start": start,
        "end": end,
        "page_index": page,
        "ocr_derived": ocr,
        "bbox": bbox,
        "occurrence": occurrence,
    }


def _sentence(text: str, page: int, *, ocr: bool = False) -> dict:
    return {"text": text, "page_index": page, "ocr_derived": ocr, "source": "test"}


# Name-based (RFC 4122 version 5) UUID inside the EviTrace annotation URN.
_UUID5_URN = (
    r"^urn:evitrace:anno:[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Task 8.3 — project() tests (8.1)
# ---------------------------------------------------------------------------

class TestProject:
    def test_returns_list_of_annotation_records_not_dicts(self):
        """project() must return list[AnnotationRecord], not list[dict]."""
        from artifact_generation.w3c_annotation import AnnotationRecord, project

        unified = _born_digital_unified()
        result = project(unified)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], AnnotationRecord)

    def test_born_digital_has_text_position_selector(self):
        """Born-digital entry → selector_type == 'TextPositionSelector'."""
        from artifact_generation.w3c_annotation import project

        unified = _born_digital_unified()
        records = project(unified)

        assert records[0].selector_type == "TextPositionSelector"

    def test_born_digital_selector_payload_has_start_end(self):
        """Born-digital selector_payload must carry integer start and end."""
        from artifact_generation.w3c_annotation import project

        unified = _born_digital_unified()
        records = project(unified)
        payload = records[0].selector_payload

        assert "start" in payload
        assert "end" in payload
        assert isinstance(payload["start"], int)
        assert isinstance(payload["end"], int)

    def test_born_digital_quote_selector_populated(self):
        """Every record must have a populated quote_selector with exact/prefix/suffix."""
        from artifact_generation.w3c_annotation import project

        unified = _born_digital_unified()
        records = project(unified)
        qs = records[0].quote_selector

        assert "exact" in qs
        assert "prefix" in qs
        assert "suffix" in qs
        assert qs["exact"] == "Hello world."

    def test_scanned_has_fragment_selector(self):
        """Scanned entry → selector_type == 'FragmentSelector'."""
        from artifact_generation.w3c_annotation import project

        unified = _scanned_unified()
        records = project(unified)

        assert records[0].selector_type == "FragmentSelector"

    def test_scanned_ocr_derived_flag_set(self):
        """Scanned entry must have ocr_derived == True on the AnnotationRecord."""
        from artifact_generation.w3c_annotation import project

        unified = _scanned_unified()
        records = project(unified)

        assert records[0].ocr_derived is True

    def test_scanned_quote_selector_populated(self):
        """Scanned records must also carry a populated quote_selector."""
        from artifact_generation.w3c_annotation import project

        unified = _scanned_unified()
        records = project(unified)
        qs = records[0].quote_selector

        assert qs["exact"] == "Scanned text."

    def test_mixed_document_produces_both_selector_types(self):
        """A document with both page types must produce both selector types."""
        from artifact_generation.w3c_annotation import project

        unified = _mixed_unified()
        records = project(unified)

        selector_types = {r.selector_type for r in records}
        assert "TextPositionSelector" in selector_types
        assert "FragmentSelector" in selector_types

    def test_project_alignment_none_falls_back_to_quote_selectors(self, caplog):
        """alignment is None → one record per sentence, quote selector only,
        and exactly one log line in total (design: RegionSelector
        precondition), even when OCR sentences are present."""
        from artifact_generation.w3c_annotation import project

        unified = UnifiedRecord(
            document_id="empty",
            semantic=SemanticLayer(sentences=[
                {"text": "x", "page_index": 0},
                {"text": "y", "page_index": 1, "ocr_derived": True},
            ]),
            structural=_ForbiddenLayer(),
            alignment=None,
        )
        with caplog.at_level(logging.WARNING, logger="artifact_generation"):
            result = project(unified)

        assert len(result) == 2
        assert [r.selector_type for r in result] == ["TextQuoteSelector", "TextQuoteSelector"]
        assert [r.selector_payload for r in result] == [{}, {}]
        assert [r.quote_selector["exact"] for r in result] == ["x", "y"]
        assert [r.ocr_derived for r in result] == [False, True]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        assert "alignment" in warnings[0].getMessage()

    def test_project_returns_empty_list_when_semantic_none(self):
        """project() returns [] when semantic is None."""
        from artifact_generation.w3c_annotation import project

        unified = UnifiedRecord(
            document_id="empty",
            alignment=DocumentAlignment(),
            semantic=None,
        )
        result = project(unified)
        assert result == []


# ---------------------------------------------------------------------------
# risk-remediation 6.1 — RegionSelector (requirements 4.5, 4.7)
# ---------------------------------------------------------------------------

class TestRegionSelector:
    def test_second_ocr_sentence_on_page_gets_its_own_bbox(self):
        """4.5: two OCR blocks on one page → the second sentence's region is
        its own box, not the first block on the page."""
        from artifact_generation.w3c_annotation import project

        unified = UnifiedRecord(
            document_id="doc-two-blocks",
            semantic=SemanticLayer(sentences=[
                _sentence("First block.", 3, ocr=True),
                _sentence("Second block.", 3, ocr=True),
            ]),
            structural=_ForbiddenLayer(),
            alignment=DocumentAlignment(sentence_to_char_range=[
                _entry("First block.", 3, start=0, end=12, ocr=True, bbox=[0, 0, 100, 20]),
                _entry("Second block.", 3, start=13, end=26, ocr=True, bbox=[10, 300, 210, 340]),
            ]),
        )
        records = project(unified)

        assert [r.selector_type for r in records] == ["FragmentSelector", "FragmentSelector"]
        assert records[0].selector_payload == {"page": 3, "xywh": "0,0,100,20"}
        assert records[1].selector_payload == {"page": 3, "xywh": "10,300,200,40"}

    def test_ocr_sentence_without_bbox_warns_and_continues(self, caplog):
        """4.7: missing region → WARNING naming page and sentence prefix;
        the record is quote-only, still ocr_derived, and later sentences
        are still emitted."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        long_text = "An OCR sentence with no region whose text runs well past sixty characters."
        unified = UnifiedRecord(
            document_id="doc-missing-region",
            semantic=SemanticLayer(sentences=[
                _sentence(long_text, 7, ocr=True),
                _sentence("Trailing sentence.", 7, ocr=True),
            ]),
            structural=_ForbiddenLayer(),
            alignment=DocumentAlignment(sentence_to_char_range=[
                _entry(long_text, 7, start=0, end=len(long_text), ocr=True, bbox=None),
                _entry("Trailing sentence.", 7, start=len(long_text) + 1,
                       end=len(long_text) + 19, ocr=True, bbox=[1, 2, 3, 4]),
            ]),
        )
        with caplog.at_level(logging.WARNING, logger="artifact_generation"):
            records = project(unified)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "no region for OCR sentence on page 7" in msg
        assert repr(long_text)[:20] in msg          # sentence prefix is named
        assert long_text not in msg                 # truncated to the %.60r prefix

        assert len(records) == 2, "processing must continue past the missing region"
        missing, trailing = records
        assert missing.selector_type == "TextQuoteSelector"
        assert missing.selector_payload == {}
        assert missing.quote_selector["exact"] == long_text
        assert missing.ocr_derived is True
        assert trailing.selector_type == "FragmentSelector"
        assert trailing.selector_payload == {"page": 7, "xywh": "1,2,2,2"}

        body = generate_w3c_jsonld(records)[0]["body"]
        assert body["ocr_derived"] is True, "body marking must follow the record"
        target_types = [s["type"] for s in generate_w3c_jsonld(records)[0]["target"]["selector"]]
        assert target_types == ["TextQuoteSelector"]

    def test_alignment_length_mismatch_falls_back_to_quote_selectors(self, caplog):
        """Length mismatch → quote-only for every sentence and exactly one
        WARNING in total (no per-sentence "no region" line for the OCR one)."""
        from artifact_generation.w3c_annotation import project

        unified = UnifiedRecord(
            document_id="doc-mismatch",
            semantic=SemanticLayer(sentences=[
                _sentence("One.", 0),
                _sentence("Two.", 1, ocr=True),
            ]),
            structural=_ForbiddenLayer(),
            alignment=DocumentAlignment(sentence_to_char_range=[
                _entry("One.", 0, start=0, end=4),
            ]),
        )
        with caplog.at_level(logging.WARNING, logger="artifact_generation"):
            records = project(unified)

        assert [r.selector_type for r in records] == ["TextQuoteSelector", "TextQuoteSelector"]
        assert [r.quote_selector["exact"] for r in records] == ["One.", "Two."]
        assert [r.ocr_derived for r in records] == [False, True]
        assert [r.page_index for r in records] == [0, 1]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
        assert "alignment" in warnings[0].getMessage()

    def test_native_sentence_with_missing_offsets_is_quote_only(self):
        """Native sentence whose entry is a miss (start == -1) → quote only,
        never a TextPositionSelector with negative offsets."""
        from artifact_generation.w3c_annotation import project

        unified = UnifiedRecord(
            document_id="doc-miss",
            semantic=SemanticLayer(sentences=[_sentence("Unfound.", 2)]),
            structural=_ForbiddenLayer(),
            alignment=DocumentAlignment(sentence_to_char_range=[
                _entry("Unfound.", 2, start=-1, end=-1),
            ]),
        )
        records = project(unified)

        assert records[0].selector_type == "TextQuoteSelector"
        assert records[0].selector_payload == {}
        assert records[0].ocr_derived is False
        assert records[0].page_index == 2

    def test_native_sentence_with_valid_offsets_uses_entry_offsets(self):
        """Native sentence with start >= 0 → TextPositionSelector carrying the
        entry's own offsets (positional zip, not text-keyed lookup)."""
        from artifact_generation.w3c_annotation import project

        unified = UnifiedRecord(
            document_id="doc-dup",
            semantic=SemanticLayer(sentences=[
                _sentence("Repeat.", 0),
                _sentence("Repeat.", 4),
            ]),
            structural=_ForbiddenLayer(),
            alignment=DocumentAlignment(sentence_to_char_range=[
                _entry("Repeat.", 0, start=100, end=107, occurrence=0),
                _entry("Repeat.", 4, start=900, end=907, occurrence=1),
            ]),
        )
        records = project(unified)

        assert records[0].selector_type == "TextPositionSelector"
        assert records[0].selector_payload == {"start": 100, "end": 107}
        assert records[1].selector_type == "TextPositionSelector"
        assert records[1].selector_payload == {"start": 900, "end": 907}
        assert [r.occurrence for r in records] == [0, 1]
        assert [r.page_index for r in records] == [0, 4]

    def test_duplicate_sentences_get_their_own_quote_context(self):
        """Duplicate text must not both take the first occurrence's context."""
        from artifact_generation.w3c_annotation import project

        unified = UnifiedRecord(
            document_id="doc-ctx",
            semantic=SemanticLayer(sentences=[
                _sentence("Alpha.", 0),
                _sentence("Same.", 0),
                _sentence("Beta.", 0),
                _sentence("Same.", 0),
                _sentence("Gamma.", 0),
            ]),
            structural=_ForbiddenLayer(),
            alignment=DocumentAlignment(sentence_to_char_range=[
                _entry("Alpha.", 0, start=0, end=6),
                _entry("Same.", 0, start=7, end=12, occurrence=0),
                _entry("Beta.", 0, start=13, end=18),
                _entry("Same.", 0, start=19, end=24, occurrence=1),
                _entry("Gamma.", 0, start=25, end=31),
            ]),
        )
        records = project(unified)

        first, second = records[1], records[3]
        assert first.quote_selector["prefix"].endswith("Alpha. ")
        assert first.quote_selector["suffix"].startswith(" Beta.")
        assert second.quote_selector["prefix"].endswith("Beta. ")
        assert second.quote_selector["suffix"].startswith(" Gamma.")

    def test_project_never_reads_structural_layer(self):
        """CLAUDE.md rule: project() reads only semantic and alignment."""
        from artifact_generation.w3c_annotation import project

        unified = UnifiedRecord(
            document_id="doc-sentinel",
            semantic=SemanticLayer(sentences=[
                _sentence("Native.", 0),
                _sentence("Scanned.", 1, ocr=True),
                _sentence("Scanned no box.", 1, ocr=True),
            ]),
            structural=_ForbiddenLayer(),
            alignment=DocumentAlignment(sentence_to_char_range=[
                _entry("Native.", 0, start=0, end=7),
                _entry("Scanned.", 1, start=8, end=16, ocr=True, bbox=[0, 0, 1, 1]),
                _entry("Scanned no box.", 1, start=17, end=32, ocr=True, bbox=None),
            ]),
        )
        records = project(unified)  # raises AssertionError if structural is touched
        assert len(records) == 3

        unified.structural = None
        assert len(project(unified)) == 3

    def test_record_carries_occurrence_and_document_id(self):
        """AnnotationRecord gains occurrence (from the entry) and document_id
        (from unified.document_id); defaults keep old constructions working."""
        from artifact_generation.w3c_annotation import AnnotationRecord, project

        legacy = AnnotationRecord(
            sentence_text="t", page_index=0, selector_type="TextQuoteSelector",
            selector_payload={}, quote_selector={"exact": "t", "prefix": "", "suffix": ""},
        )
        assert legacy.occurrence == 0
        assert legacy.document_id == ""

        records = project(_mixed_unified())
        assert all(r.document_id == "doc-mixed" for r in records)
        assert [r.occurrence for r in records] == [0, 0]


# ---------------------------------------------------------------------------
# Task 8.3 — generate_w3c_jsonld() tests (8.2)
# ---------------------------------------------------------------------------

class TestGenerateW3cJsonld:
    def test_empty_list_returns_empty_list(self):
        """generate_w3c_jsonld([]) must return [] without raising."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld

        result = generate_w3c_jsonld([])
        assert result == []

    def test_born_digital_produces_dict_with_five_required_keys(self):
        """Born-digital record → dict with @context, id, type, body, target."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        result = generate_w3c_jsonld(records)

        assert len(result) == 1
        anno = result[0]
        for key in ("@context", "id", "type", "body", "target"):
            assert key in anno, f"Missing key: {key}"

    def test_born_digital_serializes_text_position_selector(self):
        """Born-digital JSON-LD target must include a TextPositionSelector."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        selectors = anno["target"]["selector"]
        selector_types = [s["type"] for s in selectors]
        assert "TextPositionSelector" in selector_types

    def test_scanned_serializes_fragment_selector(self):
        """Scanned JSON-LD target must include a FragmentSelector."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_scanned_unified())
        anno = generate_w3c_jsonld(records)[0]

        selectors = anno["target"]["selector"]
        selector_types = [s["type"] for s in selectors]
        assert "FragmentSelector" in selector_types

    def test_scanned_body_has_ocr_derived_true(self):
        """Scanned JSON-LD body must carry 'ocr_derived': True."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_scanned_unified())
        anno = generate_w3c_jsonld(records)[0]

        assert anno["body"].get("ocr_derived") is True

    def test_body_ocr_derived_mirrors_record_for_every_selector_type(self):
        """Body marking equals the record marking regardless of selector type
        (consumer contract for 4.8)."""
        from artifact_generation.w3c_annotation import AnnotationRecord, generate_w3c_jsonld

        quote = {"exact": "t", "prefix": "", "suffix": ""}
        records = [
            AnnotationRecord("t", 0, "TextPositionSelector", {"start": 0, "end": 1}, quote, ocr_derived=False),
            AnnotationRecord("t", 0, "TextQuoteSelector", {}, quote, ocr_derived=False),
            AnnotationRecord("t", 1, "TextQuoteSelector", {}, quote, ocr_derived=True),
            AnnotationRecord("t", 1, "FragmentSelector", {"page": 1, "xywh": "0,0,1,1"}, quote, ocr_derived=True),
        ]
        bodies = [a["body"]["ocr_derived"] for a in generate_w3c_jsonld(records)]
        assert bodies == [False, False, True, True]

    def test_scanned_fragment_selector_value_comes_from_alignment_bbox(self):
        """FragmentSelector value is page=<page>&xywh=<x,y,w,h> from the entry bbox."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        anno = generate_w3c_jsonld(project(_scanned_unified()))[0]
        frag = next(s for s in anno["target"]["selector"] if s["type"] == "FragmentSelector")
        assert frag["value"] == "page=1&xywh=10,20,100,30"

    def test_id_matches_urn_prefix(self):
        """3.4: every annotation id is urn:evitrace:anno:<name-based uuid5>,
        not a random uuid4 (version nibble ``5``)."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        assert re.match(_UUID5_URN, anno["id"]), f"ID does not match pattern: {anno['id']}"

    def test_scanned_id_matches_urn_prefix(self):
        """3.4: scanned annotation ids follow the same name-based uuid5 URN shape."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_scanned_unified())
        anno = generate_w3c_jsonld(records)[0]

        assert re.match(_UUID5_URN, anno["id"]), f"ID does not match pattern: {anno['id']}"

    def test_type_field_is_annotation(self):
        """JSON-LD 'type' field must be 'Annotation'."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        assert anno["type"] == "Annotation"

    def test_context_is_w3c_anno_context(self):
        """JSON-LD '@context' must be the W3C annotation context URI."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        assert anno["@context"] == "http://www.w3.org/ns/anno.jsonld"

    def test_text_quote_selector_present_in_born_digital_target(self):
        """Born-digital target must also include a TextQuoteSelector."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        selectors = anno["target"]["selector"]
        selector_types = [s["type"] for s in selectors]
        assert "TextQuoteSelector" in selector_types

    def test_text_quote_selector_present_in_scanned_target(self):
        """Scanned target must also include a TextQuoteSelector."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_scanned_unified())
        anno = generate_w3c_jsonld(records)[0]

        selectors = anno["target"]["selector"]
        selector_types = [s["type"] for s in selectors]
        assert "TextQuoteSelector" in selector_types


# ---------------------------------------------------------------------------
# risk-remediation 6.2 — AnnotationIdentity (requirements 3.1, 3.2, 3.3, 3.4)
# ---------------------------------------------------------------------------

def _two_sentence_unified(document_id: str = "doc-ids") -> UnifiedRecord:
    """Two distinct native sentences on different pages, with per-sentence entries."""
    return UnifiedRecord(
        document_id=document_id,
        semantic=SemanticLayer(sentences=[
            _sentence("Alpha sentence.", 0),
            _sentence("Beta sentence.", 1),
        ]),
        structural=_ForbiddenLayer(),
        alignment=DocumentAlignment(sentence_to_char_range=[
            _entry("Alpha sentence.", 0, start=0, end=15),
            _entry("Beta sentence.", 1, start=16, end=30),
        ]),
    )


class TestAnnotationIdentity:
    BASE = "urn:evitrace:document:paper-a"

    def test_same_records_annotated_twice_yield_identical_ids(self):
        """3.1: same source record + base identifier → byte-identical ids."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_two_sentence_unified())
        first = [a["id"] for a in generate_w3c_jsonld(records, base_uri=self.BASE)]
        second = [a["id"] for a in generate_w3c_jsonld(records, base_uri=self.BASE)]

        assert first == second
        assert len(set(first)) == 2

    def test_reprojected_record_yields_identical_ids(self):
        """3.1: a fresh projection of an equal record (separate run) gives the same ids."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        first = [a["id"] for a in generate_w3c_jsonld(project(_two_sentence_unified()), base_uri=self.BASE)]
        second = [a["id"] for a in generate_w3c_jsonld(project(_two_sentence_unified()), base_uri=self.BASE)]

        assert first == second

    def test_same_sentence_in_two_papers_gets_different_ids(self):
        """3.2 / paper scoping: identical text and position under a different
        base_uri (another paper) → different ids."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_two_sentence_unified())
        paper_a = [a["id"] for a in generate_w3c_jsonld(records, base_uri="urn:evitrace:document:paper-a")]
        paper_b = [a["id"] for a in generate_w3c_jsonld(records, base_uri="urn:evitrace:document:paper-b")]

        assert paper_a[0] != paper_b[0]
        assert paper_a[1] != paper_b[1]

    def test_duplicate_sentence_in_one_paper_gets_distinct_ids(self):
        """3.3: the same sentence text twice in one document (occurrence 0 and 1)
        → each occurrence has its own id."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        unified = UnifiedRecord(
            document_id="doc-dup",
            semantic=SemanticLayer(sentences=[
                _sentence("Repeated line.", 2),
                _sentence("Repeated line.", 2),
            ]),
            structural=_ForbiddenLayer(),
            alignment=DocumentAlignment(sentence_to_char_range=[
                _entry("Repeated line.", 2, start=0, end=14, occurrence=0),
                _entry("Repeated line.", 2, start=15, end=29, occurrence=1),
            ]),
        )
        ids = [a["id"] for a in generate_w3c_jsonld(project(unified), base_uri=self.BASE)]

        assert ids[0] != ids[1]
        # and the ids are themselves stable across a second serialization
        assert ids == [a["id"] for a in generate_w3c_jsonld(project(unified), base_uri=self.BASE)]

    def test_different_page_or_text_gives_different_id(self):
        """3.2: ids differ when the page differs, and when the text differs."""
        from artifact_generation.w3c_annotation import AnnotationRecord, generate_w3c_jsonld

        quote = {"exact": "Same text.", "prefix": "", "suffix": ""}
        page0 = AnnotationRecord("Same text.", 0, "TextQuoteSelector", {}, quote)
        page1 = AnnotationRecord("Same text.", 1, "TextQuoteSelector", {}, quote)
        other = AnnotationRecord("Other text.", 0, "TextQuoteSelector", {}, quote)

        ids = [a["id"] for a in generate_w3c_jsonld([page0, page1, other], base_uri=self.BASE)]

        assert len(set(ids)) == 3

    def test_default_document_source_is_still_deterministic(self):
        """3.1 with no base_uri: the default document source is a constant, so
        ids stay reproducible (and differ from the paper-scoped ones)."""
        from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

        records = project(_two_sentence_unified())
        default_a = [a["id"] for a in generate_w3c_jsonld(records)]
        default_b = [a["id"] for a in generate_w3c_jsonld(records)]
        scoped = [a["id"] for a in generate_w3c_jsonld(records, base_uri=self.BASE)]

        assert default_a == default_b
        assert default_a != scoped

    def test_id_is_uuid5_of_fixed_namespace_and_content_only(self):
        """3.4: the id is exactly uuid5(namespace, source/page/occurrence/text) —
        recomputable from stable content alone (no time, path or randomness)."""
        import uuid

        from artifact_generation.w3c_annotation import AnnotationRecord, generate_w3c_jsonld

        quote = {"exact": "Known text.", "prefix": "", "suffix": ""}
        rec = AnnotationRecord("Known text.", 4, "TextQuoteSelector", {}, quote, occurrence=2)
        anno = generate_w3c_jsonld([rec], base_uri=self.BASE)[0]

        namespace = uuid.uuid5(uuid.NAMESPACE_URL, "urn:evitrace:anno")
        expected = uuid.uuid5(namespace, f"{self.BASE}\x1f4\x1f2\x1fKnown text.")
        assert anno["id"] == f"urn:evitrace:anno:{expected}"
        assert anno["target"]["source"] == self.BASE
