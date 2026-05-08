"""Pipeline orchestrator — exposes run_pipeline() and module-level runtime constants."""
import asyncio
from pathlib import Path
from typing import List

from pdf_extractor.extraction.GROBID import extract_with_grobid
from pdf_extractor.extraction.PyMuPDF import extract_with_pymupdf
from quality_control import QCContext, run_quality_control
from quality_control.models import BranchOutput
from utils.config_utils import load_openai_config, load_qc_config
from utils.logging_utils import get_logger

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
    """Run GROBID + PyMuPDF extraction and full QC pipeline for one PDF."""
    tei_xml, _ = extract_with_grobid(str(pdf_path))
    pymupdf_blocks, _ = extract_with_pymupdf(str(pdf_path))
    branches = [
        BranchOutput(extractor="grobid",  branch=0, payload=tei_xml,       status=None),
        BranchOutput(extractor="pymupdf", branch=1, payload=pymupdf_blocks, status=None),
    ]
    return run_quality_control(branches, pdf_name, qc_config)


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
