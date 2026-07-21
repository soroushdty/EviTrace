"""
pipeline/deterministic_merge.py
-----------------------------------------
Rule-based merge of non-conflicting chunk extraction outputs, eliminating
LLM synthesis calls when no genuine conflict exists across chunks.

Data models
-----------
MergeResult
    Result of merging chunk results: merged (compact-format) fields, the
    list of field indices requiring LLM synthesis, and whether synthesis
    can be skipped entirely.

Functions
---------
normalize_value
    Strip leading/trailing whitespace and collapse internal whitespace runs
    to a single space, for string-equivalence comparison.

deterministic_merge
    Merge chunk results per field_index, deduplicating Evidence_IDs and
    resolving confidence, marking genuinely disagreeing fields as conflicts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.logging_utils import get_logger

logger = get_logger(__name__)

# The literal value used across the pipeline (see pdf_processor.py,
# evidence_index.py) to mark a field with no reported value.
NOT_REPORTED_VALUE = "nr"
NOT_REPORTED_CONFIDENCE = "nr"

# Confidence ordering, highest first. Values not in this mapping are treated
# as the lowest rank (see _confidence_rank).
_CONFIDENCE_RANK = {"h": 3, "m": 2, "l": 1, "nr": 0}

_WHITESPACE_RUN_RE = re.compile(r"\s+")


@dataclass
class MergeResult:
    """Result of deterministically merging chunk extraction outputs.

    Attributes
    ----------
    merged_fields:
        Compact-format field dicts (``{"i", "v", "loc", "c"}``) for every
        field_index resolved without LLM synthesis.
    conflicts:
        field_index values where chunk outputs disagreed on the extracted
        value after normalization; these require LLM synthesis.
    skipped_synthesis:
        ``True`` iff ``conflicts`` is empty, i.e. every field in
        ``1..total_fields`` was resolved deterministically.
    """

    merged_fields: list[dict] = field(default_factory=list)
    conflicts: list[int] = field(default_factory=list)
    skipped_synthesis: bool = False


def normalize_value(value: "str | None") -> "str | None":
    """Strip leading/trailing whitespace and collapse internal whitespace.

    ``None`` is returned unchanged (there is nothing to normalize). Any
    other value is coerced via ``str()`` before normalization, matching the
    lenient string coercion used elsewhere in the pipeline (see
    ``validator.py::_validate_extraction_item``).
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return _WHITESPACE_RUN_RE.sub(" ", text.strip())


def _confidence_rank(confidence: "str | None") -> int:
    """Rank a confidence label for max-selection; unknown labels rank lowest."""
    if confidence is None:
        return -1
    return _CONFIDENCE_RANK.get(confidence, -1)


def _highest_confidence(confidences: list[str]) -> str:
    """Select the highest confidence label using ordering h > m > l > nr.

    Ties (e.g. two "h" labels) resolve to that shared label. If none of the
    provided labels are recognized, the first one is returned unchanged
    (defensive default; should not occur for schema-valid chunk output).
    """
    if not confidences:
        return NOT_REPORTED_CONFIDENCE
    return max(confidences, key=_confidence_rank)


def _dedup_sorted_ids(loc_lists: list[list[str]]) -> list[str]:
    """Union unique Evidence_IDs across loc lists, sorted ascending."""
    unique_ids: set[str] = set()
    for loc in loc_lists:
        unique_ids.update(loc)
    return sorted(unique_ids)


def deterministic_merge(
    chunk_results: "list[list[dict]]",
    total_fields: int = 62,
) -> MergeResult:
    """Merge non-conflicting chunk outputs without invoking the LLM.

    ``chunk_results`` is a list with one entry per chunk (in original
    dispatch order), each entry a list of compact-format field dicts
    ``{"i": int, "v": str | None, "loc": list[str], "c": str}`` produced by
    that chunk.

    Rules applied per field_index in ``1..total_fields``:

    1. Collect every chunk's entry for the field_index (chunks that omit
       the index contribute nothing).
    2. Normalize each provided value (see :func:`normalize_value`).
    3. If every chunk that provided a non-empty value agrees after
       normalization, the field is non-conflicting: the *normalized*
       string form (see :func:`normalize_value`) is used as the canonical
       value (Req 5.1). "chunk index" in ``chunk_results`` is a
       pipeline-assigned position (dispatch order), not an identity that
       is invariant under reordering the argument list itself — so
       picking a *raw*, pre-normalization string based on list position
       would make the result depend on argument order whenever
       contributors differ only in incidental whitespace (see Req 5.7).
       Using the normalized form instead makes the canonical value a pure
       function of the *multiset* of contributed values, which cannot
       change under any permutation of ``chunk_results``.
    4. If exactly one chunk provides a non-empty value and the rest provide
       null/empty/absent, that (normalized) value is used directly
       (Req 5.5).
    5. If every chunk provides null/empty/absent for the field, the field
       is assigned ``NOT_REPORTED_VALUE`` ("nr") with confidence "nr"
       (Req 5.2).
    6. Otherwise (two or more chunks provide different non-empty values
       after normalization) the field_index is recorded as a conflict and
       is excluded from ``merged_fields`` (requires LLM synthesis).

    For every non-conflicting field, ``loc`` Evidence_IDs are deduplicated
    as the sorted union across all chunks that contributed a non-empty
    value (Req 5.3), and confidence is resolved to the highest label among
    those same contributing chunks using ``h > m > l > nr`` (Req 5.4).

    The result is independent of the order of ``chunk_results`` (Req 5.7):
    every decision above (agreement/conflict/nr, canonical value, loc
    dedup, confidence resolution) is computed from the *set* or
    *multiset* of contributing values rather than from list position, so
    permuting the outer list never changes the output.

    ``skipped_synthesis`` is ``True`` iff no field_index in
    ``1..total_fields`` was a conflict (Req 5.6).

    If a chunk entry for a field has an unexpected data type (e.g. ``v``
    is not a string/None, or ``loc``/``c`` malformed), the field is logged
    at ERROR level and treated as a conflict, deferring to LLM synthesis
    rather than risking a silently wrong deterministic merge.
    """
    # Index each chunk's field entries by field_index. chunk_idx (0-based)
    # is retained only for diagnostic logging below -- it is never used to
    # decide which value wins (see canonical-value selection below).
    per_field: dict[int, list[tuple[int, dict]]] = {}
    for chunk_idx, chunk_fields in enumerate(chunk_results):
        for entry in chunk_fields:
            try:
                field_index = entry["i"]
            except (KeyError, TypeError):
                logger.error(
                    "deterministic_merge: chunk %d entry missing 'i' key or "
                    "not a dict: %r",
                    chunk_idx, entry,
                )
                continue
            per_field.setdefault(field_index, []).append((chunk_idx, entry))

    merged_fields: list[dict] = []
    conflicts: list[int] = []

    for field_index in range(1, total_fields + 1):
        entries = per_field.get(field_index, [])

        malformed = False
        non_empty: list[tuple[int, dict, str]] = []  # (chunk_idx, entry, normalized_value)
        for chunk_idx, entry in entries:
            raw_value = entry.get("v")
            loc = entry.get("loc", [])
            confidence = entry.get("c")

            if raw_value is not None and not isinstance(raw_value, str):
                logger.error(
                    "deterministic_merge: field_index=%d chunk=%d has "
                    "non-string 'v': %r; treating field as conflict",
                    field_index, chunk_idx, raw_value,
                )
                malformed = True
                continue
            if not isinstance(loc, list) or not all(isinstance(x, str) for x in loc):
                logger.error(
                    "deterministic_merge: field_index=%d chunk=%d has "
                    "malformed 'loc': %r; treating field as conflict",
                    field_index, chunk_idx, loc,
                )
                malformed = True
                continue
            if confidence is not None and not isinstance(confidence, str):
                logger.error(
                    "deterministic_merge: field_index=%d chunk=%d has "
                    "non-string 'c': %r; treating field as conflict",
                    field_index, chunk_idx, confidence,
                )
                malformed = True
                continue

            normalized = normalize_value(raw_value)
            if normalized:
                non_empty.append((chunk_idx, entry, normalized))

        if malformed:
            conflicts.append(field_index)
            continue

        if not non_empty:
            # All null/empty/absent (or no chunk provided this field at all).
            merged_fields.append(
                {
                    "i": field_index,
                    "v": NOT_REPORTED_VALUE,
                    "loc": [],
                    "c": NOT_REPORTED_CONFIDENCE,
                }
            )
            continue

        distinct_normalized = {normalized for _, _, normalized in non_empty}
        if len(distinct_normalized) > 1:
            # Genuine disagreement after normalization -> conflict.
            conflicts.append(field_index)
            continue

        # All contributing chunks agree (post-normalization), or there is
        # exactly one contributor. Canonical string form: the normalized
        # value shared by every contributor (distinct_normalized has
        # exactly one element here). This depends only on the multiset of
        # contributed values, never on chunk_results list position, so it
        # is genuinely order-independent (Req 5.7) even when contributors
        # differ only in incidental raw whitespace.
        (canonical_value,) = distinct_normalized

        loc_lists = [entry.get("loc", []) for _, entry, _ in non_empty]
        merged_loc = _dedup_sorted_ids(loc_lists)

        confidences = [entry.get("c") for _, entry, _ in non_empty if entry.get("c")]
        merged_confidence = _highest_confidence(confidences)

        merged_fields.append(
            {
                "i": field_index,
                "v": canonical_value,
                "loc": merged_loc,
                "c": merged_confidence,
            }
        )

    return MergeResult(
        merged_fields=merged_fields,
        conflicts=conflicts,
        skipped_synthesis=len(conflicts) == 0,
    )
