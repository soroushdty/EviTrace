"""Minimal pdf2image shim for tests when pdf2image is not installed.
Provides `pdfinfo_from_path` and `convert_from_path` signatures used by
`pdf_extractor.extraction.PaddleOCR`. Tests patch these functions as needed.
"""
from typing import List, Dict, Any


def pdfinfo_from_path(path: str) -> Dict[str, Any]:
    # Default fallback: assume single-page PDF
    return {"Pages": 1}


def convert_from_path(path: str, first_page: int = None, last_page: int = None, dpi: int = 200, **kwargs) -> List[Any]:
    # Return empty list by default; tests patch this to return a mock PIL image.
    return []
