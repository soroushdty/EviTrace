"""
evi_trace/utils/embedding_utils.py
========================
Embedding Engine for the EviTrace parser.

Provides lazy-loaded, optional-dependency functions for loading a BGE
sentence-embedding model, normalising vectors, building a FAISS index, and
encoding query strings.  **None** of the heavy dependencies (``faiss``,
``torch``, ``sentence_transformers``) are imported at module level: they are
imported lazily inside each function that requires them.  This ensures that
``import evi_trace.utils.embedding_utils`` succeeds in any environment, even when the
optional packages are not installed.

No global state is mutated at import time.  In particular, neither
``np.random.seed()`` nor ``torch.manual_seed()`` are called anywhere in this
module.

Functions
---------
load_embedding_model  — lazy-load a SentenceTransformer model
embed_query           — embed a query string with configurable prefix
l2_normalise          — L2-normalise a 2-D float32 array via faiss.normalize_L2
build_faiss_index     — build a faiss.IndexFlatIP index from embeddings
build_sentence_store  — build the full SentenceStore dict for a PDF

Usage::

    from evi_trace.utils.embedding_utils import load_embedding_model, embed_query
    model = load_embedding_model()           # requires sentence-transformers
    vec   = embed_query("my query", model)   # shape (1, 768), L2-normalised

Logging is via ``logging.getLogger("evi_trace")`` — no ``print()`` calls.
"""

import logging

# ---------------------------------------------------------------------------
# Module-level constants (no heavy imports, no side effects)
# ---------------------------------------------------------------------------

_BGE_MODEL_NAME: str = "BAAI/bge-base-en-v1.5"
_BGE_QUERY_PREFIX: str = "Represent this sentence for searching relevant passages: "
_EMBEDDING_DIM: int = 768
_MAX_SENTENCES: int = 10_000
_ENCODE_BATCH_SIZE: int = 64

logger = logging.getLogger("evi_trace")


# ---------------------------------------------------------------------------
# Public API — Task 3.1
# ---------------------------------------------------------------------------

def load_embedding_model(model_name: str = _BGE_MODEL_NAME):
    """Lazy-import SentenceTransformer and load the BGE model.

    Parameters
    ----------
    model_name:
        HuggingFace model identifier to load.  Defaults to
        ``_BGE_MODEL_NAME`` (``"BAAI/bge-base-en-v1.5"``).  Override via
        ``config["quality_control"]["semantic_qc"]["model_name"]`` when
        calling from the pipeline.

    Returns
    -------
    SentenceTransformer
        The loaded sentence-transformer model instance.

    Raises
    ------
    ImportError
        When ``sentence-transformers`` is not installed, with a human-readable
        ``pip install`` hint.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is required for semantic QC. "
            "Install it with: pip install sentence-transformers"
        ) from exc
    model = SentenceTransformer(model_name)
    logger.info("Loaded embedding model: %s", model_name)
    return model


def embed_query(
    query_text: str,
    model,
    query_prefix: str = _BGE_QUERY_PREFIX,
):
    """Embed a single query string and return an L2-normalised ``(1, D)`` array.

    Parameters
    ----------
    query_text:
        The raw query string to embed.
    model:
        A loaded SentenceTransformer (or compatible) model with an
        ``encode(texts, convert_to_numpy=True)`` method.
    query_prefix:
        String prepended to *query_text* before encoding.  Defaults to
        ``_BGE_QUERY_PREFIX``.  Pass ``""`` to disable prepending — useful
        for models that do not use asymmetric retrieval prefixes.  Override
        via ``config["quality_control"]["semantic_qc"]["query_prefix"]`` when
        calling from the pipeline.

    Returns
    -------
    numpy.ndarray
        Float32 array of shape ``(1, D)`` where *D* is the model's embedding
        dimension.  Each row is L2-normalised (unit norm).

    Notes
    -----
    L2 normalisation is delegated to :func:`l2_normalise`, which requires ``faiss``
    (``faiss.normalize_L2`` is called in-place).  Callers must have ``faiss`` installed.
    The BGE retrieval prefix is prepended to the query string; an empty string disables
    prefix prepending, which is appropriate for models that do not use asymmetric
    retrieval prefixes.
    """
    # Lazy import numpy — avoids any module-level import of numpy.
    import numpy as np

    # Prepend prefix unless it is empty (empty string disables prepending).
    prefixed = query_prefix + query_text if query_prefix else query_text

    # Encode using the model; convert_to_numpy=True returns a plain ndarray.
    query_vec = model.encode([prefixed], convert_to_numpy=True).astype(np.float32)

    # L2-normalise via l2_normalise (Task 3.2 refactor — delegates to faiss).
    query_vec = l2_normalise(query_vec)

    return query_vec


# ---------------------------------------------------------------------------
# Public API — Task 3.2
# ---------------------------------------------------------------------------

_FAISS_IMPORT_ERROR = (
    "faiss is required for semantic QC. "
    "Install it with: pip install faiss-cpu  (or faiss-gpu for GPU support)"
)


def l2_normalise(vectors) -> "np.ndarray":
    """L2-normalise each row of a 2-D float32 array in-place via faiss.

    Parameters
    ----------
    vectors : np.ndarray
        2-D array of shape ``(N, D)`` with dtype ``float32``.

    Returns
    -------
    np.ndarray
        The same array with every row scaled to unit L2 norm.
        If ``vectors.shape[0] == 0``, returns ``vectors`` unchanged.

    Raises
    ------
    ImportError
        When ``faiss`` is not installed, with a human-readable pip install hint.
    """
    import numpy as np

    if vectors.shape[0] == 0:
        return vectors

    try:
        import faiss
    except ImportError as exc:
        raise ImportError(_FAISS_IMPORT_ERROR) from exc

    vectors = np.ascontiguousarray(vectors, dtype=np.float32)
    faiss.normalize_L2(vectors)
    return vectors


def build_faiss_index(embeddings) -> object:
    """Build a ``faiss.IndexFlatIP`` index from L2-normalised embeddings.

    Inner-product search on unit-norm vectors is equivalent to cosine
    similarity, which is the correct metric for BGE embeddings.

    If a CUDA-capable GPU is visible to FAISS, the index is moved to GPU 0
    for accelerated search.

    Parameters
    ----------
    embeddings : np.ndarray
        2-D array of shape ``(N, D)``, dtype ``float32``, already L2-normalised.

    Returns
    -------
    faiss.Index
        A populated index containing all ``N`` vectors.

    Raises
    ------
    ImportError
        When ``faiss`` is not installed, with a human-readable pip install hint.
    """
    try:
        import faiss
    except ImportError as exc:
        raise ImportError(_FAISS_IMPORT_ERROR) from exc

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


# ---------------------------------------------------------------------------
# Public API — Task 3.3
# ---------------------------------------------------------------------------

def build_sentence_store(pdf_path: str, sentence_records: list, model) -> dict:
    """Build a complete ``SentenceStore`` dict for a single PDF.

    Embeddings are computed ONCE in a single batched call — never inside any
    per-row verification loop.  The BGE retrieval prefix is intentionally
    NOT prepended to document sentences (only ``embed_query`` does that).

    Parameters
    ----------
    pdf_path : str
        Absolute path to the source PDF; stored verbatim in the returned dict.
    sentence_records : list[dict]
        Sentence records as produced by
        ``sentence_processor.process_sentences``.  Each record must contain
        at least ``'sentence'`` (str) and ``'page_index'`` (int); the optional
        ``'block_bbox'`` and ``'span_bboxes'`` fields are stored in the
        returned store so that semantic-match results carry full anchor data.
    model :
        A loaded SentenceTransformer (or compatible) model with an
        ``encode(texts, batch_size, show_progress_bar, convert_to_numpy)``
        method.

    Returns
    -------
    dict
        A ``SentenceStore`` with keys:
        ``pdf_path``, ``sentences``, ``pages``, ``block_bboxes``,
        ``span_bboxes``, ``embeddings``, ``faiss_index``.

        If ``sentence_records`` is empty, ``embeddings`` has shape
        ``(0, _EMBEDDING_DIM)`` and ``faiss_index`` is ``None``.

    Warns
    -----
    RuntimeWarning
        When ``len(sentence_records) > _MAX_SENTENCES``, a ``RuntimeWarning``
        is emitted before truncating to the first ``_MAX_SENTENCES`` records.
        The warning message includes ``pdf_path`` and the actual record count.
    """
    import numpy as np

    # -- Empty-input guard ----------------------------------------------------
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

    # -- Unpack parallel lists from records -----------------------------------
    sentences = [r["sentence"] for r in sentence_records]
    pages = [r["page_index"] for r in sentence_records]
    block_bboxes = [r.get("block_bbox") for r in sentence_records]
    span_bboxes = [r.get("span_bboxes") for r in sentence_records]

    # -- Truncation guard (large PDFs) ----------------------------------------
    if len(sentences) > _MAX_SENTENCES:
        import warnings
        warnings.warn(
            f"[embedding_utils] PDF '{pdf_path}' has {len(sentences)} sentences; "
            f"truncating to the first {_MAX_SENTENCES}.",
            RuntimeWarning,
            stacklevel=2,
        )
        sentences = sentences[:_MAX_SENTENCES]
        pages = pages[:_MAX_SENTENCES]
        block_bboxes = block_bboxes[:_MAX_SENTENCES]
        span_bboxes = span_bboxes[:_MAX_SENTENCES]

    # -- Batch encode (single call, no per-row loops) -------------------------
    raw_embeddings = model.encode(
        sentences,
        batch_size=_ENCODE_BATCH_SIZE,
        show_progress_bar=False,
        convert_to_numpy=True,
    ).astype(np.float32)

    # -- L2 normalisation -----------------------------------------------------
    embeddings = l2_normalise(raw_embeddings)

    # -- FAISS index ----------------------------------------------------------
    index = build_faiss_index(embeddings)

    return {
        "pdf_path": pdf_path,
        "sentences": sentences,
        "pages": pages,
        "block_bboxes": block_bboxes,
        "span_bboxes": span_bboxes,
        "embeddings": embeddings,
        "faiss_index": index,
    }
