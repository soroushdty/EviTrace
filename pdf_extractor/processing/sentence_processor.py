"""
sentence_processor.py
---------------------
Sentence-level text processing module.

Responsibilities:
    - Text normalisation         (normalise_text)
    - Noise / metadata filtering (is_noise)
    - Sentence segmentation      (process_sentences)
    - Full-text assembly         (build_full_text)

No code executes at import time.
"""

import re


# ---------------------------------------------------------------------------
# 1. normalise_text
# ---------------------------------------------------------------------------

def normalise_text(text: str) -> str:
    """Normalise a raw text block for downstream sentence segmentation.

    Operations applied (in order):
        1. Merge broken lines: a single newline that is NOT followed by an
           uppercase letter (A-Z) or a bullet character (-, *, •, ·) is
           replaced with a space, so mid-sentence line-breaks are healed.
        2. Collapse runs of two or more newlines into a single newline.
        3. Collapse runs of two or more spaces into a single space.
        4. Strip leading and trailing whitespace.

    Parameters
    ----------
    text : str
        Raw text block, potentially containing line breaks and extra spaces.

    Returns
    -------
    str
        The normalised string.
    """
    # Step 1 – heal mid-sentence line breaks.
    # A single '\n' NOT followed by an uppercase letter or a bullet character
    # is treated as a soft wrap; replace it with a space.
    text = re.sub(r'\n(?![A-Z\-\*•·])', ' ', text)  # FIX 9: \d excluded from lookahead intentionally — merges "Table\n1…" correctly

    # Step 2 – collapse multiple consecutive newlines into one.
    text = re.sub(r'\n{2,}', '\n', text)

    # Step 3 – collapse multiple spaces into one.
    text = re.sub(r' {2,}', ' ', text)

    # Step 4 – strip leading / trailing whitespace.
    return text.strip()


# ---------------------------------------------------------------------------
# 2. is_noise
# ---------------------------------------------------------------------------

# Pre-compiled patterns used by is_noise — compiled once at definition time
# (not at import time of any downstream module; this module is already being
# imported, so the compilation happens exactly once when the module loads,
# which is the intended and efficient behaviour).

_RE_REFERENCE_BRACKET = re.compile(r'^\s*\[\d+\]')          # [1], [23] …
_RE_REFERENCE_NUMBERED = re.compile(r'^\s*\d+\.\s')         # 1. Author …
_RE_DOI = re.compile(
    r'(?:doi:|https?://doi\.org|10\.\d{4,}/)',
    re.IGNORECASE,
)
_RE_EMAIL = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')
_RE_URL = re.compile(r'https?://', re.IGNORECASE)
_RE_ORCID_DOMAIN = re.compile(r'orcid\.org', re.IGNORECASE)
_RE_ORCID_ID = re.compile(r'\d{4}-\d{4}-\d{4}-\d{4}')

# Comma-separated capitalised tokens followed by digits — typical author /
# affiliation lines, e.g. "Smith J, Jones A 1,2, Brown K 3"
_RE_AUTHOR_LINE = re.compile(
    r'^(?:[A-Z][A-Za-z\-]+(?:\s[A-Z]\.?)?,\s*){2,}.*\d'
)

# Lines that consist almost entirely of digits, punctuation, and whitespace
# (page headers / footers, e.g. "— 42 —", "| 3 |", "12  34  56").
_RE_MOSTLY_NONALPHA = re.compile(r'^[\d\s\W]+$')


def is_noise(sentence: str) -> bool:
    """Return True if *sentence* should be discarded from the pipeline.

    A sentence is considered noise if it matches any of the following
    heuristics:

    * **Reference / bibliography lines** – starts with ``[N]`` or ``N. ``,
      or contains a DOI pattern (``doi:``, ``https://doi.org``,
      ``10.<digits>/``).
    * **Email addresses** – contains a syntactically valid e-mail address.
    * **URLs** – contains ``http://`` or ``https://`` (DOI URLs are already
      caught by the DOI rule above, but this catches all others).
    * **ORCID identifiers** – contains ``orcid.org`` or the 16-digit
      hyphenated pattern ``XXXX-XXXX-XXXX-XXXX``.
    * **Author / metadata lines** – matches a pattern of three or more
      comma-separated capitalised names followed by digits, OR consists
      almost entirely of non-alphabetic characters (digits, punctuation,
      whitespace).
    * **Pure metadata** – the string is composed solely of digits,
      punctuation, and/or whitespace.

    Parameters
    ----------
    sentence : str
        A single sentence candidate (already stripped of surrounding
        whitespace).

    Returns
    -------
    bool
        ``True`` if the sentence is noise and should be discarded,
        ``False`` otherwise.
    """
    if _RE_REFERENCE_BRACKET.search(sentence):
        return True

    if _RE_REFERENCE_NUMBERED.match(sentence):
        return True

    if _RE_DOI.search(sentence):
        return True

    if _RE_EMAIL.search(sentence):
        return True

    # URL check – _RE_URL also matches https://doi.org, but the DOI rule
    # already fired above; keeping both rules independent is intentional so
    # that either rule alone is sufficient.
    if _RE_URL.search(sentence):
        return True

    if _RE_ORCID_DOMAIN.search(sentence):
        return True

    if _RE_ORCID_ID.search(sentence):
        return True

    if _RE_AUTHOR_LINE.match(sentence):
        return True

    if _RE_MOSTLY_NONALPHA.match(sentence):
        return True

    # FIX 8: catch numbered section headers (e.g. "3.2 Study design", "10.1 …")
    if re.match(r'^\d+(\.\d+)+\s', sentence.strip()):
        return True

    return False


# ---------------------------------------------------------------------------
# 3. process_sentences
# ---------------------------------------------------------------------------

# Sentence boundary: after a sentence-ending punctuation mark followed by
# whitespace and an uppercase letter.  This avoids splitting on abbreviations
# like "Fig. 3" or "e.g. something" because they are typically not followed
# by an uppercase letter after exactly one whitespace character.
_RE_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')


def process_sentences(text_blocks_with_pages: list, len_filter: int) -> list:
    """Segment, filter, and normalise text blocks into sentence-level records.

    Takes the raw output of ``evi_trace.extraction.extract_pdf`` (a list of
    enriched block dicts) and returns sentence records with page and
    anchor metadata suitable for embedding and hallucination verification.

    Processing pipeline for each block dict:

    1. Apply :func:`normalise_text` to the block.
    2. Split the normalised block into sentence candidates using the regex
       ``(?<=[.!?])\\s+(?=[A-Z])``.
    3. For every candidate sentence:

       a. Strip surrounding whitespace.
       b. Discard if ``len(sentence) < len_filter``.
       c. Discard if :func:`is_noise` returns ``True``.
       d. Otherwise append a dict with sentence/page/bbox metadata.

    The ``page_index`` carried by each surviving sentence is the page index
    of the text block it originated from — this is required by
    ``evi_trace/utils/embedding_utils`` and ``evi_trace/utils/text_utils``.

    Parameters
    ----------
    text_blocks_with_pages : list[dict]
        Raw block dicts as returned by
        ``evi_trace.extraction.extract_pdf``.
    len_filter : int
        Minimum character length a sentence must have to survive filtering.
        Sentences strictly shorter than this value are discarded.

    Returns
    -------
    list[dict]
        Filtered sentence records with keys:
        ``sentence``, ``page_index``, ``block_bbox``, ``span_bboxes``.
    """
    results = []

    for block in text_blocks_with_pages:
        text_block = block.get("text", "") if isinstance(block, dict) else ""
        page_index = block.get("page_index") if isinstance(block, dict) else None
        block_bbox = block.get("block_bbox") if isinstance(block, dict) else None
        spans = block.get("spans") if isinstance(block, dict) else None
        span_bboxes = None
        if isinstance(spans, list):
            span_bboxes = [
                {"text": str(span.get("text", "")), "bbox": span.get("bbox")}
                for span in spans
                if isinstance(span, dict)
            ]

        if page_index is None:
            continue

        normalised = normalise_text(text_block)

        candidates = _RE_SENTENCE_SPLIT.split(normalised)

        for candidate in candidates:
            sentence = candidate.strip()

            if len(sentence) < len_filter:
                continue

            if is_noise(sentence):
                continue

            results.append(
                {
                    "sentence": sentence,
                    "page_index": page_index,
                    "block_bbox": block_bbox,
                    "span_bboxes": span_bboxes,
                }
            )

    return results


# ---------------------------------------------------------------------------
# 4. build_full_text
# ---------------------------------------------------------------------------

def build_full_text(text_blocks_with_pages: list) -> tuple:
    """Assemble raw text blocks into document-level and page-level structures.

    Produces the two data structures consumed by the two-pass exact-string-
    match location logic in ``evi_trace/utils/text_utils``:

    * ``full_pdf_text`` – a single string formed by joining all text blocks
      with a single space.  Used as the haystack for whole-document
      substring searches.
    * ``page_texts`` – a ``dict`` mapping each ``page_index`` (int) to the
      concatenation of all text blocks that belong to that page, joined with
      a single space.  Used to recover the 64-character prefix/suffix context
      around a match.

    .. important::
        No normalisation is applied to the text blocks.  The location logic
        in ``evi_trace/utils/text_utils`` operates on the original extracted text so
        that it can recover the exact surrounding context of a matched sentence.

    Parameters
    ----------
    text_blocks_with_pages : list[dict]
        Raw block dicts as returned by
        ``evi_trace.extraction.extract_pdf``.

    Returns
    -------
    tuple of (str, dict)
        ``(full_pdf_text, page_texts)`` where

        * ``full_pdf_text`` is a ``str`` — all blocks joined with spaces.
        * ``page_texts`` is a ``dict[int, str]`` — per-page concatenated text.
    """
    all_blocks = []
    page_blocks: dict = {}

    for block in text_blocks_with_pages:
        text_block = block.get("text", "") if isinstance(block, dict) else ""
        page_index = block.get("page_index") if isinstance(block, dict) else None
        if page_index is None:
            continue

        all_blocks.append(text_block)

        if page_index not in page_blocks:
            page_blocks[page_index] = []
        page_blocks[page_index].append(text_block)

    full_pdf_text = ' '.join(all_blocks)

    page_texts = {
        page_index: ' '.join(blocks)
        for page_index, blocks in page_blocks.items()
    }

    return full_pdf_text, page_texts
