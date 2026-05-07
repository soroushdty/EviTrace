"""
evi_trace/extraction/quality_control
------------------------------
Quality Control (QC) module for the EviTrace PDF text extraction pipeline.

Orchestrates a deterministic pipeline of artifact generation, rating,
inter-rater agreement calculation, adjudication, and reconciliation over
the outputs of the extraction backends. The result is a QCContext object
that carries all branch outputs, reports, metrics, and the unified record.

Module files:
  - quality_control.py      — QC orchestrator
  - artifact_generator.py   — External artifact generation (optional, pre-QC)
  - rater.py                — Per-branch quality scoring
  - iaa_calculator.py       — Inter-rater agreement computation
  - adjudicator.py          — Quality-based selection / adjudication
  - reconciler.py           — Output reconciliation into UnifiedRecord

Public API
----------
- run_quality_control(branches, document_id, config) -> QCContext
"""

from .quality_control import run_quality_control
from .models import (
    BranchOutput,
    QCContext,
    QualityMetrics,
    QualityReport,
    InterRaterMetrics,
    AdjudicationDecision,
    UnifiedRecord,
    LocalQCMetricRecord,
)
from .local_metrics import LocalQCReport

__all__ = [
    "run_quality_control",
    "BranchOutput",
    "QCContext",
    "QualityMetrics",
    "QualityReport",
    "InterRaterMetrics",
    "AdjudicationDecision",
    "UnifiedRecord",
    "LocalQCMetricRecord",
    "LocalQCReport",
]
