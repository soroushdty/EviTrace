"""
pdf_extractor/extraction/schemas.py
-------------------------
Canonical output types and factory/validation helpers for all extraction
backends.

No imports outside the Python standard library; no import-time side effects.
"""

from __future__ import annotations

from typing import TypedDict


# ---------------------------------------------------------------------------
# TypedDict definitions
# ---------------------------------------------------------------------------

class SpanDict(TypedDict):
    text: str
    font: str
    size: float
    flags: int
    color: int
    bbox: tuple


class BlockDict(TypedDict):
    text: str
    page_index: int
    block_bbox: tuple | None
    spans: list  # list[SpanDict]


class FontMetaDict(TypedDict):
    size: float
    text: str
    page: int


class PaddleOCRBlockDict(BlockDict, total=False):
    """Extended block type carrying PaddleOCR-specific metadata.

    Inherits all required fields from :class:`BlockDict` (``text``,
    ``page_index``, ``block_bbox``, ``spans``) and adds two optional fields:

    * ``rasterization_dpi`` — the DPI used to rasterize the page image before
      OCR; needed for pixel → PDF user-space coordinate conversion.
    * ``ocr_confidence`` — the confidence score returned by PaddleOCR for this
      block, in the range ``[0.0, 1.0]``.

    Requirements: 3.2
    """

    rasterization_dpi: int
    ocr_confidence: float


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def make_block(
    text: str,
    page_index: int,
    block_bbox: tuple | None,
    spans: list,
) -> BlockDict:
    """Construct a :class:`BlockDict` from the supplied arguments.

    Parameters
    ----------
    text:
        Concatenated text content of the block.
    page_index:
        0-based page number.
    block_bbox:
        Bounding box ``(x0, y0, x1, y1)`` or ``None`` for OCR backends.
    spans:
        Per-span detail; ``[]`` for OCR backends.

    Returns
    -------
    BlockDict
    """
    return BlockDict(
        text=text,
        page_index=page_index,
        block_bbox=block_bbox,
        spans=spans,
    )


def make_ocr_block(
    text: str,
    page_index: int,
    block_bbox: tuple | None,
    rasterization_dpi: int,
    ocr_confidence: float,
) -> "PaddleOCRBlockDict":
    """Construct a :class:`PaddleOCRBlockDict` from the supplied arguments.

    Parameters
    ----------
    text:
        Concatenated OCR text content of the block.
    page_index:
        0-based page number.
    block_bbox:
        Bounding box ``(x0, y0, x1, y1)`` in PDF user-space points, or
        ``None`` when unavailable.
    rasterization_dpi:
        DPI used to rasterize the page image before OCR.
    ocr_confidence:
        Confidence score returned by PaddleOCR, in ``[0.0, 1.0]``.

    Returns
    -------
    PaddleOCRBlockDict
    """
    return PaddleOCRBlockDict(
        text=text,
        page_index=page_index,
        block_bbox=block_bbox,
        spans=[],
        rasterization_dpi=rasterization_dpi,
        ocr_confidence=ocr_confidence,
    )


def make_font_meta(size: float, text: str, page: int) -> FontMetaDict:
    """Construct a :class:`FontMetaDict` from the supplied arguments.

    Parameters
    ----------
    size:
        Font size in points.
    text:
        Span text.
    page:
        0-based page number.

    Returns
    -------
    FontMetaDict
    """
    return FontMetaDict(size=size, text=text, page=page)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = ("text", "page_index", "block_bbox", "spans")
_TYPE_CHECKS: dict[str, type] = {
    "text": str,
    "page_index": int,
    "spans": list,
}


def validate_blocks(blocks: list) -> None:
    """Validate that every element of *blocks* conforms to :class:`BlockDict`.

    Checks performed for each block:

    * All four required keys are present: ``text``, ``page_index``,
      ``block_bbox``, ``spans``.
    * ``text`` is a :class:`str`.
    * ``page_index`` is an :class:`int` (``bool`` is excluded because
      ``bool`` is a subclass of ``int`` in Python but is not a valid
      page index).
    * ``spans`` is a :class:`list`.

    Parameters
    ----------
    blocks:
        List of dicts to validate.

    Raises
    ------
    ValueError
        If any block is missing a required key or has a wrong-typed field.
        The message identifies the offending block index and field.
    """
    for i, block in enumerate(blocks):
        # Check all required keys are present.
        for key in _REQUIRED_KEYS:
            if key not in block:
                raise ValueError(
                    f"Block at index {i} is missing required key '{key}'."
                )

        # Check types for the three typed fields.
        for field, expected_type in _TYPE_CHECKS.items():
            value = block[field]
            # Exclude bool from int check: bool is a subclass of int but
            # is not a valid page_index value.
            if field == "page_index" and isinstance(value, bool):
                raise ValueError(
                    f"Block at index {i}: field 'page_index' must be int, "
                    f"got {type(value).__name__}."
                )
            if not isinstance(value, expected_type):
                raise ValueError(
                    f"Block at index {i}: field '{field}' must be "
                    f"{expected_type.__name__}, got {type(value).__name__}."
                )
