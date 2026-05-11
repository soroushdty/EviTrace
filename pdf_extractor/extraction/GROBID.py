"""
pdf_extractor/extraction/core/branch1.py
-------------------------------
GROBID extraction backend.

Calls the GROBID REST API (processFulltextDocument), parses the returned
TEI XML into BlockDict objects, and returns both the raw XML string and
the parsed blocks.

Returns
-------
tuple[str, list[BlockDict]]
    ``(tei_xml_str, blocks)`` where *tei_xml_str* is the raw TEI XML
    (used as ``Candidate.payload`` in the QC pipeline) and *blocks* is
    a ``list[BlockDict]`` extracted from the XML (used for cascade quality
    scoring).

``requests`` is imported lazily inside the function body — no import-time
side effects.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET

from . import schemas

logger = logging.getLogger("pdf_extractor")

_GROBID_ENDPOINT = "/api/processFulltextDocument"
_TEI_NS = "http://www.tei-c.org/ns/1.0"
_NS = f"{{{_TEI_NS}}}"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _call_grobid_api(
    pdf_path: str,
    url: str,
    form_data: dict,
    timeout: int,
    max_retries: int,
) -> str:
    """POST *pdf_path* to the GROBID REST API and return the raw TEI XML string.

    Retries on HTTP 5xx and timeout with exponential backoff (1 s, 2 s).
    Never retries on HTTP 4xx client errors. Emits an INFO-level timing log
    for every attempt so per-request latency can be separated from parsing
    and end-to-end wall time.

    Raises
    ------
    RuntimeError
        On connection failure, timeout, or non-2xx HTTP response after all
        retries are exhausted.
    """
    import requests  # lazy import — no import-time side effect

    endpoint = url.rstrip("/") + _GROBID_ENDPOINT
    pdf_name = pdf_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    with open(pdf_path, "rb") as fh:
        for attempt in range(max_retries + 1):
            fh.seek(0)
            t_start = time.monotonic()
            try:
                response = requests.post(
                    endpoint,
                    files={"input": (pdf_name, fh, "application/pdf")},
                    data=form_data,
                    timeout=timeout,
                )
            except requests.exceptions.ConnectionError as exc:
                raise RuntimeError(
                    f"GROBID server not reachable at {url}: {exc}"
                ) from exc
            except requests.exceptions.Timeout as exc:
                dt = time.monotonic() - t_start
                logger.warning(
                    "GROBID request timed out after %.1fs (limit=%ds) on attempt %d/%d",
                    dt, timeout, attempt + 1, max_retries + 1,
                )
                if attempt < max_retries:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(
                    f"GROBID request timed out after {timeout}s"
                ) from exc

            dt = time.monotonic() - t_start
            logger.info(
                "GROBID request returned in %.1fs (status=%d, attempt=%d/%d, bytes=%d)",
                dt, response.status_code, attempt + 1, max_retries + 1,
                len(response.content or b""),
            )

            if response.status_code >= 500:
                if attempt < max_retries:
                    logger.warning(
                        "GROBID HTTP %d on attempt %d/%d — retrying",
                        response.status_code,
                        attempt + 1,
                        max_retries + 1,
                    )
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(
                    f"GROBID HTTP {response.status_code} after {max_retries} retries"
                )

            if response.status_code >= 400:
                raise RuntimeError(
                    f"GROBID HTTP {response.status_code}: client error"
                    " (check PDF file and request parameters)"
                )

            return response.text

    raise RuntimeError("GROBID: exhausted retries without a response")


def _parse_coords(
    coords_str: str,
) -> tuple[int, tuple[float, float, float, float]] | None:
    """Parse a TEI ``coords`` attribute into a ``(page_index, bbox)`` pair.

    GROBID format: ``"page;x0,y0,x1,y1"`` (1-indexed page).  Multiple
    space-separated segments are allowed for elements that cross page
    boundaries; this function uses the first segment's page and unions the
    bounding boxes of all segments on that page.

    Returns ``None`` on any parse failure.
    """
    if not coords_str or not coords_str.strip():
        return None

    segments = coords_str.strip().split()
    first_page_idx: int | None = None
    x0_min = float("inf")
    y0_min = float("inf")
    x1_max = float("-inf")
    y1_max = float("-inf")

    for seg in segments:
        parts = seg.split(";")
        if len(parts) != 2:
            return None
        page_str, coords_part = parts
        try:
            page_idx = int(page_str) - 1  # 1-indexed → 0-indexed
            nums = [float(v) for v in coords_part.split(",")]
        except ValueError:
            return None
        if len(nums) != 4:
            return None

        if first_page_idx is None:
            first_page_idx = page_idx

        if page_idx == first_page_idx:
            x0, y0, x1, y1 = nums
            x0_min = min(x0_min, x0)
            y0_min = min(y0_min, y0)
            x1_max = max(x1_max, x1)
            y1_max = max(y1_max, y1)

    if first_page_idx is None or x0_min == float("inf"):
        return None

    return first_page_idx, (x0_min, y0_min, x1_max, y1_max)


def _elem_text(elem: ET.Element) -> str:
    """Return all character data under *elem*, stripping XML tags."""
    return "".join(elem.itertext()).strip()


def _ref_text(biblstruct: ET.Element) -> str:
    """Build a human-readable string from a ``<biblStruct>`` element.

    Prefers the raw citation note when ``includeRawCitations`` was enabled;
    falls back to reconstructing author–title–venue–year from structured fields.
    """
    raw_note = biblstruct.find(f".//{_NS}note[@type='raw_reference']")
    if raw_note is not None:
        raw = _elem_text(raw_note)
        if raw:
            return raw

    parts: list[str] = []

    # Authors (surname only for brevity)
    for author in biblstruct.findall(f".//{_NS}author"):
        surname = author.find(f".//{_NS}surname")
        if surname is not None:
            name = _elem_text(surname)
            if name:
                parts.append(name)

    # Title
    for title in biblstruct.findall(f".//{_NS}title"):
        t = _elem_text(title)
        if t:
            parts.append(t)
            break

    # Year
    date = biblstruct.find(f".//{_NS}date[@type='published']")
    if date is not None:
        yr = date.get("when", _elem_text(date))
        if yr:
            parts.append(f"({yr})")

    return " ".join(parts)


def _parse_tei_to_blocks(
    tei_xml_str: str,
    with_coordinates: bool,
) -> list[dict]:
    """Convert a GROBID TEI XML string into a list of ``BlockDict`` objects.

    Processes the tree in document order: title → abstract → body sections
    (headings, paragraphs, figures, formulae, footnotes) → back matter
    (funding, data availability, references).  Each non-empty text unit
    becomes one block; XML tags are stripped via ``_elem_text``.

    Parameters
    ----------
    tei_xml_str:
        Raw TEI XML string from GROBID.
    with_coordinates:
        When ``True``, populate ``block_bbox`` from the ``coords`` attribute;
        when ``False``, set ``block_bbox=None`` on every block.
    """
    root = ET.fromstring(tei_xml_str)
    blocks: list[dict] = []

    def _make(text: str, elem: ET.Element | None, fallback_page: int = 0) -> None:
        text = text.strip()
        if not text:
            return
        page_idx = fallback_page
        bbox: tuple[float, float, float, float] | None = None
        if with_coordinates and elem is not None:
            parsed = _parse_coords(elem.get("coords", ""))
            if parsed is not None:
                page_idx, bbox = parsed
        blocks.append(
            schemas.make_block(
                text=text,
                page_index=page_idx,
                block_bbox=bbox,
                spans=[],
            )
        )

    # --- Document title (teiHeader) ---
    for title in root.findall(f".//{_NS}titleStmt/{_NS}title"):
        _make(_elem_text(title), title, fallback_page=0)
        break  # only the first/main title

    # --- Abstract ---
    for abstract in root.findall(f".//{_NS}abstract"):
        for p in abstract.findall(f".//{_NS}p"):
            _make(_elem_text(p), p)

    # --- Body: sections, paragraphs, figures, formulae, footnotes ---
    body = root.find(f".//{_NS}body")
    if body is not None:
        # Pre-compute descendant sets so we can skip nested elements in O(1).
        # <p> inside <figure>: already represented via <figDesc>.
        # <formula>/<note> inside <p>: text already captured by _elem_text(p).
        figure_descendants: set[int] = {
            id(desc)
            for fig in body.findall(f".//{_NS}figure")
            for desc in fig.iter()
        }
        para_descendants: set[int] = {
            id(desc)
            for p in body.findall(f".//{_NS}p")
            for desc in p.iter()
            if desc.tag != p.tag  # exclude the <p> element itself
        }

        for elem in body.iter():
            tag = elem.tag
            eid = id(elem)

            if tag == f"{_NS}head":
                _make(_elem_text(elem), elem)

            elif tag == f"{_NS}p" and eid not in figure_descendants:
                _make(_elem_text(elem), elem)

            elif tag == f"{_NS}figDesc":
                _make(_elem_text(elem), elem)

            elif tag == f"{_NS}formula" and eid not in para_descendants:
                # Only standalone/display formulas; inline formulas are
                # already captured inside their parent <p> via itertext().
                _make(_elem_text(elem), elem)

            elif (
                tag == f"{_NS}note"
                and elem.get("place") == "foot"
                and eid not in para_descendants
            ):
                _make(_elem_text(elem), elem)

    # --- Back matter ---
    back = root.find(f".//{_NS}back")
    if back is not None:
        # Funding & data availability
        for div in back.findall(f".//{_NS}div"):
            div_type = div.get("type", "")
            if div_type in ("funding", "availability"):
                _make(_elem_text(div), div)

        # References
        for biblstruct in back.findall(f".//{_NS}listBibl/{_NS}biblStruct"):
            ref_text = _ref_text(biblstruct)
            _make(ref_text, biblstruct)

    return blocks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_with_grobid(
    pdf_path: str,
    *,
    grobid_url: str = "http://localhost:8070",
    timeout: int = 120,
    consolidate_header: int = 0,
    consolidate_citations: int = 0,
    generate_ids: bool = True,
    segment_sentences: bool = True,
    include_raw_citations: bool = True,
    include_raw_affiliations: bool = False,
    tei_coordinates: bool = True,
    max_retries: int = 2,
) -> tuple:
    """Extract text from *pdf_path* using the GROBID REST API.

    Sends the PDF to GROBID's ``processFulltextDocument`` endpoint with the
    requested feature flags, parses the returned TEI XML into
    :class:`~pdf_extractor.extraction.schemas.BlockDict` objects, and
    returns both the raw XML and the parsed blocks.

    Parameters
    ----------
    pdf_path:
        Absolute or relative path to the PDF file.
    grobid_url:
        Base URL of the running GROBID server.
    timeout:
        HTTP request timeout in seconds.
    consolidate_header:
        ``0`` = no consolidation; ``1`` = CrossRef; ``2`` = PubMed.
        Enriches ``<teiHeader>`` with publisher metadata and DOI.
    consolidate_citations:
        ``0`` = no consolidation; ``1`` = CrossRef; ``2`` = PubMed.
        Resolves each reference to an external DOI.
    generate_ids:
        Assign stable ``xml:id`` attributes to elements, enabling
        citation-to-reference linking within the document.
    segment_sentences:
        Wrap sentences in ``<s>`` elements inside ``<p>``. When combined
        with *tei_coordinates*, each sentence receives its own PDF coordinates.
    include_raw_citations:
        Attach verbatim citation strings as ``<note type="raw_reference">``
        inside each ``<biblStruct>``.
    include_raw_affiliations:
        Attach raw affiliation text to ``<author>`` elements.
    tei_coordinates:
        Request PDF page coordinates for ``p,s,ref,biblStruct,figure,
        formula,head`` elements.  Populated into ``block_bbox`` fields.
    max_retries:
        Maximum number of retry attempts for transient HTTP 5xx / timeout
        errors (exponential backoff: 1 s, 2 s, …).

    Returns
    -------
    tuple
        ``(tei_xml_str, blocks)`` where *tei_xml_str* is the raw TEI XML
        string (use as ``Candidate.payload`` for the QC pipeline) and
        *blocks* is a ``list[BlockDict]`` for cascade quality scoring.

    Raises
    ------
    RuntimeError
        If GROBID is unreachable, returns an error status, times out, the
        TEI XML cannot be parsed, or no text blocks can be extracted.
    """
    form_data: dict[str, str] = {
        "consolidateHeader": str(consolidate_header),
        "consolidateCitations": str(consolidate_citations),
        "generateIDs": "1" if generate_ids else "0",
        "segmentSentences": "1" if segment_sentences else "0",
        "includeRawCitations": "1" if include_raw_citations else "0",
        "includeRawAffiliations": "1" if include_raw_affiliations else "0",
    }
    if tei_coordinates:
        form_data["teiCoordinates"] = "p,s,ref,biblStruct,figure,formula,head"

    logger.info("GROBID extraction start: %s", pdf_path)
    tei_xml_str = _call_grobid_api(
        pdf_path, grobid_url, form_data, timeout, max_retries
    )
    logger.debug("GROBID returned %d bytes of TEI XML", len(tei_xml_str))

    try:
        blocks = _parse_tei_to_blocks(tei_xml_str, tei_coordinates)
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse GROBID TEI XML: {exc}") from exc

    if not blocks:
        raise RuntimeError("GROBID returned no extractable text blocks")

    schemas.validate_blocks(blocks)
    logger.info(
        "GROBID extraction complete: %d blocks, pdf=%s", len(blocks), pdf_path
    )
    return tei_xml_str, blocks
