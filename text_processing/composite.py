"""Full-featured composite TextProcessor for the EviTrace pipeline.

Implements all six abstract methods of :class:`~text_processing.base.TextProcessor`
with real, functional logic using only stdlib primitives (plus ``difflib``).

The ``tokenize_sentences`` method delegates to a configurable
:class:`~text_processing.base.SentenceSegment` backend (default: NLTK Punkt).

This class is the default loaded by ``_load_text_processor()`` and by the
reconciler fallback when no explicit text processor is provided.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from text_processing.base import TextProcessor


# ---------------------------------------------------------------------------
# English stopword set (compact, no external dependency)
# ---------------------------------------------------------------------------

_STOPWORDS: frozenset[str] = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "aren't", "as", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "can't",
    "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't",
    "doing", "don't", "down", "during", "each", "few", "for", "from",
    "further", "had", "hadn't", "has", "hasn't", "have", "haven't", "having",
    "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers",
    "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll",
    "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its",
    "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
    "ought", "our", "ours", "ourselves", "out", "over", "own", "same",
    "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't",
    "so", "some", "such", "than", "that", "that's", "the", "their",
    "theirs", "them", "themselves", "then", "there", "there's", "these",
    "they", "they'd", "they'll", "they're", "they've", "this", "those",
    "through", "to", "too", "under", "until", "up", "very", "was",
    "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't",
    "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't",
    "would", "wouldn't", "you", "you'd", "you'll", "you're", "you've",
    "your", "yours", "yourself", "yourselves",
})

# Regex for C0 control characters (excluding tab \x09, LF \x0a, CR \x0d)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Regex for collapsing whitespace
_WHITESPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# DefaultTextProcessor
# ---------------------------------------------------------------------------

class DefaultTextProcessor(TextProcessor):
    """Full-featured TextProcessor for the EviTrace pipeline.

    Implements all six abstract methods with real, functional logic:

    - ``normalize`` — NFKC unicode normalization + whitespace collapsing
    - ``tokenize_words`` — split on whitespace after normalization
    - ``tokenize_sentences`` — delegate to configurable SentenceSegment backend
    - ``clean_ocr`` — remove U+FFFD, C0 controls, collapse whitespace
    - ``compare`` — normalized Levenshtein similarity via ``difflib.SequenceMatcher``
    - ``extract_keywords`` — non-stopword tokens after normalization

    Uses only stdlib + ``difflib`` for all methods except ``tokenize_sentences``,
    which lazy-loads a configurable sentence segmentation backend.

    Parameters
    ----------
    config : dict or None
        Configuration dict. Recognized keys:

        - ``sentence_tokenizer.backend``: one of ``"nltk_punkt"``, ``"scispacy"``,
          ``"wtpsplit"``, ``"spacy_sentencizer"``, ``"stanza"``
          (default: ``"nltk_punkt"``)
        - ``sentence_tokenizer.model``: model name passed to the backend
          (default: backend-specific)
    """

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._sentence_backend: Any = None

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def normalize(self, text: str) -> str:
        """Apply NFKC unicode normalization and collapse whitespace."""
        if not text:
            return ""
        normalized = unicodedata.normalize("NFKC", text)
        return _WHITESPACE_RE.sub(" ", normalized).strip()

    # ------------------------------------------------------------------
    # tokenize_words
    # ------------------------------------------------------------------

    def tokenize_words(self, text: str) -> list[str]:
        """Split on whitespace after normalization."""
        normalized = self.normalize(text)
        if not normalized:
            return []
        return normalized.split()

    # ------------------------------------------------------------------
    # tokenize_sentences
    # ------------------------------------------------------------------

    def tokenize_sentences(self, text: str) -> list[str]:
        """Delegate to configured SentenceSegment backend (lazy-loaded).

        Default backend: ``"nltk_punkt"`` (lightweight, already a project dependency).
        """
        if not text or not text.strip():
            return []

        if self._sentence_backend is None:
            self._sentence_backend = self._load_sentence_backend()

        return self._sentence_backend.tokenize_sentences(text)

    def _load_sentence_backend(self):
        """Lazy-load the configured sentence segmentation backend."""
        from text_processing.base import (
            NLTKPunktSentenceSegment,
            ScispaCySentenceSegment,
            SpacySentencizerSegment,
            StanzaSentenceSegment,
            WtpSplitSentenceSegment,
        )

        st_cfg = self._config.get("sentence_tokenizer", {})
        backend = st_cfg.get("backend", "nltk_punkt")

        _BACKEND_MAP = {
            "nltk_punkt": NLTKPunktSentenceSegment,
            "scispacy": ScispaCySentenceSegment,
            "wtpsplit": WtpSplitSentenceSegment,
            "spacy_sentencizer": SpacySentencizerSegment,
            "stanza": StanzaSentenceSegment,
        }

        cls = _BACKEND_MAP.get(backend)
        if cls is None:
            raise ValueError(
                f"Unknown sentence_tokenizer backend: {backend!r}. "
                f"Valid options: {sorted(_BACKEND_MAP.keys())}"
            )

        return cls(config=st_cfg)

    # ------------------------------------------------------------------
    # clean_ocr
    # ------------------------------------------------------------------

    def clean_ocr(self, text: str) -> str:
        """Remove OCR noise: U+FFFD replacement chars, C0 control chars, then collapse whitespace."""
        if not text:
            return ""
        # Remove U+FFFD (replacement character)
        cleaned = text.replace("\ufffd", "")
        # Remove C0 control characters (keep tab, LF, CR)
        cleaned = _CONTROL_CHARS_RE.sub("", cleaned)
        # Collapse whitespace
        return _WHITESPACE_RE.sub(" ", cleaned).strip()

    # ------------------------------------------------------------------
    # compare
    # ------------------------------------------------------------------

    def compare(self, a: str, b: str) -> float:
        """Return normalized similarity ratio in [0.0, 1.0] between *a* and *b*.

        Uses ``difflib.SequenceMatcher`` on NFKC-normalized, whitespace-collapsed
        versions of both strings. Returns 1.0 when both are empty.
        """
        norm_a = self.normalize(a)
        norm_b = self.normalize(b)

        # Both empty → identical
        if not norm_a and not norm_b:
            return 1.0

        # One empty, one not → completely dissimilar
        if not norm_a or not norm_b:
            return 0.0

        return SequenceMatcher(None, norm_a, norm_b).ratio()

    # ------------------------------------------------------------------
    # extract_keywords
    # ------------------------------------------------------------------

    def extract_keywords(self, text: str) -> list[str]:
        """Return non-stopword tokens after normalization."""
        tokens = self.tokenize_words(text)
        return [t for t in tokens if t.lower() not in _STOPWORDS]
