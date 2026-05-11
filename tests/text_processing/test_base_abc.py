"""
tests/text_processing/test_base_abc.py
======================================
ABC enforcement tests (Property 1) and lazy model loading tests (Property 2)
for the text_processing.base module.

Migrated from tests/utils/test_text_processor.py.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from text_processing.base import (
    TextProcessor,
    SentenceSegment,
    ScispaCySentenceSegment,
    WtpSplitSentenceSegment,
    NLTKPunktSentenceSegment,
    SpacySentencizerSegment,
    StanzaSentenceSegment,
)


# ---------------------------------------------------------------------------
# Property 1: ABC enforcement — cannot instantiate abstract classes
# ---------------------------------------------------------------------------

class TestABCEnforcement:
    """TextProcessor and SentenceSegment are ABCs that cannot be directly instantiated."""

    def test_text_processor_cannot_be_instantiated(self):
        """TextProcessor is abstract — direct instantiation raises TypeError."""
        with pytest.raises(TypeError):
            TextProcessor()

    def test_sentence_segment_cannot_be_instantiated(self):
        """SentenceSegment is abstract — direct instantiation raises TypeError."""
        with pytest.raises(TypeError):
            SentenceSegment()

    def test_incomplete_subclass_cannot_be_instantiated(self):
        """A subclass missing abstract methods cannot be instantiated."""

        class IncompleteProcessor(TextProcessor):
            def normalize(self, text: str) -> str:
                return text

        with pytest.raises(TypeError):
            IncompleteProcessor()

    def test_all_abstract_methods_exist(self):
        """TextProcessor declares exactly 6 abstract methods."""
        abstract_methods = TextProcessor.__abstractmethods__
        expected = {"normalize", "tokenize_words", "tokenize_sentences", "clean_ocr", "compare", "extract_keywords"}
        assert abstract_methods == expected


# ---------------------------------------------------------------------------
# Hierarchy checks
# ---------------------------------------------------------------------------

class TestHierarchy:
    """SentenceSegment and all backends are subclasses of TextProcessor."""

    def test_sentence_segment_is_subclass_of_text_processor(self):
        assert issubclass(SentenceSegment, TextProcessor)

    def test_all_backends_are_subclasses_of_text_processor(self):
        for cls in (
            ScispaCySentenceSegment,
            WtpSplitSentenceSegment,
            NLTKPunktSentenceSegment,
            SpacySentencizerSegment,
            StanzaSentenceSegment,
        ):
            assert issubclass(cls, TextProcessor), f"{cls.__name__} is not a subclass of TextProcessor"

    def test_all_backends_are_subclasses_of_sentence_segment(self):
        for cls in (
            ScispaCySentenceSegment,
            WtpSplitSentenceSegment,
            NLTKPunktSentenceSegment,
            SpacySentencizerSegment,
            StanzaSentenceSegment,
        ):
            assert issubclass(cls, SentenceSegment), f"{cls.__name__} is not a subclass of SentenceSegment"


# ---------------------------------------------------------------------------
# Property 2: Lazy model loading
# ---------------------------------------------------------------------------

class TestScispaCySentenceSegment:
    """ScispaCySentenceSegment eagerly loads the default model."""

    def test_import_error_raised_with_pip_hint(self):
        with patch.dict(sys.modules, {"scispacy": None, "spacy": None}):
            with pytest.raises(ImportError, match="pip install scispacy"):
                ScispaCySentenceSegment()

    def test_non_default_model_is_lazy_loaded(self):
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
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
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
            seg.tokenize_sentences("Again.")

        assert mock_spacy.load.call_count == 1

    def test_returns_list_of_strings(self):
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        sent1 = MagicMock(); sent1.text = "First."
        sent2 = MagicMock(); sent2.text = "Second."
        mock_doc = MagicMock(); mock_doc.sents = [sent1, sent2]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp

        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            seg = ScispaCySentenceSegment()
            result = seg.tokenize_sentences("First. Second.")

        assert result == ["First.", "Second."]


class TestWtpSplitSentenceSegment:
    def test_import_error_raised_with_pip_hint(self):
        with patch.dict(sys.modules, {"wtpsplit": None}):
            with pytest.raises(ImportError, match="pip install wtpsplit"):
                WtpSplitSentenceSegment()

    def test_model_loaded_at_most_once(self):
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
    def test_import_error_raised_with_pip_hint(self):
        with patch.dict(sys.modules, {"nltk": None}):
            with pytest.raises(ImportError, match="pip install nltk"):
                NLTKPunktSentenceSegment()

    def test_model_set_once(self):
        mock_nltk = MagicMock()
        mock_nltk.sent_tokenize = MagicMock(return_value=["Hello world."])

        with patch.dict(sys.modules, {"nltk": mock_nltk}):
            seg = NLTKPunktSentenceSegment()
            first_model = seg._model
            seg.tokenize_sentences("Hello world.")
            assert seg._model is first_model


class TestSpacySentencizerSegment:
    def test_import_error_raised_with_pip_hint(self):
        with patch.dict(sys.modules, {"spacy": None}):
            with pytest.raises(ImportError, match="pip install spacy"):
                SpacySentencizerSegment()

    def test_model_loaded_at_most_once(self):
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
    def test_import_error_raised_with_pip_hint(self):
        with patch.dict(sys.modules, {"stanza": None}):
            with pytest.raises(ImportError, match="pip install stanza"):
                StanzaSentenceSegment()

    def test_model_loaded_at_most_once(self):
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


# ---------------------------------------------------------------------------
# Unrelated abstract methods raise NotImplementedError
# ---------------------------------------------------------------------------

class TestUnrelatedMethodsRaiseNotImplemented:
    """All SentenceSegment backends raise NotImplementedError for unrelated methods."""

    @pytest.fixture
    def scispacy_seg(self):
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            return ScispaCySentenceSegment()

    def test_normalize_raises(self, scispacy_seg):
        with pytest.raises(NotImplementedError):
            scispacy_seg.normalize("text")

    def test_tokenize_words_raises(self, scispacy_seg):
        with pytest.raises(NotImplementedError):
            scispacy_seg.tokenize_words("text")

    def test_clean_ocr_raises(self, scispacy_seg):
        with pytest.raises(NotImplementedError):
            scispacy_seg.clean_ocr("text")

    def test_compare_raises(self, scispacy_seg):
        with pytest.raises(NotImplementedError):
            scispacy_seg.compare("a", "b")

    def test_extract_keywords_raises(self, scispacy_seg):
        with pytest.raises(NotImplementedError):
            scispacy_seg.extract_keywords("text")
