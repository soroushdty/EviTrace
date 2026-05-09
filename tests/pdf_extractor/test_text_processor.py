"""
test_text_processor.py
======================
TDD tests for :class:`utils.text_processor.TextProcessor`.

Covers requirements 5.2, 5.3, 5.4, 5.5 — word segmentation, normalization,
text comparison, OCR cleaning, keyword extraction, backend validation.

Run with::

    pytest tests/pdf_extractor/test_text_processor.py -v
"""

from __future__ import annotations

import unicodedata
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tp(config=None):
    """Construct a TextProcessor with optional config, defaulting to empty."""
    from utils.text_processor import TextProcessor
    return TextProcessor(config=config or {})


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

class TestInstantiation:
    def test_default_config_does_not_raise(self):
        """TextProcessor() with config={} must not raise any exception."""
        from utils.text_processor import TextProcessor
        tp = TextProcessor(config={})
        assert tp is not None

    def test_none_config_does_not_raise(self):
        """TextProcessor(config=None) must not raise."""
        from utils.text_processor import TextProcessor
        tp = TextProcessor(config=None)
        assert tp is not None

    def test_unknown_normalizer_raises_value_error(self):
        """Unknown normalizer backend raises ValueError listing valid options."""
        from utils.text_processor import TextProcessor
        with pytest.raises(ValueError) as exc_info:
            TextProcessor(config={"normalizer": {"backend": "bogus_normalizer"}})
        msg = str(exc_info.value)
        # Must list valid options
        assert "nfc" in msg.lower() or "nfkc" in msg.lower()

    def test_unknown_word_tokenizer_raises_value_error(self):
        """Unknown word_tokenizer backend raises ValueError listing valid options."""
        from utils.text_processor import TextProcessor
        with pytest.raises(ValueError) as exc_info:
            TextProcessor(config={"word_tokenizer": {"backend": "unknown_tok"}})
        msg = str(exc_info.value)
        # Must list valid options
        assert "simple" in msg.lower() or "spacy" in msg.lower() or "nltk" in msg.lower()


# ---------------------------------------------------------------------------
# normalize()
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_returns_string(self):
        tp = _make_tp()
        assert isinstance(tp.normalize("hello"), str)

    def test_empty_string(self):
        tp = _make_tp()
        assert tp.normalize("") == ""

    def test_whitespace_collapsed(self):
        tp = _make_tp()
        result = tp.normalize("  hello   world  ")
        assert "  " not in result
        assert result.strip() == result

    def test_idempotent_plain(self):
        tp = _make_tp()
        text = "  Hello   World  "
        once = tp.normalize(text)
        twice = tp.normalize(once)
        assert once == twice

    def test_idempotent_unicode(self):
        tp = _make_tp()
        text = "éàü café"  # éàü café
        once = tp.normalize(text)
        twice = tp.normalize(once)
        assert once == twice

    def test_nfkc_mode_ligature_fi(self):
        """NFKC mode expands ﬁ (U+FB01) to fi."""
        tp = _make_tp({"normalizer": {"backend": "nfkc"}})
        result = tp.normalize("ﬁle")  # ﬁle
        assert result == "file"

    def test_nfkc_mode_ligature_fl(self):
        """NFKC mode expands ﬂ (U+FB02) to fl."""
        tp = _make_tp({"normalizer": {"backend": "nfkc"}})
        result = tp.normalize("ﬂoor")  # ﬂoor
        assert result == "floor"

    def test_nfkc_mode_idempotent(self):
        """After NFKC normalization, applying again yields the same result."""
        tp = _make_tp({"normalizer": {"backend": "nfkc"}})
        text = "ﬁle ﬂoor café"
        once = tp.normalize(text)
        twice = tp.normalize(once)
        assert once == twice

    def test_nfc_mode_preserves_ligature(self):
        """NFC mode does NOT expand ﬁ (ligature is preserved under NFC)."""
        tp = _make_tp({"normalizer": {"backend": "nfc"}})
        result = tp.normalize("ﬁle")
        # Under NFC, ﬁ stays as ﬁ
        assert "ﬁ" in result

    def test_nfc_mode_idempotent(self):
        tp = _make_tp({"normalizer": {"backend": "nfc"}})
        text = "éà café"
        once = tp.normalize(text)
        twice = tp.normalize(once)
        assert once == twice

    def test_default_mode_is_nfkc(self):
        """Default normalizer (no config) should use NFKC and expand ﬁ."""
        tp = _make_tp({})
        result = tp.normalize("ﬁle")
        assert result == "file"


# ---------------------------------------------------------------------------
# compare()
# ---------------------------------------------------------------------------

class TestCompare:
    def test_identical_strings_return_one(self):
        tp = _make_tp()
        assert tp.compare("hello world", "hello world") == pytest.approx(1.0)

    def test_completely_different_strings_return_low(self):
        tp = _make_tp()
        score = tp.compare("aaaa", "zzzz")
        assert 0.0 <= score <= 1.0

    def test_empty_strings_return_one(self):
        """Two empty strings are identical."""
        tp = _make_tp()
        assert tp.compare("", "") == pytest.approx(1.0)

    def test_result_in_range(self):
        tp = _make_tp()
        score = tp.compare("hello", "world")
        assert 0.0 <= score <= 1.0

    def test_partial_match_between_zero_and_one(self):
        tp = _make_tp()
        score = tp.compare("hello world", "hello there")
        assert 0.0 < score < 1.0

    def test_compare_normalizes_inputs(self):
        """compare applies normalize() before computing similarity.
        'ﬁle' and 'file' should match perfectly after NFKC normalization."""
        tp = _make_tp({"normalizer": {"backend": "nfkc"}})
        score = tp.compare("ﬁle", "file")
        assert score == pytest.approx(1.0)

    def test_symmetry_approximately(self):
        """compare(a, b) and compare(b, a) should be equal (SequenceMatcher is symmetric)."""
        tp = _make_tp()
        a = "The quick brown fox"
        b = "A slow red dog"
        assert tp.compare(a, b) == pytest.approx(tp.compare(b, a), rel=1e-6)


# ---------------------------------------------------------------------------
# clean_ocr()
# ---------------------------------------------------------------------------

class TestCleanOcr:
    def test_removes_replacement_character(self):
        """U+FFFD (replacement char) must be removed."""
        tp = _make_tp()
        result = tp.clean_ocr("hello�world")
        assert "�" not in result
        assert result == "helloworld"

    def test_removes_null_byte(self):
        """\\x00 (C0 control) must be removed."""
        tp = _make_tp()
        result = tp.clean_ocr("a\x00b")
        assert "\x00" not in result
        assert result == "ab"

    def test_removes_c0_controls_range(self):
        """All C0 controls \\x00–\\x08 must be removed."""
        tp = _make_tp()
        for code in range(0x00, 0x09):  # \x00 through \x08
            text = f"a{chr(code)}b"
            result = tp.clean_ocr(text)
            assert chr(code) not in result, f"U+{code:04X} not removed"

    def test_removes_0x0b_and_0x0c(self):
        """\\x0b (VT) and \\x0c (FF) must be removed."""
        tp = _make_tp()
        for code in (0x0b, 0x0c):
            result = tp.clean_ocr(f"a{chr(code)}b")
            assert chr(code) not in result, f"U+{code:04X} not removed"

    def test_removes_0x0e_to_0x1f(self):
        """C0 controls \\x0e–\\x1f must be removed."""
        tp = _make_tp()
        for code in range(0x0e, 0x20):
            text = f"a{chr(code)}b"
            result = tp.clean_ocr(text)
            assert chr(code) not in result, f"U+{code:04X} not removed"

    def test_preserves_tab_lf_cr(self):
        """\\t (\\x09), \\n (\\x0a), \\r (\\x0d) are NOT in the removal set."""
        tp = _make_tp()
        text = "a\tb\nc\rd"
        result = tp.clean_ocr(text)
        assert "\t" in result
        assert "\n" in result
        assert "\r" in result

    def test_preserves_normal_text(self):
        """clean_ocr on clean text returns the text unchanged."""
        tp = _make_tp()
        text = "The quick brown fox."
        assert tp.clean_ocr(text) == text

    def test_empty_string(self):
        tp = _make_tp()
        assert tp.clean_ocr("") == ""

    def test_multiple_replacement_chars(self):
        """Multiple U+FFFD instances are all removed."""
        tp = _make_tp()
        result = tp.clean_ocr("���")
        assert result == ""

    def test_idempotent(self):
        """clean_ocr is idempotent."""
        tp = _make_tp()
        text = "hello�\x00world\x1f"
        once = tp.clean_ocr(text)
        twice = tp.clean_ocr(once)
        assert once == twice


# ---------------------------------------------------------------------------
# extract_keywords()
# ---------------------------------------------------------------------------

class TestExtractKeywords:
    def test_returns_list(self):
        tp = _make_tp()
        result = tp.extract_keywords("machine learning classification")
        assert isinstance(result, list)

    def test_filters_stopwords(self):
        """Common English stopwords should not appear in keywords."""
        tp = _make_tp()
        result = tp.extract_keywords("the cat is on the mat")
        stopwords_in_result = [w for w in result if w in {"the", "is", "on", "a", "an"}]
        assert stopwords_in_result == [], f"Stopwords found: {stopwords_in_result}"

    def test_returns_lowercase(self):
        """Keywords should be lowercase."""
        tp = _make_tp()
        result = tp.extract_keywords("Machine Learning Classification")
        for kw in result:
            assert kw == kw.lower(), f"Keyword not lowercase: {kw!r}"

    def test_empty_string_returns_empty(self):
        tp = _make_tp()
        assert tp.extract_keywords("") == []

    def test_all_stopwords_returns_empty(self):
        """A sentence made only of stopwords returns an empty list."""
        tp = _make_tp()
        result = tp.extract_keywords("the a an is are was were be")
        assert result == []

    def test_content_words_preserved(self):
        """Non-stopword tokens are preserved."""
        tp = _make_tp()
        result = tp.extract_keywords("neural network")
        assert "neural" in result
        assert "network" in result

    def test_mixed_sentence(self):
        """Stopwords are filtered; content words remain."""
        tp = _make_tp()
        result = tp.extract_keywords("the quick brown fox")
        assert "the" not in result
        assert "quick" in result
        assert "brown" in result
        assert "fox" in result

    def test_no_nlp_package_required(self):
        """extract_keywords works without spacy/nltk installed (uses hardcoded list)."""
        # Just verifying that it doesn't raise ImportError
        tp = _make_tp()
        try:
            tp.extract_keywords("some text about science")
        except ImportError:
            pytest.fail("extract_keywords raised ImportError — it should not require NLP packages")


# ---------------------------------------------------------------------------
# tokenize_words()
# ---------------------------------------------------------------------------

class TestTokenizeWords:
    def test_simple_backend_splits_on_whitespace(self):
        """Default 'simple' backend splits on whitespace."""
        tp = _make_tp({"word_tokenizer": {"backend": "simple"}})
        result = tp.tokenize_words("hello world foo")
        assert result == ["hello", "world", "foo"]

    def test_simple_backend_normalizes_first(self):
        """tokenize_words normalizes text before splitting (NFKC by default)."""
        tp = _make_tp({"word_tokenizer": {"backend": "simple"}, "normalizer": {"backend": "nfkc"}})
        result = tp.tokenize_words("ﬁle ﬂoor")  # ﬁle ﬂoor
        assert result == ["file", "floor"]

    def test_simple_backend_empty_string(self):
        tp = _make_tp({"word_tokenizer": {"backend": "simple"}})
        result = tp.tokenize_words("")
        assert result == []

    def test_simple_backend_collapses_whitespace(self):
        """Extra whitespace is collapsed during normalization, so no empty tokens."""
        tp = _make_tp({"word_tokenizer": {"backend": "simple"}})
        result = tp.tokenize_words("  hello   world  ")
        assert "" not in result
        assert "hello" in result and "world" in result

    def test_returns_list(self):
        tp = _make_tp()
        assert isinstance(tp.tokenize_words("test"), list)


# ---------------------------------------------------------------------------
# tokenize_sentences() stub
# ---------------------------------------------------------------------------

class TestTokenizeSentencesStub:
    def test_raises_not_implemented_when_no_segmenter(self):
        """tokenize_sentences raises NotImplementedError when _segmenter is None."""
        tp = _make_tp({})
        with pytest.raises(NotImplementedError):
            tp.tokenize_sentences("This is a sentence. And another.")
