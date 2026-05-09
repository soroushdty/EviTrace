"""
quality_control/concerns/table_figure_merge.py
-----------------------------------------------
Table/figure merge strategy with absence guard.

Merges a primary caption reference (e.g., from GROBID) with a reference
spatial record (e.g., from pdfplumber).  Raises ``MissingContributionError``
when either side is absent so the caller can decide how to handle the
incomplete data.

Design reference: .kiro/specs/architecture-migration/design.md
                  §Concern Strategy Package (TableFigureMergeConcern)
Requirements: 7.3
Boundary: quality_control/concerns/table_figure_merge
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# MissingContributionError
# ---------------------------------------------------------------------------

class MissingContributionError(ValueError):
    """Raised when a required side (primary or reference) is None.

    Inherits from :class:`ValueError` so callers that handle ``ValueError``
    generically still catch this exception.

    Args:
        message: Human-readable description naming the absent side
            (e.g. ``"primary side is missing"`` or
            ``"reference side is missing"``).
    """


# ---------------------------------------------------------------------------
# TableFigureMergeConcern
# ---------------------------------------------------------------------------

class TableFigureMergeConcern:
    """Table/figure merge strategy.

    Merges a primary caption reference with a reference spatial record.  The
    constructor labels encode which argument corresponds to which extractor;
    those labels become the keys in the returned merged dict.

    Args:
        primary_label: Key used for the primary side in the merged output dict.
        reference_label: Key used for the reference side in the merged output dict.
    """

    def __init__(
        self,
        primary_label: str = "primary",
        reference_label: str = "reference",
    ) -> None:
        self.primary_label = primary_label
        self.reference_label = reference_label

    # ------------------------------------------------------------------
    # merge
    # ------------------------------------------------------------------

    def merge(
        self,
        primary: dict | None,
        reference: dict | None,
    ) -> dict:
        """Merge *primary* and *reference* records into a unified dict.

        Raises :class:`MissingContributionError` naming the absent side when
        either argument is ``None``.  The *primary* record is never modified.

        Args:
            primary: Primary side record (e.g., GROBID caption reference).
                Must not be ``None``.
            reference: Reference side record (e.g., pdfplumber spatial block).
                Must not be ``None``.

        Returns:
            dict with keys:
            - ``self.primary_label`` → *primary* record (reference, not copy)
            - ``self.reference_label`` → *reference* record (reference, not copy)
            - ``"agreement"`` → ``"present"`` (both sides contributed)
            - ``"merged_text"`` → best available text from either side

        Raises:
            MissingContributionError: If *primary* or *reference* is ``None``.
        """
        # Guard: check primary first, then reference (per design spec error messages)
        if primary is None:
            raise MissingContributionError("primary side is missing")
        if reference is None:
            raise MissingContributionError("reference side is missing")

        # Build merged text from the primary caption if available, else reference text.
        merged_text: str = primary.get("caption", "") or reference.get("text", "")

        return {
            self.primary_label: primary,
            self.reference_label: reference,
            "agreement": "present",
            "merged_text": merged_text,
        }

    # ------------------------------------------------------------------
    # adjudicate
    # ------------------------------------------------------------------

    def adjudicate(
        self,
        alignment_entries: list,
        config: dict,
    ) -> dict:
        """Return adjudication result across table/figure alignment entries.

        Args:
            alignment_entries: List of objects with ``confidence`` attributes.
            config: Pipeline configuration dict (reserved for future use).

        Returns:
            dict with keys ``preferred_source``, ``confidence``, and
            ``rationale``.
        """
        if not alignment_entries:
            return {
                "preferred_source": self.primary_label,
                "confidence": 0.0,
                "rationale": "no table/figure entries",
            }
        best = max(alignment_entries, key=lambda e: getattr(e, "confidence", 0.0))
        return {
            "preferred_source": getattr(best, "source", self.primary_label),
            "confidence": getattr(best, "confidence", 0.0),
            "rationale": (
                f"highest table/figure confidence: {getattr(best, 'confidence', 0.0):.3f}"
            ),
        }


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

DEFAULT_TABLE_FIGURE_MERGE = TableFigureMergeConcern(
    primary_label="grobid",
    reference_label="pdfplumber",
)
