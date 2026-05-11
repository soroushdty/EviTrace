"""
quality_control/models.py
-----------------------------------------
Shared dataclass models for the QC pipeline.

All five QC modules communicate through a single ``QCBundle`` instance that
is mutated in place rather than passed by value.  This avoids copying large
extractor payloads at each step and keeps the full pipeline state inspectable
at any point.

This module contains **only** abstract base classes and pure data containers.
Concrete default implementations of the ABCs live in
``quality_control/builtin_impls/``:

- :class:`~quality_control.builtin_impls.QualityReport`
- :class:`~quality_control.builtin_impls.InterRaterReport`
- :class:`~quality_control.builtin_impls.AdjudicationDecision`

Classes
-------
Candidate
    One extractor branch's output, carrying extractor name, branch index,
    native payload, and pass/fail status.

QualityMetrics
    Abstract base class defining the schema and the ``passes_check`` interface.
    Users subclass this with custom metrics.

InterRaterMetrics
    Abstract base class for inter-rater agreement metrics.  Users subclass
    this with custom metric fields and implement ``compute``.

AdjudicationRules
    Abstract base class for adjudication logic.  Users subclass this with
    custom decision fields and implement ``adjudicate``.

SemanticLayer
    Typed semantic layer holding sections, paragraphs, sentences, references,
    and document metadata.

StructuralLayer
    Typed structural layer holding pages, text blocks, tables, and figures.

AlignmentRecord
    Provenance and agreement record for one semantic-to-structural alignment.

DocumentAlignment
    Container linking semantic elements to structural blocks via alignment
    entries.

UnifiedRecord
    Final reconciled output produced by the Reconciler.

ExtractionCoverageMetricRecord
    Structured record for a single Metrics Tier 1 (Local_QC_Metrics) result.

QCBundle
    Shared mutable state passed through all five QC modules.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Candidate — one contributor's output entering the QC pipeline
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    """One contributor's output entering the QC pipeline.

    The contributor can be anything: a PDF extraction backend, an LLM agent,
    a human annotator, or any other source.  ``source`` is the canonical
    field name; ``extractor`` and ``agent`` are aliases for backwards
    compatibility and domain-specific readability.

    Attributes
    ----------
    source:
        Name of the contributor. Not constrained to a fixed set.
    index:
        Position of this candidate in the run (integer).
    payload:
        The contributor's native output in whatever format it produces.
    status:
        Pass/fail tag set by the rater: ``"pass"`` | ``"fail"`` | ``None``.
    """

    source: str
    index: int
    payload: Any
    status: str | None

    @property
    def extractor(self) -> str:
        """Alias for ``source`` — use in extraction pipeline contexts."""
        return self.source

    @property
    def agent(self) -> str:
        """Alias for ``source`` — use in multi-agent contexts."""
        return self.source


# ---------------------------------------------------------------------------
# Abstract base classes
# ---------------------------------------------------------------------------

@dataclass
class QualityMetrics:
    """Abstract base class for quality metrics.

    Users subclass this with custom metrics.  The only constraint is that all
    extractor branches within a given run must use the same ``QualityMetrics``
    subclass so that comparisons are fair.

    Attributes
    ----------
    status:
        Pass/fail tag: ``"pass"`` | ``"fail"`` | ``None`` (not yet rated).
    """

    status: str | None = None

    @abstractmethod
    def passes_check(self, source: Any = None) -> bool:
        """Return True if this branch passes the quality check.

        Subclasses must override this method with their actual criteria.
        """


@dataclass
class InterRaterMetrics:
    """Abstract base class for inter-rater agreement metrics.

    Users subclass this with custom metric fields and implement ``compute``,
    which populates those fields from a list of quality reports.  All reports
    within a run must use the same ``InterRaterMetrics`` subclass so that
    comparisons are consistent.
    """

    @abstractmethod
    def compute(self, reports: list[QualityMetrics]) -> None:
        """Populate metric fields from the given quality reports.

        Subclasses must override this method with their actual computation.
        """


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


# ---------------------------------------------------------------------------
# Typed document layers
# ---------------------------------------------------------------------------

@dataclass
class SemanticLayer:
    """Typed semantic layer of a processed document.

    Attributes
    ----------
    metadata:
        Document-level metadata dict (title, authors, abstract, etc.).
    sections:
        List of section dicts (heading, depth, label, …).
    paragraphs:
        List of paragraph dicts (text, page_index, …).
    sentences:
        List of sentence dicts (text, page_index, …).
    references:
        List of reference dicts (ref_id, text, …).
    """

    metadata: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)
    paragraphs: list = field(default_factory=list)
    sentences: list = field(default_factory=list)
    references: list = field(default_factory=list)


@dataclass
class StructuralLayer:
    """Typed structural layer of a processed document.

    Attributes
    ----------
    pages:
        List of page dicts (index, width, height in PDF user-space points).
    blocks:
        List of block dicts (bbox, text, page_index, …).
    tables:
        List of table dicts (caption, bbox, …).
    figures:
        List of figure dicts (caption, bbox, …).
    """

    pages: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    figures: list = field(default_factory=list)


@dataclass
class AlignmentRecord:
    """Provenance and agreement record for one semantic-to-structural alignment.

    Attributes
    ----------
    source:
        Free string identifying the data source; not constrained to any fixed
        extractor name set.
    ocr_derived:
        True when this entry was produced by an OCR backend.
    ocr_engines:
        List of OCR engine names that contributed to this entry.
    agreement:
        Agreement level: ``"full"`` | ``"partial"`` | ``"divergent"`` |
        ``"one_engine_only"``.
    edit_distance:
        Normalized Levenshtein distance in ``[0.0, 1.0]``.
    preferred_reading:
        The text chosen by the injected concern strategy.
    confidence:
        Confidence score in ``[0.0, 1.0]``.
    """

    source: str = "native"
    ocr_derived: bool = False
    ocr_engines: list = field(default_factory=list)
    agreement: str = "full"
    edit_distance: float = 0.0
    preferred_reading: str = ""
    confidence: float = 1.0


@dataclass
class DocumentAlignment:
    """Alignment layer produced by the Reconciler as a mandatory output.

    Always fully populated when returned from ``reconciler.reconcile()``.
    The ``| None`` typing on :class:`UnifiedRecord` reflects only that the
    field is unset before the reconciler runs — it is never ``None`` in a
    completed pipeline run.

    Attributes
    ----------
    paragraph_to_blocks:
        List of :class:`AlignmentRecord` linking paragraphs to structural
        blocks, one entry per matched paragraph/block pair.
    sentence_to_char_range:
        List of dicts mapping each sentence to its character range in the
        full document text
        (``{"sentence": str, "start": int, "end": int, "page_index": int}``).
    section_header_to_block:
        List of :class:`AlignmentRecord` linking section headings to their
        corresponding structural blocks.
    reconciliation_flags:
        List of :class:`AlignmentRecord` recording sentences or blocks that
        could not be matched across backends (``agreement="one_engine_only"``).
    """

    paragraph_to_blocks: list = field(default_factory=list)
    sentence_to_char_range: list = field(default_factory=list)
    section_header_to_block: list = field(default_factory=list)
    reconciliation_flags: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Unified output record
# ---------------------------------------------------------------------------

@dataclass
class UnifiedRecord:
    """The Reconciler's output — a composition of all four prior pipeline layers.

    Produced by ``reconciler.reconcile()`` as the final stage of the QC
    pipeline, after the rater, IAA calculator, adjudicator, and reconciler
    have each contributed their outputs.  By the time this object is returned,
    ``semantic``, ``structural``, and ``alignment`` are always populated.
    The ``| None`` typing reflects only that the fields are unset while the
    pipeline is still in progress.

    Composition
    -----------
    ``UnifiedRecord`` is a composition of four layers, each produced by a
    distinct pipeline stage:

    - ``content``   — flat dict for downstream consumers (LLM retrieval,
                      annotation export, manifest persistence)
    - ``semantic``  — typed semantic layer (:class:`SemanticLayer`), built
                      from the primary extractor's blocks
    - ``structural``— typed structural layer (:class:`StructuralLayer`),
                      built from the secondary extractor's blocks
    - ``alignment`` — alignment layer (:class:`DocumentAlignment`), the
                      mandatory by-product of reconciliation

    Attributes
    ----------
    document_id:
        Stable document identifier, resolved from whichever artifact carries it.
    content:
        Reconciled content dict carrying: ``document_id``, ``metadata``,
        ``pages``, ``segments``, ``annotations``, ``tables``, ``figures``,
        ``images``, ``exact_text``, ``provenance``.
    semantic:
        Typed semantic layer; always set after reconciliation.
    structural:
        Typed structural layer; always set after reconciliation.
    alignment:
        Alignment layer linking semantic to structural elements; always set
        after reconciliation.
    """

    document_id: str = ""
    content: dict = field(default_factory=dict)
    semantic: SemanticLayer | None = None
    structural: StructuralLayer | None = None
    alignment: DocumentAlignment | None = None


# ---------------------------------------------------------------------------
# Metric record
# ---------------------------------------------------------------------------

@dataclass
class ExtractionCoverageMetricRecord:
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


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    """Stable result dataclass produced by QC check classes.

    Each QC check class produces one ``VerificationResult`` per invocation,
    describing the outcome of a single verification check.

    Attributes
    ----------
    check_name:
        Identifier for the check that produced this result
        (e.g. ``"source_text_presence"``).
    status:
        Outcome of the check.  Must be one of ``"verified"``,
        ``"candidate_match"``, ``"no_match"``, ``"skipped"``, or
        ``"unavailable"``.
    score:
        Confidence score in the closed range ``[0.0, 1.0]``.  Raises
        ``ValueError`` on construction if outside this range.
    evidence:
        Evidence dict.  When produced by source-verification checks the dict
        contains exactly the six standard keys: ``found_sentence``,
        ``page_index``, ``prefix``, ``suffix``, ``block_bbox``,
        ``span_bboxes``.  Each key is ``None`` when no evidence is available.
    details:
        Free-form additional information.  The semantic check stores a
        below-threshold diagnostic score here under the key
        ``"below_threshold_score"``.
    """

    check_name: str
    status: str
    score: float
    evidence: dict
    details: dict

    def __post_init__(self) -> None:
        _VALID_STATUSES = {"verified", "candidate_match", "no_match", "skipped", "unavailable"}
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"status must be one of {_VALID_STATUSES!r}; got {self.status!r}"
            )
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(
                f"score must be in [0.0, 1.0]; got {self.score!r}"
            )


# ---------------------------------------------------------------------------
# Pipeline state bundle
# ---------------------------------------------------------------------------

@dataclass
class QCBundle:
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
    metrics_hierarchy:
        Tier 1/2/3 metric results keyed by tier name.
    """

    branches: list[Candidate]
    reports: list[QualityMetrics] = field(default_factory=list)
    iaa_metrics: InterRaterMetrics | None = None
    decision: AdjudicationRules | None = None
    unified: UnifiedRecord | None = None
    metrics_hierarchy: dict = field(default_factory=dict)
