"""
artifact_generation — centralized artifact production for EviTrace.

This package is the sole producer of all artifacts in EviTrace:
  - Canonical artifacts (GROBID XML, PyMuPDF JSON) with deterministic serialization
  - W3C JSON-LD annotations for extracted content
  - CSV exports of extraction data
  - Extraction artifacts (serialized QCBundle results)

Public API
----------
Canonical artifact generation:
  - canonicalize_grobid_xml
  - canonicalize_pymupdf_json
  - build_canonical_artifacts
  - export_canonical_artifacts

W3C annotation generation:
  - AnnotationRecord
  - project
  - generate_w3c_jsonld

CSV export:
  - extract_to_csv
  - process_folder

Extraction artifact serialization:
  - unified_to_artifact
  - save_artifact
"""

from __future__ import annotations

# Canonical artifacts
from artifact_generation.canonical import (
  canonicalize_grobid_xml,
  canonicalize_pymupdf_json,
  build_canonical_artifacts,
  export_canonical_artifacts,
)


from artifact_generation.w3c_annotation import AnnotationRecord


def generate_w3c_jsonld(*args, **kwargs):
  from artifact_generation.w3c_annotation import generate_w3c_jsonld as _generate_w3c_jsonld

  return _generate_w3c_jsonld(*args, **kwargs)


def project(*args, **kwargs):
  from artifact_generation.w3c_annotation import project as _project

  return _project(*args, **kwargs)


# CSV export
from artifact_generation.csv_exporter import (
  extract_to_csv,
  export_all_extracted_jsons_to_csv,
  process_folder,
)

# Extraction artifacts
from artifact_generation.extraction_artifact import (
  unified_to_artifact,
  save_artifact,
)

__all__ = [
    # Canonical
    "canonicalize_grobid_xml",
    "canonicalize_pymupdf_json",
    "build_canonical_artifacts",
    "export_canonical_artifacts",
    # W3C
    "AnnotationRecord",
    "project",
    "generate_w3c_jsonld",
    # CSV
    "extract_to_csv",
    "export_all_extracted_jsons_to_csv",
    "process_folder",
    # Extraction
    "unified_to_artifact",
    "save_artifact",
]
