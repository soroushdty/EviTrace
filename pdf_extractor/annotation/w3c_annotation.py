"""
pdf_extractor/annotation/w3c_annotation.py
------------------------------------------
W3C annotation data model and projection function.

Reads ONLY from UnifiedRecord.semantic and UnifiedRecord.alignment —
never reads raw extractor output directly.

Classes
-------
AnnotationRecord
    Dataclass holding the projected annotation fields for a single sentence.

Functions
---------
project(unified, base_uri="")
    Project a UnifiedRecord into a list of AnnotationRecord instances.
    Returns list[AnnotationRecord] — no JSON serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
        ``"TextPositionSelector"`` for born-digital pages;
        ``"FragmentSelector"`` for OCR/scanned pages.
    selector_payload:
        For TextPositionSelector: ``{"start": int, "end": int}``
        For FragmentSelector:     ``{"page": int, "xywh": str}``
    quote_selector:
        ``{"exact": str, "prefix": str, "suffix": str}`` — always populated.
    ocr_derived:
        True when this record originates from an OCR backend.
    body_value:
        Annotation body text (typically same as sentence_text).
    """

    sentence_text: str
    page_index: int
    selector_type: str
    selector_payload: dict
    quote_selector: dict
    ocr_derived: bool = False
    body_value: str = ""


def project(unified: Any, base_uri: str = "") -> list[AnnotationRecord]:  # noqa: ARG001
    """Project a UnifiedRecord into annotation records.

    Reads only from ``unified.semantic`` and ``unified.alignment``.
    For scanned sentences also consults ``unified.structural.blocks`` to
    obtain bounding boxes, but never reads raw extractor payloads.

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
        One record per sentence in the semantic layer.
        Returns an empty list when either ``semantic`` or ``alignment`` is
        absent.
    """
    records: list[AnnotationRecord] = []

    if unified.alignment is None or unified.semantic is None:
        return records

    sentences = unified.semantic.sentences  # list of sentence dicts
    alignment = unified.alignment

    # Build a lookup: sentence text → (start, end) from the list of dicts
    char_range_lookup: dict[str, tuple[int, int]] = {}
    for entry in alignment.sentence_to_char_range:
        text = entry.get("sentence", "")
        start = entry.get("start", 0)
        end = entry.get("end", 0)
        char_range_lookup[text] = (start, end)

    # Full concatenated text for TextQuoteSelector context
    full_text = " ".join(s.get("text", "") for s in sentences)

    for sent_dict in sentences:
        sent_text: str = sent_dict.get("text", "")
        page_idx: int = sent_dict.get("page_index", 0)
        ocr_derived: bool = bool(sent_dict.get("ocr_derived", False))

        # Build TextQuoteSelector
        idx = full_text.find(sent_text)
        prefix = full_text[max(0, idx - 20) : idx] if idx >= 0 else ""
        suffix = (
            full_text[idx + len(sent_text) : idx + len(sent_text) + 20]
            if idx >= 0
            else ""
        )
        quote_selector = {"exact": sent_text, "prefix": prefix, "suffix": suffix}

        if not ocr_derived:
            # TextPositionSelector — character offsets from alignment map
            char_range = char_range_lookup.get(sent_text, (0, 0))
            selector_payload = {"start": char_range[0], "end": char_range[1]}
            selector_type = "TextPositionSelector"
        else:
            # FragmentSelector — bounding box from structural blocks
            ocr_block = None
            if unified.structural is not None:
                for block in unified.structural.blocks:
                    if block.get("page_index") == page_idx:
                        ocr_block = block
                        break
            bbox = (
                ocr_block.get("block_bbox", (0, 0, 0, 0))
                if ocr_block is not None
                else (0, 0, 0, 0)
            )
            xywh = f"{bbox[0]},{bbox[1]},{bbox[2] - bbox[0]},{bbox[3] - bbox[1]}"
            selector_payload = {"page": page_idx, "xywh": xywh}
            selector_type = "FragmentSelector"

        records.append(
            AnnotationRecord(
                sentence_text=sent_text,
                page_index=page_idx,
                selector_type=selector_type,
                selector_payload=selector_payload,
                quote_selector=quote_selector,
                ocr_derived=ocr_derived,
                body_value=sent_text,
            )
        )

    return records
