"""Matcher subclasses for the text_processing package.

Provides :class:`LexicalMatcher` (two-pass exact string match) and
:class:`SemanticMatcher` (FAISS-based semantic search).
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from text_processing.base import TextProcessor
from text_processing.normalizers import WhitespaceNormalizer, FullNormalizer

logger = logging.getLogger("pdf_extractor")


# ---------------------------------------------------------------------------
# LexicalMatcher
# ---------------------------------------------------------------------------

class LexicalMatcher(TextProcessor):
    """Two-pass exact string match against full PDF text.

    Pass 1 uses :class:`WhitespaceNormalizer` (score 1.0).
    Pass 2 uses :class:`FullNormalizer` (score 0.9), only when Pass 1 fails.

    Returns ``None`` when both passes fail or pre-checks fail.
    """

    def __init__(self) -> None:
        self._ws_normalizer = WhitespaceNormalizer()
        self._full_normalizer = FullNormalizer()

    def search(
        self,
        needle: str,
        full_text: str,
        page_texts: dict,
        blocks: list,
    ) -> dict | None:
        """Two-pass exact string match.

        Parameters
        ----------
        needle : str
            The sentence to search for.
        full_text : str
            Concatenated text of the entire PDF.
        page_texts : dict
            Mapping of ``{page_index: page_text}``.
        blocks : list
            Enriched extraction blocks with text/page/bbox/span metadata.

        Returns
        -------
        dict or None
            Result dict with keys: ``found_sentence``, ``page_index``,
            ``prefix``, ``suffix``, ``block_bbox``, ``span_bboxes``, ``score``.
        """
        # Pre-check: skip very short needles
        if len(self._ws_normalizer.normalize(needle)) < 10:
            return None

        # Empty guards
        if not full_text:
            return None
        if not page_texts:
            return None

        # Pass 1: whitespace normalisation
        needle_ws = self._ws_normalizer.normalize(needle)
        haystack_ws = self._ws_normalizer.normalize(full_text)
        pass1_hit = needle_ws in haystack_ws

        # Pass 2: full normalisation (only if Pass 1 fails)
        use_full_norm = False
        if not pass1_hit:
            needle_full = self._full_normalizer.normalize(needle)
            haystack_full = self._full_normalizer.normalize(full_text)
            if needle_full in haystack_full:
                use_full_norm = True
            else:
                return None  # both passes failed

        # Determine the active normaliser for page attribution
        if use_full_norm:
            normalise = self._full_normalizer.normalize
            active_needle = self._full_normalizer.normalize(needle)
            score = 0.9
        else:
            normalise = self._ws_normalizer.normalize
            active_needle = self._ws_normalizer.normalize(needle)
            score = 1.0

        # Find the page whose normalised text contains the needle
        matched_page_index: int | None = None
        for page_index, page_text in page_texts.items():
            if active_needle in normalise(page_text):
                matched_page_index = page_index
                break

        if matched_page_index is None:
            # Cross-page: find page with longest common substring overlap
            best_page = None
            best_overlap = 0
            for page_index, page_text in page_texts.items():
                m = SequenceMatcher(None, active_needle, normalise(page_text))
                overlap = m.find_longest_match(
                    0, len(active_needle), 0, len(normalise(page_text))
                ).size
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_page = page_index
            if best_page is None:
                return None
            logger.debug(
                "Cross-page sentence detected; attributing to page %s (overlap=%s chars).",
                best_page,
                best_overlap,
            )
            matched_page_index = best_page

        page_text = page_texts[matched_page_index]

        # Recover original span via SequenceMatcher
        matcher = SequenceMatcher(None, needle.lower(), page_text.lower())
        match = matcher.find_longest_match(0, len(needle), 0, len(page_text))
        found_sentence = page_text[match.b: match.b + match.size]

        # Extract 64-char prefix/suffix from original page text
        start = match.b
        end = match.b + match.size
        prefix = page_text[max(0, start - 64): start]
        suffix = page_text[end: end + 64]

        # Block/span bounding-box attribution
        matched_block_bbox = None
        matched_span_bboxes = None
        sentence_lower = found_sentence.lower().strip()
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("page_index") != matched_page_index:
                continue
            block_text = str(block.get("text", ""))
            if sentence_lower and sentence_lower in block_text.lower():
                matched_block_bbox = block.get("block_bbox")
                spans = block.get("spans")
                if isinstance(spans, list):
                    overlapping = []
                    for span in spans:
                        if not isinstance(span, dict):
                            continue
                        span_text = str(span.get("text", ""))
                        span_lower = span_text.lower().strip()
                        if (
                            sentence_lower
                            and span_lower
                            and (span_lower in sentence_lower or sentence_lower in span_lower)
                        ):
                            overlapping.append({
                                "text": span_text,
                                "bbox": span.get("bbox"),
                            })
                    if overlapping:
                        matched_span_bboxes = overlapping
                    else:
                        matched_span_bboxes = [
                            {"text": str(span.get("text", "")), "bbox": span.get("bbox")}
                            for span in spans
                            if isinstance(span, dict)
                        ]
                break

        return {
            "found_sentence": found_sentence,
            "page_index": matched_page_index,
            "prefix": prefix,
            "suffix": suffix,
            "block_bbox": matched_block_bbox,
            "span_bboxes": matched_span_bboxes,
            "score": score,
        }

    # -- Unrelated abstract methods --

    def normalize(self, text: str) -> str:
        raise NotImplementedError("LexicalMatcher does not implement normalize().")

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError("LexicalMatcher does not implement tokenize_words().")

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError("LexicalMatcher does not implement tokenize_sentences().")

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError("LexicalMatcher does not implement clean_ocr().")

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError("LexicalMatcher does not implement compare().")

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError("LexicalMatcher does not implement extract_keywords().")


# ---------------------------------------------------------------------------
# SemanticMatcher
# ---------------------------------------------------------------------------

class SemanticMatcher(TextProcessor):
    """FAISS-based semantic search over a pre-built sentence store.

    Uses caller-provided ``embed_fn`` and pre-built sentence store
    to find the semantically closest sentence.
    """

    def search(
        self,
        query: str,
        sentence_store: dict,
        embed_fn: "callable",
        threshold: float,
        page_texts: dict | None = None,
    ) -> dict | None:
        """Semantic similarity search.

        Parameters
        ----------
        query : str
            The query sentence to look up.
        sentence_store : dict
            Must contain ``'faiss_index'``, ``'sentences'``, ``'pages'``,
            optionally ``'block_bboxes'`` and ``'span_bboxes'``.
        embed_fn : callable
            Callable ``(str) -> np.ndarray`` returning shape ``(1, D)``.
        threshold : float
            Minimum cosine similarity to report a match.
        page_texts : dict or None
            Optional ``{page_index: page_text}`` for prefix/suffix extraction.

        Returns
        -------
        dict or None
            Result dict or ``None`` when guards fail.
        """
        # Guard 1: index unavailable
        if sentence_store.get("faiss_index") is None:
            return None
        # Guard 2: sentences empty or missing
        if not sentence_store.get("sentences"):
            return None

        # Embed the query
        query_emb = embed_fn(query)  # shape (1, D)

        # FAISS top-1 inner-product search
        distances, indices = sentence_store["faiss_index"].search(query_emb, 1)
        similarity = float(distances[0][0])
        best_idx = int(indices[0][0])
        best_sentence = sentence_store["sentences"][best_idx]
        best_page = sentence_store["pages"][best_idx]

        block_bbox = None
        span_bboxes = None
        if sentence_store.get("block_bboxes") and best_idx < len(sentence_store["block_bboxes"]):
            block_bbox = sentence_store["block_bboxes"][best_idx]
        if sentence_store.get("span_bboxes") and best_idx < len(sentence_store["span_bboxes"]):
            span_bboxes = sentence_store["span_bboxes"][best_idx]

        # Extract prefix/suffix from page text (up to 64 chars each side)
        prefix = ""
        suffix = ""
        if page_texts and best_page in page_texts:
            page_text = page_texts[best_page]
            sent_start = page_text.find(best_sentence)
            if sent_start != -1:
                sent_end = sent_start + len(best_sentence)
                prefix = page_text[max(0, sent_start - 64): sent_start]
                suffix = page_text[sent_end: sent_end + 64]

        # Guard: score < threshold
        if similarity < threshold:
            return None

        return {
            "found_sentence": best_sentence,
            "page_index": best_page,
            "prefix": prefix,
            "suffix": suffix,
            "block_bbox": block_bbox,
            "span_bboxes": span_bboxes,
            "score": similarity,
        }

    # -- Unrelated abstract methods --

    def normalize(self, text: str) -> str:
        raise NotImplementedError("SemanticMatcher does not implement normalize().")

    def tokenize_words(self, text: str) -> list[str]:
        raise NotImplementedError("SemanticMatcher does not implement tokenize_words().")

    def tokenize_sentences(self, text: str) -> list[str]:
        raise NotImplementedError("SemanticMatcher does not implement tokenize_sentences().")

    def clean_ocr(self, text: str) -> str:
        raise NotImplementedError("SemanticMatcher does not implement clean_ocr().")

    def compare(self, a: str, b: str) -> float:
        raise NotImplementedError("SemanticMatcher does not implement compare().")

    def extract_keywords(self, text: str) -> list[str]:
        raise NotImplementedError("SemanticMatcher does not implement extract_keywords().")
