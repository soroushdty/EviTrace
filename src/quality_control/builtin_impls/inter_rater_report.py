"""
quality_control/builtin_impls/inter_rater_report.py
------------------------------------------------
Default concrete inter-rater agreement report produced by the IAA Calculator.

This is the out-of-the-box implementation of
:class:`~quality_control.models.InterRaterMetrics`.  It computes pairwise
pass/fail agreement scores across all branch reports.  Subclass
:class:`~quality_control.models.InterRaterMetrics` directly to inject custom
inter-rater agreement computation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from quality_control.models import InterRaterMetrics, QualityMetrics


@dataclass
class InterRaterReport(InterRaterMetrics):
    """Default concrete inter-rater report produced by the IAA Calculator.

    Inherits the ``compute`` interface from
    :class:`~quality_control.models.InterRaterMetrics`.

    Attributes
    ----------
    pairwise:
        Dict mapping extractor-pair keys to agreement scores.
        A score of ``1.0`` means both branches agree (same pass/fail status);
        ``0.0`` means they disagree.
    """

    pairwise: dict = field(default_factory=dict)

    def compute(self, reports: list[QualityMetrics]) -> None:
        """Default: pairwise pass/fail agreement (1.0 = agree, 0.0 = disagree)."""
        indexed = list(enumerate(reports))
        for (i, a), (j, b) in combinations(indexed, 2):
            name_a = getattr(a, "extractor", str(i))
            name_b = getattr(b, "extractor", str(j))
            self.pairwise[f"{name_a}_vs_{name_b}"] = 1.0 if a.status == b.status else 0.0
