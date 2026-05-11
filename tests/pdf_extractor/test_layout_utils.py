"""
tests/test_layout_utils.py
==========================
Tests for :mod:`pdf_extractor.utils.layout_utils`.

Run with::

    pytest tests/test_layout_utils.py -v
"""

import pytest

from pdf_extractor.layout_utils import detect_section_heading, location_cross_check


# ---------------------------------------------------------------------------
# detect_section_heading — edge / guard cases
# ---------------------------------------------------------------------------

def test_empty_metadata_returns_empty():
    """Empty font_metadata list → ''."""
    assert detect_section_heading(0, []) == ''


def test_no_size_key_returns_empty():
    """Spans missing the 'size' key → no sizes → ''."""
    metadata = [
        {'text': 'body', 'page': 0},
        {'text': 'another', 'page': 0},
    ]
    assert detect_section_heading(0, metadata) == ''


def test_all_spans_below_threshold_returns_empty():
    """All spans are at or below median+2.0 → no heading → ''."""
    # sizes: [10, 10, 11] → median=10, threshold=12; max size=11 < 12
    metadata = [
        {'size': 10, 'text': 'body1', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
        {'size': 11, 'text': 'body3', 'page': 0},
    ]
    assert detect_section_heading(0, metadata) == ''


def test_heading_above_threshold_returned():
    """Span with size >= median+2.0 on page <= page_index is returned."""
    # sizes: [10, 10, 15] → median=10, threshold=12; size=15 qualifies
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
        {'size': 15, 'text': 'Introduction', 'page': 0},
    ]
    assert detect_section_heading(0, metadata) == 'Introduction'


def test_heading_on_future_page_excluded():
    """A heading whose page > page_index must NOT be returned."""
    # sizes: [10, 20] → median=15, threshold=17; size=20 qualifies but page=2 > 0
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 20, 'text': 'Methods', 'page': 2},  # page 2 > page_index 0
    ]
    assert detect_section_heading(0, metadata) == ''


def test_returns_last_qualifying_heading():
    """When multiple headings precede page_index, the LAST one is returned."""
    # sizes: [10, 10, 14, 14] → median=12, threshold=14; both 14-size spans qualify
    metadata = [
        {'size': 10, 'text': 'body1', 'page': 0},
        {'size': 14, 'text': 'Chapter One', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 1},
        {'size': 14, 'text': 'Chapter Two', 'page': 1},
    ]
    assert detect_section_heading(1, metadata) == 'Chapter Two'


def test_heading_exactly_on_page_index_included():
    """A heading on a page equal to page_index (not just strictly before) is included."""
    # sizes: [10, 10, 15] → median=10, threshold=12
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
        {'size': 15, 'text': 'Results', 'page': 3},
    ]
    assert detect_section_heading(3, metadata) == 'Results'


def test_heading_text_is_stripped():
    """Returned heading text has leading/trailing whitespace removed."""
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
        {'size': 15, 'text': '  Trimmed Heading  ', 'page': 0},
    ]
    assert detect_section_heading(0, metadata) == 'Trimmed Heading'


def test_span_without_page_key_uses_default_exclusion():
    """A span missing the 'page' key uses default page_index+1, so it is excluded."""
    # span missing 'page' → span.get('page', page_index+1) = 0+1 = 1 > 0 → excluded
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
        {'size': 20, 'text': 'Ghost Heading'},  # no 'page' key
    ]
    assert detect_section_heading(0, metadata) == ''


# ---------------------------------------------------------------------------
# location_cross_check
# ---------------------------------------------------------------------------

def test_location_with_heading_uses_em_dash_format():
    """When heading is detected, found_location uses 'p.N — heading' with em-dash."""
    # sizes: [10, 10, 15] → median=10, threshold=12; size=15 → heading
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
        {'size': 15, 'text': 'Introduction', 'page': 0},
    ]
    found_location, _ = location_cross_check(0, metadata, 'anything')
    assert found_location == 'p.1 — Introduction'


def test_location_without_heading_uses_page_only_format():
    """When no heading, found_location is 'p.N'."""
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
    ]
    found_location, _ = location_cross_check(2, metadata, 'anything')
    assert found_location == 'p.3'


def test_drift_detected_when_strings_differ():
    """location_drift is True when found_location != claimed_location."""
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
    ]
    _, location_drift = location_cross_check(0, metadata, 'p.5')
    assert location_drift is True


def test_no_drift_when_strings_match_exactly():
    """location_drift is False when found_location == claimed_location (exact)."""
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
    ]
    _, location_drift = location_cross_check(0, metadata, 'p.1')
    assert location_drift is False


def test_no_drift_case_insensitive():
    """location_drift is False when strings match after lower-casing."""
    # sizes: [10, 10, 15] → heading present
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
        {'size': 15, 'text': 'Introduction', 'page': 0},
    ]
    # found_location = 'p.1 — Introduction'; claimed varies only in case
    _, location_drift = location_cross_check(0, metadata, 'P.1 — INTRODUCTION')
    assert location_drift is False


def test_no_drift_strips_whitespace():
    """location_drift is False when strings differ only by surrounding whitespace."""
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 10, 'text': 'body2', 'page': 0},
    ]
    _, location_drift = location_cross_check(0, metadata, '  p.1  ')
    assert location_drift is False


def test_page_index_incremented_by_one_for_display():
    """Page display is 1-based: found_page_index=4 → 'p.5'."""
    metadata = [
        {'size': 10, 'text': 'body', 'page': 0},
        {'size': 11, 'text': 'body2', 'page': 0},
    ]
    found_location, _ = location_cross_check(4, metadata, '')
    assert found_location == 'p.5'
