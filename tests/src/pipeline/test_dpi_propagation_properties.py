"""
Property-based tests for DPI configuration propagation (Property 4).

Feature: audit-remediation
Validates: Requirements 2.1, 2.2

Property 4: For any configured `quality_control.ocr.rasterization_dpi` value,
when `build_qc_bundle()` invokes `extract_with_paddleocr()`, the DPI parameter
received by the OCR extractor SHALL equal the configured value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

from hypothesis import given, settings
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


def _build_scanned_config(dpi: int) -> dict:
    """Build a minimal qc_config that routes through the OCR path with the
    given DPI value.

    The config must:
    - Set ocr=true so the OCR path is taken.
    - Set quality_control.ocr.rasterization_dpi to the test DPI value.
    - Provide a text_processor config that avoids loading heavy models.
    - Disable the GROBID TEI cache so we always hit the scan_detector path.
    """
    return {
        "ocr": True,
        "quality_control": {
            "ocr": {
                "rasterization_dpi": dpi,
            },
            "grobid_integration": {
                "failure_behavior": "fallback",
            },
            "grobid": {
                "tei_cache_dir": "",
            },
        },
        "text_processor": {
            "class": "text_processing.base.NLTKPunktSentenceSegment",
            "sentence_tokenizer": {"backend": "nltk_punkt"},
        },
    }


# ---------------------------------------------------------------------------
# Property 4: DPI propagation end-to-end
# ---------------------------------------------------------------------------


@given(st.integers(min_value=72, max_value=600))
@settings(max_examples=100)
def test_dpi_propagation_end_to_end(dpi: int):
    """For any configured quality_control.ocr.rasterization_dpi value (72–600),
    when build_qc_bundle() invokes extract_with_paddleocr(), the DPI parameter
    received by the OCR extractor SHALL equal the configured value.

    **Validates: Requirements 2.1, 2.2**
    """
    qc_config = _build_scanned_config(dpi)

    # Track the DPI value received by extract_with_paddleocr
    captured_dpi: list[int] = []

    def mock_paddleocr(pdf_path: str, dpi: int = 150) -> list:
        captured_dpi.append(dpi)
        return []  # Return empty blocks — we only care about the DPI param

    # A single scanned page classification so the OCR path is taken
    scanned_page = _FakePageClassification(
        page_index=0,
        is_native=False,
        triggered_stages=[1],
        stage_values={"word_count": 0},
    )

    with (
        # Mock the GROBID cache to return a miss (forces scan_detector path)
        patch(
            "pipeline.extraction_pipeline._grobid_cache_read",
            return_value=(None, ""),
        ),
        # Mock the text processor to avoid loading heavy NLP models
        patch(
            "pipeline.extraction_pipeline._get_text_processor",
            return_value=MagicMock(),
        ),
        # Mock scan_detector.classify_page to return scanned classification
        # The function is called inside _run_scan_detector which opens the PDF
        # with fitz — we mock the entire ThreadPoolExecutor flow
        patch(
            "pipeline.extraction_pipeline.ThreadPoolExecutor",
        ) as mock_executor_cls,
        # Mock extract_with_paddleocr to capture the DPI parameter
        patch(
            "pipeline.extraction_pipeline.extract_with_paddleocr",
            side_effect=mock_paddleocr,
        ),
        # Mock extract_with_pymupdf (secondary OCR cross-validation)
        patch(
            "pipeline.extraction_pipeline.extract_with_pymupdf",
            return_value=([], {}),
        ),
        # Mock run_quality_control to avoid running the full QC pipeline
        patch(
            "pipeline.extraction_pipeline.run_quality_control",
        ) as mock_qc,
        # Mock w3c_project to avoid annotation chain
        patch(
            "pipeline.extraction_pipeline.w3c_project",
            return_value=[],
        ),
        # Mock generate_w3c_jsonld
        patch(
            "pipeline.extraction_pipeline.generate_w3c_jsonld",
            return_value=[],
        ),
    ):
        # Configure the ThreadPoolExecutor mock to simulate the concurrent
        # execution path where scan_detector flags pages as scanned.
        mock_executor = MagicMock()
        mock_executor_cls.return_value.__enter__ = MagicMock(
            return_value=mock_executor
        )
        mock_executor_cls.return_value.__exit__ = MagicMock(return_value=False)

        # The futures: grobid_future, plumber_future, scan_future
        mock_grobid_future = MagicMock()
        mock_grobid_future.result.return_value = ("<TEI/>", [])
        mock_grobid_future.cancel.return_value = None

        mock_plumber_future = MagicMock()
        mock_plumber_future.result.return_value = []
        mock_plumber_future.cancel.return_value = None

        mock_scan_future = MagicMock()
        mock_scan_future.result.return_value = [scanned_page]

        # submit() is called 3 times: grobid, plumber, scan_detector
        mock_executor.submit.side_effect = [
            mock_grobid_future,
            mock_plumber_future,
            mock_scan_future,
        ]

        # Configure QC mock to return a minimal QCBundle-like object
        mock_ctx = MagicMock()
        mock_ctx.unified = None
        mock_qc.return_value = mock_ctx

        from pipeline.extraction_pipeline import build_qc_bundle

        build_qc_bundle(
            pdf_path="/fake/test.pdf",
            pdf_name="test_paper",
            qc_config=qc_config,
        )

    # Assert the DPI parameter received by extract_with_paddleocr equals
    # the configured value
    assert len(captured_dpi) == 1, (
        f"Expected extract_with_paddleocr to be called exactly once, "
        f"got {len(captured_dpi)} calls"
    )
    assert captured_dpi[0] == dpi, (
        f"Expected DPI={dpi} to be propagated to extract_with_paddleocr, "
        f"but received DPI={captured_dpi[0]}"
    )
