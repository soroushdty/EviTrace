"""Per-PDF processing logic."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

from quality_control import QCBundle
from .evidence_index import (
    attach_table_figure_crops,
    build_chunk_evidence_package,
    build_or_load_evidence_bundle,
    build_paper_evidence_package,
)
from quality_control.validate_context import validate_qc_context_input
from .validator import (
    reconstruct_fields,
    validate_chunk_output,
    ValidationError,
    FinalOutputValidator,
    ValidationResult,
)
from utils.logging_utils import get_logger, log_model_response
from utils.path_utils import OUTPUT_DIR
from .manifest import save_manifest

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Atomic write helper (Requirement 11)
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """Write JSON atomically via temp file + os.replace.

    Writes to a temporary file in the same directory, then atomically renames
    to the final path using ``os.replace()``. If the write fails at any point
    before the rename, the temp file is cleaned up in the ``finally`` block and
    the final path remains unchanged (either absent or containing previous
    valid content).

    Args:
        path: Final destination path for the JSON file.
        data: JSON-serializable data to write.
        indent: JSON indentation level (default 2).
    """
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        # Clean up temp file on any failure
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Compact_Schema format reference (used in repair prompts)
# ---------------------------------------------------------------------------

_COMPACT_SCHEMA_FORMAT = (
    'Each extraction object must have exactly these keys:\n'
    '  "i": integer (field index),\n'
    '  "v": string (extracted value),\n'
    '  "loc": array of strings (evidence location IDs),\n'
    '  "c": string — one of "h", "m", "l", "nr" (confidence)\n'
    'Wrap the array in: {"extractions": [...]}'
)


# ---------------------------------------------------------------------------
# RepairRetryLoop — validation-aware LLM retries (Requirement 5)
# ---------------------------------------------------------------------------


class RepairRetryLoop:
    """Wraps extract_chunk with validation-aware repair retries.

    On parse or schema validation failure, constructs a targeted repair prompt
    and retries the LLM call. After ``max_repair_attempts`` exhausted retries,
    records structured error metadata.
    """

    def __init__(
        self,
        max_repair_attempts: int = 2,
        max_log_response_chars: int = 500,
        debug_artifact_dir: str | None = None,
    ):
        self.max_repair_attempts = max_repair_attempts
        self.max_log_response_chars = max_log_response_chars
        self.debug_artifact_dir = debug_artifact_dir

    async def extract_with_repair(
        self,
        chunk_num: int,
        source: str,
        fields: list[dict],
        semaphore: asyncio.Semaphore,
        *,
        valid_location_ids: set[str],
        expected_indices: list[int],
        pdf_name: str,
    ) -> list[dict]:
        """Try extraction, then repair on parse/validation failure.

        Returns the validated list of compact extraction dicts on success.

        Raises:
            RepairExhaustedError: When all repair attempts are exhausted.
                The exception carries structured error metadata.
        """
        from agents.openai.api_client import extract_chunk  # noqa: PLC0415

        # --- Initial attempt ---
        raw = await extract_chunk(
            chunk_num,
            source,
            fields,
            semaphore,
            valid_location_ids=valid_location_ids,
            pdf_name=pdf_name,
        )

        # Safe bounded logging of the raw model response (Requirement 6)
        log_model_response(
            logger,
            raw,
            pdf_name=pdf_name,
            chunk_num=chunk_num,
            max_chars=self.max_log_response_chars,
            debug_artifact_dir=self.debug_artifact_dir,
        )

        last_error: Exception | None = None
        last_error_type: str = ""

        try:
            validated = validate_chunk_output(
                raw,
                expected_indices,
                valid_location_ids=valid_location_ids,
            )
            return validated
        except ValidationError as exc:
            last_error = exc
            # Detect if the ValidationError wraps a JSON parse failure
            last_error_type = (
                "parse" if isinstance(exc.__cause__, json.JSONDecodeError) else "schema"
            )
        except json.JSONDecodeError as exc:
            last_error = exc
            last_error_type = "parse"

        # --- Repair attempts ---
        for attempt in range(1, self.max_repair_attempts + 1):
            repair_prompt = self._build_repair_prompt(last_error, expected_indices)
            logger.warning(
                "%s chunk %d repair attempt %d/%d (%s error): %s",
                pdf_name, chunk_num, attempt, self.max_repair_attempts,
                last_error_type, str(last_error)[:200],
            )

            raw = await extract_chunk(
                chunk_num,
                source,
                fields,
                semaphore,
                valid_location_ids=valid_location_ids,
                pdf_name=pdf_name,
                repair_prompt=repair_prompt,
            )

            # Safe bounded logging of the repair response (Requirement 6)
            log_model_response(
                logger,
                raw,
                pdf_name=pdf_name,
                chunk_num=chunk_num,
                max_chars=self.max_log_response_chars,
                debug_artifact_dir=self.debug_artifact_dir,
            )

            try:
                validated = validate_chunk_output(
                    raw,
                    expected_indices,
                    valid_location_ids=valid_location_ids,
                )
                logger.info(
                    "%s chunk %d repair succeeded on attempt %d",
                    pdf_name, chunk_num, attempt,
                )
                return validated
            except ValidationError as exc:
                last_error = exc
                last_error_type = (
                    "parse" if isinstance(exc.__cause__, json.JSONDecodeError) else "schema"
                )
            except json.JSONDecodeError as exc:
                last_error = exc
                last_error_type = "parse"

        # --- Exhaustion ---
        error_metadata = {
            "status": "failed_validation",
            "chunk": chunk_num,
            "last_error": str(last_error),
            "error_type": last_error_type,
            "attempts": self.max_repair_attempts,
        }
        logger.error(
            "%s chunk %d repair exhausted after %d attempts: %s",
            pdf_name, chunk_num, self.max_repair_attempts, error_metadata,
        )
        raise RepairExhaustedError(error_metadata)

    def _build_repair_prompt(
        self,
        error: ValidationError | json.JSONDecodeError | Exception,
        expected_indices: list[int],
    ) -> str:
        """Construct targeted repair prompt with error details.

        For JSON parse errors: includes the error message + required Compact_Schema format.
        For schema validation errors: lists specific failures (missing keys, invalid
        confidence, out-of-range indexes) and specifies valid field-index range.
        """
        parts: list[str] = [
            "Your previous response was invalid. Please fix the output and try again.",
            "",
        ]

        # Determine if this is a parse error (either direct JSONDecodeError or
        # a ValidationError wrapping one via __cause__)
        is_parse_error = isinstance(error, json.JSONDecodeError) or (
            isinstance(error, ValidationError)
            and isinstance(getattr(error, "__cause__", None), json.JSONDecodeError)
        )

        if is_parse_error:
            parts.append(f"JSON PARSE ERROR: {error}")
            parts.append("")
            parts.append("REQUIRED FORMAT:")
            parts.append(_COMPACT_SCHEMA_FORMAT)
        elif isinstance(error, ValidationError):
            error_msg = str(error)
            parts.append(f"SCHEMA VALIDATION ERROR: {error_msg}")
            parts.append("")

            # Detect out-of-range field indexes and specify valid range
            if expected_indices:
                idx_min = min(expected_indices)
                idx_max = max(expected_indices)
                if "index" in error_msg.lower() or "mismatch" in error_msg.lower():
                    parts.append(
                        f"VALID FIELD INDEX RANGE: [{idx_min}, {idx_max}]"
                    )
                    parts.append(
                        f"Expected field indices: {sorted(expected_indices)}"
                    )
                    parts.append("")

            parts.append("REQUIRED FORMAT:")
            parts.append(_COMPACT_SCHEMA_FORMAT)
        else:
            # Fallback for unexpected error types
            parts.append(f"ERROR: {error}")
            parts.append("")
            parts.append("REQUIRED FORMAT:")
            parts.append(_COMPACT_SCHEMA_FORMAT)

        return "\n".join(parts)


class RepairExhaustedError(Exception):
    """Raised when all repair attempts are exhausted.

    Carries structured error metadata as ``self.metadata``.
    """

    def __init__(self, metadata: dict):
        self.metadata = metadata
        super().__init__(
            f"Repair exhausted: chunk {metadata.get('chunk')}, "
            f"error_type={metadata.get('error_type')}, "
            f"attempts={metadata.get('attempts')}"
        )

# Module-level singleton — loaded once, reused across all _save_pdf_output calls.
_final_output_validator = FinalOutputValidator()


def _check_location_metadata_cross_references(fields: list[dict]) -> list[str]:
    """Check that every location_metadata item's id exists in the field's location list or equals 'unresolved'.

    Returns a list of error strings for any violations found.
    """
    errors: list[str] = []
    for field in fields:
        location_set = set(field.get("location", []))
        location_metadata = field.get("location_metadata", [])
        if not location_metadata:
            continue
        field_index = field.get("field_index", "?")
        field_name = field.get("field_name", "")
        for idx, meta_item in enumerate(location_metadata):
            meta_id = meta_item.get("id") if isinstance(meta_item, dict) else None
            if meta_id is None:
                continue
            if meta_id != "unresolved" and meta_id not in location_set:
                errors.append(
                    f"field_index={field_index} | field_name={field_name!r} | "
                    f"location_metadata[{idx}].id={meta_id!r} not found in "
                    f"field's location list and is not 'unresolved'"
                )
    return errors


def _get_normalizer(normalizer_name: str):
    """Get a normalizer instance by class name from text_processing.normalizers.

    Args:
        normalizer_name: Name of the normalizer class (e.g., 'AggressiveNormalizer')

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


def _save_pdf_output(
    pdf_name: str,
    fields: list[dict],
    normalizer=None,
    manifest: Optional[dict] = None,
) -> bool:
    """Save extracted fields to JSON, optionally sanitizing extracted_value with a normalizer.

    Validates the field list against the Final Output Schema before writing.
    On validation failure: sets manifest status to "failed_schema_validation",
    logs structured errors, and returns without writing the output file.

    Args:
        pdf_name: PDF identifier
        fields: List of extracted field dicts
        normalizer: Optional text normalizer to apply to extracted_value fields
        manifest: Optional manifest dict to update on validation failure

    Returns:
        True if the output was written successfully, False if validation failed.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / f"{pdf_name}.extracted.json"

    # Apply normalizer if provided
    if normalizer is not None:
        for field in fields:
            if "extracted_value" in field and isinstance(field["extracted_value"], str):
                field["extracted_value"] = normalizer.normalize(field["extracted_value"])

    # --- Validation gate ---
    # 1. JSON Schema validation
    result: ValidationResult = _final_output_validator.validate(fields)

    # 2. Location metadata cross-reference check
    cross_ref_errors = _check_location_metadata_cross_references(fields)

    all_errors = list(result.errors) + cross_ref_errors
    if all_errors:
        for err in all_errors:
            logger.warning("Schema validation error for %s: %s", pdf_name, err)
        if manifest is not None:
            manifest[pdf_name] = {"status": "failed_schema_validation"}
            save_manifest(manifest)
        logger.error(
            "FAIL  %s -- schema validation failed with %d error(s); output not written",
            pdf_name,
            len(all_errors),
        )
        return False

    _atomic_write_json(out, fields)
    logger.info(f"Saved -> {out.name}")
    return True


def _load_completed_result(pdf_name: str, manifest: dict) -> Optional[list[dict]]:
    """Return cached extraction result if this PDF is already marked complete.

    If the output file exists but fails JSON parsing (e.g. corrupted or
    partial write from a previous interrupted run), treats it as absent
    and returns None — triggering re-processing.
    """
    if manifest.get(pdf_name, {}).get("status") != "complete":
        return None
    out = OUTPUT_DIR / f"{pdf_name}.extracted.json"
    if out.exists():
        try:
            with open(out, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning(
                "Output file for %s exists but failed to parse (%s); "
                "treating as incomplete — will re-process",
                pdf_name, exc,
            )
            return None
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
    max_log_response_chars: int = 500,
    debug_artifact_dir: str | None = None,
) -> Optional[list]:
    """Run extraction chunks 1..(num_chunks-1) in parallel with validation-aware retries.

    Uses RepairRetryLoop to automatically repair malformed LLM responses.
    Returns a list of validated chunk results (list[dict] per chunk), or None
    if any chunk failed after repair exhaustion.

    Also fires a synthesis-shaped warmup task concurrently if configured.
    The synthesis warmup extends the cached prefix past the extraction map
    for the synthesis chunk, which is the most the server-side prompt cache
    can preserve (prior_context is data-dependent and cannot be cached).
    """
    if not chunk_sources:
        return []

    from agents.openai.api_client import warm_pdf_cache  # noqa: PLC0415

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

    # Phase 2 — Extraction chunks run in parallel with validation-aware
    # repair retries. RepairRetryLoop handles JSON parse errors and schema
    # validation failures by constructing targeted repair prompts.
    repair_loop = RepairRetryLoop(
        max_log_response_chars=max_log_response_chars,
        debug_artifact_dir=debug_artifact_dir,
    )

    chunk_tasks = [
        repair_loop.extract_with_repair(
            i,
            chunk_sources.get(i, warm_source),
            chunk_fields[i],
            api_semaphore,
            valid_location_ids=valid_location_ids,
            expected_indices=sorted(
                f["field_index"] for f in chunk_fields[i]
            ),
            pdf_name=pdf_name,
        )
        for i in extraction_chunks
        if i in chunk_fields
    ]
    validated_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

    failed: dict[int, Exception] = {}
    for i, result in enumerate(validated_results):
        if isinstance(result, RepairExhaustedError):
            chunk_num = extraction_chunks[i]
            logger.error(
                "FAIL  %s -- chunk %d repair exhausted: %s",
                pdf_name, chunk_num, result.metadata,
            )
            failed[chunk_num] = result
        elif isinstance(result, Exception):
            chunk_num = extraction_chunks[i]
            logger.error(f"FAIL  {pdf_name} -- chunk {chunk_num}: {result}")
            failed[chunk_num] = result

    if failed:
        async with manifest_lock:
            manifest[pdf_name] = {
                "status": "failed_chunks",
                "failed_chunks": list(failed.keys()),
            }
            save_manifest(manifest)
        return None

    return list(validated_results)


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
    from agents.openai.api_client import extract_chunk  # noqa: PLC0415

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
    # _run_parallel_chunks now uses RepairRetryLoop internally, so results
    # are already validated compact extraction dicts (not raw strings).
    validated_results = await _run_parallel_chunks(
        chunk_sources, chunk_fields_for_llm, valid_location_ids, api_semaphore, pdf_name,
        num_chunks, enable_prewarm, chunk_model, synthesis_model,
        prewarm_synthesis_diff, manifest, manifest_lock,
        max_log_response_chars=int(openai_config.get("max_log_response_chars", 500)),
        debug_artifact_dir=openai_config.get("debug_artifact_dir"),
    )
    if validated_results is None:
        return None

    # Step 3: reconstruct prior-context for synthesis from validated results.
    extraction_chunks = sorted(
        i for i in range(1, num_chunks) if i in chunk_fields_for_llm
    )
    logger.debug(
        "%s extraction_chunks validated: %s",
        pdf_name, extraction_chunks,
    )
    prior_context: list[dict] = list(prefilled_fields)
    for chunk_idx, validated in zip(extraction_chunks, validated_results):
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
            # Safe bounded logging of the synthesis response (Requirement 6)
            log_model_response(
                logger,
                synthesis_raw,
                pdf_name=pdf_name,
                chunk_num=synthesis_chunk,
                max_chars=int(openai_config.get("max_log_response_chars", 500)),
                debug_artifact_dir=openai_config.get("debug_artifact_dir"),
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
        normalizer_name = openai_config.get("exported_value_normalizer", "AggressiveNormalizer")
        normalizer = _get_normalizer(normalizer_name)

    write_ok = _save_pdf_output(pdf_name, all_fields, normalizer=normalizer, manifest=manifest)

    if write_ok:
        async with manifest_lock:
            manifest[pdf_name] = {"status": "complete"}
            save_manifest(manifest)
        logger.info(f"DONE  {pdf_name} -- {len(all_fields)} fields extracted")
        return all_fields
    else:
        # Validation failed — manifest already updated inside _save_pdf_output
        return None
