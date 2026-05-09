"""
Adjudicator delegates quality-control decisions to injected concern strategies.

Each concern type (text fidelity, section verification, table/figure merge) is
handled by an injected strategy object whose ``adjudicate(alignment_entries,
config)`` method returns a decisions dict.  The adjudicator assembles per-
concern decisions into a single ``decisions`` dict and returns it directly —
no downstream reconciler call is made here.

Requirements: 7.2, 10
Boundary: quality_control/adjudicator
"""

from __future__ import annotations

import logging

from quality_control.concerns import (
    DEFAULT_TEXT_FIDELITY,
    DEFAULT_SECTION_VERIFICATION,
    DEFAULT_TABLE_FIGURE_MERGE,
)

logger = logging.getLogger("pdf_extractor")


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _adjudicate_concern(alignment_entries: list, strategy, config: dict) -> dict:
    """Delegate adjudication for a single concern to *strategy*.

    Parameters
    ----------
    alignment_entries:
        List of alignment entries relevant to this concern type.
    strategy:
        Any object with an ``adjudicate(alignment_entries, config) -> dict``
        method.
    config:
        Pipeline configuration dict forwarded to the strategy.

    Returns
    -------
    dict
        Whatever the strategy returns — at minimum ``preferred_source``,
        ``confidence``, and ``rationale``.
    """
    return strategy.adjudicate(alignment_entries, config)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def adjudicate(
    alignment_map,
    config: dict,
    *,
    text_fidelity_strategy=None,
    section_strategy=None,
    table_figure_strategy=None,
) -> dict:
    """Adjudicate extraction quality by delegating to injected concern strategies.

    For each concern type, the corresponding strategy's ``adjudicate`` method
    is called with the relevant entries from *alignment_map* and the pipeline
    *config*.  The resulting per-concern decision dicts are assembled into a
    single ``decisions`` dict that is returned to the caller.

    Parameters
    ----------
    alignment_map:
        Object with attributes ``paragraph_to_blocks``,
        ``section_header_to_block``, and ``reconciliation_flags`` (each a
        list, or anything falsy when absent).
    config:
        Pipeline configuration dict forwarded to each strategy.
    text_fidelity_strategy:
        Strategy for paragraph-level text fidelity.  Defaults to
        ``DEFAULT_TEXT_FIDELITY`` when ``None``.
    section_strategy:
        Strategy for section-heading verification.  Defaults to
        ``DEFAULT_SECTION_VERIFICATION`` when ``None``.
    table_figure_strategy:
        Strategy for table/figure merge decisions.  Defaults to
        ``DEFAULT_TABLE_FIGURE_MERGE`` when ``None``.

    Returns
    -------
    dict
        Decisions dict with zero or more of the following keys, each mapping
        to the corresponding strategy's return value:
        ``"text_fidelity"``, ``"section_verification"``, ``"table_figure"``.
    """
    if text_fidelity_strategy is None:
        text_fidelity_strategy = DEFAULT_TEXT_FIDELITY
    if section_strategy is None:
        section_strategy = DEFAULT_SECTION_VERIFICATION
    if table_figure_strategy is None:
        table_figure_strategy = DEFAULT_TABLE_FIGURE_MERGE

    decisions: dict = {}

    # Adjudicate paragraph-level text fidelity
    para_entries = getattr(alignment_map, "paragraph_to_blocks", []) or []
    if para_entries:
        logger.debug("Adjudicator: adjudicating text_fidelity (%d entries)", len(para_entries))
        decisions["text_fidelity"] = _adjudicate_concern(
            para_entries, text_fidelity_strategy, config
        )

    # Adjudicate section verification
    section_entries = getattr(alignment_map, "section_header_to_block", []) or []
    if section_entries:
        logger.debug("Adjudicator: adjudicating section_verification (%d entries)", len(section_entries))
        decisions["section_verification"] = _adjudicate_concern(
            section_entries, section_strategy, config
        )

    # Adjudicate table/figure merge
    flag_entries = getattr(alignment_map, "reconciliation_flags", []) or []
    if flag_entries:
        logger.debug("Adjudicator: adjudicating table_figure (%d entries)", len(flag_entries))
        decisions["table_figure"] = _adjudicate_concern(
            flag_entries, table_figure_strategy, config
        )

    logger.info("Adjudicator: produced decisions for concerns: %s", list(decisions.keys()))
    return decisions
