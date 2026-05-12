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

from pdf_extractor.annotation.w3c_annotation import AnnotationRecord, project


def generate_w3c_jsonld(*args, **kwargs):
    from artifact_generation.w3c_annotation import generate_w3c_jsonld as _generate_w3c_jsonld

    return _generate_w3c_jsonld(*args, **kwargs)

__all__ = ["AnnotationRecord", "generate_w3c_jsonld", "project"]
