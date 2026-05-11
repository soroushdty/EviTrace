"""
Generates one QualityReport per extractor branch. Does not produce canonical
artifacts and does not call any Artifact_Generator functions.
"""

from __future__ import annotations

import logging

from quality_control.models import BranchOutput, QualityReport

logger = logging.getLogger("pdf_extractor")


def observe(branch: BranchOutput, config: dict) -> QualityReport:
    """Return a single QualityReport for the given extractor branch.

    Parameters
    ----------
    branch:
        The extractor branch to rate.  ``branch.extractor`` and
        ``branch.branch`` are used directly to populate the report.
    config:
        Pipeline config dict.  Attribute names are read from
        ``config["quality_control"]["rater"]["attributes"]``.

    Returns
    -------
    QualityReport
        A QualityReport with ``extractor`` and ``branch`` populated from the
        input branch, and ``status`` set to ``None`` (not yet adjudicated).
    """
    report = QualityReport(
        extractor=branch.extractor,
        branch=branch.branch,
        status=None,
    )
    return report
