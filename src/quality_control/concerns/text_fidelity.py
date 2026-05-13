"""
quality_control/concerns/text_fidelity.py
-----------------------------------------
Asymmetric text fidelity reconciliation and adjudication strategy.

The strategy encodes which argument is authoritative: ``reference`` is always
the preferred reading.  Callers that need the opposite asymmetry simply swap
the argument order.
"""

from __future__ import annotations

from quality_control.models import AlignmentRecord


class TextFidelityConcern:
    """Asymmetric text comparison strategy.

    Args:
        source_label: Free string identifying the ground-truth source.
            Stored on :class:`~quality_control.models.AlignmentRecord` and
            returned as ``preferred_source`` by :meth:`adjudicate`.
        threshold: Edit-distance threshold (exclusive) for the ``"partial"``
            agreement band.  Entries with ``edit_distance < threshold`` are
            ``"partial"``; anything at or above is ``"divergent"``.
    """

    def __init__(
        self,
        source_label: str = "reference",
        threshold: float = 0.10,
    ) -> None:
        self.source_label = source_label
        self.threshold = threshold

    # ------------------------------------------------------------------
    # reconcile
    # ------------------------------------------------------------------

    def reconcile(
        self,
        primary: str,
        reference: str,
        text_processor,
    ) -> dict:
        """Compute alignment metadata between *primary* and *reference* texts.

        The ``reference`` argument is always the preferred reading — the
        strategy encodes which side is ground-truth through argument order,
        not through a flag.

        Args:
            primary: Text from the primary extractor.
            reference: Text from the reference/ground-truth extractor.
            text_processor: Object with a ``compare(a, b) -> float`` method
                returning a similarity ratio in ``[0.0, 1.0]``.

        Returns:
            dict with keys:
            - ``edit_distance`` (float, [0.0, 1.0])
            - ``agreement``  (``"full"`` | ``"partial"`` | ``"divergent"``)
            - ``preferred_reading`` (always equal to *reference*)
            - ``confidence`` (float, ``1.0 - edit_distance``)
        """
        similarity = text_processor.compare(primary, reference)
        edit_distance = 1.0 - similarity

        if edit_distance == 0.0:
            agreement = "full"
        elif edit_distance < self.threshold:
            agreement = "partial"
        else:
            agreement = "divergent"

        return {
            "edit_distance": edit_distance,
            "agreement": agreement,
            "preferred_reading": reference,
            "confidence": 1.0 - edit_distance,
        }

    # ------------------------------------------------------------------
    # adjudicate
    # ------------------------------------------------------------------

    def adjudicate(
        self,
        alignment_entries: list,
        config: dict,
    ) -> dict:
        """Select the best-supported source from a list of alignment entries.

        When *alignment_entries* is empty, returns a fallback using
        ``self.source_label`` with zero confidence.

        Args:
            alignment_entries: List of :class:`~quality_control.models.AlignmentRecord`
                objects (or dicts with compatible attributes via ``getattr``).
            config: Pipeline configuration dict (reserved for future thresholds).

        Returns:
            dict with keys ``preferred_source``, ``confidence``, and
            ``rationale``.
        """
        if not alignment_entries:
            return {
                "preferred_source": self.source_label,
                "confidence": 0.0,
                "rationale": "no entries",
            }

        best = min(
            alignment_entries,
            key=lambda e: getattr(e, "edit_distance", 1.0),
        )
        return {
            "preferred_source": getattr(best, "source", self.source_label),
            "confidence": getattr(best, "confidence", 1.0),
            "rationale": (
                f"lowest edit distance: {getattr(best, 'edit_distance', 0.0):.3f}"
            ),
        }


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

DEFAULT_TEXT_FIDELITY = TextFidelityConcern(source_label="pdfplumber")
