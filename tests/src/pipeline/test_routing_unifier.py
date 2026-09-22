"""Unit and property tests for ``_build_branches_for_classifications``.

Feature: risk-remediation (task 11.1, boundary RoutingUnifier)
Validates: Requirements 6.2, 6.4

The function turns a per-page classification list into the QC branches and
per-page routing results.  It is the single implementation the cache-miss
path calls (task 11.1) and the cache-hit path will call (task 11.2), so its
output must be a pure function of its inputs: ``from_cache`` may only change
what is logged (design.md "#### RoutingUnifier").

Backends are never called for real: PaddleOCR and PyMuPDF are patched at the
``pipeline.extraction_pipeline`` module path, the same seam the routing
suites use for ``build_qc_bundle``.
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from pdf_extractor.extraction.scan_detector import PageScanClassification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PDF = Path("/fake/test.pdf")
_TEI = "<TEI>mock</TEI>"


def _qc_config(*, ocr: bool = True, dpi: int = 150) -> dict:
    return {
        "ocr": ocr,
        "quality_control": {
            "ocr": {"rasterization_dpi": dpi},
            "grobid_integration": {"failure_behavior": "fallback"},
        },
    }


def _cls(page_index: int, is_native: bool, triggered_stages=None) -> PageScanClassification:
    return PageScanClassification(
        page_index=page_index,
        is_native=is_native,
        triggered_stages=triggered_stages if triggered_stages is not None else ([] if is_native else [1]),
        stage_values={},
    )


def _block(page_index: int, text: str) -> dict:
    return {"text": text, "page_index": page_index, "block_bbox": None, "spans": []}


def _plumber_blocks(n: int) -> list[dict]:
    return [_block(i, f"plumber page {i}") for i in range(n)]


def _paddle_blocks(n: int) -> list[dict]:
    return [_block(i, f"paddle page {i}") for i in range(n)]


def _call(classifications, *, tei_xml: str = _TEI, plumber_blocks=None, qc_config=None,
          from_cache: bool = False, paddle_blocks=None, pymupdf_blocks=None):
    """Invoke the unifier with OCR backends patched; return (result, paddle_mock, pymupdf_mock)."""
    from pipeline import extraction_pipeline as ep

    n = len(classifications)
    plumber_blocks = _plumber_blocks(n) if plumber_blocks is None else plumber_blocks
    paddle_blocks = _paddle_blocks(n) if paddle_blocks is None else paddle_blocks
    pymupdf_blocks = [_block(i, f"pymupdf page {i}") for i in range(n)] if pymupdf_blocks is None else pymupdf_blocks
    qc_config = _qc_config() if qc_config is None else qc_config

    mock_paddle = MagicMock(return_value=paddle_blocks)
    mock_pymupdf = MagicMock(return_value=(pymupdf_blocks, []))
    with patch("pipeline.extraction_pipeline.extract_with_paddleocr", mock_paddle), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf", mock_pymupdf):
        result = ep._build_branches_for_classifications(
            _PDF, tei_xml, plumber_blocks, classifications, qc_config, from_cache=from_cache,
        )
    return result, mock_paddle, mock_pymupdf


def _branch_key(b) -> tuple:
    return (b.source, b.index, b.payload, b.status)


# ---------------------------------------------------------------------------
# Property: output is a pure function of its inputs; from_cache only logs
# ---------------------------------------------------------------------------


@given(
    st.lists(st.booleans(), min_size=1, max_size=8),
    st.booleans(),
    st.sampled_from([_TEI, ""]),
)
@settings(max_examples=60)
def test_from_cache_does_not_change_branches_or_routing(
    page_is_native: list[bool], ocr: bool, tei_xml: str,
):
    """For any classification list, ocr flag and TEI payload, ``from_cache``
    True and False yield equal branches (source, index, payload, status) and
    equal routing results.

    **Validates: Requirements 6.4**
    """
    classifications = [_cls(i, nat) for i, nat in enumerate(page_is_native)]
    cfg = _qc_config(ocr=ocr)

    (branches_a, routing_a), _, _ = _call(classifications, tei_xml=tei_xml, qc_config=cfg, from_cache=False)
    (branches_b, routing_b), _, _ = _call(classifications, tei_xml=tei_xml, qc_config=cfg, from_cache=True)

    assert [_branch_key(b) for b in branches_a] == [_branch_key(b) for b in branches_b]
    assert routing_a == routing_b
    # One routing result per classified page, in classification order.
    assert [r.page_index for r in routing_a] == [c.page_index for c in classifications]


def test_from_cache_flag_is_visible_only_in_logging(caplog):
    """``from_cache=True`` names the cache in the log; the outputs are unchanged.

    **Validates: Requirements 6.4**
    """
    classifications = [_cls(0, True), _cls(1, False)]

    with caplog.at_level(logging.DEBUG, logger="pdf_extractor"):
        (branches_hit, routing_hit), _, _ = _call(classifications, from_cache=True)
    hit_messages = [r.getMessage() for r in caplog.records]
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger="pdf_extractor"):
        (branches_miss, routing_miss), _, _ = _call(classifications, from_cache=False)
    miss_messages = [r.getMessage() for r in caplog.records]

    assert any("cache" in m for m in hit_messages), hit_messages
    assert not any("cached" in m for m in miss_messages), miss_messages
    assert [_branch_key(b) for b in branches_hit] == [_branch_key(b) for b in branches_miss]
    assert routing_hit == routing_miss


# ---------------------------------------------------------------------------
# Determinism and input immutability
# ---------------------------------------------------------------------------


def test_calling_twice_yields_equal_results_and_leaves_inputs_untouched():
    """Same inputs twice -> equal outputs; the caller's block lists are not mutated.

    **Validates: Requirements 6.4**
    """
    classifications = [_cls(0, True), _cls(1, False, [2, 3]), _cls(2, True)]
    plumber = _plumber_blocks(3)
    plumber_snapshot = copy.deepcopy(plumber)
    paddle = _paddle_blocks(3)
    paddle_snapshot = copy.deepcopy(paddle)

    (b1, r1), _, _ = _call(classifications, plumber_blocks=plumber, paddle_blocks=paddle)
    (b2, r2), _, _ = _call(classifications, plumber_blocks=plumber, paddle_blocks=paddle)

    assert [_branch_key(b) for b in b1] == [_branch_key(b) for b in b2]
    assert r1 == r2
    assert plumber == plumber_snapshot
    assert paddle == paddle_snapshot


# ---------------------------------------------------------------------------
# All-native
# ---------------------------------------------------------------------------


def test_all_native_yields_grobid_and_pdfplumber_with_all_native_reasons():
    """All-native: [grobid, pdfplumber]; every page ``all_native``; OCR never called.

    **Validates: Requirements 6.2**
    """
    classifications = [_cls(0, True), _cls(1, True), _cls(2, True)]

    (branches, routing), mock_paddle, mock_pymupdf = _call(classifications)

    mock_paddle.assert_not_called()
    mock_pymupdf.assert_not_called()

    assert [(b.source, b.index) for b in branches] == [("grobid", 0), ("pdfplumber", 1)]
    assert branches[0].payload == _TEI
    assert [b["text"] for b in branches[1].payload] == [f"plumber page {i}" for i in range(3)]
    for blk in branches[1].payload:
        assert blk["source"] == "pdfplumber"
        assert blk["ocr_derived"] is False

    assert len(routing) == 3
    for r, c in zip(routing, classifications):
        assert r.page_index == c.page_index
        assert r.selected_extractor == "grobid+pdfplumber"
        assert r.fallback_extractor is None
        assert r.routing_reason == "all_native"
        assert r.classification is c


def test_all_native_keeps_grobid_branch_when_tei_is_empty():
    """All-native with a failed GROBID (``tei_xml == ""``) keeps the grobid
    branch with an empty payload so the fallback contract of
    ``test_build_qc_bundle_grobid_fallback`` is unchanged by the refactor.
    """
    classifications = [_cls(0, True)]

    (branches, _), _, _ = _call(classifications, tei_xml="")

    assert [(b.source, b.index, b.payload) for b in branches][0] == ("grobid", 0, "")
    assert branches[1].source == "pdfplumber"


# ---------------------------------------------------------------------------
# Mixed, ocr=true
# ---------------------------------------------------------------------------


def test_mixed_ocr_enabled_invokes_paddleocr_and_merges_by_page():
    """Mixed + ocr=true: PaddleOCR called with the configured dpi, PyMuPDF
    called for cross-validation; the second branch is named ``paddleocr``
    and its payload is page-sorted with per-block provenance.

    **Validates: Requirements 6.2**
    """
    classifications = [_cls(0, True), _cls(1, False), _cls(2, False), _cls(3, True)]

    (branches, routing), mock_paddle, mock_pymupdf = _call(classifications, qc_config=_qc_config(dpi=222))

    mock_paddle.assert_called_once_with(str(_PDF), dpi=222)
    mock_pymupdf.assert_called_once_with(str(_PDF))

    assert [(b.source, b.index) for b in branches] == [("grobid", 0), ("paddleocr", 1)]
    payload = branches[1].payload
    assert [b["page_index"] for b in payload] == [0, 1, 2, 3]
    assert [b["text"] for b in payload] == [
        "plumber page 0", "paddle page 1", "paddle page 2", "plumber page 3",
    ]
    for b in payload:
        if b["page_index"] in (0, 3):
            assert (b["source"], b["ocr_derived"]) == ("pdfplumber", False)
        else:
            assert (b["source"], b["ocr_derived"]) == ("paddleocr", True)

    native = [r for r in routing if r.classification.is_native]
    scanned = [r for r in routing if not r.classification.is_native]
    assert [r.page_index for r in native] == [0, 3]
    assert [r.page_index for r in scanned] == [1, 2]
    for r in native:
        assert r.selected_extractor == "grobid+pdfplumber"
        assert r.fallback_extractor == "paddleocr+pymupdf"
        assert r.routing_reason == "mixed_native_page"
    for r in scanned:
        assert r.selected_extractor == "paddleocr+pymupdf"
        assert r.fallback_extractor == "grobid+pdfplumber"
        assert r.routing_reason == "stage_1_empty_text"


def test_scanned_routing_reason_derives_from_triggered_stages():
    """Scanned pages report ``stage_1_empty_text``, ``stages_<n>_<m>`` or
    ``classified_scanned`` exactly as the miss path did.
    """
    classifications = [_cls(0, False, [1]), _cls(1, False, [2, 3]), _cls(2, False, [])]

    (_, routing), _, _ = _call(classifications, tei_xml="")

    assert [r.routing_reason for r in routing] == [
        "stage_1_empty_text", "stages_2_3", "classified_scanned",
    ]


# ---------------------------------------------------------------------------
# Mixed, ocr=false
# ---------------------------------------------------------------------------


def test_mixed_ocr_disabled_skips_ocr_and_warns_per_scanned_page(caplog):
    """Mixed + ocr=false: no OCR backend call, one WARNING per scanned page,
    scanned pages routed to ``none`` with a native fallback, native pages
    without an OCR fallback; the structural branch holds native blocks only.

    **Validates: Requirements 6.2**
    """
    classifications = [_cls(0, True), _cls(1, False, [2, 5]), _cls(2, True)]

    with caplog.at_level(logging.WARNING, logger="pdf_extractor"):
        (branches, routing), mock_paddle, mock_pymupdf = _call(classifications, qc_config=_qc_config(ocr=False))

    mock_paddle.assert_not_called()
    mock_pymupdf.assert_not_called()

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Skipping scanned page 1" in m for m in warnings), warnings
    assert not any("Skipping scanned page 0" in m or "Skipping scanned page 2" in m for m in warnings)

    assert [(b.source, b.index) for b in branches] == [("grobid", 0), ("pdfplumber", 1)]
    assert [b["page_index"] for b in branches[1].payload] == [0, 2]

    by_page = {r.page_index: r for r in routing}
    assert by_page[1].selected_extractor == "none"
    assert by_page[1].fallback_extractor == "grobid+pdfplumber"
    assert by_page[1].routing_reason == "stages_2_5"
    for pi in (0, 2):
        assert by_page[pi].selected_extractor == "grobid+pdfplumber"
        assert by_page[pi].fallback_extractor is None
        assert by_page[pi].routing_reason == "mixed_native_page"


def test_all_scanned_ocr_disabled_and_no_tei_yields_no_branches(caplog):
    """All-scanned + ocr=false + no TEI: nothing to hand to QC (branches empty),
    routing still reports every page.
    """
    classifications = [_cls(0, False), _cls(1, False)]

    with caplog.at_level(logging.WARNING, logger="pdf_extractor"):
        (branches, routing), mock_paddle, _ = _call(
            classifications, tei_xml="", plumber_blocks=[], qc_config=_qc_config(ocr=False),
        )

    mock_paddle.assert_not_called()
    assert branches == []
    assert [r.page_index for r in routing] == [0, 1]
    for r in routing:
        assert r.selected_extractor == "none"
        assert r.fallback_extractor is None
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Skipping scanned page 0" in m for m in warnings)
    assert any("Skipping scanned page 1" in m for m in warnings)


# ---------------------------------------------------------------------------
# Empty TEI on the scanned path
# ---------------------------------------------------------------------------


def test_empty_tei_with_scanned_pages_yields_no_grobid_branch():
    """``tei_xml == ""`` when any page is scanned: no grobid branch; the
    structural branch takes index 0.
    """
    classifications = [_cls(0, True), _cls(1, False)]

    (branches, _), mock_paddle, _ = _call(classifications, tei_xml="")

    mock_paddle.assert_called_once()
    assert [(b.source, b.index) for b in branches] == [("paddleocr", 0)]
    assert [b["page_index"] for b in branches[0].payload] == [0, 1]


def test_all_scanned_ocr_enabled_yields_single_paddleocr_branch():
    """All-scanned + ocr=true: the only branch is ``paddleocr`` at index 0
    (the caller passes no TEI and no pdfplumber blocks on that path).
    """
    classifications = [_cls(0, False), _cls(1, False)]

    (branches, routing), mock_paddle, mock_pymupdf = _call(classifications, tei_xml="", plumber_blocks=[])

    mock_paddle.assert_called_once()
    mock_pymupdf.assert_called_once()
    assert [(b.source, b.index) for b in branches] == [("paddleocr", 0)]
    assert all(b["source"] == "paddleocr" and b["ocr_derived"] is True for b in branches[0].payload)
    for r in routing:
        assert r.selected_extractor == "paddleocr+pymupdf"
        assert r.fallback_extractor is None
