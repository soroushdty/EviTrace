"""
pipeline/extraction_pipeline.py
--------------------------------------
Shared extraction logic: per-page scan detection, backend routing, and QC
pipeline for a single PDF.

This module is the single source of truth for the full multi-backend
extraction flow.  Both the standalone ``pdf_extractor.py`` CLI and the async
``pipeline/orchestrator.py`` delegate to :func:`build_qc_bundle` rather than
duplicating the routing logic.

Public API
----------
build_qc_bundle(pdf_path, pdf_name, qc_config) -> QCBundle
    Run scan detection → backend routing → QC pipeline for one PDF and
    return a fully populated :class:`~quality_control.models.QCBundle`.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pdf_extractor.extraction.GROBID import extract_with_grobid, parse_grobid_tei
from pdf_extractor.extraction.PyMuPDF import extract_with_pymupdf
from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber
from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr
from pdf_extractor.extraction import scan_detector
from pdf_extractor.annotation import w3c_annotation
from artifact_generation import generate_w3c_jsonld
from quality_control import QCBundle, run_quality_control
from quality_control.models import Candidate
from text_processing.base import TextProcessor

logger = logging.getLogger("pdf_extractor")


# ---------------------------------------------------------------------------
# Expensive singletons
# ---------------------------------------------------------------------------
# Previously, LexicalMatcher, SemanticMatcher, and the TextProcessor class
# were instantiated per PDF inside build_qc_bundle. Each instantiation paid
# hidden cost:
#
#   - SemanticMatcher construction is cheap, but its FAISS-backed search path
#     lazy-imports sentence_transformers + torch the first time it runs,
#     loading a 400 MB BGE model. Per-PDF instantiation can trigger repeated
#     model reloads depending on Python's import caching.
#   - TextProcessor (DefaultTextProcessor with sentence_tokenizer.backend =
#     "scispacy") eagerly loads en_core_sci_sm on first use, which spaCy
#     caches per-process but the wrapper object rebuilds its own private
#     lazy-load state per instance, so each PDF re-runs the _load_sentence_
#     backend dispatch.
#   - LexicalMatcher itself is effectively stateless but still allocates.
#
# We cache these at module level. lru_cache() is thread-safe in CPython
# (GIL-protected), and the cached objects are read-only w.r.t. their
# ``search`` / ``tokenize_sentences`` methods, so sharing them across
# concurrent PDF workers is safe.

_tp_lock = threading.Lock()
_tp_cache: dict[tuple[str, int], Any] = {}


def _freeze_config(cfg: dict | None) -> int:
    """Return a stable hash of a nested config dict for lru_cache keying."""
    if not cfg:
        return 0
    # Repr is stable for the small, simple dicts we use here and avoids
    # needing to recursively freeze nested dicts into frozensets.
    return hash(repr(sorted(_flatten_config(cfg))))


def _flatten_config(cfg: dict, prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for k, v in cfg.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.extend(_flatten_config(v, key))
        else:
            items.append((key, repr(v)))
    return items


@functools.lru_cache(maxsize=1)
def _get_lexical_matcher():
    from text_processing.matchers import LexicalMatcher  # noqa: PLC0415
    return LexicalMatcher()


@functools.lru_cache(maxsize=1)
def _get_semantic_matcher():
    from text_processing.matchers import SemanticMatcher  # noqa: PLC0415
    return SemanticMatcher()


def _get_text_processor(class_path: str, tp_cfg: dict):
    """Return a cached TextProcessor instance for (class_path, config-hash).

    We cache by a hash of the flattened config instead of by object identity
    so callers that rebuild the config dict per PDF still hit the cache.
    """
    cfg_hash = _freeze_config(tp_cfg)
    key = (class_path, cfg_hash)
    cached = _tp_cache.get(key)
    if cached is not None:
        return cached
    with _tp_lock:
        cached = _tp_cache.get(key)
        if cached is not None:
            return cached
        import importlib as _importlib  # noqa: PLC0415
        module_name, class_name = class_path.rsplit(".", 1)
        module = _importlib.import_module(module_name)
        cls = getattr(module, class_name)
        instance = cls(config=tp_cfg)
        _tp_cache[key] = instance
        logger.debug(
            "text_processor cached: class=%s config_hash=%d (cache size=%d)",
            class_path, cfg_hash, len(_tp_cache),
        )
        return instance


# ---------------------------------------------------------------------------
# GROBID TEI disk cache
# ---------------------------------------------------------------------------

def _pdf_sha256(pdf_path: Path) -> str:
    """Return the hex SHA-256 digest of *pdf_path* contents."""
    h = hashlib.sha256()
    with open(pdf_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _grobid_cache_read(
    pdf_path: Path, cache_dir: Path | None
) -> tuple[str | None, str]:
    """Return (cached_tei_xml_or_None, pdf_sha256).

    The cache is content-addressed by PDF SHA-256 so renamed or moved PDFs
    still hit the cache and re-processed PDFs with changed content always miss.
    Returns (None, "") when the PDF cannot be read (e.g. in unit tests with
    fake paths), which is treated as a cache miss.
    """
    try:
        digest = _pdf_sha256(pdf_path)
    except OSError:
        return None, ""
    if cache_dir is not None:
        cache_file = cache_dir / f"{digest}.tei.xml"
        if cache_file.exists():
            try:
                return cache_file.read_text(encoding="utf-8"), digest
            except OSError:
                pass
    return None, digest


def _grobid_cache_write(tei_xml: str, digest: str, cache_dir: Path | None) -> None:
    if cache_dir is None or not tei_xml or not digest:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{digest}.tei.xml").write_text(tei_xml, encoding="utf-8")
    except OSError as exc:
        logger.warning("GROBID cache write failed: %s", exc)


def build_qc_bundle(
    pdf_path: Path | str,
    pdf_name: str,
    qc_config: dict,
) -> QCBundle:
    """Run per-page scan detection, route to correct extractors, and run the
    full QC pipeline for one PDF.

    Per-page routing
    ----------------
    - All pages native → GROBID (semantic authority) + pdfplumber (structural
      authority); PyMuPDF font metadata stored in ``ctx.unified.content``.
    - Any page scanned + ``ocr=true`` → PaddleOCR (primary) + PyMuPDF OCR
      (secondary cross-validation).
    - Any page scanned + ``ocr=false`` → skip extraction, log WARNING, no branch.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.
    pdf_name:
        Human-readable name used in log messages and as the document ID.
    qc_config:
        Loaded QC config dict (from ``load_qc_config()``).

    Returns
    -------
    QCBundle
        Fully populated bundle with ``branches``, ``reports``, ``iaa_metrics``,
        ``decision``, and ``unified`` set.
    """
    pdf_path = Path(pdf_path)
    logger.debug("build_qc_bundle: pdf=%s, pdf_name=%s", pdf_path, pdf_name)

    grobid_failure_behavior = (
        qc_config.get("quality_control", {})
        .get("grobid_integration", {})
        .get("failure_behavior", "fallback")
    )
    ocr_enabled: bool = bool(qc_config.get("ocr", True))
    logger.debug(
        "grobid_failure_behavior=%s, ocr_enabled=%s",
        grobid_failure_behavior, ocr_enabled,
    )

    # ------------------------------------------------------------------
    # Step 1 — Per-page scan detection
    # ------------------------------------------------------------------
    tp_cfg = qc_config.get(
        "text_processor",
        {"sentence_tokenizer": {"backend": "nltk_punkt"}},
    )
    _tp_class_path = tp_cfg.get("class", "text_processing.composite.DefaultTextProcessor")
    logger.debug("Loading text_processor class: %s", _tp_class_path)
    tp = _get_text_processor(_tp_class_path, tp_cfg)
    logger.debug("text_processor instance: %s (cached)", type(tp).__name__)

    import fitz as _fitz  # noqa: PLC0415 — lazy; not installed in all envs
    doc = _fitz.open(str(pdf_path))
    try:
        pages = list(doc)
        logger.debug("%s opened with fitz: %d pages", pdf_name, len(pages))
        scan_cfg = qc_config.get("quality_control", {})
        page_classifications = [
            scan_detector.classify_page(page, tp, scan_cfg, page_index=i)
            for i, page in enumerate(pages)
        ]
        for cls in page_classifications:
            logger.debug(
                "  scan page %d: native=%s, triggered_stages=%s, values=%s",
                cls.page_index, cls.is_native, cls.triggered_stages, cls.stage_values,
            )
    finally:
        doc.close()

    all_native = all(c.is_native for c in page_classifications)
    has_scanned = not all_native
    logger.debug(
        "scan summary: all_native=%s, has_scanned=%s, native_pages=%d/%d",
        all_native, has_scanned,
        sum(1 for c in page_classifications if c.is_native),
        len(page_classifications),
    )

    # ------------------------------------------------------------------
    # Step 2 — Route to correct extractors based on page classifications
    # ------------------------------------------------------------------
    tei_xml = ""
    branches: list[Candidate] = []

    if all_native:
        # Native path: GROBID (semantic) + pdfplumber (structural)
        logger.debug("Routing %s: native path (GROBID + pdfplumber)", pdf_name)
        grobid_cfg = qc_config.get("quality_control", {}).get("grobid", {})

        # Resolve GROBID TEI disk cache directory (empty string = disabled).
        _cache_dir_str = str(grobid_cfg.get("tei_cache_dir", "") or "").strip()
        tei_cache_dir: Path | None = Path(_cache_dir_str).resolve() if _cache_dir_str else None

        cached_tei, pdf_digest = _grobid_cache_read(pdf_path, tei_cache_dir)

        if cached_tei is not None:
            # Cache hit: skip the GROBID HTTP call entirely.
            logger.info("GROBID cache hit for %s (%s); skipping API call", pdf_name, pdf_digest[:12])
            tei_xml = cached_tei
            plumber_blocks = extract_with_pdfplumber(str(pdf_path))
            logger.debug("pdfplumber returned %d blocks (cache-hit path)", len(plumber_blocks))
        else:
            # Cache miss: run GROBID and pdfplumber concurrently — they are
            # fully independent (different libraries, same read-only PDF file).
            grobid_kwargs: dict = dict(
                grobid_url=grobid_cfg.get("url", "http://localhost:8070"),
                timeout=int(grobid_cfg.get("timeout", 300)),
                consolidate_header=int(grobid_cfg.get("consolidate_header", 0)),
                consolidate_citations=int(grobid_cfg.get("consolidate_citations", 0)),
                generate_ids=bool(grobid_cfg.get("generate_ids", False)),
                segment_sentences=bool(grobid_cfg.get("segment_sentences", True)),
                include_raw_citations=bool(grobid_cfg.get("include_raw_citations", True)),
                include_raw_affiliations=bool(grobid_cfg.get("include_raw_affiliations", False)),
                tei_coordinates=bool(grobid_cfg.get("tei_coordinates", True)),
                max_retries=int(grobid_cfg.get("max_retries", 2)),
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                grobid_future = pool.submit(extract_with_grobid, str(pdf_path), **grobid_kwargs)
                plumber_future = pool.submit(extract_with_pdfplumber, str(pdf_path))

                try:
                    tei_xml, _ = grobid_future.result()
                    logger.debug("GROBID returned TEI XML: %d chars", len(tei_xml))
                    _grobid_cache_write(tei_xml, pdf_digest, tei_cache_dir)
                except Exception:
                    if grobid_failure_behavior == "manifest_fail":
                        raise
                    logger.warning(
                        "GROBID failed for %s; continuing with fallback mode", pdf_name
                    )
                    logger.debug("GROBID exception for %s", pdf_name, exc_info=True)
                    tei_xml = ""

                plumber_blocks = plumber_future.result()
                logger.debug("pdfplumber returned %d blocks", len(plumber_blocks))
        branches = [
            Candidate(source="grobid",     index=0, payload=tei_xml,        status=None),
            Candidate(source="pdfplumber",  index=1, payload=plumber_blocks, status=None),
        ]

    elif has_scanned and not ocr_enabled:
        # Scanned path with ocr=false: skip extraction, log WARNING
        logger.debug("Routing %s: scanned pages present + ocr=false -> skipping", pdf_name)
        for cls in page_classifications:
            if not cls.is_native:
                logger.warning(
                    "Skipping scanned page %d in '%s' — OCR is disabled (ocr=false)",
                    cls.page_index,
                    pdf_name,
                )

    else:
        # Scanned path with ocr=true: PaddleOCR (primary) + PyMuPDF OCR (secondary)
        logger.debug("Routing %s: OCR path (PaddleOCR + PyMuPDF)", pdf_name)
        paddle_blocks = extract_with_paddleocr(str(pdf_path))
        pymupdf_blocks, _ = extract_with_pymupdf(str(pdf_path))
        logger.debug(
            "paddleocr=%d blocks, pymupdf=%d blocks",
            len(paddle_blocks), len(pymupdf_blocks),
        )
        branches = [
            Candidate(source="paddleocr", index=0, payload=paddle_blocks,  status=None),
            Candidate(source="pymupdf",   index=1, payload=pymupdf_blocks, status=None),
        ]

    # ------------------------------------------------------------------
    # Step 3 — QC pipeline
    # ------------------------------------------------------------------
    _lexical_matcher = _get_lexical_matcher()
    _semantic_matcher = _get_semantic_matcher()
    logger.debug(
        "Running QC pipeline for %s with %d branches: %s",
        pdf_name, len(branches), [b.source for b in branches],
    )
    ctx = run_quality_control(
        branches,
        pdf_name,
        qc_config,
        exact_match_fn=_lexical_matcher.search,
        semantic_search_fn=_semantic_matcher.search,
    )
    if ctx.unified is not None and isinstance(ctx.unified.content, dict):
        ctx.unified.content["source_pdf_path"] = str(pdf_path)
        ctx.unified.content["grobid_tei_xml"] = tei_xml
        logger.debug(
            "QC unified content keys for %s: %s",
            pdf_name, sorted(ctx.unified.content.keys()),
        )

    # ------------------------------------------------------------------
    # Step 4 — Annotation chain (project W3C JSON-LD from unified record)
    # ------------------------------------------------------------------
    if ctx.unified is not None:
        annotation_records = w3c_annotation.project(ctx.unified)
        logger.debug(
            "W3C annotation projection produced %d records for %s",
            len(annotation_records), pdf_name,
        )
        jsonld = generate_w3c_jsonld(annotation_records)
        if not isinstance(ctx.unified.content, dict):
            ctx.unified.content = {}
        ctx.unified.content["annotations"] = jsonld

    logger.debug("build_qc_bundle complete for %s", pdf_name)
    return ctx
