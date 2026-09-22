"""
Unit tests for extractor-name resolution in the built-in IAA and adjudication
implementations (``quality_control/builtin_impls/``).

Feature: risk-remediation, task 2.1 (ExtractorIdentity).
Requirements: 9.1, 9.2, 9.5

The built-ins must key on the report's extractor name and, when that name is
empty or absent, fall back to the documented identifier: the zero-based report
position as a string (``str(i)``).
"""

from __future__ import annotations

from quality_control.builtin_impls import AdjudicationDecision, InterRaterReport, QualityReport


def _report(source: str, status: str, index: int = 0) -> QualityReport:
    return QualityReport(status=status, source=source, index=index)


# ---------------------------------------------------------------------------
# Named reports (9.1, 9.2)
# ---------------------------------------------------------------------------


def test_inter_rater_report_keys_pairwise_by_source_name():
    """Requirement 9.1 — pairwise keys use the reports' extractor names."""
    reports = [_report("grobid", "pass", 0), _report("pdfplumber", "fail", 1), _report("paddleocr", "pass", 2)]
    iaa = InterRaterReport()
    iaa.compute(reports)

    assert iaa.pairwise == {
        "grobid_vs_pdfplumber": 0.0,
        "grobid_vs_paddleocr": 1.0,
        "pdfplumber_vs_paddleocr": 0.0,
    }


def test_adjudication_decision_picks_a_named_extractor():
    """Requirement 9.2 — the recorded primary is a source name and the rationale names it."""
    reports = [_report("grobid", "pass", 0), _report("pdfplumber", "fail", 1), _report("paddleocr", "pass", 2)]
    decision = AdjudicationDecision()
    decision.adjudicate(reports, InterRaterReport())

    assert decision.primary_extractor in {"grobid", "paddleocr"}
    assert decision.primary_extractor in decision.rationale


# ---------------------------------------------------------------------------
# Empty names -> documented positional fallback (9.5)
# ---------------------------------------------------------------------------


def test_inter_rater_report_falls_back_to_positional_keys_for_empty_names():
    """Requirement 9.5 — empty extractor names yield ``0_vs_1``-style keys, one per pair."""
    reports = [_report("", "pass"), _report("", "pass"), _report("", "fail")]
    iaa = InterRaterReport()
    iaa.compute(reports)

    assert iaa.pairwise == {"0_vs_1": 1.0, "0_vs_2": 0.0, "1_vs_2": 0.0}


def test_adjudication_decision_falls_back_to_positional_name_for_empty_names():
    """Requirement 9.5 — adjudication over unnamed reports still returns a usable identifier."""
    reports = [_report("", "fail"), _report("", "pass"), _report("", "fail")]
    decision = AdjudicationDecision()
    decision.adjudicate(reports, InterRaterReport())

    assert decision.primary_extractor == "1"
    assert decision.confidence == 1 / 3
    assert "1 selected" in decision.rationale


def test_builtins_fall_back_when_extractor_attribute_is_absent():
    """Requirement 9.5 — reports lacking an ``extractor`` attribute entirely also use ``str(i)``."""

    class _Bare:
        def __init__(self, status: str) -> None:
            self.status = status

    reports = [_Bare("pass"), _Bare("pass")]
    iaa = InterRaterReport()
    iaa.compute(reports)
    assert iaa.pairwise == {"0_vs_1": 1.0}

    decision = AdjudicationDecision()
    decision.adjudicate(reports, iaa)
    assert decision.primary_extractor in {"0", "1"}
