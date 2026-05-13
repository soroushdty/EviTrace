"""
Extraction artifact serialization — converts QCBundle to JSON-serializable format.

This module handles serialization of extraction results (QCBundle) to artifact
dicts suitable for JSON export. This is the sole producer of extraction artifacts
(as opposed to canonical artifacts or W3C annotations).

Functions
---------
unified_to_artifact(pdf_name, pdf_info, ctx)
    Serialize a QCBundle into a JSON-serializable artifact dict.
save_artifact(output_folder, pdf_name, artifact)
    Write a single extraction artifact JSON file and return its path.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path


def unified_to_artifact(pdf_name: str, pdf_info: dict, ctx) -> dict:
    """Serialize a QCBundle into a JSON-serializable artifact dict.

    Args:
        pdf_name: Name of the PDF file
        pdf_info: Dict with 'id' and 'uri' keys
        ctx: QCBundle context object

    Returns:
        dict: JSON-serializable artifact containing all extraction results
    """
    unified = ctx.unified
    if unified is None:
        return {
            "pdf_name": pdf_name,
            "pdf_id": pdf_info.get("id"),
            "pdf_uri": pdf_info.get("uri"),
            "status": "no_unified_record",
            "branches": [
                {"source": b.source, "index": b.index, "status": b.status}
                for b in ctx.branches
            ],
        }

    def _safe_asdict(obj):
        try:
            return asdict(obj) if obj is not None else None
        except Exception:
            return None

    return {
        "pdf_name": pdf_name,
        "pdf_id": pdf_info.get("id"),
        "pdf_uri": pdf_info.get("uri"),
        "document_id": unified.document_id,
        "content": unified.content,
        "semantic": _safe_asdict(unified.semantic),
        "structural": _safe_asdict(unified.structural),
        "alignment": _safe_asdict(unified.alignment),
        "branches": [
            {"source": b.source, "index": b.index, "status": b.status}
            for b in ctx.branches
        ],
        "metrics_hierarchy": ctx.metrics_hierarchy,
    }


def save_artifact(output_folder: str, pdf_name: str, artifact: dict) -> str:
    """Write a single extraction artifact JSON file and return its path.

    Args:
        output_folder: Directory to write artifact to
        pdf_name: Name of PDF file (used to derive artifact filename)
        artifact: Artifact dict to serialize

    Returns:
        str: Path to written artifact file
    """
    stem = Path(pdf_name).stem
    out_path = Path(output_folder) / f"{stem}.extracted.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False, default=str)
    return str(out_path)
