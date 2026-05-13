"""Tokenizer subclasses for the text_processing package.

Provides :class:`SimpleWordTokenizer` which composes a
:class:`~text_processing.normalizers.UnicodeNormalizer` and splits on whitespace.
"""

from __future__ import annotations

from text_processing.base import TextProcessor
from text_processing.normalizers import UnicodeNormalizer


class SimpleWordTokenizer(TextProcessor):
    """Unicode-normalize then split on whitespace.

    Composes a :class:`UnicodeNormalizer` (NFKC by default) and splits
    on whitespace to produce word tokens.

    ``tokenize_words("")`` returns ``[]``.
    """

    def __init__(self, form: str = "NFKC") -> None:
        self._normalizer = UnicodeNormalizer(form=form)

    def normalize(self, text: str) -> str:
        return self._normalizer.normalize(text)

    def tokenize_words(self, text: str) -> list[str]:
        normalized = self._normalizer.normalize(text)
        if not normalized:
            return []
        return normalized.split()

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError(
            "SimpleWordTokenizer does not implement tokenize_sentences()."
        )

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError(
            "SimpleWordTokenizer does not implement clean_ocr()."
        )

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError(
            "SimpleWordTokenizer does not implement compare()."
        )

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError(
            "SimpleWordTokenizer does not implement extract_keywords()."
        )
