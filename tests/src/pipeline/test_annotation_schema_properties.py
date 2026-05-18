"""Property-based tests for annotation schema uniformity (Property 14).

**Validates: Requirements 9.1, 9.2, 9.3, 9.4**

Property 14: For any annotation stored in evidence_items[*].annotations,
regardless of whether it was produced by a heuristic or a service, it SHALL be
a dict containing at minimum `text` (str), `type` (str), `source` (str), and
optionally `confidence` (float in [0.0, 1.0] or null). Extra service metadata
SHALL be stored under a `metadata` key. No annotation list SHALL contain mixed
string/dict entries.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Import the evidence_index module directly (same pattern as existing tests)
# ---------------------------------------------------------------------------

_EVIDENCE_PATH = Path(__file__).resolve().parents[3] / "src" / "pipeline" / "evidence_index.py"
_SPEC = importlib.util.spec_from_file_location("pipeline_evidence_index_annotation_props", _EVIDENCE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

_make_annotation = _MODULE._make_annotation
_normalize_service_annotation = _MODULE._normalize_service_annotation
_heuristic_quantities = _MODULE._heuristic_quantities
_heuristic_datasets = _MODULE._heuristic_datasets
_heuristic_entities = _MODULE._heuristic_entities


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Annotation types used in the pipeline
_ANNOTATION_TYPES = st.sampled_from(["quantity", "dataset", "entity"])

# Sources for heuristic annotations
_HEURISTIC_SOURCES = st.sampled_from(["heuristic_regex"])

# Sources for service annotations
_SERVICE_SOURCES = st.sampled_from([
    "grobid_quantities", "datastet", "entity_fishing",
])

# All valid sources
_ALL_SOURCES = st.sampled_from([
    "heuristic_regex", "grobid_quantities", "datastet", "entity_fishing",
])

# Confidence values: float in [0.0, 1.0] or None
_CONFIDENCE = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)

# Non-empty text strings for annotations
_ANNOTATION_TEXT = st.text(min_size=1, max_size=200).filter(lambda t: t.strip())

# Metadata dicts (extra service-specific fields)
_METADATA = st.dictionaries(
    keys=st.text(min_size=1, max_size=30).filter(lambda k: k.isidentifier()),
    values=st.one_of(
        st.text(max_size=100),
        st.integers(min_value=-1000, max_value=1000),
        st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
        st.booleans(),
        st.none(),
    ),
    max_size=5,
)

# Service raw annotation dicts (simulating what GROBID quantities, DataStet,
# entity-fishing return)
_SERVICE_TEXT_KEYS = st.sampled_from([
    "rawName", "rawForm", "normalizedForm", "name", "text", "rawValue",
])

_SERVICE_CONFIDENCE_KEYS = st.sampled_from([
    "confidence", "conf", "score", "nerd_score",
])


@st.composite
def _raw_service_annotation(draw):
    """Generate a raw service annotation dict as returned by addon services."""
    # Pick a text key and value
    text_key = draw(_SERVICE_TEXT_KEYS)
    text_value = draw(_ANNOTATION_TEXT)

    raw: dict[str, Any] = {text_key: text_value}

    # Optionally add a confidence value
    if draw(st.booleans()):
        conf_key = draw(_SERVICE_CONFIDENCE_KEYS)
        conf_value = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
        raw[conf_key] = conf_value

    # Add extra metadata fields
    extra = draw(_METADATA)
    # Avoid overwriting the text/confidence keys
    base_keys = {"rawName", "rawForm", "normalizedForm", "name", "text",
                 "rawValue", "confidence", "conf", "score", "nerd_score"}
    for k, v in extra.items():
        if k not in base_keys:
            raw[k] = v

    return raw


# Text that contains quantity-like patterns for heuristic testing
_QUANTITY_TEXT = st.sampled_from([
    "The dose was 5mg daily",
    "Patients received 10ml of solution",
    "Follow-up at 2 years",
    "Weight loss of 3kg observed",
    "Blood pressure 120mm Hg",
    "Treatment lasted 14 days",
    "Concentration of 0.5%",
])

# Text that contains dataset-like patterns for heuristic testing
_DATASET_TEXT = st.sampled_from([
    "Data from MIMIC-III was used",
    "We analyzed the eICU database",
    "UK Biobank participants were included",
    "Using MIMIC-IV records",
    "MarketScan claims data",
])

# Text that contains entity-like patterns for heuristic testing
_ENTITY_TEXT = st.sampled_from([
    "John Smith conducted the study",
    "Harvard Medical School researchers",
    "The New York Times reported",
    "World Health Organization guidelines",
    "United States Food and Drug Administration",
])


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def _validate_annotation(ann: Any) -> None:
    """Assert that an annotation conforms to the NormalizedAnnotation schema."""
    # Must be a dict
    assert isinstance(ann, dict), (
        f"Annotation must be a dict, got {type(ann).__name__}: {ann!r}"
    )

    # Required keys
    assert "text" in ann, f"Annotation missing 'text' key: {ann!r}"
    assert "type" in ann, f"Annotation missing 'type' key: {ann!r}"
    assert "source" in ann, f"Annotation missing 'source' key: {ann!r}"

    # Type checks for required fields
    assert isinstance(ann["text"], str), (
        f"'text' must be str, got {type(ann['text']).__name__}: {ann['text']!r}"
    )
    assert isinstance(ann["type"], str), (
        f"'type' must be str, got {type(ann['type']).__name__}: {ann['type']!r}"
    )
    assert isinstance(ann["source"], str), (
        f"'source' must be str, got {type(ann['source']).__name__}: {ann['source']!r}"
    )

    # Confidence: optional, float in [0.0, 1.0] or None
    assert "confidence" in ann, f"Annotation missing 'confidence' key: {ann!r}"
    confidence = ann["confidence"]
    if confidence is not None:
        assert isinstance(confidence, (int, float)), (
            f"'confidence' must be float or None, got {type(confidence).__name__}"
        )
        assert 0.0 <= float(confidence) <= 1.0, (
            f"'confidence' must be in [0.0, 1.0], got {confidence}"
        )

    # Metadata: must be a dict
    assert "metadata" in ann, f"Annotation missing 'metadata' key: {ann!r}"
    assert isinstance(ann["metadata"], dict), (
        f"'metadata' must be a dict, got {type(ann['metadata']).__name__}"
    )


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


@given(
    text=_ANNOTATION_TEXT,
    ann_type=_ANNOTATION_TYPES,
    source=_ALL_SOURCES,
    confidence=_CONFIDENCE,
    metadata=_METADATA,
)
@settings(max_examples=100)
def test_make_annotation_produces_valid_schema(
    text: str,
    ann_type: str,
    source: str,
    confidence: float | None,
    metadata: dict,
):
    """_make_annotation() always produces a dict conforming to NormalizedAnnotation.

    **Validates: Requirements 9.1, 9.2**
    """
    ann = _make_annotation(text, ann_type, source, confidence=confidence, metadata=metadata)
    _validate_annotation(ann)

    # Verify the values match what was passed in
    assert ann["text"] == text
    assert ann["type"] == ann_type
    assert ann["source"] == source
    assert ann["confidence"] == confidence
    assert ann["metadata"] == metadata


@given(
    raw=_raw_service_annotation(),
    ann_type=_ANNOTATION_TYPES,
    source=_SERVICE_SOURCES,
)
@settings(max_examples=100)
def test_normalize_service_annotation_produces_valid_schema(
    raw: dict[str, Any],
    ann_type: str,
    source: str,
):
    """_normalize_service_annotation() always produces a dict conforming to NormalizedAnnotation.

    Extra service-specific fields beyond the base schema are stored under `metadata`.

    **Validates: Requirements 9.2, 9.3**
    """
    ann = _normalize_service_annotation(raw, ann_type, source)
    _validate_annotation(ann)

    # Type and source must match what was passed
    assert ann["type"] == ann_type
    assert ann["source"] == source

    # Extra fields from raw (not in base_keys) must be in metadata
    base_keys = {"rawName", "rawForm", "normalizedForm", "name", "text",
                 "rawValue", "confidence", "conf", "score", "nerd_score"}
    for key in raw:
        if key not in base_keys:
            assert key in ann["metadata"], (
                f"Extra field {key!r} from raw annotation not found in metadata"
            )
            assert ann["metadata"][key] == raw[key], (
                f"Metadata field {key!r} value mismatch"
            )


@given(text=_QUANTITY_TEXT)
@settings(max_examples=100)
def test_heuristic_quantities_produce_uniform_annotations(text: str):
    """Heuristic quantity annotations use the same schema as service annotations.

    **Validates: Requirements 9.1, 9.4**
    """
    annotations = _heuristic_quantities(text)

    # All annotations must be dicts (no mixed string/dict entries)
    for ann in annotations:
        _validate_annotation(ann)
        assert ann["type"] == "quantity"
        assert ann["source"] == "heuristic_regex"


@given(text=_DATASET_TEXT)
@settings(max_examples=100)
def test_heuristic_datasets_produce_uniform_annotations(text: str):
    """Heuristic dataset annotations use the same schema as service annotations.

    **Validates: Requirements 9.1, 9.4**
    """
    annotations = _heuristic_datasets(text)

    # All annotations must be dicts (no mixed string/dict entries)
    for ann in annotations:
        _validate_annotation(ann)
        assert ann["type"] == "dataset"
        assert ann["source"] == "heuristic_regex"


@given(text=_ENTITY_TEXT)
@settings(max_examples=100)
def test_heuristic_entities_produce_uniform_annotations(text: str):
    """Heuristic entity annotations use the same schema as service annotations.

    **Validates: Requirements 9.1, 9.4**
    """
    annotations = _heuristic_entities(text)

    # All annotations must be dicts (no mixed string/dict entries)
    for ann in annotations:
        _validate_annotation(ann)
        assert ann["type"] == "entity"
        assert ann["source"] == "heuristic_regex"


@given(
    raw=_raw_service_annotation(),
    ann_type=_ANNOTATION_TYPES,
    source=_SERVICE_SOURCES,
)
@settings(max_examples=100)
def test_service_confidence_clamped_to_valid_range(
    raw: dict[str, Any],
    ann_type: str,
    source: str,
):
    """Service annotation confidence is always clamped to [0.0, 1.0] or None.

    **Validates: Requirements 9.2**
    """
    ann = _normalize_service_annotation(raw, ann_type, source)

    confidence = ann["confidence"]
    if confidence is not None:
        assert 0.0 <= confidence <= 1.0, (
            f"Confidence {confidence} is outside [0.0, 1.0]"
        )


@given(
    heuristic_text=_QUANTITY_TEXT,
    raw=_raw_service_annotation(),
)
@settings(max_examples=100)
def test_no_mixed_string_dict_entries_in_annotation_lists(
    heuristic_text: str,
    raw: dict[str, Any],
):
    """Annotation lists never contain mixed string/dict entries.

    When combining heuristic and service annotations in the same list,
    every element must be a dict conforming to the annotation schema.

    **Validates: Requirements 9.4**
    """
    # Generate heuristic annotations
    heuristic_anns = _heuristic_quantities(heuristic_text)

    # Generate a service annotation
    service_ann = _normalize_service_annotation(raw, "quantity", "grobid_quantities")

    # Combine them as would happen in a real annotation list
    combined = heuristic_anns + [service_ann]

    # Every entry must be a dict — no strings allowed
    for i, entry in enumerate(combined):
        assert isinstance(entry, dict), (
            f"Entry {i} in annotation list is {type(entry).__name__}, not dict: {entry!r}"
        )
        _validate_annotation(entry)


@given(
    text=_ANNOTATION_TEXT,
    ann_type=_ANNOTATION_TYPES,
    source=_ALL_SOURCES,
)
@settings(max_examples=100)
def test_heuristic_and_service_annotations_share_same_keys(
    text: str,
    ann_type: str,
    source: str,
):
    """Both heuristic and service annotations have the same required key set.

    **Validates: Requirements 9.1, 9.2**
    """
    # Heuristic annotation
    heuristic = _make_annotation(text, ann_type, source)

    # Service annotation (simulate a raw service response)
    raw_service = {"rawName": text, "extra_field": "value"}
    service = _normalize_service_annotation(raw_service, ann_type, source)

    # Both must have the same required keys
    required_keys = {"text", "type", "source", "confidence", "metadata"}
    assert set(heuristic.keys()) == required_keys, (
        f"Heuristic annotation keys {set(heuristic.keys())} != {required_keys}"
    )
    assert set(service.keys()) == required_keys, (
        f"Service annotation keys {set(service.keys())} != {required_keys}"
    )
