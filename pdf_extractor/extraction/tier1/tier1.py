"""
evi_trace/extraction/tier1/tier1.py
------------------------------
pdfplumber extraction backend.

Migrated from root-level ``tier1.py``.  Return type changed from ``str``
to ``list[BlockDict]``; each page becomes one
:class:`~evi_trace.extraction.schemas.BlockDict` via
:func:`~evi_trace.extraction.schemas.make_block` with ``block_bbox=None`` and
``spans=[]``.

``[PAGE n]`` and ``[TABLE]`` / ``[/TABLE]`` markers are preserved in the
``text`` field of each block.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .. import schemas


def _ensure_pdfplumber() -> None:
    """Install pdfplumber if it is not already importable."""
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "pdfplumber"]
        )


def extract_with_pdfplumber(pdf_path: Path) -> list[schemas.BlockDict]:
    """Extract text from a PDF using pdfplumber.

    Each page in the PDF becomes one :class:`~evi_trace.extraction.schemas.BlockDict`
    whose ``text`` field contains the page content prefixed with a
    ``[PAGE n]`` marker (1-based).  Tables are wrapped in
    ``[TABLE]`` / ``[/TABLE]`` markers.

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
                text = page.extract_text() or ""

                # Extract tables as plain text so tabular results aren't lost.
                table_blocks: list[str] = []
                for table in page.extract_tables() or []:
                    if not table:
                        continue
                    rows = [
                        "\t".join(str(cell or "").strip() for cell in row)
                        for row in table
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
