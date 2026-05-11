"""
Test Task 13: Domain-agnosticism invariant tests.

Verifies that the QC pipeline and related modules are truly domain-agnostic,
not coupled to PDF-specific libraries or extractor names.
"""

import inspect
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from quality_control import quality_control, reconciler, adjudicator
from quality_control.concerns import (
    TextFidelityConcern,
    SectionVerificationConcern,
    TableFigureMergeConcern,
    MissingContributionError,
)
from quality_control.models import (
    Candidate,
    QCBundle,
    QualityMetrics,
    InterRaterMetrics,
    AdjudicationRules,
    UnifiedRecord,
)
from pdf_extractor.extraction.scan_detector import classify_page, PageScanClassification
from text_processing.base import TextProcessor
from text_processing.composite import DefaultTextProcessor


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


# ============================================================================
# Task 13.1: Test run_pipeline domain isolation
# ============================================================================


class TestRunPipelineDomainIsolation:
    """Verify run_pipeline is domain-agnostic and callable with all-mock callables."""

    def test_run_pipeline_callable_with_all_mocks(self):
        """Test that run_pipeline accepts and executes all-mock callables."""
        # Create minimal mock branch (domain-agnostic)
        mock_branch = Candidate(
            source="mock_agent",
            index=0,
            payload={"blocks": [{"text": "mock text"}]},
            status=None,
        )

        # Create mock callables returning dummy model instances
        def mock_rater_fn(branch, branches, index, config):
            report = QualityMetrics()
            report.status = "pass"
            return report

        def mock_iaa_fn(reports, config):
            iaa_metrics = InterRaterMetrics()
            return iaa_metrics

        def mock_adjudicator_fn(reports, iaa_metrics, config):
            decision = AdjudicationRules()
            decision.primary_extractor = "mock_agent"
            decision.confidence = 1.0
            return decision

        def mock_reconciler_fn(decision, branches, config):
            unified = UnifiedRecord(
                document_id="test_doc",
                content={"text": "reconciled content"},
            )
            return unified

        # Execute pipeline with all mocks
        ctx = quality_control.run_pipeline(
            branches=[mock_branch],
            rater_fn=mock_rater_fn,
            iaa_fn=mock_iaa_fn,
            adjudicator_fn=mock_adjudicator_fn,
            reconciler_fn=mock_reconciler_fn,
            config={},
        )

        # Verify pipeline executed and populated context
        assert isinstance(ctx, QCBundle)
        assert len(ctx.branches) == 1
        assert len(ctx.reports) == 1
        assert ctx.iaa_metrics is not None
        assert ctx.decision is not None
        assert ctx.unified is not None

    def test_run_pipeline_with_non_pdf_branch_payload(self):
        """Test that non-PDF branch payloads do not cause pipeline to raise."""
        # Create branch with generic payload (not PDF-specific)
        mock_branch = Candidate(
            source="generic_agent",
            index=0,
            payload="Plain text content without PDF structure",  # Simple string
            status=None,
        )

        def mock_rater_fn(branch, branches, index, config):
            report = QualityMetrics()
            report.status = "pass"
            return report

        def mock_iaa_fn(reports, config):
            return InterRaterMetrics()

        def mock_adjudicator_fn(reports, iaa_metrics, config):
            decision = AdjudicationRules()
            decision.primary_extractor = "generic_agent"
            return decision

        def mock_reconciler_fn(decision, branches, config):
            return UnifiedRecord(
                document_id="generic_doc",
                content={"result": "generic reconciliation"},
            )

        # Should not raise on non-PDF payload
        ctx = quality_control.run_pipeline(
            branches=[mock_branch],
            rater_fn=mock_rater_fn,
            iaa_fn=mock_iaa_fn,
            adjudicator_fn=mock_adjudicator_fn,
            reconciler_fn=mock_reconciler_fn,
        )

        assert ctx.unified is not None

    def test_run_pipeline_source_contains_no_forbidden_strings(self):
        """Verify run_pipeline implementation contains no PDF-specific lib strings."""
        source = inspect.getsource(quality_control.run_pipeline)

        forbidden_strings = [
            "fitz",
            "grobid",
            "pdfplumber",
            "PyMuPDF",
            "PaddleOCR",
            "TEI",
            "scan",
        ]

        for forbidden in forbidden_strings:
            assert (
                forbidden not in source
            ), f"run_pipeline source contains forbidden string '{forbidden}'"


# ============================================================================
# Task 13.2: Verify acceptance criteria grep checks
# ============================================================================


class TestAcceptanceCriteriaVerification:
    """Verify that acceptance criteria are met via grep and code inspection."""

    def test_no_tesseract_references(self):
        """Verify no extract_with_tesseract or pytesseract references in codebase."""
        repo_root = Path(__file__).parent.parent.parent
        try:
            # Exclude documentation and spec files
            result = subprocess.run(
                [
                    "git",
                    "grep",
                    "-l",
                    "extract_with_tesseract",
                    "--",
                    ":(exclude).kiro/",
                    ":(exclude)**.md",
                    ":(exclude)**/test_*.py",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            # If git grep found matches, result.stdout will be non-empty
            assert (
                result.stdout.strip() == ""
            ), f"Found extract_with_tesseract references in code: {result.stdout}"
        except FileNotFoundError:
            pytest.skip("git not available")

        try:
            result = subprocess.run(
                [
                    "git",
                    "grep",
                    "-l",
                    "pytesseract",
                    "--",
                    ":(exclude).kiro/",
                    ":(exclude)**.md",
                    ":(exclude)**/test_*.py",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            assert (
                result.stdout.strip() == ""
            ), f"Found pytesseract references in code: {result.stdout}"
        except FileNotFoundError:
            pytest.skip("git not available")

    def test_no_regex_sentence_splitter_references(self):
        """Verify _split_sentences and _RE_SENTENCE_SPLIT are removed."""
        repo_root = Path(__file__).parent.parent.parent
        try:
            result = subprocess.run(
                [
                    "git",
                    "grep",
                    "-l",
                    "_split_sentences\\|_RE_SENTENCE_SPLIT",
                    "--",
                    ":(exclude).kiro/",
                    ":(exclude)**/*.md",
                    ":(exclude)**/test_*.py",  # Exclude old test files
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                env={**dict(__import__("os").environ), "LC_ALL": "C"},
            )
            assert (
                result.stdout.strip() == ""
            ), f"Found _split_sentences/_RE_SENTENCE_SPLIT references in code: {result.stdout}"
        except FileNotFoundError:
            pytest.skip("git not available")

    def test_reconciler_no_hardcoded_extractor_names_in_control_flow(self):
        """Verify reconciler.py contains no hardcoded 'grobid' or 'pdfplumber' in control logic."""
        source = inspect.getsource(reconciler.reconcile)

        # Ensure no control-flow decisions use hardcoded extractor names
        lines = source.split("\n")
        for i, line in enumerate(lines):
            # Skip comments and strings
            if "#" in line:
                line = line[: line.index("#")]

            # Check for control-flow patterns with hardcoded names
            if any(
                keyword in line for keyword in ["if", "elif", "while", "for", "return"]
            ):
                if "grobid" in line.lower() or "pdfplumber" in line.lower():
                    # This is acceptable only if it's a comment or string literal
                    # More strict: reject if it's in an actual expression
                    if not (line.strip().startswith("#") or '"""' in line or "'''" in line):
                        pytest.fail(
                            f"Found hardcoded extractor name in control flow at line {i}: {line}"
                        )

    def test_adjudicator_no_hardcoded_preferred_source_assignment(self):
        """Verify adjudicator.py has no hardcoded preferred_source assignments."""
        source = inspect.getsource(adjudicator.adjudicate)

        # Check that preferred_source is not assigned a hardcoded extractor name
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "preferred_source" in line and "=" in line:
                # Should never be assigned a literal string like "grobid" or "pdfplumber"
                if '"grobid"' in line or "'grobid'" in line:
                    pytest.fail(
                        f"Found hardcoded 'grobid' assigned to preferred_source: {line}"
                    )
                if '"pdfplumber"' in line or "'pdfplumber'" in line:
                    pytest.fail(
                        f"Found hardcoded 'pdfplumber' assigned to preferred_source: {line}"
                    )

    def test_text_fidelity_concern_asymmetric_preferred_reading(self):
        """Verify TextFidelityConcern.reconcile swaps preferred_reading based on argument order."""
        tp = DefaultTextProcessor()
        concern = TextFidelityConcern(source_label="source_a")

        text_a = "The quick brown fox"
        text_b = "The slow brown fox"

        # Reconcile with a as primary and b as reference
        result1 = concern.reconcile(text_a, text_b, tp)
        reading1 = result1.get("preferred_reading")

        # Reconcile with b as primary and a as reference
        result2 = concern.reconcile(text_b, text_a, tp)
        reading2 = result2.get("preferred_reading")

        # When arguments differ, preferred_reading should be the reference (second arg)
        assert reading1 == text_b, "preferred_reading should be the reference argument"
        assert reading2 == text_a, "preferred_reading should be the reference argument"
        assert (
            reading1 != reading2
        ), "Swapping argument order should produce different preferred_reading"

    def test_table_figure_merge_raises_on_missing_contribution(self):
        """Verify TableFigureMergeConcern.merge raises on None arguments."""
        concern = TableFigureMergeConcern(primary_label="grobid", reference_label="pdfplumber")

        primary_record = {"text": "table data"}
        reference_record = {"text": "reference table data"}

        # Both present should work
        result = concern.merge(primary_record, reference_record)
        assert "merged_text" in result

        # primary=None should raise
        with pytest.raises(MissingContributionError):
            concern.merge(None, reference_record)

        # reference=None should raise
        with pytest.raises(MissingContributionError):
            concern.merge(primary_record, None)

    def test_scan_detector_returns_page_scan_classification(self):
        """Verify scan_detector.classify_page returns PageScanClassification with triggered_stages."""
        try:
            # Mock page object with required methods/properties
            mock_page = MagicMock()
            mock_page.get_text.return_value = "Sample text content"
            mock_page.get_images.return_value = []  # No images
            # Mock mediabox to return a proper tuple-like value
            mock_page.mediabox = MagicMock()
            mock_page.mediabox.__iter__ = MagicMock(return_value=iter([0, 0, 612, 792]))
            # Make sure comparisons work for calculating page area
            mock_page.mediabox.__getitem__ = MagicMock(side_effect=lambda i: [0, 0, 612, 792][i])

            tp = DefaultTextProcessor()
            config = {
                "quality_control": {
                    "scan_detection": {
                        "alpha_threshold": 0.5,
                        "word_ratio_threshold": 0.4,
                        "font_variance_threshold": 0.3,
                        "entropy_threshold": 4.0,
                    }
                }
            }

            # Classify a native (non-scanned) page
            result = classify_page(mock_page, tp, config)

            assert isinstance(result, PageScanClassification)
            assert hasattr(result, "is_native")
            assert hasattr(result, "triggered_stages")
            assert isinstance(result.triggered_stages, list)
            assert hasattr(result, "stage_values")
            assert isinstance(result.stage_values, dict)
        except TypeError as e:
            # Skip if there are issues with mock comparison
            if "MagicMock" in str(e):
                pytest.skip(f"Mock comparison issue: {e}")
            raise

    def test_custom_sentence_segment_backend_injection(self):
        """Verify that TextProcessor can use different sentence segmentation backends."""
        try:
            # Use a simpler backend (nltk_punkt) instead of scispacy to avoid import issues.
            # _load_text_processor reads config["text_processor"], not config["quality_control"]["text_processor"].
            config = {
                "text_processor": {
                    "sentence_tokenizer": {
                        "backend": "nltk_punkt"
                    }
                }
            }

            tp = quality_control._load_text_processor(config)

            # The text processor should be callable for sentence tokenization
            # (_sentence_backend is lazy-loaded on first tokenize_sentences call)

            # Test that tokenize_sentences works with the configured backend
            text = "First sentence. Second sentence. Third sentence."
            result = tp.tokenize_sentences(text)

            assert isinstance(result, list), "tokenize_sentences should return a list"
            assert len(result) > 0, "tokenize_sentences should return at least one sentence"

            # All elements should be strings
            for sentence in result:
                assert isinstance(sentence, str), f"Expected str, got {type(sentence)}"
        except ImportError as e:
            # Skip if required backend is not installed
            pytest.skip(f"Backend not available: {e}")

    def test_reconciler_concern_routing_not_hardcoded(self):
        """Verify reconciler concern routing comes from injectable strategies, not hardcoded logic."""
        # Verify the source code has no hardcoded extractor names in the reconcile function
        source = inspect.getsource(reconciler.reconcile)

        # Check that control flow doesn't hardcode extractor names
        forbidden_patterns = [
            'extractor == "grobid"',
            'extractor == "pdfplumber"',
            'if "grobid"',
            'if "pdfplumber"',
        ]

        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"Found forbidden pattern '{pattern}' in reconciler.reconcile"
            )

        # Verify the function accepts injectable strategy parameters
        sig = inspect.signature(reconciler.reconcile)
        assert "text_fidelity_strategy" in sig.parameters
        assert "section_strategy" in sig.parameters
        assert "table_figure_strategy" in sig.parameters
        assert "text_processor" in sig.parameters


# ============================================================================
# Integration tests verifying full pipeline domain-agnosticism
# ============================================================================


class TestFullPipelineDomainAgnosticism:
    """Verify the full QC pipeline is truly domain-agnostic."""

    def test_pipeline_with_generic_agent_data(self):
        """Test run_pipeline with completely generic (non-PDF) agent outputs."""
        # Simulate LLM or multi-agent scenario
        agent_outputs = [
            Candidate(
                source="agent_a",
                index=0,
                payload={"extraction": "Agent A result"},
                status=None,
            ),
            Candidate(
                source="agent_b",
                index=1,
                payload={"extraction": "Agent B result"},
                status=None,
            ),
        ]

        def generic_rater_fn(branch, branches, index, config):
            report = QualityMetrics()
            report.status = "pass"
            return report

        def generic_iaa_fn(reports, config):
            return InterRaterMetrics()

        def generic_adjudicator_fn(reports, iaa_metrics, config):
            decision = AdjudicationRules()
            decision.primary_extractor = "agent_a"
            return decision

        def generic_reconciler_fn(decision, branches, config):
            primary_branch = next(
                (b for b in branches if b.extractor == decision.primary_extractor),
                branches[0],
            )
            return UnifiedRecord(
                document_id="task_1",
                content={"reconciled": primary_branch.payload},
            )

        ctx = quality_control.run_pipeline(
            branches=agent_outputs,
            rater_fn=generic_rater_fn,
            iaa_fn=generic_iaa_fn,
            adjudicator_fn=generic_adjudicator_fn,
            reconciler_fn=generic_reconciler_fn,
        )

        assert ctx.unified.document_id == "task_1"
        assert "reconciled" in ctx.unified.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
