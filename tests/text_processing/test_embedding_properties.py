"""
tests/text_processing/test_embedding_properties.py
==================================================
Property-based tests for EmbeddingProcessor (Properties 7, 8).
"""

import sys
import unittest.mock

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from text_processing.embedding import EmbeddingProcessor

pytestmark = pytest.mark.slow


class MockFaiss:
    """Minimal faiss stand-in."""

    def normalize_L2(self, vectors) -> None:
        # In-place unit norm
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        vectors[:] = vectors / norms

    def get_num_gpus(self):
        return 0

    class IndexFlatIP:
        def __init__(self, d):
            self._d = d

        def add(self, vectors):
            pass

    class StandardGpuResources:
        pass


class MockModel:
    def __init__(self, dim: int = 4):
        self._dim = dim

    def encode(self, texts, batch_size=64, show_progress_bar=False, convert_to_numpy=True):
        return np.random.randn(len(texts), self._dim).astype(np.float32)


# ---------------------------------------------------------------------------
# Property 7: embed_query always returns shape (1, D) with D > 0
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(st.text(min_size=1, max_size=100))
def test_embed_query_shape_property(query: str):
    """Property 7: embed_query returns (1, D) for any non-empty query."""
    mock_faiss = MockFaiss()
    with unittest.mock.patch.dict(sys.modules, {"faiss": mock_faiss}):
        proc = EmbeddingProcessor()
        model = MockModel(dim=4)
        result = proc.embed_query(query, model)
        assert result.shape[0] == 1
        assert result.shape[1] > 0


# ---------------------------------------------------------------------------
# Property 8: l2_normalise produces unit vectors
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(
    st.integers(min_value=1, max_value=10),
    st.integers(min_value=2, max_value=8),
)
def test_l2_normalise_produces_unit_vectors(n: int, d: int):
    """Property 8: after l2_normalise, each row has L2 norm approx 1.0."""
    mock_faiss = MockFaiss()
    with unittest.mock.patch.dict(sys.modules, {"faiss": mock_faiss}):
        proc = EmbeddingProcessor()
        vectors = np.random.randn(n, d).astype(np.float32)
        # Avoid zero vectors
        vectors[np.linalg.norm(vectors, axis=1) == 0] = 1.0
        result = proc.l2_normalise(vectors)
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-5)
