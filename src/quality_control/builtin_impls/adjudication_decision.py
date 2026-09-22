"""
quality_control/builtin_impls/adjudication_decision.py
---------------------------------------------------
Default concrete adjudication decision produced by the Adjudicator.

This is the out-of-the-box implementation of
:class:`~quality_control.models.AdjudicationRules`.  It elects the extractor
with the most passing branches (majority vote).  Subclass
:class:`~quality_control.models.AdjudicationRules` directly to inject custom
adjudication logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from quality_control.models import AdjudicationRules, InterRaterMetrics, QualityMetrics


@dataclass
class AdjudicationDecision(AdjudicationRules):
    """Default concrete adjudication decision produced by the Adjudicator.

    Inherits ``primary_extractor``, ``confidence``, ``rationale``, and the
    ``adjudicate`` interface from
    :class:`~quality_control.models.AdjudicationRules`.
    """

    def adjudicate(
        self,
        reports: list[QualityMetrics],
        metrics: InterRaterMetrics,  # noqa: ARG002
    ) -> None:
        """Default: elect the extractor with the most passing branches."""
        pass_counts: dict[str, int] = {}
        for i, r in enumerate(reports):
            # Key by extractor name; fall back to the zero-based report position
            # when the name is empty or absent (documented fallback identifier).
            name = getattr(r, "extractor", "") or str(i)
            pass_counts[name] = pass_counts.get(name, 0) + (1 if r.status == "pass" else 0)
        if not pass_counts:
            self.rationale = "no reports available"
            return
        best = max(pass_counts, key=lambda k: pass_counts[k])
        self.primary_extractor = best
        self.confidence = pass_counts[best] / len(reports)
        self.rationale = f"{best} selected: {pass_counts[best]}/{len(reports)} branches passed"
