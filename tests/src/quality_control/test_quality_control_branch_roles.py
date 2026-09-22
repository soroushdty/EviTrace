"""
Branch-role selection in the QC reconciler callback.

Feature: risk-remediation, tasks 3.1 and 3.2 (BranchRoleSelector).
Requirements: 2.1, 2.2, 2.3, 2.4

``_pdf_reconciler_fn`` must derive primary/secondary branches from the
adjudication decision (not from hard-coded extractor names), fall back to
index order with a WARNING when nothing matches, and record the branches it
actually used in the output provenance.  The done condition for task 3.1 is a
mixed run whose second branch is named ``paddleocr`` yielding a non-empty
structural layer.  Task 3.2 adds the fuller end-to-end matrix below it: an
adjudicated non-GROBID primary, an all-scanned single-branch run, the
unmatched-primary fallback through ``run_quality_control``, and a multi-page
``paddleocr`` secondary carried whole into the structural layer.
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


# ---------------------------------------------------------------------------
# Task 3.2 — fuller matrix through ``run_quality_control`` end-to-end
# ---------------------------------------------------------------------------

# A clean OCR page: names every default expected section, stays above the
# per-page character floor, and carries no reference markers or odd glyphs,
# so it triggers none of the eight rater metrics.
_CLEAN_OCR_TEXT = (
    "abstract introduction methods results the scanned page reads clearly. "
    "This second sentence keeps the OCR page above the per-page character floor. "
    "A third sentence pads the text further"
)

# A degraded GROBID payload: mostly replacement characters plus a leaked
# reference marker.  Against a clean OCR sibling it trips five of the eight
# rater metrics (min_chars_per_page, extraction_coverage_ratio,
# section_coverage, references_in_body, weird_char_ratio), so with the
# documented 0.5 ``max_triggered_fraction`` the branch fails and adjudication
# elects the OCR branch instead.
_DEGRADED_GROBID_TEXT = "��� [1] �"


def _ocr_block(text: str, page_index: int, bbox: list[float]) -> dict:
    return {
        "text": text,
        "page_index": page_index,
        "block_bbox": bbox,
        "span_bboxes": [],
        "ocr_derived": True,
        "source": "paddleocr",
    }


def _config_with_documented_rater_threshold() -> dict:
    """Minimal config that pins the rater's documented default pass threshold."""
    config = _make_minimal_config()
    config["quality_control"]["rater"]["max_triggered_fraction"] = 0.5
    return config


def test_adjudicated_non_grobid_primary_builds_semantic_layer_from_that_branch():
    """Requirements 2.1, 2.2, 2.4 — adjudication that prefers ``paddleocr`` is honoured.

    Before 3.1 the primary was always the branch named ``grobid`` regardless of
    the decision, so the semantic layer came from GROBID even when adjudication
    elected the OCR branch.
    """
    degraded_grobid = Candidate(
        source="grobid",
        index=0,
        payload=f"<TEI><text><body><p>{_DEGRADED_GROBID_TEXT}</p></body></text></TEI>",
        status=None,
    )
    clean_ocr = Candidate(
        source="paddleocr",
        index=1,
        payload={"blocks": [_ocr_block(_CLEAN_OCR_TEXT, 0, _OCR_BBOX)]},
        status=None,
    )

    ctx = run_quality_control(
        [degraded_grobid, clean_ocr], "ocr-wins-doc", _config_with_documented_rater_threshold()
    )

    # Precondition guard: the rater must actually fail GROBID and pass OCR so
    # the default majority-vote adjudicator elects ``paddleocr``.
    statuses = {r.extractor: r.status for r in ctx.reports}
    assert statuses == {"grobid": "fail", "paddleocr": "pass"}, statuses
    assert ctx.decision.primary_extractor == "paddleocr"

    # 2.1: primary content comes from the adjudicated branch.
    paragraph_texts = [p["text"] for p in ctx.unified.semantic.paragraphs]
    assert paragraph_texts == [_CLEAN_OCR_TEXT]
    assert all(_DEGRADED_GROBID_TEXT not in t for t in paragraph_texts)
    assert ctx.unified.semantic.sentences, "primary OCR text must yield sentences"
    assert ctx.unified.semantic.sentences[0]["text"].startswith("abstract introduction")

    # 2.2: the remaining (GROBID) branch becomes the secondary structural input.
    assert ctx.unified.structural.blocks, "grobid secondary must populate structural.blocks"
    assert ctx.unified.structural.blocks[0]["text"] == _DEGRADED_GROBID_TEXT

    # 2.4: provenance names the branches actually used and why.
    provenance = ctx.unified.content["provenance"]
    assert provenance["primary_branch_source"] == "paddleocr"
    assert provenance["secondary_branch_source"] == "grobid"
    assert provenance["branch_selection"] == "adjudicated"
    assert provenance["adjudication_decisions"]["primary_extractor"] == "paddleocr"
    assert "paddleocr" in provenance["adjudication_decisions"]["rationale"]


def test_all_scanned_single_paddleocr_branch_is_primary_without_secondary():
    """Requirements 2.1, 2.2, 2.4 — all-scanned run: one ``paddleocr`` branch.

    Before 3.1 the primary lookup only accepted ``grobid``, so an all-scanned
    run had no primary at all and the OCR text never reached the semantic layer.
    """
    ocr_only = Candidate(
        source="paddleocr",
        index=0,
        payload={"blocks": [_ocr_block(_CLEAN_OCR_TEXT, 0, _OCR_BBOX)]},
        status=None,
    )

    ctx = run_quality_control([ocr_only], "all-scanned-doc", _make_minimal_config())

    assert ctx.decision.primary_extractor == "paddleocr"

    # 2.1: the sole branch is the primary and feeds the semantic layer.
    assert [p["text"] for p in ctx.unified.semantic.paragraphs] == [_CLEAN_OCR_TEXT]
    assert ctx.unified.semantic.sentences

    # 2.2: no distinct remaining branch, so no secondary and an empty structural layer.
    assert ctx.unified.structural is not None
    assert ctx.unified.structural.blocks == []
    assert ctx.unified.alignment is not None

    # 2.4: provenance is present and records the single-branch selection.
    provenance = ctx.unified.content["provenance"]
    assert provenance["primary_branch_source"] == "paddleocr"
    assert provenance["secondary_branch_source"] is None
    assert provenance["branch_selection"] == "adjudicated"
    assert provenance["adjudication_decisions"]["primary_extractor"] == "paddleocr"
    assert provenance["adjudication_decisions"]["rationale"]


def test_run_quality_control_falls_back_to_index_order_when_adjudicated_primary_is_unmatched(
    monkeypatch, caplog
):
    """Requirements 2.3, 2.4 — unmatched adjudicated primary through the full pipeline.

    ``run_quality_control`` exposes no adjudicator injection point (its
    ``_pdf_adjudicator_fn`` closure instantiates the module-level
    ``AdjudicationDecision`` name), so the bogus decision is injected by
    monkeypatching that name in ``quality_control.quality_control``.
    """
    from quality_control import quality_control as qc_module
    from quality_control.builtin_impls import AdjudicationDecision

    class _UnmatchedDecision(AdjudicationDecision):
        def adjudicate(self, reports, metrics):  # noqa: ARG002
            self.primary_extractor = "tesseract"
            self.confidence = 0.0
            self.rationale = "tesseract selected by injected test adjudicator"

    monkeypatch.setattr(qc_module, "AdjudicationDecision", _UnmatchedDecision)

    branches = [_grobid_branch(0), _paddleocr_branch(1)]

    with caplog.at_level(logging.WARNING, logger="pdf_extractor"):
        ctx = run_quality_control(branches, "unmatched-doc", _make_minimal_config())

    assert ctx.decision.primary_extractor == "tesseract"

    # 2.3: deterministic fallback to index order, warning names the unmatched extractor.
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert _FALLBACK_WARNING in warnings, warnings

    provenance = ctx.unified.content["provenance"]
    assert provenance["primary_branch_source"] == "grobid"
    assert provenance["secondary_branch_source"] == "paddleocr"
    assert provenance["branch_selection"] == "fallback_index_order"

    # The fallback still reconciles both branches: GROBID text is primary,
    # the paddleocr blocks land in the structural layer.
    assert [p["text"] for p in ctx.unified.semantic.paragraphs] == [_TEXT]
    assert ctx.unified.structural.blocks
    assert ctx.unified.structural.blocks[0]["block_bbox"] == _OCR_BBOX

    # 2.4: the (unmatched) adjudication rationale remains auditable from the output.
    assert provenance["adjudication_decisions"]["primary_extractor"] == "tesseract"
    assert provenance["adjudication_decisions"]["rationale"] == (
        "tesseract selected by injected test adjudicator"
    )


def test_paddleocr_secondary_keeps_every_ocr_block_with_provenance_intact():
    """Requirement 2.2 — a multi-page ``paddleocr`` secondary is carried whole.

    Complements the 3.1 single-block done-condition test: every OCR block
    survives into ``structural.blocks`` in payload order with its bbox,
    ``ocr_derived`` flag and ``source`` untouched, and none of them leaks into
    the GROBID-derived semantic layer.
    """
    ocr_blocks = [
        _ocr_block("Scanned page one text", 0, [1.0, 2.0, 100.0, 12.0]),
        _ocr_block("Scanned page two text", 1, [3.0, 4.0, 120.0, 14.0]),
        _ocr_block("Scanned page three text", 2, [5.0, 6.0, 140.0, 16.0]),
    ]
    multi_page_ocr = Candidate(
        source="paddleocr", index=1, payload={"blocks": ocr_blocks}, status=None
    )

    ctx = run_quality_control(
        [_grobid_branch(0), multi_page_ocr], "multi-page-doc", _make_minimal_config()
    )

    provenance = ctx.unified.content["provenance"]
    assert provenance["primary_branch_source"] == "grobid"
    assert provenance["secondary_branch_source"] == "paddleocr"

    structural_blocks = ctx.unified.structural.blocks
    assert [b["text"] for b in structural_blocks] == [b["text"] for b in ocr_blocks]
    assert [b["page_index"] for b in structural_blocks] == [0, 1, 2]
    assert [b["block_bbox"] for b in structural_blocks] == [b["block_bbox"] for b in ocr_blocks]
    assert all(b["ocr_derived"] is True for b in structural_blocks)
    assert all(b["source"] == "paddleocr" for b in structural_blocks)

    # The OCR blocks are structural input only; the semantic layer is GROBID's.
    assert [p["text"] for p in ctx.unified.semantic.paragraphs] == [_TEXT]
