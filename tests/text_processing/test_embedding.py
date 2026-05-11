"""
tests/text_processing/test_embedding.py
=======================================
EmbeddingProcessor tests with mocked deps (mark slow).

Migrated from tests/pdf_extractor/test_embedding_utils.py.
"""

import importlib
import sys
import unittest.mock

import numpy as np
import pytest

from text_processing.embedding import EmbeddingProcessor

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Mock model
# ---------------------------------------------------------------------------

class MockModel:
    """Minimal stand-in for a SentenceTransformer model."""

    def __init__(self, dim: int = 768):
        self._dim = dim

    def encode(self, texts, batch_size=64, show_progress_bar=False, convert_to_numpy=True):
        return np.ones((len(texts), self._dim), dtype=np.float32)


class MockFaiss:
    """Minimal faiss stand-in."""

    def normalize_L2(self, vectors) -> None:
        pass

    def get_num_gpus(self):
        return 0

    class IndexFlatIP:
        def __init__(self, d):
            self._d = d
            self._vectors = []

        def add(self, vectors):
            self._vectors.append(vectors)

    class StandardGpuResources:
        pass


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------

class TestImportSafety:
    """text_processing.embedding must be importable without heavy deps."""

    def test_import_succeeds_without_heavy_deps(self):
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


# ---------------------------------------------------------------------------
# load_embedding_model
# ---------------------------------------------------------------------------

class TestLoadEmbeddingModel:
    def test_raises_import_error_when_missing(self):
        proc = EmbeddingProcessor()
        with unittest.mock.patch.dict(sys.modules, {"sentence_transformers": None}):
            with pytest.raises(ImportError, match="pip install"):
                proc.load_embedding_model()


# ---------------------------------------------------------------------------
# embed_query
# ---------------------------------------------------------------------------

class TestEmbedQuery:
    def test_returns_correct_shape(self):
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {"faiss": mock_faiss}):
            proc = EmbeddingProcessor()
            model = MockModel(dim=768)
            result = proc.embed_query("test query", model)
            assert result.ndim == 2
            assert result.shape[0] == 1
            assert result.shape[1] == 768

    def test_returns_shape_small_dim(self):
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {"faiss": mock_faiss}):
            proc = EmbeddingProcessor()
            model = MockModel(dim=4)
            result = proc.embed_query("hello", model)
            assert result.shape == (1, 4)


# ---------------------------------------------------------------------------
# l2_normalise
# ---------------------------------------------------------------------------

class TestL2Normalise:
    def test_raises_import_error_when_faiss_missing(self):
        proc = EmbeddingProcessor()
        vectors = np.ones((2, 4), dtype=np.float32)
        with unittest.mock.patch.dict(sys.modules, {"faiss": None}):
            with pytest.raises(ImportError, match="pip install"):
                proc.l2_normalise(vectors)

    def test_empty_array_returns_unchanged(self):
        proc = EmbeddingProcessor()
        empty = np.empty((0, 4), dtype=np.float32)
        result = proc.l2_normalise(empty)
        assert result.shape == (0, 4)


# ---------------------------------------------------------------------------
# build_faiss_index
# ---------------------------------------------------------------------------

class TestBuildFaissIndex:
    def test_raises_import_error_when_faiss_missing(self):
        proc = EmbeddingProcessor()
        embeddings = np.ones((2, 4), dtype=np.float32)
        with unittest.mock.patch.dict(sys.modules, {"faiss": None}):
            with pytest.raises(ImportError, match="pip install"):
                proc.build_faiss_index(embeddings)


# ---------------------------------------------------------------------------
# build_sentence_store
# ---------------------------------------------------------------------------

class TestBuildSentenceStore:
    def test_empty_records_returns_none_index(self):
        proc = EmbeddingProcessor()
        result = proc.build_sentence_store("/fake.pdf", [], MockModel())
        assert result["faiss_index"] is None
        assert result["sentences"] == []
        assert result["embeddings"].shape == (0, 768)

    def test_truncation_warning(self):
        proc = EmbeddingProcessor(max_sentences=5)
        records = [{"sentence": f"s{i}", "page_index": 0} for i in range(10)]
        mock_faiss = MockFaiss()
        with unittest.mock.patch.dict(sys.modules, {"faiss": mock_faiss}):
            with pytest.warns(RuntimeWarning):
                proc.build_sentence_store("/fake.pdf", records, MockModel(dim=768))


# ---------------------------------------------------------------------------
# Unrelated methods
# ---------------------------------------------------------------------------

class TestUnrelatedMethods:
    def test_normalize_raises(self):
        proc = EmbeddingProcessor()
        with pytest.raises(NotImplementedError):
            proc.normalize("text")

    def test_tokenize_words_raises(self):
        proc = EmbeddingProcessor()
        with pytest.raises(NotImplementedError):
            proc.tokenize_words("text")

    def test_tokenize_sentences_raises(self):
        proc = EmbeddingProcessor()
        with pytest.raises(NotImplementedError):
            proc.tokenize_sentences("text")

    def test_clean_ocr_raises(self):
        proc = EmbeddingProcessor()
        with pytest.raises(NotImplementedError):
            proc.clean_ocr("text")

    def test_compare_raises(self):
        proc = EmbeddingProcessor()
        with pytest.raises(NotImplementedError):
            proc.compare("a", "b")

    def test_extract_keywords_raises(self):
        proc = EmbeddingProcessor()
        with pytest.raises(NotImplementedError):
            proc.extract_keywords("text")
