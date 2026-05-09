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
  - Each "id" field matches "urn:evitrace:anno:" prefix
"""
import re

import pytest

from quality_control.models import (
    AlignmentMap,
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
    alignment = AlignmentMap(
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
    """UnifiedRecord with one OCR-derived sentence."""
    semantic = SemanticLayer(
        sentences=[
            {"text": "Scanned text.", "page_index": 1, "ocr_derived": True},
        ]
    )
    structural = StructuralLayer(
        blocks=[
            {
                "page_index": 1,
                "block_bbox": (10, 20, 110, 50),
            }
        ]
    )
    alignment = AlignmentMap()
    return UnifiedRecord(
        document_id="doc-scan",
        semantic=semantic,
        structural=structural,
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
    structural = StructuralLayer(
        blocks=[
            {"page_index": 1, "block_bbox": (5, 10, 105, 40)},
        ]
    )
    alignment = AlignmentMap(
        sentence_to_char_range=[
            {"sentence": "Born digital.", "start": 0, "end": 13, "page_index": 0},
        ]
    )
    return UnifiedRecord(
        document_id="doc-mixed",
        semantic=semantic,
        structural=structural,
        alignment=alignment,
    )


# ---------------------------------------------------------------------------
# Task 8.3 — project() tests (8.1)
# ---------------------------------------------------------------------------

class TestProject:
    def test_returns_list_of_annotation_records_not_dicts(self):
        """project() must return list[AnnotationRecord], not list[dict]."""
        from pdf_extractor.annotation import AnnotationRecord, project

        unified = _born_digital_unified()
        result = project(unified)

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], AnnotationRecord)

    def test_born_digital_has_text_position_selector(self):
        """Born-digital entry → selector_type == 'TextPositionSelector'."""
        from pdf_extractor.annotation import project

        unified = _born_digital_unified()
        records = project(unified)

        assert records[0].selector_type == "TextPositionSelector"

    def test_born_digital_selector_payload_has_start_end(self):
        """Born-digital selector_payload must carry integer start and end."""
        from pdf_extractor.annotation import project

        unified = _born_digital_unified()
        records = project(unified)
        payload = records[0].selector_payload

        assert "start" in payload
        assert "end" in payload
        assert isinstance(payload["start"], int)
        assert isinstance(payload["end"], int)

    def test_born_digital_quote_selector_populated(self):
        """Every record must have a populated quote_selector with exact/prefix/suffix."""
        from pdf_extractor.annotation import project

        unified = _born_digital_unified()
        records = project(unified)
        qs = records[0].quote_selector

        assert "exact" in qs
        assert "prefix" in qs
        assert "suffix" in qs
        assert qs["exact"] == "Hello world."

    def test_scanned_has_fragment_selector(self):
        """Scanned entry → selector_type == 'FragmentSelector'."""
        from pdf_extractor.annotation import project

        unified = _scanned_unified()
        records = project(unified)

        assert records[0].selector_type == "FragmentSelector"

    def test_scanned_ocr_derived_flag_set(self):
        """Scanned entry must have ocr_derived == True on the AnnotationRecord."""
        from pdf_extractor.annotation import project

        unified = _scanned_unified()
        records = project(unified)

        assert records[0].ocr_derived is True

    def test_scanned_quote_selector_populated(self):
        """Scanned records must also carry a populated quote_selector."""
        from pdf_extractor.annotation import project

        unified = _scanned_unified()
        records = project(unified)
        qs = records[0].quote_selector

        assert qs["exact"] == "Scanned text."

    def test_mixed_document_produces_both_selector_types(self):
        """A document with both page types must produce both selector types."""
        from pdf_extractor.annotation import project

        unified = _mixed_unified()
        records = project(unified)

        selector_types = {r.selector_type for r in records}
        assert "TextPositionSelector" in selector_types
        assert "FragmentSelector" in selector_types

    def test_project_returns_empty_list_when_alignment_none(self):
        """project() returns [] when alignment is None."""
        from pdf_extractor.annotation import project

        unified = UnifiedRecord(
            document_id="empty",
            semantic=SemanticLayer(sentences=[{"text": "x", "page_index": 0}]),
            alignment=None,
        )
        result = project(unified)
        assert result == []

    def test_project_returns_empty_list_when_semantic_none(self):
        """project() returns [] when semantic is None."""
        from pdf_extractor.annotation import project

        unified = UnifiedRecord(
            document_id="empty",
            alignment=AlignmentMap(),
            semantic=None,
        )
        result = project(unified)
        assert result == []


# ---------------------------------------------------------------------------
# Task 8.3 — generate_w3c_jsonld() tests (8.2)
# ---------------------------------------------------------------------------

class TestGenerateW3cJsonld:
    def test_empty_list_returns_empty_list(self):
        """generate_w3c_jsonld([]) must return [] without raising."""
        from pdf_extractor.annotation import generate_w3c_jsonld

        result = generate_w3c_jsonld([])
        assert result == []

    def test_born_digital_produces_dict_with_five_required_keys(self):
        """Born-digital record → dict with @context, id, type, body, target."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        result = generate_w3c_jsonld(records)

        assert len(result) == 1
        anno = result[0]
        for key in ("@context", "id", "type", "body", "target"):
            assert key in anno, f"Missing key: {key}"

    def test_born_digital_serializes_text_position_selector(self):
        """Born-digital JSON-LD target must include a TextPositionSelector."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        selectors = anno["target"]["selector"]
        selector_types = [s["type"] for s in selectors]
        assert "TextPositionSelector" in selector_types

    def test_scanned_serializes_fragment_selector(self):
        """Scanned JSON-LD target must include a FragmentSelector."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_scanned_unified())
        anno = generate_w3c_jsonld(records)[0]

        selectors = anno["target"]["selector"]
        selector_types = [s["type"] for s in selectors]
        assert "FragmentSelector" in selector_types

    def test_scanned_body_has_ocr_derived_true(self):
        """Scanned JSON-LD body must carry 'ocr_derived': True."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_scanned_unified())
        anno = generate_w3c_jsonld(records)[0]

        assert anno["body"].get("ocr_derived") is True

    def test_id_matches_urn_prefix(self):
        """Every annotation id must match the pattern urn:evitrace:anno:<uuid4>."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        pattern = r"^urn:evitrace:anno:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        assert re.match(pattern, anno["id"]), f"ID does not match pattern: {anno['id']}"

    def test_scanned_id_matches_urn_prefix(self):
        """Scanned annotation id must also match the urn:evitrace:anno: pattern."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_scanned_unified())
        anno = generate_w3c_jsonld(records)[0]

        pattern = r"^urn:evitrace:anno:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        assert re.match(pattern, anno["id"]), f"ID does not match pattern: {anno['id']}"

    def test_type_field_is_annotation(self):
        """JSON-LD 'type' field must be 'Annotation'."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        assert anno["type"] == "Annotation"

    def test_context_is_w3c_anno_context(self):
        """JSON-LD '@context' must be the W3C annotation context URI."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        assert anno["@context"] == "http://www.w3.org/ns/anno.jsonld"

    def test_text_quote_selector_present_in_born_digital_target(self):
        """Born-digital target must also include a TextQuoteSelector."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_born_digital_unified())
        anno = generate_w3c_jsonld(records)[0]

        selectors = anno["target"]["selector"]
        selector_types = [s["type"] for s in selectors]
        assert "TextQuoteSelector" in selector_types

    def test_text_quote_selector_present_in_scanned_target(self):
        """Scanned target must also include a TextQuoteSelector."""
        from pdf_extractor.annotation import generate_w3c_jsonld, project

        records = project(_scanned_unified())
        anno = generate_w3c_jsonld(records)[0]

        selectors = anno["target"]["selector"]
        selector_types = [s["type"] for s in selectors]
        assert "TextQuoteSelector" in selector_types
