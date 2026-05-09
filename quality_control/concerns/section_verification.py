"""
quality_control/concerns/section_verification.py
-------------------------------------------------
Section heading confidence strategy.

Compares the heading text of a primary section against a reference font block,
optionally penalizing confidence when the reference block's font size falls
below a configured median threshold.

Design reference: .kiro/specs/architecture-migration/design.md
                  §Concern Strategy Package (SectionVerificationConcern)
Requirements: 7.3
Boundary: quality_control/concerns/section_verification
"""

from __future__ import annotations


# Default median font size threshold below which a penalty is applied.
# This matches the design's ``section_verification.font_size_tolerance`` config key
# and represents the minimum acceptable heading font size.
_DEFAULT_MEDIAN_FONT_SIZE: float = 10.0


class SectionVerificationConcern:
    """Section heading confidence strategy.

    Computes a confidence score ``[0.0, 1.0]`` for a primary section heading
    against a reference structural block.

    Args:
        median_font_size: Font size below which confidence is proportionally
            reduced.  Defaults to 10.0 pt (a typical body-text font size).
        font_size_tolerance: Tolerance added to ``median_font_size`` when
            computing the penalty boundary.  When ``reference_block["font_size"]``
            is below ``median_font_size - font_size_tolerance``, a proportional
            penalty is applied.  Defaults to 0.0.
    """

    def __init__(
        self,
        median_font_size: float = _DEFAULT_MEDIAN_FONT_SIZE,
        font_size_tolerance: float = 0.0,
    ) -> None:
        self.median_font_size = median_font_size
        self.font_size_tolerance = font_size_tolerance

    # ------------------------------------------------------------------
    # reconcile
    # ------------------------------------------------------------------

    def reconcile(
        self,
        primary_section: dict,
        reference_block: dict,
        text_processor,
    ) -> float:
        """Return a confidence score in ``[0.0, 1.0]`` for the heading match.

        The score is the heading-text similarity (from ``text_processor.compare``)
        reduced proportionally when the reference block's font size is below the
        configured median threshold.

        This method **never** modifies *primary_section* or any of its fields.

        Args:
            primary_section: Extractor-agnostic section dict.  Must have at
                least a ``"heading"`` key.
            reference_block: Structural block dict from the reference extractor.
                Must have at least ``"text"`` and ``"font_size"`` keys.
            text_processor: Object with a ``compare(a, b) -> float`` method.

        Returns:
            Confidence score in ``[0.0, 1.0]``.
        """
        # Text similarity score for the heading (read-only access to primary_section)
        primary_heading = primary_section.get("heading", "")
        reference_text = reference_block.get("text", "")
        text_score: float = text_processor.compare(primary_heading, reference_text)

        # Font-size penalty: reduce confidence proportionally when the reference
        # block's font size is below the configured median threshold.
        font_size: float = float(reference_block.get("font_size", self.median_font_size))
        threshold = self.median_font_size - self.font_size_tolerance

        if font_size >= threshold:
            font_penalty = 0.0
        else:
            # Penalty scales linearly from 0 (at threshold) to 1 (at font_size=0).
            # Clamp to [0, 1] to guard against unexpected negative font sizes.
            font_penalty = min(1.0, max(0.0, (threshold - font_size) / threshold))

        confidence = text_score * (1.0 - font_penalty)
        # Clamp to [0.0, 1.0] to guard against floating-point drift.
        return float(max(0.0, min(1.0, confidence)))

    # ------------------------------------------------------------------
    # adjudicate
    # ------------------------------------------------------------------

    def adjudicate(
        self,
        alignment_entries: list,
        config: dict,
    ) -> dict:
        """Return adjudication result across multiple section alignment entries.

        Args:
            alignment_entries: List of objects with ``confidence`` attributes.
            config: Pipeline configuration dict (reserved for future use).

        Returns:
            dict with keys ``preferred_source``, ``confidence``, and
            ``rationale``.
        """
        if not alignment_entries:
            return {
                "preferred_source": "unknown",
                "confidence": 0.0,
                "rationale": "no section entries",
            }
        best = max(alignment_entries, key=lambda e: getattr(e, "confidence", 0.0))
        return {
            "preferred_source": getattr(best, "source", "unknown"),
            "confidence": getattr(best, "confidence", 0.0),
            "rationale": f"highest section confidence: {getattr(best, 'confidence', 0.0):.3f}",
        }


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

DEFAULT_SECTION_VERIFICATION = SectionVerificationConcern()
