"""Embedding utilities for the text_processing package.

Provides :class:`EmbeddingProcessor` with lazy-loaded functions for
sentence embedding, FAISS index construction, and sentence store building.

Heavy dependencies (``faiss``, ``torch``, ``sentence-transformers``) are
imported lazily inside method bodies — never at module level.
"""

from __future__ import annotations

import logging
import warnings

from text_processing.base import TextProcessor

logger = logging.getLogger("pdf_extractor")

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_BGE_MODEL_NAME: str = "BAAI/bge-base-en-v1.5"
_BGE_QUERY_PREFIX: str = "Represent this sentence for searching relevant passages: "
_EMBEDDING_DIM: int = 768
_ENCODE_BATCH_SIZE: int = 64


class EmbeddingProcessor(TextProcessor):
    """Embedding engine with lazy-loaded dependencies.

    Parameters
    ----------
    model_name : str
        HuggingFace model identifier. Default: ``"BAAI/bge-base-en-v1.5"``.
    max_sentences : int
        Maximum number of sentences to encode. Default: 10000.
    """

    def __init__(
        self,
        model_name: str = _BGE_MODEL_NAME,
        max_sentences: int = 10_000,
    ) -> None:
        self._model_name = model_name
        self._max_sentences = max_sentences
        # No model loading at construction time

    def load_embedding_model(self, model_name: str | None = None):
        """Lazy-load a SentenceTransformer model.

        Parameters
        ----------
        model_name : str or None
            Model to load. Defaults to ``self._model_name``.

        Returns
        -------
        SentenceTransformer
            The loaded model instance.

        Raises
        ------
        ImportError
            When ``sentence-transformers`` is not installed.
        """
        name = model_name or self._model_name
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for semantic QC. "
                "Install it with: pip install sentence-transformers"
            ) from exc
        model = SentenceTransformer(name)
        logger.info("Loaded embedding model: %s", name)
        return model

    def embed_query(
        self,
        query_text: str,
        model,
        query_prefix: str = _BGE_QUERY_PREFIX,
    ):
        """Embed a single query string and return an L2-normalised ``(1, D)`` array.

        Parameters
        ----------
        query_text : str
            The raw query string.
        model :
            A loaded SentenceTransformer model.
        query_prefix : str
            Prefix prepended before encoding.

        Returns
        -------
        numpy.ndarray
            Float32 array of shape ``(1, D)``, L2-normalised.
        """
        import numpy as np

        prefixed = query_prefix + query_text if query_prefix else query_text
        query_vec = model.encode([prefixed], convert_to_numpy=True).astype(np.float32)
        query_vec = self.l2_normalise(query_vec)
        return query_vec

    def l2_normalise(self, vectors):
        """L2-normalise each row of a 2-D float32 array in-place via faiss.

        Parameters
        ----------
        vectors : numpy.ndarray
            2-D array of shape ``(N, D)`` with dtype ``float32``.

        Returns
        -------
        numpy.ndarray
            The same array with every row scaled to unit L2 norm.

        Raises
        ------
        ImportError
            When ``faiss`` is not installed.
        """
        import numpy as np

        if vectors.shape[0] == 0:
            return vectors

        try:
            import faiss
        except ImportError as exc:
            raise ImportError(
                "faiss is required for semantic QC. "
                "Install it with: pip install faiss-cpu  (or faiss-gpu for GPU support)"
            ) from exc

        vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        faiss.normalize_L2(vectors)
        return vectors

    def build_faiss_index(self, embeddings):
        """Build a ``faiss.IndexFlatIP`` index from L2-normalised embeddings.

        Parameters
        ----------
        embeddings : numpy.ndarray
            2-D array of shape ``(N, D)``, dtype ``float32``, L2-normalised.

        Returns
        -------
        faiss.Index
            A populated inner-product index.

        Raises
        ------
        ImportError
            When ``faiss`` is not installed.
        """
        try:
            import faiss
        except ImportError as exc:
            raise ImportError(
                "faiss is required for semantic QC. "
                "Install it with: pip install faiss-cpu  (or faiss-gpu for GPU support)"
            ) from exc

        D: int = embeddings.shape[1]
        index = faiss.IndexFlatIP(D)

        if faiss.get_num_gpus() > 0:
            res = faiss.StandardGpuResources()
            index = faiss.index_cpu_to_gpu(res, 0, index)
            logger.info("FAISS index moved to GPU 0 (D=%d)", D)
        else:
            logger.info("FAISS index kept on CPU (D=%d)", D)

        index.add(embeddings)
        return index

    def build_sentence_store(
        self,
        pdf_path: str,
        sentence_records: list,
        model,
    ) -> dict:
        """Build a complete SentenceStore dict for a single PDF.

        Parameters
        ----------
        pdf_path : str
            Absolute path to the source PDF.
        sentence_records : list[dict]
            Each must contain ``'sentence'`` and ``'page_index'``.
        model :
            A loaded SentenceTransformer model.

        Returns
        -------
        dict
            SentenceStore with keys: ``pdf_path``, ``sentences``, ``pages``,
            ``block_bboxes``, ``span_bboxes``, ``embeddings``, ``faiss_index``.

        Warns
        -----
        RuntimeWarning
            When ``len(sentence_records) > max_sentences``.
        """
        import numpy as np

        if not sentence_records:
            return {
                "pdf_path": pdf_path,
                "sentences": [],
                "pages": [],
                "block_bboxes": [],
                "span_bboxes": [],
                "embeddings": np.empty((0, _EMBEDDING_DIM), dtype=np.float32),
                "faiss_index": None,
            }

        sentences = [r["sentence"] for r in sentence_records]
        pages = [r["page_index"] for r in sentence_records]
        block_bboxes = [r.get("block_bbox") for r in sentence_records]
        span_bboxes = [r.get("span_bboxes") for r in sentence_records]

        if len(sentences) > self._max_sentences:
            warnings.warn(
                f"[embedding_utils] PDF '{pdf_path}' has {len(sentences)} sentences; "
                f"truncating to the first {self._max_sentences}.",
                RuntimeWarning,
                stacklevel=2,
            )
            sentences = sentences[: self._max_sentences]
            pages = pages[: self._max_sentences]
            block_bboxes = block_bboxes[: self._max_sentences]
            span_bboxes = span_bboxes[: self._max_sentences]

        raw_embeddings = model.encode(
            sentences,
            batch_size=_ENCODE_BATCH_SIZE,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).astype(np.float32)

        embeddings = self.l2_normalise(raw_embeddings)
        index = self.build_faiss_index(embeddings)

        return {
            "pdf_path": pdf_path,
            "sentences": sentences,
            "pages": pages,
            "block_bboxes": block_bboxes,
            "span_bboxes": span_bboxes,
            "embeddings": embeddings,
            "faiss_index": index,
        }

    # -- Unrelated abstract methods --

    def normalize(self, text: str) -> str:
        raise NotImplementedError("EmbeddingProcessor does not implement normalize().")

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError("EmbeddingProcessor does not implement tokenize_words().")

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError("EmbeddingProcessor does not implement tokenize_sentences().")

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError("EmbeddingProcessor does not implement clean_ocr().")

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError("EmbeddingProcessor does not implement compare().")

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError("EmbeddingProcessor does not implement extract_keywords().")
