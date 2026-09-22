"""
Single-producer property for ``semantic.sentences`` in the QC pipeline.

Feature: risk-remediation, task 5.2 (SentenceProducer).
Requirements: 4.2, 4.9

Since task 5.1 ``reconciler.reconcile()`` builds the sentence list itself
(native paragraphs and OCR blocks alike), and every sentence it emits carries
exactly the keys ``{text, page_index, ocr_derived, source}``.  Task 5.2
deletes the post-reconciliation fallback loop in
``quality_control._pdf_reconciler_fn``: that loop only ran when the reconciler
returned no sentences, hard-coded ``ocr_derived: False`` and emitted dicts
WITHOUT a ``source`` key, so it was both a second producer and one that could
never mark OCR sentences.

The tests below pin the property from two sides:

* Unpatched: a native-only run and a mixed run yield reconciler-shaped
  sentences only (``source`` present, exact key set), and the native-only
  sentence texts/pages are the ones the reconciler produced before this
  change (preservation).
* Patched: when ``reconcile()`` is forced to return ``sentences=[]`` the QC
  module must leave the list empty and must not call
  ``tokenize_sentences`` any more often than it does unpatched -- there is no
  second producer left in the QC module.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tokenize_calls(monkeypatch) -> list[str]:
    """Mock scispacy/spacy and replace ``tokenize_sentences`` with a
    deterministic splitter that records every input it is asked to segment."""
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)

    calls: list[str] = []

    def _tokenize(self, text: str) -> list[str]:  # noqa: ARG001
        calls.append(text)
        return text.split(". ")

    monkeypatch.setattr(
        "text_processing.composite.DefaultTextProcessor.tokenize_sentences", _tokenize
    )
    return calls


from quality_control import Candidate  # noqa: E402
from quality_control import reconciler as reconciler_module  # noqa: E402
from quality_control.quality_control import run_quality_control  # noqa: E402


_SENTENCE_KEYS = {"text", "page_index", "ocr_derived", "source"}

_NATIVE_TEXT = "Hello world. Second sentence here"
_OCR_TEXT = "Scanned page two reads clearly. A second OCR sentence"
_OCR_BBOX = [10.0, 20.0, 300.0, 40.0]


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
        payload=f"<TEI><text><body><p>{_NATIVE_TEXT}</p></body></text></TEI>",
        status=None,
    )


def _paddleocr_branch(index: int = 1, page_index: int = 1) -> Candidate:
    return Candidate(
        source="paddleocr",
        index=index,
        payload={
            "blocks": [
                {
                    "text": _OCR_TEXT,
                    "page_index": page_index,
                    "block_bbox": _OCR_BBOX,
                    "span_bboxes": [],
                    "ocr_derived": True,
                    "source": "paddleocr",
                }
            ]
        },
        status=None,
    )


@pytest.fixture
def reconcile_returning_no_sentences(monkeypatch):
    """Wrap the real ``reconcile()`` so it returns its record with
    ``semantic.sentences`` cleared -- the exact condition that used to make
    the fallback loop in ``_pdf_reconciler_fn`` run."""
    original = reconciler_module.reconcile

    def _patched(**kwargs):
        unified = original(**kwargs)
        unified.semantic.sentences = []
        return unified

    monkeypatch.setattr(reconciler_module, "reconcile", _patched)


# ---------------------------------------------------------------------------
# Unpatched: only reconciler-shaped sentences reach the QC output
# ---------------------------------------------------------------------------


def test_native_only_run_sentences_are_reconciler_shaped_and_preserved(tokenize_calls):
    """Requirement 4.2 / 4.9 -- every emitted sentence carries the reconciler
    key set (``source`` included), and a native-only run yields the same
    texts and pages the reconciler produced before the loop was removed."""
    ctx = run_quality_control([_grobid_branch(0)], "native-doc", _make_minimal_config())

    sentences = ctx.unified.semantic.sentences
    assert sentences, "native paragraphs must still yield sentences"
    assert all(set(s) == _SENTENCE_KEYS for s in sentences), [set(s) for s in sentences]
    assert all(s["source"] == "grobid" for s in sentences)
    assert all(s["ocr_derived"] is False for s in sentences)

    # Preservation: measured on the pre-change tree with this fixture.
    assert [(s["text"], s["page_index"]) for s in sentences] == [
        ("Hello world", 0),
        ("Second sentence here", 0),
    ]


def test_mixed_run_sentences_carry_source_and_both_markings(tokenize_calls):
    """Requirement 4.2 / 4.9 -- a mixed run's OCR-page sentences come from the
    reconciler (``source='paddleocr'``, ``ocr_derived=True``); no loop
    re-derives them with a hard-coded ``False`` or drops ``source``."""
    ctx = run_quality_control(
        [_grobid_branch(0), _paddleocr_branch(1, page_index=1)],
        "mixed-doc",
        _make_minimal_config(),
    )

    sentences = ctx.unified.semantic.sentences
    assert all(set(s) == _SENTENCE_KEYS for s in sentences), [set(s) for s in sentences]

    by_source = {s["source"] for s in sentences}
    assert by_source == {"grobid", "paddleocr"}, by_source
    assert [(s["text"], s["page_index"], s["ocr_derived"]) for s in sentences] == [
        ("Hello world", 0, False),
        ("Second sentence here", 0, False),
        ("Scanned page two reads clearly", 1, True),
        ("A second OCR sentence", 1, True),
    ]


# ---------------------------------------------------------------------------
# Patched: an empty reconciler sentence list stays empty -- no second producer
# ---------------------------------------------------------------------------


def test_qc_module_has_no_fallback_sentence_producer(tokenize_calls, reconcile_returning_no_sentences):
    """Requirement 4.2 -- when ``reconcile()`` yields no sentences the QC module
    must not manufacture any.  Before 5.2 the fallback loop re-tokenized every
    paragraph and appended ``{text, page_index, ocr_derived: False}`` dicts
    without ``source``."""
    ctx = run_quality_control([_grobid_branch(0)], "native-doc", _make_minimal_config())

    assert ctx.unified.semantic.sentences == [], ctx.unified.semantic.sentences


def test_tokenize_call_count_is_unchanged_when_reconciler_yields_no_sentences(
    tokenize_calls, monkeypatch
):
    """Requirement 4.2 -- ``tokenize_sentences`` is called exactly as often
    whether or not the reconciler returned sentences: nothing in the QC module
    re-segments text after reconciliation."""
    branches = [_grobid_branch(0)]

    run_quality_control(branches, "native-doc", _make_minimal_config())
    unpatched_calls = list(tokenize_calls)
    tokenize_calls.clear()

    original = reconciler_module.reconcile

    def _patched(**kwargs):
        unified = original(**kwargs)
        unified.semantic.sentences = []
        return unified

    monkeypatch.setattr(reconciler_module, "reconcile", _patched)
    run_quality_control(branches, "native-doc", _make_minimal_config())

    assert tokenize_calls == unpatched_calls, (
        f"extra tokenize_sentences calls after reconciliation: "
        f"{len(tokenize_calls)} vs {len(unpatched_calls)}"
    )
