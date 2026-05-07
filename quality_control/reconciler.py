"""
Sole producer of the Unified Output, which is the source of truth for all
downstream consumers (PDF reader highlighting, LLM retrieval/QA, TEI XML
export, W3C Web Annotation JSON-LD export). TEI XML and W3C JSON-LD exports
are optional downstream projections.

Repair receives adjudication decisions from the Adjudicator and reconciles
the outputs from both extractors to create the unified "best of both worlds"
output for downstream tasks.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("pdf_extractor")

PLACEHOLDER_NOTICE: str = (
    "Reconciliation logic is not yet implemented. "
    "This output is a structural placeholder for downstream interface stability."
)


def _extract_blocks_from_artifact(artifact: dict, extractor_name: str) -> list[dict]:
    """Extract blocks from a canonical artifact.
    
    Parameters
    ----------
    artifact:
        Canonical artifacts dict.
    extractor_name:
        "grobid" or "pymupdf".
    
    Returns
    -------
    list[dict]
        List of block dicts.
    """
    try:
        content = artifact[extractor_name]["content"]
        
        if extractor_name == "pymupdf":
            if isinstance(content, str):
                data = json.loads(content)
            else:
                data = content
            
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "blocks" in data:
                return data["blocks"]
            else:
                return []
        
        elif extractor_name == "grobid":
            # GROBID TEI XML parsing not yet implemented
            # Future: parse TEI XML and extract text blocks
            return []
        
        return []
    
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to extract blocks from %s: %s", extractor_name, e)
        return []


def _extract_text_from_blocks(blocks: list[dict]) -> str:
    """Extract concatenated text from a list of blocks."""
    return "\n".join(block.get("text", "") for block in blocks if block.get("text"))


def _organize_blocks_by_page(blocks: list[dict]) -> dict[int, list[dict]]:
    """Organize blocks by page index.
    
    Returns a dict mapping page_index -> list of blocks on that page.
    """
    pages: dict[int, list[dict]] = {}
    for block in blocks:
        page_idx = block.get("page_index", 0)
        if page_idx not in pages:
            pages[page_idx] = []
        pages[page_idx].append(block)
    return pages


def _build_page_texts(blocks: list[dict]) -> dict[str, str]:
    """Build page_texts dict from blocks.
    
    Returns a dict mapping string page numbers to concatenated text.
    """
    pages = _organize_blocks_by_page(blocks)
    return {
        str(page_idx): "\n".join(block.get("text", "") for block in page_blocks)
        for page_idx, page_blocks in pages.items()
    }


def _reconcile_blocks(
    grobid_blocks: list[dict],
    pymupdf_blocks: list[dict],
    adjudication_decisions: dict,
) -> list[dict]:
    """Reconcile blocks from both extractors based on adjudication decisions.
    
    Current implementation: simple primary/fallback strategy.
    Future: per-page or per-block reconciliation based on adjudication decisions.
    
    Parameters
    ----------
    grobid_blocks:
        Blocks from GROBID extractor.
    pymupdf_blocks:
        Blocks from PyMuPDF extractor.
    adjudication_decisions:
        Adjudication decisions dict from Adjudicator.
    
    Returns
    -------
    list[dict]
        Reconciled blocks (best of both worlds).
    """
    primary = adjudication_decisions.get("primary_extractor", "pymupdf")
    
    # Simple strategy: use primary extractor's blocks, fall back to secondary if empty
    if primary == "pymupdf":
        primary_blocks = pymupdf_blocks
        fallback_blocks = grobid_blocks
    else:
        primary_blocks = grobid_blocks
        fallback_blocks = pymupdf_blocks
    
    # Use primary if available, otherwise fallback
    if primary_blocks:
        logger.debug("Repair: using %d blocks from primary extractor (%s)", len(primary_blocks), primary)
        return primary_blocks
    else:
        logger.debug("Repair: primary extractor empty, using %d blocks from fallback", len(fallback_blocks))
        return fallback_blocks


def _build_metadata(
    grobid_artifact: dict,
    pymupdf_artifact: dict,
    adjudication_decisions: dict,
) -> dict:
    """Build metadata section of Unified Output.
    
    Future: extract title, authors, abstract, keywords from GROBID TEI XML.
    """
    return {
        "title": None,
        "authors": [],
        "abstract": None,
        "keywords": [],
        "primary_extractor": adjudication_decisions.get("primary_extractor"),
        "extraction_confidence": adjudication_decisions.get("confidence"),
    }


def _build_segments(blocks: list[dict]) -> list[dict]:
    """Build segments from reconciled blocks.
    
    Segments are logical text units (paragraphs, sections, etc.).
    Current implementation: one segment per block.
    Future: semantic segmentation, section detection.
    """
    segments = []
    for idx, block in enumerate(blocks):
        segments.append({
            "segment_id": f"seg-{idx}",
            "text": block.get("text", ""),
            "page_index": block.get("page_index", 0),
            "bbox": block.get("block_bbox"),
            "type": "paragraph",  # Future: detect section, heading, caption, etc.
        })
    return segments


def _build_geometry(blocks: list[dict]) -> dict:
    """Build geometry information from blocks.
    
    Returns bounding box information for spatial layout.
    """
    pages_with_bbox = {}
    
    for block in blocks:
        page_idx = block.get("page_index", 0)
        bbox = block.get("block_bbox")
        
        if bbox and page_idx not in pages_with_bbox:
            pages_with_bbox[page_idx] = {
                "page_index": page_idx,
                "has_geometry": True,
                "block_count": 0,
            }
        
        if page_idx in pages_with_bbox:
            pages_with_bbox[page_idx]["block_count"] += 1
    
    return {
        "pages": list(pages_with_bbox.values()),
        "coordinate_system": "pdf",  # PDF coordinate system (bottom-left origin)
    }


def reconcile(
    grobid_artifact: dict,
    pymupdf_artifact: dict,
    grobid_observation: dict,
    pymupdf_observation: dict,
    investigator_object: dict,
    adjudication_decisions: dict | None = None,
    config: dict | None = None,
) -> dict:
    """Reconcile both extractor outputs to produce the Unified Output.
    
    Repair is the sole producer of the Unified Output dict, which serves as
    the source of truth for all downstream consumers.
    
    Parameters
    ----------
    grobid_artifact:
        Canonical artifacts dict containing GROBID output.
    pymupdf_artifact:
        Canonical artifacts dict containing PyMuPDF output.
    grobid_observation:
        Observation object for GROBID extractor.
    pymupdf_observation:
        Observation object for PyMuPDF extractor.
    investigator_object:
        Investigator object with threshold checks and agreement metrics.
    adjudication_decisions:
        Adjudication decisions dict from Adjudicator containing:
        - primary_extractor: which extractor to prefer
        - confidence: confidence score
        - rationale: explanation of decision
        - per_page_decisions: page-level decisions (future)
        - per_block_decisions: block-level decisions (future)
        If None (for backward compatibility), uses a default placeholder strategy.
    config:
        Pipeline config dict. If None, uses empty dict.
    
    Returns
    -------
    dict
        Unified Output dict with the following structure:
        {
            "document_id": str,
            "metadata": dict,
            "pages": list,
            "segments": list,
            "annotations": list,
            "tables": list,
            "figures": list,
            "images": list,
            "exact_text": str,
            "geometry": dict,
            "provenance": dict,
            "observer_summary": dict,
            "investigator_summary": dict,
            "adjudication_status": str,
            "placeholder_notice": str (optional),
        }
    """
    # Backward compatibility: handle old signature reconcile(g, p, go, po, io, config)
    if config is None and isinstance(adjudication_decisions, dict):
        # Check if adjudication_decisions looks like a config dict
        if "quality_control" in adjudication_decisions or not any(
            k in adjudication_decisions for k in ["primary_extractor", "confidence", "rationale"]
        ):
            config = adjudication_decisions
            adjudication_decisions = None
    
    if config is None:
        config = {}
    
    document_id = grobid_artifact["document_id"]
    
    # If no adjudication decisions provided, use placeholder logic
    if adjudication_decisions is None:
        logger.debug("Repair: no adjudication decisions provided, using placeholder mode")
        return {
            "document_id": document_id,
            "metadata": {},
            "pages": [],
            "segments": [],
            "annotations": [],
            "tables": [],
            "figures": [],
            "images": [],
            "exact_text": "",
            "geometry": {},
            "provenance": {
                "grobid_artifact_id": grobid_artifact["grobid"]["id"],
                "pymupdf_artifact_id": pymupdf_artifact["pymupdf"]["id"],
                "grobid_observation": grobid_observation,
                "pymupdf_observation": pymupdf_observation,
                "investigator_object": investigator_object,
            },
            "observer_summary": {},
            "investigator_summary": {},
            "adjudication_status": "placeholder",
            "placeholder_notice": PLACEHOLDER_NOTICE,
        }
    
    logger.debug("Repair: reconciling outputs for document_id=%s", document_id)
    
    # Extract blocks from both artifacts
    grobid_blocks = _extract_blocks_from_artifact(grobid_artifact, "grobid")
    pymupdf_blocks = _extract_blocks_from_artifact(pymupdf_artifact, "pymupdf")
    
    logger.debug(
        "Repair: extracted %d GROBID blocks, %d PyMuPDF blocks",
        len(grobid_blocks),
        len(pymupdf_blocks),
    )
    
    # Reconcile blocks based on adjudication decisions
    reconciled_blocks = _reconcile_blocks(grobid_blocks, pymupdf_blocks, adjudication_decisions)
    
    # Build exact_text from reconciled blocks
    exact_text = _extract_text_from_blocks(reconciled_blocks)
    
    # Build page_texts
    page_texts_dict = _build_page_texts(reconciled_blocks)
    
    # Build metadata
    metadata = _build_metadata(grobid_artifact, pymupdf_artifact, adjudication_decisions)
    
    # Build segments
    segments = _build_segments(reconciled_blocks)
    
    # Build geometry
    geometry = _build_geometry(reconciled_blocks)
    
    # Organize pages
    pages_by_idx = _organize_blocks_by_page(reconciled_blocks)
    pages = [
        {
            "page_index": page_idx,
            "text": page_texts_dict.get(str(page_idx), ""),
            "block_count": len(blocks),
        }
        for page_idx, blocks in sorted(pages_by_idx.items())
    ]
    
    # Determine adjudication status
    primary = adjudication_decisions.get("primary_extractor", "unknown")
    confidence = adjudication_decisions.get("confidence", 0.0)
    
    if confidence >= 0.8:
        status = f"accepted_{primary}"
    elif confidence >= 0.5:
        status = f"accepted_{primary}_low_confidence"
    else:
        status = "needs_review"
    
    # Build provenance
    provenance = {
        "grobid_artifact_id": grobid_artifact["grobid"]["id"],
        "pymupdf_artifact_id": pymupdf_artifact["pymupdf"]["id"],
        "grobid_observation": grobid_observation,
        "pymupdf_observation": pymupdf_observation,
        "investigator_object": investigator_object,
        "adjudication_decisions": adjudication_decisions,
    }
    
    # Build observer and investigator summaries
    observer_summary = {
        "grobid_status": grobid_observation.get("status", "unknown"),
        "pymupdf_status": pymupdf_observation.get("status", "unknown"),
    }
    
    investigator_summary = {
        "decision": investigator_object.get("decision", "unknown"),
        "agreement_metrics": investigator_object.get("agreement_metrics", {}),
    }
    
    logger.info(
        "Repair: reconciliation complete for document_id=%s, status=%s, blocks=%d",
        document_id,
        status,
        len(reconciled_blocks),
    )
    
    return {
        "document_id": document_id,
        "metadata": metadata,
        "pages": pages,
        "segments": segments,
        "annotations": [],  # Future: extract annotations from GROBID
        "tables": [],  # Future: table extraction
        "figures": [],  # Future: figure extraction
        "images": [],  # Future: image extraction
        "exact_text": exact_text,
        "geometry": geometry,
        "provenance": provenance,
        "observer_summary": observer_summary,
        "investigator_summary": investigator_summary,
        "adjudication_status": status,
        "placeholder_notice": None,  # Not in placeholder mode; real reconciliation performed
    }
