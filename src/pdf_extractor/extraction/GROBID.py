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
from dataclasses import dataclass

from . import schemas

logger = logging.getLogger("pdf_extractor")

_GROBID_ENDPOINT = "/api/processFulltextDocument"
_TEI_NS = "http://www.tei-c.org/ns/1.0"
_NS = f"{{{_TEI_NS}}}"

# Disable env-proxy detection on every GROBID call so a stray HTTP_PROXY /
# HTTPS_PROXY can't route loopback traffic through a corp proxy.
_NO_PROXY: dict[str, str | None] = {"http": None, "https": None}

# Lazily-built process-wide Session. Keeps a small connection pool open so
# back-to-back PDF extractions to localhost reuse the TCP socket. Tests can
# patch requests.post without going through this object because the module
# falls back to requests.post when no session has been built yet.
_session = None  # type: ignore[assignment]
_session_lock_pid: int | None = None


def _get_session():
    """Return a process-wide ``requests.Session`` for GROBID calls.

    Re-creates the Session if the PID changes (forked worker) so child
    processes don't share parent's socket pool.
    """
    global _session, _session_lock_pid
    import os as _os  # noqa: PLC0415
    pid = _os.getpid()
    if _session is None or _session_lock_pid != pid:
        import requests  # noqa: PLC0415
        s = requests.Session()
        s.trust_env = False  # ignore HTTP(S)_PROXY / NO_PROXY env vars
        _session = s
        _session_lock_pid = pid
    return _session


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

    logger.info(
        "GROBID request start: pdf=%s timeout=%ds retries=%d endpoint=%s",
        pdf_name, timeout, max_retries + 1, endpoint,
    )

    session = _get_session()
    with open(pdf_path, "rb") as fh:
        for attempt in range(max_retries + 1):
            fh.seek(0)
            t_start = time.monotonic()
            try:
                response = session.post(
                    endpoint,
                    files={"input": (pdf_name, fh, "application/pdf")},
                    data=form_data,
                    timeout=timeout,
                    proxies=_NO_PROXY,
                )
            except requests.exceptions.ConnectionError as exc:
                raise RuntimeError(
                    f"GROBID server not reachable at {url}: {exc}"
                ) from exc
            except requests.exceptions.Timeout as exc:
                dt = time.monotonic() - t_start
                logger.warning(
                    "GROBID request timed out after %.1fs (limit=%ds) on attempt %d/%d for %s; "
                    "GROBID was still processing or stalled on this PDF",
                    dt, timeout, attempt + 1, max_retries + 1,
                    pdf_name,
                )
                if attempt < max_retries:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(
                    f"GROBID request timed out after {timeout}s for {pdf_name}"
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


@dataclass(frozen=True)
class CoordBox:
    """One GROBID coordinate box: ``page`` is 1-based as emitted in the TEI.

    ``x``/``y`` are the top-left corner and ``w``/``h`` the extent, in PDF
    points, exactly as GROBID writes them (``page,x,y,w,h``).
    """

    page: int
    x: float
    y: float
    w: float
    h: float


def _parse_coord_box(segment: str) -> CoordBox | None:
    """Parse one ``page,x,y,w,h`` segment; ``None`` if it is not exactly that."""
    parts = [p.strip() for p in segment.split(",")]
    if len(parts) != 5:
        return None
    page_str, *nums_str = parts
    if not (page_str.isascii() and page_str.isdigit()):
        return None
    nums: list[float] = []
    for token in nums_str:
        try:
            value = float(token)
        except ValueError:
            return None
        if value != value or value in (float("inf"), float("-inf")):
            return None
        nums.append(value)
    return CoordBox(int(page_str), *nums)


def parse_tei_coords(coords: str | None) -> list[CoordBox]:
    """Parse a GROBID TEI ``coords`` attribute into its boxes (canonical parser).

    Grammar (GROBID 0.8.x): boxes separated by ``;``, each ``page,x,y,w,h``
    with a 1-based page; whitespace around tokens and separators is tolerated.
    Returns ``[]`` when the input is absent, empty, or when *any* box is
    malformed (Requirement 11.5) -- never a partial list, and never raises.
    """
    if not isinstance(coords, str):
        return []
    text = coords.strip()
    if not text:
        return []
    boxes: list[CoordBox] = []
    for segment in text.split(";"):
        box = _parse_coord_box(segment)
        if box is None:
            return []
        boxes.append(box)
    return boxes


def _parse_coords(
    coords_str: str,
) -> tuple[int, tuple[float, float, float, float]] | None:
    """Parse a TEI ``coords`` attribute into a ``(page_index, bbox)`` pair.

    Delegates to :func:`parse_tei_coords`; the page is the first box's page
    converted to 0-based, and the bbox is the ``(x0, y0, x1, y1)`` union of
    every box on that page (``x1 = x + w``, ``y1 = y + h``).

    Returns ``None`` on absent or malformed input.
    """
    boxes = parse_tei_coords(coords_str)
    if not boxes:
        return None
    first_page = boxes[0].page
    same_page = [b for b in boxes if b.page == first_page]
    x0 = min(b.x for b in same_page)
    y0 = min(b.y for b in same_page)
    x1 = max(b.x + b.w for b in same_page)
    y1 = max(b.y + b.h for b in same_page)
    return first_page - 1, (x0, y0, x1, y1)




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
    generate_ids: bool = False,
    segment_sentences: bool = True,
    include_raw_citations: bool = True,
    include_raw_affiliations: bool = False,
    tei_coordinates: bool = True,
    max_retries: int = 2,
    parse_blocks: bool = True,
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
        Wrap sentences in ``<s>`` elements inside ``<p>``. Enables finer-
        grained per-sentence page routing in the QC pipeline; sentence-level
        coordinates are not requested (see *tei_coordinates*) so the QC
        layer falls back to the parent paragraph's page for routing.
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
    form_data: dict[str, str | list[str]] = {
        "consolidateHeader": str(consolidate_header),
        "consolidateCitations": str(consolidate_citations),
        "generateIDs": "1" if generate_ids else "0",
        "segmentSentences": "1" if segment_sentences else "0",
        "includeRawCitations": "1" if include_raw_citations else "0",
        "includeRawAffiliations": "1" if include_raw_affiliations else "0",
    }
    if tei_coordinates:
        # Request coords only for elements we actually consume. Omitting 's'
        # (sentences) avoids hundreds of per-sentence layout lookups per PDF;
        # the QC pipeline falls back to parent-paragraph page when sentence
        # coords are absent. Omitting 'ref' (inline citations) avoids
        # coordinate work for elements we never query individually.
        # GROBID expects one ``teiCoordinates`` form field per element type;
        # a list value makes ``requests`` emit repeated multipart fields. A
        # single comma-joined value is read as one unknown element name and
        # yields no ``coords`` at all (Requirement 11.1).
        form_data["teiCoordinates"] = ["p", "figure", "formula", "head", "biblStruct"]

    logger.info("GROBID extraction start: %s", pdf_path)
    tei_xml_str = _call_grobid_api(
        pdf_path, grobid_url, form_data, timeout, max_retries
    )
    logger.debug("GROBID returned %d bytes of TEI XML", len(tei_xml_str))

    if not parse_blocks:
        # The QC pipeline re-parses the TEI for its own page-routing needs and
        # discards this function's blocks. Skipping the parse here saves a
        # full xml.etree walk + validate_blocks pass per cache-miss PDF.
        logger.info(
            "GROBID extraction complete (parse_blocks=False): pdf=%s", pdf_path
        )
        return tei_xml_str, []

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


def parse_grobid_tei(tei_xml_str: str, tei_coordinates: bool = True) -> list[dict]:
    """Parse a previously-cached TEI XML string into BlockDict objects.

    Used by the GROBID disk-cache hit path to avoid the HTTP round-trip
    while reusing the same parsing logic as ``extract_with_grobid``.

    Raises
    ------
    RuntimeError
        If the XML cannot be parsed or yields no text blocks.
    """
    try:
        blocks = _parse_tei_to_blocks(tei_xml_str, tei_coordinates)
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse cached GROBID TEI XML: {exc}") from exc
    if not blocks:
        raise RuntimeError("Cached GROBID TEI XML contains no extractable text blocks")
    schemas.validate_blocks(blocks)
    return blocks
