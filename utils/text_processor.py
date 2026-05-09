"""Text transformation hub for EviTrace.

Provides the :class:`TextProcessor` class, which offers configurable
normalization, comparison, OCR cleaning, word tokenization, and keyword
extraction.  All backends are resolved at construction time; optional NLP
backends (spaCy, NLTK) are lazy-imported inside method bodies.

Design reference: .kiro/specs/architecture-migration/design.md §TextProcessor
Requirements: 5.2, 5.3, 5.4, 5.5
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_NORMALIZERS: tuple[str, ...] = ("nfc", "nfkc")
_VALID_WORD_TOKENIZERS: tuple[str, ...] = ("simple", "spacy", "nltk")

# C0 control characters to strip (per design: \x00–\x08, \x0b, \x0c, \x0e–\x1f).
# Note: \x09 (tab), \x0a (LF), \x0d (CR) are intentionally preserved.
_C0_STRIP_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f�]"
)

# Minimal English stopword set for extract_keywords() — no NLP package required.
_ENGLISH_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "in", "on", "at", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "and", "or", "but", "for", "with", "by", "from",
    "as", "that", "this", "it", "its",
})


# ---------------------------------------------------------------------------
# TextProcessor
# ---------------------------------------------------------------------------

class TextProcessor:
    """Config-driven text transformation hub.

    Args:
        config: Optional configuration dict.  Recognised keys (all optional)::

            {
                "normalizer": {"backend": "nfkc"},        # "nfc" | "nfkc"
                "word_tokenizer": {"backend": "simple"},  # "simple" | "spacy" | "nltk"
                "sentence_tokenizer": {"backend": "..."},  # resolved in task 2.2
            }

        If a key is absent the documented default is used.  Unknown backend
        names raise :class:`ValueError` immediately at construction time.
    """

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}

        # ---- normalizer ---------------------------------------------------
        norm_cfg = cfg.get("normalizer", {})
        norm_backend = norm_cfg.get("backend", "nfkc")
        if norm_backend not in _VALID_NORMALIZERS:
            raise ValueError(
                f"Unknown normalizer backend {norm_backend!r}. "
                f"Valid options: {sorted(_VALID_NORMALIZERS)}"
            )
        self._norm_backend: str = norm_backend

        # ---- word tokenizer -----------------------------------------------
        wt_cfg = cfg.get("word_tokenizer", {})
        wt_backend = wt_cfg.get("backend", "simple")
        if wt_backend not in _VALID_WORD_TOKENIZERS:
            raise ValueError(
                f"Unknown word_tokenizer backend {wt_backend!r}. "
                f"Valid options: {sorted(_VALID_WORD_TOKENIZERS)}"
            )
        self._wt_backend: str = wt_backend
        self._wt_cfg: dict = wt_cfg

        # ---- sentence segmenter (wired in task 2.2) -----------------------
        # Kept as None by default; task 2.2 will assign a SentenceSegment
        # instance here when constructing via the loader.
        self._segmenter = None  # type: ignore[assignment]

        # Store the full config for sub-class / future use.
        self._config: dict = cfg

    # -----------------------------------------------------------------------
    # normalize
    # -----------------------------------------------------------------------

    def normalize(self, text: str) -> str:
        """Return a normalized copy of *text*.

        Steps applied (in order):
        1. Unicode normalization — NFC or NFKC depending on config.
           NFKC expands compatibility characters including ligatures
           (U+FB01 ﬁ → fi, U+FB02 ﬂ → fl).
        2. Collapse all whitespace sequences (including leading/trailing) to a
           single ASCII space; strip surrounding whitespace.

        The operation is idempotent: ``normalize(normalize(t)) == normalize(t)``.
        """
        if not text:
            return text

        # Step 1 — Unicode normalization
        normalized = unicodedata.normalize(self._norm_backend.upper(), text)

        # Step 2 — collapse internal whitespace, strip edges
        normalized = re.sub(r"\s+", " ", normalized).strip()

        return normalized

    # -----------------------------------------------------------------------
    # compare
    # -----------------------------------------------------------------------

    def compare(self, a: str, b: str) -> float:
        """Return similarity ratio in [0.0, 1.0] after normalizing both inputs.

        Uses :class:`difflib.SequenceMatcher` ratio, which equals
        ``2 * M / T`` where *M* is the number of matching characters and *T*
        is the total number of characters in both sequences.  Identical strings
        → 1.0; completely different strings → close to 0.0.
        """
        na = self.normalize(a)
        nb = self.normalize(b)
        return difflib.SequenceMatcher(None, na, nb).ratio()

    # -----------------------------------------------------------------------
    # clean_ocr
    # -----------------------------------------------------------------------

    def clean_ocr(self, text: str) -> str:
        """Remove OCR noise characters from *text*.

        Removes:
        - U+FFFD REPLACEMENT CHARACTER (``�`` / ``\N{REPLACEMENT CHARACTER}``)
        - C0 control characters ``\\x00``–``\\x08``, ``\\x0b``, ``\\x0c``,
          ``\\x0e``–``\\x1f``

        Preserved (intentionally):
        - ``\\t`` (U+0009), ``\\n`` (U+000A), ``\\r`` (U+000D)
        """
        if not text:
            return text
        return _C0_STRIP_RE.sub("", text)

    # -----------------------------------------------------------------------
    # tokenize_words
    # -----------------------------------------------------------------------

    def tokenize_words(self, text: str) -> list[str]:
        """Return a list of word tokens, normalizing *text* first.

        Backend selection:
        - ``"simple"`` — split on whitespace after normalization (no package required).
        - ``"spacy"`` — delegates to spaCy tokenizer (lazy import).
        - ``"nltk"``  — delegates to NLTK word_tokenize (lazy import).
        """
        normalized = self.normalize(text)
        if not normalized:
            return []

        if self._wt_backend == "simple":
            return normalized.split()

        if self._wt_backend == "spacy":
            return self._tokenize_words_spacy(normalized)

        if self._wt_backend == "nltk":
            return self._tokenize_words_nltk(normalized)

        # Should be unreachable — caught at __init__ time.
        raise ValueError(f"Unknown word_tokenizer backend: {self._wt_backend!r}")

    def _tokenize_words_spacy(self, text: str) -> list[str]:
        try:
            import spacy  # noqa: PLC0415 (lazy import by design)
        except ImportError as exc:
            raise ImportError(
                "spaCy is required for the 'spacy' word tokenizer backend. "
                "Install it with: pip install spacy"
            ) from exc

        model_name = self._wt_cfg.get("model", "en_core_web_sm")
        try:
            nlp = spacy.load(model_name)
        except OSError as exc:
            raise ImportError(
                f"spaCy model {model_name!r} not found. "
                f"Install it with: python -m spacy download {model_name}"
            ) from exc

        doc = nlp(text)
        return [token.text for token in doc if not token.is_space]

    def _tokenize_words_nltk(self, text: str) -> list[str]:
        try:
            from nltk import word_tokenize  # noqa: PLC0415 (lazy import by design)
        except ImportError as exc:
            raise ImportError(
                "NLTK is required for the 'nltk' word tokenizer backend. "
                "Install it with: pip install nltk"
            ) from exc

        try:
            return word_tokenize(text)
        except LookupError as exc:
            raise ImportError(
                "NLTK punkt tokenizer data not found. "
                "Download it with: python -c \"import nltk; nltk.download('punkt')\""
            ) from exc

    # -----------------------------------------------------------------------
    # extract_keywords
    # -----------------------------------------------------------------------

    def extract_keywords(self, text: str) -> list[str]:
        """Return non-stopword tokens from *text*.

        Processing:
        1. Lowercase the text.
        2. Split on whitespace.
        3. Filter out tokens that appear in the English stopword list.

        No NLP package is required; the stopword list is hardcoded.
        """
        if not text:
            return []
        tokens = text.lower().split()
        return [tok for tok in tokens if tok not in _ENGLISH_STOPWORDS]

    # -----------------------------------------------------------------------
    # tokenize_sentences  (stub — wired in task 2.2 via SentenceSegment)
    # -----------------------------------------------------------------------

    def tokenize_sentences(self, text: str) -> list[str]:
        """Delegate to the configured sentence segmenter.

        Raises :class:`NotImplementedError` when no segmenter has been wired
        (i.e., ``_segmenter is None``).  Task 2.2 assigns a
        :class:`SentenceSegment` instance at construction time via the loader.
        """
        if self._segmenter is None:
            raise NotImplementedError(
                "No sentence segmenter configured. "
                "Set text_processor.sentence_tokenizer.backend in config, "
                "or use a SentenceSegment subclass directly."
            )
        return self._segmenter.tokenize_sentences(text)
