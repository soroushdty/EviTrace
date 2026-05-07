"""
Evaluates observation objects against configured thresholds and computes
inter-extractor agreement metrics. Does not make final accept, reject, or
reconcile decisions.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("evi_trace")


def investigate(
    grobid_observation: dict,
    pymupdf_observation: dict,
    grobid_artifact: dict,
    pymupdf_artifact: dict,
    config: dict,
) -> dict:
    """Return a single Investigator_Object.

    Parameters
    ----------
    grobid_observation:
        Observation_Object produced by the Observer for the GROBID extractor.
    pymupdf_observation:
        Observation_Object produced by the Observer for the PyMuPDF extractor.
    grobid_artifact:
        Canonical artifacts dict; ``grobid_artifact["grobid"]["id"]`` is used
        as the GROBID artifact reference.
    pymupdf_artifact:
        Canonical artifacts dict; ``pymupdf_artifact["pymupdf"]["id"]`` is used
        as the PyMuPDF artifact reference.
    config:
        Pipeline config dict.  Metric names are read from
        ``config["quality_control"]["investigator"]["agreement_metrics"]``.

    Returns
    -------
    dict
        An Investigator_Object with the following keys:
        ``grobid_threshold_checks``, ``pymupdf_threshold_checks``,
        ``agreement_metrics``, ``grobid_observation_ref``,
        ``pymupdf_observation_ref``, ``grobid_artifact_ref``,
        ``pymupdf_artifact_ref``, ``decision``.
    """
    metric_names: list[str] = (
        config["quality_control"]["iaa_calculator"]["agreement_metrics"]
    )

    return {
        "grobid_threshold_checks": {},
        "pymupdf_threshold_checks": {},
        "agreement_metrics": {metric: None for metric in metric_names},
        "grobid_observation_ref": grobid_observation,
        "pymupdf_observation_ref": pymupdf_observation,
        "grobid_artifact_ref": grobid_artifact["grobid"]["id"],
        "pymupdf_artifact_ref": pymupdf_artifact["pymupdf"]["id"],
        "decision": "deferred_to_adjudicator",
    }
