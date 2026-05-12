"""Per-PDF processing logic."""
import asyncio
import json
from typing import Optional

from quality_control import QCBundle
from .evidence_index import (
    attach_table_figure_crops,
    build_chunk_evidence_package,
    build_or_load_evidence_bundle,
    build_paper_evidence_package,
)
from quality_control.validate_context import validate_qc_context_input
from .validator import reconstruct_fields, validate_chunk_output, ValidationError
from utils.logging_utils import get_logger
from utils.path_utils import OUTPUT_DIR
from .manifest import save_manifest

logger = get_logger(__name__)


def _get_normalizer(normalizer_name: str):
    """Get a normalizer instance by class name from text_processing.normalizers.
    
    Args:
        normalizer_name: Name of the normalizer class (e.g., 'FullNormalizer')
        
    Returns:
        Normalizer instance or None if not found
    """
    try:
        from text_processing import normalizers
        normalizer_class = getattr(normalizers, normalizer_name, None)
        if normalizer_class is None:
            logger.warning(f"Normalizer '{normalizer_name}' not found, skipping sanitization")
            return None
        return normalizer_class()
    except Exception as e:
        logger.warning(f"Failed to load normalizer '{normalizer_name}': {e}, skipping sanitization")
        return None


def _save_pdf_output(pdf_name: str, fields: list[dict], normalizer=None) -> None:
    """Save extracted fields to JSON, optionally sanitizing extracted_value with a normalizer.
    
    Args:
        pdf_name: PDF identifier
        fields: List of extracted field dicts
        normalizer: Optional text normalizer to apply to extracted_value fields
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / f"{pdf_name}.extracted.json"
    
    # Apply normalizer if provided
    if normalizer is not None:
        for field in fields:
            if "extracted_value" in field and isinstance(field["extracted_value"], str):
                field["extracted_value"] = normalizer.normalize(field["extracted_value"])
    
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
    """Run extraction chunks 1..(num_chunks-1) in parallel.

    Also fires a synthesis-shaped warmup task concurrently if configured.
    The synthesis warmup extends the cached prefix past the extraction map
    for the synthesis chunk, which is the most the server-side prompt cache
    can preserve (prior_context is data-dependent and cannot be cached).
    Returns the list of raw chunk results, or None if any chunk failed.
    """
    if not chunk_sources:
        return []

    from agents.openai.api_client import extract_chunk, warm_pdf_cache  # noqa: PLC0415

    warm_source = chunk_sources.get(1) or next(iter(chunk_sources.values()))

    extraction_chunks = [i for i in range(1, num_chunks) if i in chunk_fields]
    synthesis_chunk_num = num_chunks
    synthesis_fields = chunk_fields.get(synthesis_chunk_num, [])

    # Phase 1 — Concurrent warmups (accuracy-safe, token-efficient):
    # Both warmups fire at t=0 in parallel rather than having synthesis
    # warmup block on chunk warmup (which previously delayed it by the
    # full chunk-warmup latency, typically ~5s in the reference run).
    # Both warmups are tiny output (32 tokens) and hit disjoint prefix
    # extensions, so there is no cache contention between them.
    if enable_prewarm:
        warmup_tasks: list[asyncio.Task] = [
            asyncio.create_task(
                warm_pdf_cache(warm_source, api_semaphore, pdf_name=pdf_name, model=chunk_model),
                name=f"warmup-chunk-{pdf_name}",
            )
        ]
        if synthesis_fields and (prewarm_synthesis_diff or synthesis_model == chunk_model):
            # Also warm the synthesis-shaped prefix (shared_prefix +
            # extraction_map). prior_context is a data-dependent trailing
            # suffix that the cache cannot preserve anyway.
            warmup_tasks.append(
                asyncio.create_task(
                    warm_pdf_cache(
                        warm_source, api_semaphore, pdf_name=pdf_name,
                        model=synthesis_model, chunk_fields=synthesis_fields,
                        tag_suffix="warmup-synth",
                    ),
                    name=f"warmup-synth-{pdf_name}",
                )
            )
        await asyncio.gather(*warmup_tasks, return_exceptions=True)

    # Phase 2 — Extraction chunks run in parallel. Their shared byte-
    # identical prefix (build_paper_evidence_package, commit 3) now hits
    # the cache seeded by phase 1.
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
    raw_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

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
    from agents.openai.api_client import extract_chunk, warm_pdf_cache  # noqa: PLC0415

    validate_qc_context_input(qc_context)
    pdf_name = qc_context.unified.document_id
    pdf_text = qc_context.unified.content["exact_text"]
    logger.debug(
        "process_pdf %s: exact_text=%d chars, unified.content keys=%s",
        pdf_name, len(pdf_text),
        sorted(qc_context.unified.content.keys()) if isinstance(qc_context.unified.content, dict) else "?",
    )

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
    logger.debug(
        "%s config: chunk_model=%s, synthesis_model=%s, num_chunks=%d, "
        "enable_prewarm=%s, prewarm_synthesis_diff=%s, "
        "max_evidence_items=%d, max_evidence_chars=%d",
        pdf_name, chunk_model, synthesis_model, num_chunks,
        enable_prewarm, prewarm_synthesis_diff,
        max_evidence_items, max_evidence_chars,
    )

    bundle = build_or_load_evidence_bundle(qc_context, openai_config)
    valid_location_ids = set(bundle.evidence_map.keys())
    logger.info("Evidence index ready for %s: %d IDs", pdf_name, len(valid_location_ids))
    logger.debug(
        "%s evidence types: %s",
        pdf_name,
        {t: sum(1 for it in bundle.evidence_items if it.get("type") == t)
         for t in {it.get("type") for it in bundle.evidence_items}},
    )

    # Locally prefill field 1 and 2 from TEI metadata.
    prefilled_fields: list[dict] = []
    prefilled_indices = set(bundle.prefilled_fields.keys())
    logger.debug(
        "%s prefilled field indices from TEI: %s",
        pdf_name, sorted(prefilled_indices),
    )
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

    # Build ONE paper-level evidence package shared by every chunk (extraction
    # and synthesis). This is the keystone of the prompt-cache strategy: the
    # shared PDF prefix in agents.openai.prompts._shared_paper_prefix must be
    # byte-identical across chunks so the server-side prefix cache hits on
    # chunks 2..N after warmup seeds chunk 1.
    filtered_per_chunk: dict[int, list[dict]] = {}
    all_llm_fields: list[dict] = []
    for chunk_num, fields in chunk_fields.items():
        filtered = [f for f in fields if f.get("field_index") not in prefilled_indices]
        if not filtered:
            continue
        filtered_per_chunk[chunk_num] = filtered
        all_llm_fields.extend(filtered)

    paper_source = ""
    if all_llm_fields:
        paper_source = build_paper_evidence_package(
            bundle,
            all_llm_fields,
            max_items=max_evidence_items,
            max_chars=max_evidence_chars,
        )
        logger.info(
            "Paper evidence package for %s: %d chars, %d fields across %d chunks",
            pdf_name, len(paper_source), len(all_llm_fields), len(filtered_per_chunk),
        )

    for chunk_num, filtered in filtered_per_chunk.items():
        chunk_fields_for_llm[chunk_num] = filtered
        # Identical bytes across all chunks -> shared-prefix cache hits.
        chunk_sources[chunk_num] = paper_source
        logger.debug(
            "Chunk %d evidence package for %s reuses shared paper package: %d chars, %d fields",
            chunk_num, pdf_name, len(paper_source), len(filtered),
        )
    if chunk_sources:
        reduction = 0.0
        if pdf_text:
            reduction = max(0.0, 1.0 - (len(paper_source) / max(len(pdf_text), 1)))
        logger.info(
            "Evidence token-size estimate for %s: source=%d chars, shared package=%d chars, reduction=%.1f%%",
            pdf_name, len(pdf_text), len(paper_source), reduction * 100,
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
    logger.debug(
        "%s extraction_chunks to validate: %s",
        pdf_name, extraction_chunks,
    )
    prior_context: list[dict] = list(prefilled_fields)
    for chunk_idx, raw_text in zip(extraction_chunks, raw_results):
        expected_idx = sorted(
            f["field_index"] for f in chunk_fields_for_llm[chunk_idx]
        )
        logger.debug(
            "%s validating chunk %d: raw=%d chars, expected_indices=%s",
            pdf_name, chunk_idx, len(raw_text), expected_idx,
        )
        try:
            validated = validate_chunk_output(
                raw_text,
                expected_idx,
                valid_location_ids=valid_location_ids,
            )
        except ValidationError as exc:
            logger.error(f"FAIL  {pdf_name} -- chunk {chunk_idx} validation: {exc}")
            logger.debug(
                "%s chunk %d raw output (full): %r",
                pdf_name, chunk_idx, raw_text,
            )
            async with manifest_lock:
                manifest[pdf_name] = {
                    "status": "failed_chunks",
                    "failed_chunks": [chunk_idx],
                }
                save_manifest(manifest)
            return None
        logger.debug(
            "%s chunk %d validated: %d items",
            pdf_name, chunk_idx, len(validated),
        )
        prior_context.extend(reconstruct_fields(validated, field_lookup, bundle.evidence_map))
    prior_context.sort(key=lambda x: x["field_index"])
    logger.debug(
        "%s prior_context after extraction chunks: %d fields",
        pdf_name, len(prior_context),
    )

    # Step 4: run synthesis chunk.
    synthesis_chunk = num_chunks
    try:
        final_fields: list[dict] = []
        synthesis_fields = chunk_fields_for_llm.get(synthesis_chunk, [])
        if synthesis_fields:
            logger.debug(
                "%s synthesis chunk %d: %d fields (indices=%s)",
                pdf_name, synthesis_chunk, len(synthesis_fields),
                sorted(f.get("field_index") for f in synthesis_fields),
            )
            synthesis_raw = await extract_chunk(
                synthesis_chunk,
                chunk_sources.get(synthesis_chunk, paper_source or json.dumps({"paper_id": pdf_name, "evidence": []})),
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
            logger.debug(
                "%s synthesis validated: %d items",
                pdf_name, len(final_compact),
            )
            final_fields = reconstruct_fields(final_compact, field_lookup, bundle.evidence_map)
    except Exception as exc:
        logger.error(f"FAIL  {pdf_name} -- chunk {synthesis_chunk} (synthesis): {exc}")
        logger.debug(
            "%s synthesis exception details",
            pdf_name, exc_info=True,
        )
        async with manifest_lock:
            manifest[pdf_name] = {"status": f"failed_chunk_{synthesis_chunk}", "error": str(exc)}
            save_manifest(manifest)
        return None

    # Step 5: merge, sort, save, and mark complete.
    all_fields = prior_context + final_fields
    all_fields.sort(key=lambda x: x["field_index"])
    attach_table_figure_crops(all_fields, bundle, openai_config)

    # Apply sanitization if enabled
    normalizer = None
    if openai_config.get("sanitize_extracted_values", False):
        normalizer_name = openai_config.get("exported_value_normalizer", "FullNormalizer")
        normalizer = _get_normalizer(normalizer_name)

    _save_pdf_output(pdf_name, all_fields, normalizer=normalizer)

    async with manifest_lock:
        manifest[pdf_name] = {"status": "complete"}
        save_manifest(manifest)

    logger.info(f"DONE  {pdf_name} -- {len(all_fields)} fields extracted")
    return all_fields
