"""quality_control.checks — QC check classes and scaffold builder.

Exports:
    SourceTextPresenceCheck
    SemanticSourceVerificationCheck
    ExtractorAgreementCheck
    build_task_quality_scaffold

No top-level imports of faiss, torch, sentence_transformers, spacy,
scispacy, stanza, wtpsplit, TextProcessor, or text_processing are
permitted in this package.
"""
from __future__ import annotations

from quality_control.checks.source_text import SourceTextPresenceCheck
from quality_control.checks.semantic_source import SemanticSourceVerificationCheck
from quality_control.checks.extractor_agreement import ExtractorAgreementCheck
from quality_control.checks.task_quality import build_task_quality_scaffold

__all__ = [
    "SourceTextPresenceCheck",
    "SemanticSourceVerificationCheck",
    "ExtractorAgreementCheck",
    "build_task_quality_scaffold",
]
