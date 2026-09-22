"""
Branch-role selection in the QC reconciler callback.

Feature: risk-remediation, task 3.1 (BranchRoleSelector).
Requirements: 2.1, 2.2, 2.3, 2.4

``_pdf_reconciler_fn`` must derive primary/secondary branches from the
adjudication decision (not from hard-coded extractor names), fall back to
index order with a WARNING when nothing matches, and record the branches it
actually used in the output provenance.  The done condition for task 3.1 is a
mixed run whose second branch is named ``paddleocr`` yielding a non-empty
structural layer.  Task 3.2 adds the fuller matrix.
"""

from __future__ import annotations

import logging
import sys
from types import SimpleNamespace
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
from quality_control.quality_control import _select_branch_roles, run_quality_control  # noqa: E402


_TEXT = "Hello world. Second sentence here"
_OCR_BBOX = [10.0, 20.0, 300.0, 40.0]

_FALLBACK_WARNING = (
    "adjudicated primary extractor 'tesseract' not among branches "
    "['grobid', 'paddleocr']; falling back to index order"
)


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


def _grobid_branch(index: int = 0) -> Candidate:
    return Candidate(
        source="grobid",
        index=index,
        payload=f"<TEI><text><body><p>{_TEXT}</p></body></text></TEI>",
        status=None,
    )


def _paddleocr_branch(index: int = 1) -> Candidate:
    return Candidate(
        source="paddleocr",
        index=index,
        payload={
            "blocks": [
                {
                    "text": _TEXT,
                    "page_index": 0,
                    "block_bbox": _OCR_BBOX,
                    "span_bboxes": [],
                    "ocr_derived": True,
                    "source": "paddleocr",
                }
            ]
        },
        status=None,
    )


# ---------------------------------------------------------------------------
# Done condition: mixed run grobid + paddleocr keeps the OCR structural layer
# ---------------------------------------------------------------------------


def test_mixed_run_with_paddleocr_secondary_yields_non_empty_structural_layer():
    """Requirements 2.1, 2.2, 2.4 — task 3.1 done condition.

    Before 3.1 the secondary lookup only accepted ``pdfplumber``/``pymupdf``, so
    a ``paddleocr`` branch was dropped and ``structural.blocks`` was ``[]``.
    """
    branches = [_grobid_branch(0), _paddleocr_branch(1)]

    ctx = run_quality_control(branches, "mixed-doc", _make_minimal_config())

    assert ctx.decision.primary_extractor == "grobid"

    structural = ctx.unified.structural
    assert structural is not None
    assert structural.blocks, "paddleocr secondary must populate structural.blocks"
    assert structural.blocks[0]["block_bbox"] == _OCR_BBOX, "OCR bbox must survive into the structural layer"

    provenance = ctx.unified.content["provenance"]
    assert provenance["primary_branch_source"] == "grobid"
    assert provenance["secondary_branch_source"] == "paddleocr"
    assert provenance["branch_selection"] == "adjudicated"
    # 2.4: the adjudication rationale still sits beside the new keys.
    assert provenance["adjudication_decisions"]["primary_extractor"] == "grobid"
    assert provenance["adjudication_decisions"]["rationale"]


def test_single_branch_run_has_no_secondary_and_records_it():
    """Requirements 2.2, 2.4 — secondary is None only when a single branch exists."""
    ctx = run_quality_control([_grobid_branch(0)], "single-doc", _make_minimal_config())

    provenance = ctx.unified.content["provenance"]
    assert provenance["primary_branch_source"] == "grobid"
    assert provenance["secondary_branch_source"] is None
    assert provenance["branch_selection"] == "adjudicated"
    assert ctx.unified.structural is not None
    assert ctx.unified.structural.blocks == []


# ---------------------------------------------------------------------------
# _select_branch_roles unit contract
# ---------------------------------------------------------------------------


def test_select_branch_roles_adjudicated_match_by_primary_extractor():
    """Requirements 2.1, 2.2 — primary keys only on decision.primary_extractor."""
    grobid, ocr = _grobid_branch(0), _paddleocr_branch(1)
    decision = SimpleNamespace(primary_extractor="paddleocr", confidence=0.5, rationale="r")

    primary, secondary, mode = _select_branch_roles(decision, [grobid, ocr])

    assert primary is ocr
    assert secondary is grobid
    assert mode == "adjudicated"


def test_select_branch_roles_falls_back_to_index_order_with_warning(caplog):
    """Requirement 2.3 — no match: all_branches[0] is primary and the design's WARNING is logged."""
    grobid, ocr = _grobid_branch(0), _paddleocr_branch(1)
    decision = SimpleNamespace(primary_extractor="tesseract", confidence=0.0, rationale="r")

    with caplog.at_level(logging.WARNING, logger="pdf_extractor"):
        primary, secondary, mode = _select_branch_roles(decision, [grobid, ocr])

    assert primary is grobid
    assert secondary is ocr
    assert mode == "fallback_index_order"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(r.getMessage() == _FALLBACK_WARNING for r in warnings), [
        r.getMessage() for r in warnings
    ]


def test_select_branch_roles_single_branch_has_no_secondary():
    """Requirement 2.2 — secondary is None only for a single branch."""
    ocr = _paddleocr_branch(0)
    decision = SimpleNamespace(primary_extractor="paddleocr", confidence=1.0, rationale="r")

    primary, secondary, mode = _select_branch_roles(decision, [ocr])

    assert primary is ocr
    assert secondary is None
    assert mode == "adjudicated"
