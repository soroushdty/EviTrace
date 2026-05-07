"""
tests/test_text_extractor_schemas.py
-------------------------------------
Property-based tests for ``pdf_extractor.extraction.schemas``.

Properties covered:
  1. make_block is a faithful constructor
  2. make_font_meta is a faithful constructor
  3. validate_blocks accepts all valid BlockDict lists
  4. validate_blocks rejects any block with a missing or wrong-typed field
"""

import pytest
from hypothesis import given, settings, strategies as st

from pdf_extractor.extraction import schemas


# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Strategy for block_bbox: either None or a 4-tuple of finite floats.
_bbox_strategy = st.one_of(
    st.none(),
    st.tuples(
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    ),
)

# Strategy for a single SpanDict-shaped dict.
_span_strategy = st.fixed_dictionaries(
    {
        "text": st.text(),
        "bbox": st.tuples(
            st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
            st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
            st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
            st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        ),
        "size": st.floats(min_value=0.0, allow_nan=False, allow_infinity=False),
    }
)

# Strategy for a list of spans.
_spans_strategy = st.lists(_span_strategy, max_size=10)

# Strategy for a well-formed BlockDict-shaped dict.
_valid_block_strategy = st.fixed_dictionaries(
    {
        "text": st.text(),
        "page_index": st.integers(min_value=0),
        "block_bbox": _bbox_strategy,
        "spans": _spans_strategy,
    }
)


# ---------------------------------------------------------------------------
# Property 1: make_block is a faithful constructor
# Feature: text-extractor-restructure, Property 1: make_block is a faithful constructor
# Validates: Requirements 13.4
# ---------------------------------------------------------------------------

@given(
    text=st.text(),
    page_index=st.integers(min_value=0),
    block_bbox=_bbox_strategy,
    spans=_spans_strategy,
)
@settings(max_examples=100)
def test_make_block_faithful_constructor(text, page_index, block_bbox, spans):
    # Feature: text-extractor-restructure, Property 1: make_block is a faithful constructor
    result = schemas.make_block(
        text=text,
        page_index=page_index,
        block_bbox=block_bbox,
        spans=spans,
    )
    assert result["text"] == text
    assert result["page_index"] == page_index
    assert result["block_bbox"] == block_bbox
    assert result["spans"] == spans


# ---------------------------------------------------------------------------
# Property 2: make_font_meta is a faithful constructor
# Feature: text-extractor-restructure, Property 2: make_font_meta is a faithful constructor
# Validates: Requirements 13.5
# ---------------------------------------------------------------------------

@given(
    size=st.floats(min_value=0.0, allow_nan=False, allow_infinity=False),
    text=st.text(),
    page=st.integers(min_value=0),
)
@settings(max_examples=100)
def test_make_font_meta_faithful_constructor(size, text, page):
    # Feature: text-extractor-restructure, Property 2: make_font_meta is a faithful constructor
    result = schemas.make_font_meta(size=size, text=text, page=page)
    assert result["size"] == size
    assert result["text"] == text
    assert result["page"] == page


# ---------------------------------------------------------------------------
# Property 3: validate_blocks accepts all valid BlockDict lists
# Feature: text-extractor-restructure, Property 3: validate_blocks accepts all valid BlockDict lists
# Validates: Requirements 13.11
# ---------------------------------------------------------------------------

@given(blocks=st.lists(_valid_block_strategy, max_size=20))
@settings(max_examples=100)
def test_validate_blocks_accepts_valid_lists(blocks):
    # Feature: text-extractor-restructure, Property 3: validate_blocks accepts all valid BlockDict lists
    result = schemas.validate_blocks(blocks)
    assert result is None


# ---------------------------------------------------------------------------
# Property 4: validate_blocks rejects any block with a missing or wrong-typed field
# Feature: text-extractor-restructure, Property 4: validate_blocks rejects any block with a missing or wrong-typed field
# Validates: Requirements 13.6, 13.7, 13.8, 13.9, 13.10
# ---------------------------------------------------------------------------

# Strategy for a non-str value (for the 'text' field).
_non_str = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
    st.lists(st.text(), max_size=3),
)

# Strategy for a non-int value (for the 'page_index' field).
# Note: bool is a subclass of int in Python, so we must include it as invalid.
_non_int = st.one_of(
    st.text(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
    st.lists(st.integers(), max_size=3),
)

# Strategy for a non-list value (for the 'spans' field).
_non_list = st.one_of(
    st.text(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
    st.tuples(st.text()),
)

# Strategy for an invalid block: one of the four required keys is missing,
# or one of the typed fields has the wrong type.
_invalid_block_strategy = st.one_of(
    # Missing 'text' key
    st.fixed_dictionaries(
        {"page_index": st.integers(min_value=0), "block_bbox": _bbox_strategy, "spans": _spans_strategy}
    ),
    # Missing 'page_index' key
    st.fixed_dictionaries(
        {"text": st.text(), "block_bbox": _bbox_strategy, "spans": _spans_strategy}
    ),
    # Missing 'block_bbox' key
    st.fixed_dictionaries(
        {"text": st.text(), "page_index": st.integers(min_value=0), "spans": _spans_strategy}
    ),
    # Missing 'spans' key
    st.fixed_dictionaries(
        {"text": st.text(), "page_index": st.integers(min_value=0), "block_bbox": _bbox_strategy}
    ),
    # Wrong type for 'text'
    st.fixed_dictionaries(
        {
            "text": _non_str,
            "page_index": st.integers(min_value=0),
            "block_bbox": _bbox_strategy,
            "spans": _spans_strategy,
        }
    ),
    # Wrong type for 'page_index' (including bool)
    st.fixed_dictionaries(
        {
            "text": st.text(),
            "page_index": _non_int,
            "block_bbox": _bbox_strategy,
            "spans": _spans_strategy,
        }
    ),
    # Wrong type for 'spans'
    st.fixed_dictionaries(
        {
            "text": st.text(),
            "page_index": st.integers(min_value=0),
            "block_bbox": _bbox_strategy,
            "spans": _non_list,
        }
    ),
)


@given(
    valid_prefix=st.lists(_valid_block_strategy, max_size=5),
    invalid_block=_invalid_block_strategy,
    valid_suffix=st.lists(_valid_block_strategy, max_size=5),
)
@settings(max_examples=100)
def test_validate_blocks_rejects_invalid_blocks(valid_prefix, invalid_block, valid_suffix):
    # Feature: text-extractor-restructure, Property 4: validate_blocks rejects any block with a missing or wrong-typed field
    blocks = valid_prefix + [invalid_block] + valid_suffix
    with pytest.raises(ValueError):
        schemas.validate_blocks(blocks)
