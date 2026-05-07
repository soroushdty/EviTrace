"""
pdf_extractor/utils/text_utils.py
===================
Text normalisation helpers for QC text comparison.

These functions are DISTINCT from :func:`sentence_processor.normalise_text`,
which heals line breaks for segmentation purposes.  The functions here are
used for Quality Control text comparison: stripping noise before comparing
extracted strings with expected strings.

Only the standard library modules are imported — this module deliberately
has no dependency on numpy, pandas, faiss, torch, or sentence_transformers.

Functions
---------
normalise_ws(text)
    Collapse all whitespace runs to a single space and lowercase.
    Pass 1 normalisation; punctuation is preserved.

normalise_full(text)
    Apply ``normalise_ws`` then strip every character that is not a word
    character (``\\w``) or whitespace.  Pass 2 normalisation; punctuation
    is removed.

exact_match_search(exact_sentence, full_pdf_text, page_texts, blocks)
    Two-pass exact string match against the full PDF text.
    Pass 1 uses whitespace normalisation; Pass 2 uses full normalisation.

semantic_search(exact_sentence, sentence_store, embed_query_fn, semantic_match_threshold, page_texts)
    Semantic fallback using a pre-built FAISS index of sentence embeddings.
    Returns a 'near_match' or 'not_found' dict, or None when the index is
    unavailable.

Both normalisation functions are idempotent and empty-safe:

    normalise_ws(normalise_ws(s)) == normalise_ws(s)   for all s
    normalise_full(normalise_full(s)) == normalise_full(s)   for all s
    normalise_ws('') == ''
    normalise_full('') == ''
"""

import re
import logging
from difflib import SequenceMatcher

logger = logging.getLogger("pdf_extractor")

# ---------------------------------------------------------------------------
# 1. WHITESPACE NORMALISATION  (Pass 1)
# ---------------------------------------------------------------------------

def normalise_ws(text: str) -> str:
    """
    Whitespace-only normalisation (Pass 1).

    Collapses all runs of whitespace to a single space and lowercases the
    string.  Punctuation is intentionally preserved.

    Distinct from :func:`sentence_processor.normalise_text`, which heals
    broken lines for segmentation.  This function is for QC text comparison.

    Parameters
    ----------
    text : str
        Raw input string.

    Returns
    -------
    str
        Lowercased, whitespace-collapsed string.  Returns ``''`` when given
        an empty string.

    Examples
    --------
    >>> normalise_ws("  Hello   World  ")
    'hello world'
    >>> normalise_ws("UPPER CASE")
    'upper case'
    >>> normalise_ws("hello, world!")
    'hello, world!'
    >>> normalise_ws("")
    ''
    """
    return re.sub(r'\s+', ' ', text.lower()).strip()


# ---------------------------------------------------------------------------
# 2. FULL NORMALISATION  (Pass 2)
# ---------------------------------------------------------------------------

def normalise_full(text: str) -> str:
    """
    Whitespace AND punctuation normalisation (Pass 2).

    Applies :func:`normalise_ws` first (collapse whitespace, lowercase), then
    strips every character that is not a word character (``\\w``) or
    whitespace.

    Distinct from :func:`sentence_processor.normalise_text`, which heals
    broken lines for segmentation.  This function is for QC text comparison.

    Parameters
    ----------
    text : str
        Raw input string.

    Returns
    -------
    str
        Lowercased string with whitespace collapsed and non-word, non-space
        characters removed.  Returns ``''`` when given an empty string.

    Examples
    --------
    >>> normalise_full("hello, world!")
    'hello world'
    >>> normalise_full("  UPPER-CASE: test.  ")
    'uppercase test'
    >>> normalise_full("")
    ''
    """
    text = re.sub(r'\s+', ' ', text.lower()).strip()
    text = re.sub(r'[^\w\s]', '', text)
    # Re-collapse whitespace and strip: removing punctuation can leave runs of
    # spaces (e.g. "hello . world" → "hello  world") or trailing spaces
    # (e.g. "0 §" → "0 ").  A second pass ensures idempotency.
    return re.sub(r'\s+', ' ', text).strip()


# ---------------------------------------------------------------------------
# 3. EXACT MATCH SEARCH  (two-pass)
# ---------------------------------------------------------------------------

def exact_match_search(
    exact_sentence: str,
    full_pdf_text: str,
    page_texts: dict,
    blocks: list,
) -> dict | None:
    """
    Two-pass exact string match against the full PDF text.

    The search is NEVER restricted to a claimed source location.

    Pass 1 — whitespace normalisation only (:func:`normalise_ws`).
    Pass 2 — whitespace + punctuation normalisation (:func:`normalise_full`),
              only attempted when Pass 1 fails.

    If both passes fail, returns ``None``.

    On a successful match, the function locates the containing page, recovers
    the original non-normalised span via ``difflib.SequenceMatcher``, and
    extracts up to 64 characters of surrounding context (prefix / suffix).

    Parameters
    ----------
    exact_sentence : str
        The claimed sentence to look for.
    full_pdf_text : str
        Concatenated text of the entire PDF (used for the substring check).
    page_texts : dict
        Mapping of ``{page_index: page_text}`` used for page attribution and
        span recovery.
    blocks : list
        Enriched extraction blocks with text/page/bbox/span metadata.

    Returns
    -------
    dict or None
        On success::

            {
                'verification_status': 'exact_match',
                'confidence':          1.0,
                'found_sentence':      str,   # recovered from original page text
                'page_index':          int,
                'prefix':              str,   # up to 64 chars before match
                'suffix':              str,   # up to 64 chars after match
                'block_bbox':          tuple | None,
                'span_bboxes':         list[dict] | None
            }

        ``None`` when no match is found or the pre-check fails.
    """
    # Pre-check: skip very short needles
    if len(normalise_ws(exact_sentence)) < 10:
        return None

    # ---- Pass 1: whitespace normalisation --------------------------------
    needle_ws   = normalise_ws(exact_sentence)
    haystack_ws = normalise_ws(full_pdf_text)

    pass1_hit = needle_ws in haystack_ws

    # ---- Pass 2: strip punctuation (only if Pass 1 fails) ----------------
    use_full_norm = False
    if not pass1_hit:
        needle_full   = normalise_full(exact_sentence)
        haystack_full = normalise_full(full_pdf_text)
        if needle_full in haystack_full:
            use_full_norm = True
        else:
            return None  # both passes failed

    # ---- Determine the active normaliser for page attribution ------------
    if use_full_norm:
        normalise = normalise_full
        needle    = normalise_full(exact_sentence)
    else:
        normalise = normalise_ws
        needle    = normalise_ws(exact_sentence)

    # ---- Find the page whose normalised text contains the needle ---------
    matched_page_index: int | None = None
    for page_index, page_text in page_texts.items():
        if needle in normalise(page_text):
            matched_page_index = page_index
            break

    if matched_page_index is None:
        # Sentence spans a page boundary — find the page with the longest
        # common substring overlap and use it for span recovery.
        best_page = None
        best_overlap = 0
        for page_index, page_text in page_texts.items():
            m = SequenceMatcher(None, needle, normalise(page_text))
            overlap = m.find_longest_match(0, len(needle), 0, len(normalise(page_text))).size
            if overlap > best_overlap:
                best_overlap = overlap
                best_page = page_index
        if best_page is None:
            return None
        logger.info(
            "[pdf_extractor] Cross-page sentence detected; attributing to page %s "
            "(overlap=%s chars). Returning exact_match.",
            best_page,
            best_overlap,
        )
        matched_page_index = best_page

    page_text = page_texts[matched_page_index]

    # ---- Recover original span via SequenceMatcher -----------------------
    matcher = SequenceMatcher(None, exact_sentence.lower(), page_text.lower())
    match   = matcher.find_longest_match(0, len(exact_sentence), 0, len(page_text))
    found_sentence = page_text[match.b: match.b + match.size]

    # ---- Extract 64-char prefix / suffix from original page text ---------
    start  = match.b
    end    = match.b + match.size
    prefix = page_text[max(0, start - 64): start]
    suffix = page_text[end: end + 64]

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
        'verification_status': 'exact_match',
        'confidence':          1.0,
        'found_sentence':      found_sentence,
        'page_index':          matched_page_index,
        'prefix':              prefix,
        'suffix':              suffix,
        'block_bbox':          matched_block_bbox,
        'span_bboxes':         matched_span_bboxes,
    }


# ---------------------------------------------------------------------------
# 4. SEMANTIC SEARCH  (FAISS fallback)
# ---------------------------------------------------------------------------

def semantic_search(
    exact_sentence: str,
    sentence_store: dict,
    embed_query_fn: callable,
    semantic_match_threshold: float,
    page_texts: dict | None = None,
) -> dict | None:
    """
    Semantic fallback using a pre-built FAISS index of sentence embeddings.

    Embeds ``exact_sentence`` with ``embed_query_fn``, performs a top-1
    inner-product search (vectors are assumed to be L2-normalised, so inner
    product equals cosine similarity), and compares the similarity against
    ``semantic_match_threshold``.

    No imports of faiss, torch, or sentence_transformers are used here;
    the caller provides the index and the embed function.

    Parameters
    ----------
    exact_sentence : str
        The claimed sentence to look up.
    sentence_store : dict
        Must contain:
          - ``'faiss_index'``: a FAISS index object (e.g. ``IndexFlatIP``),
            or ``None``.
          - ``'sentences'``: list of sentence strings.
          - ``'pages'``: list of page indices parallel to ``'sentences'``.
          - ``'block_bboxes'`` (optional): list of block bounding boxes.
          - ``'span_bboxes'`` (optional): list of span bbox lists.
    embed_query_fn : callable
        Callable that accepts a string and returns a numpy array of shape
        ``(1, D)`` containing the L2-normalised embedding.
    semantic_match_threshold : float
        Minimum cosine similarity required to report a ``'near_match'``.
    page_texts : dict or None, optional
        Mapping of ``{page_index: page_text}`` used to extract prefix/suffix
        context.  If ``None`` or the page is not present, empty strings are
        used for prefix and suffix.

    Returns
    -------
    dict or None
        ``None`` when the index is unavailable or the sentence store is empty.

        Otherwise a dict with keys:
          - ``'verification_status'``: ``'near_match'`` or ``'not_found'``
          - ``'confidence'``: float (cosine similarity)
          - ``'found_sentence'``: str (``''`` for not_found)
          - ``'page_index'``: int or None (None for not_found)
          - ``'prefix'``: str (``''`` for not_found)
          - ``'suffix'``: str (``''`` for not_found)
          - ``'block_bbox'``: tuple or None (None for not_found)
          - ``'span_bboxes'``: list or None (None for not_found)
    """
    # Guard 1: index unavailable
    if sentence_store.get('faiss_index') is None:
        return None
    # Guard 2: store empty
    if not sentence_store.get('sentences'):
        return None

    # Embed the query
    query_emb = embed_query_fn(exact_sentence)  # shape (1, D)

    # FAISS top-1 search
    distances, indices = sentence_store['faiss_index'].search(query_emb, 1)
    similarity    = float(distances[0][0])
    best_idx      = int(indices[0][0])
    best_sentence = sentence_store['sentences'][best_idx]
    best_page     = sentence_store['pages'][best_idx]

    block_bbox = None
    span_bboxes = None
    if sentence_store.get('block_bboxes') and best_idx < len(sentence_store['block_bboxes']):
        block_bbox = sentence_store['block_bboxes'][best_idx]
    if sentence_store.get('span_bboxes') and best_idx < len(sentence_store['span_bboxes']):
        span_bboxes = sentence_store['span_bboxes'][best_idx]

    # Extract surrounding context from page text (up to 64 chars each side)
    prefix = ''
    suffix = ''
    if page_texts and best_page in page_texts:
        page_text  = page_texts[best_page]
        sent_start = page_text.find(best_sentence)
        if sent_start != -1:
            sent_end = sent_start + len(best_sentence)
            prefix   = page_text[max(0, sent_start - 64): sent_start]
            suffix   = page_text[sent_end: sent_end + 64]

    if similarity >= semantic_match_threshold:
        return {
            'verification_status': 'near_match',
            'confidence':          similarity,
            'found_sentence':      best_sentence,
            'page_index':          best_page,
            'prefix':              prefix,
            'suffix':              suffix,
            'block_bbox':          block_bbox,
            'span_bboxes':         span_bboxes,
        }
    else:
        return {
            'verification_status': 'not_found',
            'confidence':          similarity,
            'found_sentence':      '',
            'page_index':          None,
            'prefix':              '',
            'suffix':              '',
            'block_bbox':          None,
            'span_bboxes':         None,
        }
