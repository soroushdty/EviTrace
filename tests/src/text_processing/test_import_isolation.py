"""
tests/text_processing/test_import_isolation.py
==============================================
Verify import without heavy deps (Property 11 partial).
"""

import importlib
import sys
import unittest.mock

import pytest


class TestImportIsolation:
    """Importing text_processing must not pull in heavy dependencies."""

    def test_import_text_processing_without_heavy_deps(self):
        """import text_processing succeeds when faiss/torch/sentence_transformers are absent."""
        with unittest.mock.patch.dict(sys.modules, {
            "sentence_transformers": None,
            "faiss": None,
            "torch": None,
            "spacy": None,
            "scispacy": None,
            "stanza": None,
            "wtpsplit": None,
            "nltk": None,
        }):
            # Evict cached text_processing modules
            keys_to_evict = [k for k in sys.modules if k.startswith("text_processing")]
            for k in keys_to_evict:
                del sys.modules[k]
            mod = importlib.import_module("text_processing")
            assert mod is not None

    def test_import_text_processing_embedding_without_heavy_deps(self):
        """import text_processing.embedding succeeds without heavy deps."""
        with unittest.mock.patch.dict(sys.modules, {
            "sentence_transformers": None,
            "faiss": None,
            "torch": None,
        }):
            keys_to_evict = [k for k in sys.modules if "text_processing.embedding" in k]
            for k in keys_to_evict:
                del sys.modules[k]
            mod = importlib.import_module("text_processing.embedding")
            assert mod is not None

    def test_import_text_processing_matchers_without_heavy_deps(self):
        """import text_processing.matchers succeeds without heavy deps."""
        with unittest.mock.patch.dict(sys.modules, {
            "faiss": None,
            "torch": None,
        }):
            keys_to_evict = [k for k in sys.modules if "text_processing.matchers" in k]
            for k in keys_to_evict:
                del sys.modules[k]
            mod = importlib.import_module("text_processing.matchers")
            assert mod is not None

    def test_heavy_deps_not_in_sys_modules_after_import(self):
        """After importing text_processing, heavy deps should not appear in sys.modules."""
        heavy = {"sentence_transformers", "faiss", "torch"}
        # Record what's already there
        before = set(sys.modules.keys())
        import text_processing  # noqa: F401
        after = set(sys.modules.keys())
        new_modules = after - before
        leaked = heavy & new_modules
        assert not leaked, f"Heavy deps leaked into sys.modules on import: {leaked}"
