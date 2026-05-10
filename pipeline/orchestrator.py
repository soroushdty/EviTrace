"""Pipeline orchestrator — exposes run_pipeline() and module-level runtime constants."""
import asyncio
from pathlib import Path
from typing import List

from pdf_extractor.extraction.GROBID import extract_with_grobid
from pdf_extractor.extraction.PyMuPDF import extract_with_pymupdf
from pdf_extractor.extraction.pdfplumber import extract_with_pdfplumber
from pdf_extractor.extraction.PaddleOCR import extract_with_paddleocr
from pdf_extractor.extraction import scan_detector
from quality_control import QCContext, run_quality_control
from quality_control.models import BranchOutput
from utils.config_utils import load_openai_config, load_qc_config
from utils.logging_utils import get_logger
from utils.text_processor import TextProcessor

from .extraction_map import load_chunk_fields, _build_field_lookup
from . import pdf_processor
from .manifest import load_manifest, save_manifest

logger = get_logger(__name__)

_openai_config = load_openai_config()
_qc_config = load_qc_config()

CHUNK_MODEL: str = _openai_config["chunk_model"]
DOMAIN_TO_CHUNK: dict[int, int] = _openai_config["domain_to_chunk"]
ENABLE_CACHE_PREWARM: bool = _openai_config["enable_cache_prewarm"]
GLOBAL_API_LIMIT: int = _openai_config["global_api_limit"]
NUM_CHUNKS: int = _openai_config["num_chunks"]
PDF_CONCURRENCY: int = _openai_config["pdf_concurrency"]
PREWARM_SYNTHESIS_IF_MODEL_DIFF: bool = _openai_config["prewarm_synthesis_if_model_diff"]
SYNTHESIS_MODEL: str = _openai_config["synthesis_model"]


def _build_qc_context(
    pdf_path: Path,
    pdf_name: str,
    qc_config: dict,
) -> QCContext:
    """Run per-page scan detection, route to correct extractors, and run the
    full QC pipeline for one PDF.

    Per-page routing:
    - All pages native → GROBID (semantic authority) + pdfplumber (structural
      authority); PyMuPDF font metadata stored in ctx.unified.content.
    - Any page scanned + ocr=true → PaddleOCR (primary) + PyMuPDF OCR
      (secondary cross-validation) fed into GROBID downstream.
    - Any page scanned + ocr=false → skip extraction, log WARNING, no branch.

    All backend callables are resolved through sys.modules[__name__] at call
    time so that unittest.mock patches applied to this module's attributes are
    always honoured, regardless of whether the caller holds a reference to an
    older module object (e.g. when tests delete and re-import the module).
    """
    import sys as _sys

    # Resolve every patchable name through the *current* module object.
    # Tests use patch("pipeline.orchestrator.<name>", ...) which replaces the
    # attribute on sys.modules["pipeline.orchestrator"].  By looking up names
    # here we always see the patched version even when this function object
    # was imported from an older module instance.
    _mod = _sys.modules[__name__]

    def _resolve(name: str, import_fn):
        """Return the module attribute if present, else import and cache it."""
        obj = getattr(_mod, name, None)
        if obj is None:
            obj = import_fn()
            setattr(_mod, name, obj)
        return obj

    _extract_with_grobid = _resolve(
        "extract_with_grobid",
        lambda: __import__(
            "pdf_extractor.extraction.GROBID", fromlist=["extract_with_grobid"]
        ).extract_with_grobid,
    )
    _extract_with_pymupdf = _resolve(
        "extract_with_pymupdf",
        lambda: __import__(
            "pdf_extractor.extraction.PyMuPDF", fromlist=["extract_with_pymupdf"]
        ).extract_with_pymupdf,
    )
    _extract_with_pdfplumber = _resolve(
        "extract_with_pdfplumber",
        lambda: __import__(
            "pdf_extractor.extraction.pdfplumber", fromlist=["extract_with_pdfplumber"]
        ).extract_with_pdfplumber,
    )
    _extract_with_paddleocr = _resolve(
        "extract_with_paddleocr",
        lambda: __import__(
            "pdf_extractor.extraction.PaddleOCR", fromlist=["extract_with_paddleocr"]
        ).extract_with_paddleocr,
    )
    _scan_detector = _resolve(
        "scan_detector",
        lambda: __import__(
            "pdf_extractor.extraction", fromlist=["scan_detector"]
        ).scan_detector,
    )
    _TextProcessor = _resolve(
        "TextProcessor",
        lambda: __import__(
            "utils.text_processor", fromlist=["TextProcessor"]
        ).TextProcessor,
    )
    _run_quality_control = _resolve(
        "run_quality_control",
        lambda: __import__(
            "quality_control", fromlist=["run_quality_control"]
        ).run_quality_control,
    )

    grobid_failure_behavior = (
        qc_config.get("quality_control", {})
        .get("grobid_integration", {})
        .get("failure_behavior", "fallback")
    )
    ocr_enabled: bool = bool(qc_config.get("ocr", True))

    # ------------------------------------------------------------------
    # Step 1 — Per-page scan detection
    # ------------------------------------------------------------------
    # Use the text_processor config from qc_config if present; fall back to
    # nltk_punkt which lazy-loads on first tokenize_sentences() call so that
    # construction never fails when NLP packages are absent.  classify_page()
    # only calls clean_ocr(), so the sentence segmenter is never invoked here.
    tp_cfg = qc_config.get(
        "text_processor",
        {"sentence_tokenizer": {"backend": "nltk_punkt"}},
    )
    tp = _TextProcessor(config=tp_cfg)
    import fitz  # PyMuPDF — lazy import, no import-time side effect
    doc = fitz.open(str(pdf_path))
    try:
        pages = list(doc)
        scan_cfg = qc_config.get("quality_control", {})
        page_classifications = [
            _scan_detector.classify_page(page, tp, scan_cfg, page_index=i)
            for i, page in enumerate(pages)
        ]
    finally:
        doc.close()

    all_native = all(c.is_native for c in page_classifications)
    has_scanned = not all_native

    # ------------------------------------------------------------------
    # Step 2 — Route to correct extractors based on page classifications
    # ------------------------------------------------------------------
    tei_xml = ""
    branches: list[BranchOutput] = []

    if all_native:
        # Native path: GROBID (semantic) + pdfplumber (structural)
        try:
            tei_xml, _ = _extract_with_grobid(str(pdf_path))
        except Exception:
            if grobid_failure_behavior == "manifest_fail":
                raise
            logger.warning("GROBID failed for %s; continuing with fallback mode", pdf_name)
            tei_xml = ""

        plumber_blocks = _extract_with_pdfplumber(str(pdf_path))
        branches = [
            BranchOutput(extractor="grobid",    branch=0, payload=tei_xml,        status=None),
            BranchOutput(extractor="pdfplumber", branch=1, payload=plumber_blocks, status=None),
        ]

    elif has_scanned and not ocr_enabled:
        # Scanned path with ocr=false: skip extraction, log WARNING
        for cls in page_classifications:
            if not cls.is_native:
                logger.warning(
                    "Skipping scanned page %d in '%s' — OCR is disabled (ocr=false)",
                    cls.page_index,
                    pdf_name,
                )
        # No extraction branches produced for scanned pages when ocr=false

    else:
        # Scanned path with ocr=true: PaddleOCR (primary) + PyMuPDF OCR (secondary)
        paddle_blocks = _extract_with_paddleocr(str(pdf_path))
        pymupdf_blocks, _ = _extract_with_pymupdf(str(pdf_path))
        branches = [
            BranchOutput(extractor="paddleocr", branch=0, payload=paddle_blocks,  status=None),
            BranchOutput(extractor="pymupdf",   branch=1, payload=pymupdf_blocks, status=None),
        ]

    # ------------------------------------------------------------------
    # Step 3 — QC pipeline
    # ------------------------------------------------------------------
    ctx = _run_quality_control(branches, pdf_name, qc_config)
    if ctx.unified is not None and isinstance(ctx.unified.content, dict):
        ctx.unified.content["source_pdf_path"] = str(pdf_path)
        ctx.unified.content["grobid_tei_xml"] = tei_xml
    return ctx


async def run_pipeline(
    pdf_paths: List[Path],
    *,
    pdf_concurrency: int | None = None,
    enable_cache_prewarm: bool | None = None,
) -> List[dict]:
    """Process all PDFs with parallel workers.

    Args:
        pdf_paths:           PDFs to process.
        pdf_concurrency:     Override PDF_CONCURRENCY from config. None = use config.
        enable_cache_prewarm: Override ENABLE_CACHE_PREWARM from config. None = use config.

    Returns:
        List of {"pdf": filename, "fields": [...]} for every successful paper.
    """
    effective_concurrency = pdf_concurrency if pdf_concurrency is not None else PDF_CONCURRENCY
    effective_prewarm = enable_cache_prewarm if enable_cache_prewarm is not None else ENABLE_CACHE_PREWARM

    # Propagate runtime overrides into the config dict passed to each PDF worker.
    runtime_config = {**_openai_config, "enable_cache_prewarm": effective_prewarm}
    runtime_config.update(_qc_config.get("quality_control", {}).get("grobid_integration", {}))
    runtime_config["addons"] = _qc_config.get("quality_control", {}).get("addons", {})

    chunk_fields = load_chunk_fields()
    field_lookup = _build_field_lookup()
    manifest = load_manifest()
    manifest_lock = asyncio.Lock()
    api_semaphore = asyncio.Semaphore(GLOBAL_API_LIMIT)
    pdf_semaphore = asyncio.Semaphore(effective_concurrency)

    async def _bounded(pdf_path: Path):
        async with pdf_semaphore:
            pdf_name = pdf_path.stem
            try:
                qc_context = await asyncio.to_thread(
                    _build_qc_context, pdf_path, pdf_name, _qc_config
                )
            except Exception as exc:
                logger.error(f"FAIL  {pdf_name} -- QC pipeline: {exc}")
                async with manifest_lock:
                    manifest[pdf_name] = {"status": "failed_qc_pipeline", "error": str(exc)}
                    save_manifest(manifest)
                return None
            return await pdf_processor.process_pdf(
                qc_context, chunk_fields, field_lookup,
                api_semaphore, manifest, manifest_lock, runtime_config,
            )

    results = await asyncio.gather(
        *[_bounded(p) for p in pdf_paths],
        return_exceptions=True,
    )

    output: list[dict] = []
    for pdf_path, result in zip(pdf_paths, results):
        if isinstance(result, Exception):
            logger.error(f"Unhandled error for {pdf_path.name}: {result}")
        elif result is not None:
            output.append({"pdf": pdf_path.name, "fields": result})

    return output
