"""Per-PDF processing logic."""
import asyncio
import json
from pathlib import Path
from typing import Optional

from agents.openai.api_client import extract_chunk, warm_pdf_cache
from pdf_extractor import extract_pdf_text
from .validator import reconstruct_fields
from utils.logging_utils import get_logger
from utils.path_utils import OUTPUT_DIR
from .manifest import save_manifest

logger = get_logger(__name__)


def _save_pdf_output(pdf_name: str, fields: list[dict]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / f"{pdf_name}.extracted.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=2)
    logger.info(f"Saved -> {out.name}")


def _load_completed_result(pdf_name: str, manifest: dict) -> Optional[list[dict]]:
    """Return cached extraction result if this PDF is already marked complete."""
    if manifest.get(pdf_name, {}).get("status") != "complete":
        return None
    out = OUTPUT_DIR / f"{pdf_name}.extracted.json"
    if out.exists():
        with open(out, encoding="utf-8") as f:
            return json.load(f)
    return None


async def _extract_text(
    pdf_path: Path,
    pdf_name: str,
    manifest: dict,
    manifest_lock: asyncio.Lock,
) -> Optional[str]:
    """Extract raw text from a PDF file; record failure in manifest and return None on error."""
    try:
        pdf_text = extract_pdf_text(pdf_path)
        logger.info(f"TEXT  {pdf_name} ({len(pdf_text):,} chars)")
        return pdf_text
    except Exception as exc:
        logger.error(f"FAIL  {pdf_name} -- PDF extraction: {exc}")
        async with manifest_lock:
            manifest[pdf_name] = {"status": "failed_pdf_extraction", "error": str(exc)}
            save_manifest(manifest)
        return None


async def _run_parallel_chunks(
    pdf_text: str,
    chunk_fields: dict[int, list[dict]],
    api_semaphore: asyncio.Semaphore,
    pdf_name: str,
    num_chunks: int,
    enable_prewarm: bool,
    chunk_model: str,
    synthesis_model: str,
    prewarm_synthesis_diff: bool,
    manifest: dict,
    manifest_lock: asyncio.Lock,
) -> Optional[list]:
    """Run extraction chunks 1–(num_chunks-1) in parallel.

    Also fires a synthesis-model warmup task concurrently if configured.
    Returns the list of raw chunk results, or None if any chunk failed.
    """
    if enable_prewarm:
        await warm_pdf_cache(pdf_text, api_semaphore, pdf_name=pdf_name, model=chunk_model)

    extraction_chunks = list(range(1, num_chunks))
    chunk_tasks = [
        extract_chunk(i, pdf_text, chunk_fields[i], api_semaphore, pdf_name=pdf_name)
        for i in extraction_chunks
    ]

    synthesis_warmup_task: asyncio.Task[bool] | None = None
    if enable_prewarm and prewarm_synthesis_diff and synthesis_model != chunk_model:
        synthesis_warmup_task = asyncio.create_task(
            warm_pdf_cache(pdf_text, api_semaphore, pdf_name=pdf_name, model=synthesis_model)
        )

    raw_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

    if synthesis_warmup_task is not None:
        await synthesis_warmup_task

    failed = {extraction_chunks[i]: err for i, err in enumerate(raw_results) if isinstance(err, Exception)}
    if failed:
        for chunk_num, err in failed.items():
            logger.error(f"FAIL  {pdf_name} -- chunk {chunk_num}: {err}")
        async with manifest_lock:
            manifest[pdf_name] = {
                "status": "failed_chunks",
                "failed_chunks": list(failed.keys()),
            }
            save_manifest(manifest)
        return None

    return list(raw_results)


async def process_pdf(
    pdf_path: Path,
    chunk_fields: dict[int, list[dict]],
    field_lookup: dict[int, dict],
    api_semaphore: asyncio.Semaphore,
    manifest: dict,
    manifest_lock: asyncio.Lock,
    openai_config: dict,
) -> Optional[list[dict]]:
    """Process one PDF end-to-end. Returns extracted field list on success, None on failure."""
    pdf_name = pdf_path.stem

    # Step 1: skip already-complete PDFs.
    completed = _load_completed_result(pdf_name, manifest)
    if completed is not None:
        logger.info(f"SKIP  {pdf_name} (already complete)")
        return completed

    logger.info(f"START {pdf_name}")

    chunk_model            = openai_config["chunk_model"]
    enable_prewarm         = openai_config["enable_cache_prewarm"]
    num_chunks             = openai_config["num_chunks"]
    synthesis_model        = openai_config["synthesis_model"]
    prewarm_synthesis_diff = openai_config.get("prewarm_synthesis_if_model_diff", True)

    # Step 2: extract PDF text locally.
    pdf_text = await _extract_text(pdf_path, pdf_name, manifest, manifest_lock)
    if pdf_text is None:
        return None

    # Step 3: run parallel extraction chunks (with optional cache warmup).
    raw_results = await _run_parallel_chunks(
        pdf_text, chunk_fields, api_semaphore, pdf_name,
        num_chunks, enable_prewarm, chunk_model, synthesis_model,
        prewarm_synthesis_diff, manifest, manifest_lock,
    )
    if raw_results is None:
        return None

    # Step 4: reconstruct prior-context for synthesis.
    prior_context: list[dict] = []
    for chunk_result in raw_results:
        prior_context.extend(reconstruct_fields(chunk_result, field_lookup))  # type: ignore[arg-type]
    prior_context.sort(key=lambda x: x["field_index"])

    # Step 5: run synthesis chunk.
    synthesis_chunk = num_chunks
    try:
        final_compact = await extract_chunk(
            synthesis_chunk, pdf_text, chunk_fields[synthesis_chunk], api_semaphore,
            prior_context=prior_context, pdf_name=pdf_name,
        )
        final_fields = reconstruct_fields(final_compact, field_lookup)
    except Exception as exc:
        logger.error(f"FAIL  {pdf_name} -- chunk {synthesis_chunk} (synthesis): {exc}")
        async with manifest_lock:
            manifest[pdf_name] = {"status": f"failed_chunk_{synthesis_chunk}", "error": str(exc)}
            save_manifest(manifest)
        return None

    # Step 6: merge, sort, save, and mark complete.
    all_fields = prior_context + final_fields
    all_fields.sort(key=lambda x: x["field_index"])

    _save_pdf_output(pdf_name, all_fields)

    async with manifest_lock:
        manifest[pdf_name] = {"status": "complete"}
        save_manifest(manifest)

    logger.info(f"DONE  {pdf_name} -- {len(all_fields)} fields extracted")
    return all_fields
