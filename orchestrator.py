"""
Pipeline orchestrator.

Per PDF:
1. Check manifest skip status.
2. Extract PDF text once locally.
3. If enabled, run a tiny chunk-model cache warmup for the shared PDF prefix.
4. Run chunks 1-4 in parallel with the OpenAI chunk model.
5. If the synthesis model differs, run its warmup while chunks 1-4 are running.
6. Validate all four extraction chunks; abort this PDF on failure.
7. Combine chunks 1-4 and pass them as context to chunk 5.
8. Merge all 62 fields, sort by field_index, save JSON, update manifest.

PDF-level concurrency is controlled by pdf_semaphore (default 3).
API-level concurrency is controlled by api_semaphore (default 15).
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from config import (
    CHUNK_FIELD_RANGES,
    CHUNK_MODEL,
    ENABLE_CACHE_PREWARM,
    EXTRACTION_MAP,
    GLOBAL_API_LIMIT,
    MANIFEST_FILE,
    OUTPUT_DIR,
    PDF_CONCURRENCY,
    PREWARM_SYNTHESIS_IF_MODEL_DIFF,
    SYNTHESIS_MODEL,
)
from api_client import extract_chunk, warm_pdf_cache
from pdf_extractor import extract_pdf_text

logger = logging.getLogger(__name__)


# -- Extraction map helpers --------------------------------------------------

def load_chunk_fields() -> dict[int, list[dict]]:
    """Load extraction_map.json and split into per-chunk field lists."""
    with open(EXTRACTION_MAP, encoding="utf-8") as f:
        all_fields: list[dict] = json.load(f)

    result: dict[int, list[dict]] = {}
    for chunk_num, (lo, hi) in CHUNK_FIELD_RANGES.items():
        result[chunk_num] = [
            field for field in all_fields
            if lo <= field["field_index"] <= hi
        ]
    return result


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
    api_semaphore: asyncio.Semaphore,
    manifest: dict,
    manifest_lock: asyncio.Lock,
) -> Optional[list[dict]]:
    """
    Process one PDF end-to-end.
    Returns the 62-field list on success, None on failure.
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

    # Step 3: chunks 1-4 in parallel. If synthesis uses a different model, warm
    # its cache concurrently so chunk 5 can reuse the same PDF prefix later.
    chunk_tasks = [
        extract_chunk(i, pdf_text, chunk_fields[i], api_semaphore, pdf_name=pdf_name)
        for i in range(1, 5)
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

    failed = {i + 1: err for i, err in enumerate(raw_results) if isinstance(err, Exception)}
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

    # Step 4: combine chunks 1-4 as prior context.
    prior_context: list[dict] = []
    for chunk_result in raw_results:
        prior_context.extend(chunk_result)          # type: ignore[arg-type]
    prior_context.sort(key=lambda x: x["field_index"])

    # Step 5: chunk 5 synthesis. Its prompt keeps PDF text before prior outputs.
    try:
        chunk5 = await extract_chunk(
            5, pdf_text, chunk_fields[5], api_semaphore,
            prior_context=prior_context, pdf_name=pdf_name,
        )
    except Exception as exc:
        logger.error(f"FAIL  {pdf_name} -- chunk 5 (synthesis): {exc}")
        async with manifest_lock:
            manifest[pdf_name] = {"status": "failed_chunk_5", "error": str(exc)}
            _save_manifest(manifest)
        return None

    # Step 6: merge, sort, save.
    all_fields = prior_context + chunk5
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
    manifest = load_manifest()
    manifest_lock = asyncio.Lock()
    api_semaphore = asyncio.Semaphore(GLOBAL_API_LIMIT)
    pdf_semaphore = asyncio.Semaphore(PDF_CONCURRENCY)

    async def _bounded(pdf_path: Path):
        async with pdf_semaphore:
            return await _process_pdf(
                pdf_path, chunk_fields, api_semaphore, manifest, manifest_lock
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
