"""
Pipeline-level tests for extractor identity in QC reports, IAA, and adjudication.

Feature: risk-remediation, task 2.1 (ExtractorIdentity).
Requirements: 9.1, 9.2, 9.3, 9.4

Exercises ``run_quality_control`` with three named branches and asserts that
the identity of each extractor survives from ``Candidate.source`` through the
``ExtractionCoverageReport`` into the pairwise agreement keys and the
adjudication decision.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_text_processor_tokenize(monkeypatch):
    """Mock scispacy/spacy AND tokenize_sentences for deterministic output."""
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)
    monkeypatch.setattr(
        "text_processing.composite.DefaultTextProcessor.tokenize_sentences",
        lambda self, text: text.split(". "),
    )


from quality_control import Candidate  # noqa: E402
from quality_control.quality_control import _build_local_metrics_report, run_quality_control  # noqa: E402


_SOURCES = ("grobid", "pdfplumber", "paddleocr")
_TEXT = "Hello world. Second sentence here"


def _make_minimal_config() -> dict:
    return {
        "quality_control": {
            "artifact_generator": {"export_to_disk": False, "output_dir": "output/qc_artifacts"},
            "rater": {"attributes": []},
            "iaa_calculator": {"thresholds": {}, "agreement_metrics": []},
            "adjudicator": {"strategy": "placeholder"},
            "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
        }
    }


def _make_three_branches() -> list[Candidate]:
    return [
        Candidate(
            source="grobid",
            index=0,
            payload=f"<TEI><text><body><p>{_TEXT}</p></body></text></TEI>",
            status=None,
        ),
        Candidate(
            source="pdfplumber",
            index=1,
            payload={"blocks": [{"text": _TEXT, "page_index": 0}]},
            status=None,
        ),
        Candidate(
            source="paddleocr",
            index=2,
            payload={"blocks": [{"text": _TEXT, "page_index": 0}]},
            status=None,
        ),
    ]


def test_build_local_metrics_report_carries_branch_source_and_index():
    """Requirement 9.3 — design postcondition: result.source == branch.source, result.index == branch_index."""
    branches = _make_three_branches()
    for branch_index, branch in enumerate(branches):
        report = _build_local_metrics_report(
            branch, branches, branch_index, _make_minimal_config(), text_processor=None
        )
        assert report.source == branch.source
        assert report.extractor == branch.source
        assert report.index == branch_index


def test_three_branch_run_reports_are_named_by_source():
    """Requirement 9.3 — every report emitted by run_quality_control carries its branch's source."""
    ctx = run_quality_control(_make_three_branches(), "identity-doc", _make_minimal_config())

    assert [r.source for r in ctx.reports] == list(_SOURCES)
    assert [r.index for r in ctx.reports] == [0, 1, 2]


def test_three_branch_run_yields_three_source_named_pairwise_keys():
    """Requirements 9.1, 9.4 — pairwise has N·(N−1)/2 keys of the form {a}_vs_{b} named by source."""
    ctx = run_quality_control(_make_three_branches(), "identity-doc", _make_minimal_config())

    pairwise = ctx.iaa_metrics.pairwise
    assert set(pairwise) == {
        "grobid_vs_pdfplumber",
        "grobid_vs_paddleocr",
        "pdfplumber_vs_paddleocr",
    }
    assert len(pairwise) == 3 * (3 - 1) // 2
    # No positional-only identity leaks into the keys (9.4).
    for key in pairwise:
        left, right = key.split("_vs_")
        assert not left.isdigit() and not right.isdigit()
        assert left and right


def test_three_branch_run_adjudicates_a_real_source_name():
    """Requirement 9.2 — primary_extractor is one of the branch sources and the rationale names it."""
    ctx = run_quality_control(_make_three_branches(), "identity-doc", _make_minimal_config())

    primary = ctx.decision.primary_extractor
    assert primary in _SOURCES
    assert primary in ctx.decision.rationale
    assert ctx.unified.content["provenance"]["adjudication_decisions"]["primary_extractor"] == primary
