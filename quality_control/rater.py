"""
Generates one QualityReport per candidate. Does not produce canonical
artifacts and does not call any Artifact_Generator functions.
"""

from __future__ import annotations

import logging

from quality_control.models import Candidate
from quality_control.defaults import QualityReport

logger = logging.getLogger("pdf_extractor")


def observe(candidate: Candidate, config: dict) -> QualityReport:
    """Return a single QualityReport for the given candidate.

    Parameters
    ----------
    candidate:
        The candidate to rate.  ``candidate.source`` and ``candidate.index``
        are used directly to populate the report.
    config:
        Pipeline config dict.  Attribute names are read from
        ``config["quality_control"]["rater"]["attributes"]``.

    Returns
    -------
    QualityReport
        A QualityReport with ``source`` and ``index`` populated from the
        input candidate, and ``status`` set to ``None`` (not yet adjudicated).
    """
    report = QualityReport(
        source=candidate.source,
        index=candidate.index,
        status=None,
    )
    return report
