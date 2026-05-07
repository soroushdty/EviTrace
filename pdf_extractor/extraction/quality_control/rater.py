"""
Generates one Observation_Object per extractor. Does not produce canonical
artifacts and does not call any Artifact_Generator functions.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("evi_trace")


def observe(
    extractor_name: str,
    canonical_artifact: dict,
    document_id: str,
    config: dict,
) -> dict:
    """Return a single Observation_Object for the named extractor.

    Parameters
    ----------
    extractor_name:
        One of ``"grobid"`` or ``"pymupdf"`` — selects the sub-dict from
        *canonical_artifact* that provides provenance information.
    canonical_artifact:
        The full canonical artifacts dict produced by
        ``artifacts.build_canonical_artifacts``.  Must contain a key matching
        *extractor_name* with ``"id"`` and ``"format"`` sub-keys.
    document_id:
        Stable document identifier forwarded into the observation object.
    config:
        Pipeline config dict.  Attribute names are read from
        ``config["quality_control"]["observer"]["attributes"]``.

    Returns
    -------
    dict
        An Observation_Object with the following keys:
        ``extractor_name``, ``document_id``, ``attributes``, ``status``,
        ``provenance``.
    """
    attribute_names: list[str] = config["quality_control"]["rater"]["attributes"]
    artifact_sub = canonical_artifact[extractor_name]

    return {
        "extractor_name": extractor_name,
        "document_id": document_id,
        "attributes": {attr_name: None for attr_name in attribute_names},
        "status": "placeholder",
        "provenance": {
            "artifact_id": artifact_sub["id"],
            "artifact_format": artifact_sub["format"],
        },
    }
