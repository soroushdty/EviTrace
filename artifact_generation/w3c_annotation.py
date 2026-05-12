"""
Sole producer of W3C JSON-LD annotation dicts.

This module is the ONLY place in EviTrace that constructs W3C JSON-LD
annotation dicts. All other modules must call ``generate_w3c_jsonld()``
rather than building annotation dicts themselves.

Functions
---------
generate_w3c_jsonld(records, base_uri="")
    Serialize a list of AnnotationRecord instances into W3C JSON-LD dicts.
"""

from __future__ import annotations

import uuid

from pdf_extractor.annotation.w3c_annotation import AnnotationRecord

W3C_ANNO_CONTEXT = "http://www.w3.org/ns/anno.jsonld"


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
        Annotation records produced by :func:`pdf_extractor.annotation.project`.
    base_uri:
        Optional document URI used as the annotation target source.
        Defaults to ``"urn:evitrace:document"`` when empty.

    Returns
    -------
    list[dict]
        One W3C JSON-LD annotation dict per record.
        Returns ``[]`` when ``records`` is empty — never raises.
    """
    result: list[dict] = []
    document_source = base_uri if base_uri else "urn:evitrace:document"

    for rec in records:
        anno_id = f"urn:evitrace:anno:{uuid.uuid4()}"

        if rec.selector_type == "TextPositionSelector":
            target = {
                "source": document_source,
                "selector": [
                    {
                        "type": "TextPositionSelector",
                        "start": rec.selector_payload["start"],
                        "end": rec.selector_payload["end"],
                    },
                    {
                        "type": "TextQuoteSelector",
                        **rec.quote_selector,
                    },
                ],
            }
            body: dict = {
                "type": "TextualBody",
                "value": rec.body_value,
                "format": "text/plain",
            }
        else:
            # FragmentSelector (scanned / OCR-derived)
            target = {
                "source": document_source,
                "selector": [
                    {
                        "type": "FragmentSelector",
                        "conformsTo": "http://www.w3.org/TR/media-frags/",
                        "value": (
                            f"page={rec.selector_payload['page']}"
                            f"&xywh={rec.selector_payload['xywh']}"
                        ),
                    },
                    {
                        "type": "TextQuoteSelector",
                        **rec.quote_selector,
                    },
                ],
            }
            body = {
                "type": "TextualBody",
                "value": rec.body_value,
                "format": "text/plain",
                "ocr_derived": True,
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
