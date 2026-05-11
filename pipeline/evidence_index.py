"""Build and cache GROBID-centered evidence bundles for extraction chunks."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from utils.logging_utils import get_logger
from utils.path_utils import OUTPUT_DIR

logger = get_logger(__name__)

_TEI_NS = "http://www.tei-c.org/ns/1.0"
_NS = f"{{{_TEI_NS}}}"


@dataclass
class EvidenceBundle:
    """Derived evidence package with compact index and metadata."""

    paper_id: str
    tei_xml: str
    evidence_items: list[dict[str, Any]]
    evidence_map: dict[str, dict[str, Any]]
    prefilled_fields: dict[int, str]
    index_path: Path


def _safe_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _pdf_hash(source_pdf_path: str) -> str:
    """Return a short, stable cache key for *source_pdf_path*.

    Uses the filesystem-native (size, mtime_ns) pair plus the basename as
    the key. This is what make / ninja / most build systems key on and is
    always correct when the filesystem's mtime is reliable -- which is
    every supported EviTrace target (Linux ext4/xfs/btrfs, macOS APFS,
    Windows NTFS).

    Advantages over full SHA-256:
      - O(1) regardless of PDF size (large papers no longer block the
        event loop while being hashed).
      - Zero I/O beyond a single stat() call.
      - Same collision-resistance in practice: a user would have to edit
        a PDF in place without changing its size AND with clock skew
        that preserves mtime_ns to the nanosecond to collide.

    Falls back to a SHA-256 prefix when stat() fails (unusual paths,
    broken symlinks) so the cache key is never empty.
    """
    try:
        st = Path(source_pdf_path).stat()
    except OSError:
        # Fallback: one-shot SHA-256 of the file. Matches the previous
        # behaviour so existing caches are still reachable.
        h = hashlib.sha256()
        try:
            with open(source_pdf_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        except OSError:
            return "nohash"

    # Pack (size, mtime_ns, basename) into a small hash to keep the key
    # short and filesystem-safe. The inputs are all cheap to obtain.
    basename = Path(source_pdf_path).name
    payload = f"{st.st_size}:{st.st_mtime_ns}:{basename}".encode("utf-8")
    return hashlib.blake2b(payload, digest_size=8).hexdigest()


def _cache_dir(config: dict) -> Path:
    cache_dir = config.get("evidence_cache_dir", str(OUTPUT_DIR / "evidence_cache"))
    path = Path(cache_dir)
    if not path.is_absolute():
        path = (OUTPUT_DIR.parent / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_coords(coords: str) -> dict[str, Any]:
    if not coords:
        return {"page": None, "coords": None}
    first = coords.strip().split()[0]
    parts = first.split(";")
    if len(parts) != 2:
        return {"page": None, "coords": None}
    page_raw, bbox_raw = parts
    try:
        nums = [float(x) for x in bbox_raw.split(",")]
        if len(nums) != 4:
            return {"page": None, "coords": None}
        return {"page": int(page_raw), "coords": nums}
    except ValueError:
        return {"page": None, "coords": None}


def _tei_xpath(elem: ET.Element) -> str:
    xml_id = elem.attrib.get("{http://www.w3.org/XML/1998/namespace}id") or elem.attrib.get("xml:id")
    if xml_id:
        return f"//*[@xml:id='{xml_id}']"
    return f".//{elem.tag.split('}')[-1]}"


def _section_score(path: str) -> int:
    lower = path.lower()
    boosts = {
        "abstract": 40,
        "introduction": 30,
        "method": 35,
        "data": 35,
        "dataset": 35,
        "experiment": 35,
        "result": 35,
        "evaluation": 30,
        "discussion": 25,
        "limitation": 25,
        "conclusion": 25,
    }
    penalties = {
        "reference": -60,
        "bibliograph": -60,
        "acknowledg": -40,
        "funding": -50,
        "author": -50,
        "affiliation": -50,
        "related work": -25,
        "background": -15,
        "literature review": -25,
    }
    score = 0
    for key, value in boosts.items():
        if key in lower:
            score += value
    for key, value in penalties.items():
        if key in lower:
            score += value
    return score


def _build_items_from_tei(tei_xml: str, paper_id: str, source_pdf: str) -> tuple[list[dict[str, Any]], dict[int, str]]:
    root = ET.fromstring(tei_xml)
    items: list[dict[str, Any]] = []
    sentence_counter = 1
    table_counter = 1
    figure_counter = 1

    title = root.find(f".//{_NS}titleStmt/{_NS}title")
    first_author = root.find(f".//{_NS}sourceDesc//{_NS}surname")
    pub_date = root.find(f".//{_NS}teiHeader//{_NS}date")
    year = ""
    if pub_date is not None:
        candidate = pub_date.attrib.get("when", "") or "".join(pub_date.itertext())
        m = re.search(r"(19|20)\d{2}", candidate)
        if m:
            year = m.group(0)
    author = _safe_text("".join(first_author.itertext())) if first_author is not None else ""

    prefilled = {1: author or paper_id, 2: year or "nr"}

    section_path = "body"
    body = root.find(f".//{_NS}body")
    if body is not None:
        for div in body.findall(f".//{_NS}div"):
            head = div.find(f"./{_NS}head")
            if head is not None:
                section_path = _safe_text("".join(head.itertext())) or section_path
            for sent in div.findall(f".//{_NS}s"):
                text = _safe_text("".join(sent.itertext()))
                if not text:
                    continue
                sid = f"S{sentence_counter:06d}"
                sentence_counter += 1
                loc = _parse_coords(sent.attrib.get("coords", ""))
                items.append(
                    {
                        "id": sid,
                        "type": "sentence",
                        "section_path": section_path,
                        "page": loc["page"],
                        "coords": loc["coords"],
                        "xpath": _tei_xpath(sent),
                        "text": text,
                        "source_pdf": source_pdf,
                        "score": _section_score(section_path),
                        "annotations": {},
                    }
                )

            for p in div.findall(f".//{_NS}p"):
                if p.findall(f".//{_NS}s"):
                    continue
                text = _safe_text("".join(p.itertext()))
                if not text:
                    continue
                sid = f"S{sentence_counter:06d}"
                sentence_counter += 1
                loc = _parse_coords(p.attrib.get("coords", ""))
                items.append(
                    {
                        "id": sid,
                        "type": "sentence",
                        "section_path": section_path,
                        "page": loc["page"],
                        "coords": loc["coords"],
                        "xpath": _tei_xpath(p),
                        "text": text,
                        "source_pdf": source_pdf,
                        "score": _section_score(section_path),
                        "annotations": {},
                    }
                )

        for fig in body.findall(f".//{_NS}figure"):
            caption = fig.find(f".//{_NS}figDesc")
            text = _safe_text("".join(caption.itertext())) if caption is not None else ""
            if not text:
                continue
            fid = f"F{figure_counter:06d}"
            figure_counter += 1
            loc = _parse_coords(fig.attrib.get("coords", ""))
            items.append(
                {
                    "id": fid,
                    "type": "figure_caption",
                    "section_path": section_path,
                    "page": loc["page"],
                    "coords": loc["coords"],
                    "xpath": _tei_xpath(fig),
                    "text": text,
                    "source_pdf": source_pdf,
                    "score": _section_score(section_path) + 5,
                    "annotations": {},
                }
            )

        for table in body.findall(f".//{_NS}table"):
            rows: list[str] = []
            for row in table.findall(f".//{_NS}row"):
                cells = [_safe_text("".join(cell.itertext())) for cell in row.findall(f".//{_NS}cell")]
                row_text = " | ".join([cell for cell in cells if cell])
                if row_text:
                    rows.append(row_text)
            text = "\n".join(rows)
            if not text:
                continue
            tid = f"T{table_counter:06d}"
            table_counter += 1
            loc = _parse_coords(table.attrib.get("coords", ""))
            items.append(
                {
                    "id": tid,
                    "type": "table",
                    "section_path": section_path,
                    "page": loc["page"],
                    "coords": loc["coords"],
                    "xpath": _tei_xpath(table),
                    "text": text,
                    "source_pdf": source_pdf,
                    "score": _section_score(section_path) + 10,
                    "annotations": {},
                }
            )

    abstract = root.find(f".//{_NS}abstract")
    if abstract is not None:
        for p in abstract.findall(f".//{_NS}p"):
            text = _safe_text("".join(p.itertext()))
            if not text:
                continue
            sid = f"S{sentence_counter:06d}"
            sentence_counter += 1
            loc = _parse_coords(p.attrib.get("coords", ""))
            items.append(
                {
                    "id": sid,
                    "type": "sentence",
                    "section_path": "Abstract",
                    "page": loc["page"],
                    "coords": loc["coords"],
                    "xpath": _tei_xpath(p),
                    "text": text,
                    "source_pdf": source_pdf,
                    "score": _section_score("Abstract"),
                    "annotations": {},
                }
            )

    if title is not None:
        text = _safe_text("".join(title.itertext()))
        if text:
            sid = f"S{sentence_counter:06d}"
            items.append(
                {
                    "id": sid,
                    "type": "sentence",
                    "section_path": "Metadata",
                    "page": 1,
                    "coords": None,
                    "xpath": _tei_xpath(title),
                    "text": text,
                    "source_pdf": source_pdf,
                    "score": -30,
                    "annotations": {},
                }
            )
    return items, prefilled


def _service_enabled(cfg: dict, key: str) -> bool:
    return bool(cfg.get("addons", {}).get(key, {}).get("enabled", False))


# Heuristic fallbacks, used when a service is disabled, unreachable, or returns
# no structured annotations. They never overwrite service-provided data.
_QUANTITY_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|kg|ml|cm|mm|%|h|hr|day|days|year|years)\b",
    re.IGNORECASE,
)
_DATASET_RE = re.compile(
    r"\b(MIMIC-III|MIMIC-IV|eICU|UK Biobank|MarketScan)\b",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")


def _heuristic_quantities(text: str) -> list[str]:
    return _QUANTITY_RE.findall(text or "")


def _heuristic_datasets(text: str) -> list[str]:
    return _DATASET_RE.findall(text or "")


def _heuristic_entities(text: str) -> list[str]:
    return _ENTITY_RE.findall(text or "")[:10]


def _preflight_addon(requests_mod: Any, name: str, info: dict) -> bool:
    """Return True if the addon service responds to a fast isalive probe."""
    url = (info.get("url") or "").strip()
    if not info.get("enabled", False) or not url:
        return False
    alive_path = (info.get("isalive", "") or "/service/isalive").strip()
    probe_timeout = float(info.get("preflight_timeout", 2.0))
    try:
        resp = requests_mod.get(url.rstrip("/") + alive_path, timeout=probe_timeout)
    except Exception as exc:
        logger.info("%s addon preflight failed (%s); falling back to heuristics", name, exc)
        return False
    if resp.status_code >= 400:
        logger.info(
            "%s addon preflight returned HTTP %s; falling back to heuristics",
            name, resp.status_code,
        )
        return False
    return True


def _call_addon(
    requests_mod: Any,
    name: str,
    info: dict,
    payload: dict,
) -> dict:
    """POST *payload* to the named addon and return the parsed JSON dict."""
    url = (info.get("url") or "").strip()
    endpoint = (info.get("endpoint") or "").strip() or "/service/process"
    timeout = float(info.get("timeout", 5))
    try:
        resp = requests_mod.post(
            url.rstrip("/") + endpoint, json=payload, timeout=timeout,
        )
    except Exception as exc:
        logger.warning("%s addon POST failed: %s", name, exc)
        return {}
    if resp.status_code >= 400:
        logger.warning("%s addon returned HTTP %s", name, resp.status_code)
        return {}
    try:
        data = resp.json() if resp.text else {}
    except ValueError:
        logger.warning("%s addon returned non-JSON body", name)
        return {}
    return data if isinstance(data, dict) else {}


def _build_offset_index(items: list[dict[str, Any]], joiner: str) -> list[tuple[int, int, dict[str, Any]]]:
    """Return [(start, end, item), ...] describing each item's range in the joined blob."""
    ranges: list[tuple[int, int, dict[str, Any]]] = []
    offset = 0
    for i, item in enumerate(items):
        text = item.get("text") or ""
        if not text:
            continue
        start = offset
        end = start + len(text)
        ranges.append((start, end, item))
        offset = end + (len(joiner) if i < len(items) - 1 else 0)
    return ranges


def _assign_by_offset(
    ranges: list[tuple[int, int, dict[str, Any]]],
    key: str,
    annotations_with_offset: list[dict[str, Any]],
    offset_key: str,
) -> set[int]:
    """Distribute *annotations_with_offset* into the right item by character offset.

    Returns the set of item ``id(...)`` values that received at least one
    annotation — callers use this to decide which items fall back to heuristics.
    """
    populated: set[int] = set()
    if not ranges or not annotations_with_offset:
        return populated
    for ann in annotations_with_offset:
        try:
            off = int(ann.get(offset_key, -1))
        except (TypeError, ValueError):
            continue
        if off < 0:
            continue
        # Binary-search-ish linear scan; ranges are monotonically increasing.
        for start, end, item in ranges:
            if start <= off < end:
                bucket = item.setdefault("annotations", {}).setdefault(key, [])
                bucket.append(ann)
                populated.add(id(item))
                break
    return populated


def _enrich_with_addons(items: list[dict[str, Any]], cfg: dict) -> None:
    """Enrich each evidence item with per-item quantities, datasets, entities.

    Strategy:
    1. Preflight each enabled addon with a fast isalive probe; skip ones that
       are unreachable (no long timeouts).
    2. For live services, send the document blob ONCE and map each returned
       annotation back to the specific item whose text span contains its
       offset. Annotations without offsets are attached document-wide but
       stored under a ``_document`` key, not blasted onto every item.
    3. Items that receive no service-derived annotations for a given key fall
       back to per-item regex heuristics so we never degrade accuracy below
       the heuristic baseline.
    """
    addon_cfg = cfg.get("addons", {})
    if not isinstance(addon_cfg, dict):
        return

    any_enabled = any(
        isinstance(v, dict) and v.get("enabled", False) for v in addon_cfg.values()
    )

    requests_mod = None
    if any_enabled:
        try:
            import requests as _requests  # noqa: PLC0415
            requests_mod = _requests
        except Exception:
            logger.warning(
                "Addon services enabled but requests is unavailable; using heuristics only"
            )

    # Always apply heuristic defaults first so items are never missing keys
    # when a service returns empty or is down. Service data will merge on top.
    for item in items:
        text = item.get("text", "") or ""
        annotations = item.setdefault("annotations", {})
        annotations.setdefault("quantities", _heuristic_quantities(text))
        annotations.setdefault("datasets", _heuristic_datasets(text))
        annotations.setdefault("entities", _heuristic_entities(text))

    # Nothing more to do without a live service.
    if requests_mod is None:
        return

    text_items = [it for it in items if (it.get("text") or "").strip()]
    if not text_items:
        return

    # Preflight each enabled addon ONCE so we know which services to even try.
    q_live = _preflight_addon(requests_mod, "grobid_quantities", addon_cfg.get("grobid_quantities", {}))
    d_live = _preflight_addon(requests_mod, "datastet", addon_cfg.get("datastet", {}))
    e_live = _preflight_addon(requests_mod, "entity_fishing", addon_cfg.get("entity_fishing", {}))
    logger.info(
        "Addon preflight: quantities=%s datastet=%s entity_fishing=%s",
        q_live, d_live, e_live,
    )
    if not (q_live or d_live or e_live):
        return

    joiner = "\n\n"  # two newlines => offsets in blob are well-defined
    text_blob = joiner.join(it.get("text", "") for it in text_items)
    if not text_blob.strip():
        return
    ranges = _build_offset_index(text_items, joiner)

    # grobid-quantities: expects {"text": "..."}, returns {"measurements":[...]}
    # with "offsetStart"/"offsetEnd" keys per measurement.
    if q_live:
        q_data = _call_addon(
            requests_mod, "grobid_quantities",
            addon_cfg.get("grobid_quantities", {}),
            {"text": text_blob},
        )
        measurements = q_data.get("measurements") or q_data.get("quantities") or []
        if isinstance(measurements, list) and measurements:
            populated = _assign_by_offset(ranges, "quantities", measurements, "offsetStart")
            # Items that received a service annotation drop their heuristic
            # fallback in favour of the richer service payload. Items that
            # received nothing keep the heuristic quantities we populated above.
            for _, _, item in ranges:
                if id(item) in populated:
                    # Convert from "quantities": [ann, ...] (list of dicts) —
                    # already correct; nothing to normalise here.
                    pass

    # datastet (DataStet): {"text":...} -> {"mentions":[{"offsetStart":...}, ...]}
    if d_live:
        d_data = _call_addon(
            requests_mod, "datastet",
            addon_cfg.get("datastet", {}),
            {"text": text_blob},
        )
        mentions = d_data.get("mentions") or d_data.get("datasets") or []
        if isinstance(mentions, list) and mentions:
            populated = _assign_by_offset(ranges, "datasets", mentions, "offsetStart")
            for _, _, item in ranges:
                if id(item) in populated:
                    pass

    # entity-fishing: {"text":...} -> {"entities":[{"offsetStart":...,"rawName":...}, ...]}
    if e_live:
        e_data = _call_addon(
            requests_mod, "entity_fishing",
            addon_cfg.get("entity_fishing", {}),
            {"text": text_blob, "language": {"lang": "en"}},
        )
        entities = e_data.get("entities") or []
        if isinstance(entities, list) and entities:
            populated = _assign_by_offset(ranges, "entities", entities, "offsetStart")
            for _, _, item in ranges:
                if id(item) in populated:
                    pass


def build_or_load_evidence_bundle(qc_context, config: dict) -> EvidenceBundle:
    """Build evidence index from GROBID TEI with local cache and enrichment."""
    unified = qc_context.unified
    assert unified is not None
    content = unified.content if isinstance(unified.content, dict) else {}
    paper_id = unified.document_id
    source_pdf_path = content.get("source_pdf_path", "")
    tei_xml = content.get("grobid_tei_xml", "")
    if not isinstance(tei_xml, str):
        tei_xml = ""
    logger.debug(
        "build_or_load_evidence_bundle: paper_id=%s, source_pdf=%s, tei_xml=%d chars",
        paper_id, source_pdf_path, len(tei_xml),
    )

    cache_root = _cache_dir(config)
    pdf_hash = _pdf_hash(source_pdf_path) if source_pdf_path and Path(source_pdf_path).exists() else "nohash"
    cache_key = f"{paper_id}_{pdf_hash}"
    tei_path = cache_root / f"{cache_key}.tei.xml"
    idx_path = cache_root / f"{cache_key}.evidence.json"
    logger.debug(
        "Evidence cache: root=%s, key=%s, tei_exists=%s, idx_exists=%s",
        cache_root, cache_key, tei_path.exists(), idx_path.exists(),
    )

    if idx_path.exists():
        try:
            loaded = json.loads(idx_path.read_text(encoding="utf-8"))
            items = loaded.get("evidence_items", [])
            prefilled = loaded.get("prefilled_fields", {})
            if isinstance(items, list):
                evidence_map = {item["id"]: item for item in items if isinstance(item, dict) and item.get("id")}
                logger.info("Evidence index cache hit: %s (%d items)", idx_path.name, len(evidence_map))
                # Parens matter: the ternary binds tighter than ``or`` in
                # Python, so the previous version was parsed as
                # ``tei_xml or (read_text() if exists else "")`` which was
                # correct but fragile. Make it explicit.
                cached_tei_xml = tei_xml or (
                    tei_path.read_text(encoding="utf-8") if tei_path.exists() else ""
                )
                return EvidenceBundle(
                    paper_id=paper_id,
                    tei_xml=cached_tei_xml,
                    evidence_items=items,
                    evidence_map=evidence_map,
                    prefilled_fields={int(k): str(v) for k, v in prefilled.items()},
                    index_path=idx_path,
                )
        except Exception:
            logger.warning("Ignoring corrupted evidence cache for %s", paper_id)

    if not tei_xml and tei_path.exists():
        tei_xml = tei_path.read_text(encoding="utf-8")

    if not tei_xml.strip():
        # Fallback: build sentence-only index from exact text.
        logger.debug(
            "No TEI available for %s; falling back to sentence split of exact_text",
            paper_id,
        )
        exact_text = str(content.get("exact_text", ""))
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", exact_text) if s.strip()]
        items = [
            {
                "id": f"S{i:06d}",
                "type": "sentence",
                "section_path": "Fallback",
                "page": None,
                "coords": None,
                "xpath": "",
                "text": sentence,
                "source_pdf": source_pdf_path,
                "score": 0,
                "annotations": {},
            }
            for i, sentence in enumerate(sentences, start=1)
        ]
        prefilled = {1: paper_id, 2: "nr"}
    else:
        try:
            items, prefilled = _build_items_from_tei(tei_xml, paper_id, source_pdf_path)
            logger.debug(
                "TEI parsed for %s: %d items, prefilled=%s",
                paper_id, len(items), prefilled,
            )
        except ET.ParseError as exc:
            logger.debug("TEI parse error for %s: %s", paper_id, exc)
            items = []
            prefilled = {1: paper_id, 2: "nr"}

    _enrich_with_addons(items, config)
    evidence_map = {item["id"]: item for item in items if item.get("id")}

    if tei_xml.strip():
        tei_path.write_text(tei_xml, encoding="utf-8")
    idx_path.write_text(
        json.dumps(
            {"paper_id": paper_id, "source_pdf_path": source_pdf_path, "evidence_items": items, "prefilled_fields": prefilled},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info("Evidence index generated: %s (%d items)", idx_path.name, len(items))
    return EvidenceBundle(
        paper_id=paper_id,
        tei_xml=tei_xml,
        evidence_items=items,
        evidence_map=evidence_map,
        prefilled_fields=prefilled,
        index_path=idx_path,
    )


def build_chunk_evidence_package(
    bundle: EvidenceBundle,
    chunk_fields: list[dict],
    *,
    max_items: int,
    max_chars: int,
) -> str:
    """Build a per-chunk, chunk-specific evidence package (LEGACY).

    .. deprecated::
        Prefer :func:`build_paper_evidence_package` for cache-friendly extraction
        across multiple chunks. This helper ranks items by a score that depends
        on the specific ``chunk_fields`` passed in, which means two different
        chunks for the same paper produce different serialisations and defeat
        OpenAI's prompt-prefix cache. Kept for tests and advanced callers that
        genuinely need chunk-specific pruning.

    The serialised JSON is deterministic: items that tie on score are ordered
    by their stable ``id`` so repeated calls with the same inputs always
    produce byte-identical output.
    """
    if not chunk_fields:
        return '{"paper_id":"","evidence":[]}'

    keywords = " ".join(
        f"{field.get('field_name', '')} {field.get('definition', '')} {field.get('reviewer_question', '')}"
        for field in chunk_fields
    ).lower()
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in bundle.evidence_items:
        text = item.get("text", "")
        overlap = 0
        for token in set(re.findall(r"[a-z]{4,}", keywords)):
            if token in text.lower():
                overlap += 1
        score = int(item.get("score", 0)) + overlap * 3
        ranked.append((score, str(item.get("id", "")), item))

    # Deterministic ordering: higher score first, ties broken by stable id.
    ranked.sort(key=lambda x: (-x[0], x[1]))
    selected: list[dict[str, Any]] = []
    char_budget = 0
    for _, _, item in ranked:
        if len(selected) >= max_items:
            break
        text = item.get("text", "")
        if char_budget + len(text) > max_chars:
            continue
        selected.append(
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "section": item.get("section_path"),
                "page": item.get("page"),
                "coords": item.get("coords"),
                "text": text,
                "annotations": item.get("annotations", {}),
            }
        )
        char_budget += len(text)

    # Emit selected items in stable id order so the serialised prefix is
    # byte-identical across repeated calls with the same inputs.
    selected.sort(key=lambda x: str(x.get("id", "")))
    logger.debug(
        "build_chunk_evidence_package: paper=%s, fields=%d, ranked=%d, selected=%d, chars=%d",
        bundle.paper_id, len(chunk_fields), len(ranked), len(selected), char_budget,
    )

    package = {
        "paper_id": bundle.paper_id,
        "evidence_count": len(selected),
        "evidence": selected,
    }
    return json.dumps(package, ensure_ascii=False)


def build_paper_evidence_package(
    bundle: EvidenceBundle,
    all_fields: list[dict],
    *,
    max_items: int,
    max_chars: int,
) -> str:
    """Build a single paper-level evidence package shared by all extraction chunks.

    This is the preferred builder. It produces **one** byte-identical evidence
    string for every chunk of a given paper so that the shared PDF prefix
    embedded in :func:`agents.openai.prompts._shared_paper_prefix` hits
    OpenAI's prompt cache on every call after the first.

    Ranking
    -------
    Items are scored using the union of keywords from every field across every
    chunk (plus the per-item section bonuses already stored on
    :class:`EvidenceBundle`). This preserves relevance — any token that any
    chunk cares about lifts an item's score — while keeping the score, the
    selected set, and the serialised bytes identical across chunks.

    Determinism
    -----------
    After score-based selection, items are emitted in stable ``id`` order so
    two invocations with identical inputs always produce identical output.
    """
    if not all_fields:
        return '{"paper_id":"","evidence":[]}'

    keywords = " ".join(
        f"{field.get('field_name', '')} {field.get('definition', '')} {field.get('reviewer_question', '')}"
        for field in all_fields
    ).lower()
    keyword_tokens = set(re.findall(r"[a-z]{4,}", keywords))

    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for item in bundle.evidence_items:
        text = (item.get("text") or "").lower()
        overlap = sum(1 for token in keyword_tokens if token in text)
        score = int(item.get("score", 0)) + overlap * 3
        ranked.append((score, str(item.get("id", "")), item))

    # Select by score, ties broken by id.
    ranked.sort(key=lambda x: (-x[0], x[1]))
    selected: list[dict[str, Any]] = []
    char_budget = 0
    for _, _, item in ranked:
        if len(selected) >= max_items:
            break
        text = item.get("text", "") or ""
        if char_budget + len(text) > max_chars:
            continue
        selected.append(
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "section": item.get("section_path"),
                "page": item.get("page"),
                "coords": item.get("coords"),
                "text": text,
                "annotations": item.get("annotations", {}),
            }
        )
        char_budget += len(text)

    # Emit selected items in stable id order so the serialised prefix is
    # byte-identical across chunks (this is what the prompt cache keys on).
    selected.sort(key=lambda x: str(x.get("id", "")))

    logger.info(
        "build_paper_evidence_package: paper=%s, total_fields=%d, ranked=%d, "
        "selected=%d, chars=%d",
        bundle.paper_id, len(all_fields), len(ranked), len(selected), char_budget,
    )

    package = {
        "paper_id": bundle.paper_id,
        "evidence_count": len(selected),
        "evidence": selected,
    }
    return json.dumps(package, ensure_ascii=False, sort_keys=False)


def attach_table_figure_crops(
    fields: list[dict[str, Any]],
    bundle: EvidenceBundle,
    config: dict,
) -> None:
    """Crop table/figure regions for resolved loc IDs when configured."""
    crop_figures = bool(config.get("crop_figures", True))
    crop_tables = bool(config.get("crop_tables", True))
    if not (crop_figures or crop_tables):
        return
    if not bundle.evidence_items:
        return
    source_pdf = bundle.evidence_items[0].get("source_pdf", "")
    if not source_pdf or not Path(source_pdf).exists():
        return

    # Fast path: walk the fields once and short-circuit if nothing is
    # crop-eligible. Avoids the ~50ms fitz.open() + font-load cost on PDFs
    # whose extraction map has no table/figure location_metadata.
    eligible_types: set[str] = set()
    if crop_figures:
        eligible_types.add("figure_caption")
    if crop_tables:
        eligible_types.add("table")

    def _has_eligible_crop() -> bool:
        for field in fields:
            meta = field.get("location_metadata", [])
            if not isinstance(meta, list):
                continue
            for item in meta:
                if not isinstance(item, dict):
                    continue
                if item.get("type") not in eligible_types:
                    continue
                coords = item.get("coords")
                page = item.get("page")
                if (
                    isinstance(coords, list)
                    and len(coords) == 4
                    and isinstance(page, int)
                    and page > 0
                ):
                    return True
        return False

    if not _has_eligible_crop():
        return

    import fitz

    crop_dir = bundle.index_path.parent / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(source_pdf)
    try:
        for field in fields:
            meta = field.get("location_metadata", [])
            if not isinstance(meta, list):
                continue
            for item in meta:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type", "")
                coords = item.get("coords")
                page = item.get("page")
                if not isinstance(coords, list) or len(coords) != 4:
                    continue
                if not isinstance(page, int) or page <= 0 or page > len(doc):
                    continue
                if item_type == "figure_caption" and not crop_figures:
                    continue
                if item_type == "table" and not crop_tables:
                    continue
                rect = fitz.Rect(*coords)
                pix = doc[page - 1].get_pixmap(clip=rect, dpi=180)
                file_name = f"{bundle.paper_id}_{item.get('id','loc')}.png"
                out_path = crop_dir / file_name
                pix.save(str(out_path))
                item["crop_path"] = str(out_path)
    finally:
        doc.close()
