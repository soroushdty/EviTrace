"""
quality_control/local_metrics.py
------------------------------------------------
Concrete LocalQCReport dataclass implementing all 8 Metrics Tier 1
(Local_QC_Metrics) checks for the pdf_extractor QC pipeline.

All extractor branches in a given run must use the same QualityReport
subclass (Universal_Metrics constraint).  LocalQCReport reads thresholds
from the pipeline config dict and produces one LocalQCMetricRecord per
metric when ``passes_check()`` is called.

Metrics (Tier 1)
----------------
1. min_chars_per_page        — per-page character-count coverage
2. grobid_vs_native_ratio    — GROBID branch text vs. native backend length ratio
3. long_sentence_fraction    — fraction of sentences exceeding a word-count threshold
4. section_coverage          — presence of expected section headings in full text
5. caption_table_figure_coverage — table/figure references in text matched to blocks
6. coordinate_availability   — fraction of blocks missing bounding-box coordinates
7. references_in_body        — reference/bibliography keywords leaking into body sentences
8. weird_char_ratio          — ratio of replacement / control / overlong unicode chars
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import LocalQCMetricRecord
from .defaults import QualityReport


@dataclass
class LocalQCReport(QualityReport):
    """Concrete QualityReport with all 8 Metrics Tier 1 (Local_QC_Metrics) checks.

    All extractor branches in a given run must use the same QualityReport subclass
    (Universal_Metrics constraint). LocalQCReport reads thresholds from config and
    produces one LocalQCMetricRecord per metric.

    Attributes
    ----------
    config:
        Full pipeline config dict.  Metric thresholds are read from
        ``config["quality_control"]["local_metrics"]``.
    blocks:
        List of BlockDict dicts from this extractor branch.
    sentence_records:
        List of sentence record dicts (each must have a ``"sentence"`` key).
    full_pdf_text:
        Full text of the document (all pages concatenated).
    page_texts:
        Mapping of ``{page_index: page_text}`` for this branch.
    native_page_texts:
        Mapping of ``{page_index: page_text}`` from the native backend
        (PyMuPDF / pdfplumber), used for GROBID-vs-native comparisons.
    metric_records:
        Populated by ``passes_check()``.  One ``LocalQCMetricRecord`` per metric.
    """

    config: dict = field(default_factory=dict)
    blocks: list = field(default_factory=list)
    sentence_records: list = field(default_factory=list)
    full_pdf_text: str = ""
    page_texts: dict = field(default_factory=dict)
    native_page_texts: dict = field(default_factory=dict)
    metric_records: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def passes_check(self, source=None) -> bool:  # noqa: ARG002  (source unused here)
        """Compute all 8 Tier 1 metrics, populate metric_records, return True if none triggered.

        Parameters
        ----------
        source:
            Unused placeholder for interface compatibility with the base class.

        Returns
        -------
        bool
            ``True`` when **no** metric fired; ``False`` when at least one triggered.
        """
        lm: dict = (self.config.get("quality_control") or {}).get("local_metrics") or {}
        records: list[LocalQCMetricRecord] = []

        records.append(self._check_min_chars_per_page(lm))
        records.append(self._check_grobid_vs_native_ratio(lm))
        records.append(self._check_long_sentence_fraction(lm))
        records.append(self._check_section_coverage(lm))
        records.append(self._check_caption_table_figure_coverage(lm))
        records.append(self._check_coordinate_availability(lm))
        records.append(self._check_references_in_body(lm))
        records.append(self._check_weird_char_ratio(lm))

        self.metric_records = records
        return not any(r.triggered for r in records)

    # ------------------------------------------------------------------
    # Metric 1 — per-page text coverage
    # ------------------------------------------------------------------

    def _check_min_chars_per_page(self, lm: dict) -> LocalQCMetricRecord:
        """Triggered if any page in this branch has fewer characters than the
        configured threshold AND the native backend had substantially more on
        that same page.
        """
        min_chars: int = lm.get("min_chars_per_page", 100)
        triggered_pages: list[int] = []
        for page_idx, page_text in self.page_texts.items():
            if len(page_text) < min_chars:
                native_text = self.native_page_texts.get(page_idx, "")
                if len(native_text) > min_chars:
                    triggered_pages.append(page_idx)
        return LocalQCMetricRecord(
            metric_name="min_chars_per_page",
            computed_value=len(triggered_pages),
            threshold=min_chars,
            triggered=bool(triggered_pages),
        )

    # ------------------------------------------------------------------
    # Metric 2 — GROBID-vs-native length ratio
    # ------------------------------------------------------------------

    def _check_grobid_vs_native_ratio(self, lm: dict) -> LocalQCMetricRecord:
        """Compute the average ratio of this branch's page text length versus the
        native backend's page text length.  Triggered when the average ratio is
        below the configured threshold.
        """
        ratio_threshold: float = lm.get("grobid_vs_native_ratio_threshold", 0.6)
        ratios: list[float] = []
        for page_idx, page_text in self.page_texts.items():
            native_text = self.native_page_texts.get(page_idx, "")
            if len(native_text) > 0:
                ratios.append(len(page_text) / len(native_text))
        avg_ratio: float = float(sum(ratios) / len(ratios)) if ratios else 1.0
        return LocalQCMetricRecord(
            metric_name="grobid_vs_native_ratio",
            computed_value=avg_ratio,
            threshold=ratio_threshold,
            triggered=avg_ratio < ratio_threshold,
        )

    # ------------------------------------------------------------------
    # Metric 3 — long-sentence fraction
    # ------------------------------------------------------------------

    def _check_long_sentence_fraction(self, lm: dict) -> LocalQCMetricRecord:
        """Triggered when the fraction of sentences whose word count exceeds
        ``long_sentence_word_threshold`` is greater than ``long_sentence_max_fraction``.
        """
        word_threshold: int = lm.get("long_sentence_word_threshold", 120)
        max_fraction: float = lm.get("long_sentence_max_fraction", 0.12)
        if self.sentence_records:
            long_count = sum(
                1
                for r in self.sentence_records
                if len(r.get("sentence", "").split()) > word_threshold
            )
            fraction = long_count / len(self.sentence_records)
        else:
            fraction = 0.0
        return LocalQCMetricRecord(
            metric_name="long_sentence_fraction",
            computed_value=fraction,
            threshold=max_fraction,
            triggered=fraction > max_fraction,
        )

    # ------------------------------------------------------------------
    # Metric 4 — section coverage
    # ------------------------------------------------------------------

    def _check_section_coverage(self, lm: dict) -> LocalQCMetricRecord:
        """Triggered when one or more expected section headings are absent from
        the full document text.

        When ``full_pdf_text`` is empty (no document data provided), the metric
        is not triggered — there is nothing to check.
        """
        expected_sections: list[str] = lm.get(
            "expected_sections", ["abstract", "introduction", "methods", "results"]
        )
        # No document text means there is nothing to evaluate; skip the check.
        if not self.full_pdf_text:
            return LocalQCMetricRecord(
                metric_name="section_coverage",
                computed_value=0,
                threshold=len(expected_sections),
                triggered=False,
            )
        full_lower = self.full_pdf_text.lower()
        missing = [s for s in expected_sections if s.lower() not in full_lower]
        found_count = len(expected_sections) - len(missing)
        return LocalQCMetricRecord(
            metric_name="section_coverage",
            computed_value=found_count,
            threshold=len(expected_sections),
            triggered=bool(missing),
        )

    # ------------------------------------------------------------------
    # Metric 5 — table/figure caption coverage
    # ------------------------------------------------------------------

    def _check_caption_table_figure_coverage(self, lm: dict) -> LocalQCMetricRecord:
        """Triggered when "Table N" / "Figure N" references appear in the full
        text but none of those references can be matched to any block's text.

        Disabled when ``caption_table_figure_check_enabled`` is ``False``.
        """
        caption_check: bool = lm.get("caption_table_figure_check_enabled", True)
        if not caption_check:
            return LocalQCMetricRecord(
                metric_name="caption_table_figure_coverage",
                computed_value=0,
                threshold=None,
                triggered=False,
            )

        table_figure_refs: list[str] = re.findall(
            r"\b(?:Table|Figure)\s+\d+", self.full_pdf_text
        )
        block_texts_lower = [
            b.get("text", "").lower()
            for b in self.blocks
            if isinstance(b, dict)
        ]
        caption_hits = [
            ref for ref in table_figure_refs
            if any(ref.lower() in bt for bt in block_texts_lower)
        ]
        triggered_caption = bool(table_figure_refs) and not caption_hits
        return LocalQCMetricRecord(
            metric_name="caption_table_figure_coverage",
            computed_value=len(table_figure_refs),
            threshold=None,
            triggered=triggered_caption,
        )

    # ------------------------------------------------------------------
    # Metric 6 — coordinate availability
    # ------------------------------------------------------------------

    def _check_coordinate_availability(self, lm: dict) -> LocalQCMetricRecord:
        """Triggered when the fraction of blocks missing both ``block_bbox`` and
        ``page_index`` exceeds the configured threshold.
        """
        coord_threshold: float = lm.get("coordinate_coverage_threshold", 0.1)
        if self.blocks:
            missing_coords = sum(
                1
                for b in self.blocks
                if isinstance(b, dict)
                and b.get("block_bbox") is None
                and b.get("page_index") is None
            )
            missing_fraction = missing_coords / len(self.blocks)
        else:
            missing_fraction = 0.0
        return LocalQCMetricRecord(
            metric_name="coordinate_availability",
            computed_value=missing_fraction,
            threshold=coord_threshold,
            triggered=missing_fraction > coord_threshold,
        )

    # ------------------------------------------------------------------
    # Metric 7 — header/body/back-matter separation (references in body)
    # ------------------------------------------------------------------

    def _check_references_in_body(self, lm: dict) -> LocalQCMetricRecord:
        """Triggered when reference / bibliography keywords appear in more than
        ``references_in_body_threshold`` fraction of the sentence records,
        suggesting that back-matter content has leaked into body sentences.
        """
        ref_threshold: float = lm.get("references_in_body_threshold", 0.05)
        if self.sentence_records:
            _REFERENCE_KEYWORDS = ("references", "bibliography", "et al.", "[1]", "(1)")
            ref_pattern_count = sum(
                1
                for r in self.sentence_records
                if any(kw in r.get("sentence", "").lower() for kw in _REFERENCE_KEYWORDS)
            )
            ref_fraction = ref_pattern_count / len(self.sentence_records)
        else:
            ref_fraction = 0.0
        return LocalQCMetricRecord(
            metric_name="references_in_body",
            computed_value=ref_fraction,
            threshold=ref_threshold,
            triggered=ref_fraction > ref_threshold,
        )

    # ------------------------------------------------------------------
    # Metric 8 — weird character ratio
    # ------------------------------------------------------------------

    def _check_weird_char_ratio(self, lm: dict) -> LocalQCMetricRecord:
        """Triggered when the ratio of replacement / control / overlong non-ASCII
        characters to total document length exceeds the configured threshold.

        Matched patterns:
        - U+FFFD replacement character (``�`` / ``?``)
        - C0 control characters (``\x00``–``\x08``, ``\x0b``, ``\x0c``, ``\x0e``–``\x1f``)
        - Runs of 3+ consecutive non-ASCII bytes
        """
        weird_threshold: float = lm.get("weird_char_ratio_threshold", 0.05)
        if self.full_pdf_text:
            weird_chars = re.findall(
                r"[^\x00-\x7f]|[\x00-\x08\x0b\x0c\x0e-\x1f]",
                self.full_pdf_text,
            )
            weird_ratio = len(weird_chars) / max(len(self.full_pdf_text), 1)
        else:
            weird_ratio = 0.0
        return LocalQCMetricRecord(
            metric_name="weird_char_ratio",
            computed_value=weird_ratio,
            threshold=weird_threshold,
            triggered=weird_ratio > weird_threshold,
        )
