"""
Pipeline orchestrator.

Per PDF:
1. Check manifest skip status.
2. Extract PDF text once locally.
3. If enabled, run a tiny chunk-model cache warmup for the shared PDF prefix.
4. Run extraction chunks 1 to (NUM_CHUNKS-1) in parallel with the OpenAI chunk model.
5. If the synthesis model differs, run its warmup while extraction chunks are running.
6. Validate all extraction chunks; abort this PDF on failure.
7. Combine extraction chunk outputs and pass them as context to the final synthesis chunk.
8. Merge all extracted fields, sort by field_index, save JSON, update manifest.

Field ranges are inferred dynamically from extraction_map.json and NUM_CHUNKS.
PDF-level concurrency is controlled by pdf_semaphore (default 3).
API-level concurrency is controlled by api_semaphore (default 15).
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from config import (
    CHUNK_MODEL,
    DOMAIN_TO_CHUNK,
    ENABLE_CACHE_PREWARM,
    EXTRACTION_MAP,
    GLOBAL_API_LIMIT,
    MANIFEST_FILE,
    NUM_CHUNKS,
    OUTPUT_DIR,
    PDF_CONCURRENCY,
    PREWARM_SYNTHESIS_IF_MODEL_DIFF,
    SYNTHESIS_MODEL,
)
from api_client import extract_chunk, warm_pdf_cache
from pdf_extractor import extract_pdf_text
from validator import reconstruct_fields

logger = logging.getLogger(__name__)


# -- Extraction map helpers --------------------------------------------------

def _infer_chunk_field_ranges() -> dict[int, tuple[int, int]]:
    """Infer chunk field index ranges from extraction_map.json and domain-to-chunk mapping.

    Returns:
        A dict mapping chunk_num to (min_field_index, max_field_index).
    """
    with open(EXTRACTION_MAP, encoding="utf-8") as f:
        all_fields: list[dict] = json.load(f)

    # Group fields by chunk number
    chunk_to_fields: dict[int, list[int]] = {}
    for field in all_fields:
        domain_prefix = int(field["domain_group"].split(".")[0])
        chunk_num = DOMAIN_TO_CHUNK.get(domain_prefix)
        if chunk_num is None:
            raise ValueError(
                f"Domain {domain_prefix} from '{field['domain_group']}' "
                f"not found in DOMAIN_TO_CHUNK mapping"
            )
        if chunk_num not in chunk_to_fields:
            chunk_to_fields[chunk_num] = []
        chunk_to_fields[chunk_num].append(field["field_index"])

    # Build ranges from grouped fields
    result: dict[int, tuple[int, int]] = {}
    for chunk_num, field_indices in chunk_to_fields.items():
        result[chunk_num] = (min(field_indices), max(field_indices))

    return result


def load_chunk_fields() -> dict[int, list[dict]]:
    """Load extraction_map.json and split into per-chunk field lists.

    Field assignments are inferred from extraction_map.json domain groups and
    the DOMAIN_TO_CHUNK configuration.
    """
    with open(EXTRACTION_MAP, encoding="utf-8") as f:
        all_fields: list[dict] = json.load(f)

    chunk_field_ranges = _infer_chunk_field_ranges()
    result: dict[int, list[dict]] = {}
    for chunk_num, (lo, hi) in chunk_field_ranges.items():
        result[chunk_num] = [
            field for field in all_fields
            if lo <= field["field_index"] <= hi
        ]
    return result


def _build_field_lookup() -> dict[int, dict]:
    """Build a field_index → {domain_group, field_name} lookup from extraction_map.json."""
    with open(EXTRACTION_MAP, encoding="utf-8") as f:
        all_fields: list[dict] = json.load(f)
    return {
        f["field_index"]: {"domain_group": f["domain_group"], "field_name": f["field_name"]}
        for f in all_fields
    }


# -- Manifest helpers --------------------------------------------------------

def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_manifest(manifest: dict) -> None:
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def _save_pdf_output(pdf_name: str, fields: list[dict]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / f"{pdf_name}.extracted.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=2)
    logger.info(f"Saved -> {out.name}")


# -- Per-PDF processor -------------------------------------------------------

async def _process_pdf(
    pdf_path: Path,
    chunk_fields: dict[int, list[dict]],
    field_lookup: dict[int, dict],
    api_semaphore: asyncio.Semaphore,
    manifest: dict,
    manifest_lock: asyncio.Lock,
) -> Optional[list[dict]]:
    """
    Process one PDF end-to-end.
    Returns the extracted field list on success, None on failure.
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

    # Step 1: extract PDF text locally.
    try:
        pdf_text = extract_pdf_text(pdf_path)
        logger.info(f"TEXT  {pdf_name} ({len(pdf_text):,} chars)")
    except Exception as exc:
        logger.error(f"FAIL  {pdf_name} -- PDF extraction: {exc}")
        async with manifest_lock:
            manifest[pdf_name] = {"status": "failed_pdf_extraction", "error": str(exc)}
            _save_manifest(manifest)
        return None

    # Step 2: warm shared PDF prefix for the chunk model. With PDF_CONCURRENCY=3,
    # the three uploaded PDFs warm in parallel before their real extraction calls.
    if ENABLE_CACHE_PREWARM:
        await warm_pdf_cache(pdf_text, api_semaphore, pdf_name=pdf_name, model=CHUNK_MODEL)

    # Step 3: Run extraction chunks 1 to (NUM_CHUNKS-1) in parallel.
    # If synthesis uses a different model, warm its cache concurrently to improve efficiency.
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
        # Warmup failure should not fail extraction; warm_pdf_cache already logs it.
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
            _save_manifest(manifest)
        return None

    # Step 4: reconstruct compact results and combine as prior context.
    prior_context: list[dict] = []
    for chunk_result in raw_results:
        prior_context.extend(reconstruct_fields(chunk_result, field_lookup))  # type: ignore[arg-type]
    prior_context.sort(key=lambda x: x["field_index"])

    # Step 5: Final chunk (synthesis). Its prompt keeps PDF text before prior outputs.
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
            _save_manifest(manifest)
        return None

    # Step 6: merge, sort, save.
    all_fields = prior_context + final_fields
    all_fields.sort(key=lambda x: x["field_index"])

    _save_pdf_output(pdf_name, all_fields)

    async with manifest_lock:
        manifest[pdf_name] = {"status": "complete"}
        _save_manifest(manifest)

    logger.info(f"DONE  {pdf_name} -- {len(all_fields)} fields extracted")
    return all_fields


# -- Main pipeline entry point ----------------------------------------------

async def run_pipeline(pdf_paths: list[Path]) -> list[dict]:
    """
    Process all PDFs with PDF_CONCURRENCY parallel workers.

    Returns:
        List of {"pdf": filename, "fields": [...]} for every successful paper.
    """
    chunk_fields = load_chunk_fields()
    field_lookup = _build_field_lookup()
    manifest = load_manifest()
    manifest_lock = asyncio.Lock()
    api_semaphore = asyncio.Semaphore(GLOBAL_API_LIMIT)
    pdf_semaphore = asyncio.Semaphore(PDF_CONCURRENCY)

    async def _bounded(pdf_path: Path):
        async with pdf_semaphore:
            return await _process_pdf(
                pdf_path, chunk_fields, field_lookup, api_semaphore, manifest, manifest_lock
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
