"""
quality_control/checks/source_text.py
--------------------------------------
SourceTextPresenceCheck — verifies source-text presence via an injected
lexical matcher dependency.

No inline lexical matching logic lives here.  The check delegates entirely
to the ``matcher`` callable supplied at construction time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, ClassVar

from quality_control.models import VerificationResult

_EVIDENCE_KEYS = (
    "found_sentence",
    "page_index",
    "prefix",
    "suffix",
    "block_bbox",
    "span_bboxes",
)


@dataclass
class SourceTextPresenceCheck:
    """QC check that verifies source-text presence via an injected matcher.

    Attributes
    ----------
    matcher:
        Callable with signature
        ``(needle, full_text, page_texts, blocks) -> dict | None``.
        When it returns a non-``None`` dict the check is ``"verified"``;
        when it returns ``None`` the check is ``"no_match"``.
    """

    check_name: ClassVar[str] = "source_text_presence"
    matcher: Callable

    def run(
        self,
        needle: str,
        full_text: str,
        page_texts: dict,
        blocks: list,
    ) -> VerificationResult:
        """Run the source-text presence check.

        Parameters
        ----------
        needle:
            The text fragment to search for.
        full_text:
            The full document text to search within.
        page_texts:
            Mapping of page index to page text.
        blocks:
            List of text block dicts from the extractor.

        Returns
        -------
        VerificationResult
            ``status="verified"`` when the matcher finds a match;
            ``status="no_match"`` otherwise.
        """
        result = self.matcher(needle, full_text, page_texts, blocks)

        if result is not None:
            raw_confidence = result.get("confidence", 1.0)
            score = max(0.0, min(1.0, float(raw_confidence)))
            evidence = {key: result.get(key, None) for key in _EVIDENCE_KEYS}
            return VerificationResult(
                check_name=self.check_name,
                status="verified",
                score=score,
                evidence=evidence,
                details={},
            )

        # matcher returned None — no match found
        evidence = {key: None for key in _EVIDENCE_KEYS}
        return VerificationResult(
            check_name=self.check_name,
            status="no_match",
            score=0.0,
            evidence=evidence,
            details={},
        )
