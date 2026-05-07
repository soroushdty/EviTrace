"""
tests/test_parser_pipeline.py
=============================
Basic smoke tests for the parser pipeline.
"""

import pytest

from pdf_extractor.extraction.tier1.tier1 import parse_document


def test_parse_document_returns_dict():
    # Minimal invocation smoke test
    doc = {'pages': []}
    res = parse_document(doc)
    assert isinstance(res, dict)
