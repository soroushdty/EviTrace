"""
pdf_extractor/extraction/__init__.py
-------------------------------------
Re-exports for the extraction package.

Public API
----------
- schemas          — block/font-metadata schema helpers
- PyMuPDF          — PyMuPDF backend module
- extract_with_pymupdf    — PyMuPDF extraction function (re-exported for patch target resolution)
- extract_with_pdfplumber — pdfplumber extraction function
- extract_with_paddleocr  — PaddleOCR extraction function
- scan_detector    — per-page scan classification helpers
"""

from __future__ import annotations

from . import schemas
from . import PyMuPDF
from .PyMuPDF import extract_with_pymupdf          # re-export for patch target resolution
from .pdfplumber import extract_with_pdfplumber
from .PaddleOCR import extract_with_paddleocr
from . import scan_detector
