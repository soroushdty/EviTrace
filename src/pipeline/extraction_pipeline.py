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
import json
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
# Block provenance
# ---------------------------------------------------------------------------

#: Extractor names whose blocks are OCR-derived.  This is the single place
#: that knows which sources are OCR; ``quality_control`` never consults it and
#: only reads the ``ocr_derived`` boolean stamped on each block.
OCR_SOURCES: frozenset[str] = frozenset({"paddleocr"})


def _tag_blocks(blocks: list, source: str, ocr_derived: bool) -> list[dict]:
    """Return copies of *blocks* stamped with ``source`` and ``ocr_derived``.

    Every block handed to quality control passes through here exactly once,
    so downstream consumers (reconciler, annotation generator) can rely on
    both keys being present and can read ``ocr_derived`` as a plain boolean
    instead of re-deriving it from the extractor name.  Shallow copies are
    returned so the extractor's own lists are never mutated; all existing
    keys (including PaddleOCR extras such as ``rasterization_dpi`` and
    ``ocr_confidence``) are preserved.
    """
    return [{**b, "source": source, "ocr_derived": ocr_derived} for b in blocks]


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


# ---------------------------------------------------------------------------
# Page-classification sidecar (PageClassificationCache)
# ---------------------------------------------------------------------------
# Beside every cached TEI lives ``{digest}.pages.json`` holding the per-page
# scan classification computed on the first run. On a TEI cache hit the
# sidecar is read back so scanned pages still route to OCR without opening a
# single page for classification; it is keyed by PDF digest only, so it is
# written whenever classifications were computed, whether or not GROBID
# succeeded.

_PAGE_CLASS_CACHE_VERSION = 1


def _scan_detection_config_hash(qc_config: dict) -> str:
    """SHA-256 of the ``quality_control.scan_detection`` subsection.

    Stored in the sidecar so a threshold change invalidates persisted
    classifications: ``sha256(json.dumps(section, sort_keys=True))``.
    """
    section = qc_config.get("quality_control", {}).get("scan_detection", {})
    encoded = json.dumps(section, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _page_class_cache_path(digest: str, cache_dir: Path) -> Path:
    return cache_dir / f"{digest}.pages.json"


def _page_class_cache_read(
    digest: str, cache_dir: Path | None, config_hash: str
) -> list[PageScanClassification] | None:
    """Return the persisted classifications for *digest*, or ``None``.

    ``None`` (a sidecar miss) is returned when the cache is disabled, the
    sidecar is absent or unreadable (I/O error, invalid JSON, malformed
    payload), its ``version`` is not :data:`_PAGE_CLASS_CACHE_VERSION`, or
    its ``scan_detection_config_hash`` differs from *config_hash*.  Every
    miss is logged at INFO with the reason; the caller then recomputes.
    """
    if cache_dir is None or not digest:
        return None
    sidecar = _page_class_cache_path(digest, cache_dir)
    if not sidecar.exists():
        logger.info("Page-classification sidecar absent for %s; classifying pages", digest[:12])
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        version = payload["version"]
        stored_hash = payload["scan_detection_config_hash"]
        pages = payload["pages"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.info(
            "Page-classification sidecar unreadable for %s (%s: %s); classifying pages",
            digest[:12], type(exc).__name__, exc,
        )
        return None
    if version != _PAGE_CLASS_CACHE_VERSION:
        logger.info(
            "Page-classification sidecar version mismatch for %s (found %r, expected %r); classifying pages",
            digest[:12], version, _PAGE_CLASS_CACHE_VERSION,
        )
        return None
    if stored_hash != config_hash:
        logger.info(
            "Page-classification sidecar scan_detection config hash mismatch for %s; classifying pages",
            digest[:12],
        )
        return None
    try:
        classifications = [
            PageScanClassification(
                page_index=int(p["page_index"]),
                is_native=bool(p["is_native"]),
                triggered_stages=[int(st) for st in p["triggered_stages"]],
                stage_values={str(k): float(v) for k, v in p["stage_values"].items()},
            )
            for p in pages
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.info(
            "Page-classification sidecar unreadable for %s (malformed pages: %s); classifying pages",
            digest[:12], exc,
        )
        return None
    logger.info(
        "Page-classification sidecar hit for %s: %d pages (%d scanned)",
        digest[:12], len(classifications), sum(1 for c in classifications if not c.is_native),
    )
    return classifications


def _page_class_cache_write(
    classifications: list[PageScanClassification],
    digest: str,
    cache_dir: Path | None,
    config_hash: str,
) -> None:
    """Persist *classifications* beside the TEI cache; never raises.

    No-op when the cache is disabled (*cache_dir* is ``None``) or *digest*
    is empty (the PDF could not be hashed). I/O failures are logged at
    WARNING and otherwise ignored so a read-only cache directory cannot
    fail the document.
    """
    if cache_dir is None or not digest:
        return
    payload = {
        "version": _PAGE_CLASS_CACHE_VERSION,
        "scan_detection_config_hash": config_hash,
        "pages": [
            {
                "page_index": c.page_index,
                "is_native": c.is_native,
                "triggered_stages": list(c.triggered_stages),
                "stage_values": dict(c.stage_values),
            }
            for c in classifications
        ],
    }
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _page_class_cache_path(digest, cache_dir).write_text(
            json.dumps(payload, sort_keys=True), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Page-classification sidecar write failed for %s: %s", digest[:12], exc)


# ---------------------------------------------------------------------------
# Classification -> branches (RoutingUnifier)
# ---------------------------------------------------------------------------

def _build_branches_for_classifications(
    pdf_path: Path,
    tei_xml: str,
    plumber_blocks: list[dict],
    classifications: list[PageScanClassification],
    qc_config: dict,
    *,
    from_cache: bool,
) -> tuple[list[Candidate], list[PageRoutingResult]]:
    """Turn per-page classifications into QC branches and routing results.

    This is the single place that maps a classification list onto extractor
    branches: native/scanned index sets, OCR extraction when scanned pages
    exist and ``ocr=true``, block tagging via :func:`_tag_blocks`, the
    page-ordered merge, ``Candidate`` naming and the per-page
    :class:`PageRoutingResult` list.  It is the one call site both cache
    paths of :func:`build_qc_bundle` are meant to share so a document routes
    identically whether its TEI was freshly parsed or read back from disk.

    Parameters
    ----------
    pdf_path:
        Path to the PDF; handed to the OCR backends and used in log lines.
    tei_xml:
        GROBID TEI XML for the document, or ``""`` when GROBID produced
        nothing (failed with ``failure_behavior=fallback``, or was skipped
        because no page is native).
    plumber_blocks:
        Raw, untagged pdfplumber blocks for the whole document (``[]`` when
        pdfplumber was skipped).  Tagged and, on the scanned path, filtered
        to native pages here; the caller's list is never mutated.
    classifications:
        One :class:`PageScanClassification` per page, in page order.
    qc_config:
        Loaded QC config; reads ``ocr`` and
        ``quality_control.ocr.rasterization_dpi``.
    from_cache:
        Whether *tei_xml* came from the TEI disk cache.  Affects logging
        only: the returned branches and routing results are a pure function
        of the other inputs.

    Returns
    -------
    tuple[list[Candidate], list[PageRoutingResult]]
        ``(branches, page_routing_results)``.

        * All pages native: ``[grobid, pdfplumber]`` (the grobid branch is
          kept even when *tei_xml* is empty so a GROBID fallback still
          surfaces as an empty grobid branch), every page ``all_native``.
        * Any page scanned: ``[grobid]`` only if *tei_xml* is non-empty,
          then one structural branch holding the page-ordered merge of
          native pdfplumber blocks and scanned PaddleOCR blocks, named
          ``"pdfplumber"`` when no OCR block was produced and
          ``"paddleocr"`` otherwise.  With ``ocr=false`` the scanned pages
          are skipped with a WARNING each and routed to ``"none"``.
    """
    doc_name = pdf_path.stem
    ocr_enabled: bool = bool(qc_config.get("ocr", True))
    logger.debug(
        "Building branches for %s from %s classifications (%d pages)",
        doc_name, "cached" if from_cache else "fresh", len(classifications),
    )

    all_native = all(c.is_native for c in classifications)
    all_scanned = all(not c.is_native for c in classifications)
    native_indices = {c.page_index for c in classifications if c.is_native}
    scanned_indices = {c.page_index for c in classifications if not c.is_native}
    logger.debug(
        "scan summary: all_native=%s, all_scanned=%s, native_pages=%d/%d",
        all_native, all_scanned,
        len(native_indices), len(classifications),
    )

    if all_native:
        # ---- All-native path: GROBID + pdfplumber ----
        tagged_blocks = _tag_blocks(plumber_blocks, "pdfplumber", "pdfplumber" in OCR_SOURCES)
        logger.debug("pdfplumber returned %d blocks", len(tagged_blocks))
        branches = [
            Candidate(source="grobid",     index=0, payload=tei_xml,       status=None),
            Candidate(source="pdfplumber", index=1, payload=tagged_blocks, status=None),
        ]
        page_routing_results = [
            PageRoutingResult(
                page_index=c.page_index,
                selected_extractor="grobid+pdfplumber",
                fallback_extractor=None,
                routing_reason="all_native",
                classification=c,
            )
            for c in classifications
        ]
        return branches, page_routing_results

    # ---- Mixed or all-scanned path: per-page routing ----
    # For mixed PDFs, GROBID processes the full document but we filter the
    # structural output to native page indices only. PaddleOCR already
    # processes page-by-page so we filter to scanned indices.
    logger.debug(
        "Per-page routing for %s: native_pages=%s, scanned_pages=%s",
        doc_name, sorted(native_indices), sorted(scanned_indices),
    )

    # --- Native page extraction (GROBID + pdfplumber) ---
    native_blocks: list = []
    if native_indices:
        native_blocks = _tag_blocks(
            [b for b in plumber_blocks if b["page_index"] in native_indices],
            "pdfplumber", "pdfplumber" in OCR_SOURCES,
        )
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
        scanned_blocks = _tag_blocks(
            [b for b in paddle_blocks if b["page_index"] in scanned_indices],
            "paddleocr", "paddleocr" in OCR_SOURCES,
        )
        scanned_pymupdf_blocks = [
            b for b in pymupdf_blocks if b["page_index"] in scanned_indices
        ]
        logger.debug(
            "OCR (scanned pages): paddleocr=%d blocks, pymupdf=%d blocks",
            len(scanned_blocks), len(scanned_pymupdf_blocks),
        )
    elif scanned_indices and not ocr_enabled:
        # Scanned pages with ocr=false: log WARNING per page
        logger.debug("Routing %s: scanned pages present + ocr=false -> skipping OCR", doc_name)
        for cls in classifications:
            if not cls.is_native:
                logger.warning(
                    "Skipping scanned page %d in '%s' — OCR is disabled (ocr=false)",
                    cls.page_index,
                    doc_name,
                )

    # --- Merge page-level results in original page order ---
    merged_blocks = sorted(
        native_blocks + scanned_blocks,
        key=lambda b: b["page_index"],
    )

    branches: list[Candidate] = []
    if tei_xml:
        branches.append(
            Candidate(source="grobid", index=0, payload=tei_xml, status=None)
        )
    if native_blocks or scanned_blocks:
        # Merged blocks as the structural branch
        branches.append(
            Candidate(
                source="pdfplumber" if native_blocks and not scanned_blocks else "paddleocr",
                index=len(branches),
                payload=merged_blocks,
                status=None,
            )
        )

    # Build per-page routing results
    page_routing_results: list[PageRoutingResult] = []
    for c in classifications:
        if c.is_native:
            page_routing_results.append(PageRoutingResult(
                page_index=c.page_index,
                selected_extractor="grobid+pdfplumber",
                fallback_extractor="paddleocr+pymupdf" if ocr_enabled else None,
                routing_reason="mixed_native_page",
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

    return branches, page_routing_results


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

    TEI cache
    ---------
    With ``quality_control.grobid.tei_cache_dir`` set, the GROBID TEI is
    cached by PDF SHA-256 and the per-page classification is persisted
    beside it (``{digest}.pages.json``). A cache hit skips the GROBID call
    and reads the sidecar instead of opening pages; both paths then route
    through :func:`_build_branches_for_classifications`, so a hit on a
    document with scanned pages runs OCR exactly like a miss.

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

    # Check the TEI cache BEFORE running scan_detector: a hit skips the GROBID
    # HTTP call, and the page-classification sidecar written beside the TEI
    # lets the hit path route scanned pages to OCR without opening a page.
    cached_tei, pdf_digest = _grobid_cache_read(pdf_path, tei_cache_dir)
    scan_config_hash = _scan_detection_config_hash(qc_config)

    tei_xml = ""
    branches: list[Candidate] = []
    page_classifications: list = []
    page_routing_results: list[PageRoutingResult] = []

    def _classify_pages(fitz_module) -> list:
        """Run scan_detector over every page; the only place pages are opened."""
        d = fitz_module.open(str(pdf_path))
        try:
            return [
                scan_detector.classify_page(page, tp, scan_cfg, page_index=i)
                for i, page in enumerate(d)
            ]
        finally:
            d.close()

    if cached_tei is not None:
        # Cache hit: skip the GROBID HTTP call; classification comes from the
        # sidecar (no page opened), else is recomputed once and persisted.
        logger.info("GROBID cache hit for %s (%s); skipping API call", pdf_name, pdf_digest[:12])
        tei_xml = cached_tei
        plumber_blocks = extract_with_pdfplumber(str(pdf_path))
        logger.debug("pdfplumber returned %d blocks (cache-hit path)", len(plumber_blocks))

        page_classifications = _page_class_cache_read(pdf_digest, tei_cache_dir, scan_config_hash)
        if page_classifications is None:
            try:
                import fitz as _fitz  # noqa: PLC0415 — lazy; optional (AGPL) dependency
            except ImportError:
                _fitz = None
            if _fitz is not None:
                page_classifications = _classify_pages(_fitz)
                _page_class_cache_write(
                    page_classifications, pdf_digest, tei_cache_dir, scan_config_hash
                )
            else:
                # 6.5: classification can be neither reused nor determined.
                # Proceed with the conservative all-native default over the
                # pages pdfplumber saw rather than aborting the document; the
                # guess is deliberately not persisted.
                logger.error(
                    "Cannot determine page classification for %s on TEI cache hit: "
                    "sidecar unusable and PyMuPDF (fitz) not installed. Proceeding "
                    "with the all-native default; scanned pages (if any) will not "
                    "be OCR'd. Install the 'ocr' extra or delete the cached TEI to "
                    "force a full re-parse.",
                    pdf_name,
                )
                page_classifications = [
                    PageScanClassification(page_index=pi, is_native=True)
                    for pi in sorted({b["page_index"] for b in plumber_blocks})
                ]

        n_scanned = sum(1 for c in page_classifications if not c.is_native)
        if n_scanned:
            logger.info(
                "TEI cache hit for %s: %d/%d pages classified scanned; routing them to OCR",
                pdf_name, n_scanned, len(page_classifications),
            )
        branches, page_routing_results = _build_branches_for_classifications(
            pdf_path, tei_xml, plumber_blocks, page_classifications, qc_config,
            from_cache=True,
        )
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

        # Set to False by _run_scan_detector when PyMuPDF is missing and every
        # page was labelled native by default — a guess that must not be
        # persisted to the sidecar. (A holder rather than a return value so
        # the scan future keeps yielding a plain classification list.)
        classification_determined: list[bool] = [True]

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
                classification_determined[0] = False
                return [
                    scan_detector.PageScanClassification(page_index=i, is_native=True)
                    for i in range(page_count)
                ]
            return _classify_pages(_fitz)

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
            if classification_determined[0]:
                # Keyed by PDF digest, independent of whether GROBID succeeds.
                _page_class_cache_write(
                    page_classifications, pdf_digest, tei_cache_dir, scan_config_hash
                )

            has_scanned = any(not c.is_native for c in page_classifications)
            has_native = any(c.is_native for c in page_classifications)
            only_scanned = has_scanned and not has_native

            plumber_blocks: list = []
            if only_scanned:
                # Every page is scanned: GROBID and pdfplumber output would
                # be discarded, so cancel the speculative futures.
                for _f in (grobid_future, plumber_future):
                    _f.cancel()
            else:
                # At least one native page (or an empty document): GROBID
                # processes the full document; the structural blocks are
                # filtered to native pages by the branch builder below.
                try:
                    tei_xml, _ = grobid_future.result()
                    logger.debug(
                        "GROBID returned TEI XML: %d chars%s",
                        len(tei_xml), " (mixed path)" if has_scanned else "",
                    )
                    _grobid_cache_write(tei_xml, pdf_digest, tei_cache_dir)
                except Exception:
                    if grobid_failure_behavior == "manifest_fail":
                        raise
                    if has_scanned:
                        logger.warning(
                            "GROBID failed for %s (mixed path); continuing with pdfplumber-only for native pages",
                            pdf_name,
                        )
                    else:
                        logger.warning(
                            "GROBID failed for %s; continuing with fallback mode", pdf_name
                        )
                    logger.debug("GROBID exception for %s", pdf_name, exc_info=True)
                    tei_xml = ""
                plumber_blocks = plumber_future.result()

            # Build branches (and run OCR, when needed) inside the executor
            # block so OCR on an all-scanned document overlaps the cancelled
            # but possibly still-running GROBID future instead of waiting
            # for the pool to drain first.
            branches, page_routing_results = _build_branches_for_classifications(
                pdf_path, tei_xml, plumber_blocks, page_classifications, qc_config,
                from_cache=False,
            )

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
        # Paper-scoped document source: it becomes the annotation target
        # source and the document component of every (deterministic)
        # annotation id, so identical text in two papers never collides.
        jsonld = generate_w3c_jsonld(
            annotation_records, base_uri=f"urn:evitrace:document:{pdf_name}"
        )
        if not isinstance(ctx.unified.content, dict):
            ctx.unified.content = {}
        ctx.unified.content["annotations"] = jsonld

    logger.debug("build_qc_bundle complete for %s", pdf_name)
    return ctx
