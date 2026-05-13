"""Abstract base classes and concrete sentence-segmentation backends.

Provides :class:`TextProcessor` (ABC with six abstract methods) and
:class:`SentenceSegment` (TextProcessor subclass with lazy model loading).
Five concrete backends are included:

- :class:`ScispaCySentenceSegment`
- :class:`WtpSplitSentenceSegment`
- :class:`NLTKPunktSentenceSegment`
- :class:`SpacySentencizerSegment`
- :class:`StanzaSentenceSegment`

All heavy optional dependencies are imported lazily inside method bodies.
"""

from __future__ import annotations

import abc
from typing import Any


# ---------------------------------------------------------------------------
# TextProcessor ABC
# ---------------------------------------------------------------------------

class TextProcessor(abc.ABC):
    """Abstract base class defining the text processing interface."""

    @abc.abstractmethod
    def normalize(self, text: str) -> str:
        """Return a normalized copy of *text*."""
        ...

    @abc.abstractmethod
    def tokenize_words(self, text: str) -> list[str]:
        """Return a list of word tokens from *text*."""
        ...

    @abc.abstractmethod
    def tokenize_sentences(self, text: str) -> list[str]:
        """Return a list of sentence strings from *text*."""
        ...

    @abc.abstractmethod
    def clean_ocr(self, text: str) -> str:
        """Remove OCR noise characters from *text*."""
        ...

    @abc.abstractmethod
    def compare(self, a: str, b: str) -> float:
        """Return similarity ratio in [0.0, 1.0] between *a* and *b*."""
        ...

    @abc.abstractmethod
    def extract_keywords(self, text: str) -> list[str]:
        """Return non-stopword tokens from *text*."""
        ...


# ---------------------------------------------------------------------------
# SentenceSegment ABC
# ---------------------------------------------------------------------------

class SentenceSegment(TextProcessor, abc.ABC):
    """Abstract base class for sentence segmentation backends.

    Inherits :class:`TextProcessor` so any concrete segment backend can
    serve as a standalone text processor.  Subclasses **must** override
    :meth:`tokenize_sentences`.  All other abstract methods raise
    :class:`NotImplementedError` by default (they are unrelated concerns
    for a sentence segmenter).

    Lazy-loading pattern: ``self._model`` is ``None`` until first
    :meth:`tokenize_sentences` call.
    """

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}
        self._model: Any = None

    # -- Unrelated abstract methods: raise NotImplementedError by default --

    def normalize(self, text: str) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement normalize(). "
            "Use a dedicated normalizer."
        )

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement tokenize_words(). "
            "Use a dedicated tokenizer."
        )

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement clean_ocr(). "
            "Use OcrCleaner."
        )

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement compare()."
        )

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement extract_keywords()."
        )

    @abc.abstractmethod
    def tokenize_sentences(self, text: str) -> list[str]:
        """Must be overridden by concrete backends."""
        ...


# ---------------------------------------------------------------------------
# Concrete sentence segmentation backends
# ---------------------------------------------------------------------------

class ScispaCySentenceSegment(SentenceSegment):
    """Sentence segmentation via scispaCy.

    The default model (``en_core_sci_sm``) is loaded eagerly at construction.
    Non-default models are lazy-loaded on first :meth:`tokenize_sentences` call.
    """

    DEFAULT_MODEL = "en_core_sci_sm"

    _SCISPACY_MODEL_URLS: dict[str, str] = {
        "en_core_sci_sm": (
            "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy"
            "/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz"
        ),
        "en_core_sci_md": (
            "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy"
            "/releases/v0.5.4/en_core_sci_md-0.5.4.tar.gz"
        ),
        "en_core_sci_lg": (
            "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy"
            "/releases/v0.5.4/en_core_sci_lg-0.5.4.tar.gz"
        ),
        "en_core_sci_scibert": (
            "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy"
            "/releases/v0.5.4/en_core_sci_scibert-0.5.4.tar.gz"
        ),
    }

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config=config)
        self._model_name = (config or {}).get("model", self.DEFAULT_MODEL)
        if self._model_name == self.DEFAULT_MODEL:
            self._model = self._load_model()

    def _load_model(self):
        try:
            import scispacy  # noqa: F401
            import spacy
            return spacy.load(self._model_name)
        except ImportError as exc:
            raise ImportError(
                f"scispaCy model {self._model_name!r} is not installed. Install it with:\n"
                "  pip install scispacy\n"
                f"  python -m spacy download {self._model_name}"
            ) from exc
        except OSError:
            import subprocess
            import sys
            import spacy

            url = self._SCISPACY_MODEL_URLS.get(self._model_name)
            if url is None:
                raise ImportError(
                    f"scispaCy model {self._model_name!r} is not installed and no "
                    "automatic install URL is known for it.\n"
                    "Install it manually with:\n"
                    "  pip install scispacy\n"
                    f"  python -m spacy download {self._model_name}"
                )

            print(
                f"[EviTrace] scispaCy model {self._model_name!r} not found — "
                f"installing automatically from:\n  {url}",
                flush=True,
            )
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", url],
                check=False,
            )
            if result.returncode != 0:
                raise ImportError(
                    f"Automatic installation of {self._model_name!r} failed "
                    f"(pip exit code {result.returncode}).\n"
                    "Install it manually with:\n"
                    f"  pip install {url}"
                )

            import importlib
            importlib.reload(spacy)
            return spacy.load(self._model_name)

    def tokenize_sentences(self, text: str) -> list[str]:
        if self._model is None:
            self._model = self._load_model()
        return [sent.text for sent in self._model(text).sents]


class WtpSplitSentenceSegment(SentenceSegment):
    """Sentence segmentation via wtpsplit (``WtP`` model).

    Loads eagerly at construction; raises :class:`ImportError` with install
    hint when the ``wtpsplit`` package is absent.
    """

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config=config)
        self._model = self._load_model()

    def _load_model(self):
        try:
            import wtpsplit
            return wtpsplit.WtP("wtp-bert-mini")
        except ImportError as exc:
            raise ImportError(
                "wtpsplit is not installed. Install it with:\n"
                "  pip install wtpsplit"
            ) from exc

    def tokenize_sentences(self, text: str) -> list[str]:
        return list(self._model.split(text))


class NLTKPunktSentenceSegment(SentenceSegment):
    """Sentence segmentation via NLTK Punkt tokenizer.

    Loads eagerly at construction; raises :class:`ImportError` with install
    hint when the ``nltk`` package or Punkt data is absent.
    """

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config=config)
        self._model = self._load_model()

    def _load_model(self):
        try:
            import nltk
            return nltk
        except ImportError as exc:
            raise ImportError(
                "NLTK is not installed. Install it with:\n"
                "  pip install nltk\n"
                "  python -c \"import nltk; nltk.download('punkt')\""
            ) from exc

    def tokenize_sentences(self, text: str) -> list[str]:
        try:
            return self._model.sent_tokenize(text)
        except LookupError as exc:
            raise ImportError(
                "NLTK Punkt data not found. Download it with:\n"
                "  python -c \"import nltk; nltk.download('punkt')\""
            ) from exc


class SpacySentencizerSegment(SentenceSegment):
    """Sentence segmentation via spaCy ``Sentencizer`` component.

    Loads eagerly at construction; raises :class:`ImportError` with install
    hint when ``spacy`` is absent.
    """

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config=config)
        self._model = self._load_model()

    def _load_model(self):
        try:
            import spacy
            nlp = spacy.blank("en")
            nlp.add_pipe("sentencizer")
            return nlp
        except ImportError as exc:
            raise ImportError(
                "spaCy is not installed. Install it with:\n"
                "  pip install spacy\n"
                f"Underlying error: {exc}"
            ) from exc

    def tokenize_sentences(self, text: str) -> list[str]:
        doc = self._model(text)
        return [sent.text for sent in doc.sents]


class StanzaSentenceSegment(SentenceSegment):
    """Sentence segmentation via Stanford Stanza.

    Loads eagerly at construction; raises :class:`ImportError` with install
    hint when the ``stanza`` package is absent.
    """

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config=config)
        self._model = self._load_model()

    def _load_model(self):
        try:
            import stanza
            return stanza.Pipeline(lang="en", processors="tokenize")
        except ImportError as exc:
            raise ImportError(
                "Stanza is not installed. Install it with:\n"
                "  pip install stanza"
            ) from exc

    def tokenize_sentences(self, text: str) -> list[str]:
        doc = self._model(text)
        return [sent.text for sent in doc.sentences]
