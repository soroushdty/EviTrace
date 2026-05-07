"""
evi_trace/extraction/core/branch2.py
-------------------------------
PyMuPDF extraction backend.

Migrated verbatim from ``evi_trace.extraction.py::extract_with_pymupdf``.
Block and font-meta construction uses :mod:`evi_trace.extraction.schemas`
factory functions instead of raw dict literals.

``fitz`` is imported lazily inside the function body — no import-time
side effects.
"""

from .. import schemas


def extract_with_pymupdf(pdf_path: str) -> tuple:
    """Extract text and font metadata from a PDF using PyMuPDF (fitz).

    Opens *pdf_path* with ``fitz.open``, iterates over every page, and
    collects two data structures:

    * **blocks** – one :class:`~evi_trace.extraction.schemas.BlockDict` per
      block.  The text of a block is formed by joining the text of all
      spans that belong to that block.
    * **font_metadata** – one :class:`~evi_trace.extraction.schemas.FontMetaDict`
      per span across the whole document.

    Parameters
    ----------
    pdf_path:
        Absolute or relative path to the PDF file.

    Returns
    -------
    tuple
        ``(blocks, font_metadata)`` where *blocks* is a
        ``list[BlockDict]`` and *font_metadata* is a
        ``list[FontMetaDict]``.
    """
    import fitz  # PyMuPDF — lazy import, no import-time side effect

    blocks: list[schemas.BlockDict] = []
    font_metadata: list[schemas.FontMetaDict] = []

    doc = fitz.open(pdf_path)
    try:
        for page_index, page in enumerate(doc):
            page_dict = page.get_text("dict")

            for block in page_dict.get("blocks", []):
                # Only text blocks carry a 'lines' key (image blocks do not).
                if block.get("type") != 0:
                    continue

                block_spans_text: list[str] = []
                block_spans: list[schemas.SpanDict] = []

                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        span_text: str = span.get("text", "")
                        span_size: float = span.get("size", 0.0)

                        # Accumulate span text for the parent block.
                        block_spans_text.append(span_text)
                        block_spans.append(
                            schemas.SpanDict(
                                text=span_text,
                                font=span.get("font", ""),
                                size=span_size,
                                flags=span.get("flags", 0),
                                color=span.get("color", 0),
                                bbox=tuple(span.get("bbox", ())),
                            )
                        )

                        # Record per-span font metadata.
                        font_metadata.append(
                            schemas.make_font_meta(
                                size=span_size,
                                text=span_text,
                                page=page_index,
                            )
                        )

                joined_block_text = "".join(block_spans_text).strip()
                if joined_block_text:
                    blocks.append(
                        schemas.make_block(
                            text=joined_block_text,
                            page_index=page_index,
                            block_bbox=tuple(block.get("bbox", ())),
                            spans=block_spans,
                        )
                    )
    finally:
        doc.close()

    return blocks, font_metadata
