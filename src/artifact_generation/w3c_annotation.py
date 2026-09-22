"""
artifact_generation/w3c_annotation.py
-------------------------------------
W3C annotation data model, projection, and JSON-LD serialization.

This module is the ONLY place in EviTrace that defines annotation records
and constructs W3C JSON-LD annotation dicts.

Classes
-------
AnnotationRecord
    Dataclass holding the projected annotation fields for a single sentence.

Functions
---------
project(unified, base_uri="")
    Project a UnifiedRecord into a list of AnnotationRecord instances.

generate_w3c_jsonld(records, base_uri="")
    Serialize a list of AnnotationRecord instances into W3C JSON-LD dicts.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("artifact_generation")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AnnotationRecord:
    """Single projected annotation record.

    Attributes
    ----------
    sentence_text:
        The sentence text used as annotation body.
    page_index:
        Zero-based index of the page containing this sentence.
    selector_type:
        ``"TextPositionSelector"`` for born-digital sentences with valid
        offsets; ``"FragmentSelector"`` for OCR sentences with a region;
        ``"TextQuoteSelector"`` when no region is available (the quote
        selector is then the only target selector).
    selector_payload:
        For TextPositionSelector: ``{"start": int, "end": int}``
        For FragmentSelector:     ``{"page": int, "xywh": str}``
        For TextQuoteSelector:    ``{}``
    quote_selector:
        ``{"exact": str, "prefix": str, "suffix": str}`` — always populated.
    ocr_derived:
        True when this record originates from an OCR backend.
    body_value:
        Annotation body text (typically same as sentence_text).
    occurrence:
        Zero-based count of prior sentences with identical text in the
        document (copied from the sentence's alignment entry).
    document_id:
        ``unified.document_id`` of the record the sentence came from.
    """

    sentence_text: str
    page_index: int
    selector_type: str
    selector_payload: dict
    quote_selector: dict
    ocr_derived: bool = False
    body_value: str = ""
    occurrence: int = 0
    document_id: str = ""


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def project(unified: Any, base_uri: str = "") -> list[AnnotationRecord]:  # noqa: ARG001
    """Project a UnifiedRecord into annotation records.

    Reads only ``unified.semantic``, ``unified.alignment`` and
    ``unified.document_id`` — never ``unified.structural`` and never raw
    extractor payloads. Sentence ``i`` is zipped with
    ``alignment.sentence_to_char_range[i]``, the location entry the
    reconciler emitted for that very sentence:

    - native sentence, ``start >= 0``  → ``TextPositionSelector`` (start/end)
    - native sentence, ``start == -1`` → ``TextQuoteSelector`` only
    - OCR sentence with ``bbox``       → ``FragmentSelector`` from that box
    - OCR sentence without ``bbox``    → WARNING (page + sentence prefix),
      ``TextQuoteSelector`` only, ``ocr_derived`` stays ``True``; the
      remaining sentences are still projected

    When ``alignment`` is ``None`` or its length differs from the sentence
    count, every sentence falls back to a quote selector and one line is
    logged.

    Parameters
    ----------
    unified:
        The reconciled document record.
    base_uri:
        Optional document URI placed in the annotation target source.
        Not used during projection (only during serialization), but accepted
        here to match the public interface signature.

    Returns
    -------
    list[AnnotationRecord]
        One record per sentence in the semantic layer. Returns an empty
        list when ``semantic`` is absent.
    """
    records: list[AnnotationRecord] = []
    if unified.semantic is None:
        return records

    sentences: list[dict] = list(unified.semantic.sentences or [])
    document_id = str(getattr(unified, "document_id", "") or "")
    entries = _aligned_entries(unified.alignment, len(sentences), document_id)

    # Quote context comes from the sentences joined in order; the running
    # cursor gives every sentence (including duplicated text) its own
    # neighbourhood instead of the first occurrence's.
    full_text = " ".join(s.get("text", "") for s in sentences)
    cursor = 0
    for sent_dict, entry in zip(sentences, entries):
        sent_text: str = sent_dict.get("text", "")
        page_idx: int = int(sent_dict.get("page_index", 0))
        ocr_derived: bool = bool(sent_dict.get("ocr_derived", False))

        quote_selector = {
            "exact": sent_text,
            "prefix": full_text[max(0, cursor - _QUOTE_CONTEXT_CHARS):cursor],
            "suffix": full_text[cursor + len(sent_text):cursor + len(sent_text) + _QUOTE_CONTEXT_CHARS],
        }
        cursor += len(sent_text) + 1

        selector_type, selector_payload = _select_region(entry, sent_text, page_idx, ocr_derived)
        records.append(
            AnnotationRecord(
                sentence_text=sent_text,
                page_index=page_idx,
                selector_type=selector_type,
                selector_payload=selector_payload,
                quote_selector=quote_selector,
                ocr_derived=ocr_derived,
                body_value=sent_text,
                occurrence=int(entry.get("occurrence", 0)) if entry else 0,
                document_id=document_id,
            )
        )
    return records


_QUOTE_CONTEXT_CHARS = 20


def _aligned_entries(alignment: Any, n_sentences: int, document_id: str) -> list[dict | None]:
    """Return the location entries positionally aligned with the sentences,
    or ``[None] * n`` (after a single log line) when the alignment is absent
    or its length does not match."""
    if alignment is None:
        logger.warning(
            "no alignment layer for document %r: emitting TextQuoteSelector only for %d sentence(s)",
            document_id,
            n_sentences,
        )
        return [None] * n_sentences
    entries = list(alignment.sentence_to_char_range or [])
    if len(entries) != n_sentences:
        logger.warning(
            "alignment has %d location entries for %d sentences in document %r: "
            "emitting TextQuoteSelector only",
            len(entries),
            n_sentences,
            document_id,
        )
        return [None] * n_sentences
    return entries


def _select_region(
    entry: dict | None, text: str, page_idx: int, ocr_derived: bool
) -> tuple[str, dict]:
    """Choose the region selector for one sentence from its own entry.

    - ``entry is None`` (alignment fallback) → quote selector only, silently:
      ``_aligned_entries`` already logged the single fallback line.
    - OCR sentence with a ``bbox`` → ``FragmentSelector`` from that box.
    - OCR sentence without one → WARNING (page + sentence prefix), quote only.
    - Native sentence with ``start >= 0`` → ``TextPositionSelector``.
    - Otherwise → quote selector only (``selector_payload == {}``).
    """
    if entry is None:
        return "TextQuoteSelector", {}

    if ocr_derived:
        bbox = entry.get("bbox")
        if bbox is not None and len(bbox) == 4:
            x0, y0, x1, y1 = bbox
            return "FragmentSelector", {
                "page": page_idx,
                "xywh": f"{x0},{y0},{x1 - x0},{y1 - y0}",
            }
        logger.warning("no region for OCR sentence on page %d: %.60r", page_idx, text)
        return "TextQuoteSelector", {}

    start = entry.get("start", -1)
    if isinstance(start, int) and start >= 0:
        return "TextPositionSelector", {"start": start, "end": entry.get("end", start)}
    return "TextQuoteSelector", {}


# ---------------------------------------------------------------------------
# W3C JSON-LD serialization
# ---------------------------------------------------------------------------

W3C_ANNO_CONTEXT = "http://www.w3.org/ns/anno.jsonld"

# Fixed namespace for name-based annotation ids (design: AnnotationIdentity).
_ANNO_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:evitrace:anno")


def _annotation_id(document_source: str, page_index: int, occurrence: int, text: str) -> str:
    """Deterministic ``urn:evitrace:anno:<uuid5>`` id for one sentence.

    Derived only from stable content and position -- the paper-scoped
    ``document_source``, the page, the occurrence counter (distinguishes
    duplicate sentence text) and the sentence text itself. No wall-clock,
    file-system path or random input (requirement 3.4).
    """
    name = f"{document_source}\x1f{page_index}\x1f{occurrence}\x1f{text}"
    return f"urn:evitrace:anno:{uuid.uuid5(_ANNO_NAMESPACE, name)}"


def generate_w3c_jsonld(
    records: list[AnnotationRecord],
    base_uri: str = "",
) -> list[dict]:
    """Serialize annotation records into W3C JSON-LD dicts.

    This is the **sole** producer of W3C JSON-LD dicts in EviTrace.
    Each returned dict contains the five required W3C Web Annotation keys:
    ``@context``, ``id``, ``type``, ``body``, and ``target``.

    Parameters
    ----------
    records:
        Annotation records produced by :func:`project`.
    base_uri:
        Optional document URI used as the annotation target source **and**
        as the document component of every annotation id, so the same
        sentence in two papers gets two ids. Defaults to
        ``"urn:evitrace:document"`` when empty. The pipeline always passes
        a paper-scoped value (``urn:evitrace:document:<pdf_name>``).

    Returns
    -------
    list[dict]
        One W3C JSON-LD annotation dict per record. Annotation ids are
        name-based (:func:`_annotation_id`): serializing equal records with
        the same ``base_uri`` twice yields byte-identical ids.
        Returns ``[]`` when ``records`` is empty — never raises.
    """
    result: list[dict] = []
    document_source = base_uri if base_uri else "urn:evitrace:document"

    for rec in records:
        anno_id = _annotation_id(document_source, rec.page_index, rec.occurrence, rec.sentence_text)

        selectors: list[dict] = []
        if rec.selector_type == "TextPositionSelector":
            selectors.append(
                {
                    "type": "TextPositionSelector",
                    "start": rec.selector_payload["start"],
                    "end": rec.selector_payload["end"],
                }
            )
        elif rec.selector_type == "FragmentSelector":
            selectors.append(
                {
                    "type": "FragmentSelector",
                    "conformsTo": "http://www.w3.org/TR/media-frags/",
                    "value": (
                        f"page={rec.selector_payload['page']}"
                        f"&xywh={rec.selector_payload['xywh']}"
                    ),
                }
            )
        # "TextQuoteSelector" records (no region) carry the quote selector only.
        selectors.append({"type": "TextQuoteSelector", **rec.quote_selector})

        target = {"source": document_source, "selector": selectors}
        # The body marking always mirrors the record (and therefore the
        # sentence), regardless of which region selector was chosen.
        body: dict = {
            "type": "TextualBody",
            "value": rec.body_value,
            "format": "text/plain",
            "ocr_derived": bool(rec.ocr_derived),
        }

        result.append(
            {
                "@context": W3C_ANNO_CONTEXT,
                "id": anno_id,
                "type": "Annotation",
                "body": body,
                "target": target,
            }
        )

    return result
