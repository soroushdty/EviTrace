"""
tests/pdf_extractor/test_pymupdf_schema.py
-------------------------------------------
Property-based tests for ``pdf_extractor.extraction.PyMuPDF`` (PyMuPDF backend).

Properties covered:
  - PyMuPDF backend output conforms to BlockDict schema
"""

import sys
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, strategies as st, HealthCheck

from pdf_extractor.extraction import schemas
from pdf_extractor.extraction import PyMuPDF as pymupdf_backend


# ---------------------------------------------------------------------------
# Strategies for generating mock fitz document data
# ---------------------------------------------------------------------------

_finite_float = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)

_span_strategy = st.fixed_dictionaries(
    {
        "text": st.text(min_size=1),  # non-empty so blocks are kept
        "size": st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        "bbox": st.tuples(_finite_float, _finite_float, _finite_float, _finite_float),
    }
)

_line_strategy = st.fixed_dictionaries(
    {"spans": st.lists(_span_strategy, min_size=1, max_size=5)}
)

_block_strategy = st.fixed_dictionaries(
    {
        "type": st.just(0),  # text block
        "bbox": st.tuples(_finite_float, _finite_float, _finite_float, _finite_float),
        "lines": st.lists(_line_strategy, min_size=1, max_size=5),
    }
)

_page_strategy = st.fixed_dictionaries(
    {"blocks": st.lists(_block_strategy, min_size=1, max_size=5)}
)


def _build_mock_fitz(pages_data: list[dict]) -> MagicMock:
    """Build a mock fitz module from a list of page dicts."""
    mock_fitz = MagicMock()

    mock_pages = []
    for page_data in pages_data:
        mock_page = MagicMock()
        mock_page.get_text.return_value = page_data
        mock_pages.append(mock_page)

    mock_doc = MagicMock()
    # The code does: for page_index, page in enumerate(doc)
    # So doc must be iterable and yield page objects directly.
    mock_doc.__iter__ = MagicMock(return_value=iter(mock_pages))
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_doc.close = MagicMock()

    mock_fitz.open.return_value = mock_doc

    return mock_fitz


# ---------------------------------------------------------------------------
# PyMuPDF backend output conforms to BlockDict schema
# ---------------------------------------------------------------------------

@given(pages_data=st.lists(_page_strategy, min_size=1, max_size=5))
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
def test_pymupdf_output_conforms_to_blockdict_schema(pages_data):
    mock_fitz = _build_mock_fitz(pages_data)

    with patch.dict(sys.modules, {"fitz": mock_fitz}):
        blocks, font_metadata = pymupdf_backend.extract_with_pymupdf("fake_path.pdf")

    # Every block must pass validate_blocks without raising.
    schemas.validate_blocks(blocks)

    # Verify structural invariants for each block.
    for block in blocks:
        assert isinstance(block["text"], str)
        assert isinstance(block["page_index"], int)
        assert isinstance(block["spans"], list)
        # block_bbox is a tuple (from PyMuPDF) — not None for this backend.
        assert isinstance(block["block_bbox"], tuple)
