"""
Sole producer of the UnifiedRecord, which is the source of truth for all
downstream consumers (PDF reader highlighting, LLM retrieval/QA, TEI XML
export, W3C Web Annotation JSON-LD export). TEI XML and W3C JSON-LD exports
are optional downstream projections.

Receives adjudication decisions from the Adjudicator and reconciles the
outputs from two extractor branches using injectable concern strategies
(text fidelity, section verification, table/figure merge) to produce a
fully-populated UnifiedRecord with semantic, structural, and alignment layers.
"""

from __future__ import annotations

import logging
import re

from quality_control.models import (
    DocumentAlignment,
    AlignmentRecord,
    SemanticLayer,
    StructuralLayer,
    UnifiedRecord,
)

logger = logging.getLogger("pdf_extractor")

# Heuristic patterns for classifying blocks from primary artifact.
# A block whose text matches _SECTION_RE is treated as a section heading.
# A block whose text matches _REFERENCE_RE is treated as a reference.
_SECTION_RE = re.compile(r"^\s*(?:\d+[\.\d]*\s+)?\b[A-Z][A-Za-z\s]{2,50}\b\s*$")
_REFERENCE_RE = re.compile(r"^\s*\[?\d+\]?\s+\w")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_blocks(artifact: dict) -> list[dict]:
    """Return the block list from an extractor-agnostic artifact dict.

    Looks for a top-level ``"blocks"`` key first (preferred, extractor-agnostic).
    Falls back gracefully to an empty list if absent.
    """
    blocks = artifact.get("blocks")
    if isinstance(blocks, list):
        return blocks
    return []


def _classify_block(block: dict) -> str:
    """Return the block type: ``"paragraph"``, ``"section"``, ``"table"``,
    ``"figure"``, or ``"other"``.

    Priority order:
    1. Explicit ``block_type`` key in the block dict.
    2. Heuristic pattern matching on ``text``.
    """
    explicit = block.get("block_type", "")
    if explicit in ("paragraph", "section", "table", "figure"):
        return explicit

    text = block.get("text", "")
    if not text:
        return "other"

    if _REFERENCE_RE.match(text):
        return "other"  # treat as reference / other, not section

    if _SECTION_RE.match(text.strip()):
        return "section"

    return "paragraph"


def _extract_text_from_blocks(blocks: list[dict]) -> str:
    """Extract concatenated text from a list of blocks."""
    return "\n".join(block.get("text", "") for block in blocks if block.get("text"))


def _build_semantic_layer(primary_blocks: list[dict]) -> SemanticLayer:
    """Build a SemanticLayer from primary artifact blocks.

    Uses text-pattern heuristics to classify each block as a section,
    paragraph, or reference, then populates the SemanticLayer accordingly.
    """
    sections: list[dict] = []
    paragraphs: list[dict] = []
    references: list[dict] = []

    for idx, block in enumerate(primary_blocks):
        block_type = _classify_block(block)
        text = block.get("text", "")
        page_index = block.get("page_index", 0)

        if block_type == "section":
            sections.append({
                "heading": text,
                "depth": 1,
                "label": "",
                "page_index": page_index,
                "block_index": idx,
            })
        elif block_type == "paragraph":
            paragraphs.append({
                "text": text,
                "page_index": page_index,
                "block_index": idx,
            })
        else:
            # Treat as reference / other content
            references.append({
                "text": text,
                "page_index": page_index,
                "block_index": idx,
            })

    return SemanticLayer(
        metadata={},
        sections=sections,
        paragraphs=paragraphs,
        sentences=[],
        references=references,
    )


def _build_structural_layer(secondary_blocks: list[dict]) -> StructuralLayer:
    """Build a StructuralLayer from secondary artifact blocks."""
    tables: list[dict] = []
    figures: list[dict] = []
    plain_blocks: list[dict] = []

    for idx, block in enumerate(secondary_blocks):
        block_type = _classify_block(block)
        if block_type == "table":
            tables.append(block)
        elif block_type == "figure":
            figures.append(block)
        else:
            plain_blocks.append(block)

    return StructuralLayer(
        pages=[],
        blocks=plain_blocks,
        tables=tables,
        figures=figures,
    )


def _compute_sentence_to_char_range(
    sentences: list[str],
    full_text: str,
    reconciliation_flags: list,
) -> list[dict]:
    """Map each sentence to its character range in full_text.

    Sentences not found in full_text are omitted from the result and a
    ``"one_engine_only"`` flag entry is appended to reconciliation_flags.
    """
    result: list[dict] = []
    for sentence in sentences:
        if not sentence:
            continue
        start = full_text.find(sentence)
        if start == -1:
            reconciliation_flags.append(
                AlignmentRecord(
                    source="reconciler",
                    agreement="one_engine_only",
                    preferred_reading=sentence,
                    confidence=0.0,
                )
            )
        else:
            end = start + len(sentence)
            result.append({
                "sentence": sentence,
                "start": start,
                "end": end,
                "page_index": 0,  # approximate; full page attribution requires block search
            })
    return result


def _route_paragraph_blocks(
    primary_blocks: list[dict],
    secondary_blocks: list[dict],
    text_fidelity_strategy,
    text_processor,
) -> list[AlignmentRecord]:
    """Route paragraph/block pairs through text_fidelity_strategy.reconcile()."""
    entries: list[AlignmentRecord] = []

    primary_para = [b for b in primary_blocks if _classify_block(b) == "paragraph"]
    secondary_para = [b for b in secondary_blocks if _classify_block(b) == "paragraph"]

    # Pair by position (zip stops at the shorter list)
    for p_block, s_block in zip(primary_para, secondary_para):
        primary_text = p_block.get("text", "")
        secondary_text = s_block.get("text", "")

        result = text_fidelity_strategy.reconcile(primary_text, secondary_text, text_processor)

        entry = AlignmentRecord(
            source="text_fidelity",
            edit_distance=result.get("edit_distance", 0.0),
            agreement=result.get("agreement", "full"),
            preferred_reading=result.get("preferred_reading", secondary_text),
            confidence=result.get("confidence", 1.0),
        )
        entries.append(entry)

    return entries


def _route_section_blocks(
    primary_blocks: list[dict],
    secondary_blocks: list[dict],
    section_strategy,
    text_processor,
) -> list[AlignmentRecord]:
    """Route section heading pairs through section_strategy.reconcile()."""
    entries: list[AlignmentRecord] = []

    primary_sections = [b for b in primary_blocks if _classify_block(b) == "section"]
    secondary_sections = [b for b in secondary_blocks if _classify_block(b) == "section"]

    for p_block, s_block in zip(primary_sections, secondary_sections):
        primary_section = {
            "heading": p_block.get("text", ""),
            "depth": 1,
            "label": "",
        }
        reference_block = {
            "text": s_block.get("text", ""),
            "font_size": s_block.get("font_size", 10.0),
        }

        confidence = section_strategy.reconcile(primary_section, reference_block, text_processor)

        entry = AlignmentRecord(
            source="section_verification",
            agreement="full" if confidence >= 0.8 else "partial",
            preferred_reading=reference_block["text"],
            confidence=float(confidence),
        )
        entries.append(entry)

    return entries


def _route_table_figure_blocks(
    primary_blocks: list[dict],
    secondary_blocks: list[dict],
    table_figure_strategy,
) -> tuple[list[dict], list[AlignmentRecord]]:
    """Route table/figure pairs through table_figure_strategy.merge().

    Returns (merged_records, alignment_entries).
    """
    from quality_control.concerns import MissingContributionError

    merged: list[dict] = []
    entries: list[AlignmentRecord] = []

    primary_tables = [b for b in primary_blocks if _classify_block(b) in ("table", "figure")]
    secondary_tables = [b for b in secondary_blocks if _classify_block(b) in ("table", "figure")]

    for p_block, s_block in zip(primary_tables, secondary_tables):
        try:
            result = table_figure_strategy.merge(p_block, s_block)
            merged.append(result)
            entry = AlignmentRecord(
                source="table_figure_merge",
                agreement=result.get("agreement", "present"),
                preferred_reading=result.get("merged_text", ""),
                confidence=1.0,
            )
            entries.append(entry)
        except MissingContributionError:
            entry = AlignmentRecord(
                source="table_figure_merge",
                agreement="one_engine_only",
                confidence=0.0,
            )
            entries.append(entry)

    return merged, entries


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def reconcile(
    primary_artifact: dict,
    secondary_artifact: dict,
    primary_observation: dict | None = None,
    secondary_observation: dict | None = None,
    investigator_object: dict | None = None,
    adjudication_decisions: dict = None,
    config: dict | None = None,
    *,
    text_fidelity_strategy=None,
    section_strategy=None,
    table_figure_strategy=None,
    text_processor=None,
) -> UnifiedRecord:
    """Reconcile both extractor outputs to produce a UnifiedRecord.

    Repair is the sole producer of the UnifiedRecord, which serves as the
    source of truth for all downstream consumers.

    Parameters
    ----------
    primary_artifact:
        Extractor-agnostic artifact dict for the primary source.
        Role (which extractor this represents) is determined by the caller.
    secondary_artifact:
        Extractor-agnostic artifact dict for the secondary source.
    primary_observation:
        Observation object for the primary extractor.
    secondary_observation:
        Observation object for the secondary extractor.
    investigator_object:
        Investigator object with threshold checks and agreement metrics.
    adjudication_decisions:
        Adjudication decisions dict from Adjudicator containing:
        - primary_extractor: which extractor to prefer
        - confidence: confidence score
        - rationale: explanation of decision
    config:
        Pipeline config dict. If None, uses empty dict.
    text_fidelity_strategy:
        Injectable text fidelity concern strategy. Defaults to DEFAULT_TEXT_FIDELITY.
    section_strategy:
        Injectable section verification concern strategy. Defaults to
        DEFAULT_SECTION_VERIFICATION.
    table_figure_strategy:
        Injectable table/figure merge concern strategy. Defaults to
        DEFAULT_TABLE_FIGURE_MERGE.
    text_processor:
        Injectable text processor. Defaults to TextProcessor().

    Returns
    -------
    UnifiedRecord
        Final reconciled record with semantic, structural, and alignment layers
        populated alongside the content dict.
    """
    # ------------------------------------------------------------------
    # Normalise optional observations / investigator
    # ------------------------------------------------------------------
    if primary_observation is None:
        primary_observation = {}
    if secondary_observation is None:
        secondary_observation = {}
    if investigator_object is None:
        investigator_object = {}

    if adjudication_decisions is None:
        adjudication_decisions = {}

    if config is None:
        config = {}

    # ------------------------------------------------------------------
    # Resolve document_id from whichever artifact has it
    # ------------------------------------------------------------------
    document_id = primary_artifact.get("document_id") or secondary_artifact.get("document_id", "")

    # ------------------------------------------------------------------
    # Set strategy defaults
    # ------------------------------------------------------------------
    from quality_control.concerns import (
        DEFAULT_SECTION_VERIFICATION,
        DEFAULT_TABLE_FIGURE_MERGE,
        DEFAULT_TEXT_FIDELITY,
    )
    from text_processing.base import TextProcessor

    if text_fidelity_strategy is None:
        text_fidelity_strategy = DEFAULT_TEXT_FIDELITY
    if section_strategy is None:
        section_strategy = DEFAULT_SECTION_VERIFICATION
    if table_figure_strategy is None:
        table_figure_strategy = DEFAULT_TABLE_FIGURE_MERGE
    if text_processor is None:
        text_processor = TextProcessor()

    logger.debug("Repair: reconciling outputs for document_id=%s", document_id)

    # ------------------------------------------------------------------
    # Extract blocks from both artifacts (extractor-agnostic)
    # ------------------------------------------------------------------
    primary_blocks = _get_blocks(primary_artifact)
    secondary_blocks = _get_blocks(secondary_artifact)

    logger.debug(
        "Repair: extracted %d primary blocks, %d secondary blocks",
        len(primary_blocks),
        len(secondary_blocks),
    )

    # ------------------------------------------------------------------
    # Concern routing — collect AlignmentRecord objects
    # ------------------------------------------------------------------
    reconciliation_flags: list[AlignmentRecord] = []

    # 1. Text fidelity: paragraph/block pairs
    paragraph_entries = _route_paragraph_blocks(
        primary_blocks, secondary_blocks,
        text_fidelity_strategy, text_processor,
    )

    # 2. Section verification: section heading pairs
    section_entries = _route_section_blocks(
        primary_blocks, secondary_blocks,
        section_strategy, text_processor,
    )

    # 3. Table/figure merge
    _merged_tf, table_figure_entries = _route_table_figure_blocks(
        primary_blocks, secondary_blocks,
        table_figure_strategy,
    )

    # ------------------------------------------------------------------
    # Build SemanticLayer from primary artifact blocks
    # ------------------------------------------------------------------
    semantic = _build_semantic_layer(primary_blocks)

    # ------------------------------------------------------------------
    # Build StructuralLayer from secondary artifact blocks
    # ------------------------------------------------------------------
    structural = _build_structural_layer(secondary_blocks)

    # ------------------------------------------------------------------
    # Compute sentence_to_char_range
    # ------------------------------------------------------------------
    full_text = _extract_text_from_blocks(primary_blocks)
    all_sentences = [p["text"] for p in semantic.paragraphs if p.get("text")]
    sentence_to_char_range = _compute_sentence_to_char_range(
        all_sentences, full_text, reconciliation_flags
    )

    # ------------------------------------------------------------------
    # Assemble DocumentAlignment
    # ------------------------------------------------------------------
    alignment = DocumentAlignment(
        paragraph_to_blocks=paragraph_entries,
        sentence_to_char_range=sentence_to_char_range,
        section_header_to_block=section_entries,
        reconciliation_flags=reconciliation_flags,
    )

    # ------------------------------------------------------------------
    # Build content dict
    # ------------------------------------------------------------------
    provenance = _build_provenance_dict(
        primary_artifact, secondary_artifact,
        primary_observation, secondary_observation,
        investigator_object, adjudication_decisions=adjudication_decisions,
    )

    segments = [
        {
            "segment_id": f"seg-{idx}",
            "text": block.get("text", ""),
            "page_index": block.get("page_index", 0),
            "bbox": block.get("block_bbox"),
            "type": _classify_block(block),
        }
        for idx, block in enumerate(primary_blocks)
    ]

    pages_by_idx: dict[int, list[dict]] = {}
    for block in primary_blocks:
        page_idx = block.get("page_index", 0)
        pages_by_idx.setdefault(page_idx, []).append(block)

    pages = [
        {
            "page_index": page_idx,
            "text": "\n".join(b.get("text", "") for b in blks),
            "block_count": len(blks),
        }
        for page_idx, blks in sorted(pages_by_idx.items())
    ]

    content = {
        "document_id": document_id,
        "metadata": {},
        "pages": pages,
        "segments": segments,
        "annotations": [],
        "tables": [],
        "figures": [],
        "images": [],
        "exact_text": full_text,
        "provenance": provenance,
    }

    logger.info(
        "Repair: reconciliation complete for document_id=%s, blocks=%d",
        document_id,
        len(primary_blocks),
    )

    return UnifiedRecord(
        document_id=document_id,
        content=content,
        semantic=semantic,
        structural=structural,
        alignment=alignment,
    )


# ---------------------------------------------------------------------------
# Private provenance builder (shared by placeholder and full paths)
# ---------------------------------------------------------------------------


def _build_provenance_dict(
    primary_artifact: dict,
    secondary_artifact: dict,
    primary_observation: dict,
    secondary_observation: dict,
    investigator_object: dict,
    adjudication_decisions: dict | None,
) -> dict:
    """Build a provenance dict from the two artifacts.

    Uses artifact["id"] keys when available; falls back to empty strings.
    Provenance keys use extractor-agnostic names.
    """
    def _extract_id(artifact: dict, preferred_keys: tuple[str, ...]) -> str:
        """Try preferred sub-keys first, then top-level 'id'."""
        for key in preferred_keys:
            sub = artifact.get(key, {})
            if isinstance(sub, dict) and sub.get("id"):
                return sub["id"]
        return artifact.get("id", "")

    primary_id = _extract_id(primary_artifact, ("primary",))
    secondary_id = _extract_id(secondary_artifact, ("secondary",))

    provenance: dict = {
        "primary_artifact_id": primary_id,
        "secondary_artifact_id": secondary_id,
        "primary_observation": primary_observation,
        "secondary_observation": secondary_observation,
        "investigator_object": investigator_object,
    }
    if adjudication_decisions is not None:
        provenance["adjudication_decisions"] = adjudication_decisions

    return provenance
