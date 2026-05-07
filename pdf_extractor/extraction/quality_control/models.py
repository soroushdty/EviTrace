"""
pdf_extractor/extraction/quality_control/models.py
-----------------------------------------
Shared dataclass models for the QC pipeline.

All five QC modules communicate through a single ``QCContext`` instance that
is mutated in place rather than passed by value.  This avoids copying large
extractor payloads at each step and keeps the full pipeline state inspectable
at any point.

Classes
-------
BranchOutput
    One extractor branch's output, carrying extractor name, branch index,
    native payload, and pass/fail status.

QualityMetrics
    Abstract base class defining the schema and the ``passes_check`` interface.
    Users subclass this with custom metrics.

QualityReport
    Concrete report produced by the rater for one branch.  Inherits from
    ``QualityMetrics`` and carries the actual metrics and pass/fail status.

InterRaterMetrics
    Pairwise inter-rater agreement metrics produced by the IAA Calculator.

AdjudicationDecision
    Decision produced by the Adjudicator: which extractor to prefer and why.

UnifiedRecord
    Final reconciled output produced by the Reconciler.

LocalQCMetricRecord
    Structured record for a single Metrics Tier 1 (Local_QC_Metrics) result.

QCContext
    Shared mutable state passed through all five QC modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BranchOutput:
    """One extractor branch's output.

    Attributes
    ----------
    extractor:
        Name of the extractor: ``"grobid"`` | ``"pymupdf"`` | ``"tier1"`` |
        ``"tier2"`` | ``"tier3"``.
    branch:
        Branch index (integer).
    payload:
        Native format of the extractor's output.
    status:
        Pass/fail tag: ``"pass"`` | ``"fail"`` | ``None`` (not yet rated).
    """

    extractor: str
    branch: int
    payload: Any
    status: str | None


@dataclass
class QualityMetrics:
    """Abstract base class for quality metrics.

    Users subclass this with custom metrics.  The only constraint is that all
    extractor branches within a given run must use the same ``QualityReport``
    subclass so that comparisons are fair.

    Attributes
    ----------
    status:
        Pass/fail tag: ``"pass"`` | ``"fail"`` | ``None`` (not yet rated).
    """

    status: str | None = None

    def passes_check(self, pdf: Any) -> bool:
        """Return True if this branch passes the quality check.

        Subclasses must override this method with their actual criteria.
        """
        raise NotImplementedError


@dataclass
class QualityReport(QualityMetrics):
    """Concrete quality report produced by the rater for one branch.

    Inherits ``status`` from ``QualityMetrics``.

    Attributes
    ----------
    extractor:
        Name of the extractor this report covers.
    branch:
        Branch index this report covers.
    """

    extractor: str = ""
    branch: int = 0


@dataclass
class InterRaterMetrics:
    """Pairwise inter-rater agreement metrics.

    Attributes
    ----------
    pairwise:
        Dict mapping extractor-pair keys to agreement scores.
    """

    pairwise: dict = field(default_factory=dict)


@dataclass
class AdjudicationDecision:
    """Decision produced by the Adjudicator.

    Attributes
    ----------
    primary_extractor:
        Name of the extractor whose output is preferred.
    confidence:
        Confidence score in ``[0.0, 1.0]``.
    rationale:
        Human-readable explanation of the decision.
    """

    primary_extractor: str = ""
    confidence: float = 0.0
    rationale: str = ""


@dataclass
class UnifiedRecord:
    """Final reconciled output produced by the Reconciler.

    Attributes
    ----------
    document_id:
        Stable document identifier.
    content:
        Reconciled content dict.
    """

    document_id: str = ""
    content: dict = field(default_factory=dict)


@dataclass
class LocalQCMetricRecord:
    """Structured record for a single Metrics Tier 1 (Local_QC_Metrics) result.

    Attributes
    ----------
    metric_name:
        Identifier for the metric (e.g. "min_chars_per_page", "weird_char_ratio").
    computed_value:
        The actual value computed for this metric on this branch/page.
    threshold:
        The configured threshold against which computed_value is compared.
        None when the metric has no numeric threshold (e.g. boolean checks).
    triggered:
        True when the metric fired (i.e. the check detected a potential issue).
    """

    metric_name: str
    computed_value: float | int | bool
    threshold: float | int | bool | None
    triggered: bool


@dataclass
class QCContext:
    """Shared mutable state passed through all five QC modules.

    Each module reads from and writes to specific fields:

    +-----------------------+------------------+----------------------------------+
    | Module                | Reads            | Writes                           |
    +=======================+==================+==================================+
    | artifact_generator    | branches         | external files only (no mutation)|
    | rater                 | branches         | reports, branch status           |
    | iaa_calculator        | passing reports  | iaa_metrics                      |
    | adjudicator           | reports, metrics | decision                         |
    | reconciler            | decision, branch | unified                          |
    +-----------------------+------------------+----------------------------------+

    Attributes
    ----------
    branches:
        List of all extractor branch outputs.
    reports:
        List of quality reports produced by the rater.
    iaa_metrics:
        Inter-rater agreement metrics (set by iaa_calculator).
    decision:
        Adjudication decision (set by adjudicator).
    unified:
        Final unified record (set by reconciler).
    """

    branches: list[BranchOutput]
    reports: list[QualityReport] = field(default_factory=list)
    iaa_metrics: InterRaterMetrics | None = None
    decision: AdjudicationDecision | None = None
    unified: UnifiedRecord | None = None
