"""
tests/src/pipeline/test_annotation_schema.py
---------------------------------------------
Unit tests for the Normalized Annotation Schema (Req 9).

Covers:
  - W3C annotations remain unchanged (do not use NormalizedAnnotation)
  - Annotations from two different sources (heuristic + service) both
    validate against the same schema
  - _make_annotation() produces valid NormalizedAnnotation dicts
  - _normalize_service_annotation() produces valid NormalizedAnnotation dicts

Requirements: 9.5, 9.6
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from pipeline.evidence_index import (
    NormalizedAnnotation,
    _make_annotation,
    _normalize_service_annotation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validates_annotation_schema(ann: dict) -> bool:
    """Return True if ann conforms to the NormalizedAnnotation schema."""
    # Required keys
    if not isinstance(ann, dict):
        return False
    for key in ("text", "type", "source"):
        if key not in ann:
            return False
        if not isinstance(ann[key], str):
            return False
    # confidence: float in [0.0, 1.0] or None
    if "confidence" not in ann:
        return False
    conf = ann["confidence"]
    if conf is not None:
        if not isinstance(conf, (int, float)):
            return False
        if conf < 0.0 or conf > 1.0:
            return False
    # metadata: must be a dict
    if "metadata" not in ann:
        return False
    if not isinstance(ann["metadata"], dict):
        return False
    return True


# ---------------------------------------------------------------------------
# Test: W3C annotations remain unchanged (Req 9.5)
# ---------------------------------------------------------------------------

class TestW3cAnnotationsUnchanged:
    """Verify that w3c_annotation.py does not use NormalizedAnnotation."""

    def test_w3c_module_does_not_import_normalized_annotation(self):
        """artifact_generation.w3c_annotation must NOT import NormalizedAnnotation."""
        from artifact_generation import w3c_annotation

        source_file = Path(inspect.getfile(w3c_annotation))
        source_code = source_file.read_text(encoding="utf-8")

        # AST-based check: no import of NormalizedAnnotation
        tree = ast.parse(source_code)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.names:
                    imported_names = [alias.name for alias in node.names]
                    assert "NormalizedAnnotation" not in imported_names, (
                        f"w3c_annotation.py imports NormalizedAnnotation from "
                        f"{node.module} — this violates Req 9.5"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "NormalizedAnnotation" not in alias.name, (
                        f"w3c_annotation.py imports {alias.name} which references "
                        f"NormalizedAnnotation — this violates Req 9.5"
                    )

    def test_w3c_module_does_not_reference_normalized_annotation_in_source(self):
        """The string 'NormalizedAnnotation' must not appear in w3c_annotation.py."""
        from artifact_generation import w3c_annotation

        source_file = Path(inspect.getfile(w3c_annotation))
        source_code = source_file.read_text(encoding="utf-8")

        assert "NormalizedAnnotation" not in source_code, (
            "w3c_annotation.py references NormalizedAnnotation — "
            "the normalized annotation schema applies only to internal "
            "pipeline annotations, not the W3C output artifact (Req 9.5)"
        )

    def test_w3c_annotation_record_uses_own_dataclass(self):
        """W3C annotations use AnnotationRecord, not NormalizedAnnotation."""
        from artifact_generation.w3c_annotation import AnnotationRecord

        # AnnotationRecord is a dataclass with its own fields
        assert hasattr(AnnotationRecord, "__dataclass_fields__")
        fields = AnnotationRecord.__dataclass_fields__
        # It should have W3C-specific fields, not NormalizedAnnotation fields
        assert "sentence_text" in fields
        assert "selector_type" in fields
        # It should NOT have NormalizedAnnotation fields
        assert "source" not in fields
        assert "metadata" not in fields


# ---------------------------------------------------------------------------
# Test: Annotations from two sources validate against same schema (Req 9.6)
# ---------------------------------------------------------------------------

class TestAnnotationSchemaUniformity:
    """Verify heuristic and service annotations share the same schema."""

    def test_heuristic_annotation_validates(self):
        """_make_annotation() produces a valid NormalizedAnnotation dict."""
        ann = _make_annotation("5mg", "quantity", "heuristic_regex")
        assert _validates_annotation_schema(ann)

    def test_heuristic_annotation_with_confidence_validates(self):
        """_make_annotation() with explicit confidence produces valid output."""
        ann = _make_annotation(
            "MIMIC-III", "dataset", "heuristic_regex", confidence=0.9
        )
        assert _validates_annotation_schema(ann)
        assert ann["confidence"] == 0.9

    def test_service_annotation_validates(self):
        """_normalize_service_annotation() produces a valid NormalizedAnnotation dict."""
        raw_service_response = {
            "rawName": "10 mg/day",
            "confidence": 0.85,
            "unit": "mg",
            "value": 10,
            "offsetStart": 42,
            "offsetEnd": 52,
        }
        ann = _normalize_service_annotation(raw_service_response, "quantity", "grobid_quantities")
        assert _validates_annotation_schema(ann)

    def test_heuristic_and_service_share_same_keys(self):
        """Both heuristic and service annotations have identical top-level keys."""
        heuristic = _make_annotation("heart failure", "entity", "heuristic_regex")
        service = _normalize_service_annotation(
            {"rawName": "heart failure", "nerd_score": 0.92, "wikidataId": "Q181754"},
            "entity",
            "entity_fishing",
        )

        assert set(heuristic.keys()) == set(service.keys())
        expected_keys = {"text", "type", "source", "confidence", "metadata"}
        assert set(heuristic.keys()) == expected_keys
        assert set(service.keys()) == expected_keys

    def test_service_extra_fields_stored_in_metadata(self):
        """Service-specific fields beyond the base schema go into metadata."""
        raw = {
            "rawName": "UK Biobank",
            "confidence": 0.95,
            "wikidataId": "Q28133228",
            "lang": "en",
            "url": "https://www.ukbiobank.ac.uk",
        }
        ann = _normalize_service_annotation(raw, "dataset", "datastet")

        assert ann["text"] == "UK Biobank"
        assert ann["type"] == "dataset"
        assert ann["source"] == "datastet"
        assert ann["confidence"] == 0.95
        # Extra fields stored in metadata
        assert "wikidataId" in ann["metadata"]
        assert "lang" in ann["metadata"]
        assert "url" in ann["metadata"]
        # Base fields NOT duplicated in metadata
        assert "rawName" not in ann["metadata"]
        assert "confidence" not in ann["metadata"]

    def test_service_annotation_without_confidence(self):
        """Service annotation with no confidence key → confidence is None."""
        raw = {"rawName": "MIMIC-IV", "type": "dataset"}
        ann = _normalize_service_annotation(raw, "dataset", "datastet")

        assert _validates_annotation_schema(ann)
        assert ann["confidence"] is None

    def test_service_annotation_confidence_clamped(self):
        """Service confidence values outside [0, 1] are clamped."""
        raw = {"rawName": "test", "confidence": 1.5}
        ann = _normalize_service_annotation(raw, "quantity", "grobid_quantities")
        assert ann["confidence"] == 1.0

        raw_neg = {"rawName": "test", "confidence": -0.3}
        ann_neg = _normalize_service_annotation(raw_neg, "quantity", "grobid_quantities")
        assert ann_neg["confidence"] == 0.0

    def test_no_mixed_string_dict_entries(self):
        """Annotation lists must not contain mixed string/dict entries."""
        # Create annotations from both sources
        heuristic_anns = [
            _make_annotation("5mg", "quantity", "heuristic_regex"),
            _make_annotation("10ml", "quantity", "heuristic_regex"),
        ]
        service_anns = [
            _normalize_service_annotation(
                {"rawName": "20 kg", "confidence": 0.9, "unit": "kg"},
                "quantity",
                "grobid_quantities",
            ),
        ]

        # Combine them as they would be in a real annotation list
        combined = heuristic_anns + service_anns

        # Every element must be a dict (no strings)
        for ann in combined:
            assert isinstance(ann, dict), (
                f"Annotation list contains non-dict entry: {type(ann)}"
            )
            assert _validates_annotation_schema(ann)

    def test_make_annotation_default_metadata_is_empty_dict(self):
        """_make_annotation() without metadata kwarg produces empty dict."""
        ann = _make_annotation("test", "entity", "heuristic_regex")
        assert ann["metadata"] == {}

    def test_make_annotation_custom_metadata(self):
        """_make_annotation() with metadata kwarg stores it correctly."""
        ann = _make_annotation(
            "test", "entity", "heuristic_regex",
            metadata={"pattern": r"\b[A-Z]+\b"},
        )
        assert ann["metadata"] == {"pattern": r"\b[A-Z]+\b"}

    def test_normalize_service_annotation_extracts_text_from_various_keys(self):
        """_normalize_service_annotation() extracts text from common service keys."""
        # rawName
        ann1 = _normalize_service_annotation(
            {"rawName": "aspirin"}, "entity", "entity_fishing"
        )
        assert ann1["text"] == "aspirin"

        # rawForm
        ann2 = _normalize_service_annotation(
            {"rawForm": "100mg"}, "quantity", "grobid_quantities"
        )
        assert ann2["text"] == "100mg"

        # name
        ann3 = _normalize_service_annotation(
            {"name": "MIMIC-III"}, "dataset", "datastet"
        )
        assert ann3["text"] == "MIMIC-III"

    def test_normalize_service_annotation_empty_raw(self):
        """_normalize_service_annotation() handles empty raw dict gracefully."""
        ann = _normalize_service_annotation({}, "entity", "entity_fishing")
        assert _validates_annotation_schema(ann)
        assert ann["text"] == ""
        assert ann["confidence"] is None
        assert ann["metadata"] == {}
