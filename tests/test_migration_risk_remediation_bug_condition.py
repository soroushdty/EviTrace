"""
Bug Condition Exploration Test — Migration: risk-remediation

**Property 1: Bug Condition** — the ten audit defects are present in unfixed code.

CRITICAL: every sub-check in this file MUST FAIL on the pre-change tree
(commit ``a57294d``, before the ``risk-remediation`` spec's work) and MUST PASS
after it.  A sub-check that passes on both trees is not a bug condition and does
not belong here — put it in
``tests/test_migration_risk_remediation_preservation.py`` instead.

DO NOT "fix" a failing sub-check by weakening it; a failure means the defect it
encodes is still present.

Each sub-check calls :func:`pytest.fail` with a message naming the requirement
it encodes, per ``.kiro/steering/testing.md`` ("Migration / Steering-Drift
Regression Tests").

Feature: risk-remediation, task 12.2.
Validates: Requirements 2.1, 2.2, 3.1, 4.2, 4.5, 4.9, 5.1, 6.3, 7.3, 7.5,
           8.1, 8.2, 8.3, 9.1, 9.2, 10.1, 11.2

Design reference: ``.kiro/specs/risk-remediation/design.md``,
"Testing Strategy → Migration / Regression Pair".

Conventions used here
---------------------
* **Every project import happens inside a test function.**  Several of the
  symbols exercised below (``select_paper_evidence``, ``parse_tei_coords``,
  ``_page_class_cache_read``, …) do not exist at ``a57294d``; keeping the
  imports local means a missing symbol fails only its own sub-check instead of
  collapsing collection for the whole module.
* The file is deliberately self-contained: the fixtures it needs are built
  inline rather than imported from the per-module test suites, so it reads on
  its own as the spec's acceptance evidence.  The only shared dependency is
  ``tests/helpers/grobid_tei.py`` (the real GROBID 0.8.2 TEI fixtures).
* No GROBID, OpenAI, PaddleOCR, PyMuPDF or network access: every backend is
  mocked.  Whole-module runtime is well under five seconds, so the module is
  NOT marked slow.
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import inspect
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The pre-change tree's root ``conftest.py`` puts only ``src/`` on ``sys.path``,
# so ``tests.helpers`` is not importable there by default.  Adding the repo root
# here keeps the RED run meaningful (the helper files are copied in for it).
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


_QC_TEXT = "Hello world. Second sentence here"
_OCR_BBOX = [10.0, 20.0, 300.0, 40.0]


def _three_named_branches():
    """grobid / pdfplumber / paddleocr, each carrying its own ``source`` name."""
    from quality_control import Candidate

    return [
        Candidate(
            source="grobid",
            index=0,
            payload=f"<TEI><text><body><p>{_QC_TEXT}</p></body></text></TEI>",
            status=None,
        ),
        Candidate(
            source="pdfplumber",
            index=1,
            payload={"blocks": [{"text": _QC_TEXT, "page_index": 0}]},
            status=None,
        ),
        Candidate(
            source="paddleocr",
            index=2,
            payload={"blocks": [{"text": _QC_TEXT, "page_index": 0}]},
            status=None,
        ),
    ]


# ---------------------------------------------------------------------------
# Requirement 9 — extractor identity in agreement and adjudication output
# ---------------------------------------------------------------------------


def test_adjudication_records_a_named_primary_extractor(deterministic_sentences):
    """Requirement 9.2 — the adjudicated selection is the extractor's source name.

    Pre-change the rater never populated ``QualityReport.source``, so the
    built-in adjudicator's ``getattr(report, "extractor", str(i))`` resolved to
    the empty-string default and ``primary_extractor`` came out as ``""``.
    """
    from quality_control.quality_control import run_quality_control

    ctx = run_quality_control(_three_named_branches(), "identity-doc", _qc_config())
    primary = ctx.decision.primary_extractor

    if not primary:
        pytest.fail(
            "BUG CONDITION (Requirement 9.2): adjudication produced "
            f"primary_extractor={primary!r} — no extractor is named. The recorded "
            "selection must be the source name of a branch."
        )
    if primary not in {"grobid", "pdfplumber", "paddleocr"}:
        pytest.fail(
            "BUG CONDITION (Requirement 9.2/9.4): adjudication selected "
            f"{primary!r}, which is not one of the branch source names "
            "('grobid', 'pdfplumber', 'paddleocr')."
        )
    if not ctx.decision.rationale or primary not in ctx.decision.rationale:
        pytest.fail(
            "BUG CONDITION (Requirement 9.2): the adjudication rationale "
            f"{ctx.decision.rationale!r} does not name the selected extractor "
            f"{primary!r}."
        )


def test_three_branches_yield_three_source_named_pairwise_keys(deterministic_sentences):
    """Requirements 9.1, 9.4 — pairwise agreement is keyed by source name.

    Pre-change every report carried the same empty name, so the three pairs
    collapsed into the single key ``"_vs_"``.
    """
    from quality_control.quality_control import run_quality_control

    ctx = run_quality_control(_three_named_branches(), "identity-doc", _qc_config())
    pairwise = ctx.iaa_metrics.pairwise
    expected = {"grobid_vs_pdfplumber", "grobid_vs_paddleocr", "pdfplumber_vs_paddleocr"}

    if len(pairwise) != 3:
        pytest.fail(
            "BUG CONDITION (Requirement 9.1): three branches must produce "
            f"3·(3−1)/2 = 3 pairwise entries, got {len(pairwise)}: {sorted(pairwise)}."
        )
    if set(pairwise) != expected:
        pytest.fail(
            "BUG CONDITION (Requirement 9.1): pairwise keys must be named by "
            f"extractor source. Expected {sorted(expected)}, got {sorted(pairwise)}."
        )
    for key in pairwise:
        left, _, right = key.partition("_vs_")
        if not left or not right or left.isdigit() or right.isdigit():
            pytest.fail(
                "BUG CONDITION (Requirement 9.4): pairwise key "
                f"{key!r} identifies an extractor by position, not by source name."
            )


# ---------------------------------------------------------------------------
# Requirement 2 — adjudication-driven reconciliation (branch roles)
# ---------------------------------------------------------------------------


def test_ocr_named_secondary_populates_the_structural_layer(deterministic_sentences):
    """Requirements 2.1, 2.2 — the secondary is the first remaining branch.

    Pre-change the reconciler callback looked the secondary up by hard-coded
    name (``pdfplumber``/``pymupdf`` only), so a ``paddleocr`` branch was
    dropped on the floor and ``structural.blocks`` came back empty.
    """
    from quality_control import Candidate
    from quality_control.quality_control import run_quality_control

    branches = [
        Candidate(
            source="grobid",
            index=0,
            payload=f"<TEI><text><body><p>{_QC_TEXT}</p></body></text></TEI>",
            status=None,
        ),
        Candidate(
            source="paddleocr",
            index=1,
            payload={
                "blocks": [
                    {
                        "text": _QC_TEXT,
                        "page_index": 0,
                        "block_bbox": _OCR_BBOX,
                        "span_bboxes": [],
                        "ocr_derived": True,
                        "source": "paddleocr",
                    }
                ]
            },
            status=None,
        ),
    ]

    ctx = run_quality_control(branches, "mixed-doc", _qc_config())
    structural = ctx.unified.structural

    if structural is None or not structural.blocks:
        pytest.fail(
            "BUG CONDITION (Requirements 2.1, 2.2): with branches "
            "['grobid', 'paddleocr'] the OCR branch must become the secondary "
            "input, but structural.blocks is "
            f"{None if structural is None else structural.blocks!r} — the "
            "OCR-named branch was dropped."
        )
    if structural.blocks[0].get("block_bbox") != _OCR_BBOX:
        pytest.fail(
            "BUG CONDITION (Requirement 2.2): the secondary branch's bounding "
            f"box did not survive into the structural layer: "
            f"{structural.blocks[0].get('block_bbox')!r} != {_OCR_BBOX!r}."
        )

    provenance = ctx.unified.content.get("provenance", {})
    if provenance.get("secondary_branch_source") != "paddleocr":
        pytest.fail(
            "BUG CONDITION (Requirements 2.1, 2.2, 2.4): provenance must record "
            "the branch actually used as secondary; got "
            f"secondary_branch_source={provenance.get('secondary_branch_source')!r} "
            f"(primary_branch_source={provenance.get('primary_branch_source')!r})."
        )


# ---------------------------------------------------------------------------
# Requirement 4.2 / 4.9 — OCR sentences exist for scanned pages
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


def _block(text: str, page: int, source: str, ocr: bool, bbox=None) -> dict:
    block = {
        "text": text,
        "page_index": page,
        "block_type": "paragraph",
        "source": source,
        "ocr_derived": ocr,
    }
    if bbox is not None:
        block["block_bbox"] = bbox
    return block


#: Page 0 is native (GROBID), page 1 is scanned (two distinct PaddleOCR boxes).
_SCANNED_FIXTURE_BBOX_A = [0.0, 0.0, 100.0, 20.0]
_SCANNED_FIXTURE_BBOX_B = [10.0, 300.0, 210.0, 340.0]


def _reconcile_mixed_native_and_scanned():
    from quality_control.reconciler import reconcile

    return reconcile(
        primary_artifact={
            "document_id": "doc-mixed",
            "blocks": [_block("Native page zero.", 0, "grobid", False)],
        },
        secondary_artifact={
            "document_id": "doc-mixed",
            "blocks": [
                _block("First scanned block.", 1, "paddleocr", True, _SCANNED_FIXTURE_BBOX_A),
                _block("Second scanned block.", 1, "paddleocr", True, _SCANNED_FIXTURE_BBOX_B),
            ],
        },
        adjudication_decisions={
            "primary_extractor": "grobid",
            "confidence": 1.0,
            "rationale": "grobid selected",
        },
        text_processor=_FakeTextProcessor(),
        **_mock_concern_strategies(),
    )


def test_scanned_pages_yield_ocr_marked_sentences():
    """Requirements 4.2, 4.9 — the reconciler produces sentences for scanned pages.

    Pre-change sentences came only from the GROBID artifact (a fallback loop in
    the QC pipeline), so a scanned page produced no sentence at all and there
    was nothing for criterion 4.2 to mark.
    """
    unified = _reconcile_mixed_native_and_scanned()
    sentences = unified.semantic.sentences

    if not sentences:
        pytest.fail(
            "BUG CONDITION (Requirements 4.2, 4.9): reconcile() produced no "
            "sentences at all — the reconciler is not the sentence producer, so "
            "scanned pages can never contribute sentences."
        )

    ocr_sentences = [s for s in sentences if s.get("ocr_derived") is True]
    if not ocr_sentences:
        pytest.fail(
            "BUG CONDITION (Requirement 4.9): a document whose page 1 is scanned "
            "with OCR text must yield OCR-derived sentences, but none of "
            f"{[(s.get('text'), s.get('ocr_derived')) for s in sentences]} is "
            "marked ocr_derived=True."
        )
    if not any(s.get("ocr_derived") is False for s in sentences):
        pytest.fail(
            "BUG CONDITION (Requirement 4.4): a mixed document must carry both "
            "markings; every sentence came back ocr_derived=True."
        )
    off_page = [s for s in ocr_sentences if s.get("page_index") != 1]
    if off_page:
        pytest.fail(
            "BUG CONDITION (Requirement 4.2): OCR sentences must carry the page "
            f"they came from; these do not: {off_page!r}."
        )


# ---------------------------------------------------------------------------
# Requirement 4.5 — the OCR region is the sentence's own box
# ---------------------------------------------------------------------------


def _two_ocr_sentences_unified():
    """Two OCR sentences on one page, each from a *different* block.

    ``structural.blocks`` deliberately carries both boxes in page order: the
    pre-change projector scanned that list and took the *first* block on the
    page for every sentence, so both annotations got the first box.
    """
    from quality_control.models import (
        DocumentAlignment,
        SemanticLayer,
        StructuralLayer,
        UnifiedRecord,
    )

    return UnifiedRecord(
        document_id="doc-two-blocks",
        semantic=SemanticLayer(
            sentences=[
                {"text": "First block.", "page_index": 3, "ocr_derived": True,
                 "source": "paddleocr"},
                {"text": "Second block.", "page_index": 3, "ocr_derived": True,
                 "source": "paddleocr"},
            ]
        ),
        structural=StructuralLayer(
            blocks=[
                {"page_index": 3, "text": "First block.", "ocr_derived": True,
                 "block_bbox": _SCANNED_FIXTURE_BBOX_A},
                {"page_index": 3, "text": "Second block.", "ocr_derived": True,
                 "block_bbox": _SCANNED_FIXTURE_BBOX_B},
            ]
        ),
        alignment=DocumentAlignment(
            sentence_to_char_range=[
                {"sentence": "First block.", "start": 0, "end": 12, "page_index": 3,
                 "ocr_derived": True, "bbox": _SCANNED_FIXTURE_BBOX_A, "occurrence": 0},
                {"sentence": "Second block.", "start": 13, "end": 26, "page_index": 3,
                 "ocr_derived": True, "bbox": _SCANNED_FIXTURE_BBOX_B, "occurrence": 0},
            ]
        ),
    )


def _fragment_value(annotation: dict) -> str | None:
    for selector in annotation["target"]["selector"]:
        if selector["type"] == "FragmentSelector":
            return selector["value"]
    return None


def _fragment_region(value: str) -> tuple[int, tuple[float, ...]] | None:
    """Parse ``page=<n>&xywh=<x,y,w,h>`` into comparable numbers.

    Compared numerically so the check does not hinge on whether the producer
    formatted the box as ``10`` or ``10.0``.
    """
    match = re.fullmatch(r"page=(\d+)&xywh=([0-9.,+-]+)", value or "")
    if not match:
        return None
    return int(match.group(1)), tuple(float(n) for n in match.group(2).split(","))


def test_ocr_annotation_region_comes_from_the_sentences_own_box():
    """Requirement 4.5 — each OCR annotation's region is its own source block.

    Pre-change the projector took the first block on the page, so every OCR
    sentence on a page shared one region.
    """
    from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

    annotations = generate_w3c_jsonld(
        project(_two_ocr_sentences_unified()),
        base_uri="urn:evitrace:document:paper-a",
    )
    values = [_fragment_value(a) for a in annotations]

    if len(annotations) != 2 or any(v is None for v in values):
        pytest.fail(
            "BUG CONDITION (Requirement 4.5): both OCR sentences must be "
            f"annotated with a FragmentSelector; got {values!r} for "
            f"{len(annotations)} annotation(s)."
        )
    # bbox is (x0, y0, x1, y1); the media fragment is x,y,w,h.
    expected = [(3, (0.0, 0.0, 100.0, 20.0)), (3, (10.0, 300.0, 200.0, 40.0))]
    regions = [_fragment_region(v) for v in values]
    if regions != expected:
        pytest.fail(
            "BUG CONDITION (Requirement 4.5): each OCR annotation's region must "
            "come from its own source block's bounding box. Expected "
            f"{expected!r}, got {regions!r} (raw {values!r})"
            + (
                " — both sentences share the first block's box on the page."
                if values[0] == values[1]
                else "."
            )
        )


# ---------------------------------------------------------------------------
# Requirement 3.1 — stable annotation identifiers
# ---------------------------------------------------------------------------


_UUID5_URN_RE = re.compile(
    r"^urn:evitrace:anno:"
    r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def test_annotation_identifiers_are_stable_across_two_runs():
    """Requirement 3.1 — the same record and base identifier give the same ids.

    Pre-change ids were minted with ``uuid.uuid4()``, so every run of the same
    paper produced a fresh, unreproducible set.
    """
    from artifact_generation.w3c_annotation import generate_w3c_jsonld, project

    base = "urn:evitrace:document:paper-a"
    first = [a["id"] for a in generate_w3c_jsonld(project(_two_ocr_sentences_unified()),
                                                  base_uri=base)]
    second = [a["id"] for a in generate_w3c_jsonld(project(_two_ocr_sentences_unified()),
                                                   base_uri=base)]

    if first != second:
        pytest.fail(
            "BUG CONDITION (Requirement 3.1): annotating the same record twice "
            f"produced different identifiers.\n  run 1: {first}\n  run 2: {second}"
        )
    if len(set(first)) != len(first):
        pytest.fail(
            "BUG CONDITION (Requirement 3.2): two annotations differing in "
            f"sentence text were given the same identifier: {first}."
        )
    unstable = [i for i in first if not _UUID5_URN_RE.match(i)]
    if unstable:
        pytest.fail(
            "BUG CONDITION (Requirement 3.4): identifiers must be derived from "
            "stable document content (a name-based uuid5, version nibble 5), "
            f"not from randomness; these are not: {unstable}."
        )


# ---------------------------------------------------------------------------
# Requirement 5.1 — the synthesis stage is repaired like an extraction chunk
# ---------------------------------------------------------------------------


_SYNTH_CHUNK_FIELDS = {
    1: [{"field_index": 3, "domain_group": "2. Clinical context", "field_name": "Study design"}],
    2: [{"field_index": 4, "domain_group": "2. Clinical context", "field_name": "Sample size"}],
    3: [{"field_index": 5, "domain_group": "13. Reviewer assessment",
         "field_name": "Synthesis notes"}],
}
_SYNTH_FIELD_LOOKUP = {
    3: {"domain_group": 2, "field_name": "Study design"},
    4: {"domain_group": 2, "field_name": "Sample size"},
    5: {"domain_group": 13, "field_name": "Synthesis notes"},
}
_VALID_SYNTHESIS = json.dumps(
    {"extractions": [{"i": 5, "v": "Repaired verdict", "loc": [], "c": "h"}]}
)


def test_malformed_synthesis_response_is_repaired(tmp_path):
    """Requirements 5.1, 5.2 — synthesis uses the extraction chunks' repair loop.

    Pre-change ``process_pdf`` dispatched the synthesis chunk with a bare
    ``extract_chunk`` call and validated it inline: a malformed response was
    fatal for the paper, with no repair attempt.
    """
    import pipeline.pdf_processor as pdf_processor
    from quality_control.models import Candidate, QCBundle, UnifiedRecord

    remaining = ["this is not json {", _VALID_SYNTHESIS]

    def _extract_chunk(chunk_num, *args, **kwargs):
        if chunk_num == 1:
            return json.dumps(
                {"extractions": [{"i": 3, "v": "RCT", "loc": ["ev-1"], "c": "h"}]}
            )
        if chunk_num == 2:
            return json.dumps(
                {"extractions": [{"i": 4, "v": "120", "loc": ["ev-2"], "c": "m"}]}
            )
        if chunk_num == 3:
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]
        raise AssertionError(f"unexpected chunk {chunk_num}")

    mock_api = MagicMock()
    mock_api.extract_chunk = AsyncMock(side_effect=_extract_chunk)
    mock_api.warm_pdf_cache = AsyncMock()

    bundle = types.SimpleNamespace(
        paper_id="paper_synth_repair",
        evidence_items=[],
        prefilled_fields={},
        evidence_map={
            "ev-1": {"id": "ev-1", "type": "sentence", "text": "Randomised trial evidence."},
            "ev-2": {"id": "ev-2", "type": "sentence", "text": "One hundred twenty people."},
        },
    )
    qc_context = QCBundle(
        branches=[Candidate(source="grobid", index=0, payload="<TEI/>", status=None)],
        unified=UnifiedRecord(
            document_id="paper_synth_repair",
            content={"exact_text": "sample text", "source_pdf_path": ""},
        ),
    )
    openai_config = {
        "chunk_model": "gpt-test",
        "synthesis_model": "gpt-test",
        "enable_cache_prewarm": False,
        "num_chunks": 3,
        "prewarm_synthesis_if_model_diff": False,
        "max_evidence_items_per_chunk": 150,
        "max_evidence_chars_per_chunk": 30000,
    }
    manifest: dict = {}

    with patch.object(pdf_processor, "OUTPUT_DIR", tmp_path), \
         patch.dict(sys.modules, {"agents.openai.api_client": mock_api}), \
         patch.object(pdf_processor, "validate_qc_context_input"), \
         patch.object(pdf_processor, "save_manifest"), \
         patch.object(pdf_processor, "build_or_load_evidence_bundle", return_value=bundle):

        async def _run():
            return await pdf_processor.process_pdf(
                qc_context=qc_context,
                chunk_fields=_SYNTH_CHUNK_FIELDS,
                field_lookup=_SYNTH_FIELD_LOOKUP,
                api_semaphore=asyncio.Semaphore(5),
                manifest=manifest,
                manifest_lock=asyncio.Lock(),
                openai_config=openai_config,
            )

        result = asyncio.run(_run())

    calls = mock_api.extract_chunk.call_args_list
    dispatched = [c.args[0] for c in calls]
    if dispatched != [1, 2, 3, 3]:
        pytest.fail(
            "BUG CONDITION (Requirement 5.1): a synthesis response that fails "
            "parsing must be retried through the same repair loop as the "
            f"extraction chunks. Chunks dispatched: {dispatched} (expected "
            "[1, 2, 3, 3] — chunk 3 once plus one repair attempt)."
        )
    if calls[2].kwargs.get("stage") != "synthesis":
        pytest.fail(
            "BUG CONDITION (Requirement 5.4): the initial synthesis call must "
            f"declare stage='synthesis'; got {calls[2].kwargs.get('stage')!r}."
        )
    if calls[3].kwargs.get("stage") != "validation_repair" or \
            calls[3].kwargs.get("repair_attempt") != 1:
        pytest.fail(
            "BUG CONDITION (Requirement 5.1): the synthesis repair call must use "
            "the shared repair stage. Got stage="
            f"{calls[3].kwargs.get('stage')!r}, repair_attempt="
            f"{calls[3].kwargs.get('repair_attempt')!r}."
        )
    if result is None:
        pytest.fail(
            "BUG CONDITION (Requirement 5.2): a repaired synthesis response must "
            "be used to complete the paper, but process_pdf returned None."
        )
    by_index = {f["field_index"]: f["extracted_value"] for f in result}
    if by_index.get(5) != "Repaired verdict":
        pytest.fail(
            "BUG CONDITION (Requirement 5.2): the repaired synthesis response "
            f"did not reach the output; field 5 = {by_index.get(5)!r}."
        )


# ---------------------------------------------------------------------------
# Requirement 6.3 — the page-classification sidecar is persisted
# ---------------------------------------------------------------------------


def _routing_block(page_index: int, text: str) -> dict:
    return {"text": text, "page_index": page_index, "block_bbox": None, "spans": []}


def test_page_classification_sidecar_is_written_beside_the_tei_cache(tmp_path):
    """Requirement 6.3 — classification is persisted for reuse by later runs.

    Pre-change nothing was persisted: a later run with a TEI-cache hit assumed
    every page was native, which is defect C6 itself.
    """
    from pdf_extractor.extraction.scan_detector import PageScanClassification

    cache_dir = tmp_path / "tei_cache"
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 mixed native/scanned fixture")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()

    qc_config = {
        "ocr": True,
        "quality_control": {
            "ocr": {"rasterization_dpi": 150},
            "grobid_integration": {"failure_behavior": "fallback"},
            "grobid": {
                "url": "http://localhost:8070",
                "timeout": 300,
                "tei_cache_dir": str(cache_dir),
            },
            "scan_detection": {
                "text_density_threshold": 50,
                "alpha_ratio_threshold": 0.60,
                "image_dominance_threshold": 0.85,
            },
        },
        "text_processor": {
            "class": "text_processing.composite.DefaultTextProcessor",
            "sentence_tokenizer": {"backend": "nltk_punkt"},
        },
    }

    classifications = [
        PageScanClassification(page_index=0, is_native=True, triggered_stages=[],
                               stage_values={"word_count": 412.0}),
        PageScanClassification(page_index=1, is_native=False, triggered_stages=[2, 3],
                               stage_values={"word_count": 3.0}),
    ]

    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_doc.__iter__ = MagicMock(return_value=iter([MagicMock(), MagicMock()]))
    mock_fitz.open = MagicMock(return_value=mock_doc)

    with patch("pipeline.extraction_pipeline.extract_with_grobid",
               MagicMock(return_value=("<TEI>x</TEI>", []))), \
         patch("pipeline.extraction_pipeline.extract_with_pdfplumber",
               MagicMock(return_value=[_routing_block(0, "p0"), _routing_block(1, "p1")])), \
         patch("pipeline.extraction_pipeline.extract_with_paddleocr",
               MagicMock(return_value=[_routing_block(0, "o0"), _routing_block(1, "o1")])), \
         patch("pipeline.extraction_pipeline.extract_with_pymupdf",
               MagicMock(return_value=([_routing_block(0, "m0")], []))), \
         patch("pipeline.extraction_pipeline.scan_detector") as mock_scan_mod, \
         patch("pipeline.extraction_pipeline.run_quality_control",
               return_value=MagicMock(unified=MagicMock(content={}))), \
         patch("pipeline.extraction_pipeline._get_text_processor", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_lexical_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline._get_semantic_matcher", return_value=MagicMock()), \
         patch("pipeline.extraction_pipeline.w3c_project", return_value=[]), \
         patch("pipeline.extraction_pipeline.generate_w3c_jsonld", return_value={}), \
         patch.dict(sys.modules, {"fitz": mock_fitz}):

        mock_scan_mod.classify_page = MagicMock(side_effect=list(classifications))
        mock_scan_mod.PageScanClassification = PageScanClassification

        from pipeline.extraction_pipeline import build_qc_bundle

        build_qc_bundle(pdf_path=pdf, pdf_name=pdf.stem, qc_config=qc_config)

    tei_cache = cache_dir / f"{digest}.tei.xml"
    if not tei_cache.exists():
        pytest.fail(
            "Test precondition failed: the TEI cache was not written to "
            f"{tei_cache}, so the sidecar check below would be meaningless."
        )

    sidecar = cache_dir / f"{digest}.pages.json"
    if not sidecar.exists():
        pytest.fail(
            "BUG CONDITION (Requirement 6.3): page classification was computed "
            "but never persisted — no sidecar at "
            f"{sidecar.name}; the cache directory holds "
            f"{sorted(p.name for p in cache_dir.iterdir())}."
        )

    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    pages = payload.get("pages")
    if not isinstance(pages, list) or [p.get("page_index") for p in pages] != [0, 1]:
        pytest.fail(
            "BUG CONDITION (Requirement 6.3): the sidecar must record every "
            f"page's classification; got {payload!r}."
        )
    if [p.get("is_native") for p in pages] != [True, False]:
        pytest.fail(
            "BUG CONDITION (Requirements 6.2, 6.3): the persisted classification "
            "must preserve the scanned page; got "
            f"{[p.get('is_native') for p in pages]!r} (expected [True, False])."
        )


# ---------------------------------------------------------------------------
# Requirement 7 — figure and table section attribution
# ---------------------------------------------------------------------------


def _arxiv_evidence_items() -> list[dict]:
    """Evidence items built from the real arXiv GROBID 0.8.2 TEI fixture."""
    from pipeline.evidence_index import _build_items_from_tei
    from tests.helpers.grobid_tei import load_tei_text

    items, _prefilled, _meta = _build_items_from_tei(load_tei_text("arxiv"), "arxiv", "")
    return items


def test_arxiv_figures_are_attributed_to_more_than_one_section():
    """Requirement 7.3 — figures in different sections get different headings.

    Pre-change the section label was carried over from the last preceding
    ``<div>`` heading, and because real GROBID emits figures as ``<body>``
    siblings *after* the last ``<div>``, every figure in the document collapsed
    onto that single trailing heading.
    """
    items = _arxiv_evidence_items()
    figures = [i for i in items if i["type"] == "figure_caption"]

    if not figures:
        pytest.fail(
            "Test precondition failed (Requirement 7.3): the arXiv fixture "
            "produced no figure_caption evidence items."
        )
    sections = sorted({i["section_path"] for i in figures})
    if len(sections) < 2:
        pytest.fail(
            "BUG CONDITION (Requirement 7.3): the arXiv fixture's figures span "
            f"several sections, but all {len(figures)} figure items were "
            f"attributed to {sections!r} — a single section."
        )


def test_exactly_one_evidence_item_per_figure_and_per_table():
    """Requirement 7.5 — no duplicate items introduced by attribution.

    Pre-change each ``<figure type="table">`` was emitted twice: once as a
    ``figure_caption`` item and once as a ``table`` item.
    """
    from tests.helpers.grobid_tei import load_tei_root, tei_figures, tei_tables

    root = load_tei_root("arxiv")
    n_figures, n_tables = len(tei_figures(root)), len(tei_tables(root))

    items = _arxiv_evidence_items()
    figure_items = [i for i in items if i["type"] == "figure_caption"]
    table_items = [i for i in items if i["type"] == "table"]

    if len(figure_items) != n_figures:
        pytest.fail(
            "BUG CONDITION (Requirement 7.5): the arXiv fixture has "
            f"{n_figures} <figure> elements but produced {len(figure_items)} "
            "figure_caption evidence items."
        )
    if len(table_items) != n_tables:
        pytest.fail(
            "BUG CONDITION (Requirement 7.5): the arXiv fixture has "
            f"{n_tables} <figure type=\"table\"> elements but produced "
            f"{len(table_items)} table evidence items."
        )
    # A table must not also surface as a figure caption.
    table_shaped_figures = [i for i in figure_items if "tab_" in i["xpath"]]
    if table_shaped_figures:
        pytest.fail(
            "BUG CONDITION (Requirement 7.5): tables are emitted twice — these "
            "figure_caption items point at table elements: "
            f"{[i['xpath'] for i in table_shaped_figures]}."
        )
    if len(figure_items) + len(table_items) != n_figures + n_tables:
        pytest.fail(
            "BUG CONDITION (Requirement 7.5): expected exactly "
            f"{n_figures + n_tables} figure/table evidence items, got "
            f"{len(figure_items) + len(table_items)}."
        )


# ---------------------------------------------------------------------------
# Requirement 8 — quality-control extension contract enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "class_name, abstract_method",
    [
        ("QualityMetrics", "passes_check"),
        ("InterRaterMetrics", "compute"),
        ("AdjudicationRules", "adjudicate"),
    ],
)
def test_quality_control_base_rejects_direct_instantiation(class_name, abstract_method):
    """Requirements 8.1, 8.2, 8.3 — the three QC extension points are real ABCs.

    Pre-change the classes carried ``@abstractmethod`` decorators but no
    ``ABCMeta`` metaclass, so the decorators were inert and an implementation
    that omitted a required operation instantiated silently.
    """
    import quality_control.models as models

    cls = getattr(models, class_name)

    if not isinstance(cls, abc.ABCMeta):
        pytest.fail(
            f"BUG CONDITION (Requirement 8.1): {class_name} is not an abstract "
            f"base class (metaclass is {type(cls).__name__}), so its "
            "@abstractmethod decorators are inert and an incomplete "
            "implementation instantiates without error."
        )
    if cls.__abstractmethods__ != frozenset({abstract_method}):
        pytest.fail(
            f"BUG CONDITION (Requirement 8.1): {class_name}.__abstractmethods__ "
            f"is {set(cls.__abstractmethods__)!r}; the required operation "
            f"{abstract_method!r} must be the enforced abstract method."
        )
    try:
        cls()
    except TypeError as exc:
        if abstract_method not in str(exc):
            pytest.fail(
                f"BUG CONDITION (Requirement 8.1): instantiating {class_name} "
                f"raised {exc!r}, which does not name the missing operation "
                f"{abstract_method!r}."
            )
    else:
        pytest.fail(
            f"BUG CONDITION (Requirements 8.1-8.3): {class_name}() succeeded — "
            f"an implementation omitting {abstract_method!r} is accepted instead "
            "of raising an error that names the missing operation."
        )


# ---------------------------------------------------------------------------
# Requirement 10.1 — evidence coverage per extraction chunk
# ---------------------------------------------------------------------------


_DEFAULT_MAX_EVIDENCE_ITEMS = 150
_DEFAULT_MAX_EVIDENCE_CHARS = 30000
_COVERAGE_FLOOR = 0.6


def test_biorxiv_evidence_coverage_meets_the_floor_at_defaults():
    """Requirement 10.1 — ≥60 % of substantive text is selected at defaults.

    Pre-change nothing measured coverage at all: ``select_paper_evidence`` and
    ``EvidenceSelectionStats`` did not exist, so a shortfall was unobservable
    (Requirement 10.4) and the "at least 60 percent" guarantee was unverifiable.
    """
    try:
        from pipeline.evidence_index import (
            EvidenceBundle,
            _build_items_from_tei,
            select_paper_evidence,
        )
    except ImportError as exc:
        pytest.fail(
            "BUG CONDITION (Requirements 10.1, 10.4): evidence coverage is not "
            "measured — the selection/statistics API is absent, so no coverage "
            f"ratio can be computed or recorded ({exc})."
        )

    from tests.helpers.grobid_tei import load_tei_text
    from utils.path_utils import EXTRACTION_MAP

    items, _prefilled, _meta = _build_items_from_tei(load_tei_text("biorxiv"), "biorxiv", "")
    bundle = EvidenceBundle(
        paper_id="biorxiv",
        tei_xml="",
        evidence_items=items,
        evidence_map={item["id"]: item for item in items},
        prefilled_fields={},
        index_path=Path("/nonexistent") / "biorxiv.evidence.json",
    )
    fields = json.loads(Path(EXTRACTION_MAP).read_text(encoding="utf-8"))

    _selected, stats = select_paper_evidence(
        bundle,
        fields,
        max_items=_DEFAULT_MAX_EVIDENCE_ITEMS,
        max_chars=_DEFAULT_MAX_EVIDENCE_CHARS,
    )

    if stats.substantive_chars <= 0:
        pytest.fail(
            "BUG CONDITION (Requirement 10.1): substantive text was measured as "
            f"{stats.substantive_chars} characters, so coverage is meaningless."
        )
    if stats.coverage_ratio < _COVERAGE_FLOOR:
        pytest.fail(
            "BUG CONDITION (Requirement 10.1): at the default evidence budget "
            f"({_DEFAULT_MAX_EVIDENCE_ITEMS} items / "
            f"{_DEFAULT_MAX_EVIDENCE_CHARS} chars) the bioRxiv fixture's "
            f"coverage is {stats.coverage_ratio:.4f}, below the "
            f"{_COVERAGE_FLOOR:.0%} floor "
            f"({stats.selected_chars}/{stats.substantive_chars} chars, "
            f"{stats.selected_items}/{stats.total_items} items)."
        )


# ---------------------------------------------------------------------------
# Requirement 11.2 — GROBID's real coordinate grammar parses to a page
# ---------------------------------------------------------------------------


def test_real_grobid_coords_string_parses_to_a_page():
    """Requirement 11.2 — well-formed ``coords`` never parse to "unknown".

    Pre-change the parser only understood the hand-built fixture grammar
    ``page;x0,y0,x1,y1``. GROBID actually emits ``page,x,y,w,h`` boxes joined
    by ``;``, so every real coordinate string parsed to ``page=None`` and every
    figure/table evidence item came back with no page.
    """
    from pipeline.evidence_index import _parse_coords

    # A genuine single-box string as GROBID 0.8.2 emits it (1-based page).
    parsed = _parse_coords("7,211.98,325.41,344.69,11.28")
    if parsed.get("page") != 7:
        pytest.fail(
            "BUG CONDITION (Requirement 11.2): the real GROBID coords string "
            "'7,211.98,325.41,344.69,11.28' must parse to page 7, got "
            f"{parsed!r} — well-formed input produced an absent result."
        )
    if not parsed.get("coords") or len(parsed["coords"]) != 4:
        pytest.fail(
            "BUG CONDITION (Requirement 11.2): the parser must return the first "
            f"box's bounding box for well-formed input, got {parsed!r}."
        )

    # The same grammar, exercised across the shared fixture table.
    from tests.helpers.grobid_tei import WELL_FORMED_COORD_CASES

    unparsed = [
        case.label
        for case in WELL_FORMED_COORD_CASES
        if _parse_coords(case.coords).get("page") != case.expected_first_page
    ]
    if unparsed:
        pytest.fail(
            "BUG CONDITION (Requirement 11.2): these well-formed GROBID coords "
            f"cases did not parse to their first box's page: {unparsed}."
        )

    # End to end: the real fixture's figures must carry real pages (11.4).
    pageless = [i["id"] for i in _arxiv_evidence_items()
                if i["type"] in ("figure_caption", "table") and i["page"] is None]
    if pageless:
        pytest.fail(
            "BUG CONDITION (Requirements 11.2, 11.4): figure/table evidence "
            "items from the real arXiv fixture carry no page: "
            f"{pageless}."
        )


# ---------------------------------------------------------------------------
# Guard: the synthesis dispatch stays on the shared repair path
# ---------------------------------------------------------------------------


def test_synthesis_dispatch_calls_the_shared_repair_loop():
    """Requirement 5.1 — structural guard beside the functional check above.

    Keeps the call site itself pinned: ``process_pdf`` must reach the synthesis
    stage through ``extract_with_repair(..., stage="synthesis")``.  Note that a
    bare ``stage="synthesis"`` literal is *not* a bug condition on its own — the
    pre-change tree already passed it to the budget check — so the literal has
    to be bound to the ``extract_with_repair`` call.
    """
    from pipeline.pdf_processor import process_pdf

    body = inspect.getsource(process_pdf)
    if "extract_with_repair" not in body:
        pytest.fail(
            "BUG CONDITION (Requirement 5.1): process_pdf() never calls "
            "extract_with_repair — the synthesis stage dispatches the model "
            "directly with no repair loop."
        )
    bound_call = re.compile(
        r'extract_with_repair\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*?stage="synthesis"',
        re.S,
    )
    if not bound_call.search(body):
        pytest.fail(
            "BUG CONDITION (Requirement 5.1): process_pdf() calls "
            "extract_with_repair, but no call passes stage=\"synthesis\" — the "
            "synthesis stage is not on the shared repair path."
        )
