"""
tests/text_processing/test_matchers.py
======================================
LexicalMatcher and SemanticMatcher example-based tests.

Migrated from tests/pdf_extractor/test_text_utils.py.
"""

import numpy as np
import pytest

from text_processing.matchers import LexicalMatcher, SemanticMatcher


# ---------------------------------------------------------------------------
# Mock FAISS index for SemanticMatcher tests
# ---------------------------------------------------------------------------

class MockFaissIndex:
    def __init__(self, similarity):
        self._sim = similarity

    def search(self, query_emb, k):
        distances = np.array([[self._sim]])
        indices = np.array([[0]])
        return distances, indices


def _mock_embed(text):
    """Mock embed function: returns a (1, 4) numpy float32 array."""
    return np.array([[0.5, 0.5, 0.5, 0.5]], dtype=np.float32)


# ---------------------------------------------------------------------------
# LexicalMatcher
# ---------------------------------------------------------------------------

_NEEDLE_LONG = "This is a well-formed sentence from the document."
_PAGE_TEXT = "Some introductory content. This is a well-formed sentence from the document. More text follows."
_PAGE_TEXTS = {0: _PAGE_TEXT}
_FULL_TEXT = _PAGE_TEXT
_BLOCKS: list = []


class TestLexicalMatcherPass1:
    """Pass 1 hit: needle found via whitespace normalisation."""

    def test_pass1_hit_returns_result(self):
        matcher = LexicalMatcher()
        result = matcher.search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        assert result["score"] == 1.0

    def test_result_has_required_keys(self):
        matcher = LexicalMatcher()
        result = matcher.search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        required = {"found_sentence", "page_index", "prefix", "suffix", "block_bbox", "span_bboxes", "score"}
        assert required.issubset(result.keys())


class TestLexicalMatcherPass2:
    """Pass 2 hit: needle found only after punctuation stripping."""

    def test_pass2_hit_returns_result(self):
        matcher = LexicalMatcher()
        needle = "This is a well-formed sentence, from the document!"
        page_txt = "This is a wellformed sentence from the document more text here yes indeed"
        result = matcher.search(needle, page_txt, {0: page_txt}, _BLOCKS)
        assert result is not None
        assert result["score"] == 0.9


class TestLexicalMatcherMiss:
    """Both-pass miss -> returns None."""

    def test_both_passes_fail_returns_none(self):
        matcher = LexicalMatcher()
        needle = "completely different sentence not found anywhere in this text"
        haystack = "entirely unrelated content about something else entirely different yes"
        result = matcher.search(needle, haystack, {0: haystack}, _BLOCKS)
        assert result is None


class TestLexicalMatcherPreCheck:
    """Pre-check: short needle or empty inputs -> None."""

    def test_short_needle_returns_none(self):
        matcher = LexicalMatcher()
        result = matcher.search("short", _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is None

    def test_empty_full_text_returns_none(self):
        matcher = LexicalMatcher()
        result = matcher.search(_NEEDLE_LONG, "", _PAGE_TEXTS, _BLOCKS)
        assert result is None

    def test_empty_page_texts_returns_none(self):
        matcher = LexicalMatcher()
        result = matcher.search(_NEEDLE_LONG, _FULL_TEXT, {}, _BLOCKS)
        assert result is None


class TestLexicalMatcherPageAttribution:
    def test_correct_page_index_returned(self):
        matcher = LexicalMatcher()
        page_texts = {
            0: "Irrelevant page zero content without the needle sentence here.",
            1: "This is the target sentence used for page attribution testing.",
        }
        needle = "This is the target sentence used for page attribution testing."
        full = " ".join(page_texts.values())
        result = matcher.search(needle, full, page_texts, _BLOCKS)
        assert result is not None
        assert result["page_index"] == 1


class TestLexicalMatcherPrefixSuffix:
    def test_prefix_length_at_most_64(self):
        matcher = LexicalMatcher()
        result = matcher.search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        assert len(result["prefix"]) <= 64

    def test_suffix_length_at_most_64(self):
        matcher = LexicalMatcher()
        result = matcher.search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        assert len(result["suffix"]) <= 64


# ---------------------------------------------------------------------------
# SemanticMatcher
# ---------------------------------------------------------------------------

def _make_store(similarity=0.9, sentences=None):
    sentences = sentences if sentences is not None else ["The quick brown fox jumps over the lazy dog."]
    pages = [0] * len(sentences)
    return {
        "faiss_index": MockFaissIndex(similarity) if sentences else None,
        "sentences": sentences,
        "pages": pages,
        "block_bboxes": [(10, 20, 200, 40)] * len(sentences),
        "span_bboxes": [[{"text": s, "bbox": (10, 20, 200, 40)}] for s in sentences],
    }


class TestSemanticMatcherGuards:
    def test_none_index_returns_none(self):
        matcher = SemanticMatcher()
        store = _make_store()
        store["faiss_index"] = None
        result = matcher.search("some sentence", store, _mock_embed, 0.7)
        assert result is None

    def test_empty_sentences_returns_none(self):
        matcher = SemanticMatcher()
        store = {"faiss_index": MockFaissIndex(0.9), "sentences": [], "pages": []}
        result = matcher.search("some sentence", store, _mock_embed, 0.7)
        assert result is None


class TestSemanticMatcherNearMatch:
    def test_near_match_returns_result(self):
        matcher = SemanticMatcher()
        store = _make_store(similarity=0.95)
        result = matcher.search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result["score"] == pytest.approx(0.95)

    def test_near_match_has_required_keys(self):
        matcher = SemanticMatcher()
        store = _make_store(similarity=0.9)
        result = matcher.search("some sentence", store, _mock_embed, 0.7)
        required = {"found_sentence", "page_index", "prefix", "suffix", "block_bbox", "span_bboxes", "score"}
        assert result is not None
        assert required.issubset(result.keys())

    def test_near_match_prefix_suffix_with_page_texts(self):
        matcher = SemanticMatcher()
        store = _make_store(similarity=0.9)
        sentence = store["sentences"][0]
        page_texts = {0: f"Context before. {sentence} And after."}
        result = matcher.search("some sentence", store, _mock_embed, 0.7, page_texts=page_texts)
        assert result is not None
        assert result["prefix"] != "" or result["suffix"] != ""


class TestSemanticMatcherBelowThreshold:
    def test_below_threshold_returns_none(self):
        matcher = SemanticMatcher()
        store = _make_store(similarity=0.3)
        result = matcher.search("some sentence", store, _mock_embed, 0.7)
        assert result is None
