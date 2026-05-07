"""
test_text_utils.py
==================
Tests for :mod:`evi_trace.utils.text_utils`.

These normalisation functions are DISTINCT from
:func:`sentence_processor.normalise_text`, which heals line breaks for
    pytest tests/test_text_utils.py -v
text comparison.

Run with::

    pytest tests/test_text_evi_trace.utils.py -v
"""

import pytest
import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from evi_trace.utils.text_utils import normalise_full, normalise_ws, exact_match_search, semantic_search


# ---------------------------------------------------------------------------
# Mock FAISS index for semantic_search tests (no real FAISS dependency)
# ---------------------------------------------------------------------------

class MockFaissIndex:
    def __init__(self, similarity):
        self._sim = similarity

    def search(self, query_emb, k):
        distances = np.array([[self._sim]])
        indices = np.array([[0]])
        return distances, indices


# ---------------------------------------------------------------------------
# normalise_ws
# ---------------------------------------------------------------------------

class TestNormaliseWs:
    def test_empty_string_returns_empty(self):
        """normalise_ws('') -> ''"""
        assert normalise_ws("") == ""

    def test_collapses_whitespace_and_lowercases(self):
        """Multiple surrounding/internal spaces are collapsed; string is lowercased."""
        assert normalise_ws("  hello   world  ") == "hello world"

    def test_uppercases_lowercased(self):
        """UPPER CASE input is fully lowercased."""
        assert normalise_ws("UPPER CASE") == "upper case"

    def test_preserves_punctuation(self):
        """Punctuation characters are NOT removed by normalise_ws."""
        assert normalise_ws("hello, world!") == "hello, world!"

    def test_idempotent_explicit(self):
        """Applying normalise_ws twice is the same as applying it once."""
        examples = [
            "  Hello   World  ",
            "UPPER CASE",
            "already normalised",
            "",
            "  multiple   spaces   everywhere  ",
            "tabs\there",
            "\nnewlines\n",
        ]
        for s in examples:
            single = normalise_ws(s)
            double = normalise_ws(normalise_ws(s))
            assert single == double, f"Not idempotent for {s!r}: {single!r} != {double!r}"


# ---------------------------------------------------------------------------
# normalise_full
# ---------------------------------------------------------------------------

class TestNormaliseFull:
    def test_empty_string_returns_empty(self):
        """normalise_full('') -> ''"""
        assert normalise_full("") == ""

    def test_strips_punctuation(self):
        """normalise_full removes punctuation and collapses whitespace."""
        assert normalise_full("hello, world!") == "hello world"

    def test_collapses_whitespace(self):
        """normalise_full collapses whitespace just like normalise_ws."""
        assert normalise_full("  hello   world  ") == "hello world"

    def test_idempotent_explicit(self):
        """Applying normalise_full twice is the same as applying it once."""
        examples = [
            "hello, world!",
            "UPPER-CASE: test.",
            "",
            "  punctuation!!!  everywhere???  ",
            "already clean",
            "tabs\there",
        ]
        for s in examples:
            single = normalise_full(s)
            double = normalise_full(normalise_full(s))
            assert single == double, f"Not idempotent for {s!r}: {single!r} != {double!r}"


# ---------------------------------------------------------------------------
# Property-based tests (Hypothesis)
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(st.text())
def test_normalise_ws_idempotent_property(s: str):
    """normalise_ws(normalise_ws(s)) == normalise_ws(s) for all strings s."""
    assert normalise_ws(normalise_ws(s)) == normalise_ws(s)


@settings(max_examples=200)
@given(st.text())
def test_normalise_full_idempotent_property(s: str):
    """normalise_full(normalise_full(s)) == normalise_full(s) for all strings s."""
    assert normalise_full(normalise_full(s)) == normalise_full(s)


# ---------------------------------------------------------------------------
# exact_match_search
# ---------------------------------------------------------------------------

# Minimal fixtures used across tests
_NEEDLE_LONG = "This is a well-formed sentence from the document."
_PAGE_TEXT   = "Some introductory content. This is a well-formed sentence from the document. More text follows."
_PAGE_TEXTS  = {0: _PAGE_TEXT}
_FULL_TEXT   = _PAGE_TEXT
_BLOCKS: list = []


class TestExactMatchSearchPass1:
    """Pass 1 hit: needle found via whitespace normalisation."""

    def test_pass1_hit_returns_exact_match_status(self):
        """When needle is in haystack after whitespace normalisation, status is 'exact_match'."""
        result = exact_match_search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        assert result["verification_status"] == "exact_match"


class TestExactMatchSearchPass2:
    """Pass 2 hit: needle found only after punctuation stripping."""

    def test_pass2_hit_returns_exact_match_status(self):
        """When punctuation differs, Pass 2 still finds the match."""
        # Needle has extra punctuation; page text has equivalent sans punctuation chars
        needle   = "This is a well-formed sentence, from the document!"
        page_txt = "This is a wellformed sentence from the document more text here yes indeed"
        result = exact_match_search(needle, page_txt, {0: page_txt}, _BLOCKS)
        assert result is not None
        assert result["verification_status"] == "exact_match"


class TestExactMatchSearchMiss:
    """Both-pass miss → returns None."""

    def test_both_passes_fail_returns_none(self):
        """Needle completely absent from haystack → None."""
        needle   = "completely different sentence not found anywhere in this text"
        haystack = "entirely unrelated content about something else entirely different yes"
        result = exact_match_search(needle, haystack, {0: haystack}, _BLOCKS)
        assert result is None


class TestExactMatchSearchShortNeedle:
    """Pre-check: short needle (< 10 chars after normalise_ws) → None."""

    def test_short_needle_returns_none(self):
        """Needle shorter than 10 chars after normalise_ws → None immediately."""
        result = exact_match_search("short", _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is None

    def test_exactly_nine_chars_returns_none(self):
        """9-char needle → None (boundary check)."""
        result = exact_match_search("123456789", _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is None

    def test_exactly_ten_chars_proceeds(self):
        """10-char needle that IS present should NOT return None due to pre-check."""
        # "1234567890" is 10 chars; embed it in haystack
        needle   = "1234567890"
        haystack = "prefix text 1234567890 suffix text here yes"
        result = exact_match_search(needle, haystack, {0: haystack}, _BLOCKS)
        # 10 chars passes pre-check; should find it (or at minimum not be None due to pre-check)
        assert result is not None


class TestExactMatchSearchPageAttribution:
    """Page index in result must point to the page that contains the needle."""

    def test_correct_page_index_returned(self):
        """Result page_index matches the page that contains the needle."""
        page_texts = {
            0: "Irrelevant page zero content without the needle sentence here.",
            1: "This is the target sentence used for page attribution testing.",
        }
        needle = "This is the target sentence used for page attribution testing."
        full   = " ".join(page_texts.values())
        result = exact_match_search(needle, full, page_texts, _BLOCKS)
        assert result is not None
        assert result["page_index"] == 1


class TestExactMatchSearchPrefixSuffix:
    """Prefix and suffix in result must be ≤ 64 characters each."""

    def test_prefix_length_at_most_64(self):
        """prefix length ≤ 64."""
        result = exact_match_search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        assert len(result["prefix"]) <= 64

    def test_suffix_length_at_most_64(self):
        """suffix length ≤ 64."""
        result = exact_match_search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        assert len(result["suffix"]) <= 64


class TestExactMatchSearchResultKeys:
    """Result dict must contain all required keys."""

    REQUIRED_KEYS = {
        "verification_status",
        "confidence",
        "found_sentence",
        "page_index",
        "prefix",
        "suffix",
        "block_bbox",
        "span_bboxes",
    }

    def test_all_required_keys_present(self):
        """On a hit, result contains exactly the required keys (at minimum)."""
        result = exact_match_search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        assert self.REQUIRED_KEYS.issubset(result.keys())

    def test_confidence_is_1_0(self):
        """confidence must be 1.0 for an exact match."""
        result = exact_match_search(_NEEDLE_LONG, _FULL_TEXT, _PAGE_TEXTS, _BLOCKS)
        assert result is not None
        assert result["confidence"] == 1.0


# ---------------------------------------------------------------------------
# Property-based tests for exact_match_search
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = frozenset({
    "verification_status",
    "confidence",
    "found_sentence",
    "page_index",
    "prefix",
    "suffix",
    "block_bbox",
    "span_bboxes",
})

# Property 3: haystack ≥30 chars with letters/numbers/spaces, needle ≥10 chars embedded in it
@settings(max_examples=100)
@given(
    base=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters=" "),
        min_size=30,
    ),
    needle_start=st.integers(min_value=0),
    needle_len=st.integers(min_value=10, max_value=30),
)
def test_exact_match_search_property_embedded_needle(
    base: str, needle_start: int, needle_len: int
):
    """
    Property 3: for haystack ≥30 with letters/numbers/spaces and a substring
    needle of length ≥10 embedded in it, exact_match_search returns non-None
    with all required keys and verification_status == 'exact_match'.
    """
    if len(base) < 30:
        return  # skip degenerate cases
    # Clamp indices so needle is a valid substring of base
    start = needle_start % max(1, len(base) - needle_len)
    end   = start + min(needle_len, len(base) - start)
    needle = base[start:end]
    # Ensure needle is long enough after normalise_ws
    if len(normalise_ws(needle)) < 10:
        return  # skip
    result = exact_match_search(needle, base, {0: base}, [])
    assert result is not None
    assert _REQUIRED_KEYS.issubset(result.keys())
    assert result["verification_status"] == "exact_match"


# Property 4: string with len < 10 after normalise_ws → always None
@settings(max_examples=100)
@given(st.text(max_size=9))
def test_exact_match_search_property_short_needle_always_none(short: str):
    """
    Property 4: any string whose normalise_ws result has length < 10 must
    cause exact_match_search to return None.
    """
    # Only test when normalised length is truly < 10
    if len(normalise_ws(short)) >= 10:
        return  # skip
    result = exact_match_search(short, short * 5, {0: short * 5}, [])
    assert result is None


# ---------------------------------------------------------------------------
# semantic_search
# ---------------------------------------------------------------------------

def _make_store(similarity=0.9, sentences=None):
    """Build a minimal sentence_store for semantic_search tests."""
    sentences = sentences if sentences is not None else ["The quick brown fox jumps over the lazy dog."]
    pages = [0] * len(sentences)
    return {
        'faiss_index': MockFaissIndex(similarity) if sentences else None,
        'sentences': sentences,
        'pages': pages,
        'block_bboxes': [(10, 20, 200, 40)] * len(sentences),
        'span_bboxes': [[{'text': s, 'bbox': (10, 20, 200, 40)}] for s in sentences],
    }


def _mock_embed(text):
    """Mock embed_query_fn: returns a (1, 4) numpy float32 array."""
    return np.array([[0.5, 0.5, 0.5, 0.5]], dtype=np.float32)


class TestSemanticSearchGuards:
    """Guard conditions: return None before any embedding."""

    def test_none_index_returns_none(self):
        """Guard 1: faiss_index=None → returns None."""
        store = _make_store()
        store['faiss_index'] = None
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is None

    def test_empty_sentences_returns_none(self):
        """Guard 2: sentences=[] → returns None."""
        store = {
            'faiss_index': MockFaissIndex(0.9),
            'sentences': [],
            'pages': [],
        }
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is None


class TestSemanticSearchNearMatch:
    """similarity >= threshold → near_match result."""

    def test_near_match_status(self):
        """When similarity >= threshold, verification_status == 'near_match'."""
        store = _make_store(similarity=0.95)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['verification_status'] == 'near_match'

    def test_near_match_confidence(self):
        """confidence equals the similarity returned by the mock index."""
        store = _make_store(similarity=0.88)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert abs(result['confidence'] - 0.88) < 1e-6

    def test_near_match_found_sentence_non_empty(self):
        """found_sentence is non-empty on near_match."""
        store = _make_store(similarity=0.9)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['found_sentence'] != ''

    def test_near_match_page_index_not_none(self):
        """page_index is not None on near_match."""
        store = _make_store(similarity=0.9)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['page_index'] is not None

    def test_near_match_all_required_keys(self):
        """near_match result contains all required keys."""
        store = _make_store(similarity=0.9)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        required = {'verification_status', 'confidence', 'found_sentence',
                    'page_index', 'prefix', 'suffix', 'block_bbox', 'span_bboxes'}
        assert result is not None
        assert required.issubset(result.keys())

    def test_near_match_prefix_suffix_populated_with_page_texts(self):
        """prefix/suffix are extracted from page_texts when available."""
        store = _make_store(similarity=0.9)
        sentence = store['sentences'][0]
        page_texts = {0: f"Context before. {sentence} And some text after."}
        result = semantic_search("some sentence", store, _mock_embed, 0.7, page_texts=page_texts)
        assert result is not None
        assert result['prefix'] != '' or result['suffix'] != ''

    def test_near_match_prefix_suffix_empty_without_page_texts(self):
        """prefix/suffix are empty strings when page_texts=None."""
        store = _make_store(similarity=0.9)
        result = semantic_search("some sentence", store, _mock_embed, 0.7, page_texts=None)
        assert result is not None
        assert result['prefix'] == ''
        assert result['suffix'] == ''


class TestSemanticSearchNotFound:
    """similarity < threshold → not_found result with null/empty defaults."""

    def test_not_found_status(self):
        """When similarity < threshold, verification_status == 'not_found'."""
        store = _make_store(similarity=0.3)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['verification_status'] == 'not_found'

    def test_not_found_confidence(self):
        """confidence equals the low similarity value."""
        store = _make_store(similarity=0.3)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert abs(result['confidence'] - 0.3) < 1e-6

    def test_not_found_found_sentence_empty(self):
        """found_sentence == '' on not_found."""
        store = _make_store(similarity=0.3)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['found_sentence'] == ''

    def test_not_found_page_index_none(self):
        """page_index is None on not_found."""
        store = _make_store(similarity=0.3)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['page_index'] is None

    def test_not_found_prefix_suffix_empty(self):
        """prefix and suffix are '' on not_found."""
        store = _make_store(similarity=0.3)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['prefix'] == ''
        assert result['suffix'] == ''

    def test_not_found_block_bbox_none(self):
        """block_bbox is None on not_found."""
        store = _make_store(similarity=0.3)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['block_bbox'] is None

    def test_not_found_span_bboxes_none(self):
        """span_bboxes is None on not_found."""
        store = _make_store(similarity=0.3)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        assert result is not None
        assert result['span_bboxes'] is None

    def test_not_found_all_required_keys(self):
        """not_found result also contains all required keys."""
        store = _make_store(similarity=0.3)
        result = semantic_search("some sentence", store, _mock_embed, 0.7)
        required = {'verification_status', 'confidence', 'found_sentence',
                    'page_index', 'prefix', 'suffix', 'block_bbox', 'span_bboxes'}
        assert result is not None
        assert required.issubset(result.keys())


# ---------------------------------------------------------------------------
# Property 5: guard conditions always return None (≥100 examples)
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(st.text())
def test_semantic_search_property_none_index_always_returns_none(sentence: str):
    """
    Property 5a: for any exact_sentence, faiss_index=None always returns None.
    """
    store = {
        'faiss_index': None,
        'sentences': ['some sentence'],
        'pages': [0],
    }
    result = semantic_search(sentence, store, _mock_embed, 0.7)
    assert result is None


@settings(max_examples=100)
@given(st.text())
def test_semantic_search_property_empty_sentences_always_returns_none(sentence: str):
    """
    Property 5b: for any exact_sentence, sentences=[] always returns None.
    """
    store = {
        'faiss_index': MockFaissIndex(0.9),
        'sentences': [],
        'pages': [],
    }
    result = semantic_search(sentence, store, _mock_embed, 0.7)
    assert result is None
