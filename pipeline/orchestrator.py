"""Pipeline orchestrator — exposes run_pipeline() and module-level runtime constants."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List

from .extraction_pipeline import build_qc_bundle
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
    logger.debug(
        "run_pipeline: %d PDFs, effective_concurrency=%d, effective_prewarm=%s, "
        "global_api_limit=%d, num_chunks=%d",
        len(pdf_paths), effective_concurrency, effective_prewarm,
        GLOBAL_API_LIMIT, NUM_CHUNKS,
    )

    # Propagate runtime overrides into the config dict passed to each PDF worker.
    runtime_config = {**_openai_config, "enable_cache_prewarm": effective_prewarm}
    runtime_config.update(_qc_config.get("quality_control", {}).get("grobid_integration", {}))
    runtime_config["addons"] = _qc_config.get("quality_control", {}).get("addons", {})
    logger.debug(
        "Runtime config keys: %s; addons enabled: %s",
        sorted(runtime_config.keys()),
        {k: v.get("enabled", False) for k, v in runtime_config.get("addons", {}).items() if isinstance(v, dict)},
    )

    chunk_fields = load_chunk_fields()
    field_lookup = _build_field_lookup()
    logger.debug(
        "Loaded extraction map: %d chunks, %d total fields",
        len(chunk_fields), len(field_lookup),
    )
    for chunk_num in sorted(chunk_fields.keys()):
        logger.debug(
            "  chunk %d: %d fields (indices %s)",
            chunk_num,
            len(chunk_fields[chunk_num]),
            sorted(f.get("field_index") for f in chunk_fields[chunk_num]),
        )

    manifest = load_manifest()
    manifest_lock = asyncio.Lock()
    api_semaphore = asyncio.Semaphore(GLOBAL_API_LIMIT)
    pdf_semaphore = asyncio.Semaphore(effective_concurrency)
    logger.debug(
        "Manifest loaded with %d previously-seen entries; semaphores ready",
        len(manifest),
    )

    async def _bounded(pdf_path: Path):
        async with pdf_semaphore:
            pdf_name = pdf_path.stem
            logger.debug("Acquired pdf_semaphore for %s (begin QC pipeline)", pdf_name)
            try:
                qc_context = await asyncio.to_thread(
                    build_qc_bundle, pdf_path, pdf_name, _qc_config
                )
            except Exception as exc:
                logger.error(f"FAIL  {pdf_name} -- QC pipeline: {exc}")
                logger.debug("QC pipeline exception for %s", pdf_name, exc_info=True)
                async with manifest_lock:
                    manifest[pdf_name] = {"status": "failed_qc_pipeline", "error": str(exc)}
                    save_manifest(manifest)
                return None
            logger.debug("QC pipeline complete for %s; entering pdf_processor", pdf_name)
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
            logger.debug("Unhandled exception for %s", pdf_path.name, exc_info=result)
        elif result is not None:
            output.append({"pdf": pdf_path.name, "fields": result})
    logger.debug(
        "run_pipeline done: %d/%d PDFs produced output",
        len(output), len(pdf_paths),
    )

    return output
