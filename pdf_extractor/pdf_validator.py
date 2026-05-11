"""
PDF file validation for EviTrace.

Provides a standalone ``validate_pdf`` function that checks a PDF file in a
strict short-circuit order:

1. Magic bytes  (``b"%PDF-"``)
2. File size    (non-zero)
3. Password     (``doc.needs_pass``)
4. Fitz readability (``fitz.open()`` must not raise)

The first failing check raises :class:`PDFValidationError`; subsequent checks
are never performed.

This module is intentionally NOT part of the generic ``Validator`` hierarchy.

Note: ``fitz`` (PyMuPDF) is imported lazily inside ``validate_pdf`` so that
importing this module does not fail in environments where PyMuPDF is not
installed (e.g. during unit tests that mock fitz).
"""

from __future__ import annotations

import os
from pathlib import Path


class PDFValidationError(Exception):
    """Raised when a PDF file fails validation."""


def validate_pdf(path: str | Path) -> None:
    """Validate a PDF file, raising :class:`PDFValidationError` on the first
    failing check.

    Check order (short-circuit — first failure terminates):

    1. **Magic bytes** — first 5 bytes must equal ``b"%PDF-"``.
    2. **File size** — file must be non-zero bytes.
    3. **Password protection** — ``fitz.open()`` must succeed and
       ``doc.needs_pass`` must be ``False``.
    4. **Fitz readability** — ``fitz.open()`` must not raise any exception.

    Parameters
    ----------
    path:
        Path to the PDF file (``str`` or :class:`pathlib.Path`).

    Raises
    ------
    PDFValidationError
        At the first failing check, with a descriptive message identifying
        which check failed.
    """
    path = Path(path)

    # ------------------------------------------------------------------
    # Check 1: Magic bytes
    # ------------------------------------------------------------------
    with open(path, "rb") as fh:
        magic = fh.read(5)

    if magic != b"%PDF-":
        raise PDFValidationError(
            f"Magic bytes check failed for '{path}': "
            f"expected b'%PDF-', got {magic!r}."
        )

    # ------------------------------------------------------------------
    # Check 2: File size
    # ------------------------------------------------------------------
    if os.path.getsize(path) == 0:
        raise PDFValidationError(
            f"File size check failed for '{path}': file is empty (0 bytes)."
        )

    # ------------------------------------------------------------------
    # Checks 3 & 4: Open with fitz, then inspect needs_pass
    # ------------------------------------------------------------------
    import fitz  # noqa: PLC0415 — lazy; not installed in all envs

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise PDFValidationError(
            f"Fitz readability check failed for '{path}': "
            f"fitz.open() raised {type(exc).__name__}: {exc}"
        ) from exc

    # Check 3: Password protection (fitz.open() succeeded but doc is locked)
    if doc.needs_pass:
        raise PDFValidationError(
            f"Password protection check failed for '{path}': "
            "the PDF is password-protected."
        )
