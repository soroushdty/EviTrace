"""Per-PDF processing logic."""
import asyncio
import json
from typing import Optional

from agents.openai.api_client import extract_chunk, warm_pdf_cache
from quality_control import QCBundle
from .evidence_index import (
    attach_table_figure_crops,
    build_chunk_evidence_package,
    build_or_load_evidence_bundle,
)
from quality_control.validate_context import validate_qc_context_input
from .validator import reconstruct_fields, validate_chunk_output, ValidationError
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


async def _run_parallel_chunks(
    chunk_sources: dict[int, str],
    chunk_fields: dict[int, list[dict]],
    valid_location_ids: set[str],
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
    if not chunk_sources:
        return []

    warm_source = chunk_sources.get(1) or next(iter(chunk_sources.values()))
    if enable_prewarm:
        await warm_pdf_cache(warm_source, api_semaphore, pdf_name=pdf_name, model=chunk_model)

    extraction_chunks = [i for i in range(1, num_chunks) if i in chunk_fields]
    chunk_tasks = [
        extract_chunk(
            i,
            chunk_sources.get(i, warm_source),
            chunk_fields[i],
            api_semaphore,
            valid_location_ids=valid_location_ids,
            pdf_name=pdf_name,
        )
        for i in extraction_chunks
        if i in chunk_fields
    ]

    synthesis_warmup_task: asyncio.Task[bool] | None = None
    if enable_prewarm and prewarm_synthesis_diff and synthesis_model != chunk_model:
        synthesis_warmup_task = asyncio.create_task(
            warm_pdf_cache(warm_source, api_semaphore, pdf_name=pdf_name, model=synthesis_model)
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
    qc_context: QCBundle,
    chunk_fields: dict[int, list[dict]],
    field_lookup: dict[int, dict],
    api_semaphore: asyncio.Semaphore,
    manifest: dict,
    manifest_lock: asyncio.Lock,
    openai_config: dict,
) -> Optional[list[dict]]:
    """Process one PDF end-to-end. Returns extracted field list on success, None on failure."""
    validate_qc_context_input(qc_context)
    pdf_name = qc_context.unified.document_id
    pdf_text = qc_context.unified.content["exact_text"]

    # Step 1: skip already-complete PDFs.
    completed = _load_completed_result(pdf_name, manifest)
    if completed is not None:
        logger.info(f"SKIP  {pdf_name} (already complete)")
        return completed

    logger.info(f"START {pdf_name} ({len(pdf_text):,} chars)")

    chunk_model            = openai_config["chunk_model"]
    enable_prewarm         = openai_config["enable_cache_prewarm"]
    num_chunks             = openai_config["num_chunks"]
    synthesis_model        = openai_config["synthesis_model"]
    prewarm_synthesis_diff = openai_config.get("prewarm_synthesis_if_model_diff", True)
    max_evidence_items = int(openai_config.get("max_evidence_items_per_chunk", 250))
    max_evidence_chars = int(openai_config.get("max_evidence_chars_per_chunk", 60000))

    bundle = build_or_load_evidence_bundle(qc_context, openai_config)
    valid_location_ids = set(bundle.evidence_map.keys())
    logger.info("Evidence index ready for %s: %d IDs", pdf_name, len(valid_location_ids))

    # Locally prefill field 1 and 2 from TEI metadata.
    prefilled_fields: list[dict] = []
    prefilled_indices = set(bundle.prefilled_fields.keys())
    for field_idx, value in bundle.prefilled_fields.items():
        if field_idx in field_lookup:
            prefilled_fields.append(
                {
                    "field_index": field_idx,
                    "domain_group": field_lookup[field_idx]["domain_group"],
                    "field_name": field_lookup[field_idx]["field_name"],
                    "extracted_value": value or "nr",
                    "evidence": "",
                    "location": [],
                    "location_metadata": [],
                    "confidence": "h" if value and value != "nr" else "nr",
                }
            )

    chunk_fields_for_llm: dict[int, list[dict]] = {}
    chunk_sources: dict[int, str] = {}
    for chunk_num, fields in chunk_fields.items():
        filtered = [f for f in fields if f.get("field_index") not in prefilled_indices]
        if not filtered:
            continue
        chunk_fields_for_llm[chunk_num] = filtered
        chunk_sources[chunk_num] = build_chunk_evidence_package(
            bundle,
            filtered,
            max_items=max_evidence_items,
            max_chars=max_evidence_chars,
        )
        logger.info(
            "Chunk %d evidence package for %s: %d chars, %d fields",
            chunk_num,
            pdf_name,
            len(chunk_sources[chunk_num]),
            len(filtered),
        )
    if chunk_sources:
        avg_pkg = sum(len(v) for v in chunk_sources.values()) / len(chunk_sources)
        reduction = 0.0
        if pdf_text:
            reduction = max(0.0, 1.0 - (avg_pkg / max(len(pdf_text), 1)))
        logger.info(
            "Evidence token-size estimate for %s: source=%d chars, avg chunk package=%d chars, reduction=%.1f%%",
            pdf_name,
            len(pdf_text),
            int(avg_pkg),
            reduction * 100,
        )

    # Step 2: run parallel extraction chunks (with optional cache warmup).
    raw_results = await _run_parallel_chunks(
        chunk_sources, chunk_fields_for_llm, valid_location_ids, api_semaphore, pdf_name,
        num_chunks, enable_prewarm, chunk_model, synthesis_model,
        prewarm_synthesis_diff, manifest, manifest_lock,
    )
    if raw_results is None:
        return None

    # Step 3: validate and reconstruct prior-context for synthesis.
    # raw_results is a list of raw API response strings (one per extraction chunk).
    extraction_chunks = sorted(
        i for i in range(1, num_chunks) if i in chunk_fields_for_llm
    )
    prior_context: list[dict] = list(prefilled_fields)
    for chunk_idx, raw_text in zip(extraction_chunks, raw_results):
        expected_idx = sorted(
            f["field_index"] for f in chunk_fields_for_llm[chunk_idx]
        )
        try:
            validated = validate_chunk_output(
                raw_text,
                expected_idx,
                valid_location_ids=valid_location_ids,
            )
        except ValidationError as exc:
            logger.error(f"FAIL  {pdf_name} -- chunk {chunk_idx} validation: {exc}")
            async with manifest_lock:
                manifest[pdf_name] = {
                    "status": "failed_chunks",
                    "failed_chunks": [chunk_idx],
                }
                save_manifest(manifest)
            return None
        prior_context.extend(reconstruct_fields(validated, field_lookup, bundle.evidence_map))
    prior_context.sort(key=lambda x: x["field_index"])

    # Step 4: run synthesis chunk.
    synthesis_chunk = num_chunks
    try:
        final_fields: list[dict] = []
        synthesis_fields = chunk_fields_for_llm.get(synthesis_chunk, [])
        if synthesis_fields:
            synthesis_raw = await extract_chunk(
                synthesis_chunk,
                chunk_sources.get(synthesis_chunk, json.dumps({"paper_id": pdf_name, "evidence": []})),
                synthesis_fields,
                api_semaphore,
                valid_location_ids=valid_location_ids,
                prior_context=prior_context,
                pdf_name=pdf_name,
            )
            synthesis_expected_idx = sorted(
                f["field_index"] for f in synthesis_fields
            )
            final_compact = validate_chunk_output(
                synthesis_raw,
                synthesis_expected_idx,
                valid_location_ids=valid_location_ids,
            )
            final_fields = reconstruct_fields(final_compact, field_lookup, bundle.evidence_map)
    except Exception as exc:
        logger.error(f"FAIL  {pdf_name} -- chunk {synthesis_chunk} (synthesis): {exc}")
        async with manifest_lock:
            manifest[pdf_name] = {"status": f"failed_chunk_{synthesis_chunk}", "error": str(exc)}
            save_manifest(manifest)
        return None

    # Step 5: merge, sort, save, and mark complete.
    all_fields = prior_context + final_fields
    all_fields.sort(key=lambda x: x["field_index"])
    attach_table_figure_crops(all_fields, bundle, openai_config)

    _save_pdf_output(pdf_name, all_fields)

    async with manifest_lock:
        manifest[pdf_name] = {"status": "complete"}
        save_manifest(manifest)

    logger.info(f"DONE  {pdf_name} -- {len(all_fields)} fields extracted")
    return all_fields
