"""Normalizer subclasses for the text_processing package.

Each normalizer implements only :meth:`normalize` from :class:`TextProcessor`;
all other abstract methods raise :class:`NotImplementedError`.

All normalizers return empty string for empty input and are idempotent:
``normalizer.normalize(normalizer.normalize(text)) == normalizer.normalize(text)``
"""

from __future__ import annotations

import re
import unicodedata

from text_processing.base import TextProcessor


# ---------------------------------------------------------------------------
# WhitespaceNormalizer
# ---------------------------------------------------------------------------

class WhitespaceNormalizer(TextProcessor):
    """Collapse whitespace + lowercase.

    Operations:
    1. Collapse all whitespace runs to a single space.
    2. Lowercase.
    3. Strip leading/trailing whitespace.

    Punctuation is preserved.  Idempotent.
    """

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        return re.sub(r"\s+", " ", text.lower()).strip()

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError(
            "WhitespaceNormalizer does not implement tokenize_words()."
        )

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError(
            "WhitespaceNormalizer does not implement tokenize_sentences()."
        )

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError(
            "WhitespaceNormalizer does not implement clean_ocr()."
        )

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError(
            "WhitespaceNormalizer does not implement compare()."
        )

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError(
            "WhitespaceNormalizer does not implement extract_keywords()."
        )


# ---------------------------------------------------------------------------
# AggressiveNormalizer
# ---------------------------------------------------------------------------

class AggressiveNormalizer(TextProcessor):
    """Whitespace + strip non-word characters.

    Operations:
    1. Collapse whitespace, lowercase (same as WhitespaceNormalizer).
    2. Strip every character that is not a word character (``\\w``) or whitespace.
    3. Re-collapse whitespace and strip (to fix gaps left by punctuation removal).

    Idempotent.
    """

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text.lower()).strip()
        text = re.sub(r"[^\w\s]", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError(
            "AggressiveNormalizer does not implement tokenize_words()."
        )

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError(
            "AggressiveNormalizer does not implement tokenize_sentences()."
        )

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError(
            "AggressiveNormalizer does not implement clean_ocr()."
        )

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError(
            "AggressiveNormalizer does not implement compare()."
        )

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError(
            "AggressiveNormalizer does not implement extract_keywords()."
        )


# ---------------------------------------------------------------------------
# LineHealingNormalizer
# ---------------------------------------------------------------------------

class LineHealingNormalizer(TextProcessor):
    """Heal mid-sentence line breaks, collapse newlines/spaces.

    Operations (in order):
    1. A single newline NOT followed by an uppercase letter or bullet
       character (-, *, bullet, ·) is replaced with a space.
    2. Collapse runs of two or more newlines into a single newline.
    3. Collapse runs of two or more spaces into a single space.
    4. Strip leading/trailing whitespace.

    Idempotent.
    """

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        # Step 1 — heal mid-sentence line breaks
        text = re.sub(r"\n(?![A-Z\-\*\u2022\u00b7])", " ", text)
        # Step 2 — collapse multiple newlines
        text = re.sub(r"\n{2,}", "\n", text)
        # Step 3 — collapse multiple spaces
        text = re.sub(r" {2,}", " ", text)
        # Step 4 — strip
        return text.strip()

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError(
            "LineHealingNormalizer does not implement tokenize_words()."
        )

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError(
            "LineHealingNormalizer does not implement tokenize_sentences()."
        )

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError(
            "LineHealingNormalizer does not implement clean_ocr()."
        )

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError(
            "LineHealingNormalizer does not implement compare()."
        )

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError(
            "LineHealingNormalizer does not implement extract_keywords()."
        )


# ---------------------------------------------------------------------------
# UnicodeNormalizer
# ---------------------------------------------------------------------------

class UnicodeNormalizer(TextProcessor):
    """Unicode NFC/NFKC normalization + whitespace collapse.

    Parameters
    ----------
    form : str
        Unicode normalization form: ``"NFC"`` or ``"NFKC"`` (default).

    Operations:
    1. Apply the configured Unicode normalization form.
    2. Collapse all whitespace runs to a single space.
    3. Strip leading/trailing whitespace.

    Idempotent.
    """

    def __init__(self, form: str = "NFKC") -> None:
        self._form = form.upper()
        if self._form not in ("NFC", "NFKC"):
            raise ValueError(
                f"Unknown Unicode normalization form {form!r}. "
                "Valid options: 'NFC', 'NFKC'"
            )

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        normalized = unicodedata.normalize(self._form, text)
        return re.sub(r"\s+", " ", normalized).strip()

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError(
            "UnicodeNormalizer does not implement tokenize_words()."
        )

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError(
            "UnicodeNormalizer does not implement tokenize_sentences()."
        )

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError(
            "UnicodeNormalizer does not implement clean_ocr()."
        )

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError(
            "UnicodeNormalizer does not implement compare()."
        )

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError(
            "UnicodeNormalizer does not implement extract_keywords()."
        )


# ---------------------------------------------------------------------------
# OcrCleaner
# ---------------------------------------------------------------------------

# C0 control characters to strip: \x00–\x08, \x0b, \x0c, \x0e–\x1f, U+FFFD.
# Preserved: \x09 (tab), \x0a (LF), \x0d (CR).
_C0_STRIP_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")


class OcrCleaner(TextProcessor):
    """Strip C0 controls and U+FFFD; preserve tab/LF/CR.

    Removes:
    - C0 control characters ``\\x00``–``\\x08``, ``\\x0b``, ``\\x0c``,
      ``\\x0e``–``\\x1f``
    - U+FFFD REPLACEMENT CHARACTER

    Preserved (intentionally):
    - ``\\t`` (U+0009), ``\\n`` (U+000A), ``\\r`` (U+000D)
    """

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        return _C0_STRIP_RE.sub("", text)

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError(
            "OcrCleaner does not implement tokenize_words()."
        )

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError(
            "OcrCleaner does not implement tokenize_sentences()."
        )

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError(
            "OcrCleaner does not implement clean_ocr()."
        )

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError(
            "OcrCleaner does not implement compare()."
        )

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError(
            "OcrCleaner does not implement extract_keywords()."
        )


# ---------------------------------------------------------------------------
# OULNormalizer  (OcrCleaner → UnicodeNormalizer → LineHealingNormalizer)
# ---------------------------------------------------------------------------

class OULNormalizer(TextProcessor):
    """Composite normalizer for scientific PDF extraction values.

    Chains three normalizers in order:

    1. **OcrCleaner** — strip C0 control characters (``\\x00``–``\\x1f`` minus
       tab/LF/CR) and U+FFFD REPLACEMENT CHARACTER.
    2. **UnicodeNormalizer(NFKC)** — decompose ligatures (fi, fl, …), expand
       compatibility characters, normalize unicode.
    3. **LineHealingNormalizer** — heal mid-sentence PDF line breaks, collapse
       multiple blank lines and runs of spaces.

    Preserves case, scientific punctuation (``.,%-/()=±``), and numerics.
    Idempotent.
    """

    def __init__(self) -> None:
        self._ocr = OcrCleaner()
        self._unicode = UnicodeNormalizer(form="NFKC")
        self._line = LineHealingNormalizer()

    def normalize(self, text: str) -> str:
        if not text:
            return ""
        text = self._ocr.normalize(text)
        text = self._unicode.normalize(text)
        text = self._line.normalize(text)
        return text

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError(
            "OULNormalizer does not implement tokenize_words()."
        )

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError(
            "OULNormalizer does not implement tokenize_sentences()."
        )

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError(
            "OULNormalizer does not implement clean_ocr()."
        )

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError(
            "OULNormalizer does not implement compare()."
        )

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError(
            "OULNormalizer does not implement extract_keywords()."
        )
