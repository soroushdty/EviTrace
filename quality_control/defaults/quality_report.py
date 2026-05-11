"""
quality_control/defaults/quality_report.py
-------------------------------------------
Default concrete quality report produced by the rater for one candidate.

This is the out-of-the-box implementation of :class:`QualityMetrics`.
It unconditionally passes all candidates with no domain-specific checks
applied.  Subclass :class:`~quality_control.models.QualityMetrics`
directly to inject custom per-candidate quality criteria.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quality_control.models import QualityMetrics


@dataclass
class QualityReport(QualityMetrics):
    """Concrete quality report produced by the rater for one candidate.

    Inherits ``status`` from :class:`~quality_control.models.QualityMetrics`.

    Attributes
    ----------
    source:
        Name of the contributor this report covers.
    index:
        Position of the candidate in the run.
    """

    source: str = ""
    index: int = 0

    @property
    def extractor(self) -> str:
        """Alias for ``source`` — use in extraction pipeline contexts."""
        return self.source

    @property
    def agent(self) -> str:
        """Alias for ``source`` — use in multi-agent contexts."""
        return self.source

    def passes_check(self, source: Any = None) -> bool:  # noqa: ARG002
        """Default: unconditionally pass all candidates with no checks applied."""
        self.status = "pass"
        return True
