"""
tests/src/pdf_extractor/test_pymupdf_spacing_properties.py
-----------------------------------------------------------
Property-based tests for PyMuPDF span joining logic.

Property 18: Span joining preserves word boundaries
  For any pair of adjacent PyMuPDF spans within a line, a space SHALL be
  inserted between them if and only if the horizontal gap between the right
  edge of the first span and the left edge of the second span exceeds 1/4
  of the average character width of the preceding span.  Zero-gap spans
  SHALL be joined without a space.

**Validates: Requirements 13.1, 13.2**
"""

from __future__ import annotations

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from pdf_extractor.extraction.PyMuPDF import _should_insert_space


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate a span with a valid bbox and non-empty text.
# bbox is (x0, y0, x1, y1) where x1 > x0 and y1 > y0.
def _span_strategy(
    x0: st.SearchStrategy[float] = st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False),
    min_width: float = 5.0,
    max_width: float = 200.0,
):
    """Generate a span dict with bbox and text."""
    return st.builds(
        lambda x0_val, width, height, y0, text: {
            "bbox": (x0_val, y0, x0_val + width, y0 + height),
            "text": text,
        },
        x0_val=x0,
        width=st.floats(min_value=min_width, max_value=max_width, allow_nan=False, allow_infinity=False),
        height=st.floats(min_value=5.0, max_value=50.0, allow_nan=False, allow_infinity=False),
        y0=st.floats(min_value=0.0, max_value=800.0, allow_nan=False, allow_infinity=False),
        text=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N", "P"))),
    )


def _span_pair_with_gap():
    """Generate a pair of spans where the second starts after the first ends,
    with a controlled positive gap."""
    return st.builds(
        lambda prev_x0, prev_width, prev_height, prev_y0, prev_text, gap, curr_width, curr_height, curr_text: (
            {
                "bbox": (prev_x0, prev_y0, prev_x0 + prev_width, prev_y0 + prev_height),
                "text": prev_text,
            },
            {
                "bbox": (prev_x0 + prev_width + gap, prev_y0, prev_x0 + prev_width + gap + curr_width, prev_y0 + curr_height),
                "text": curr_text,
            },
            gap,
        ),
        prev_x0=st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        prev_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        prev_height=st.floats(min_value=5.0, max_value=50.0, allow_nan=False, allow_infinity=False),
        prev_y0=st.floats(min_value=0.0, max_value=800.0, allow_nan=False, allow_infinity=False),
        prev_text=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N"))),
        gap=st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
        curr_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        curr_height=st.floats(min_value=5.0, max_value=50.0, allow_nan=False, allow_infinity=False),
        curr_text=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N"))),
    )


# ---------------------------------------------------------------------------
# Property 18: Span joining preserves word boundaries
# ---------------------------------------------------------------------------

# Feature: audit-remediation, Property 18: Span joining preserves word boundaries

@given(data=_span_pair_with_gap())
@settings(max_examples=100)
def test_space_inserted_iff_gap_exceeds_threshold(data):
    """Space inserted iff horizontal gap > 1/4 avg char width of preceding span.

    **Validates: Requirements 13.1, 13.2**
    """
    prev_span, curr_span, gap = data

    prev_text = prev_span["text"]
    prev_bbox = prev_span["bbox"]

    # Compute expected threshold: 1/4 of avg char width in preceding span
    prev_width = prev_bbox[2] - prev_bbox[0]
    avg_char_width = prev_width / len(prev_text)
    threshold = avg_char_width / 4.0

    result = _should_insert_space(prev_span, curr_span)

    if gap <= 0:
        # Zero or negative gap: no space
        assert result is False, (
            f"Expected no space for gap={gap} <= 0, but got True"
        )
    elif gap > threshold:
        # Gap exceeds threshold: space should be inserted
        assert result is True, (
            f"Expected space for gap={gap} > threshold={threshold}, but got False"
        )
    else:
        # Gap is positive but within threshold: no space
        assert result is False, (
            f"Expected no space for gap={gap} <= threshold={threshold}, but got True"
        )


@given(
    prev_x0=st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    prev_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    prev_height=st.floats(min_value=5.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    prev_y0=st.floats(min_value=0.0, max_value=800.0, allow_nan=False, allow_infinity=False),
    prev_text=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N"))),
    curr_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    curr_height=st.floats(min_value=5.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    curr_text=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N"))),
)
@settings(max_examples=100)
def test_zero_gap_spans_joined_without_space(
    prev_x0, prev_width, prev_height, prev_y0, prev_text,
    curr_width, curr_height, curr_text,
):
    """Zero-gap spans (curr_x0 == prev_x1) SHALL be joined without a space.

    **Validates: Requirements 13.2**
    """
    prev_x1 = prev_x0 + prev_width
    prev_span = {
        "bbox": (prev_x0, prev_y0, prev_x1, prev_y0 + prev_height),
        "text": prev_text,
    }
    # Current span starts exactly where previous ends (zero gap)
    curr_span = {
        "bbox": (prev_x1, prev_y0, prev_x1 + curr_width, prev_y0 + curr_height),
        "text": curr_text,
    }

    result = _should_insert_space(prev_span, curr_span)
    assert result is False, (
        f"Zero-gap spans should be joined without space, but got True. "
        f"prev_x1={prev_x1}, curr_x0={prev_x1}"
    )


@given(
    prev_x0=st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    prev_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    prev_height=st.floats(min_value=5.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    prev_y0=st.floats(min_value=0.0, max_value=800.0, allow_nan=False, allow_infinity=False),
    prev_text=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N"))),
    overlap=st.floats(min_value=0.01, max_value=50.0, allow_nan=False, allow_infinity=False),
    curr_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    curr_height=st.floats(min_value=5.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    curr_text=st.text(min_size=1, max_size=30, alphabet=st.characters(categories=("L", "N"))),
)
@settings(max_examples=100)
def test_overlapping_spans_joined_without_space(
    prev_x0, prev_width, prev_height, prev_y0, prev_text,
    overlap, curr_width, curr_height, curr_text,
):
    """Overlapping spans (negative gap) SHALL be joined without a space.

    **Validates: Requirements 13.2**
    """
    prev_x1 = prev_x0 + prev_width
    prev_span = {
        "bbox": (prev_x0, prev_y0, prev_x1, prev_y0 + prev_height),
        "text": prev_text,
    }
    # Current span starts before previous ends (negative gap / overlap)
    curr_x0 = prev_x1 - overlap
    curr_span = {
        "bbox": (curr_x0, prev_y0, curr_x0 + curr_width, prev_y0 + curr_height),
        "text": curr_text,
    }

    result = _should_insert_space(prev_span, curr_span)
    assert result is False, (
        f"Overlapping spans (gap={curr_x0 - prev_x1}) should be joined without space, "
        f"but got True."
    )


@given(
    prev_width=st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    prev_text=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
    gap_multiplier=st.floats(min_value=0.26, max_value=5.0, allow_nan=False, allow_infinity=False),
    curr_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    curr_text=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
)
@settings(max_examples=100)
def test_gap_above_quarter_char_width_inserts_space(
    prev_width, prev_text, gap_multiplier, curr_width, curr_text,
):
    """When gap > 1/4 avg char width, space SHALL be inserted.

    **Validates: Requirements 13.1**
    """
    # Compute the threshold and ensure gap exceeds it
    avg_char_width = prev_width / len(prev_text)
    threshold = avg_char_width / 4.0
    gap = threshold * gap_multiplier  # gap_multiplier > 0.25 ensures gap > threshold

    # Ensure gap is strictly above threshold (gap_multiplier > 1.0 guarantees this)
    assume(gap > threshold)

    prev_span = {
        "bbox": (0.0, 0.0, prev_width, 20.0),
        "text": prev_text,
    }
    curr_span = {
        "bbox": (prev_width + gap, 0.0, prev_width + gap + curr_width, 20.0),
        "text": curr_text,
    }

    result = _should_insert_space(prev_span, curr_span)
    assert result is True, (
        f"Expected space for gap={gap} > threshold={threshold} "
        f"(avg_char_width={avg_char_width}), but got False"
    )


@given(
    prev_width=st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    prev_text=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
    gap_fraction=st.floats(min_value=0.001, max_value=0.99, allow_nan=False, allow_infinity=False),
    curr_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    curr_text=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
)
@settings(max_examples=100)
def test_gap_at_or_below_quarter_char_width_no_space(
    prev_width, prev_text, gap_fraction, curr_width, curr_text,
):
    """When 0 < gap <= 1/4 avg char width, no space SHALL be inserted.

    **Validates: Requirements 13.1, 13.2**
    """
    avg_char_width = prev_width / len(prev_text)
    threshold = avg_char_width / 4.0
    # gap_fraction in (0, 1) so gap is in (0, threshold)
    gap = threshold * gap_fraction

    assume(gap > 0)
    assume(gap <= threshold)

    prev_span = {
        "bbox": (0.0, 0.0, prev_width, 20.0),
        "text": prev_text,
    }
    curr_span = {
        "bbox": (prev_width + gap, 0.0, prev_width + gap + curr_width, 20.0),
        "text": curr_text,
    }

    result = _should_insert_space(prev_span, curr_span)
    assert result is False, (
        f"Expected no space for gap={gap} <= threshold={threshold} "
        f"(avg_char_width={avg_char_width}), but got True"
    )


@settings(max_examples=100)
@given(
    curr_text=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L", "N"))),
    curr_width=st.floats(min_value=5.0, max_value=200.0, allow_nan=False, allow_infinity=False),
)
def test_missing_bbox_inserts_space_conservatively(curr_text, curr_width):
    """When bbox is unavailable, space SHALL be inserted conservatively.

    **Validates: Requirements 13.1**
    """
    # prev_span with no bbox
    prev_span_no_bbox: dict = {"bbox": None, "text": "hello"}
    curr_span: dict = {
        "bbox": (100.0, 0.0, 100.0 + curr_width, 20.0),
        "text": curr_text,
    }

    result = _should_insert_space(prev_span_no_bbox, curr_span)
    assert result is True, "Missing prev bbox should conservatively insert space"

    # curr_span with no bbox
    prev_span: dict = {"bbox": (0.0, 0.0, 50.0, 20.0), "text": "hello"}
    curr_span_no_bbox: dict = {"bbox": None, "text": curr_text}

    result = _should_insert_space(prev_span, curr_span_no_bbox)
    assert result is True, "Missing curr bbox should conservatively insert space"
