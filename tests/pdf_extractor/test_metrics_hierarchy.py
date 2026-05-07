"""
tests/test_metrics_hierarchy.py
==============================
Tests for metrics hierarchy helpers in pdf_extractor.processing or utils.
"""

import pytest

from pdf_extractor.processing.sentence_processor import build_metrics_hierarchy


def test_build_metrics_hierarchy_returns_dict():
    data = [{'metric': 'accuracy', 'value': 0.9}, {'metric': 'precision', 'value': 0.8}]
    result = build_metrics_hierarchy(data)
    assert isinstance(result, dict)
