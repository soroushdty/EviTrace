"""
tests/test_text_extractor_tier2.py
------------------------------------
Placeholder — Tesseract backend removed as of architecture-migration task 1.1.

The Tesseract OCR backend (``pdf_extractor.extraction.Tesseract``) has been
deleted from the codebase. Scanned pages are now routed to PaddleOCR (tier 3).
Tests for PaddleOCR will be added in a subsequent task.
"""

import pytest

pytestmark = pytest.mark.slow
