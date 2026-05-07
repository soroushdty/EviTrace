"""
Adjudicator evaluates which specific parts from either core branch (GROBID or
PyMuPDF) should be accepted as correct/higher quality extraction based on
user-defined criteria. It produces adjudication decisions that are passed to
the Repair module for reconciliation.

Current implementation uses placeholder logic; future versions will implement
configurable quality criteria and per-block/per-page adjudication strategies.
"""

from __future__ import annotations

import json
import logging

from . import reconciler

logger = logging.getLogger("evi_trace")


def _parse_pymupdf_blocks(pymupdf_artifact: dict) -> list[dict]:
    """Parse PyMuPDF canonical artifact content to extract blocks.

    Returns a list of BlockDict objects from the PyMuPDF JSON content.
    """
    try:
        content = pymupdf_artifact["pymupdf"]["content"]
        if isinstance(content, str):
            data = json.loads(content)
        else:
            data = content

        # PyMuPDF output is expected to be a list of blocks or a dict with blocks
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "blocks" in data:
            return data["blocks"]
        else:
            return []
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to parse PyMuPDF blocks: %s", e)
        return []


def _compute_text_quality_score(text: str) -> float:
    """Compute quality score for extracted text.

    Current implementation: ratio of alphabetic characters to total
    non-whitespace characters. Range [0.0, 1.0].

    Future: user-defined metrics and schemas.
    """
    if not text:
        return 0.0

    text_no_ws = text.replace(" ", "").replace("\n", "").replace("\t", "")
    if not text_no_ws:
        return 0.0

    alpha_count = sum(1 for c in text_no_ws if c.isalpha())
    return alpha_count / len(text_no_ws)


def _evaluate_extractor_quality(artifact: dict, observation: dict, extractor_name: str) -> dict:
    """Evaluate quality metrics for a single extractor.

    Returns a dict with quality assessment:
    {
        "extractor_name": str,
        "text_quality_score": float,
        "block_count": int,
        "total_chars": int,
        "observation_status": str,
    }
    """
    blocks = []

    if extractor_name == "pymupdf":
        blocks = _parse_pymupdf_blocks(artifact)
    elif extractor_name == "grobid":
        # GROBID parsing not yet implemented; placeholder
        blocks = []

    total_text = " ".join(block.get("text", "") for block in blocks)
    quality_score = _compute_text_quality_score(total_text)

    return {
        "extractor_name": extractor_name,
        "text_quality_score": quality_score,
        "block_count": len(blocks),
        "total_chars": len(total_text),
        "observation_status": observation.get("status", "unknown"),
    }


def _make_adjudication_decisions(
    grobid_quality: dict,
    pymupdf_quality: dict,
    investigator_object: dict,
    config: dict,
) -> dict:
    """Make adjudication decisions based on quality assessments.

    Current strategy: simple winner-takes-all based on text quality score.
    Future: configurable strategies (per-page, per-block, hybrid, etc.).

    Returns adjudication decisions dict:
    {
        "strategy": str,
        "primary_extractor": str,  # "grobid" or "pymupdf"
        "fallback_extractor": str,
        "confidence": float,
        "rationale": str,
        "per_page_decisions": dict,  # placeholder for future
        "per_block_decisions": list,  # placeholder for future
    }
    """
    strategy = config.get("quality_control", {}).get("adjudicator", {}).get("strategy", "placeholder")

    # Simple winner-takes-all based on quality score
    grobid_score = grobid_quality["text_quality_score"]
    pymupdf_score = pymupdf_quality["text_quality_score"]

    if pymupdf_score > grobid_score:
        primary = "pymupdf"
        fallback = "grobid"
        confidence = pymupdf_score
        rationale = f"PyMuPDF quality score ({pymupdf_score:.3f}) exceeds GROBID ({grobid_score:.3f})"
    elif grobid_score > pymupdf_score:
        primary = "grobid"
        fallback = "pymupdf"
        confidence = grobid_score
        rationale = f"GROBID quality score ({grobid_score:.3f}) exceeds PyMuPDF ({pymupdf_score:.3f})"
    else:
        # Tie: prefer PyMuPDF as it's more commonly available
        primary = "pymupdf"
        fallback = "grobid"
        confidence = pymupdf_score
        rationale = f"Quality scores tied ({pymupdf_score:.3f}); defaulting to PyMuPDF"

    return {
        "strategy": strategy,
        "primary_extractor": primary,
        "fallback_extractor": fallback,
        "confidence": confidence,
        "rationale": rationale,
        "grobid_quality": grobid_quality,
        "pymupdf_quality": pymupdf_quality,
        "per_page_decisions": {},  # Future: page-level adjudication
        "per_block_decisions": [],  # Future: block-level adjudication
    }


def adjudicate(
    grobid_artifact: dict,
    pymupdf_artifact: dict,
    grobid_observation: dict,
    pymupdf_observation: dict,
    investigator_object: dict,
    config: dict,
) -> dict:
    """Evaluate extraction quality and delegate reconciliation to Repair.

    Adjudicator's responsibilities:
    1. Evaluate quality of both GROBID and PyMuPDF extractions
    2. Make decisions about which extractor's output to prefer (overall, per-page, or per-block)
    3. Pass adjudication decisions to Repair for actual reconciliation

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
    config:
        Pipeline config dict.

    Returns
    -------
    dict
        Unified Output dict produced by Repair module.
    """
    logger.debug("Adjudicator: evaluating extractor quality")

    # Evaluate quality of each extractor
    grobid_quality = _evaluate_extractor_quality(
        grobid_artifact, grobid_observation, "grobid"
    )
    pymupdf_quality = _evaluate_extractor_quality(
        pymupdf_artifact, pymupdf_observation, "pymupdf"
    )

    logger.debug(
        "Adjudicator: GROBID quality score=%.3f, PyMuPDF quality score=%.3f",
        grobid_quality["text_quality_score"],
        pymupdf_quality["text_quality_score"],
    )

    # Make adjudication decisions
    adjudication_decisions = _make_adjudication_decisions(
        grobid_quality, pymupdf_quality, investigator_object, config
    )

    logger.info(
        "Adjudicator: primary=%s, confidence=%.3f, rationale=%s",
        adjudication_decisions["primary_extractor"],
        adjudication_decisions["confidence"],
        adjudication_decisions["rationale"],
    )

    # Delegate to Reconciler with adjudication decisions
    return reconciler.reconcile(
        grobid_artifact,
        pymupdf_artifact,
        grobid_observation,
        pymupdf_observation,
        investigator_object,
        adjudication_decisions,
        config,
    )
