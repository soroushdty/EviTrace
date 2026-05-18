"""
Property-based tests for per-page extraction routing (Properties 5, 6).

Feature: audit-remediation
Validates: Requirements 3.1, 3.2, 3.3

Property 5: For any PDF with a mix of native and scanned pages, native pages
SHALL be routed through GROBID+pdfplumber and scanned pages through
PaddleOCR+PyMuPDF. Merged result preserves original page order. When all pages
are native, OCR extractors SHALL NOT be invoked.

Property 6: For any page processed by build_qc_bundle(), routing metadata SHALL
include page_index, selected_extractor, fallback_extractor (or null), and
routing_reason. No field SHALL be absent or empty-string for page_index or
selected_extractor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakePageClassification:
    """Minimal stand-in for scan_detector.PageScanClassification."""

    page_index: int
    is_native: bool
    triggered_stages: list[int] = field(default_factory=list)
    stage_values: dict[str, float] = field(default_factory=dict)


def _build_qc_config(*, ocr: bool = True) -> dict:
    """Build a minimal qc_config for routing tests."""
    return {
        "ocr": ocr,
        "quality_control": {
            "ocr": {"rasterization_dpi": 150},
            "grobid_integration": {"failure_behavior": "fallback"},
            "grobid": {
                "url": "http://localhost:8070",
                "timeout": 300,
                "consolidate_header": 0,
                "consolidate_citations": 0,
                "generate_ids": False,
                "segment_sentences": True,
                "include_raw_citations": True,
                "include_raw_affiliations": False,
                "tei_coordinates": True,
                "max_retries": 2,
                "tei_cache_dir": "",
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


def _make_block(page_index: int, text: str = "sample text") -> dict:
    """Create a minimal BlockDict for testing."""
    return {
        "text": text,
        "page_index": page_index,
        "block_bbox": None,
        "spans": [],
    }


def _run_build_qc_bundle_with_classifications(
    classifications: list[_FakePageClassification],
    *,
    ocr_enabled: bool = True,
) -> tuple[dict, bool, bool]:
    """Run build_qc_bundle with mocked dependencies and return routing metadata
    plus flags indicating whether OCR and native extractors were called.

    The ThreadPoolExecutor is mocked so that:
    - grobid_future.result() returns TEI XML (simulating GROBID being called)
    - plumber_future.result() returns blocks (simulating pdfplumber being called)
    - scan_future.result() returns the provided classifications

    OCR extractors (PaddleOCR, PyMuPDF) are called directly outside the executor
    in the mixed/scanned path, so we track them via mock side_effects.

    We determine whether native extractors were "called" by checking if the
    grobid_future.result() was actually invoked (i.e., the code consumed the
    future's result).

    Returns
    -------
    tuple of (page_routing_dict_list, ocr_called, native_called)
    """
    qc_config = _build_qc_config(ocr=ocr_enabled)
    num_pages = len(classifications)

    # Build blocks for all pages from each extractor
    plumber_blocks = [_make_block(i, f"plumber page {i}") for i in range(num_pages)]
    paddle_blocks = [_make_block(i, f"paddle page {i}") for i in range(num_pages)]
    pymupdf_blocks = [_make_block(i, f"pymupdf page {i}") for i in range(num_pages)]

    ocr_called = False

    def mock_paddleocr(pdf_path, dpi=150):
        nonlocal ocr_called
        ocr_called = True
        return paddle_blocks

    def mock_pymupdf(pdf_path):
        nonlocal ocr_called
        ocr_called = True
        return (pymupdf_blocks, [])

    # QC mock that captures page_routing
    mock_qc_bundle = MagicMock()
    mock_qc_bundle.unified = MagicMock()
    mock_qc_bundle.unified.content = {}

    with (
        patch(
            "pipeline.extraction_pipeline._grobid_cache_read",
            return_value=(None, ""),
        ),
        patch(
            "pipeline.extraction_pipeline._get_text_processor",
            return_value=MagicMock(),
        ),
        patch(
            "pipeline.extraction_pipeline.ThreadPoolExecutor",
        ) as mock_executor_cls,
        patch(
            "pipeline.extraction_pipeline.extract_with_paddleocr",
            side_effect=mock_paddleocr,
        ),
        patch(
            "pipeline.extraction_pipeline.extract_with_pymupdf",
            side_effect=mock_pymupdf,
        ),
        patch(
            "pipeline.extraction_pipeline.run_quality_control",
            return_value=mock_qc_bundle,
        ),
        patch(
            "pipeline.extraction_pipeline._get_lexical_matcher",
            return_value=MagicMock(),
        ),
        patch(
            "pipeline.extraction_pipeline._get_semantic_matcher",
            return_value=MagicMock(),
        ),
        patch(
            "pipeline.extraction_pipeline.w3c_project",
            return_value=[],
        ),
        patch(
            "pipeline.extraction_pipeline.generate_w3c_jsonld",
            return_value={},
        ),
    ):
        # Configure the ThreadPoolExecutor mock
        mock_executor = MagicMock()
        mock_executor_cls.return_value.__enter__ = MagicMock(
            return_value=mock_executor
        )
        mock_executor_cls.return_value.__exit__ = MagicMock(return_value=False)

        # The futures: grobid_future, plumber_future, scan_future
        mock_grobid_future = MagicMock()
        mock_grobid_future.result.return_value = ("<TEI>mock</TEI>", [])
        mock_grobid_future.cancel.return_value = None

        mock_plumber_future = MagicMock()
        mock_plumber_future.result.return_value = plumber_blocks
        mock_plumber_future.cancel.return_value = None

        mock_scan_future = MagicMock()
        mock_scan_future.result.return_value = classifications

        mock_executor.submit.side_effect = [
            mock_grobid_future,
            mock_plumber_future,
            mock_scan_future,
        ]

        from pipeline.extraction_pipeline import build_qc_bundle

        build_qc_bundle(
            pdf_path=Path("/fake/test.pdf"),
            pdf_name="test_paper",
            qc_config=qc_config,
        )

    # Determine if native extractors were called by checking if the futures
    # had their .result() method invoked (meaning the code consumed the output)
    native_called = mock_grobid_future.result.called or mock_plumber_future.result.called

    page_routing = mock_qc_bundle.unified.content.get("page_routing", [])
    return page_routing, ocr_called, native_called


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Strategy for generating a list of booleans representing page classifications
# (True = native, False = scanned). At least 1 page required.
page_classification_strategy = st.lists(
    st.booleans(),
    min_size=1,
    max_size=20,
)


# ---------------------------------------------------------------------------
# Property 5: Per-page routing correctness
# ---------------------------------------------------------------------------


@given(page_classification_strategy)
@settings(max_examples=100)
def test_per_page_routing_correctness_mixed(page_is_native_list: list[bool]):
    """For any PDF with a mix of native and scanned pages, native pages SHALL
    be routed through GROBID+pdfplumber and scanned pages through
    PaddleOCR+PyMuPDF. Merged result preserves original page order.

    **Validates: Requirements 3.1, 3.2, 3.3**
    """
    # We need at least one native and one scanned page for mixed routing
    assume(any(page_is_native_list) and not all(page_is_native_list))

    classifications = [
        _FakePageClassification(
            page_index=i,
            is_native=is_native,
            triggered_stages=([] if is_native else [1]),
            stage_values={},
        )
        for i, is_native in enumerate(page_is_native_list)
    ]

    page_routing, ocr_called, native_called = (
        _run_build_qc_bundle_with_classifications(classifications, ocr_enabled=True)
    )

    # Both OCR and native extractors should have been invoked for mixed PDFs
    assert native_called, "Native extractors (GROBID+pdfplumber) should be invoked for mixed PDFs"
    assert ocr_called, "OCR extractors (PaddleOCR+PyMuPDF) should be invoked for mixed PDFs"

    # Routing metadata should have one entry per page
    assert len(page_routing) == len(page_is_native_list), (
        f"Expected {len(page_is_native_list)} routing entries, got {len(page_routing)}"
    )

    # Verify routing decisions match page classifications
    for i, (entry, is_native) in enumerate(zip(page_routing, page_is_native_list)):
        if is_native:
            assert entry["selected_extractor"] == "grobid+pdfplumber", (
                f"Page {i} is native but routed to {entry['selected_extractor']}"
            )
        else:
            assert entry["selected_extractor"] == "paddleocr+pymupdf", (
                f"Page {i} is scanned but routed to {entry['selected_extractor']}"
            )

    # Verify page order is preserved (page_index values are monotonically increasing)
    page_indices = [entry["page_index"] for entry in page_routing]
    assert page_indices == sorted(page_indices), (
        f"Page routing not in original order: {page_indices}"
    )


@given(st.integers(min_value=1, max_value=20))
@settings(max_examples=100)
def test_per_page_routing_all_native_no_ocr(num_pages: int):
    """When all pages are native, OCR extractors SHALL NOT be invoked.

    **Validates: Requirements 3.1, 3.3**
    """
    page_is_native_list = [True] * num_pages

    classifications = [
        _FakePageClassification(
            page_index=i,
            is_native=True,
            triggered_stages=[],
            stage_values={},
        )
        for i in range(num_pages)
    ]

    page_routing, ocr_called, native_called = (
        _run_build_qc_bundle_with_classifications(classifications, ocr_enabled=True)
    )

    # OCR extractors should NOT have been called
    assert not ocr_called, (
        "OCR extractors should NOT be invoked when all pages are native"
    )

    # Native extractors should have been called
    assert native_called, (
        "Native extractors (GROBID+pdfplumber) should be invoked for all-native PDFs"
    )

    # All routing entries should indicate native extractor
    assert len(page_routing) == num_pages
    for entry in page_routing:
        assert entry["selected_extractor"] == "grobid+pdfplumber", (
            f"All-native PDF should route all pages to grobid+pdfplumber, "
            f"got {entry['selected_extractor']}"
        )
        assert entry["routing_reason"] == "all_native"


@given(st.lists(st.just(True), min_size=1, max_size=20))
@settings(max_examples=100)
def test_per_page_routing_preserves_page_order(page_is_native_list: list[bool]):
    """Merged result preserves original page order for all-native PDFs.

    **Validates: Requirements 3.1**
    """
    classifications = [
        _FakePageClassification(
            page_index=i,
            is_native=True,
            triggered_stages=[],
            stage_values={},
        )
        for i in range(len(page_is_native_list))
    ]

    page_routing, _, _ = _run_build_qc_bundle_with_classifications(
        classifications, ocr_enabled=True
    )

    # Verify page indices are in original order
    page_indices = [entry["page_index"] for entry in page_routing]
    assert page_indices == list(range(len(page_is_native_list))), (
        f"Expected page indices {list(range(len(page_is_native_list)))}, "
        f"got {page_indices}"
    )


# ---------------------------------------------------------------------------
# Property 6: Routing metadata completeness
# ---------------------------------------------------------------------------


@given(page_classification_strategy)
@settings(max_examples=100)
def test_routing_metadata_completeness(page_is_native_list: list[bool]):
    """For any page processed by build_qc_bundle(), routing metadata SHALL
    include page_index, selected_extractor, fallback_extractor (or null), and
    routing_reason. No field SHALL be absent or empty-string for page_index or
    selected_extractor.

    **Validates: Requirements 3.2**
    """
    classifications = [
        _FakePageClassification(
            page_index=i,
            is_native=is_native,
            triggered_stages=([] if is_native else [1]),
            stage_values={},
        )
        for i, is_native in enumerate(page_is_native_list)
    ]

    page_routing, _, _ = _run_build_qc_bundle_with_classifications(
        classifications, ocr_enabled=True
    )

    # Should have one routing entry per page
    assert len(page_routing) == len(page_is_native_list), (
        f"Expected {len(page_is_native_list)} routing entries, got {len(page_routing)}"
    )

    for i, entry in enumerate(page_routing):
        # page_index must be present and an integer
        assert "page_index" in entry, (
            f"Routing entry {i} missing 'page_index'"
        )
        assert isinstance(entry["page_index"], int), (
            f"Routing entry {i} 'page_index' should be int, got {type(entry['page_index'])}"
        )

        # selected_extractor must be present and non-empty string
        assert "selected_extractor" in entry, (
            f"Routing entry {i} missing 'selected_extractor'"
        )
        assert isinstance(entry["selected_extractor"], str), (
            f"Routing entry {i} 'selected_extractor' should be str, "
            f"got {type(entry['selected_extractor'])}"
        )
        assert entry["selected_extractor"] != "", (
            f"Routing entry {i} 'selected_extractor' should not be empty"
        )

        # fallback_extractor must be present (can be None or a string)
        assert "fallback_extractor" in entry, (
            f"Routing entry {i} missing 'fallback_extractor'"
        )
        assert entry["fallback_extractor"] is None or isinstance(
            entry["fallback_extractor"], str
        ), (
            f"Routing entry {i} 'fallback_extractor' should be None or str, "
            f"got {type(entry['fallback_extractor'])}"
        )

        # routing_reason must be present and non-empty string
        assert "routing_reason" in entry, (
            f"Routing entry {i} missing 'routing_reason'"
        )
        assert isinstance(entry["routing_reason"], str), (
            f"Routing entry {i} 'routing_reason' should be str, "
            f"got {type(entry['routing_reason'])}"
        )
        assert entry["routing_reason"] != "", (
            f"Routing entry {i} 'routing_reason' should not be empty"
        )

        # selected_extractor must be one of the known values
        assert entry["selected_extractor"] in (
            "grobid+pdfplumber",
            "paddleocr+pymupdf",
        ), (
            f"Routing entry {i} 'selected_extractor' has unexpected value: "
            f"{entry['selected_extractor']}"
        )
