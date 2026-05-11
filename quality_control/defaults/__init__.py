"""
quality_control/defaults/
--------------------------
Concrete default implementations of the three QC pipeline ABCs.

These are the out-of-the-box implementations shipped with EviTrace.
Domain-specific or custom implementations should live in their own
subpackage and be injected via the ``run_pipeline`` stage callables.

Exports
-------
QualityReport
    Default per-branch quality report (unconditional pass).
InterRaterReport
    Default inter-rater agreement report (pairwise pass/fail).
AdjudicationDecision
    Default adjudication decision (majority-vote election).
"""

from .quality_report import QualityReport
from .inter_rater_report import InterRaterReport
from .adjudication_decision import AdjudicationDecision

__all__ = [
    "QualityReport",
    "InterRaterReport",
    "AdjudicationDecision",
]
