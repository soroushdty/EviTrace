"""
tests/test_embedding_evi_trace.utils.py
=============================
Tests for :mod:`evi_trace.utils.embedding_utils`.
"""
test_embedding_utils.py
=======================
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


# ---------------------------------------------------------------------------
# Test 1: import succeeds when sentence_transformers is NOT installed
# ---------------------------------------------------------------------------

class TestImportSafety:
    """evi_trace.utils.embedding_utils must be importable even when heavy deps are absent."""

    def test_import_succeeds_without_sentence_transformers(self):
        """
        Req 10.5: importing evi_trace.utils.embedding_utils must NOT raise when
        sentence_transformers is not installed.
        """
        with unittest.mock.patch.dict(sys.modules, {
            'sentence_transformers': None,
            'faiss': None,
            'torch': None,
        }):
            # Force a clean reimport with patched sys.modules
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            # This must not raise
            import evi_trace.utils.embedding_utils  # noqa: F401 — we only care it doesn't raise
            importlib.reload(evi_trace.utils.embedding_utils)

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
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            mod = importlib.import_module('evi_trace.utils.embedding_utils')
            assert mod is not None


# ---------------------------------------------------------------------------
# Test 2: load_embedding_model raises ImportError with pip hint when ST absent
# ---------------------------------------------------------------------------

class TestImportSafetyAndFunctionRaises:
    """
    Req 11.7: A single test that imports evi_trace.utils.embedding_utils with ALL THREE
    heavy deps patched as missing, confirms the import succeeds, then calls
    each Embedding_Engine function and asserts ImportError with 'pip install'.
    """

    def test_import_succeeds_and_functions_raise_on_all_deps_missing(self):
        """
        Req 11.7: import evi_trace.utils.embedding_utils when faiss, torch, and
        sentence_transformers are all patched as missing — import must succeed;
        calling any function that requires a missing dep must raise ImportError
        containing 'pip install'.
        """
        with unittest.mock.patch.dict(sys.modules, {
            'sentence_transformers': None,
            'faiss': None,
            'torch': None,
        }):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            # Import must NOT raise — this is the import-safety assertion.
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
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
        import evi_trace.utils.embedding_utils as eu

        with unittest.mock.patch.dict(sys.modules, {'sentence_transformers': None}):
            with pytest.raises(ImportError) as exc_info:
                eu.load_embedding_model()
        assert exc_info.value is not None

    def test_import_error_message_contains_pip_install(self):
        """
        Req 5.3: the ImportError message must contain 'pip install'.
        """
        import evi_trace.utils.embedding_utils as eu

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
        evi_trace.utils.embedding_utils is imported.
        """
        with unittest.mock.patch('numpy.random.seed') as mock_seed:
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            importlib.import_module('evi_trace.utils.embedding_utils')
            mock_seed.assert_not_called()

    # ---------------------------------------------------------------------------
    # Test 4: torch.manual_seed is NOT called at import time
    # ---------------------------------------------------------------------------

    def test_torch_manual_seed_not_called_at_import(self):
        """
        Req 5.4 / 10.2: torch.manual_seed() must NOT be called when
        evi_trace.utils.embedding_utils is imported.
        """
        # Provide a mock torch module so we can observe manual_seed calls
        mock_torch = unittest.mock.MagicMock()
        with unittest.mock.patch.dict(sys.modules, {'torch': mock_torch}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            importlib.import_module('evi_trace.utils.embedding_utils')
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
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
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
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)
            model = MockModel(dim=4)
            result = eu.embed_query("hello world", model)
            assert result.shape == (1, 4)


# ---------------------------------------------------------------------------
# Test 6: embed_query with empty-string prefix does NOT prepend anything
# ---------------------------------------------------------------------------

class TestEmbedQueryPrefix:
    """embed_query prefix handling."""

    def test_empty_prefix_does_not_prepend(self):
        """
        Req 5.7: when query_prefix='' (empty string), embed_query must NOT
        prepend anything to the query text.
        Since Task 3.2, embed_query delegates to l2_normalise which needs faiss.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            captured_texts: list = []

            class CapturingModel:
                def encode(self, texts, convert_to_numpy=True):
                    captured_texts.extend(texts)
                    return np.ones((len(texts), 768), dtype=np.float32)

            model = CapturingModel()
            eu.embed_query("my query", model, query_prefix="")
            assert len(captured_texts) == 1
            assert captured_texts[0] == "my query"

    # ---------------------------------------------------------------------------
    # Test 7: embed_query with non-empty prefix DOES prepend it
    # ---------------------------------------------------------------------------

    def test_non_empty_prefix_prepended(self):
        """
        Req 5.7: when query_prefix is non-empty, embed_query must prepend it
        to the query text before encoding.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            captured_texts: list = []

            class CapturingModel:
                def encode(self, texts, convert_to_numpy=True):
                    captured_texts.extend(texts)
                    return np.ones((len(texts), 768), dtype=np.float32)

            model = CapturingModel()
            eu.embed_query("my query", model, query_prefix="PREFIX: ")
            assert len(captured_texts) == 1
            assert captured_texts[0] == "PREFIX: my query"

    def test_default_prefix_prepended(self):
        """Default BGE prefix is prepended when no explicit prefix is given."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            captured_texts: list = []

            class CapturingModel:
                def encode(self, texts, convert_to_numpy=True):
                    captured_texts.extend(texts)
                    return np.ones((len(texts), 768), dtype=np.float32)

            model = CapturingModel()
            eu.embed_query("test", model)
            assert len(captured_texts) == 1
            assert captured_texts[0].startswith(eu._BGE_QUERY_PREFIX)
            assert captured_texts[0].endswith("test")


# ---------------------------------------------------------------------------
# Test 8: embed_query returns L2-normalised output (norm ≈ 1.0)
# ---------------------------------------------------------------------------

class TestEmbedQueryNormalisation:
    """embed_query must return L2-normalised embeddings."""

    def test_output_is_l2_normalised(self):
        """
        Req 5.8: embed_query must return an L2-normalised array.
        The L2 norm of the single row must be ≈ 1.0.
        Since Task 3.2, embed_query delegates to l2_normalise which needs faiss.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            import evi_trace.utils.embedding_utils as eu  # noqa: F811
            model = MockModel(dim=768)
            result = eu.embed_query("test query", model)
            norm = float(np.linalg.norm(result[0]))
            assert abs(norm - 1.0) < 1e-5, f"Expected L2 norm ≈ 1.0, got {norm}"

    def test_output_is_l2_normalised_non_uniform(self):
        """L2 normalisation also works for non-uniform embeddings."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            import evi_trace.utils.embedding_utils as eu  # noqa: F811

            class NonUniformModel:
                def encode(self, texts, convert_to_numpy=True):
                    # Return a vector with varying values
                    return np.array([[1.0, 2.0, 3.0, 4.0]], dtype=np.float32)

            model = NonUniformModel()
            result = eu.embed_query("test", model, query_prefix="")
            norm = float(np.linalg.norm(result[0]))
            assert abs(norm - 1.0) < 1e-5, f"Expected L2 norm ≈ 1.0, got {norm}"

    def test_output_dtype_is_float32(self):
        """Output array should be float32 (as produced by BGE models)."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            import evi_trace.utils.embedding_utils as eu  # noqa: F811
            model = MockModel(dim=768)
            result = eu.embed_query("test", model)
            assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# Module-level constants sanity checks
# ---------------------------------------------------------------------------

class TestModuleConstants:
    """Verify module-level constants have expected values."""

    def test_bge_model_name_constant(self):
        import evi_trace.utils.embedding_utils as eu
        assert eu._BGE_MODEL_NAME == "BAAI/bge-base-en-v1.5"

    def test_bge_query_prefix_constant(self):
        import evi_trace.utils.embedding_utils as eu
        expected = "Represent this sentence for searching relevant passages: "
        assert eu._BGE_QUERY_PREFIX == expected

    def test_embedding_dim_constant(self):
        import evi_trace.utils.embedding_utils as eu
        assert eu._EMBEDDING_DIM == 768

    def test_max_sentences_constant(self):
        import evi_trace.utils.embedding_utils as eu
        assert eu._MAX_SENTENCES == 10_000

    def test_encode_batch_size_constant(self):
        import evi_trace.utils.embedding_utils as eu
        assert eu._ENCODE_BATCH_SIZE == 64


# ---------------------------------------------------------------------------
# load_embedding_model: successful load path (mocked SentenceTransformer)
# ---------------------------------------------------------------------------

class TestLoadEmbeddingModelSuccess:
    """load_embedding_model with mocked SentenceTransformer."""

    def test_load_model_returns_model_object(self):
        """
        When sentence_transformers IS available, load_embedding_model returns
        the object produced by SentenceTransformer(model_name).
        """
        import evi_trace.utils.embedding_utils as eu

        mock_st_class = unittest.mock.MagicMock()
        mock_model_instance = unittest.mock.MagicMock()
        mock_st_class.return_value = mock_model_instance
        mock_st_module = unittest.mock.MagicMock()
        mock_st_module.SentenceTransformer = mock_st_class

        with unittest.mock.patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            result = eu.load_embedding_model()

        assert result is mock_model_instance

    def test_load_model_uses_default_model_name(self):
        """
        load_embedding_model() with no arguments passes _BGE_MODEL_NAME to
        SentenceTransformer.
        """
        import evi_trace.utils.embedding_utils as eu

        mock_st_class = unittest.mock.MagicMock()
        mock_st_module = unittest.mock.MagicMock()
        mock_st_module.SentenceTransformer = mock_st_class

        with unittest.mock.patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            eu.load_embedding_model()

        mock_st_class.assert_called_once_with(eu._BGE_MODEL_NAME)

    def test_load_model_uses_custom_model_name(self):
        """
        load_embedding_model('custom/model') passes the custom name to
        SentenceTransformer.
        """
        import evi_trace.utils.embedding_utils as eu

        mock_st_class = unittest.mock.MagicMock()
        mock_st_module = unittest.mock.MagicMock()
        mock_st_module.SentenceTransformer = mock_st_class

        with unittest.mock.patch.dict(sys.modules, {'sentence_transformers': mock_st_module}):
            eu.load_embedding_model('my/custom-model')

        mock_st_class.assert_called_once_with('my/custom-model')


# ---------------------------------------------------------------------------
# Task 3.2 Tests: l2_normalise
# ---------------------------------------------------------------------------

class MockFaiss:
    """Minimal stand-in for the faiss module used in l2_normalise tests."""

    def normalize_L2(self, x):
        """Actually perform L2 normalisation so we can verify the result."""
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        x[:] = x / norms

    def IndexFlatIP(self, d):
        return MockFaissIndex(d)

    def get_num_gpus(self):
        return 0


class MockFaissIndex:
    """Minimal stand-in for a faiss.IndexFlatIP index."""

    def __init__(self, d):
        self.d = d
        self.added_vectors = None
        self.ntotal = 0

    def add(self, vectors):
        self.added_vectors = vectors.copy()
        self.ntotal = vectors.shape[0]


class TestL2Normalise:
    """Tests for l2_normalise (Task 3.2, Req 5.1, 5.2, 5.3)."""

    def test_zero_rows_returns_input_unchanged(self):
        """
        Req 5.1: l2_normalise with a zero-row array must return the input
        array unchanged (no faiss call needed, early return).
        """
        from evi_trace.utils.embedding_utils import l2_normalise
        arr = np.zeros((0, 768), dtype=np.float32)
        result = l2_normalise(arr)
        assert result.shape == (0, 768)
        assert result is arr  # same object returned

    def test_absent_faiss_raises_import_error(self):
        """
        Req 5.3 / 10.3: when faiss is not installed, l2_normalise must raise
        ImportError with 'pip install' in the message.
        """
        # Reload the module with faiss patched as absent
        with unittest.mock.patch.dict(sys.modules, {'faiss': None}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            from evi_trace.utils.embedding_utils import l2_normalise as l2n
            arr = np.ones((2, 4), dtype=np.float32)
            with pytest.raises(ImportError, match="pip install"):
                l2n(arr)

    def test_normalised_rows_have_unit_norm(self):
        """
        Req 5.1 / 5.8: l2_normalise must produce rows with L2 norm ≈ 1.0
        when applied to a non-zero array.  Uses MockFaiss to avoid GPU deps.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            # Evict cached module so it picks up the mock
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            from evi_trace.utils.embedding_utils import l2_normalise

            arr = np.array([[3.0, 4.0], [1.0, 0.0], [0.5, 0.5]], dtype=np.float32)
            result = l2_normalise(arr)
            for i in range(result.shape[0]):
                norm = float(np.linalg.norm(result[i]))
                assert abs(norm - 1.0) < 1e-5, (
                    f"Row {i} has norm {norm}, expected ≈ 1.0"
                )

    def test_import_error_message_names_faiss_cpu(self):
        """
        Req 5.3: ImportError message must contain 'faiss-cpu' as install hint.
        """
        with unittest.mock.patch.dict(sys.modules, {'faiss': None}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            from evi_trace.utils.embedding_utils import l2_normalise as l2n
            arr = np.ones((1, 4), dtype=np.float32)
            with pytest.raises(ImportError, match="faiss-cpu"):
                l2n(arr)

    def test_output_dtype_is_float32(self):
        """l2_normalise must return a float32 array."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            from evi_trace.utils.embedding_utils import l2_normalise

            arr = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
            result = l2_normalise(arr)
            assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# Task 3.2 Tests: build_faiss_index
# ---------------------------------------------------------------------------

class TestBuildFaissIndex:
    """Tests for build_faiss_index (Task 3.2, Req 5.1, 5.2, 5.3, 10.3, 10.6)."""

    def test_absent_faiss_raises_import_error(self):
        """
        Req 5.3 / 10.3: when faiss is not installed, build_faiss_index must
        raise ImportError with 'pip install' in the message.
        """
        with unittest.mock.patch.dict(sys.modules, {'faiss': None}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            from evi_trace.utils.embedding_utils import build_faiss_index
            embeddings = np.ones((5, 4), dtype=np.float32)
            with pytest.raises(ImportError, match="pip install"):
                build_faiss_index(embeddings)

    def test_adds_vectors_to_index_cpu(self):
        """
        Req 5.1: build_faiss_index must call index.add with the provided
        embeddings array when on CPU (faiss.get_num_gpus() == 0).
        """
        mock_faiss_index = MockFaissIndex(4)
        mock_faiss = MockFaiss()

        # Override IndexFlatIP to return our trackable mock index
        def mock_index_flat_ip(d):
            mock_faiss_index.d = d
            return mock_faiss_index

        mock_faiss.IndexFlatIP = mock_index_flat_ip

        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            import evi_trace.utils.embedding_utils as eu_local
            from evi_trace.utils.embedding_utils import build_faiss_index

            embeddings = np.array(
                [[1.0, 0.0, 0.0, 0.0],
                 [0.0, 1.0, 0.0, 0.0]],
                dtype=np.float32
            )
            with unittest.mock.patch.object(eu_local.logger, 'info') as mock_info:
                returned_index = build_faiss_index(embeddings)
                # check the CPU log message
                assert any("CPU" in str(call) for call in mock_info.call_args_list)
            assert returned_index is mock_faiss_index
            assert mock_faiss_index.ntotal == 2
            np.testing.assert_array_equal(mock_faiss_index.added_vectors, embeddings)

    def test_index_dimension_matches_embedding_dim(self):
        """build_faiss_index must create an index whose dimension equals embeddings.shape[1]."""
        mock_faiss_index = MockFaissIndex(0)
        mock_faiss = MockFaiss()

        def mock_index_flat_ip(d):
            mock_faiss_index.d = d
            return mock_faiss_index

        mock_faiss.IndexFlatIP = mock_index_flat_ip

        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            from evi_trace.utils.embedding_utils import build_faiss_index

            embeddings = np.ones((3, 768), dtype=np.float32)
            build_faiss_index(embeddings)
            assert mock_faiss_index.d == 768

    def test_import_error_message_names_faiss_cpu(self):
        """
        Req 5.3: ImportError message for build_faiss_index must contain 'faiss-cpu'.
        """
        with unittest.mock.patch.dict(sys.modules, {'faiss': None}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            from evi_trace.utils.embedding_utils import build_faiss_index
            with pytest.raises(ImportError, match="faiss-cpu"):
                build_faiss_index(np.ones((2, 4), dtype=np.float32))

    def test_gpu_path_moves_index(self):
        """
        When faiss.get_num_gpus() > 0, build_faiss_index must call
        faiss.index_cpu_to_gpu and return the GPU index.
        """
        mock_cpu_index = MockFaissIndex(4)
        mock_gpu_index = MockFaissIndex(4)

        class MockFaissWithGPU:
            def IndexFlatIP(self, d):
                mock_cpu_index.d = d
                return mock_cpu_index

            def get_num_gpus(self):
                return 1

            def StandardGpuResources(self):
                return object()

            def index_cpu_to_gpu(self, res, device, index):
                return mock_gpu_index

        mock_faiss_gpu = MockFaissWithGPU()

        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss_gpu}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            import evi_trace.utils.embedding_utils as eu_local
            from evi_trace.utils.embedding_utils import build_faiss_index

            embeddings = np.ones((2, 4), dtype=np.float32)
            with unittest.mock.patch.object(eu_local.logger, 'info') as mock_info:
                returned_index = build_faiss_index(embeddings)
                # check the GPU log message
                assert any("GPU" in str(call) for call in mock_info.call_args_list)
            # The returned index must be the GPU index, not the CPU index
            assert returned_index is mock_gpu_index


# ---------------------------------------------------------------------------
# Task 3.2 Tests: embed_query now calls l2_normalise
# ---------------------------------------------------------------------------

class TestEmbedQueryCallsL2Normalise:
    """
    Req 5.8: After Task 3.2, embed_query should call l2_normalise instead
    of using inline numpy normalisation.  Verify that it still produces
    L2-normalised output.
    """

    def test_embed_query_still_returns_unit_norm_after_refactor(self):
        """
        embed_query must return L2-normalised output after the Task 3.2 refactor
        that delegates normalisation to l2_normalise.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            import evi_trace.utils.embedding_utils as eu

            model = MockModel(dim=768)
            result = eu.embed_query("test query", model)
            norm = float(np.linalg.norm(result[0]))
            assert abs(norm - 1.0) < 1e-5, f"Expected L2 norm ≈ 1.0, got {norm}"

    def test_embed_query_delegates_to_l2_normalise(self):
        """
        embed_query must call l2_normalise exactly once, ensuring that a future
        revert to inline numpy normalisation would be caught by this test.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            import importlib
            import evi_trace.utils.embedding_utils
            importlib.reload(evi_trace.utils.embedding_utils)
            import evi_trace.utils.embedding_utils as eu

            mock_l2 = unittest.mock.MagicMock(side_effect=lambda x: x)  # pass through
            with unittest.mock.patch.object(eu, 'l2_normalise', mock_l2):
                mock_model = MockModel()
                eu.embed_query("test query", mock_model)
            mock_l2.assert_called_once()


# ---------------------------------------------------------------------------
# Task 3.3 Tests: build_sentence_store
# ---------------------------------------------------------------------------
# MockModel is defined at module level (top of file) and already supports
# batch_size and show_progress_bar kwargs as of Task 3.3.


def _make_records(n: int) -> list:
    """Helper to generate n sentence records."""
    return [
        {
            "sentence": f"Sentence {i}",
            "page_index": i,
            "block_bbox": (0, i * 10, 100, i * 10 + 10),
            "span_bboxes": [(0, i * 10, 50, i * 10 + 10)],
        }
        for i in range(n)
    ]


class TestBuildSentenceStoreEmpty:
    """Test 1: Empty sentence_records → guard path returns correct sentinel dict."""

    def test_empty_records_faiss_index_is_none(self):
        """
        Req 5.5: When sentence_records is empty, faiss_index must be None.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            result = eu.build_sentence_store("test.pdf", [], model)
            assert result["faiss_index"] is None

    def test_empty_records_embeddings_shape(self):
        """
        Req 5.5: When sentence_records is empty, embeddings must have shape (0, 768).
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            result = eu.build_sentence_store("test.pdf", [], model)
            assert result["embeddings"].shape == (0, 768)

    def test_empty_records_all_lists_empty(self):
        """
        Req 5.5: When sentence_records is empty, sentences/pages/block_bboxes/span_bboxes
        must all be empty lists.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            result = eu.build_sentence_store("test.pdf", [], model)
            assert result["sentences"] == []
            assert result["pages"] == []
            assert result["block_bboxes"] == []
            assert result["span_bboxes"] == []


class TestBuildSentenceStoreNonEmpty:
    """Test 2 & 3: Non-empty records → all 7 keys present; parallel list lengths match."""

    def test_non_empty_returns_all_seven_keys(self):
        """
        Req 5.6: build_sentence_store must return a dict with all 7 required keys.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            records = _make_records(3)
            result = eu.build_sentence_store("test.pdf", records, model)
            required_keys = {"pdf_path", "sentences", "pages", "block_bboxes",
                             "span_bboxes", "embeddings", "faiss_index"}
            assert required_keys == set(result.keys())

    def test_non_empty_parallel_list_lengths_match(self):
        """
        Req 5.6: All parallel lists (sentences, pages, block_bboxes, span_bboxes,
        embeddings) must have the same length as sentence_records.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            n = 5
            records = _make_records(n)
            result = eu.build_sentence_store("test.pdf", records, model)
            assert len(result["sentences"]) == n
            assert len(result["pages"]) == n
            assert len(result["block_bboxes"]) == n
            assert len(result["span_bboxes"]) == n
            assert len(result["embeddings"]) == n

    def test_non_empty_sentences_values_correct(self):
        """Sentences in returned dict match those extracted from records."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            records = _make_records(3)
            result = eu.build_sentence_store("test.pdf", records, model)
            assert result["sentences"] == [r["sentence"] for r in records]

    def test_non_empty_pages_values_correct(self):
        """Pages in returned dict match those extracted from records."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            records = _make_records(3)
            result = eu.build_sentence_store("test.pdf", records, model)
            assert result["pages"] == [r["page_index"] for r in records]


class TestBuildSentenceStoreTruncation:
    """Test 4: Records exceeding _MAX_SENTENCES → RuntimeWarning emitted; truncation applied."""

    def test_truncation_emits_runtime_warning(self):
        """
        Req 5.7: When sentence_records exceeds _MAX_SENTENCES, a RuntimeWarning
        must be emitted containing pdf_path and the actual record count.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            n = eu._MAX_SENTENCES + 5
            records = _make_records(n)
            pdf_path = "big_document.pdf"

            with pytest.warns(RuntimeWarning) as warning_info:
                eu.build_sentence_store(pdf_path, records, model)

            # Warning message must contain pdf_path and actual count
            warning_messages = [str(w.message) for w in warning_info.list]
            assert any(pdf_path in msg for msg in warning_messages), (
                f"Warning must contain pdf_path '{pdf_path}'. Got: {warning_messages}"
            )
            assert any(str(n) in msg for msg in warning_messages), (
                f"Warning must contain actual count {n}. Got: {warning_messages}"
            )

    def test_truncation_result_has_max_sentences_length(self):
        """
        Req 5.7: After truncation warning, returned lists must have length == _MAX_SENTENCES.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            n = eu._MAX_SENTENCES + 10
            records = _make_records(n)
            max_s = eu._MAX_SENTENCES

            with pytest.warns(RuntimeWarning):
                result = eu.build_sentence_store("big.pdf", records, model)

            assert len(result["sentences"]) == max_s
            assert len(result["pages"]) == max_s
            assert len(result["block_bboxes"]) == max_s
            assert len(result["span_bboxes"]) == max_s

    def test_no_warning_when_at_max_sentences(self):
        """No RuntimeWarning when sentence count equals exactly _MAX_SENTENCES."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            n = eu._MAX_SENTENCES
            records = _make_records(n)

            import warnings
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                eu.build_sentence_store("exact.pdf", records, model)
            runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
            assert len(runtime_warnings) == 0


class TestBuildSentenceStoreFaissIndex:
    """Test 5: faiss_index is not None for non-empty records."""

    def test_non_empty_faiss_index_is_not_none(self):
        """
        Req 5.6 / 10.6: build_sentence_store must return a non-None faiss_index
        when sentence_records is non-empty.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            records = _make_records(3)
            result = eu.build_sentence_store("test.pdf", records, model)
            assert result["faiss_index"] is not None

    def test_non_empty_faiss_index_is_mock_faiss_index(self):
        """faiss_index is an instance of MockFaissIndex when MockFaiss is used."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            records = _make_records(3)
            result = eu.build_sentence_store("test.pdf", records, model)
            assert isinstance(result["faiss_index"], MockFaissIndex)

    def test_faiss_index_ntotal_matches_sentence_count(self):
        """The FAISS index must contain exactly as many vectors as sentences."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            n = 4
            records = _make_records(n)
            result = eu.build_sentence_store("test.pdf", records, model)
            assert result["faiss_index"].ntotal == n


class TestBuildSentenceStorePdfPath:
    """Test 6: pdf_path is stored correctly in returned dict."""

    def test_pdf_path_stored_verbatim(self):
        """
        Req 5.5 / 5.6: pdf_path must be stored verbatim in the returned dict.
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            pdf_path = "/absolute/path/to/my_document.pdf"
            result = eu.build_sentence_store(pdf_path, _make_records(2), model)
            assert result["pdf_path"] == pdf_path

    def test_pdf_path_stored_verbatim_empty_records(self):
        """pdf_path is stored verbatim even when sentence_records is empty."""
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            pdf_path = "relative/path.pdf"
            result = eu.build_sentence_store(pdf_path, [], model)
            assert result["pdf_path"] == pdf_path


class TestBuildSentenceStoreEmbeddingsNormalised:
    """Embeddings in returned dict must be L2-normalised."""

    def test_embeddings_are_l2_normalised(self):
        """
        Req 5.8: Embeddings in the returned store must be L2-normalised
        (each row has L2 norm ≈ 1.0).
        """
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {'faiss': mock_faiss}):
            keys_to_evict = [k for k in sys.modules if k == 'evi_trace.utils.embedding_utils']
            for k in keys_to_evict:
                del sys.modules[k]
            eu = importlib.import_module('evi_trace.utils.embedding_utils')
            importlib.reload(eu)

            model = MockModel()
            records = _make_records(3)
            result = eu.build_sentence_store("test.pdf", records, model)
            embeddings = result["embeddings"]
            for i in range(embeddings.shape[0]):
                norm = float(np.linalg.norm(embeddings[i]))
                assert abs(norm - 1.0) < 1e-5, (
                    f"Row {i} has norm {norm}, expected ≈ 1.0"
                )
