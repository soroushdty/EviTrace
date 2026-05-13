"""
tests/utils/test_sentence_processor.py
=======================================
Tests for ``pdf_extractor.processing.sentence_processor``:
  - Remove _RE_SENTENCE_SPLIT constant from sentence_processor.py
  - Add text_processor as 3rd positional parameter to process_sentences()
  - Replace regex split with text_processor.tokenize_sentences()
  - pdf_extractor.py passes a TextProcessor instance to process_sentences()
"""

import importlib
import inspect
import sys
import types
from unittest.mock import MagicMock

import pytest

from text_processing.base import TextProcessor


# ---------------------------------------------------------------------------
# Autouse fixture — prevent spacy.load('en_core_sci_sm') from running in CI
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_scispacy(monkeypatch):
    """Prevent spacy.load('en_core_sci_sm') from running in CI."""
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_sentence_processor():
    """Re-import sentence_processor module bypassing cache for attribute checks."""
    mod_name = "pdf_extractor.processing.sentence_processor"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# 1. _RE_SENTENCE_SPLIT no longer exists in sentence_processor
# ---------------------------------------------------------------------------

class TestRegexConstantRemoved:
    """Verify that _RE_SENTENCE_SPLIT has been deleted from sentence_processor."""

    def test_re_sentence_split_not_present(self):
        """_RE_SENTENCE_SPLIT must not exist as a module-level attribute."""
        sp = _fresh_sentence_processor()
        assert not hasattr(sp, "_RE_SENTENCE_SPLIT"), (
            "_RE_SENTENCE_SPLIT still exists in sentence_processor. "
            "Delete the re.compile(...) constant."
        )


# ---------------------------------------------------------------------------
# 2. process_sentences accepts text_processor as the 3rd positional parameter
# ---------------------------------------------------------------------------

class TestSignature:
    """Verify the updated function signature."""

    def test_process_sentences_has_text_processor_param(self):
        """process_sentences must accept 'text_processor' as 3rd parameter."""
        sp = _fresh_sentence_processor()
        sig = inspect.signature(sp.process_sentences)
        params = list(sig.parameters.keys())
        assert "text_processor" in params, (
            f"'text_processor' not found in process_sentences parameters: {params}"
        )

    def test_text_processor_is_third_positional_param(self):
        """text_processor must be the 3rd positional parameter."""
        sp = _fresh_sentence_processor()
        sig = inspect.signature(sp.process_sentences)
        params = list(sig.parameters.keys())
        assert params[2] == "text_processor", (
            f"Expected 3rd parameter to be 'text_processor', got '{params[2]}'"
        )


# ---------------------------------------------------------------------------
# 3. process_sentences uses text_processor.tokenize_sentences (not regex)
# ---------------------------------------------------------------------------

class TestTokenizeSentencesIsCalled:
    """Verify that tokenize_sentences is invoked for each text block."""

    def _make_tp(self, sentences):
        """Return a MagicMock TextProcessor that returns *sentences* from tokenize_sentences."""
        tp = MagicMock(spec=TextProcessor)
        tp.tokenize_sentences.return_value = sentences
        return tp

    def _make_block(self, text, page_index=0):
        return {
            "text": text,
            "page_index": page_index,
            "block_bbox": [0, 0, 100, 20],
            "spans": [{"text": text, "bbox": [0, 0, 100, 20]}],
        }

    def test_tokenize_sentences_called_once_per_block(self):
        """tokenize_sentences must be called once for each text block."""
        sp = _fresh_sentence_processor()
        tp = self._make_tp(
            ["This is sentence one.", "This is sentence two, it is longer."]
        )
        blocks = [self._make_block("Some text block.", page_index=0)]
        sp.process_sentences(blocks, len_filter=5, text_processor=tp)
        assert tp.tokenize_sentences.call_count == 1

    def test_tokenize_sentences_called_with_normalised_text(self):
        """tokenize_sentences is called with the normalised text (not raw)."""
        sp = _fresh_sentence_processor()
        tp = self._make_tp(["A long enough sentence here."])
        # Raw text with a soft line-break that normalise_text should heal
        blocks = [self._make_block("Hello\nworld", page_index=0)]
        sp.process_sentences(blocks, len_filter=5, text_processor=tp)
        # normalise_text turns '\n' (not followed by uppercase) into ' '
        call_arg = tp.tokenize_sentences.call_args[0][0]
        assert "\n" not in call_arg, (
            "tokenize_sentences was called with un-normalised text (newline present)"
        )

    def test_multiple_blocks_each_call_tokenize(self):
        """With two blocks, tokenize_sentences is called twice."""
        sp = _fresh_sentence_processor()
        tp = self._make_tp(
            ["This is a sufficiently long sentence for the test."]
        )
        blocks = [
            self._make_block("Block one text here.", page_index=0),
            self._make_block("Block two text here.", page_index=1),
        ]
        sp.process_sentences(blocks, len_filter=5, text_processor=tp)
        assert tp.tokenize_sentences.call_count == 2


# ---------------------------------------------------------------------------
# 4. Output keys are unchanged
# ---------------------------------------------------------------------------

class TestOutputKeys:
    """Verify the output record structure is unaffected by the refactor."""

    def _make_tp_with(self, sentences):
        tp = MagicMock(spec=TextProcessor)
        tp.tokenize_sentences.return_value = sentences
        return tp

    def test_output_record_keys(self):
        """Each output record must have exactly: sentence, page_index, block_bbox, span_bboxes."""
        sp = _fresh_sentence_processor()
        expected_keys = {"sentence", "page_index", "block_bbox", "span_bboxes"}
        tp = self._make_tp_with(
            ["This is a long enough sentence to survive the filter."]
        )
        blocks = [
            {
                "text": "Some text.",
                "page_index": 2,
                "block_bbox": [10, 20, 200, 40],
                "spans": [{"text": "Some text.", "bbox": [10, 20, 200, 40]}],
            }
        ]
        records = sp.process_sentences(blocks, len_filter=5, text_processor=tp)
        assert len(records) == 1
        assert set(records[0].keys()) == expected_keys

    def test_output_sentence_value_comes_from_mock(self):
        """The 'sentence' field must contain the text returned by tokenize_sentences."""
        sp = _fresh_sentence_processor()
        mocked_sentence = "This is the mocked sentence from the mock tokenizer."
        tp = self._make_tp_with([mocked_sentence])
        blocks = [
            {
                "text": "Irrelevant raw text.",
                "page_index": 0,
                "block_bbox": [0, 0, 50, 10],
                "spans": [],
            }
        ]
        records = sp.process_sentences(blocks, len_filter=5, text_processor=tp)
        assert len(records) == 1
        assert records[0]["sentence"] == mocked_sentence

    def test_page_index_preserved(self):
        """The 'page_index' in output records must match the block's page_index."""
        sp = _fresh_sentence_processor()
        tp = self._make_tp_with(
            ["This sentence is definitely long enough to pass the filter here."]
        )
        blocks = [
            {
                "text": "Text on page seven.",
                "page_index": 7,
                "block_bbox": [0, 0, 100, 20],
                "spans": [],
            }
        ]
        records = sp.process_sentences(blocks, len_filter=5, text_processor=tp)
        assert len(records) == 1
        assert records[0]["page_index"] == 7

    def test_len_filter_still_applied(self):
        """Sentences shorter than len_filter must be discarded."""
        sp = _fresh_sentence_processor()
        tp = self._make_tp_with(["short", "This is a long enough sentence to pass the filter."])
        blocks = [
            {
                "text": "Some text.",
                "page_index": 0,
                "block_bbox": [0, 0, 100, 20],
                "spans": [],
            }
        ]
        # len_filter=10 → "short" (5 chars) is discarded
        records = sp.process_sentences(blocks, len_filter=10, text_processor=tp)
        assert len(records) == 1
        assert records[0]["sentence"] == "This is a long enough sentence to pass the filter."
