"""
tests/text_processing/test_matchers_properties.py
=================================================
Property-based tests for LexicalMatcher and SemanticMatcher.
Properties 4, 5, 6, 9, 10.
"""

import numpy as np
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from text_processing.matchers import LexicalMatcher, SemanticMatcher
from text_processing.normalizers import WhitespaceNormalizer

_ws = WhitespaceNormalizer()


# ---------------------------------------------------------------------------
# Mock FAISS index
# ---------------------------------------------------------------------------

class MockFaissIndex:
    def __init__(self, similarity):
        self._sim = similarity

    def search(self, query_emb, k):
        distances = np.array([[self._sim]])
        indices = np.array([[0]])
        return distances, indices


def _mock_embed(text):
    return np.array([[0.5, 0.5, 0.5, 0.5]], dtype=np.float32)


# ---------------------------------------------------------------------------
# Property 4: Embedded needle always found
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = frozenset({
    "found_sentence", "page_index", "prefix", "suffix",
    "block_bbox", "span_bboxes", "score",
})


@settings(max_examples=50)
@given(
    base=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters=" "),
        min_size=30,
    ),
    needle_start=st.integers(min_value=0),
    needle_len=st.integers(min_value=10, max_value=30),
)
def test_lexical_matcher_embedded_needle_found(base: str, needle_start: int, needle_len: int):
    """Property 4: embedded substring needle of length >= 10 is always found."""
    if len(base) < 30:
        return
    start = needle_start % max(1, len(base) - needle_len)
    end = start + min(needle_len, len(base) - start)
    needle = base[start:end]
    if len(_ws.normalize(needle)) < 10:
        return
    matcher = LexicalMatcher()
    result = matcher.search(needle, base, {0: base}, [])
    assert result is not None
    assert _REQUIRED_KEYS.issubset(result.keys())


# ---------------------------------------------------------------------------
# Property 5: Short needle always returns None
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(st.text(max_size=9))
def test_lexical_matcher_short_needle_always_none(short: str):
    """Property 5: any string < 10 chars after normalise_ws must return None."""
    if len(_ws.normalize(short)) >= 10:
        return
    matcher = LexicalMatcher()
    result = matcher.search(short, short * 5, {0: short * 5}, [])
    assert result is None


# ---------------------------------------------------------------------------
# Property 6: Empty inputs always return None
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(st.text(min_size=15))
def test_lexical_matcher_empty_full_text_returns_none(needle: str):
    """Property 6: empty full_text always returns None."""
    assume(len(_ws.normalize(needle)) >= 10)
    matcher = LexicalMatcher()
    result = matcher.search(needle, "", {0: "some page text"}, [])
    assert result is None


@settings(max_examples=50)
@given(st.text(min_size=15))
def test_lexical_matcher_empty_page_texts_returns_none(needle: str):
    """Property 6: empty page_texts always returns None."""
    assume(len(_ws.normalize(needle)) >= 10)
    matcher = LexicalMatcher()
    result = matcher.search(needle, needle, {}, [])
    assert result is None


# ---------------------------------------------------------------------------
# Property 9: SemanticMatcher guard — None index always returns None
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(st.text())
def test_semantic_matcher_none_index_always_returns_none(sentence: str):
    """Property 9: faiss_index=None always returns None."""
    matcher = SemanticMatcher()
    store = {"faiss_index": None, "sentences": ["some sentence"], "pages": [0]}
    result = matcher.search(sentence, store, _mock_embed, 0.7)
    assert result is None


# ---------------------------------------------------------------------------
# Property 10: SemanticMatcher guard — empty sentences always returns None
# ---------------------------------------------------------------------------

@settings(max_examples=50)
@given(st.text())
def test_semantic_matcher_empty_sentences_always_returns_none(sentence: str):
    """Property 10: sentences=[] always returns None."""
    matcher = SemanticMatcher()
    store = {"faiss_index": MockFaissIndex(0.9), "sentences": [], "pages": []}
    result = matcher.search(sentence, store, _mock_embed, 0.7)
    assert result is None
