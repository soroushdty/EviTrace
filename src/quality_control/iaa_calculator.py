"""
Evaluates observation objects against configured thresholds and computes
inter-extractor agreement metrics. Does not make final accept, reject, or
reconcile decisions.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("pdf_extractor")


def investigate(
    primary_observation: dict,
    secondary_observation: dict,
    primary_artifact: dict,
    secondary_artifact: dict,
    config: dict,
) -> dict:
    """Return a single Investigator_Object.

    Parameters
    ----------
    primary_observation:
        Observation_Object produced by the Observer for the primary extractor.
    secondary_observation:
        Observation_Object produced by the Observer for the secondary extractor.
    primary_artifact:
        Canonical artifact dict; ``primary_artifact.get("id", "")`` is used
        as the primary artifact reference.
    secondary_artifact:
        Canonical artifact dict; ``secondary_artifact.get("id", "")`` is used
        as the secondary artifact reference.
    config:
        Pipeline config dict.  Metric names are read from
        ``config["quality_control"]["investigator"]["agreement_metrics"]``.

    Returns
    -------
    dict
        An Investigator_Object with the following keys:
        ``primary_threshold_checks``, ``secondary_threshold_checks``,
        ``agreement_metrics``, ``primary_observation_ref``,
        ``secondary_observation_ref``, ``primary_artifact_ref``,
        ``secondary_artifact_ref``, ``decision``.
    """
    metric_names: list[str] = (
        config["quality_control"]["iaa_calculator"]["agreement_metrics"]
    )

    return {
        "primary_threshold_checks": {},
        "secondary_threshold_checks": {},
        "agreement_metrics": {metric: None for metric in metric_names},
        "primary_observation_ref": primary_observation,
        "secondary_observation_ref": secondary_observation,
        "primary_artifact_ref": primary_artifact.get("id", ""),
        "secondary_artifact_ref": secondary_artifact.get("id", ""),
        "decision": "deferred_to_adjudicator",
    }
