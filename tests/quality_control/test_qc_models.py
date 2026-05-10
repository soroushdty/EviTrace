"""
tests/test_qc_models.py
=======================
Tests for LocalQCMetricRecord dataclass in pdf_extractor/extraction/quality_control/models.py.

Covers:
  - Requirements 13.11, 14.4
  - Import succeeds from the public quality_control package
  - Instantiation with valid field values
  - Field type annotations match design spec
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Import test (TDD: this test fails until LocalQCMetricRecord is added)
# ---------------------------------------------------------------------------

def test_local_qc_metric_record_importable():
    """LocalQCMetricRecord must be importable from the public package."""
    from quality_control import LocalQCMetricRecord  # noqa: F401


def test_local_qc_metric_record_in_all():
    """LocalQCMetricRecord must appear in __all__ of the quality_control package."""
    import quality_control as qc
    assert "LocalQCMetricRecord" in qc.__all__


# ---------------------------------------------------------------------------
# Instantiation tests
# ---------------------------------------------------------------------------

def test_local_qc_metric_record_basic_instantiation():
    """Creates a valid instance with float computed_value and float threshold."""
    from quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="min_chars_per_page",
        computed_value=0.5,
        threshold=0.7,
        triggered=False,
    )
    assert rec.metric_name == "min_chars_per_page"
    assert rec.computed_value == 0.5
    assert rec.threshold == 0.7
    assert rec.triggered is False


def test_local_qc_metric_record_int_values():
    """computed_value and threshold accept int values."""
    from quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="page_count",
        computed_value=3,
        threshold=1,
        triggered=True,
    )
    assert rec.computed_value == 3
    assert rec.threshold == 1
    assert rec.triggered is True


def test_local_qc_metric_record_bool_computed_value():
    """computed_value accepts bool (boolean checks)."""
    from quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="has_text",
        computed_value=True,
        threshold=None,
        triggered=False,
    )
    assert rec.computed_value is True
    assert rec.threshold is None


def test_local_qc_metric_record_none_threshold():
    """threshold can be None for boolean checks."""
    from quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="weird_char_ratio",
        computed_value=0.02,
        threshold=None,
        triggered=False,
    )
    assert rec.threshold is None


def test_local_qc_metric_record_triggered_true():
    """triggered=True when metric fires (issue detected)."""
    from quality_control import LocalQCMetricRecord

    rec = LocalQCMetricRecord(
        metric_name="weird_char_ratio",
        computed_value=0.9,
        threshold=0.3,
        triggered=True,
    )
    assert rec.triggered is True


# ---------------------------------------------------------------------------
# Field annotation tests
# ---------------------------------------------------------------------------

def test_local_qc_metric_record_field_annotations():
    """Verify field names exist as expected on the dataclass."""
    from quality_control import LocalQCMetricRecord
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(LocalQCMetricRecord)}
    assert "metric_name" in field_names
    assert "computed_value" in field_names
    assert "threshold" in field_names
    assert "triggered" in field_names


def test_local_qc_metric_record_is_dataclass():
    """LocalQCMetricRecord must be a dataclass."""
    from quality_control import LocalQCMetricRecord
    import dataclasses

    assert dataclasses.is_dataclass(LocalQCMetricRecord)


# ---------------------------------------------------------------------------
# Task 3.1: New typed data layer dataclasses — import / __all__ tests
# ---------------------------------------------------------------------------

def test_new_dataclasses_importable():
    """SemanticLayer, StructuralLayer, AlignmentMapEntry, AlignmentMap must be
    importable from the public quality_control package."""
    from quality_control import (  # noqa: F401
        SemanticLayer,
        StructuralLayer,
        AlignmentMapEntry,
        AlignmentMap,
    )


def test_new_dataclasses_in_all():
    """All four new dataclasses must appear in quality_control.__all__."""
    import quality_control as qc

    for name in ("SemanticLayer", "StructuralLayer", "AlignmentMapEntry", "AlignmentMap"):
        assert name in qc.__all__, f"{name} missing from quality_control.__all__"


# ---------------------------------------------------------------------------
# SemanticLayer
# ---------------------------------------------------------------------------

def test_semantic_layer_default_instantiation():
    """SemanticLayer constructs with all defaults."""
    import dataclasses
    from quality_control import SemanticLayer

    sl = SemanticLayer()
    assert dataclasses.is_dataclass(sl)
    assert sl.metadata == {}
    assert sl.sections == []
    assert sl.paragraphs == []
    assert sl.sentences == []
    assert sl.references == []


def test_semantic_layer_list_fields_are_independent():
    """Each SemanticLayer instance has its own independent list objects."""
    from quality_control import SemanticLayer

    a = SemanticLayer()
    b = SemanticLayer()
    a.sections.append({"heading": "Intro"})
    assert b.sections == [], "list fields must use default_factory=list"


def test_semantic_layer_accepts_values():
    """SemanticLayer stores assigned values correctly."""
    from quality_control import SemanticLayer

    sl = SemanticLayer(
        metadata={"title": "Test Doc"},
        sections=[{"heading": "Abstract", "depth": 1}],
        paragraphs=[{"text": "Lorem ipsum"}],
        sentences=[{"text": "Lorem ipsum."}],
        references=[{"ref_id": "1", "text": "Author 2024"}],
    )
    assert sl.metadata["title"] == "Test Doc"
    assert len(sl.sections) == 1
    assert len(sl.paragraphs) == 1
    assert len(sl.sentences) == 1
    assert len(sl.references) == 1


# ---------------------------------------------------------------------------
# StructuralLayer
# ---------------------------------------------------------------------------

def test_structural_layer_default_instantiation():
    """StructuralLayer constructs with all defaults."""
    import dataclasses
    from quality_control import StructuralLayer

    stl = StructuralLayer()
    assert dataclasses.is_dataclass(stl)
    assert stl.pages == []
    assert stl.blocks == []
    assert stl.tables == []
    assert stl.figures == []


def test_structural_layer_list_fields_are_independent():
    """Each StructuralLayer instance has its own independent list objects."""
    from quality_control import StructuralLayer

    a = StructuralLayer()
    b = StructuralLayer()
    a.blocks.append({"bbox": (0, 0, 100, 20)})
    assert b.blocks == [], "list fields must use default_factory=list"


def test_structural_layer_accepts_values():
    """StructuralLayer stores assigned values correctly."""
    from quality_control import StructuralLayer

    stl = StructuralLayer(
        pages=[{"index": 0, "width": 612, "height": 792}],
        blocks=[{"bbox": (0, 0, 100, 20), "text": "Hi"}],
        tables=[{"caption": "Table 1"}],
        figures=[{"caption": "Figure 1"}],
    )
    assert len(stl.pages) == 1
    assert len(stl.blocks) == 1
    assert len(stl.tables) == 1
    assert len(stl.figures) == 1


# ---------------------------------------------------------------------------
# AlignmentMapEntry
# ---------------------------------------------------------------------------

def test_alignment_map_entry_default_instantiation():
    """AlignmentMapEntry constructs with correct defaults."""
    import dataclasses
    from quality_control import AlignmentMapEntry

    entry = AlignmentMapEntry()
    assert dataclasses.is_dataclass(entry)
    assert entry.source == "native"
    assert entry.ocr_derived is False
    assert entry.ocr_engines == []
    assert entry.agreement == "full"
    assert entry.edit_distance == 0.0
    assert entry.preferred_reading == ""
    assert entry.confidence == 1.0


def test_alignment_map_entry_source_is_free_string():
    """AlignmentMapEntry.source accepts any string — not constrained to a fixed
    extractor name set (Req 1.3)."""
    from quality_control import AlignmentMapEntry

    for value in ("native", "grobid", "pdfplumber", "custom_extractor_xyz", "", "42"):
        entry = AlignmentMapEntry(source=value)
        assert entry.source == value, f"source should accept '{value}'"


def test_alignment_map_entry_agreement_values():
    """AlignmentMapEntry.agreement accepts the four defined values."""
    from quality_control import AlignmentMapEntry

    for level in ("full", "partial", "divergent", "one_engine_only"):
        entry = AlignmentMapEntry(agreement=level)
        assert entry.agreement == level


def test_alignment_map_entry_ocr_derived_and_engines():
    """AlignmentMapEntry stores ocr_derived and ocr_engines correctly."""
    from quality_control import AlignmentMapEntry

    entry = AlignmentMapEntry(ocr_derived=True, ocr_engines=["paddle"])
    assert entry.ocr_derived is True
    assert entry.ocr_engines == ["paddle"]


def test_alignment_map_entry_ocr_engines_independent():
    """Each AlignmentMapEntry has its own independent ocr_engines list."""
    from quality_control import AlignmentMapEntry

    a = AlignmentMapEntry()
    b = AlignmentMapEntry()
    a.ocr_engines.append("paddle")
    assert b.ocr_engines == [], "ocr_engines must use default_factory=list"


def test_alignment_map_entry_numeric_fields():
    """edit_distance and confidence store float values in [0.0, 1.0]."""
    from quality_control import AlignmentMapEntry

    entry = AlignmentMapEntry(edit_distance=0.25, confidence=0.9)
    assert entry.edit_distance == 0.25
    assert entry.confidence == 0.9


# ---------------------------------------------------------------------------
# AlignmentMap
# ---------------------------------------------------------------------------

def test_alignment_map_default_instantiation():
    """AlignmentMap constructs with all list defaults empty."""
    import dataclasses
    from quality_control import AlignmentMap

    am = AlignmentMap()
    assert dataclasses.is_dataclass(am)
    assert am.paragraph_to_blocks == []
    assert am.sentence_to_char_range == []
    assert am.section_header_to_block == []
    assert am.reconciliation_flags == []


def test_alignment_map_list_fields_are_independent():
    """Each AlignmentMap instance has its own independent list objects."""
    from quality_control import AlignmentMap

    a = AlignmentMap()
    b = AlignmentMap()
    a.reconciliation_flags.append({"note": "divergent"})
    assert b.reconciliation_flags == [], "list fields must use default_factory=list"


def test_alignment_map_accepts_entries():
    """AlignmentMap stores AlignmentMapEntry objects correctly."""
    from quality_control import AlignmentMap, AlignmentMapEntry

    entry = AlignmentMapEntry(source="pdfplumber", agreement="partial")
    am = AlignmentMap(
        paragraph_to_blocks=[entry],
        section_header_to_block=[entry],
        reconciliation_flags=[entry],
    )
    assert len(am.paragraph_to_blocks) == 1
    assert am.paragraph_to_blocks[0].source == "pdfplumber"


# ---------------------------------------------------------------------------
# UnifiedRecord extension (Req 1.4 backward compat + new optional fields)
# ---------------------------------------------------------------------------

def test_unified_record_backward_compat():
    """UnifiedRecord.document_id and .content are unchanged (Req 1.4)."""
    from quality_control import UnifiedRecord

    rec = UnifiedRecord(document_id="doc-001", content={"text": "Hello"})
    assert rec.document_id == "doc-001"
    assert rec.content == {"text": "Hello"}


def test_unified_record_new_fields_default_none():
    """UnifiedRecord.semantic, .structural, .alignment default to None."""
    from quality_control import UnifiedRecord

    rec = UnifiedRecord()
    assert rec.semantic is None
    assert rec.structural is None
    assert rec.alignment is None


def test_unified_record_accepts_typed_layers():
    """UnifiedRecord accepts SemanticLayer, StructuralLayer, AlignmentMap."""
    from quality_control import (
        UnifiedRecord,
        SemanticLayer,
        StructuralLayer,
        AlignmentMap,
    )

    rec = UnifiedRecord(
        document_id="doc-002",
        content={"raw": "text"},
        semantic=SemanticLayer(metadata={"title": "Paper"}),
        structural=StructuralLayer(pages=[{"index": 0}]),
        alignment=AlignmentMap(),
    )
    assert rec.semantic is not None
    assert rec.semantic.metadata["title"] == "Paper"
    assert rec.structural is not None
    assert rec.alignment is not None


def test_unified_record_content_alongside_new_fields():
    """content field is populated alongside semantic/structural/alignment (Req 1.4)."""
    from quality_control import UnifiedRecord, SemanticLayer, StructuralLayer, AlignmentMap

    rec = UnifiedRecord(
        document_id="doc-003",
        content={"legacy_key": "legacy_value"},
        semantic=SemanticLayer(),
        structural=StructuralLayer(),
        alignment=AlignmentMap(),
    )
    # Old field still accessible
    assert rec.content["legacy_key"] == "legacy_value"
    # New fields also accessible
    assert rec.semantic is not None
    assert rec.structural is not None
    assert rec.alignment is not None
