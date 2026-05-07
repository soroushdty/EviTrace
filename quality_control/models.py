"""
quality_control/models.py
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
    Abstract base class for inter-rater agreement metrics.  Users subclass
    this with custom metric fields and implement ``compute``.

InterRaterReport
    Default concrete inter-rater report.  Inherits from ``InterRaterMetrics``
    and carries pairwise agreement scores.

AdjudicationRules
    Abstract base class for adjudication logic.  Users subclass this with
    custom decision fields and implement ``adjudicate``.

AdjudicationDecision
    Default concrete adjudication decision.  Inherits from
    ``AdjudicationRules`` and carries the preferred extractor, confidence,
    and rationale.

UnifiedRecord
    Final reconciled output produced by the Reconciler.

LocalQCMetricRecord
    Structured record for a single Metrics Tier 1 (Local_QC_Metrics) result.

QCContext
    Shared mutable state passed through all five QC modules.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from itertools import combinations
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

    @property
    def agent(self) -> str:
        """Alias for ``extractor`` — use this name in multi-agent contexts."""
        return self.extractor


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

    def passes_check(self, source: Any = None) -> bool:
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

    @property
    def agent(self) -> str:
        """Alias for ``extractor`` — use this name in multi-agent contexts."""
        return self.extractor

    def passes_check(self, source: Any = None) -> bool:  # noqa: ARG002
        """Default: unconditionally pass all branches with no checks applied."""
        self.status = "pass"
        return True


@dataclass
class InterRaterMetrics:
    """Abstract base class for inter-rater agreement metrics.

    Users subclass this with custom metric fields and implement ``compute``,
    which populates those fields from a list of quality reports.  All reports
    within a run must use the same ``InterRaterReport`` subclass so that
    comparisons are consistent.
    """

    @abstractmethod
    def compute(self, reports: list[QualityMetrics]) -> None:
        """Populate metric fields from the given quality reports.

        Subclasses must override this method with their actual computation.
        """


@dataclass
class InterRaterReport(InterRaterMetrics):
    """Default concrete inter-rater report produced by the IAA Calculator.

    Inherits the ``compute`` interface from ``InterRaterMetrics``.

    Attributes
    ----------
    pairwise:
        Dict mapping extractor-pair keys to agreement scores.
    """

    pairwise: dict = field(default_factory=dict)

    def compute(self, reports: list[QualityMetrics]) -> None:
        """Default: pairwise pass/fail agreement (1.0 = agree, 0.0 = disagree)."""
        indexed = list(enumerate(reports))
        for (i, a), (j, b) in combinations(indexed, 2):
            name_a = getattr(a, "extractor", str(i))
            name_b = getattr(b, "extractor", str(j))
            self.pairwise[f"{name_a}_vs_{name_b}"] = 1.0 if a.status == b.status else 0.0


@dataclass
class AdjudicationRules:
    """Abstract base class for adjudication logic.

    Users subclass this with custom decision fields and implement
    ``adjudicate``, which populates those fields from quality reports and
    inter-rater metrics.  The base class carries the three canonical decision
    fields so that the rest of the pipeline can always read them.

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

    @property
    def primary_agent(self) -> str:
        """Alias for ``primary_extractor`` — use this name in multi-agent contexts."""
        return self.primary_extractor

    @abstractmethod
    def adjudicate(
        self,
        reports: list[QualityMetrics],
        metrics: InterRaterMetrics,
    ) -> None:
        """Populate decision fields from reports and inter-rater metrics.

        Subclasses must override this method with their actual adjudication
        logic.
        """


@dataclass
class AdjudicationDecision(AdjudicationRules):
    """Default concrete adjudication decision produced by the Adjudicator.

    Inherits ``primary_extractor``, ``confidence``, ``rationale``, and the
    ``adjudicate`` interface from ``AdjudicationRules``.
    """

    def adjudicate(
        self,
        reports: list[QualityMetrics],
        metrics: InterRaterMetrics,  # noqa: ARG002
    ) -> None:
        """Default: elect the extractor with the most passing branches."""
        pass_counts: dict[str, int] = {}
        for i, r in enumerate(reports):
            name = getattr(r, "extractor", str(i))
            pass_counts[name] = pass_counts.get(name, 0) + (1 if r.status == "pass" else 0)
        if not pass_counts:
            self.rationale = "no reports available"
            return
        best = max(pass_counts, key=lambda k: pass_counts[k])
        self.primary_extractor = best
        self.confidence = pass_counts[best] / len(reports)
        self.rationale = f"{best} selected: {pass_counts[best]}/{len(reports)} branches passed"


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
    reports: list[QualityMetrics] = field(default_factory=list)
    iaa_metrics: InterRaterMetrics | None = None
    decision: AdjudicationRules | None = None
    unified: UnifiedRecord | None = None
    metrics_hierarchy: dict = field(default_factory=dict)
