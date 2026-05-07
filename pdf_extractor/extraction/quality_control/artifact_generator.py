"""
Sole producer of canonical artifacts. Canonical artifacts are always generated
in memory; disk export is optional and config-controlled via
quality_control.artifacts.export_to_disk.

This module exposes four public functions:
  - canonicalize_grobid_xml: deterministic UTF-8 XML string from TEI XML input
  - canonicalize_pymupdf_json: deterministic UTF-8 JSON string from PyMuPDF dict/list
  - build_canonical_artifacts: builds the canonical artifacts dict (always in-memory)
  - export_canonical_artifacts: writes canonical artifacts to disk (called only when
    export_to_disk is True in the pipeline config)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import xml.etree.ElementTree as ET

logger = logging.getLogger("evi_trace")


def canonicalize_grobid_xml(tei_xml_str: str) -> str:
    """Return a deterministic UTF-8 XML string from a TEI XML input.

    Parses the input with xml.etree.ElementTree and re-serializes with
    ET.tostring(..., encoding="unicode"). Attributes are sorted by key
    (ElementTree does this by default in Python 3.8+). No timestamps or
    processing instructions are injected.

    Pure function — no I/O, no side effects.
    """
    root = ET.fromstring(tei_xml_str)
    return ET.tostring(root, encoding="unicode")


def canonicalize_pymupdf_json(pymupdf_dict: dict | list) -> str:
    """Return a deterministic UTF-8 JSON string (sorted keys, consistent indent).

    Uses json.dumps with sort_keys=True, indent=2, ensure_ascii=False to
    produce a stable, human-readable representation.

    Pure function — no I/O, no side effects.
    """
    return json.dumps(pymupdf_dict, sort_keys=True, indent=2, ensure_ascii=False)


def build_canonical_artifacts(
    grobid_output: str,
    pymupdf_output: dict | list,
    document_id: str,
) -> dict:
    """Build and return the canonical artifacts dict (always in-memory).

    Calls both canonicalize functions, computes SHA-256 content IDs, and
    returns the canonical artifacts dict. This function always runs regardless
    of the export_to_disk config setting — disk export is a separate concern
    handled by export_canonical_artifacts.

    Returns:
        {
            "document_id": str,
            "grobid": {
                "id": str,          # sha256 of canonical XML
                "content": str,     # canonical UTF-8 XML string
                "format": "tei_xml",
            },
            "pymupdf": {
                "id": str,          # sha256 of canonical JSON
                "content": str,     # canonical UTF-8 JSON string
                "format": "json",
            },
        }
    """
    canonical_xml = canonicalize_grobid_xml(grobid_output)
    canonical_json = canonicalize_pymupdf_json(pymupdf_output)

    grobid_id = hashlib.sha256(canonical_xml.encode("utf-8")).hexdigest()
    pymupdf_id = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    return {
        "document_id": document_id,
        "grobid": {
            "id": grobid_id,
            "content": canonical_xml,
            "format": "tei_xml",
        },
        "pymupdf": {
            "id": pymupdf_id,
            "content": canonical_json,
            "format": "json",
        },
    }


def export_canonical_artifacts(artifacts: dict, output_dir: str) -> None:
    """Write canonical artifacts to disk.

    Creates output_dir if it does not exist. Writes:
      - <document_id>_grobid.xml
      - <document_id>_pymupdf.json

    This function is only called by the pipeline when export_to_disk is True.
    """
    os.makedirs(output_dir, exist_ok=True)

    document_id = artifacts["document_id"]

    grobid_path = os.path.join(output_dir, f"{document_id}_grobid.xml")
    with open(grobid_path, "w", encoding="utf-8") as f:
        f.write(artifacts["grobid"]["content"])
    logger.debug("Wrote GROBID canonical artifact: %s", grobid_path)

    pymupdf_path = os.path.join(output_dir, f"{document_id}_pymupdf.json")
    with open(pymupdf_path, "w", encoding="utf-8") as f:
        f.write(artifacts["pymupdf"]["content"])
    logger.debug("Wrote PyMuPDF canonical artifact: %s", pymupdf_path)
