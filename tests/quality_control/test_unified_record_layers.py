"""
tests/pdf_extractor/test_unified_record_layers.py
==================================================
Tests for the typed data layer dataclasses introduced in Task 3.1.

Covers Requirements: 1.1, 1.2, 1.3, 1.4

Key behaviors verified:
- All four new dataclasses (SemanticLayer, StructuralLayer, AlignmentMapEntry,
  AlignmentMap) construct with correct defaults.
- UnifiedRecord.content is accessible alongside the new typed fields.
- AlignmentMapEntry.source is a free string — not constrained to any extractor
  name set.
- All list fields use field(default_factory=list) so instances are independent.
"""

from __future__ import annotations

import dataclasses

import pytest


# ---------------------------------------------------------------------------
# Requirement 1.1 — SemanticLayer
# ---------------------------------------------------------------------------

class TestSemanticLayer:
    """SemanticLayer — born-digital and OCR-derived semantic content (Req 1.1)."""

    def test_importable_from_quality_control(self):
        from quality_control import SemanticLayer  # noqa: F401

    def test_is_dataclass(self):
        from quality_control import SemanticLayer
        assert dataclasses.is_dataclass(SemanticLayer)

    def test_default_fields(self):
        from quality_control import SemanticLayer
        sl = SemanticLayer()
        assert sl.metadata == {}
        assert sl.sections == []
        assert sl.paragraphs == []
        assert sl.sentences == []
        assert sl.references == []

    def test_list_fields_use_default_factory(self):
        """Instances must not share mutable defaults."""
        from quality_control import SemanticLayer
        a, b = SemanticLayer(), SemanticLayer()
        a.sections.append({"heading": "Intro"})
        assert b.sections == []

    def test_field_names(self):
        from quality_control import SemanticLayer
        names = {f.name for f in dataclasses.fields(SemanticLayer)}
        assert names == {"metadata", "sections", "paragraphs", "sentences", "references"}

    def test_populated_values_round_trip(self):
        from quality_control import SemanticLayer
        sl = SemanticLayer(
            metadata={"title": "My Paper"},
            sections=[{"heading": "Abstract", "depth": 1}],
            paragraphs=[{"text": "Para text"}],
            sentences=[{"text": "Sentence."}],
            references=[{"ref_id": "r1", "text": "Author 2024"}],
        )
        assert sl.metadata["title"] == "My Paper"
        assert sl.sections[0]["depth"] == 1
        assert sl.sentences[0]["text"] == "Sentence."


# ---------------------------------------------------------------------------
# Requirement 1.2 — StructuralLayer
# ---------------------------------------------------------------------------

class TestStructuralLayer:
    """StructuralLayer — pages, blocks, tables, figures (Req 1.2)."""

    def test_importable_from_quality_control(self):
        from quality_control import StructuralLayer  # noqa: F401

    def test_is_dataclass(self):
        from quality_control import StructuralLayer
        assert dataclasses.is_dataclass(StructuralLayer)

    def test_default_fields(self):
        from quality_control import StructuralLayer
        stl = StructuralLayer()
        assert stl.pages == []
        assert stl.blocks == []
        assert stl.tables == []
        assert stl.figures == []

    def test_list_fields_use_default_factory(self):
        from quality_control import StructuralLayer
        a, b = StructuralLayer(), StructuralLayer()
        a.pages.append({"index": 0, "width": 612, "height": 792})
        assert b.pages == []

    def test_field_names(self):
        from quality_control import StructuralLayer
        names = {f.name for f in dataclasses.fields(StructuralLayer)}
        assert names == {"pages", "blocks", "tables", "figures"}

    def test_blocks_accept_bboxes(self):
        """Blocks are stored as plain dicts with bounding boxes (Req 1.2)."""
        from quality_control import StructuralLayer
        stl = StructuralLayer(
            pages=[{"index": 0, "width": 612, "height": 792}],
            blocks=[{"bbox": (0.0, 0.0, 612.0, 50.0), "text": "Header"}],
        )
        assert stl.blocks[0]["bbox"][2] == 612.0


# ---------------------------------------------------------------------------
# Requirement 1.3 — AlignmentMapEntry and AlignmentMap
# ---------------------------------------------------------------------------

class TestAlignmentMapEntry:
    """AlignmentMapEntry — per-entry provenance and agreement (Req 1.3)."""

    def test_importable_from_quality_control(self):
        from quality_control import AlignmentMapEntry  # noqa: F401

    def test_is_dataclass(self):
        from quality_control import AlignmentMapEntry
        assert dataclasses.is_dataclass(AlignmentMapEntry)

    def test_default_fields(self):
        from quality_control import AlignmentMapEntry
        entry = AlignmentMapEntry()
        assert entry.source == "native"
        assert entry.ocr_derived is False
        assert entry.ocr_engines == []
        assert entry.agreement == "full"
        assert entry.edit_distance == 0.0
        assert entry.preferred_reading == ""
        assert entry.confidence == 1.0

    def test_source_is_free_string(self):
        """Req 1.3: source must not be constrained to a fixed extractor name set."""
        from quality_control import AlignmentMapEntry
        for value in (
            "native", "grobid", "pdfplumber", "pymupdf",
            "paddle", "my_custom_extractor_99", "", "arbitrary",
        ):
            entry = AlignmentMapEntry(source=value)
            assert entry.source == value

    def test_agreement_accepted_values(self):
        from quality_control import AlignmentMapEntry
        for level in ("full", "partial", "divergent", "one_engine_only"):
            entry = AlignmentMapEntry(agreement=level)
            assert entry.agreement == level

    def test_edit_distance_range(self):
        from quality_control import AlignmentMapEntry
        for dist in (0.0, 0.5, 1.0):
            entry = AlignmentMapEntry(edit_distance=dist)
            assert entry.edit_distance == dist

    def test_confidence_range(self):
        from quality_control import AlignmentMapEntry
        for conf in (0.0, 0.5, 1.0):
            entry = AlignmentMapEntry(confidence=conf)
            assert entry.confidence == conf

    def test_ocr_engines_use_default_factory(self):
        from quality_control import AlignmentMapEntry
        a, b = AlignmentMapEntry(), AlignmentMapEntry()
        a.ocr_engines.append("paddle")
        assert b.ocr_engines == []

    def test_preferred_reading_is_string(self):
        from quality_control import AlignmentMapEntry
        entry = AlignmentMapEntry(preferred_reading="The corrected text.")
        assert entry.preferred_reading == "The corrected text."


class TestAlignmentMap:
    """AlignmentMap — container for all entry lists (Req 1.3)."""

    def test_importable_from_quality_control(self):
        from quality_control import AlignmentMap  # noqa: F401

    def test_is_dataclass(self):
        from quality_control import AlignmentMap
        assert dataclasses.is_dataclass(AlignmentMap)

    def test_default_fields(self):
        from quality_control import AlignmentMap
        am = AlignmentMap()
        assert am.paragraph_to_blocks == []
        assert am.sentence_to_char_range == []
        assert am.section_header_to_block == []
        assert am.reconciliation_flags == []

    def test_field_names(self):
        from quality_control import AlignmentMap
        names = {f.name for f in dataclasses.fields(AlignmentMap)}
        assert names == {
            "paragraph_to_blocks",
            "sentence_to_char_range",
            "section_header_to_block",
            "reconciliation_flags",
        }

    def test_list_fields_use_default_factory(self):
        from quality_control import AlignmentMap
        a, b = AlignmentMap(), AlignmentMap()
        a.reconciliation_flags.append("flag")
        assert b.reconciliation_flags == []

    def test_holds_alignment_map_entries(self):
        from quality_control import AlignmentMap, AlignmentMapEntry
        entry = AlignmentMapEntry(source="pdfplumber", agreement="partial", confidence=0.8)
        am = AlignmentMap(
            paragraph_to_blocks=[entry],
            section_header_to_block=[entry],
        )
        assert am.paragraph_to_blocks[0].source == "pdfplumber"
        assert am.paragraph_to_blocks[0].confidence == 0.8


# ---------------------------------------------------------------------------
# Requirement 1.4 — UnifiedRecord default field values
# ---------------------------------------------------------------------------

class TestUnifiedRecordDefaults:
    """UnifiedRecord gains three optional fields; existing fields unchanged (Req 1.4)."""

    def test_document_id_unchanged(self):
        from quality_control import UnifiedRecord
        rec = UnifiedRecord(document_id="doc-42")
        assert rec.document_id == "doc-42"

    def test_content_unchanged(self):
        from quality_control import UnifiedRecord
        rec = UnifiedRecord(content={"key": "val"})
        assert rec.content["key"] == "val"

    def test_new_fields_default_none(self):
        from quality_control import UnifiedRecord
        rec = UnifiedRecord()
        assert rec.semantic is None
        assert rec.structural is None
        assert rec.alignment is None

    def test_content_accessible_alongside_typed_layers(self):
        """Req 1.4: content is populated alongside new typed fields."""
        from quality_control import (
            UnifiedRecord,
            SemanticLayer,
            StructuralLayer,
            AlignmentMap,
        )
        rec = UnifiedRecord(
            document_id="doc-99",
            content={"legacy": True},
            semantic=SemanticLayer(metadata={"title": "Paper"}),
            structural=StructuralLayer(pages=[{"index": 0}]),
            alignment=AlignmentMap(),
        )
        assert rec.content["legacy"] is True
        assert rec.semantic.metadata["title"] == "Paper"
        assert rec.structural.pages[0]["index"] == 0
        assert rec.alignment is not None

    def test_unified_record_with_only_document_id_and_content(self):
        """Callers that only pass document_id and content still work correctly."""
        from quality_control import UnifiedRecord
        rec = UnifiedRecord(document_id="caller-1", content={"data": [1, 2, 3]})
        assert rec.document_id == "caller-1"
        assert rec.semantic is None   # new fields are silently absent
        assert rec.structural is None
        assert rec.alignment is None
