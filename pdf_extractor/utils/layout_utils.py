"""
evi_trace/utils/layout_utils.py
=====================
Layout-aware utilities for section heading detection and location cross-check.

These functions provide layout-aware utilities used by the embedding and
text-processing pipeline (see ``evi_trace/utils/embedding_utils.py`` and ``evi_trace/utils/text_utils.py``).
"""

import numpy as np


# ---------------------------------------------------------------------------
# 1. SECTION HEADING DETECTION
# ---------------------------------------------------------------------------

def detect_section_heading(page_index: int, font_metadata: list) -> str:
    """
    Detect the nearest preceding section heading for a given page.

    A span is classified as a heading when its font size is at least
    ``median_size + 2.0``, where the median is computed across **all** spans
    in the document.

    The function returns the text of the last heading that appears on a page
    whose index is <= ``page_index`` (i.e. the nearest heading that precedes
    or is on the same page as the match).

    Parameters
    ----------
    page_index : int
        Zero-based index of the page where the matched sentence was found.
    font_metadata : list of dict
        Each element is expected to have at least::

            {'size': float, 'text': str, 'page': int}

        as produced by iterating PyMuPDF span dictionaries.

    Returns
    -------
    str
        Text of the nearest preceding heading, or ``''`` if none is found.
    """
    if not font_metadata:
        return ''

    # Collect all font sizes to compute the median
    all_sizes = [span['size'] for span in font_metadata if 'size' in span]
    if not all_sizes:
        return ''

    median_size = float(np.median(all_sizes))
    heading_threshold = median_size + 2.0

    # Identify all heading spans on pages <= page_index
    preceding_headings = [
        span
        for span in font_metadata
        if span.get('size', 0) >= heading_threshold
        and span.get('page', page_index + 1) <= page_index
    ]

    if not preceding_headings:
        return ''

    # Return the text of the last (nearest) heading
    nearest = preceding_headings[-1]
    return nearest.get('text', '').strip()


# ---------------------------------------------------------------------------
# 2. LOCATION CROSS-CHECK
# ---------------------------------------------------------------------------

def location_cross_check(
    found_page_index: int,
    font_metadata: list,
    claimed_location: str,
) -> tuple:
    """
    Build a human-readable ``found_location`` and determine whether location
    drift occurred relative to ``claimed_location``.

    Location drift **never** invalidates a match — it is purely informational.

    Parameters
    ----------
    found_page_index : int
        Zero-based page index where the sentence was actually found.
    font_metadata : list of dict
        Font span metadata forwarded to ``detect_section_heading``.
    claimed_location : str
        The ``source_location`` value recorded in the spreadsheet row.

    Returns
    -------
    tuple[str, bool]
        ``(found_location, location_drift)`` where:

        - ``found_location`` is a string such as ``"p.3 — Introduction"``
          (when a heading is detected) or ``"p.3"`` (otherwise).
        - ``location_drift`` is ``True`` when ``found_location`` and
          ``claimed_location`` differ (case-insensitive, stripped comparison).
    """
    heading = detect_section_heading(found_page_index, font_metadata)

    if heading:
        found_location = f"p.{found_page_index + 1} — {heading}"
    else:
        found_location = f"p.{found_page_index + 1}"

    location_drift = (
        found_location.strip().lower() != claimed_location.strip().lower()
    )

    return found_location, location_drift
