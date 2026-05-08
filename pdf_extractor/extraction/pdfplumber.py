"""
pdf_extractor/extraction/tier1/tier1.py
------------------------------
pdfplumber extraction backend.

Migrated from root-level ``tier1.py``.  Return type changed from ``str``
to ``list[BlockDict]``; each page becomes one
:class:`~pdf_extractor.extraction.schemas.BlockDict` via
:func:`~pdf_extractor.extraction.schemas.make_block` with ``block_bbox=None`` and
``spans=[]``.

``[PAGE n]`` and ``[TABLE]`` / ``[/TABLE]`` markers are preserved in the
``text`` field of each block.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .. import schemas

_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "min_words_vertical": 3,
    "min_words_horizontal": 1,
    "intersection_tolerance": 3,
    "text_tolerance": 3,
    "text_x_tolerance": 3,
    "text_y_tolerance": 3,
}

# Fallback for borderless / text-aligned tables (e.g. whitespace-separated columns)
_TABLE_SETTINGS_TEXT = {
    **_TABLE_SETTINGS,
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
}


def _ensure_pdfplumber() -> None:
    """Install pdfplumber if it is not already importable."""
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "pdfplumber"]
        )


def _keep_char(obj: dict, bboxes: list) -> bool:
    """Return False for chars that fall inside any table bounding box."""
    if obj.get("object_type") != "char":
        return True
    cx, cy = obj["x0"], obj["top"]
    return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in bboxes)


def extract_with_pdfplumber(pdf_path: Path) -> list[schemas.BlockDict]:
    """Extract text from a PDF using pdfplumber.

    Each page in the PDF becomes one :class:`~pdf_extractor.extraction.schemas.BlockDict`
    whose ``text`` field contains the page content prefixed with a
    ``[PAGE n]`` marker (1-based).  Tables are wrapped in
    ``[TABLE]`` / ``[/TABLE]`` markers.

    Uses layout-aware extraction (``layout=True``) to preserve spatial
    structure. Table regions are excluded from body text to prevent
    content duplication. Falls back to text-alignment table detection
    when no ruled lines are found.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.

    Returns
    -------
    list[BlockDict]
        One block per page.  ``block_bbox`` is always ``None``; ``spans``
        is always ``[]``.

    Raises
    ------
    RuntimeError
        If extraction fails or if no text is extracted from the PDF.
    """
    blocks: list[schemas.BlockDict] = []

    try:
        _ensure_pdfplumber()
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                # Primary: line-ruled table detection; fallback: text-alignment strategy
                tables = page.find_tables(_TABLE_SETTINGS)
                if not tables:
                    tables = page.find_tables(_TABLE_SETTINGS_TEXT)

                table_bboxes = [t.bbox for t in tables]

                # Exclude table regions from body text to avoid duplicating content
                if table_bboxes:
                    non_table = page.filter(
                        lambda obj, bboxes=table_bboxes: _keep_char(obj, bboxes)
                    )
                    text = non_table.extract_text(
                        layout=True,
                        x_tolerance=3,
                        y_tolerance=3,
                        dedupe_chars=True,
                    ) or ""
                else:
                    text = page.extract_text(
                        layout=True,
                        x_tolerance=3,
                        y_tolerance=3,
                        dedupe_chars=True,
                    ) or ""

                table_blocks: list[str] = []
                for table in tables:
                    data = table.extract()
                    if not data:
                        continue
                    rows = [
                        "\t".join(str(cell or "").strip() for cell in row)
                        for row in data
                    ]
                    table_blocks.append("[TABLE]\n" + "\n".join(rows) + "\n[/TABLE]")

                page_content = f"[PAGE {i}]\n{text}"
                if table_blocks:
                    page_content += "\n" + "\n".join(table_blocks)

                if page_content.strip():
                    blocks.append(
                        schemas.make_block(
                            text=page_content.strip(),
                            page_index=i - 1,  # 0-based
                            block_bbox=None,
                            spans=[],
                        )
                    )

    except Exception as exc:
        raise RuntimeError(
            f"Failed to extract text from {Path(pdf_path).name}: {exc}"
        ) from exc

    if not blocks:
        raise RuntimeError(
            f"No text extracted from {Path(pdf_path).name} — file may be scanned/image-only."
        )

    return blocks
