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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf_extractor.extraction.GROBID import extract_with_grobid, parse_grobid_tei
from pdf_extractor.extraction.PyMuPDF import extract_with_pymupdf
from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber
from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr
from pdf_extractor.extraction import scan_detector
from pdf_extractor.extraction.scan_detector import PageScanClassification
from artifact_generation import generate_w3c_jsonld
from artifact_generation.w3c_annotation import project as w3c_project
from quality_control import QCBundle, run_quality_control
from quality_control.models import Candidate
from text_processing.base import TextProcessor

logger = logging.getLogger("pdf_extractor")


# ---------------------------------------------------------------------------
# Per-page routing result
# ---------------------------------------------------------------------------

@dataclass
class PageRoutingResult:
    """Routing decision for a single page.

    Attributes
    ----------
    page_index:
        0-based page number within the document.
    selected_extractor:
        The extractor pipeline used for this page.
        One of ``"grobid+pdfplumber"`` or ``"paddleocr+pymupdf"``.
    fallback_extractor:
        The fallback extractor pipeline (if any), or ``None``.
    routing_reason:
        Human-readable reason for the routing decision.
        Examples: ``"all_native"``, ``"stage_1_empty_text"``,
        ``"mixed_native_page"``, ``"mixed_scanned_page"``.
    classification:
        The :class:`~pdf_extractor.extraction.scan_detector.PageScanClassification`
        result for this page.
    """

    page_index: int
    selected_extractor: str  # "grobid+pdfplumber" | "paddleocr+pymupdf"
    fallback_extractor: str | None
    routing_reason: str
    classification: PageScanClassification


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
    # Step 1 — Resolve text processor + GROBID cache + dispatch parallel work
    # ------------------------------------------------------------------
    tp_cfg = qc_config.get(
        "text_processor",
        {"sentence_tokenizer": {"backend": "nltk_punkt"}},
    )
    _tp_class_path = tp_cfg.get("class", "text_processing.composite.DefaultTextProcessor")
    logger.debug("Loading text_processor class: %s", _tp_class_path)
    tp = _get_text_processor(_tp_class_path, tp_cfg)
    logger.debug("text_processor instance: %s (cached)", type(tp).__name__)

    grobid_cfg = qc_config.get("quality_control", {}).get("grobid", {})
    scan_cfg = qc_config.get("quality_control", {})

    # Resolve GROBID TEI disk cache directory (empty string = disabled).
    _cache_dir_str = str(grobid_cfg.get("tei_cache_dir", "") or "").strip()
    tei_cache_dir: Path | None = Path(_cache_dir_str).resolve() if _cache_dir_str else None

    # Check the cache BEFORE running scan_detector. A cache hit implies the PDF
    # is native (GROBID would have errored on a fully-scanned PDF the first time
    # round), so we can short-circuit scan_detector entirely. This saves
    # 1-5s per cache-hit PDF (scan_detector reads every page + runs clean_ocr).
    cached_tei, pdf_digest = _grobid_cache_read(pdf_path, tei_cache_dir)

    tei_xml = ""
    branches: list[Candidate] = []
    page_classifications: list = []
    page_routing_results: list[PageRoutingResult] = []

    if cached_tei is not None:
        # Cache hit: skip scan_detector AND the GROBID HTTP call.
        logger.info("GROBID cache hit for %s (%s); skipping API + scan_detector", pdf_name, pdf_digest[:12])
        tei_xml = cached_tei
        plumber_blocks = extract_with_pdfplumber(str(pdf_path))
        logger.debug("pdfplumber returned %d blocks (cache-hit path)", len(plumber_blocks))
        branches = [
            Candidate(source="grobid",     index=0, payload=tei_xml,        status=None),
            Candidate(source="pdfplumber", index=1, payload=plumber_blocks, status=None),
        ]
        # Cache hit implies all-native (GROBID would have failed on scanned).
        # Build routing results for all pages based on pdfplumber block count.
        _page_indices_from_plumber = sorted(set(b["page_index"] for b in plumber_blocks))
        page_routing_results = [
            PageRoutingResult(
                page_index=pi,
                selected_extractor="grobid+pdfplumber",
                fallback_extractor=None,
                routing_reason="all_native",
                classification=PageScanClassification(
                    page_index=pi, is_native=True,
                ),
            )
            for pi in _page_indices_from_plumber
        ]
    else:
        # Cache miss: run scan_detector concurrently with GROBID + pdfplumber.
        # scan_detector blocks GROBID dispatch in the old design even though it
        # only routes downstream — overlap them. If scan_detector says "scanned",
        # GROBID's response is discarded (rare in scientific corpora).
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
            # The QC pipeline + evidence_index re-parse the TEI for their
            # own consumers; the blocks computed in extract_with_grobid
            # would be discarded immediately. Skip the parse.
            parse_blocks=False,
        )

        def _run_scan_detector() -> list:
            try:
                import fitz as _fitz  # noqa: PLC0415 — lazy; optional (AGPL) dependency
            except ImportError:
                # PyMuPDF is an optional (AGPL) dependency. Without it, per-page
                # scan detection — and the OCR path it gates — is unavailable, so
                # treat every page as native and let the GROBID + pdfplumber path
                # handle the document. A genuinely scanned PDF then surfaces as low
                # extraction coverage in QC rather than being routed to OCR.
                import pdfplumber  # noqa: PLC0415
                logger.warning(
                    "PyMuPDF (fitz) not installed; skipping scan detection for %s "
                    "and treating all pages as native. Install the 'ocr' extra to "
                    "enable scan detection and OCR for scanned PDFs.",
                    pdf_name,
                )
                with pdfplumber.open(str(pdf_path)) as _pdf:
                    page_count = len(_pdf.pages)
                return [
                    scan_detector.PageScanClassification(page_index=i, is_native=True)
                    for i in range(page_count)
                ]
            d = _fitz.open(str(pdf_path))
            try:
                return [
                    scan_detector.classify_page(page, tp, scan_cfg, page_index=i)
                    for i, page in enumerate(d)
                ]
            finally:
                d.close()

        with ThreadPoolExecutor(max_workers=3) as pool:
            grobid_future = pool.submit(extract_with_grobid, str(pdf_path), **grobid_kwargs)
            plumber_future = pool.submit(extract_with_pdfplumber, str(pdf_path))
            scan_future = pool.submit(_run_scan_detector)

            page_classifications = scan_future.result()
            for cls in page_classifications:
                logger.debug(
                    "  scan page %d: native=%s, triggered_stages=%s, values=%s",
                    cls.page_index, cls.is_native, cls.triggered_stages, cls.stage_values,
                )

            all_native = all(c.is_native for c in page_classifications)
            all_scanned = all(not c.is_native for c in page_classifications)
            has_scanned = not all_native
            native_indices = {c.page_index for c in page_classifications if c.is_native}
            scanned_indices = {c.page_index for c in page_classifications if not c.is_native}
            logger.debug(
                "scan summary: all_native=%s, all_scanned=%s, native_pages=%d/%d",
                all_native, all_scanned,
                len(native_indices), len(page_classifications),
            )

            if all_native:
                # ---- All-native path: GROBID + pdfplumber ----
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
                    Candidate(source="pdfplumber", index=1, payload=plumber_blocks, status=None),
                ]
                # Build routing results: all pages native
                page_routing_results = [
                    PageRoutingResult(
                        page_index=c.page_index,
                        selected_extractor="grobid+pdfplumber",
                        fallback_extractor=None,
                        routing_reason="all_native",
                        classification=c,
                    )
                    for c in page_classifications
                ]
            else:
                # ---- Mixed or all-scanned path: per-page routing ----
                # For mixed PDFs, GROBID processes the full document but we
                # filter its output to native page indices only. PaddleOCR
                # already processes page-by-page so we filter to scanned indices.
                logger.debug(
                    "Per-page routing for %s: native_pages=%s, scanned_pages=%s",
                    pdf_name, sorted(native_indices), sorted(scanned_indices),
                )

                # Cancel speculative futures only if ALL pages are scanned
                if all_scanned:
                    for _f in (grobid_future, plumber_future):
                        _f.cancel()

                # --- Native page extraction (GROBID + pdfplumber) ---
                native_blocks: list = []
                if native_indices:
                    # GROBID processes full document; filter output to native pages
                    try:
                        tei_xml, _ = grobid_future.result()
                        logger.debug("GROBID returned TEI XML: %d chars (mixed path)", len(tei_xml))
                        _grobid_cache_write(tei_xml, pdf_digest, tei_cache_dir)
                    except Exception:
                        if grobid_failure_behavior == "manifest_fail":
                            raise
                        logger.warning(
                            "GROBID failed for %s (mixed path); continuing with pdfplumber-only for native pages",
                            pdf_name,
                        )
                        logger.debug("GROBID exception for %s", pdf_name, exc_info=True)
                        tei_xml = ""

                    plumber_blocks = plumber_future.result()
                    # Filter pdfplumber blocks to native page indices only
                    native_blocks = [
                        b for b in plumber_blocks if b["page_index"] in native_indices
                    ]
                    logger.debug(
                        "pdfplumber: %d total blocks, %d native-page blocks",
                        len(plumber_blocks), len(native_blocks),
                    )

                # --- Scanned page extraction (PaddleOCR + PyMuPDF) ---
                scanned_blocks: list = []
                if scanned_indices and ocr_enabled:
                    dpi_value: int = (
                        qc_config.get("quality_control", {})
                        .get("ocr", {})
                        .get("rasterization_dpi", 150)
                    )
                    paddle_blocks = extract_with_paddleocr(str(pdf_path), dpi=dpi_value)
                    pymupdf_blocks, _ = extract_with_pymupdf(str(pdf_path))
                    # Filter to scanned page indices only
                    scanned_blocks = [
                        b for b in paddle_blocks if b["page_index"] in scanned_indices
                    ]
                    scanned_pymupdf_blocks = [
                        b for b in pymupdf_blocks if b["page_index"] in scanned_indices
                    ]
                    logger.debug(
                        "OCR (scanned pages): paddleocr=%d blocks, pymupdf=%d blocks",
                        len(scanned_blocks), len(scanned_pymupdf_blocks),
                    )
                elif scanned_indices and not ocr_enabled:
                    # Scanned pages with ocr=false: log WARNING per page
                    logger.debug("Routing %s: scanned pages present + ocr=false -> skipping OCR", pdf_name)
                    for cls in page_classifications:
                        if not cls.is_native:
                            logger.warning(
                                "Skipping scanned page %d in '%s' — OCR is disabled (ocr=false)",
                                cls.page_index,
                                pdf_name,
                            )

                # --- Merge page-level results in original page order ---
                merged_blocks = sorted(
                    native_blocks + scanned_blocks,
                    key=lambda b: b["page_index"],
                )

                if merged_blocks or tei_xml:
                    # Build branches from merged results
                    branch_list: list[Candidate] = []
                    if tei_xml:
                        branch_list.append(
                            Candidate(source="grobid", index=0, payload=tei_xml, status=None)
                        )
                    if native_blocks or scanned_blocks:
                        # Merged blocks as the structural branch
                        branch_list.append(
                            Candidate(
                                source="pdfplumber" if native_blocks and not scanned_blocks else "paddleocr",
                                index=len(branch_list),
                                payload=merged_blocks,
                                status=None,
                            )
                        )
                    branches = branch_list

                # Build per-page routing results
                page_routing_results = []
                for c in page_classifications:
                    if c.is_native:
                        # Determine routing reason from classification
                        routing_reason = "mixed_native_page"
                        page_routing_results.append(PageRoutingResult(
                            page_index=c.page_index,
                            selected_extractor="grobid+pdfplumber",
                            fallback_extractor="paddleocr+pymupdf" if ocr_enabled else None,
                            routing_reason=routing_reason,
                            classification=c,
                        ))
                    else:
                        # Determine routing reason from triggered stages
                        if 1 in c.triggered_stages:
                            routing_reason = "stage_1_empty_text"
                        elif c.triggered_stages:
                            routing_reason = f"stages_{'_'.join(str(s) for s in c.triggered_stages)}"
                        else:
                            routing_reason = "classified_scanned"
                        page_routing_results.append(PageRoutingResult(
                            page_index=c.page_index,
                            selected_extractor="paddleocr+pymupdf" if ocr_enabled else "none",
                            fallback_extractor="grobid+pdfplumber" if native_indices else None,
                            routing_reason=routing_reason,
                            classification=c,
                        ))

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
        # Attach per-page routing metadata
        ctx.unified.content["page_routing"] = [
            {
                "page_index": r.page_index,
                "selected_extractor": r.selected_extractor,
                "fallback_extractor": r.fallback_extractor,
                "routing_reason": r.routing_reason,
            }
            for r in page_routing_results
        ]
        logger.debug(
            "QC unified content keys for %s: %s",
            pdf_name, sorted(ctx.unified.content.keys()),
        )

    # ------------------------------------------------------------------
    # Step 4 — Annotation chain (project W3C JSON-LD from unified record)
    # ------------------------------------------------------------------
    if ctx.unified is not None:
        annotation_records = w3c_project(ctx.unified)
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
