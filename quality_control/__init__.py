"""
quality_control
---------------
Generic quality-control pipeline for adjudicating between multiple agent or
extractor outputs.  Ships with a PDF-specific implementation for the
pdf_extractor extraction pipeline, but the core orchestrator is fully
domain-agnostic and can be reused for LLM attribute extraction, multi-agent
workflows, or any other branched-output use case.

Module files
------------
- quality_control.py      — generic ``run_pipeline`` + PDF-specific ``run_quality_control``
- rater.py                — per-branch quality scoring
- iaa_calculator.py       — inter-rater agreement computation
- adjudicator.py          — quality-based selection / adjudication
- reconciler.py           — output reconciliation into UnifiedRecord

Note: artifact_generator.py (canonical artifact generation, PDF-specific pre-QC)
      is located in pdf_extractor/ as it deals with GROBID and PyMuPDF formats.

Public API
----------
Generic entry point (any domain):

    run_pipeline(branches, *, rater_fn, iaa_fn, adjudicator_fn, reconciler_fn, config)
        -> QCContext

PDF-specific entry point:

    run_quality_control(branches, document_id, config) -> QCContext
"""

from .quality_control import run_pipeline, run_quality_control
from .models import (
    BranchOutput,
    QCContext,
    QualityMetrics,
    QualityReport,
    InterRaterMetrics,
    InterRaterReport,
    AdjudicationRules,
    AdjudicationDecision,
    UnifiedRecord,
    LocalQCMetricRecord,
)
from .local_metrics import LocalQCReport

__all__ = [
    "run_pipeline",
    "run_quality_control",
    "BranchOutput",
    "QCContext",
    "QualityMetrics",
    "QualityReport",
    "InterRaterMetrics",
    "InterRaterReport",
    "AdjudicationRules",
    "AdjudicationDecision",
    "UnifiedRecord",
    "LocalQCMetricRecord",
    "LocalQCReport",
]
