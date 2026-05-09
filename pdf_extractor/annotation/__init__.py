"""
pdf_extractor/annotation/__init__.py
-------------------------------------
Public API for the W3C annotation layer.

Exports
-------
AnnotationRecord
    Dataclass for a single projected annotation record.
project
    Project a UnifiedRecord into a list of AnnotationRecord instances.
generate_w3c_jsonld
    Sole producer of W3C JSON-LD annotation dicts.
"""

from pdf_extractor.annotation.artifact_generator import generate_w3c_jsonld
from pdf_extractor.annotation.w3c_annotation import AnnotationRecord, project

__all__ = ["AnnotationRecord", "generate_w3c_jsonld", "project"]
