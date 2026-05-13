"""
tests/text_processing/test_tokenizers.py
========================================
SimpleWordTokenizer tests.
"""

import pytest

from text_processing.tokenizers import SimpleWordTokenizer


class TestSimpleWordTokenizer:
    @pytest.fixture
    def tok(self):
        return SimpleWordTokenizer()

    def test_splits_on_whitespace(self, tok):
        result = tok.tokenize_words("hello world foo")
        assert result == ["hello", "world", "foo"]

    def test_empty_string_returns_empty_list(self, tok):
        assert tok.tokenize_words("") == []

    def test_normalizes_before_split(self, tok):
        """NFKC normalization applied: fi-ligature expanded."""
        result = tok.tokenize_words("\ufb01le \ufb02oor")
        assert result == ["file", "floor"]

    def test_collapses_whitespace(self, tok):
        result = tok.tokenize_words("  hello   world  ")
        assert "" not in result
        assert "hello" in result and "world" in result

    def test_returns_list(self, tok):
        assert isinstance(tok.tokenize_words("test"), list)

    def test_normalize_delegates_to_unicode_normalizer(self, tok):
        result = tok.normalize("\ufb01le")
        assert result == "file"

    def test_unrelated_methods_raise(self, tok):
        with pytest.raises(NotImplementedError):
            tok.tokenize_sentences("x")
        with pytest.raises(NotImplementedError):
            tok.clean_ocr("x")
        with pytest.raises(NotImplementedError):
            tok.compare("a", "b")
        with pytest.raises(NotImplementedError):
            tok.extract_keywords("x")
