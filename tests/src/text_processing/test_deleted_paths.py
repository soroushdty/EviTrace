"""
tests/text_processing/test_deleted_paths.py
===========================================
Verify ModuleNotFoundError for legacy paths (Property 11).
"""

import importlib
import sys

import pytest


class TestDeletedPaths:
    """Legacy import paths must raise ModuleNotFoundError."""

    def test_utils_text_processor_raises(self):
        """import utils.text_processor raises ModuleNotFoundError."""
        # Ensure it's not cached
        for key in list(sys.modules):
            if "utils.text_processor" in key:
                del sys.modules[key]
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("utils.text_processor")

    def test_pdf_extractor_utils_text_utils_raises(self):
        """import pdf_extractor.utils.text_utils raises ModuleNotFoundError."""
        for key in list(sys.modules):
            if "pdf_extractor.utils.text_utils" in key:
                del sys.modules[key]
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("pdf_extractor.utils.text_utils")

    def test_pdf_extractor_utils_embedding_utils_raises(self):
        """import pdf_extractor.utils.embedding_utils raises ModuleNotFoundError."""
        for key in list(sys.modules):
            if "pdf_extractor.utils.embedding_utils" in key:
                del sys.modules[key]
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("pdf_extractor.utils.embedding_utils")
