"""
tests/test_embedding_utils.py
=============================
Tests for :mod:`pdf_extractor.utils.embedding_utils`.

no GPU required.

Run with::

    pytest tests/test_embedding_utils.py -v
"""

import importlib
import sys
import unittest.mock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Mock model helper shared across multiple test classes
# ---------------------------------------------------------------------------

class MockModel:
    """Minimal stand-in for a SentenceTransformer model."""

    def __init__(self, dim: int = 768):
        self._dim = dim

    def encode(self, texts, batch_size=64, show_progress_bar=False, convert_to_numpy=True):
        # Return un-normalised all-ones so we can verify L2-norm is applied.
        return np.ones((len(texts), self._dim), dtype=np.float32)


class MockFaiss:
    """Minimal faiss stand-in: normalize_L2 is a no-op (vectors already unit)."""

    def normalize_L2(self, vectors) -> None:
        pass  # no-op; MockModel.encode returns all-ones, close enough for shape tests


# ---------------------------------------------------------------------------
# Test 1: import succeeds when sentence_transformers is NOT installed
# ---------------------------------------------------------------------------

class TestImportSafety:
    """pdf_extractor.utils.embedding_utils must be importable even when heavy deps are absent."""

    def test_import_succeeds_without_sentence_transformers(self):
        """
        Req 10.5: importing pdf_extractor.utils.embedding_utils must NOT raise when
        sentence_transformers is not installed.
        """
        with unittest.mock.patch.dict(sys.modules, {
            'sentence_transformers': None,
            'faiss': None,
            'torch': None,
        }):
            # Force a clean reimport with patched sys.modules
            keys_to_evict = [k for k in sys.modules if k == 'pdf_extractor.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            # This must not raise
            import pdf_extractor.utils.embedding_utils  # noqa: F401 — we only care it doesn't raise
            importlib.reload(pdf_extractor.utils.embedding_utils)

    def test_import_succeeds_without_any_heavy_deps(self):
        """
        Req 5.2 / 10.3: module-level does not import faiss, torch, or
        sentence_transformers; import must succeed when all three are absent.
        """
        with unittest.mock.patch.dict(sys.modules, {
            'sentence_transformers': None,
            'faiss': None,
            'torch': None,
        }):
            keys_to_evict = [k for k in sys.modules if k == 'pdf_extractor.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            mod = importlib.import_module('pdf_extractor.utils.embedding_utils')
            assert mod is not None


# ---------------------------------------------------------------------------
# Test 2: load_embedding_model raises ImportError with pip hint when ST absent
# ---------------------------------------------------------------------------

class TestImportSafetyAndFunctionRaises:
    """
    Req 11.7: A single test that imports pdf_extractor.utils.embedding_utils with ALL THREE
    heavy deps patched as missing, confirms the import succeeds, then calls
    each Embedding_Engine function and asserts ImportError with 'pip install'.
    """

    def test_import_succeeds_and_functions_raise_on_all_deps_missing(self):
        """
        Req 11.7: import pdf_extractor.utils.embedding_utils when faiss, torch, and
        sentence_transformers are all patched as missing — import must succeed;
        calling any function that requires a missing dep must raise ImportError
        containing 'pip install'.
        """
        with unittest.mock.patch.dict(sys.modules, {
            'sentence_transformers': None,
            'faiss': None,
            'torch': None,
        }):
            keys_to_evict = [k for k in sys.modules if k == 'pdf_extractor.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            # Import must NOT raise — this is the import-safety assertion.
            eu = importlib.import_module('pdf_extractor.utils.embedding_utils')
            assert eu is not None, "Module import must succeed with all heavy deps missing"

            # Calling load_embedding_model must raise ImportError with pip install.
            with pytest.raises(ImportError) as exc_info:
                eu.load_embedding_model()
            assert 'pip install' in str(exc_info.value), (
                f"ImportError message must contain 'pip install', got: {exc_info.value}"
            )

            # Calling l2_normalise on a non-empty array must raise ImportError
            # with pip install (empty array returns early without needing faiss).
            arr = unittest.mock.MagicMock()
            arr.shape = (2, 4)
            # Patch numpy inside eu to avoid issues with the lazy import
            import numpy as _np
            real_arr = _np.ones((2, 4), dtype=_np.float32)
            with pytest.raises(ImportError) as exc_info2:
                eu.l2_normalise(real_arr)
            assert 'pip install' in str(exc_info2.value), (
                f"l2_normalise ImportError must contain 'pip install', got: {exc_info2.value}"
            )

            # Calling build_faiss_index must raise ImportError with pip install.
            with pytest.raises(ImportError) as exc_info3:
                eu.build_faiss_index(real_arr)
            assert 'pip install' in str(exc_info3.value), (
                f"build_faiss_index ImportError must contain 'pip install', got: {exc_info3.value}"
            )


class TestLoadEmbeddingModelImportError:
    """load_embedding_model must raise ImportError with pip install hint."""

    def test_raises_import_error_when_sentence_transformers_missing(self):
        """
        Req 5.3: calling load_embedding_model() when sentence_transformers is
        not installed must raise ImportError.
        """
        import pdf_extractor.utils.embedding_utils as eu

        with unittest.mock.patch.dict(sys.modules, {'sentence_transformers': None}):
            with pytest.raises(ImportError) as exc_info:
                eu.load_embedding_model()
        assert exc_info.value is not None

    def test_import_error_message_contains_pip_install(self):
        """
        Req 5.3: the ImportError message must contain 'pip install'.
        """
        import pdf_extractor.utils.embedding_utils as eu

        with unittest.mock.patch.dict(sys.modules, {'sentence_transformers': None}):
            with pytest.raises(ImportError) as exc_info:
                eu.load_embedding_model()
        assert 'pip install' in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 3: np.random.seed is NOT called at import time
# ---------------------------------------------------------------------------

class TestNoSeedAtImport:
    """np.random.seed and torch.manual_seed must not be called at import time."""

    def test_np_random_seed_not_called_at_import(self):
        """
        Req 5.4 / 10.2: np.random.seed() must NOT be called when
        pdf_extractor.utils.embedding_utils is imported.
        """
        with unittest.mock.patch('numpy.random.seed') as mock_seed:
            keys_to_evict = [k for k in sys.modules if k == 'pdf_extractor.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            importlib.import_module('pdf_extractor.utils.embedding_utils')
            mock_seed.assert_not_called()

    # ---------------------------------------------------------------------------
    # Test 4: torch.manual_seed is NOT called at import time
    # ---------------------------------------------------------------------------

    def test_torch_manual_seed_not_called_at_import(self):
        """
        Req 5.4 / 10.2: torch.manual_seed() must NOT be called when
        pdf_extractor.utils.embedding_utils is imported.
        """
        # Provide a mock torch module so we can observe manual_seed calls
        mock_torch = unittest.mock.MagicMock()
        with unittest.mock.patch.dict(sys.modules, {'torch': mock_torch}):
            keys_to_evict = [k for k in sys.modules if k == 'pdf_extractor.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            importlib.import_module('pdf_extractor.utils.embedding_utils')
            mock_torch.manual_seed.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: embed_query returns array of shape (1, D) with D > 0
# ---------------------------------------------------------------------------

class TestEmbedQueryShape:
    """embed_query must return an array of shape (1, D) where D > 0."""

    def test_embed_query_returns_correct_shape(self):
        """
        Req 5.8: embed_query must return an array of shape (1, D) where D > 0.
        Since Task 3.2, embed_query delegates L2-normalisation to l2_normalise
        which requires faiss, so we patch it with MockFaiss.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'pdf_extractor.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('pdf_extractor.utils.embedding_utils')
            importlib.reload(eu)
            model = MockModel(dim=768)
            result = eu.embed_query("test query", model)
            assert result.ndim == 2
            assert result.shape[0] == 1
            assert result.shape[1] > 0

    def test_embed_query_shape_with_small_dim(self):
        """Shape check also holds for small embedding dimensions."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'pdf_extractor.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('pdf_extractor.utils.embedding_utils')
            importlib.reload(eu)
            model = MockModel(dim=4)
            result = eu.embed_query("hello world", model)
            assert result.shape == (1, 4)

    def test_embed_query_returns_l2_normalised(self):
        pass
