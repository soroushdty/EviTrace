"""Extract clean text from a PDF, preserving page and table structure."""
from pathlib import Path

import pdfplumber


def extract_pdf_text(pdf_path: Path) -> str:
    """
    Extract all text from a PDF into a single string.

    - Pages are separated by [PAGE n] markers so the model can cite locations.
    - Tables are extracted as tab-separated rows wrapped in [TABLE] ... [/TABLE].
    - Raises RuntimeError on any extraction failure.
    """
    pages = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""

                # Extract tables as plain text so tabular results aren't lost
                table_blocks = []
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
                    pages.append(page_content.strip())

    except Exception as exc:
        raise RuntimeError(
            f"Failed to extract text from {pdf_path.name}: {exc}"
        ) from exc

    if not pages:
        raise RuntimeError(f"No text extracted from {pdf_path.name} — file may be scanned/image-only.")

    return "\n\n".join(pages)
