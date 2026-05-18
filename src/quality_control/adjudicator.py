"""
Adjudicator delegates quality-control decisions to injected concern strategies.

Each concern type (text fidelity, section verification, table/figure merge) is
handled by an injected strategy object whose ``adjudicate(alignment_entries,
config)`` method returns a decisions dict.  The adjudicator assembles per-
concern decisions into a single ``decisions`` dict and returns it directly —
no downstream reconciler call is made here.

Additionally provides quality-based primary-branch selection via
:class:`BranchQualityScore` and :func:`select_primary_branch`.

Requirements: 4.1, 4.2, 4.3, 4.4, 7.2, 10
Boundary: quality_control/adjudicator
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from quality_control.concerns import (
    DEFAULT_TEXT_FIDELITY,
    DEFAULT_SECTION_VERIFICATION,
    DEFAULT_TABLE_FIGURE_MERGE,
)
from quality_control.models import Candidate

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
# Branch quality scoring (Req 4)
# ---------------------------------------------------------------------------


@dataclass
class BranchQualityScore:
    """Composite quality score for primary-branch selection.

    Each field captures one dimension of extraction quality.  The
    :attr:`composite` property combines them into a single float for ranking.

    Attributes
    ----------
    has_content:
        True when the branch payload is non-empty.
    page_coverage:
        Fraction of pages with non-empty text (0.0–1.0).
    section_structure:
        True when at least one section heading is detected.
    text_length_plausible:
        True when text length is within expected range for the document type.
    weird_char_ratio:
        Fraction of characters that are OCR noise indicators (0.0–1.0).
        Lower is better.
    agreement_score:
        Agreement with other branches (0.0–1.0).
    """

    has_content: bool
    page_coverage: float
    section_structure: bool
    text_length_plausible: bool
    weird_char_ratio: float
    agreement_score: float

    @property
    def composite(self) -> float:
        """Weighted composite score for ranking.

        Weights:
        - has_content: 0.30 (gate — without content, nothing else matters)
        - page_coverage: 0.25
        - section_structure: 0.10
        - text_length_plausible: 0.10
        - weird_char_ratio: 0.10 (inverted — lower ratio is better)
        - agreement_score: 0.15
        """
        if not self.has_content:
            return 0.0

        score = (
            0.30 * float(self.has_content)
            + 0.25 * self.page_coverage
            + 0.10 * float(self.section_structure)
            + 0.10 * float(self.text_length_plausible)
            + 0.10 * (1.0 - self.weird_char_ratio)
            + 0.15 * self.agreement_score
        )
        return score


def _is_branch_failed(branch: Candidate) -> bool:
    """Return True if a branch is considered failed or empty.

    A branch is failed when:
    - Its status is ``"fail"``
    - Its payload is None or empty (empty string, empty list, empty dict)
    """
    if branch.status == "fail":
        return True
    if branch.payload is None:
        return True
    if isinstance(branch.payload, (str, list, dict)) and not branch.payload:
        return True
    return False


def score_branch(branch: Candidate, all_branches: list[Candidate]) -> BranchQualityScore:
    """Compute a :class:`BranchQualityScore` for a single branch.

    Parameters
    ----------
    branch:
        The candidate branch to score.
    all_branches:
        All branches in the candidate set (used for agreement computation).

    Returns
    -------
    BranchQualityScore
        The computed quality score for this branch.
    """
    if _is_branch_failed(branch):
        return BranchQualityScore(
            has_content=False,
            page_coverage=0.0,
            section_structure=False,
            text_length_plausible=False,
            weird_char_ratio=1.0,
            agreement_score=0.0,
        )

    payload = branch.payload

    # --- has_content ---
    has_content = True  # Already passed _is_branch_failed check

    # --- page_coverage ---
    page_coverage = _compute_page_coverage(payload)

    # --- section_structure ---
    section_structure = _detect_section_structure(payload)

    # --- text_length_plausible ---
    text_length_plausible = _check_text_length_plausible(payload)

    # --- weird_char_ratio ---
    weird_char_ratio = _compute_weird_char_ratio(payload)

    # --- agreement_score ---
    agreement_score = _compute_agreement_score(branch, all_branches)

    return BranchQualityScore(
        has_content=has_content,
        page_coverage=page_coverage,
        section_structure=section_structure,
        text_length_plausible=text_length_plausible,
        weird_char_ratio=weird_char_ratio,
        agreement_score=agreement_score,
    )


def _extract_text(payload) -> str:
    """Extract text content from a branch payload (various formats)."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        # List of page dicts or strings
        parts = []
        for item in payload:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text", "") or item.get("content", "") or ""
                parts.append(text)
        return "\n".join(parts)
    if isinstance(payload, dict):
        return payload.get("text", "") or payload.get("content", "") or ""
    return ""


def _compute_page_coverage(payload) -> float:
    """Compute fraction of pages with non-empty text."""
    if isinstance(payload, list) and payload:
        non_empty = sum(
            1 for item in payload
            if (isinstance(item, str) and item.strip())
            or (isinstance(item, dict) and (item.get("text", "") or item.get("content", "")).strip())
        )
        return non_empty / len(payload)
    # Single string or dict — if we have content, coverage is 1.0
    text = _extract_text(payload)
    return 1.0 if text.strip() else 0.0


def _detect_section_structure(payload) -> bool:
    """Detect whether at least one section heading is present."""
    text = _extract_text(payload)
    if not text:
        return False
    # Simple heuristic: look for common section heading patterns
    import re
    # Patterns: numbered sections, ALL CAPS lines, or common headings
    heading_patterns = [
        r"^\d+\.?\s+[A-Z]",           # "1. Introduction" or "1 Introduction"
        r"^[A-Z][A-Z\s]{3,}$",        # ALL CAPS line (min 4 chars)
        r"^(?:Abstract|Introduction|Methods|Results|Discussion|Conclusion|References)\b",
    ]
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for pattern in heading_patterns:
            if re.match(pattern, line, re.IGNORECASE if "Abstract" in pattern else 0):
                return True
    return False


def _check_text_length_plausible(payload) -> bool:
    """Check if text length is within expected range for a document.

    A typical research paper has 3,000–50,000 characters. We use a generous
    range of 500–200,000 to account for variation.
    """
    text = _extract_text(payload)
    length = len(text)
    return 500 <= length <= 200_000


def _compute_weird_char_ratio(payload) -> float:
    """Compute the fraction of 'weird' characters (OCR noise indicators)."""
    text = _extract_text(payload)
    if not text:
        return 1.0
    # Weird chars: control chars (except newline/tab), replacement char,
    # and other common OCR artifacts
    weird_count = 0
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in ("\n", "\r", "\t"):
            weird_count += 1
        elif ch == "\ufffd":  # Unicode replacement character
            weird_count += 1
        elif 0xFFF0 <= code <= 0xFFFF:  # Specials block
            weird_count += 1
    return weird_count / len(text)


def _compute_agreement_score(branch: Candidate, all_branches: list[Candidate]) -> float:
    """Compute agreement between this branch and other non-failed branches.

    Uses a simple text overlap heuristic: fraction of words in this branch
    that also appear in at least one other branch.
    """
    other_branches = [
        b for b in all_branches
        if b is not branch and not _is_branch_failed(b)
    ]
    if not other_branches:
        return 1.0  # No other branches to compare against — full agreement

    branch_text = _extract_text(branch.payload)
    branch_words = set(branch_text.lower().split())
    if not branch_words:
        return 0.0

    # Collect words from all other branches
    other_words: set[str] = set()
    for other in other_branches:
        other_text = _extract_text(other.payload)
        other_words.update(other_text.lower().split())

    if not other_words:
        return 1.0  # Other branches have no words — no disagreement possible

    overlap = branch_words & other_words
    return len(overlap) / len(branch_words)


def select_primary_branch(
    branches: list[Candidate],
    config: dict,
) -> tuple[Candidate, BranchQualityScore, str]:
    """Select the primary branch based on composite quality scores.

    Parameters
    ----------
    branches:
        List of candidate branches to evaluate.
    config:
        Pipeline configuration dict. Reads ``quality_control.discard_failed_branches``.

    Returns
    -------
    tuple[Candidate, BranchQualityScore, str]
        A 3-tuple of (selected_branch, its_score, rationale).
        The rationale explains why this branch was selected, especially
        when a non-GROBID branch is chosen.

    Raises
    ------
    ValueError
        If *branches* is empty.
    """
    if not branches:
        raise ValueError("Cannot select primary branch from empty branch list")

    qc_config = config.get("quality_control", {})
    discard_failed = qc_config.get("discard_failed_branches", False)

    # Build candidate set
    if discard_failed:
        candidates = [b for b in branches if not _is_branch_failed(b)]
    else:
        candidates = list(branches)

    # If all branches were discarded, fall back to the full set
    if not candidates:
        candidates = list(branches)

    # Score each candidate
    scored: list[tuple[Candidate, BranchQualityScore]] = []
    for branch in candidates:
        score = score_branch(branch, branches)
        scored.append((branch, score))

    # Sort by composite score descending
    scored.sort(key=lambda item: item[1].composite, reverse=True)

    selected_branch, selected_score = scored[0]

    # Build rationale
    rationale = _build_selection_rationale(selected_branch, selected_score, branches)

    return selected_branch, selected_score, rationale


def _build_selection_rationale(
    selected: Candidate,
    score: BranchQualityScore,
    all_branches: list[Candidate],
) -> str:
    """Build a human-readable rationale for the primary branch selection.

    Records the reason when a non-GROBID branch is selected.
    """
    # Check if there's a GROBID branch in the set
    grobid_branch = None
    for b in all_branches:
        if b.source.lower() == "grobid":
            grobid_branch = b
            break

    if selected.source.lower() == "grobid":
        return f"GROBID selected as primary (composite={score.composite:.3f})"

    # Non-GROBID branch selected — explain why
    if grobid_branch is None:
        return (
            f"{selected.source} selected as primary (composite={score.composite:.3f}); "
            f"no GROBID branch available"
        )

    if _is_branch_failed(grobid_branch):
        if grobid_branch.payload is None or (
            isinstance(grobid_branch.payload, (str, list, dict)) and not grobid_branch.payload
        ):
            reason = "GROBID branch empty"
        else:
            reason = "GROBID branch failed"
    else:
        grobid_score = score_branch(grobid_branch, all_branches)
        reason = (
            f"GROBID quality score below threshold "
            f"(GROBID composite={grobid_score.composite:.3f}, "
            f"{selected.source} composite={score.composite:.3f})"
        )

    return f"{selected.source} selected as primary: {reason}"


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
