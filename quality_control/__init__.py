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
- builtin_impls/          — concrete default implementations of the three ABCs
- checks/                 — QC check classes (SourceTextPresenceCheck, etc.)
- concerns/               — injectable strategy objects (TextFidelity, etc.)

Note: artifact_generator.py (canonical artifact generation, PDF-specific pre-QC)
      is located in pdf_extractor/ as it deals with GROBID and PyMuPDF formats.

Public API
----------
Generic entry point (any domain):

    run_pipeline(branches, *, rater_fn, iaa_fn, adjudicator_fn, reconciler_fn, config)
        -> QCBundle

PDF-specific entry point:

    run_quality_control(branches, document_id, config) -> QCBundle
"""

from .quality_control import run_pipeline, run_quality_control

# ABCs and data containers — always import from here
from .models import (
    SemanticLayer,
    StructuralLayer,
    AlignmentRecord,
    DocumentAlignment,
    Candidate,
    QCBundle,
    QualityMetrics,
    InterRaterMetrics,
    AdjudicationRules,
    UnifiedRecord,
    ExtractionCoverageMetricRecord,
    VerificationResult,
)

# Concrete defaults — re-exported here for backwards compatibility;
# prefer importing directly from quality_control.builtin_impls for new code
from .builtin_impls import (
    QualityReport,
    InterRaterReport,
    AdjudicationDecision,
)

from .local_metrics import ExtractionCoverageReport

from .checks import (
    SourceTextPresenceCheck,
    SemanticSourceVerificationCheck,
    ExtractorAgreementCheck,
    build_task_quality_scaffold,
)

from .structure_validator import StructureSchemaLoadError, StructureSchemaValidator

from .validator import ValidationResult, Validator

from .validate_context import ValidationError, validate_qc_context_input

__all__ = [
    # pipeline entry points
    "run_pipeline",
    "run_quality_control",
    # data containers
    "SemanticLayer",
    "StructuralLayer",
    "AlignmentRecord",
    "DocumentAlignment",
    "Candidate",
    "QCBundle",
    "UnifiedRecord",
    "ExtractionCoverageMetricRecord",
    "VerificationResult",
    # ABCs
    "QualityMetrics",
    "InterRaterMetrics",
    "AdjudicationRules",
    # concrete defaults
    "QualityReport",
    "InterRaterReport",
    "AdjudicationDecision",
    # local metrics
    "ExtractionCoverageReport",
    # QC check classes
    "SourceTextPresenceCheck",
    "SemanticSourceVerificationCheck",
    "ExtractorAgreementCheck",
    "build_task_quality_scaffold",
    # structure validator
    "StructureSchemaLoadError",
    "StructureSchemaValidator",
    # generic validator engine
    "ValidationResult",
    "Validator",
    # context validation
    "ValidationError",
    "validate_qc_context_input",
]
