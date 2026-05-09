"""Text transformation hub for EviTrace.

Provides the :class:`TextProcessor` class, which offers configurable
normalization, comparison, OCR cleaning, word tokenization, and keyword
extraction.  All backends are resolved at construction time; optional NLP
backends (spaCy, NLTK) are lazy-imported inside method bodies.

Also provides the :class:`SentenceSegment` abstract base class and five
built-in concrete backends for sentence boundary detection (task 2.2).

Design reference: .kiro/specs/architecture-migration/design.md §TextProcessor
Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""

from __future__ import annotations

import difflib
import importlib
import re
import unicodedata
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_NORMALIZERS: tuple[str, ...] = ("nfc", "nfkc")
_VALID_WORD_TOKENIZERS: tuple[str, ...] = ("simple", "spacy", "nltk")
_VALID_SENTENCE_TOKENIZERS: tuple[str, ...] = (
    "scispacy",
    "wtpsplit",
    "nltk_punkt",
    "spacy_sentencizer",
    "stanza",
)

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

        # ---- sentence segmenter -------------------------------------------
        # Resolved from config["sentence_tokenizer"]["backend"].
        # SentenceSegment subclasses call super().__init__() which skips this
        # block so they don't re-enter the wiring logic.
        st_cfg = cfg.get("sentence_tokenizer", {})
        st_backend = st_cfg.get("backend", "nltk_punkt")  # TODO: Planned to be fixed in the future (spacy/numpy incompatibility)
        self._segmenter = self._resolve_sentence_segmenter(st_backend)

        # Store the full config for sub-class / future use.
        self._config: dict = cfg

    # ------------------------------------------------------------------
    # Sentence segmenter resolver (class-level, called from __init__)
    # ------------------------------------------------------------------

    def _resolve_sentence_segmenter(self, backend: str):  # type: ignore[return]
        """Return a SentenceSegment instance for *backend*.

        If *backend* is a dotted class path (contains ``"."``), it is loaded
        via :func:`importlib.import_module`.  Otherwise it must be one of the
        built-in keys in :data:`_VALID_SENTENCE_TOKENIZERS`.

        SentenceSegment subclasses return ``None`` here (they ARE the
        segmenter), so the caller should set ``_segmenter`` to ``self`` or
        skip entirely.  By convention, ``SentenceSegment.__init__`` overrides
        this method to return ``None``.
        """
        if "." in backend:
            # Fully qualified class path — load via importlib (req 5.5)
            module_path, class_name = backend.rsplit(".", 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            return cls()

        _BACKEND_MAP = {
            "scispacy": lambda: ScispaCySentenceSegment(),
            "wtpsplit": lambda: WtpSplitSentenceSegment(),
            "nltk_punkt": lambda: NLTKPunktSentenceSegment(),
            "spacy_sentencizer": lambda: SpacySentencizerSegment(),
            "stanza": lambda: StanzaSentenceSegment(),
        }
        if backend in _BACKEND_MAP:
            return _BACKEND_MAP[backend]()

        raise ValueError(
            f"Unknown sentence_tokenizer backend {backend!r}. "
            f"Valid built-in options: {sorted(_VALID_SENTENCE_TOKENIZERS)}"
        )

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
        (i.e., ``_segmenter is None``).  This should not happen for plain
        ``TextProcessor`` instances after task 2.2; ``SentenceSegment``
        subclasses override this method directly.
        """
        if self._segmenter is None:
            raise NotImplementedError(
                "No sentence segmenter configured. "
                "Set text_processor.sentence_tokenizer.backend in config, "
                "or use a SentenceSegment subclass directly."
            )
        return self._segmenter.tokenize_sentences(text)


# ---------------------------------------------------------------------------
# SentenceSegment hierarchy  (task 2.2 — requirements 5.1, 5.5)
# ---------------------------------------------------------------------------

class SentenceSegment(TextProcessor):
    """Abstract base class for sentence segmentation backends.

    Inherits the full :class:`TextProcessor` interface so that any concrete
    ``SentenceSegment`` can also serve as the top-level ``TextProcessor``
    instance (design §TextProcessor — loader pattern).

    Subclasses **must** override :meth:`tokenize_sentences`.  All other
    ``TextProcessor`` methods are inherited and fully functional.

    Lazy-loading pattern: each concrete subclass stores the loaded model in
    ``self._model`` (``None`` until first call).  The model is loaded exactly
    once per instance.
    """

    def _resolve_sentence_segmenter(self, backend: str):
        """Override: SentenceSegment IS the segmenter — no child segmenter needed."""
        return None  # _segmenter will be set to self after super().__init__

    def __init__(self, config: dict | None = None) -> None:
        # Call TextProcessor.__init__. Because _resolve_sentence_segmenter is
        # overridden above, it returns None — no recursive instantiation.
        super().__init__(config=config)
        # Point _segmenter to self so that the TextProcessor.tokenize_sentences
        # delegation path also works correctly.
        self._segmenter = self  # type: ignore[assignment]
        self._model = None  # lazy-loaded on first tokenize_sentences call

    def tokenize_sentences(self, text: str) -> list[str]:
        """Must be overridden by concrete backends."""
        raise NotImplementedError(
            f"{type(self).__name__} does not implement tokenize_sentences(). "
            "Use a concrete SentenceSegment subclass."
        )


class ScispaCySentenceSegment(SentenceSegment):
    """Sentence segmentation via scispaCy (``en_core_sci_lg``).

    Lazy-loads the model on first call; raises :class:`ImportError` with an
    exact install command when scispaCy or spaCy is not installed.
    """

    def tokenize_sentences(self, text: str) -> list[str]:
        if self._model is None:
            try:
                import scispacy  # noqa: F401
                import spacy
                self._model = spacy.load("en_core_sci_lg")
            except ImportError:
                raise ImportError(
                    "scispaCy is not installed. Install it with:\n"
                    "  pip install scispacy\n"
                    "  python -m spacy download en_core_sci_lg"
                )
        return [sent.text for sent in self._model(text).sents]


class WtpSplitSentenceSegment(SentenceSegment):
    """Sentence segmentation via wtpsplit (``WtP`` model).

    Lazy-loads on first call; raises :class:`ImportError` with install hint
    when the ``wtpsplit`` package is absent.
    """

    def tokenize_sentences(self, text: str) -> list[str]:
        if self._model is None:
            try:
                import wtpsplit
                self._model = wtpsplit.WtP("wtp-bert-mini")
            except ImportError:
                raise ImportError(
                    "wtpsplit is not installed. Install it with:\n"
                    "  pip install wtpsplit"
                )
        return list(self._model.split(text))


class NLTKPunktSentenceSegment(SentenceSegment):
    """Sentence segmentation via NLTK Punkt tokenizer.

    Lazy-loads on first call; raises :class:`ImportError` with install hint
    when the ``nltk`` package or Punkt data is absent.
    """

    def tokenize_sentences(self, text: str) -> list[str]:
        if self._model is None:
            try:
                import nltk
                self._model = nltk
            except ImportError:
                raise ImportError(
                    "NLTK is not installed. Install it with:\n"
                    "  pip install nltk\n"
                    "  python -c \"import nltk; nltk.download('punkt')\""
                )
        try:
            return self._model.sent_tokenize(text)
        except LookupError:
            raise ImportError(
                "NLTK Punkt data not found. Download it with:\n"
                "  python -c \"import nltk; nltk.download('punkt')\""
            )


class SpacySentencizerSegment(SentenceSegment):
    """Sentence segmentation via spaCy ``Sentencizer`` component.

    Lazy-loads on first call; raises :class:`ImportError` with install hint
    when ``spacy`` is absent.
    """

    def tokenize_sentences(self, text: str) -> list[str]:
        if self._model is None:
            try:
                import spacy
                nlp = spacy.blank("en")
                nlp.add_pipe("sentencizer")
                self._model = nlp
            except ImportError as exc:
                raise ImportError(
                    "spaCy is not installed. Install it with:\n"
                    "  pip install spacy\n"
                    f"Underlying error: {exc}"
                ) from exc
        doc = self._model(text)
        return [sent.text for sent in doc.sents]


class StanzaSentenceSegment(SentenceSegment):
    """Sentence segmentation via Stanford Stanza.

    Lazy-loads on first call; raises :class:`ImportError` with install hint
    when the ``stanza`` package is absent.
    """

    def tokenize_sentences(self, text: str) -> list[str]:
        if self._model is None:
            try:
                import stanza
                pipeline = stanza.Pipeline(lang="en", processors="tokenize")
                self._model = pipeline
            except ImportError:
                raise ImportError(
                    "Stanza is not installed. Install it with:\n"
                    "  pip install stanza"
                )
        doc = self._model(text)
        return [sent.text for sent in doc.sentences]
