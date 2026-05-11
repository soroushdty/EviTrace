"""text_processing — Standalone text processing package for EviTrace.

Provides normalizers, tokenizers, matchers (lexical + semantic), embedding
utilities, and sentence-segmentation backends.  Heavy optional dependencies
(faiss, torch, sentence-transformers, spaCy, scispaCy, etc.) are never
imported at the top level of any module in this package.
"""

from text_processing.base import (
    TextProcessor,
    SentenceSegment,
    ScispaCySentenceSegment,
    WtpSplitSentenceSegment,
    NLTKPunktSentenceSegment,
    SpacySentencizerSegment,
    StanzaSentenceSegment,
)
from text_processing.matchers import LexicalMatcher, SemanticMatcher
from text_processing.embedding import EmbeddingProcessor

__all__ = [
    "TextProcessor",
    "SentenceSegment",
    "ScispaCySentenceSegment",
    "WtpSplitSentenceSegment",
    "NLTKPunktSentenceSegment",
    "SpacySentencizerSegment",
    "StanzaSentenceSegment",
    "LexicalMatcher",
    "SemanticMatcher",
    "EmbeddingProcessor",
]
