"""
Preservation Tests — Migration: risk-remediation

**Property 2: Preservation** — live behaviour unchanged after the fix.

CRITICAL: every check in this file MUST PASS on the pre-change tree (commit
``a57294d``, before the ``risk-remediation`` spec's work) **and** on the
current tree.  A check that fails on the pre-change tree is a bug condition,
not a preservation guarantee — put it in
``tests/test_migration_risk_remediation_bug_condition.py`` instead.

Observation-first methodology: every expected value below was recorded by
running the *pre-change* code and the current code side by side and keeping
only what both produce.  Where the fix deliberately improved an output (native
character offsets, the new ``source`` key on sentences/paragraphs, the richer
manifest failure record, the ``ocr_derived`` annotation body key), the check
asserts the part of the contract that is common to both trees rather than a
value that is allowed to change — it never asserts a tautology.

Feature: risk-remediation, task 12.3.
Validates: Requirements 1.1, 1.2, 1.3, 2.1, 4.3, 5.4, 8.2, 8.3

Design reference: ``.kiro/specs/risk-remediation/design.md``,
"Testing Strategy → Migration / Regression Pair":

    native-only document: sentence texts/pages identical, ``reconcile()``
    direct-call tests unchanged, ``FinalOutputValidator`` behaviour unchanged,
    ``failed_chunks`` key still present, existing ``test_w3c_annotation``
    selectors for native sentences unchanged.

Conventions used here
---------------------
* **Every project import happens inside a test function**, matching the
  bug-condition sibling: a symbol that moved between the two trees then fails
  only its own check instead of collapsing collection for the module.
* Self-contained: fixtures are built inline so the file reads on its own as
  the spec's acceptance evidence.
* No GROBID, OpenAI, PaddleOCR or network access; sentence tokenization is
  stubbed, so the whole module runs in a couple of seconds and is NOT marked
  slow.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The pre-change tree's root ``conftest.py`` puts only ``src/`` on
# ``sys.path``.  Adding the repo root here keeps the module importable when it
# is copied into a pre-change worktree for the cross-tree verification run.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def deterministic_sentences(monkeypatch):
    """Split on ``". "`` instead of loading NLTK/spaCy models.

    ``run_quality_control`` instantiates the configured ``DefaultTextProcessor``
    itself, so the class method is what has to be patched.
    """
    monkeypatch.setattr(
        "text_processing.composite.DefaultTextProcessor.tokenize_sentences",
        lambda self, text: [s for s in text.split(". ") if s],
    )


def _qc_config() -> dict:
    """Minimal ``quality_control`` config accepted by ``run_quality_control``."""
    return {
        "quality_control": {
            "artifact_generator": {"export_to_disk": False, "output_dir": "output/qc_artifacts"},
            "rater": {"attributes": []},
            "iaa_calculator": {"thresholds": {}, "agreement_metrics": []},
            "adjudicator": {"strategy": "placeholder"},
            "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
        }
    }


#: A native-only document: one GROBID branch (semantic authority) and one
#: pdfplumber branch (structural blocks).  No OCR branch anywhere.
_NATIVE_TEXT = "Hello world. Second sentence here"

#: What both trees produce for ``_NATIVE_TEXT`` — ``(text, page_index)`` pairs.
_EXPECTED_NATIVE_SENTENCES = [
    ("Hello world", 0),
    ("Second sentence here", 0),
]


def _native_only_branches():
    from quality_control import Candidate

    return [
        Candidate(
            source="grobid",
            index=0,
            payload=f"<TEI><text><body><p>{_NATIVE_TEXT}</p></body></text></TEI>",
            status=None,
        ),
        Candidate(
            source="pdfplumber",
            index=1,
            payload={"blocks": [{"text": _NATIVE_TEXT, "page_index": 0}]},
            status=None,
        ),
    ]


def _run_native_only_qc():
    """Run the QC pipeline end to end on the native-only document.

    The observation is taken here, at the pipeline level, rather than at
    ``reconcile()``: on the pre-change tree ``reconcile()`` returned
    ``sentences=[]`` and the fallback loop in ``quality_control.py`` was the
    sentence producer, while the current tree produces them in the reconciler.
    The pipeline's output is the layer that existed — and must be identical —
    on both trees.
    """
    from quality_control.quality_control import run_quality_control

    return run_quality_control(_native_only_branches(), "native-doc", _qc_config())


# ---------------------------------------------------------------------------
# Requirement 4.3 — native sentences: texts and pages must not regress
# ---------------------------------------------------------------------------


def test_native_only_document_preserves_sentence_texts_and_pages(deterministic_sentences):
    """Requirement 4.3 — moving sentence production into the reconciler must
    leave a native-only document's sentences exactly as they were.

    Both trees produce the same ``(text, page_index)`` sequence.  The current
    tree additionally stamps ``source`` on each sentence, which is why the
    check compares the two recorded fields rather than whole dicts.
    """
    sentences = _run_native_only_qc().unified.semantic.sentences

    observed = [(s.get("text"), s.get("page_index")) for s in sentences]
    assert observed == _EXPECTED_NATIVE_SENTENCES, (
        "Requirement 4.3: a native-only document's sentence texts and page "
        f"indexes must not change. Expected {_EXPECTED_NATIVE_SENTENCES!r}, "
        f"got {observed!r}."
    )
    markings = [s.get("ocr_derived") for s in sentences]
    assert markings == [False, False], (
        "Requirement 4.3: every sentence of a native-only document must be "
        f"marked ocr_derived=False; got {markings!r}."
    )


def test_native_only_document_preserves_paragraph_and_page_text(deterministic_sentences):
    """Requirement 4.3 — the paragraph layer and the reconciled page text of a
    native-only document are unchanged (recorded from both trees)."""
    unified = _run_native_only_qc().unified

    paragraphs = [(p.get("text"), p.get("page_index")) for p in unified.semantic.paragraphs]
    assert paragraphs == [(_NATIVE_TEXT, 0)], (
        "Requirement 4.3: the native-only paragraph layer must not change; got "
        f"{paragraphs!r}."
    )
    assert unified.content["exact_text"] == _NATIVE_TEXT, (
        "Requirement 4.3: the reconciled full text of a native-only document "
        f"must not change; got {unified.content['exact_text']!r}."
    )
    assert unified.content["pages"] == [
        {"page_index": 0, "text": _NATIVE_TEXT, "block_count": 1}
    ], f"Requirement 4.3: the reconciled page list changed; got {unified.content['pages']!r}."


# ---------------------------------------------------------------------------
# Requirement 4.3 — native annotations keep their text-position selectors
# ---------------------------------------------------------------------------


def _selectors_by_type(annotation: dict) -> dict[str, dict]:
    return {s["type"]: s for s in annotation["target"]["selector"]}


def test_native_sentences_keep_text_position_selectors(deterministic_sentences):
    """Requirement 4.3 — the selector *shape* emitted for native sentences is
    unchanged: a ``TextPositionSelector`` plus a ``TextQuoteSelector``, and
    never the media-fragment selector reserved for OCR regions.

    The offsets themselves are deliberately excluded: pre-change every native
    sentence got ``start=end=0`` (the offset map was keyed by paragraph text
    and looked up by sentence text), and Requirement 4.3 explicitly permits
    that defect to be improved.  The selector types, their order, the quoted
    text and the target source are what must not regress.
    """
    from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

    unified = _run_native_only_qc().unified
    base_uri = "urn:evitrace:document:native-doc"
    annotations = generate_w3c_jsonld(project(unified), base_uri=base_uri)

    assert len(annotations) == len(_EXPECTED_NATIVE_SENTENCES), (
        "Requirement 4.3: one annotation per native sentence; got "
        f"{len(annotations)} for {len(_EXPECTED_NATIVE_SENTENCES)} sentences."
    )
    for annotation, (text, _page) in zip(annotations, _EXPECTED_NATIVE_SENTENCES):
        assert annotation["type"] == "Annotation"
        assert annotation["@context"] == "http://www.w3.org/ns/anno.jsonld"
        assert annotation["body"]["value"] == text
        assert annotation["body"]["type"] == "TextualBody"
        assert annotation["target"]["source"] == base_uri

        types = [s["type"] for s in annotation["target"]["selector"]]
        assert types == ["TextPositionSelector", "TextQuoteSelector"], (
            "Requirement 4.3: a native sentence must be targeted by a "
            "TextPositionSelector followed by a TextQuoteSelector (never a "
            f"FragmentSelector, which is the OCR region form); got {types!r} "
            f"for {text!r}."
        )
        selectors = _selectors_by_type(annotation)
        position = selectors["TextPositionSelector"]
        assert isinstance(position["start"], int) and isinstance(position["end"], int), (
            "Requirement 4.3: the position selector must carry integer "
            f"character offsets; got {position!r}."
        )
        assert 0 <= position["start"] <= position["end"], (
            f"Requirement 4.3: position selector offsets are not ordered: {position!r}."
        )
        assert selectors["TextQuoteSelector"]["exact"] == text, (
            "Requirement 4.3: the quote selector must quote the sentence "
            f"verbatim; got {selectors['TextQuoteSelector']['exact']!r} for {text!r}."
        )

    ids = [a["id"] for a in annotations]
    assert len(set(ids)) == len(ids), (
        f"Requirement 4.3: native annotations must have distinct ids; got {ids!r}."
    )


# ---------------------------------------------------------------------------
# Requirement 2.1 — the direct ``reconcile()`` call contract is unchanged
#
# design.md "Migration / Regression Pair": "reconcile() direct-call tests
# unchanged".  Adjudication-driven role selection (Requirement 2.1) changed
# *which artifact* the caller passes as primary; it must not change how the
# function is called or what it hands back.
# ---------------------------------------------------------------------------


class _FakeTextProcessor:
    """Deterministic sentence splitter handed straight to ``reconcile()``."""

    def tokenize_sentences(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]

    def compare(self, a: str, b: str) -> float:
        return 1.0 if a == b else 0.0


def _mock_concern_strategies() -> dict:
    """Stub the three concern strategies so ``reconcile()`` stays hermetic."""
    fidelity = MagicMock()
    fidelity.reconcile.return_value = {
        "edit_distance": 0.0,
        "agreement": "full",
        "preferred_reading": "",
        "confidence": 1.0,
    }
    section = MagicMock()
    section.reconcile.return_value = 1.0
    table_figure = MagicMock()
    table_figure.merge.return_value = {"agreement": "present", "merged_text": ""}
    return {
        "text_fidelity_strategy": fidelity,
        "section_strategy": section,
        "table_figure_strategy": table_figure,
    }


_PRIMARY_ARTIFACT = {
    "document_id": "doc-001",
    "id": "artifact-primary",
    "blocks": [
        {"text": "Introduction", "page_index": 0, "block_type": "section"},
        {"text": "This is a paragraph.", "page_index": 0, "block_type": "paragraph"},
    ],
}
_SECONDARY_ARTIFACT = {
    "document_id": "doc-001",
    "id": "artifact-secondary",
    "blocks": [
        {"text": "This is a paragraph.", "page_index": 0, "block_type": "paragraph"},
    ],
}
_ADJUDICATION = {"primary_extractor": "grobid", "confidence": 1.0, "rationale": "r"}


def _reconcile_native_pair():
    from quality_control.reconciler import reconcile

    return reconcile(
        primary_artifact=_PRIMARY_ARTIFACT,
        secondary_artifact=_SECONDARY_ARTIFACT,
        primary_observation={"observation": "primary"},
        secondary_observation={"observation": "secondary"},
        investigator_object={"agreement_metrics": {}},
        adjudication_decisions=_ADJUDICATION,
        text_processor=_FakeTextProcessor(),
        **_mock_concern_strategies(),
    )


def test_reconcile_signature_is_unchanged():
    """The public call shape of ``reconcile()`` — parameter names, order and
    keyword-only split — is part of its contract for every direct caller."""
    from quality_control.reconciler import reconcile

    params = [
        (p.name, p.kind.name, p.default is inspect.Parameter.empty)
        for p in inspect.signature(reconcile).parameters.values()
    ]
    expected = [
        ("primary_artifact", "POSITIONAL_OR_KEYWORD", True),
        ("secondary_artifact", "POSITIONAL_OR_KEYWORD", True),
        ("primary_observation", "POSITIONAL_OR_KEYWORD", False),
        ("secondary_observation", "POSITIONAL_OR_KEYWORD", False),
        ("investigator_object", "POSITIONAL_OR_KEYWORD", False),
        ("adjudication_decisions", "POSITIONAL_OR_KEYWORD", False),
        ("config", "POSITIONAL_OR_KEYWORD", False),
        ("text_fidelity_strategy", "KEYWORD_ONLY", False),
        ("section_strategy", "KEYWORD_ONLY", False),
        ("table_figure_strategy", "KEYWORD_ONLY", False),
        ("text_processor", "KEYWORD_ONLY", False),
    ]
    assert params == expected, (
        "The reconcile() call contract changed; direct callers (including the "
        f"QC pipeline's reconciler function) would break. Got {params!r}."
    )


def test_reconcile_returns_unified_record_with_unchanged_content():
    """A direct ``reconcile()`` call still returns a ``UnifiedRecord`` whose
    ``content`` dict is byte-for-byte what it was: the ten content keys, the
    primary-derived segments and pages, and the joined exact text."""
    from quality_control.models import UnifiedRecord

    unified = _reconcile_native_pair()

    assert isinstance(unified, UnifiedRecord), (
        f"reconcile() must return a UnifiedRecord; got {type(unified).__name__}."
    )
    assert unified.document_id == "doc-001", (
        f"reconcile() must propagate document_id; got {unified.document_id!r}."
    )
    assert set(unified.content) == {
        "document_id",
        "metadata",
        "pages",
        "segments",
        "annotations",
        "tables",
        "figures",
        "images",
        "exact_text",
        "provenance",
    }, f"The reconciled content keys changed; got {sorted(unified.content)!r}."

    assert unified.content["document_id"] == "doc-001"
    assert unified.content["metadata"] == {}
    assert unified.content["annotations"] == []
    assert unified.content["tables"] == []
    assert unified.content["figures"] == []
    assert unified.content["images"] == []
    assert unified.content["exact_text"] == "Introduction\nThis is a paragraph.", (
        f"reconciled exact_text changed; got {unified.content['exact_text']!r}."
    )
    assert unified.content["segments"] == [
        {
            "segment_id": "seg-0",
            "text": "Introduction",
            "page_index": 0,
            "bbox": None,
            "type": "section",
        },
        {
            "segment_id": "seg-1",
            "text": "This is a paragraph.",
            "page_index": 0,
            "bbox": None,
            "type": "paragraph",
        },
    ], f"reconciled segments changed; got {unified.content['segments']!r}."
    assert unified.content["pages"] == [
        {
            "page_index": 0,
            "text": "Introduction\nThis is a paragraph.",
            "block_count": 2,
        }
    ], f"reconciled pages changed; got {unified.content['pages']!r}."
    json.dumps(unified.content)  # must stay JSON-serialisable


def test_reconcile_provenance_keys_are_unchanged():
    """The provenance block keeps its extractor-agnostic keys and echoes the
    observations, investigator object and adjudication decision it was given."""
    provenance = _reconcile_native_pair().content["provenance"]

    assert set(provenance) == {
        "primary_artifact_id",
        "secondary_artifact_id",
        "primary_observation",
        "secondary_observation",
        "investigator_object",
        "adjudication_decisions",
    }, f"The provenance keys changed; got {sorted(provenance)!r}."
    assert provenance["primary_artifact_id"] == "artifact-primary"
    assert provenance["secondary_artifact_id"] == "artifact-secondary"
    assert provenance["primary_observation"] == {"observation": "primary"}
    assert provenance["secondary_observation"] == {"observation": "secondary"}
    assert provenance["investigator_object"] == {"agreement_metrics": {}}
    assert provenance["adjudication_decisions"] == _ADJUDICATION


def test_reconcile_secondary_blocks_populate_the_structural_layer():
    """The structural layer is still taken from the secondary artifact's
    blocks, unchanged and un-rewritten."""
    unified = _reconcile_native_pair()

    assert unified.structural.blocks == _SECONDARY_ARTIFACT["blocks"], (
        "reconcile() must keep the secondary artifact's blocks as the "
        f"structural layer; got {unified.structural.blocks!r}."
    )


# ---------------------------------------------------------------------------
# Requirements 1.1 / 1.2 / 1.3 — FinalOutputValidator behaviour is unchanged
# ---------------------------------------------------------------------------


def _map_produced_field(field_index: int, lookup: dict) -> dict:
    """A merged field in exactly the shape ``pdf_processor`` assembles it."""
    entry = lookup[field_index]
    return {
        "field_index": field_index,
        "domain_group": entry["domain_group"],
        "field_name": entry["field_name"],
        "extracted_value": "example value",
        "evidence": "Supporting sentence from the paper.",
        "location": ["ev-001"],
        "location_metadata": [
            {
                "id": "ev-001",
                "type": "sentence",
                "section_path": "Methods",
                "page": 2,
                "coords": None,
                "xpath": None,
                "source_pdf": "paper.pdf",
            }
        ],
        "confidence": "h",
    }


def test_final_output_validator_accepts_every_map_produced_field():
    """Requirement 1.1 — a merged field whose ``domain_group`` comes from the
    configured extraction map validates, for all 62 canonical fields.  This is
    the precondition Requirement 1.2 depends on: a paper whose every field
    validates is the paper whose output file gets written.

    The map stores ``domain_group`` as ``"2. Clinical context"``;
    ``_build_field_lookup`` reduces it to the integer the schema requires.
    This pins that reduction plus the whole accepted record shape.
    """
    from pipeline.extraction_map import _build_field_lookup
    from pipeline.validator import FinalOutputValidator

    lookup = _build_field_lookup()
    assert lookup, "the extraction map produced an empty field lookup"
    assert all(isinstance(v["domain_group"], int) for v in lookup.values()), (
        "Requirement 1.1: the extraction map's domain_group must reduce to an "
        "integer for every field."
    )

    fields = [_map_produced_field(idx, lookup) for idx in sorted(lookup)]
    result = FinalOutputValidator().validate(fields)

    assert result.is_valid, (
        "Requirement 1.1: every field produced from the configured extraction "
        f"map must validate; errors: {result.errors!r}"
    )
    assert result.errors == []


def test_final_output_validator_rejects_an_invalid_field_naming_it():
    """Requirement 1.3 — a rejected field is reported with its identity so an
    operator can diagnose it."""
    from pipeline.extraction_map import _build_field_lookup
    from pipeline.validator import FinalOutputValidator

    lookup = _build_field_lookup()
    index = sorted(lookup)[2]
    good = _map_produced_field(sorted(lookup)[0], lookup)
    bad = _map_produced_field(index, lookup)
    bad["confidence"] = "high"  # not in the h/m/l/nr enum

    result = FinalOutputValidator().validate([good, bad])

    assert not result.is_valid, (
        "Requirement 1.3: a field with an out-of-enum confidence must be rejected."
    )
    joined = " ".join(result.errors)
    assert f"field_index={index}" in joined, (
        "Requirement 1.3: the rejection must name the offending field's index; "
        f"errors were {result.errors!r}"
    )
    assert repr(lookup[index]["field_name"]) in joined, (
        "Requirement 1.3: the rejection must name the offending field; errors "
        f"were {result.errors!r}"
    )
    assert "confidence" in joined, (
        f"Requirement 1.3: the rejection must name the failing property; got {result.errors!r}"
    )


# ---------------------------------------------------------------------------
# Requirement 5.4 — the ``failed_chunks`` compatibility key is still written
# ---------------------------------------------------------------------------


def test_chunk_exhaustion_still_writes_the_failed_chunks_manifest_key():
    """Requirement 5.4 — an operator reading the manifest still finds
    ``status="failed_chunks"`` and the list of failed chunk numbers.

    The current tree writes a superset of this entry (``error`` plus one
    ``failures`` record per chunk), so the check asserts the presence and the
    values of the pre-existing keys only, never the exact key set.
    """
    import pipeline.pdf_processor as pdf_processor

    chunk_fields = {
        1: [{"field_index": 3, "field_name": "Study design", "definition": "..."}],
        2: [{"field_index": 10, "field_name": "Sample size", "definition": "..."}],
    }
    chunk_sources = {1: "evidence text chunk 1", 2: "evidence text chunk 2"}
    pdf_name = "paper_fail"
    manifest: dict = {pdf_name: {"status": "pending"}}

    def _side_effect(chunk_num, *args, **kwargs):
        if chunk_num == 2:
            raise RuntimeError("API error on chunk 2")
        return json.dumps({"extractions": [{"i": 3, "v": "RCT", "loc": [], "c": "h"}]})

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_side_effect)
    mock_api.warm_pdf_cache = AsyncMock()

    with patch.dict(sys.modules, {"agents.openai.api_client": mock_api}), \
            patch.object(pdf_processor, "save_manifest"):

        async def _run():
            return await pdf_processor._run_parallel_chunks(
                chunk_sources=chunk_sources,
                chunk_fields=chunk_fields,
                valid_location_ids={"ev-001"},
                api_semaphore=asyncio.Semaphore(5),
                pdf_name=pdf_name,
                num_chunks=3,
                enable_prewarm=False,
                chunk_model="gpt-test",
                synthesis_model="gpt-test",
                prewarm_synthesis_diff=False,
                manifest=manifest,
                manifest_lock=asyncio.Lock(),
            )

        result = asyncio.run(_run())

    assert result is None, (
        "Requirement 5.4: a paper with an exhausted chunk must not return chunk results."
    )
    entry = manifest[pdf_name]
    assert entry["status"] == "failed_chunks", (
        f"Requirement 5.4: the manifest status must stay 'failed_chunks'; got {entry!r}."
    )
    assert "failed_chunks" in entry, (
        "Requirement 5.4: the compatibility 'failed_chunks' key must still be "
        f"written for readers that only understand it; got {sorted(entry)!r}."
    )
    assert entry["failed_chunks"] == [2], (
        "Requirement 5.4: 'failed_chunks' must list the failed chunk numbers; "
        f"got {entry['failed_chunks']!r}."
    )


# ---------------------------------------------------------------------------
# Requirements 8.2 / 8.3 — the built-in QC implementations are unchanged
# ---------------------------------------------------------------------------


def test_builtin_quality_report_instantiates_and_passes():
    """Requirements 8.2, 8.3 — ``QualityReport`` is a complete implementation of
    the (now abstract) ``QualityMetrics`` base and still passes every candidate."""
    from quality_control.builtin_impls import QualityReport

    report = QualityReport(source="grobid", index=0)

    assert report.passes_check() is True, (
        "Requirement 8.3: the built-in QualityReport must keep unconditionally "
        "passing candidates."
    )
    assert report.status == "pass"
    assert report.extractor == "grobid", "the extractor alias must still resolve to source"
    assert report.agent == "grobid", "the agent alias must still resolve to source"
    assert QualityReport().source == "", "QualityReport must remain default-constructible"


def test_builtin_inter_rater_report_pairwise_output_unchanged():
    """Requirements 8.2, 8.3 — ``InterRaterReport.compute`` still produces one
    pairwise score per branch pair, keyed by the branches' names."""
    from quality_control.builtin_impls import InterRaterReport, QualityReport

    agree = [QualityReport(source="grobid", index=0), QualityReport(source="pdfplumber", index=1)]
    for r in agree:
        r.passes_check()

    report = InterRaterReport()
    report.compute(agree)
    assert report.pairwise == {"grobid_vs_pdfplumber": 1.0}, (
        "Requirement 8.3: two agreeing named branches must still score 1.0 "
        f"under one 'a_vs_b' key; got {report.pairwise!r}."
    )

    disagree = [QualityReport(source="grobid", index=0), QualityReport(source="pdfplumber", index=1)]
    disagree[0].status = "pass"
    disagree[1].status = "fail"
    disagreement = InterRaterReport()
    disagreement.compute(disagree)
    assert disagreement.pairwise == {"grobid_vs_pdfplumber": 0.0}, (
        f"Requirement 8.3: disagreeing branches must still score 0.0; got "
        f"{disagreement.pairwise!r}."
    )


def test_builtin_adjudication_decision_output_unchanged():
    """Requirements 8.2, 8.3 — ``AdjudicationDecision.adjudicate`` still elects
    the extractor with the most passing branches and explains itself the same way."""
    from quality_control.builtin_impls import (
        AdjudicationDecision,
        InterRaterReport,
        QualityReport,
    )

    reports = [
        QualityReport(source="grobid", index=0),
        QualityReport(source="pdfplumber", index=1),
    ]
    reports[0].status = "pass"
    reports[1].status = "fail"
    metrics = InterRaterReport()
    metrics.compute(reports)

    decision = AdjudicationDecision()
    decision.adjudicate(reports, metrics)

    assert decision.primary_extractor == "grobid", (
        f"Requirement 8.3: the passing branch must still be elected; got "
        f"{decision.primary_extractor!r}."
    )
    assert decision.confidence == 0.5, (
        f"Requirement 8.3: confidence is still passes/total; got {decision.confidence!r}."
    )
    assert decision.rationale == "grobid selected: 1/2 branches passed", (
        f"Requirement 8.3: the rationale wording changed; got {decision.rationale!r}."
    )

    empty = AdjudicationDecision()
    empty.adjudicate([], InterRaterReport())
    assert empty.rationale == "no reports available", (
        f"Requirement 8.3: the empty-input rationale changed; got {empty.rationale!r}."
    )


#: The eight Tier-1 coverage metrics, in the order ``passes_check`` appends
#: them, with the ``(computed_value, threshold, triggered)`` triple both trees
#: produce for the clean single-page fixture below.
_EXPECTED_COVERAGE_METRICS = [
    ("min_chars_per_page", 0, 100, False),
    ("extraction_coverage_ratio", 1.0, 0.6, False),
    ("long_sentence_fraction", 0.0, 0.12, False),
    ("section_coverage", 4, 4, False),
    ("caption_table_figure_coverage", 0, None, False),
    ("coordinate_availability", 0.0, 0.1, False),
    ("references_in_body", 0.0, 0.05, False),
    ("weird_char_ratio", 0.0, 0.05, False),
]


def test_builtin_extraction_coverage_report_metrics_unchanged():
    """Requirements 8.2, 8.3 — the built-in ``ExtractionCoverageReport``
    instantiates from config and still computes the same eight metric records,
    in the same order, with the same values, for the same input."""
    from quality_control.local_metrics import ExtractionCoverageReport

    config = {
        "quality_control": {
            "local_metrics": {
                "min_chars_per_page": 100,
                "grobid_vs_native_ratio_threshold": 0.6,
                "long_sentence_word_threshold": 120,
                "long_sentence_max_fraction": 0.12,
                "expected_sections": ["abstract", "introduction", "methods", "results"],
                "caption_table_figure_check_enabled": False,
                "coordinate_coverage_threshold": 0.1,
                "references_in_body_threshold": 0.05,
                "weird_char_ratio_threshold": 0.05,
            }
        }
    }
    page_text = "Abstract\nIntroduction\nMethods\nResults\n" + "Clean sentence text here. " * 20
    report = ExtractionCoverageReport(
        config=config,
        blocks=[
            {"text": "Abstract", "page_index": 0, "block_type": "section",
             "block_bbox": [0, 0, 10, 10]},
            {"text": page_text, "page_index": 0, "block_type": "paragraph",
             "block_bbox": [0, 0, 10, 10]},
        ],
        sentence_records=[{"sentence": "Clean sentence text here."}],
        full_pdf_text=page_text,
        page_texts={0: page_text},
        native_page_texts={0: page_text},
    )

    passed = report.passes_check()

    observed = [
        (r.metric_name, r.computed_value, r.threshold, r.triggered)
        for r in report.metric_records
    ]
    assert observed == _EXPECTED_COVERAGE_METRICS, (
        "Requirement 8.3: the built-in extraction-coverage metrics must produce "
        f"identical records for identical input. Expected "
        f"{_EXPECTED_COVERAGE_METRICS!r}, got {observed!r}."
    )
    assert passed is True, (
        "Requirement 8.3: a clean branch must still pass the coverage checks."
    )
