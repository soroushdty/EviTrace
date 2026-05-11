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

import sys
import unicodedata
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tp(config=None):
    """Construct a TextProcessor with optional config, defaulting to empty."""
    from utils.text_processor import TextProcessor

    mock_spacy = MagicMock()
    mock_scispacy = MagicMock()
    mock_sent = MagicMock()
    mock_sent.text = "Sentence one."
    mock_doc = MagicMock()
    mock_doc.sents = [mock_sent]
    mock_nlp = MagicMock(return_value=mock_doc)
    mock_spacy.load.return_value = mock_nlp

    with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
        return TextProcessor(config=config or {})


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

class TestInstantiation:
    def test_default_config_does_not_raise(self):
        """TextProcessor() with config={} must not raise any exception."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={})
        assert tp is not None

    def test_none_config_does_not_raise(self):
        """TextProcessor(config=None) must not raise."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
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

    @pytest.mark.parametrize("raw", [
        # Pure whitespace: collapses to ""
        "   ",
        # Tabs and newlines mixed with spaces
        "hello\t\tworld\n\nfoo",
        # Leading/trailing whitespace around ligatures
        "  ﬁle  ﬂoor  ",
        # Composed accented characters (NFC/NFKC both handle these)
        "café naïve",
        # Already-normalized plain ASCII
        "already normalized text",
        # Empty string
        "",
        # Only non-whitespace ligatures
        "ﬁﬂ",
        # Mixed ligature and normal chars with extra whitespace
        "ﬁle  ﬂoor   baz",
    ])
    def test_idempotent_arbitrary_nfkc(self, raw):
        """normalize(normalize(x)) == normalize(x) for a variety of input shapes."""
        tp = _make_tp({"normalizer": {"backend": "nfkc"}})
        once = tp.normalize(raw)
        twice = tp.normalize(once)
        assert once == twice, (
            f"Idempotency failed for {raw!r}: "
            f"first={once!r}, second={twice!r}"
        )

    @pytest.mark.parametrize("raw", [
        "hello  world",
        "  leading and trailing  ",
        "café",
        "",
        "\t\n mixed \r whitespace",
    ])
    def test_idempotent_arbitrary_nfc(self, raw):
        """normalize(normalize(x)) == normalize(x) for NFC mode too."""
        tp = _make_tp({"normalizer": {"backend": "nfc"}})
        once = tp.normalize(raw)
        twice = tp.normalize(once)
        assert once == twice, (
            f"NFC idempotency failed for {raw!r}: "
            f"first={once!r}, second={twice!r}"
        )


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

    def test_completely_different_strings_return_zero(self):
        """compare() must return exactly 0.0 for strings with no characters in common.

        SequenceMatcher ratio is 2*M/T; when M==0 (no matching blocks at all),
        the ratio is exactly 0.0.  'aaaa' vs 'zzzz' share no character.
        """
        tp = _make_tp()
        assert tp.compare("aaaa", "zzzz") == pytest.approx(0.0)
        assert tp.compare("abc", "xyz") == pytest.approx(0.0)

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
    def test_raises_not_implemented_when_segmenter_explicitly_none(self):
        """tokenize_sentences raises NotImplementedError when _segmenter is forcibly set to None."""
        tp = _make_tp({})
        # Force _segmenter to None to exercise the guard (task 2.1 stub path).
        tp._segmenter = None
        with pytest.raises(NotImplementedError):
            tp.tokenize_sentences("This is a sentence. And another.")


# ---------------------------------------------------------------------------
# SentenceSegment hierarchy
# ---------------------------------------------------------------------------

class TestSentenceSegmentHierarchy:
    """SentenceSegment is an abstract subclass of TextProcessor."""

    def test_sentence_segment_is_subclass_of_text_processor(self):
        from utils.text_processor import SentenceSegment, TextProcessor
        assert issubclass(SentenceSegment, TextProcessor)

    def test_sentence_segment_tokenize_sentences_raises_not_implemented(self):
        from utils.text_processor import SentenceSegment
        seg = SentenceSegment()
        with pytest.raises(NotImplementedError):
            seg.tokenize_sentences("Hello world.")

    def test_all_backends_are_subclasses_of_text_processor(self):
        from utils.text_processor import (
            TextProcessor,
            ScispaCySentenceSegment,
            WtpSplitSentenceSegment,
            NLTKPunktSentenceSegment,
            SpacySentencizerSegment,
            StanzaSentenceSegment,
        )
        for cls in (
            ScispaCySentenceSegment,
            WtpSplitSentenceSegment,
            NLTKPunktSentenceSegment,
            SpacySentencizerSegment,
            StanzaSentenceSegment,
        ):
            assert issubclass(cls, TextProcessor), f"{cls.__name__} is not a subclass of TextProcessor"

    def test_all_backends_are_instances_of_text_processor(self):
        from utils.text_processor import (
            TextProcessor,
            ScispaCySentenceSegment,
            WtpSplitSentenceSegment,
            NLTKPunktSentenceSegment,
            SpacySentencizerSegment,
            StanzaSentenceSegment,
        )

        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp

        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            for cls in (
                ScispaCySentenceSegment,
                WtpSplitSentenceSegment,
                NLTKPunktSentenceSegment,
                SpacySentencizerSegment,
                StanzaSentenceSegment,
            ):
                instance = cls()
                assert isinstance(instance, TextProcessor), f"{cls.__name__} instance is not a TextProcessor"


class TestScispaCySentenceSegment:
    """ScispaCySentenceSegment eagerly loads the default model."""

    def test_import_error_raised_with_pip_hint(self):
        from utils.text_processor import ScispaCySentenceSegment
        with patch.dict(sys.modules, {"scispacy": None, "spacy": None}):
            with pytest.raises(ImportError, match="pip install scispacy"):
                ScispaCySentenceSegment()

    def test_import_error_hint_mentions_model_download(self):
        from utils.text_processor import ScispaCySentenceSegment
        with patch.dict(sys.modules, {"scispacy": None, "spacy": None}):
            with pytest.raises(ImportError, match="en_core_sci_sm"):
                ScispaCySentenceSegment()

    def test_non_default_model_is_lazy_loaded(self):
        from utils.text_processor import ScispaCySentenceSegment

        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world."
        mock_doc = MagicMock()
        mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp

        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            seg = ScispaCySentenceSegment(config={"model": "en_core_sci_lg"})
            assert mock_spacy.load.call_count == 0
            seg.tokenize_sentences("Hello world.")

        assert mock_spacy.load.call_count == 1

    def test_model_loaded_at_most_once(self):
        """Model should be cached: loading happens only on first call."""
        from utils.text_processor import ScispaCySentenceSegment

        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        # Build a mock doc whose .sents yields sentence-like objects
        mock_sent = MagicMock()
        mock_sent.text = "Hello world."
        mock_doc = MagicMock()
        mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp

        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            seg = ScispaCySentenceSegment()
            assert mock_spacy.load.call_count == 1
            seg.tokenize_sentences("Hello world.")
            seg.tokenize_sentences("Hello world.")

        # spacy.load should have been called exactly once during eager init
        assert mock_spacy.load.call_count == 1

    def test_returns_list_of_strings_when_model_present(self):
        from utils.text_processor import ScispaCySentenceSegment

        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        sent1 = MagicMock(); sent1.text = "First sentence."
        sent2 = MagicMock(); sent2.text = "Second sentence."
        mock_doc = MagicMock(); mock_doc.sents = [sent1, sent2]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp

        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            seg = ScispaCySentenceSegment()
            result = seg.tokenize_sentences("First sentence. Second sentence.")

        assert result == ["First sentence.", "Second sentence."]


class TestWtpSplitSentenceSegment:
    """WtpSplitSentenceSegment raises ImportError with wtpsplit hint when absent."""

    def test_import_error_raised_with_pip_hint(self):
        from utils.text_processor import WtpSplitSentenceSegment
        with patch.dict(sys.modules, {"wtpsplit": None}):
            seg = WtpSplitSentenceSegment()
            with pytest.raises(ImportError, match="pip install wtpsplit"):
                seg.tokenize_sentences("Hello world.")

    def test_model_loaded_at_most_once(self):
        from utils.text_processor import WtpSplitSentenceSegment

        mock_wtpsplit = MagicMock()
        mock_splitter = MagicMock()
        mock_splitter.split.return_value = ["Hello world."]
        mock_wtpsplit.WtP.return_value = mock_splitter

        with patch.dict(sys.modules, {"wtpsplit": mock_wtpsplit}):
            seg = WtpSplitSentenceSegment()
            seg.tokenize_sentences("Hello world.")
            seg.tokenize_sentences("Hello world.")

        assert mock_wtpsplit.WtP.call_count == 1


class TestNLTKPunktSentenceSegment:
    """NLTKPunktSentenceSegment raises ImportError with nltk hint when absent."""

    def test_import_error_raised_with_pip_hint(self):
        from utils.text_processor import NLTKPunktSentenceSegment
        with patch.dict(sys.modules, {"nltk": None}):
            seg = NLTKPunktSentenceSegment()
            with pytest.raises(ImportError, match="pip install nltk"):
                seg.tokenize_sentences("Hello world.")

    def test_model_loaded_at_most_once(self):
        from utils.text_processor import NLTKPunktSentenceSegment

        mock_nltk = MagicMock()
        mock_tokenize = MagicMock(return_value=["Hello world."])
        mock_nltk.sent_tokenize = mock_tokenize

        with patch.dict(sys.modules, {"nltk": mock_nltk}):
            seg = NLTKPunktSentenceSegment()
            seg.tokenize_sentences("Hello world.")
            seg.tokenize_sentences("Hello world.")

        # sent_tokenize may be called multiple times (it's stateless), but
        # any internal model/data load should happen at most once.
        # The key invariant: the module is imported at most once (checked via _model caching).
        assert seg._model is not None

    def test_model_set_exactly_once_across_multiple_calls(self):
        """The internal _model attribute is assigned only on the first call.

        Verifies the load-once invariant: after two tokenize_sentences calls,
        `_model` is the mock nltk module itself (not None), and the mock's
        `sent_tokenize` was invoked both times — meaning the guard executed
        correctly and did not re-import on the second call.
        """
        from utils.text_processor import NLTKPunktSentenceSegment

        mock_nltk = MagicMock()
        mock_nltk.sent_tokenize = MagicMock(return_value=["Hello world."])

        with patch.dict(sys.modules, {"nltk": mock_nltk}):
            seg = NLTKPunktSentenceSegment()

            # _model starts as None before any call
            assert seg._model is None

            seg.tokenize_sentences("Hello world.")

            # After first call, _model must be the captured module — not None
            first_model = seg._model
            assert first_model is not None

            seg.tokenize_sentences("Another sentence.")

            # After second call, _model must still be the exact same object
            assert seg._model is first_model


class TestSpacySentencizerSegment:
    """SpacySentencizerSegment raises ImportError with spacy hint when absent."""

    def test_import_error_raised_with_pip_hint(self):
        from utils.text_processor import SpacySentencizerSegment
        with patch.dict(sys.modules, {"spacy": None}):
            seg = SpacySentencizerSegment()
            with pytest.raises(ImportError, match="pip install spacy"):
                seg.tokenize_sentences("Hello world.")

    def test_model_loaded_at_most_once(self):
        from utils.text_processor import SpacySentencizerSegment

        mock_spacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Hello world."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.blank.return_value = mock_nlp
        mock_nlp.add_pipe = MagicMock()

        with patch.dict(sys.modules, {"spacy": mock_spacy}):
            seg = SpacySentencizerSegment()
            seg.tokenize_sentences("Hello world.")
            seg.tokenize_sentences("Hello world.")

        assert mock_spacy.blank.call_count == 1


class TestStanzaSentenceSegment:
    """StanzaSentenceSegment raises ImportError with stanza hint when absent."""

    def test_import_error_raised_with_pip_hint(self):
        from utils.text_processor import StanzaSentenceSegment
        with patch.dict(sys.modules, {"stanza": None}):
            seg = StanzaSentenceSegment()
            with pytest.raises(ImportError, match="pip install stanza"):
                seg.tokenize_sentences("Hello world.")

    def test_model_loaded_at_most_once(self):
        from utils.text_processor import StanzaSentenceSegment

        mock_stanza = MagicMock()
        mock_sent = MagicMock()
        mock_sent.text = "Hello world."
        mock_doc = MagicMock()
        mock_doc.sentences = [mock_sent]
        mock_pipeline = MagicMock(return_value=mock_doc)
        mock_stanza.Pipeline.return_value = mock_pipeline

        with patch.dict(sys.modules, {"stanza": mock_stanza}):
            seg = StanzaSentenceSegment()
            seg.tokenize_sentences("Hello world.")
            seg.tokenize_sentences("Hello world.")

        assert mock_stanza.Pipeline.call_count == 1


class TestTextProcessorSentenceTokenizerWiring:
    """TextProcessor.__init__ wires the correct SentenceSegment from config."""

    def test_default_backend_is_scispacy(self):
        from utils.text_processor import TextProcessor, ScispaCySentenceSegment
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={})
        assert isinstance(tp._segmenter, ScispaCySentenceSegment)

    def test_scispacy_backend_explicit(self):
        from utils.text_processor import TextProcessor, ScispaCySentenceSegment
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={"sentence_tokenizer": {"backend": "scispacy"}})
        assert isinstance(tp._segmenter, ScispaCySentenceSegment)

    def test_wtpsplit_backend(self):
        from utils.text_processor import TextProcessor, WtpSplitSentenceSegment
        tp = TextProcessor(config={"sentence_tokenizer": {"backend": "wtpsplit"}})
        assert isinstance(tp._segmenter, WtpSplitSentenceSegment)

    def test_nltk_punkt_backend(self):
        from utils.text_processor import TextProcessor, NLTKPunktSentenceSegment
        tp = TextProcessor(config={"sentence_tokenizer": {"backend": "nltk_punkt"}})
        assert isinstance(tp._segmenter, NLTKPunktSentenceSegment)

    def test_spacy_sentencizer_backend(self):
        from utils.text_processor import TextProcessor, SpacySentencizerSegment
        tp = TextProcessor(config={"sentence_tokenizer": {"backend": "spacy_sentencizer"}})
        assert isinstance(tp._segmenter, SpacySentencizerSegment)

    def test_stanza_backend(self):
        from utils.text_processor import TextProcessor, StanzaSentenceSegment
        tp = TextProcessor(config={"sentence_tokenizer": {"backend": "stanza"}})
        assert isinstance(tp._segmenter, StanzaSentenceSegment)

    def test_unknown_backend_raises_value_error(self):
        from utils.text_processor import TextProcessor
        with pytest.raises(ValueError, match="sentence_tokenizer"):
            TextProcessor(config={"sentence_tokenizer": {"backend": "bogus_backend"}})

    def test_unknown_backend_error_lists_valid_options(self):
        from utils.text_processor import TextProcessor
        with pytest.raises(ValueError) as exc_info:
            TextProcessor(config={"sentence_tokenizer": {"backend": "bogus"}})
        msg = str(exc_info.value)
        # Must mention at least one valid backend
        assert any(b in msg for b in ("scispacy", "wtpsplit", "nltk_punkt", "spacy_sentencizer", "stanza"))

    def test_tokenize_sentences_delegates_to_segmenter(self):
        """tokenize_sentences calls _segmenter.tokenize_sentences."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={"sentence_tokenizer": {"backend": "scispacy"}})

        mock_segmenter = MagicMock()
        mock_segmenter.tokenize_sentences.return_value = ["Sent one.", "Sent two."]
        tp._segmenter = mock_segmenter

        result = tp.tokenize_sentences("Sent one. Sent two.")
        assert result == ["Sent one.", "Sent two."]
        mock_segmenter.tokenize_sentences.assert_called_once_with("Sent one. Sent two.")


class TestCustomClassPathBackend:
    """Fully qualified class path injection (requirement 5.5)."""

    def test_dotted_path_loads_custom_backend(self):
        """A dotted class path in config loads via importlib."""
        from utils.text_processor import TextProcessor

        # Build a minimal mock backend class that behaves like a SentenceSegment
        class _FakeBackend:
            def __init__(self):
                self._model = None
            def tokenize_sentences(self, text):
                return ["mocked."]

        # Patch importlib to return our fake class
        import importlib
        with patch.object(importlib, "import_module") as mock_import:
            fake_module = MagicMock()
            fake_module._FakeBackend = _FakeBackend
            mock_import.return_value = fake_module

            tp = TextProcessor(config={
                "sentence_tokenizer": {"backend": "my.module._FakeBackend"}
            })

        assert tp._segmenter is not None
        result = tp._segmenter.tokenize_sentences("Hello.")
        assert result == ["mocked."]

    def test_dotted_path_must_contain_dot(self):
        """A backend value without a dot is resolved as a built-in key (raises ValueError if unknown)."""
        from utils.text_processor import TextProcessor
        with pytest.raises(ValueError):
            TextProcessor(config={"sentence_tokenizer": {"backend": "nodotpath"}})

    def test_resolve_sentence_segmenter_method_exists(self):
        """_resolve_sentence_segmenter (the loader helper) must exist on TextProcessor."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={})
        assert callable(getattr(tp, "_resolve_sentence_segmenter", None)), (
            "TextProcessor must expose _resolve_sentence_segmenter as the "
            "class-path injection point (requirement 5.5)"
        )

    def test_custom_backend_tokenize_sentences_end_to_end(self):
        """End-to-end: custom backend loaded via dotted path correctly handles tokenization.

        This verifies the full injection path: config -> _resolve_sentence_segmenter
        -> importlib.import_module -> class instantiation -> tokenize_sentences call.
        """
        from utils.text_processor import TextProcessor

        call_log = []

        class _CustomSegmenter:
            def __init__(self):
                self._model = "loaded"

            def tokenize_sentences(self, text: str):
                call_log.append(text)
                return text.split(". ")

        import importlib
        with patch.object(importlib, "import_module") as mock_import:
            fake_module = MagicMock()
            fake_module._CustomSegmenter = _CustomSegmenter
            mock_import.return_value = fake_module

            tp = TextProcessor(config={
                "sentence_tokenizer": {"backend": "my.custom._CustomSegmenter"}
            })

        sentences = tp.tokenize_sentences("First sentence. Second sentence.")
        assert sentences == ["First sentence", "Second sentence."]
        assert call_log == ["First sentence. Second sentence."]
