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

# Same-package, lightweight modules (no heavy optional deps) -- safe to import
# at module level, unlike agents.openai.* which is lazily imported throughout
# this file to accommodate test-time sys.modules mocking (see RepairRetryLoop
# and process_pdf below).
from .deterministic_merge import deterministic_merge
from . import token_budget

logger = get_logger(__name__)

# Confidence ranking used to select the highest-confidence candidates when
# capping conflicting-field candidates for compact synthesis input (Req 4.7,
# Property 12). Mirrors deterministic_merge._CONFIDENCE_RANK.
_CONFIDENCE_RANK = {"h": 3, "m": 2, "l": 1, "nr": 0}

# Max chars of a snippet included per conflicting-field candidate in the
# compact synthesis input (Req 4.2, Property 13).
_SYNTHESIS_SNIPPET_MAX_CHARS = 200

# Max candidates sent to synthesis per conflicting field (Req 4.7, Property 12).
_MAX_SYNTHESIS_CANDIDATES = 5

# Max chars of the raw invalid-output fragment included in a repair prompt
# (Req 6.1, Req 6.2 / Property 14 -- keeps the repair prompt well below the
# size of the original chunk prompt even for large malformed responses).
_MAX_REPAIR_FRAGMENT_CHARS = 2000

# Safety margin (chars) subtracted from `original_prompt_chars` to derive the
# repair prompt's hard size budget (Req 6.2 / Property 14). This is exact,
# not a heuristic: `estimate_tokens` is `len(text) // 4`, and 4 chars is
# exactly one token-bucket, so for any two lengths where
# `len(a) <= len(b) - _SIZE_MARGIN_CHARS`, `len(a) // 4 < len(b) // 4` holds
# unconditionally, regardless of either length's remainder mod 4.
_SIZE_MARGIN_CHARS = 4

# Terse, single-line fallback of _COMPACT_SCHEMA_FORMAT used only when the
# original chunk prompt is small enough that even the fixed repair-prompt
# template overhead (error message + "REQUIRED FORMAT" + the full
# _COMPACT_SCHEMA_FORMAT block) would risk meeting or exceeding it.
_COMPACT_SCHEMA_FORMAT_TERSE = (
    'Keys: "i" (int index), "v" (str value), "loc" (array of str), '
    '"c" ("h"|"m"|"l"|"nr"). Wrap in {"extractions": [...]}.'
)


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
# Token budget enforcement (Requirement 7)
# ---------------------------------------------------------------------------


def _flat_prompt_size(prompt_parts: dict[str, str]) -> str:
    """Concatenate prompt section texts for a cheap token-budget estimate.

    Order does not matter here -- ``token_budget.estimate_tokens`` only
    depends on total character count, so this is just a convenience join for
    ``check_budget``'s single-string signature.
    """
    return "".join(prompt_parts.values())


def _check_and_mitigate_budget(
    *,
    stage: str,
    system_text: str,
    evidence_text: str,
    field_definitions_text: str,
    prior_context_text: str,
    budgets: dict[str, int],
    evidence_config: dict,
    pdf_name: str,
    chunk_num: int,
) -> str:
    """Check a prompt's estimated token count against its stage budget.

    An additive safety net (Requirements 7.1, 7.2): for normally-sized
    prompts this returns ``evidence_text`` unchanged after a cheap
    ``check_budget`` call. Only when the estimate exceeds the configured
    Token_Budget does this apply ``token_budget.apply_mitigation()`` (evidence
    pruning) and return the mitigated evidence text. Propagates
    ``TokenBudgetExceededError`` when mitigation cannot bring the prompt
    within budget (Req 7.2(c) rejection) -- callers let this surface as an
    ordinary chunk/synthesis failure, consistent with how other extraction
    errors are already handled.
    """
    prompt_parts = {
        "system": system_text,
        "evidence": evidence_text,
        "field_definitions": field_definitions_text,
        "prior_context": prior_context_text,
    }
    check_result = token_budget.check_budget(_flat_prompt_size(prompt_parts), stage, budgets)
    if check_result.within_budget:
        return evidence_text

    logger.warning(
        "%s chunk %d: stage %r prompt over budget (estimated=%d tokens, budget=%d); "
        "applying mitigation",
        pdf_name, chunk_num, stage, check_result.estimated_tokens, check_result.budget_limit,
    )
    mitigated_text, warnings = token_budget.apply_mitigation(
        prompt_parts, stage, check_result.budget_limit, evidence_config,
    )
    for warning in warnings:
        logger.warning("%s chunk %d: %s", pdf_name, chunk_num, warning)

    # Only the "evidence" section is ever mutated by apply_mitigation (see
    # token_budget._prune_evidence); recover it by slicing off the unchanged
    # system/field_definitions/prior_context lengths from the mitigated join.
    prefix_len = len(system_text)
    suffix_len = len(field_definitions_text) + len(prior_context_text)
    end = len(mitigated_text) - suffix_len
    return mitigated_text[prefix_len:end] if end >= prefix_len else ""


# ---------------------------------------------------------------------------
# Compact synthesis input construction (Requirement 4)
# ---------------------------------------------------------------------------


def _truncate_snippet(text: "str | None", limit: int = _SYNTHESIS_SNIPPET_MAX_CHARS) -> str:
    """Truncate ``text`` to at most ``limit`` chars at the nearest word boundary.

    Property 13: the result is always <= ``limit`` characters. When the text
    already fits, it is returned unchanged. When it doesn't, the function
    prefers cutting at the last whitespace at or before ``limit`` so words
    aren't split mid-token; if no whitespace is found (e.g. one long token),
    it hard-truncates at ``limit``.
    """
    if not text:
        return ""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space]
    return truncated


def _confidence_rank(confidence: "str | None") -> int:
    """Rank a confidence label for highest-first selection; unknown ranks lowest."""
    if confidence is None:
        return -1
    return _CONFIDENCE_RANK.get(confidence, -1)


def _gather_field_candidates(
    field_index: int,
    extraction_chunk_nums: list[int],
    validated_results: list[list[dict]],
) -> list[dict]:
    """Collect every chunk's compact-format entry for ``field_index``.

    ``extraction_chunk_nums`` and ``validated_results`` are parallel lists
    (chunk number -> that chunk's validated compact extraction dicts).
    """
    candidates: list[dict] = []
    for _, validated in zip(extraction_chunk_nums, validated_results):
        for entry in validated:
            if entry.get("i") == field_index:
                candidates.append(entry)
    return candidates


def _build_conflict_candidate_records(
    field_index: int,
    candidates: list[dict],
    field_lookup: dict[int, dict],
    evidence_map: dict[str, dict],
    max_candidates: int = _MAX_SYNTHESIS_CANDIDATES,
) -> list[dict]:
    """Build compact candidate records for one conflicting field (Req 4.2, 4.7).

    Each record contains only field index, field name, candidate value,
    confidence label, Evidence_IDs, and a <=200-char evidence snippet --
    never the full evidence package or full prior chunk prompt text (Req
    4.1). Caps at ``max_candidates`` entries, keeping the highest-confidence
    ones (Property 12); ties are broken by original candidate order for
    determinism.
    """
    field_name = field_lookup.get(field_index, {}).get("field_name", "")

    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (-_confidence_rank(pair[1].get("c")), pair[0]),
    )
    top = [candidate for _, candidate in ranked[:max_candidates]]

    records: list[dict] = []
    for candidate in top:
        loc_ids = candidate.get("loc", []) or []
        resolved_text = "\n".join(
            evidence_map[eid]["text"]
            for eid in loc_ids
            if eid in evidence_map and evidence_map[eid].get("text")
        )
        records.append(
            {
                "field_index": field_index,
                "field_name": field_name,
                "value": candidate.get("v"),
                "confidence": candidate.get("c"),
                "evidence_ids": loc_ids,
                "snippet": _truncate_snippet(resolved_text),
            }
        )
    return records


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
        budgets: "dict[str, int] | None" = None,
        evidence_config: "dict | None" = None,
        collector: "Any | None" = None,
    ):
        self.max_repair_attempts = max_repair_attempts
        self.max_log_response_chars = max_log_response_chars
        self.debug_artifact_dir = debug_artifact_dir
        # Token-budget enforcement (Requirement 7). Both None by default --
        # an additive safety net that is a strict no-op for every existing
        # caller that doesn't pass them (see _run_parallel_chunks).
        self.budgets = budgets
        self.evidence_config = evidence_config or {}
        # Optional TelemetryCollector (agents.openai.telemetry.TelemetryCollector).
        # None by default: identical to before telemetry/repair-stage labeling
        # existed (Requirement 6.5).
        self.collector = collector

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
            token_budget.TokenBudgetExceededError: When a prompt exceeds its
                Token_Budget even after mitigation (Requirement 7.2(c)).
        """
        from agents.openai.api_client import extract_chunk  # noqa: PLC0415

        field_definitions_text = json.dumps(
            sorted(fields, key=lambda f: f.get("field_index", 0))
        )

        # --- Token budget check (Requirements 7.1, 7.2) ---
        # Additive safety net: when self.budgets is None (default for every
        # caller that doesn't opt in), this is a complete no-op and `source`
        # is used unchanged, exactly as before budget enforcement existed.
        if self.budgets is not None:
            system_text = self._get_system_prompt_text()
            source = _check_and_mitigate_budget(
                stage="extraction_chunk",
                system_text=system_text,
                evidence_text=source,
                field_definitions_text=field_definitions_text,
                prior_context_text="",
                budgets=self.budgets,
                evidence_config=self.evidence_config,
                pdf_name=pdf_name,
                chunk_num=chunk_num,
            )

        # --- Initial attempt ---
        raw = await extract_chunk(
            chunk_num,
            source,
            fields,
            semaphore,
            valid_location_ids=valid_location_ids,
            pdf_name=pdf_name,
            collector=self.collector,
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
        last_raw: str = raw

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

        # Original chunk prompt size estimate (Requirement 6.2 / Property
        # 14): only computed once we actually know a repair is needed (the
        # happy path -- valid on the first try -- never reaches this, so it
        # never pays for a system-prompt fetch it doesn't need). Independent
        # of whether budget enforcement (self.budgets) is enabled, so
        # _build_repair_prompt can guarantee the repair prompt stays
        # strictly smaller than the original regardless of how small
        # `source` happens to be, rather than relying solely on the fixed
        # _MAX_REPAIR_FRAGMENT_CHARS cap.
        original_prompt_chars = (
            len(self._get_system_prompt_text()) + len(source) + len(field_definitions_text)
        )

        # --- Repair attempts ---
        for attempt in range(1, self.max_repair_attempts + 1):
            repair_prompt = self._build_repair_prompt(
                last_error, expected_indices, raw_response=last_raw,
                original_prompt_chars=original_prompt_chars,
            )
            logger.warning(
                "%s chunk %d repair attempt %d/%d (%s error): %s",
                pdf_name, chunk_num, attempt, self.max_repair_attempts,
                last_error_type, str(last_error)[:200],
            )

            # Token budget check for the repair prompt itself (Requirements
            # 7.1, 7.2; validation_repair Stage). Additive safety net -- the
            # repair prompt is already small and bounded (see
            # _build_repair_prompt's fragment truncation), so this is not
            # expected to trigger mitigation in practice.
            if self.budgets is not None:
                repair_prompt = _check_and_mitigate_budget(
                    stage="validation_repair",
                    system_text="",
                    evidence_text=repair_prompt,
                    field_definitions_text="",
                    prior_context_text="",
                    budgets=self.budgets,
                    evidence_config=self.evidence_config,
                    pdf_name=pdf_name,
                    chunk_num=chunk_num,
                )

            raw = await extract_chunk(
                chunk_num,
                source,
                fields,
                semaphore,
                valid_location_ids=valid_location_ids,
                pdf_name=pdf_name,
                repair_prompt=repair_prompt,
                collector=self.collector,
                stage="validation_repair",
                repair_attempt=attempt,
                error_type=last_error_type,
            )
            last_raw = raw

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
        raw_response: "str | None" = None,
        original_prompt_chars: "int | None" = None,
    ) -> str:
        """Construct targeted repair prompt with error details.

        For JSON parse errors: includes the error message + required Compact_Schema format.
        For schema validation errors: lists specific failures (missing keys, invalid
        confidence, out-of-range indexes) and specifies valid field-index range.

        ``raw_response``, when provided, is the invalid model output fragment
        that failed validation (Requirement 6.1). It is appended as its own
        section, truncated to ``_MAX_REPAIR_FRAGMENT_CHARS`` so the repair
        prompt stays well below the size of the original chunk prompt (Req
        6.2, Property 14) even for large malformed responses. Defaults to
        ``None`` (omits the section entirely) so direct unit-test calls to
        this method that don't pass a raw response keep their exact prior
        behavior.

        ``original_prompt_chars``, when provided, is the char length of the
        original chunk prompt this repair is retrying. When given, the
        method GUARANTEES (not merely "usually achieves") that the returned
        prompt's estimated token count is strictly less than the original's
        (Requirement 6.2, Property 14), via a tiered fallback that
        progressively sheds size -- first the raw-response fragment, then
        the verbose ``_COMPACT_SCHEMA_FORMAT`` block, then (as an absolute
        last resort) a hard character truncation -- because the FIXED
        template overhead alone (error message + "REQUIRED FORMAT" +
        Compact_Schema reference) can itself meet or exceed a small
        ``original_prompt_chars`` (e.g. a degenerate/near-empty evidence
        package), independent of the raw-response fragment's size. Defaults
        to ``None``, in which case no size budget is enforced at all and
        only the fixed ``_MAX_REPAIR_FRAGMENT_CHARS`` cap applies to the
        fragment (preserves prior behavior for direct unit-test calls that
        don't pass this argument).
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
            error_line = f"JSON PARSE ERROR: {error}"
            parts.append(error_line)
            parts.append("")
            parts.append("REQUIRED FORMAT:")
            parts.append(_COMPACT_SCHEMA_FORMAT)
        elif isinstance(error, ValidationError):
            error_msg = str(error)
            error_line = f"SCHEMA VALIDATION ERROR: {error_msg}"
            parts.append(error_line)
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
            error_line = f"ERROR: {error}"
            parts.append(error_line)
            parts.append("")
            parts.append("REQUIRED FORMAT:")
            parts.append(_COMPACT_SCHEMA_FORMAT)

        base_prompt = "\n".join(parts)

        # Attach the raw-response fragment section (Requirement 6.1), bounded
        # so the whole repair prompt stays well below _MAX_REPAIR_FRAGMENT_CHARS
        # even for a large malformed response, and (when original_prompt_chars
        # is given) additionally bounded toward keeping the whole repair
        # prompt under the original's size. This alone is not a *guarantee*
        # when original_prompt_chars is small (see the tiered fallback
        # below) -- it just keeps the common case tight without needing to
        # fall back to a less-detailed prompt.
        full_prompt = base_prompt
        if raw_response:
            max_fragment_chars = _MAX_REPAIR_FRAGMENT_CHARS
            if original_prompt_chars is not None:
                base_len = len(base_prompt) + len("\n\nINVALID OUTPUT THAT FAILED VALIDATION:\n")
                budget_for_fragment = original_prompt_chars - base_len - _SIZE_MARGIN_CHARS
                max_fragment_chars = max(0, min(max_fragment_chars, budget_for_fragment))

            fragment = raw_response
            if len(fragment) > max_fragment_chars:
                suffix = "... [truncated]"
                if max_fragment_chars > len(suffix):
                    fragment = fragment[: max_fragment_chars - len(suffix)] + suffix
                else:
                    fragment = fragment[:max_fragment_chars]

            if fragment:
                full_prompt = (
                    base_prompt + "\n\nINVALID OUTPUT THAT FAILED VALIDATION:\n" + fragment
                )

        if original_prompt_chars is None:
            return full_prompt

        # --- Strict-size guarantee (Requirement 6.2 / Property 14) ---
        # `_SIZE_MARGIN_CHARS` is exact (see its docstring), so any prompt
        # with `len(prompt) <= budget` is guaranteed to have a strictly
        # smaller `chars // 4` token estimate than the original. Try
        # progressively more compact representations until one fits;
        # the final hard-truncation tier always fits by construction, so
        # this loop always terminates with a prompt satisfying the
        # invariant.
        budget = max(0, original_prompt_chars - _SIZE_MARGIN_CHARS)

        if len(full_prompt) <= budget:
            return full_prompt

        # Tier 2: drop the raw-response fragment section entirely -- the
        # fragment is supplementary context, not essential to retrying
        # correctly.
        if len(base_prompt) <= budget:
            return base_prompt

        # Tier 3: the fixed overhead itself (error message + "REQUIRED
        # FORMAT:" heading + the verbose, multi-line _COMPACT_SCHEMA_FORMAT
        # block) is too large relative to a small original prompt. Replace
        # the verbose schema block with a terse one-line reference while
        # preserving the essential validation-error message (error_line) --
        # per Requirement 6.2's intent, we shed non-essential schema
        # documentation, not the error information itself.
        terse_prompt = (
            "Invalid output. Fix and resend.\n"
            f"{error_line}\n"
            f"Format: {_COMPACT_SCHEMA_FORMAT_TERSE}"
        )
        if len(terse_prompt) <= budget:
            return terse_prompt

        # Tier 4 (absolute last resort): hard-truncate. Always satisfies the
        # invariant since the result's length is capped at `budget` by
        # construction (this only triggers when even a terse prompt exceeds
        # a pathologically small original_prompt_chars).
        return terse_prompt[:budget]

    @staticmethod
    def _get_system_prompt_text() -> str:
        """Lazily fetch the system prompt text for token-budget estimation.

        Lazy import mirrors the ``agents.openai.api_client`` import pattern
        used elsewhere in this module: it keeps ``agents.openai.*`` out of
        this module's import-time dependency graph so tests can mock those
        submodules in ``sys.modules`` before importing pdf_processor.
        """
        from agents.openai.prompts import get_system_prompt  # noqa: PLC0415
        return get_system_prompt()


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
    budgets: "dict[str, int] | None" = None,
    evidence_config: "dict | None" = None,
    collector: "Any | None" = None,
) -> Optional[list]:
    """Run extraction chunks 1..(num_chunks-1) in parallel with validation-aware retries.

    Uses RepairRetryLoop to automatically repair malformed LLM responses.
    Returns a list of validated chunk results (list[dict] per chunk), or None
    if any chunk failed after repair exhaustion.

    Also fires a synthesis-shaped warmup task concurrently if configured.
    The synthesis warmup extends the cached prefix past the extraction map
    for the synthesis chunk, which is the most the server-side prompt cache
    can preserve (prior_context is data-dependent and cannot be cached).

    ``budgets``, ``evidence_config``, and ``collector`` are optional
    (Requirements 6.5, 7.1, 7.2): when ``budgets`` is None (the default, and
    every call site prior to this feature), RepairRetryLoop performs no
    token-budget checks at all -- behavior is identical to before token
    budget enforcement existed.
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
                warm_pdf_cache(
                    warm_source, api_semaphore, pdf_name=pdf_name, model=chunk_model,
                    collector=collector,
                ),
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
                        tag_suffix="warmup-synth", collector=collector,
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
        budgets=budgets,
        evidence_config=evidence_config,
        collector=collector,
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
    collector: "Any | None" = None,
) -> Optional[list[dict]]:
    """Process one PDF end-to-end. Returns extracted field list on success, None on failure.

    ``collector`` is an optional ``agents.openai.telemetry.TelemetryCollector``
    (Requirement 6.5); defaults to ``None``, in which case no telemetry is
    recorded -- identical to before telemetry existed.
    """
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

    # Token budget enforcement (Requirement 7): always resolved from config
    # (load_budgets falls back to documented defaults for missing/invalid
    # entries), and always applied to real dispatches below. For
    # normally-sized prompts this is a pure safety net -- check_budget()
    # passes and no mitigation is ever triggered.
    budgets = token_budget.load_budgets(openai_config)
    evidence_config = {
        "max_evidence_items_per_chunk": max_evidence_items,
        "max_evidence_chars_per_chunk": max_evidence_chars,
    }

    # Full field definitions by index (needed to build an EXTRACTION MAP for
    # the synthesis call when Deterministic_Merge finds a genuine conflict
    # among extraction-chunk fields -- see Step 4 below). `chunk_fields` is
    # the caller-supplied, unfiltered per-chunk map, so this covers every
    # field regardless of which chunk originally owned it.
    all_field_defs_by_index: dict[int, dict] = {
        f["field_index"]: f
        for fields in chunk_fields.values()
        for f in fields
    }

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
        budgets=budgets,
        evidence_config=evidence_config,
        collector=collector,
    )
    if validated_results is None:
        return None

    # Step 3: deterministic merge of extraction-chunk results (Requirement 5).
    # Chunks 1..(num_chunks-1) are assigned disjoint field ranges (each
    # field_index is owned by exactly one chunk in this pipeline's
    # domain-to-chunk partitioning), so deterministic_merge's "conflict"
    # case is structurally unreachable today for these fields -- but running
    # it unconditionally still buys real, additive correctness: explicit
    # "nr"/"nr" handling for missing values (Req 5.2), whitespace
    # normalization (Req 5.1/5.5), and future-proofing if the domain-to-chunk
    # mapping is ever reconfigured to assign a domain to more than one
    # chunk. `total_fields=62` matches configs/extraction_map.json's fixed
    # field count.
    extraction_chunks = sorted(
        i for i in range(1, num_chunks) if i in chunk_fields_for_llm
    )
    logger.debug(
        "%s extraction_chunks validated: %s",
        pdf_name, extraction_chunks,
    )
    merge_result = deterministic_merge(validated_results, total_fields=62)
    extraction_field_indices = {
        f["field_index"]
        for i in extraction_chunks
        for f in chunk_fields_for_llm.get(i, [])
    }
    # Restrict merge output to fields actually dispatched to an extraction
    # chunk this run -- fields with no provider at all here (e.g. fields
    # exclusively owned by the synthesis chunk itself, or prefilled fields)
    # must not be silently recorded as "nr" by the merge; they're handled by
    # prefilled_fields / the synthesis dispatch below instead.
    merged_fields_filtered = [
        f for f in merge_result.merged_fields if f["i"] in extraction_field_indices
    ]
    conflicts_filtered = sorted(
        i for i in merge_result.conflicts if i in extraction_field_indices
    )
    if conflicts_filtered:
        logger.info(
            "%s: deterministic merge found %d conflicting field(s) requiring "
            "LLM synthesis: %s",
            pdf_name, len(conflicts_filtered), conflicts_filtered,
        )

    prior_context: list[dict] = list(prefilled_fields)
    prior_context.extend(
        reconstruct_fields(merged_fields_filtered, field_lookup, bundle.evidence_map)
    )
    prior_context.sort(key=lambda x: x["field_index"])
    logger.debug(
        "%s prior_context after deterministic merge: %d fields",
        pdf_name, len(prior_context),
    )

    # Step 4: run synthesis chunk.
    #
    # "Synthesis" in this pipeline has two distinct jobs that both land on
    # the same final chunk (num_chunks):
    #   (a) adjudicate genuinely conflicting extraction-chunk fields (rare/
    #       structurally unreachable today -- see Step 3 comment above), and
    #   (b) compute the synthesis chunk's OWN exclusive fields (e.g. domain
    #       13 "reviewer assessment and critique" in the default 5-chunk
    #       config), which have zero prior candidates by construction and
    #       therefore can NEVER be produced by Deterministic_Merge.
    # Requirement 5.6 ("skip synthesis when all fields resolve without
    # conflict") is therefore evaluated against BOTH jobs combined: synthesis
    # is only skipped when there is truly nothing left that requires an LLM
    # call -- no conflicts AND the synthesis chunk owns no exclusive fields.
    # Naively skipping whenever `merge_result.skipped_synthesis` is True
    # (conflicts empty) would silently stop computing the synthesis chunk's
    # own fields on every run, since disjoint per-chunk field ownership means
    # conflicts are always empty in today's configuration.
    synthesis_chunk = num_chunks
    synthesis_fields = chunk_fields_for_llm.get(synthesis_chunk, [])
    conflict_field_defs = [
        all_field_defs_by_index[fi]
        for fi in conflicts_filtered
        if fi in all_field_defs_by_index
    ]
    synthesis_field_indices = {f["field_index"] for f in synthesis_fields}
    effective_synthesis_fields = synthesis_fields + [
        f for f in conflict_field_defs if f["field_index"] not in synthesis_field_indices
    ]

    try:
        final_fields: list[dict] = []
        if effective_synthesis_fields:
            logger.debug(
                "%s synthesis chunk %d: %d fields (indices=%s), %d conflict field(s)",
                pdf_name, synthesis_chunk, len(effective_synthesis_fields),
                sorted(f.get("field_index") for f in effective_synthesis_fields),
                len(conflicts_filtered),
            )

            # Compact synthesis input (Requirement 4): only conflicting
            # fields get full candidate records (field_index, field_name,
            # value, confidence, Evidence_IDs, <=200-char snippet, capped at
            # 5 highest-confidence candidates -- Req 4.2, 4.7). Every other
            # prior field is reduced to a value-only summary (no evidence
            # text, no location metadata) -- never the full evidence
            # package or full prior chunk prompt text (Req 4.1).
            compact_prior_context: list[dict] = []
            for f in merged_fields_filtered:
                compact_prior_context.append(
                    {
                        "field_index": f["i"],
                        "field_name": field_lookup.get(f["i"], {}).get("field_name", ""),
                        "value": f["v"],
                        "confidence": f["c"],
                    }
                )
            for pf in prefilled_fields:
                compact_prior_context.append(
                    {
                        "field_index": pf["field_index"],
                        "field_name": pf["field_name"],
                        "value": pf["extracted_value"],
                        "confidence": pf["confidence"],
                    }
                )
            for field_idx in conflicts_filtered:
                candidates = _gather_field_candidates(
                    field_idx, extraction_chunks, validated_results
                )
                records = _build_conflict_candidate_records(
                    field_idx, candidates, field_lookup, bundle.evidence_map,
                )
                compact_prior_context.append(
                    {
                        "field_index": field_idx,
                        "field_name": field_lookup.get(field_idx, {}).get("field_name", ""),
                        "conflict": True,
                        "candidates": records,
                    }
                )
            compact_prior_context.sort(key=lambda x: x["field_index"])

            synthesis_source = chunk_sources.get(
                synthesis_chunk,
                paper_source or json.dumps({"paper_id": pdf_name, "evidence": []}),
            )
            synthesis_source = _check_and_mitigate_budget(
                stage="synthesis",
                system_text=RepairRetryLoop._get_system_prompt_text(),
                evidence_text=synthesis_source,
                field_definitions_text=json.dumps(
                    sorted(effective_synthesis_fields, key=lambda f: f.get("field_index", 0))
                ),
                prior_context_text=json.dumps(compact_prior_context),
                budgets=budgets,
                evidence_config=evidence_config,
                pdf_name=pdf_name,
                chunk_num=synthesis_chunk,
            )

            synthesis_raw = await extract_chunk(
                synthesis_chunk,
                synthesis_source,
                effective_synthesis_fields,
                api_semaphore,
                valid_location_ids=valid_location_ids,
                prior_context=compact_prior_context,
                pdf_name=pdf_name,
                collector=collector,
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
                f["field_index"] for f in effective_synthesis_fields
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
        else:
            logger.info(
                "%s: deterministic merge resolved all fields without conflict "
                "and synthesis chunk %d owns no exclusive fields; skipping "
                "synthesis LLM call entirely (Req 5.6)",
                pdf_name, synthesis_chunk,
            )
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
