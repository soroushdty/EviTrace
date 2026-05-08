"""Per-PDF processing logic."""
import asyncio
import json
from pathlib import Path
from typing import Optional

from api_client import extract_chunk, warm_pdf_cache
from pdf_extractor import extract_pdf_text
from validator import reconstruct_fields
from utils.logging_utils import get_logger
from utils.path_utils import OUTPUT_DIR
from manifest import save_manifest, save_pdf_output

logger = get_logger(__name__)


async def process_pdf(
    pdf_path: Path,
    chunk_fields: dict[int, list[dict]],
    field_lookup: dict[int, dict],
    api_semaphore: asyncio.Semaphore,
    manifest: dict,
    manifest_lock: asyncio.Lock,
    openai_config: dict,
) -> Optional[list[dict]]:
    """Process one PDF end-to-end. Returns extracted field list on success, None on failure.
    """
    pdf_name = pdf_path.stem

    # Already done?
    if manifest.get(pdf_name, {}).get("status") == "complete":
        logger.info(f"SKIP  {pdf_name} (already complete)")
        out = OUTPUT_DIR / f"{pdf_name}.extracted.json"
        if out.exists():
            with open(out, encoding="utf-8") as f:
                return json.load(f)
        return None

    logger.info(f"START {pdf_name}")

    CHUNK_MODEL = openai_config["chunk_model"]
    ENABLE_CACHE_PREWARM = openai_config["enable_cache_prewarm"]
    NUM_CHUNKS = openai_config["num_chunks"]
    SYNTHESIS_MODEL = openai_config["synthesis_model"]
    PREWARM_SYNTHESIS_IF_MODEL_DIFF = openai_config.get("prewarm_synthesis_if_model_diff", True)

    # Step 1: extract PDF text locally.
    try:
        pdf_text = extract_pdf_text(pdf_path)
        logger.info(f"TEXT  {pdf_name} ({len(pdf_text):,} chars)")
    except Exception as exc:
        logger.error(f"FAIL  {pdf_name} -- PDF extraction: {exc}")
        async with manifest_lock:
            manifest[pdf_name] = {"status": "failed_pdf_extraction", "error": str(exc)}
            save_manifest(manifest)
        return None

    # Step 2: warm shared PDF prefix for the chunk model.
    if ENABLE_CACHE_PREWARM:
        await warm_pdf_cache(pdf_text, api_semaphore, pdf_name=pdf_name, model=CHUNK_MODEL)

    # Step 3: Run extraction chunks 1 to (NUM_CHUNKS-1) in parallel.
    extraction_chunks = list(range(1, NUM_CHUNKS))
    chunk_tasks = [
        extract_chunk(i, pdf_text, chunk_fields[i], api_semaphore, pdf_name=pdf_name)
        for i in extraction_chunks
    ]

    synthesis_warmup_task: asyncio.Task[bool] | None = None
    if (
        ENABLE_CACHE_PREWARM
        and PREWARM_SYNTHESIS_IF_MODEL_DIFF
        and SYNTHESIS_MODEL != CHUNK_MODEL
    ):
        synthesis_warmup_task = asyncio.create_task(
            warm_pdf_cache(pdf_text, api_semaphore, pdf_name=pdf_name, model=SYNTHESIS_MODEL)
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

    # Step 4: reconstruct compact results and combine as prior context.
    prior_context: list[dict] = []
    for chunk_result in raw_results:
        prior_context.extend(reconstruct_fields(chunk_result, field_lookup))  # type: ignore[arg-type]
    prior_context.sort(key=lambda x: x["field_index"])

    # Step 5: Final chunk (synthesis)
    try:
        synthesis_chunk = NUM_CHUNKS
        final_compact = await extract_chunk(
            synthesis_chunk, pdf_text, chunk_fields[synthesis_chunk], api_semaphore,
            prior_context=prior_context, pdf_name=pdf_name,
        )
        final_fields = reconstruct_fields(final_compact, field_lookup)
    except Exception as exc:
        logger.error(f"FAIL  {pdf_name} -- chunk {NUM_CHUNKS} (synthesis): {exc}")
        async with manifest_lock:
            manifest[pdf_name] = {"status": f"failed_chunk_{NUM_CHUNKS}", "error": str(exc)}
            save_manifest(manifest)
        return None

    # Step 6: merge, sort, save.
    all_fields = prior_context + final_fields
    all_fields.sort(key=lambda x: x["field_index"])

    save_pdf_output(pdf_name, all_fields)

    async with manifest_lock:
        manifest[pdf_name] = {"status": "complete"}
        save_manifest(manifest)

    logger.info(f"DONE  {pdf_name} -- {len(all_fields)} fields extracted")
    return all_fields
