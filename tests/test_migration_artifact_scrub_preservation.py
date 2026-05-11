"""
Preservation Tests — Migration Artifact Scrub

**Property 2: Preservation** — Live Behavior Unchanged After Scrub

These tests encode the baseline behavior that must be preserved after the
migration artifact scrub. They PASS on unfixed code and must continue to
PASS after the fix is applied.

Observation-first methodology: tests are written by running the unfixed code
with non-buggy inputs and recording actual outputs.

Validates: REQ-15, REQ-16, REQ-20, REQ-21, REQ-22
"""

from __future__ import annotations

import json
import sys
import textwrap
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Shared spacy/scispacy mock fixture (prevents NLP model loading in CI)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_scispacy(monkeypatch):
    """Prevent spacy.load('en_core_sci_sm') from running in CI."""
    mock_spacy = MagicMock()
    mock_doc = MagicMock()
    mock_doc.sents = []
    mock_spacy.load.return_value = MagicMock(return_value=mock_doc)
    monkeypatch.setitem(sys.modules, "scispacy", MagicMock())
    monkeypatch.setitem(sys.modules, "spacy", mock_spacy)
    for key in list(sys.modules):
        if "text_processor" in key or "ScispaCy" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

# The ten content keys that must survive the scrub (per design.md REQ-6c/d/e)
_SURVIVING_CONTENT_KEYS = {
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
}

# The eight extractor-agnostic keys that investigate() must return (post-fix names)
# On unfixed code these are the grobid/pymupdf-named keys; after the fix they
# become primary_*/secondary_*. We test the invariant keys that survive both.
_INVESTIGATE_INVARIANT_KEYS = {
    "agreement_metrics",
    "decision",
}

# Required sub-namespaces that load_local_config must always return
_REQUIRED_LOCAL_CONFIG_NAMESPACES = {"quality_control"}


def _make_minimal_config() -> dict:
    """Return a minimal QC config dict sufficient for all preservation tests."""
    return {
        "quality_control": {
            "rater": {"attributes": []},
            "iaa_calculator": {"thresholds": {}, "agreement_metrics": ["metric_a"]},
            "adjudicator": {"strategy": "placeholder"},
            "reconciler": {"enable_tei_export": False, "enable_annotation_export": False},
            "semantic_qc": {"enabled": False},
            "local_metrics": {
                "min_chars_per_page": 100,
                "grobid_vs_native_ratio_threshold": 0.6,
                "long_sentence_word_threshold": 120,
                "long_sentence_max_fraction": 0.12,
                "expected_sections": ["abstract", "introduction", "methods", "results"],
                "caption_table_figure_check_enabled": True,
                "coordinate_coverage_threshold": 0.1,
                "references_in_body_threshold": 0.05,
                "weird_char_ratio_threshold": 0.05,
            },
            "text_fidelity": {"edit_distance_threshold": 0.10},
            "section_verification": {"font_size_tolerance": 1.0},
        },
        "text_processor": {
            "class": "utils.text_processor.TextProcessor",
            "sentence_tokenizer": {"backend": "scispacy", "model": "en_core_sci_sm"},
            "word_tokenizer": {"backend": "simple"},
            "normalizer": {"backend": "nfkc"},
            "comparison": {"metric": "levenshtein", "threshold": 0.85},
            "ocr_cleaning": {"weird_char_threshold": 0.05},
        },
    }


def _make_primary_artifact(document_id: str = "doc-001") -> dict:
    return {
        "document_id": document_id,
        "blocks": [
            {"text": "Introduction", "page_index": 0, "block_type": "section"},
            {"text": "This is a paragraph.", "page_index": 0, "block_type": "paragraph"},
        ],
    }


def _make_secondary_artifact(document_id: str = "doc-001") -> dict:
    return {
        "document_id": document_id,
        "blocks": [
            {"text": "Introduction", "page_index": 0, "block_type": "section"},
            {"text": "This is a paragraph.", "page_index": 0, "block_type": "paragraph"},
        ],
    }


def _make_adjudication_decisions() -> dict:
    return {
        "primary_extractor": "grobid",
        "confidence": 0.9,
        "rationale": "grobid selected: 1/1 branches passed",
    }


# ---------------------------------------------------------------------------
# Observation: reconciler.reconcile() with non-None adjudication_decisions
# ---------------------------------------------------------------------------


def test_reconcile_returns_unified_record_with_non_none_adjudication():
    """Observation: reconcile() with non-None adjudication_decisions returns UnifiedRecord.

    **Validates: Requirements REQ-15**
    """
    from quality_control.reconciler import reconcile
    from quality_control.models import UnifiedRecord

    result = reconcile(
        primary_artifact=_make_primary_artifact(),
        secondary_artifact=_make_secondary_artifact(),
        adjudication_decisions=_make_adjudication_decisions(),
        config=_make_minimal_config(),
    )

    assert isinstance(result, UnifiedRecord), (
        f"reconcile() must return a UnifiedRecord, got {type(result).__name__}"
    )


def test_reconcile_content_contains_surviving_keys():
    """Observation: reconcile() content dict contains all ten surviving keys.

    **Validates: Requirements REQ-15, REQ-16**
    """
    from quality_control.reconciler import reconcile

    result = reconcile(
        primary_artifact=_make_primary_artifact(),
        secondary_artifact=_make_secondary_artifact(),
        adjudication_decisions=_make_adjudication_decisions(),
        config=_make_minimal_config(),
    )

    content = result.content
    assert isinstance(content, dict), "UnifiedRecord.content must be a dict"

    missing = _SURVIVING_CONTENT_KEYS - set(content.keys())
    assert not missing, (
        f"UnifiedRecord.content is missing required keys: {sorted(missing)}"
    )


def test_reconcile_exact_text_is_preserved():
    """Observation: exact_text in content is a string (may be empty for empty blocks).

    **Validates: Requirements REQ-15**
    """
    from quality_control.reconciler import reconcile

    result = reconcile(
        primary_artifact=_make_primary_artifact(),
        secondary_artifact=_make_secondary_artifact(),
        adjudication_decisions=_make_adjudication_decisions(),
        config=_make_minimal_config(),
    )

    assert "exact_text" in result.content, "exact_text must be present in content"
    assert isinstance(result.content["exact_text"], str), (
        "exact_text must be a string"
    )


def test_reconcile_document_id_propagated():
    """Observation: document_id is propagated from artifact to UnifiedRecord.

    **Validates: Requirements REQ-15**
    """
    from quality_control.reconciler import reconcile

    doc_id = "test-doc-preservation-001"
    result = reconcile(
        primary_artifact=_make_primary_artifact(document_id=doc_id),
        secondary_artifact=_make_secondary_artifact(document_id=doc_id),
        adjudication_decisions=_make_adjudication_decisions(),
        config=_make_minimal_config(),
    )

    assert result.document_id == doc_id, (
        f"document_id must be propagated: expected {doc_id!r}, got {result.document_id!r}"
    )
    assert result.content["document_id"] == doc_id, (
        f"content['document_id'] must match: expected {doc_id!r}, "
        f"got {result.content['document_id']!r}"
    )


# ---------------------------------------------------------------------------
# Observation: iaa_calculator.investigate() return structure
# ---------------------------------------------------------------------------


def test_investigate_returns_dict_with_decision_deferred():
    """Observation: investigate() returns a dict with decision == 'deferred_to_adjudicator'.

    **Validates: Requirements REQ-15**
    """
    from quality_control.iaa_calculator import investigate

    cfg = _make_minimal_config()
    # On unfixed code, investigate() takes grobid_observation, pymupdf_observation,
    # grobid_artifact, pymupdf_artifact, config
    result = investigate(
        {"status": "ok"},
        {"status": "ok"},
        {"grobid": {"id": "g1"}},
        {"pymupdf": {"id": "p1"}},
        cfg,
    )

    assert isinstance(result, dict), "investigate() must return a dict"
    assert result.get("decision") == "deferred_to_adjudicator", (
        f"investigate() must return decision='deferred_to_adjudicator', "
        f"got {result.get('decision')!r}"
    )


def test_investigate_returns_agreement_metrics_key():
    """Observation: investigate() returns 'agreement_metrics' key.

    **Validates: Requirements REQ-15**
    """
    from quality_control.iaa_calculator import investigate

    cfg = _make_minimal_config()
    result = investigate(
        {"status": "ok"},
        {"status": "ok"},
        {"grobid": {"id": "g1"}},
        {"pymupdf": {"id": "p1"}},
        cfg,
    )

    assert "agreement_metrics" in result, (
        "investigate() must return 'agreement_metrics' key"
    )


# ---------------------------------------------------------------------------
# Observation: load_local_config() returns required sub-namespaces
# ---------------------------------------------------------------------------


def test_load_local_config_returns_quality_control_namespace(tmp_path):
    """Observation: load_local_config() returns a dict with 'quality_control' sub-namespace.

    **Validates: Requirements REQ-20**
    """
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            pdfs_path: /tmp
        """),
        encoding="utf-8",
    )

    from utils.config_utils import load_local_config

    config = load_local_config(str(cfg_file))

    assert isinstance(config, dict), "load_local_config() must return a dict"
    assert "quality_control" in config, (
        "load_local_config() must return a dict with 'quality_control' sub-namespace"
    )


def test_load_local_config_quality_control_has_local_metrics(tmp_path):
    """Observation: load_local_config() quality_control sub-namespace has local_metrics.

    **Validates: Requirements REQ-20**
    """
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            pdfs_path: /tmp
        """),
        encoding="utf-8",
    )

    from utils.config_utils import load_local_config

    config = load_local_config(str(cfg_file))
    qc = config["quality_control"]

    assert "local_metrics" in qc, (
        "quality_control sub-namespace must contain 'local_metrics'"
    )
    assert "scan_detection" in qc, (
        "quality_control sub-namespace must contain 'scan_detection'"
    )


def test_load_local_config_ocr_key_retained(tmp_path):
    """Observation: load_local_config() retains the 'ocr' boolean key.

    **Validates: Requirements REQ-20**
    """
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            pdfs_path: /tmp
        """),
        encoding="utf-8",
    )

    from utils.config_utils import load_local_config

    config = load_local_config(str(cfg_file))

    assert "ocr" in config, "load_local_config() must retain the 'ocr' key"
    assert isinstance(config["ocr"], bool), "'ocr' must be a boolean"


# ---------------------------------------------------------------------------
# Observation: rater.observe() returns a report object
# ---------------------------------------------------------------------------


def test_rater_observe_returns_object():
    """Observation: rater.observe() returns a QualityReport instance.

    **Validates: Requirements REQ-15**
    """
    from quality_control import rater
    from quality_control.models import Candidate
    from quality_control.defaults import QualityReport

    cfg = _make_minimal_config()
    branch = Candidate(source="grobid", index=0, payload="<TEI/>", status=None)
    result = rater.observe(branch, cfg)

    assert result is not None, "rater.observe() must not return None"
    assert isinstance(result, QualityReport), (
        f"rater.observe() must return a QualityReport, got {type(result).__name__}"
    )


# ---------------------------------------------------------------------------
# Observation: run_quality_control() returns QCBundle with populated unified
# ---------------------------------------------------------------------------


def test_run_quality_control_returns_qccontext_with_unified():
    """Observation: run_quality_control() returns a QCBundle with a populated unified field.

    **Validates: Requirements REQ-15, REQ-16**
    """
    from quality_control.models import Candidate, QCBundle
    from quality_control.quality_control import run_quality_control

    branches = [
        Candidate(source="grobid", index=0, payload="<TEI/>", status=None),
        Candidate(source="pymupdf", index=1, payload=[], status=None),
    ]
    cfg = _make_minimal_config()

    ctx = run_quality_control(branches, "doc-001", cfg)

    assert isinstance(ctx, QCBundle), (
        f"run_quality_control() must return a QCBundle, got {type(ctx).__name__}"
    )
    assert ctx.unified is not None, (
        "QCBundle.unified must be populated after run_quality_control()"
    )


def test_run_quality_control_unified_has_content():
    """Observation: run_quality_control() unified field has a content dict.

    **Validates: Requirements REQ-15, REQ-16**
    """
    from quality_control.models import Candidate
    from quality_control.quality_control import run_quality_control

    branches = [
        Candidate(source="grobid", index=0, payload="<TEI/>", status=None),
        Candidate(source="pymupdf", index=1, payload=[], status=None),
    ]
    cfg = _make_minimal_config()

    ctx = run_quality_control(branches, "doc-001", cfg)

    assert isinstance(ctx.unified.content, dict), (
        "QCBundle.unified.content must be a dict"
    )


# ---------------------------------------------------------------------------
# Property-based test: reconcile() content is JSON-serializable with all ten keys
# ---------------------------------------------------------------------------


@given(
    document_id=st.text(min_size=1, max_size=64, alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"
    )),
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=30)
def test_reconcile_content_json_serializable_with_all_surviving_keys(
    document_id: str,
    confidence: float,
):
    """Property: for any document_id and adjudication_decisions, reconcile() returns
    a UnifiedRecord whose content is JSON-serializable and contains all ten surviving keys.

    **Validates: Requirements REQ-15**
    """
    from quality_control.reconciler import reconcile
    from quality_control.models import UnifiedRecord

    adjudication_decisions = {
        "primary_extractor": "grobid",
        "confidence": confidence,
        "rationale": "test",
    }

    result = reconcile(
        primary_artifact={"document_id": document_id, "blocks": []},
        secondary_artifact={"document_id": document_id, "blocks": []},
        adjudication_decisions=adjudication_decisions,
        config=_make_minimal_config(),
    )

    assert isinstance(result, UnifiedRecord)

    # Content must be JSON-serializable
    try:
        serialized = json.dumps(result.content)
    except (TypeError, ValueError) as exc:
        pytest.fail(
            f"UnifiedRecord.content must be JSON-serializable, got error: {exc}"
        )

    # Content must contain all ten surviving keys
    missing = _SURVIVING_CONTENT_KEYS - set(result.content.keys())
    assert not missing, (
        f"UnifiedRecord.content missing required keys: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Property-based test: investigate() returns dict with decision and invariant keys
# ---------------------------------------------------------------------------


@given(
    metric_names=st.lists(
        st.text(min_size=1, max_size=32, alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_"
        )),
        min_size=0,
        max_size=5,
    )
)
@settings(max_examples=30)
def test_investigate_returns_decision_deferred_for_any_metrics(metric_names: list[str]):
    """Property: for any list of metric names, investigate() returns a dict with
    decision == 'deferred_to_adjudicator' and the invariant keys.

    **Validates: Requirements REQ-15**
    """
    from quality_control.iaa_calculator import investigate

    cfg = {
        "quality_control": {
            "iaa_calculator": {
                "thresholds": {},
                "agreement_metrics": metric_names,
            }
        }
    }

    result = investigate(
        {"status": "ok"},
        {"status": "ok"},
        {"grobid": {"id": "g1"}},
        {"pymupdf": {"id": "p1"}},
        cfg,
    )

    assert isinstance(result, dict), "investigate() must return a dict"
    assert result.get("decision") == "deferred_to_adjudicator", (
        f"investigate() must return decision='deferred_to_adjudicator', "
        f"got {result.get('decision')!r}"
    )
    assert "agreement_metrics" in result, (
        "investigate() must return 'agreement_metrics' key"
    )
    # agreement_metrics must contain exactly the requested metric names
    assert set(result["agreement_metrics"].keys()) == set(metric_names), (
        f"agreement_metrics keys must match metric_names: "
        f"expected {sorted(metric_names)}, got {sorted(result['agreement_metrics'].keys())}"
    )


# ---------------------------------------------------------------------------
# Property-based test: load_local_config() with omitted quality_control section
# ---------------------------------------------------------------------------


@given(
    ocr=st.booleans(),
)
@settings(max_examples=20)
def test_load_local_config_returns_required_namespaces_when_qc_omitted(ocr: bool):
    """Property: for any config YAML that omits quality_control, load_local_config()
    returns a dict containing all required sub-namespaces with correct default values.

    **Validates: Requirements REQ-20**
    """
    import tempfile
    import os

    from utils.config_utils import load_local_config

    yaml_content = textwrap.dedent(f"""\
        pdfs_path: /tmp
        ocr: {str(ocr).lower()}
    """)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        cfg_path = f.name

    try:
        config = load_local_config(cfg_path)
    finally:
        os.unlink(cfg_path)

    assert isinstance(config, dict), "load_local_config() must return a dict"
    assert "quality_control" in config, (
        "load_local_config() must return 'quality_control' sub-namespace"
    )
    qc = config["quality_control"]
    assert "local_metrics" in qc, (
        "quality_control must contain 'local_metrics' with default values"
    )
    assert "scan_detection" in qc, (
        "quality_control must contain 'scan_detection' with default values"
    )
    assert "iaa_calculator" in qc, (
        "quality_control must contain 'iaa_calculator' with default values"
    )
    assert "rater" in qc, (
        "quality_control must contain 'rater' with default values"
    )


# ---------------------------------------------------------------------------
# Unit preservation: concern strategies are callable with same arguments
# ---------------------------------------------------------------------------


def test_text_fidelity_concern_is_callable():
    """Unit: TextFidelityConcern.reconcile() is callable and returns expected output type.

    **Validates: Requirements REQ-15**
    """
    from quality_control.concerns import TextFidelityConcern

    concern = TextFidelityConcern(source_label="pdfplumber")

    mock_tp = MagicMock()
    mock_tp.compare.return_value = 0.9

    result = concern.reconcile("primary text", "reference text", mock_tp)

    assert isinstance(result, dict), "TextFidelityConcern.reconcile() must return a dict"
    assert "edit_distance" in result
    assert "agreement" in result
    assert "preferred_reading" in result
    assert "confidence" in result
    assert result["preferred_reading"] == "reference text", (
        "TextFidelityConcern.reconcile() must return reference as preferred_reading"
    )


def test_section_verification_concern_is_callable():
    """Unit: SectionVerificationConcern.reconcile() is callable and returns a float.

    **Validates: Requirements REQ-15**
    """
    from quality_control.concerns import SectionVerificationConcern

    concern = SectionVerificationConcern()

    mock_tp = MagicMock()
    mock_tp.compare.return_value = 0.95

    result = concern.reconcile(
        {"heading": "Introduction", "depth": 1, "label": ""},
        {"text": "Introduction", "font_size": 14.0},
        mock_tp,
    )

    assert isinstance(result, float), (
        f"SectionVerificationConcern.reconcile() must return a float, got {type(result).__name__}"
    )
    assert 0.0 <= result <= 1.0, (
        f"SectionVerificationConcern.reconcile() must return a value in [0.0, 1.0], got {result}"
    )


def test_table_figure_merge_concern_is_callable():
    """Unit: TableFigureMergeConcern.merge() is callable and returns expected output type.

    **Validates: Requirements REQ-15**
    """
    from quality_control.concerns import TableFigureMergeConcern

    concern = TableFigureMergeConcern(primary_label="grobid", reference_label="pdfplumber")

    result = concern.merge(
        {"caption": "Table 1", "bbox": [0, 0, 100, 50]},
        {"text": "Table 1", "bbox": [0, 0, 100, 50]},
    )

    assert isinstance(result, dict), "TableFigureMergeConcern.merge() must return a dict"
    assert "agreement" in result
    assert result["agreement"] == "present"
    assert "merged_text" in result


def test_all_concern_strategies_produce_same_output_types_on_same_inputs():
    """Unit: all concern strategies produce consistent output types when called
    with the same arguments.

    **Validates: Requirements REQ-15**
    """
    from quality_control.concerns import (
        TextFidelityConcern,
        SectionVerificationConcern,
        TableFigureMergeConcern,
    )

    mock_tp = MagicMock()
    mock_tp.compare.return_value = 0.85

    # TextFidelityConcern: always returns dict
    tf = TextFidelityConcern()
    r1 = tf.reconcile("a", "b", mock_tp)
    r2 = tf.reconcile("x", "y", mock_tp)
    assert type(r1) == type(r2) == dict

    # SectionVerificationConcern: always returns float
    sv = SectionVerificationConcern()
    s1 = sv.reconcile({"heading": "A"}, {"text": "A", "font_size": 12.0}, mock_tp)
    s2 = sv.reconcile({"heading": "B"}, {"text": "B", "font_size": 10.0}, mock_tp)
    assert type(s1) == type(s2) == float

    # TableFigureMergeConcern: always returns dict
    tfm = TableFigureMergeConcern()
    m1 = tfm.merge({"caption": "T1"}, {"text": "T1"})
    m2 = tfm.merge({"caption": "T2"}, {"text": "T2"})
    assert type(m1) == type(m2) == dict


# ---------------------------------------------------------------------------
# Unit preservation: load_qc_config deep-merges _QC_DEFAULTS
# ---------------------------------------------------------------------------


def test_load_qc_config_deep_merges_defaults(tmp_path):
    """Unit: load_qc_config() deep-merges _QC_DEFAULTS and returns the full QC config dict.

    **Validates: Requirements REQ-20**
    """
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            pdfs_path: /tmp
        """),
        encoding="utf-8",
    )

    from utils.config_utils import load_qc_config, _QC_DEFAULTS

    config = load_qc_config(str(cfg_file))

    assert isinstance(config, dict), "load_qc_config() must return a dict"
    assert "quality_control" in config, (
        "load_qc_config() must return a dict with 'quality_control' key"
    )
    assert "text_processor" in config, (
        "load_qc_config() must return a dict with 'text_processor' key"
    )

    # Verify deep-merge: all default QC keys must be present
    qc = config["quality_control"]
    for key in _QC_DEFAULTS["quality_control"]:
        assert key in qc, (
            f"load_qc_config() must deep-merge _QC_DEFAULTS: missing key '{key}'"
        )


def test_load_qc_config_user_values_override_defaults(tmp_path):
    """Unit: load_qc_config() user values override _QC_DEFAULTS.

    **Validates: Requirements REQ-20**
    """
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        textwrap.dedent("""\
            quality_control:
              semantic_qc:
                enabled: true
                similarity_threshold: 0.99
        """),
        encoding="utf-8",
    )

    from utils.config_utils import load_qc_config

    config = load_qc_config(str(cfg_file))

    assert config["quality_control"]["semantic_qc"]["enabled"] is True, (
        "User value 'enabled: true' must override default 'enabled: false'"
    )
    assert config["quality_control"]["semantic_qc"]["similarity_threshold"] == 0.99, (
        "User value 'similarity_threshold: 0.99' must override default"
    )


# ---------------------------------------------------------------------------
# Unit preservation: pdf_extractor.extraction exports required symbols
# ---------------------------------------------------------------------------


def test_pdf_extractor_extraction_exports_required_symbols():
    """Unit: pdf_extractor.extraction exports all required symbols.

    **Validates: Requirements REQ-15**
    """
    import pdf_extractor.extraction as extraction

    required_exports = [
        "extract_with_pymupdf",
        "extract_with_pdfplumber",
        "extract_with_paddleocr",
        "scan_detector",
        "schemas",
        "PyMuPDF",
    ]

    for symbol in required_exports:
        assert hasattr(extraction, symbol), (
            f"pdf_extractor.extraction must export '{symbol}'"
        )


def test_pdf_extractor_extraction_exports_are_callable_or_module():
    """Unit: pdf_extractor.extraction exports are callable functions or modules.

    **Validates: Requirements REQ-15**
    """
    import pdf_extractor.extraction as extraction
    import types

    callable_exports = [
        "extract_with_pymupdf",
        "extract_with_pdfplumber",
        "extract_with_paddleocr",
    ]
    module_exports = ["scan_detector", "schemas", "PyMuPDF"]

    for name in callable_exports:
        obj = getattr(extraction, name)
        assert callable(obj), (
            f"pdf_extractor.extraction.{name} must be callable"
        )

    for name in module_exports:
        obj = getattr(extraction, name)
        assert isinstance(obj, types.ModuleType), (
            f"pdf_extractor.extraction.{name} must be a module"
        )
