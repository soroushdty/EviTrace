"""
quality_control/checks/semantic_source.py
------------------------------------------
SemanticSourceVerificationCheck — verifies source text semantically via an
injected semantic-search dependency.

No embedding model loading, FAISS index construction, or top-level imports of
``sentence_transformers``, ``faiss``, or ``torch`` live here.  All heavy work
is delegated to the ``matcher`` callable supplied at construction time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, ClassVar

from quality_control.models import VerificationResult
from utils.logging_utils import get_logger

_logger: logging.Logger = get_logger(__name__)

_VALID_ON_INDEX_UNAVAILABLE = {"skip", "fail", "degrade"}

_EVIDENCE_KEYS = (
    "found_sentence",
    "page_index",
    "prefix",
    "suffix",
    "block_bbox",
    "span_bboxes",
)


def _is_store_unavailable(sentence_store: dict | None) -> bool:
    """Return True when the sentence store is considered unavailable.

    A store is unavailable when it is ``None``, an empty dict ``{}``, or
    lacks a ``"sentences"`` key with at least one entry.
    """
    if sentence_store is None:
        return True
    if not sentence_store:
        return True
    sentences = sentence_store.get("sentences")
    if not sentences:
        return True
    return False


@dataclass
class SemanticSourceVerificationCheck:
    """QC check that verifies source text semantically via an injected matcher.

    Attributes
    ----------
    matcher:
        Callable injected at construction time.  When the sentence store is
        available it is called as
        ``matcher(query, sentence_store, embed_fn, threshold)`` and must
        return either a dict with a ``"score"`` key or ``None``.
        When the store is unavailable and ``on_index_unavailable="degrade"``,
        it is called as a lexical fallback:
        ``matcher(query, None, page_texts, [])`` and must return either a
        dict with evidence keys or ``None``.
    on_index_unavailable:
        One of ``"skip"``, ``"fail"``, or ``"degrade"``.  Controls behaviour
        when the sentence store is unavailable.
    """

    check_name: ClassVar[str] = "semantic_source_verification"
    matcher: Callable
    on_index_unavailable: str

    def __post_init__(self) -> None:
        if self.on_index_unavailable not in _VALID_ON_INDEX_UNAVAILABLE:
            raise ValueError(
                f"on_index_unavailable must be one of "
                f"{sorted(_VALID_ON_INDEX_UNAVAILABLE)!r}; "
                f"got {self.on_index_unavailable!r}"
            )

    def run(
        self,
        query: str,
        sentence_store: dict | None,
        embed_fn: Callable,
        threshold: float,
        page_texts: dict | None,
    ) -> VerificationResult:
        """Run the semantic source verification check.

        Parameters
        ----------
        query:
            The query string to search for semantically.
        sentence_store:
            Dict holding sentences and optionally a FAISS index.  Considered
            unavailable when ``None``, ``{}``, or missing a ``"sentences"``
            key with at least one entry.
        embed_fn:
            Callable that converts a query string to an embedding vector.
            Passed through to the injected ``matcher``.
        threshold:
            Minimum score for a candidate to be considered a match.
        page_texts:
            Mapping of page index to page text; used as fallback context in
            ``"degrade"`` mode.

        Returns
        -------
        VerificationResult
            Outcome of the semantic verification check.

        Raises
        ------
        RuntimeError
            When the sentence store is unavailable and
            ``on_index_unavailable="fail"``.
        """
        if _is_store_unavailable(sentence_store):
            return self._handle_unavailable(query, page_texts)

        # Store is available — delegate to the semantic matcher.
        candidate = self.matcher(query, sentence_store, embed_fn, threshold)
        return self._build_result_from_candidate(candidate, threshold)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _handle_unavailable(
        self,
        query: str,
        page_texts: dict | None,
    ) -> VerificationResult:
        """Handle the case where the sentence store is unavailable."""
        mode = self.on_index_unavailable

        if mode == "skip":
            return VerificationResult(
                check_name=self.check_name,
                status="unavailable",
                score=0.0,
                evidence={key: None for key in _EVIDENCE_KEYS},
                details={},
            )

        if mode == "fail":
            raise RuntimeError(
                "sentence store is unavailable and on_index_unavailable='fail'"
            )

        # mode == "degrade": call matcher as lexical fallback
        _logger.warning(
            "SemanticSourceVerificationCheck: sentence store unavailable; "
            "degrading to lexical fallback matcher for query %r",
            query,
        )
        fallback_result = self.matcher(query, None, page_texts, [])

        if fallback_result is not None:
            evidence = {key: fallback_result.get(key, None) for key in _EVIDENCE_KEYS}
            raw_confidence = fallback_result.get("confidence", 1.0)
            score = max(0.0, min(1.0, float(raw_confidence)))
            return VerificationResult(
                check_name=self.check_name,
                status="candidate_match",
                score=score,
                evidence=evidence,
                details={"degraded": True},
            )

        return VerificationResult(
            check_name=self.check_name,
            status="no_match",
            score=0.0,
            evidence={key: None for key in _EVIDENCE_KEYS},
            details={"degraded": True},
        )

    def _build_result_from_candidate(
        self,
        candidate: dict | None,
        threshold: float,
    ) -> VerificationResult:
        """Build a VerificationResult from a matcher candidate dict."""
        if candidate is not None:
            score = candidate.get("score")
            if score is not None and score >= threshold:
                evidence = {key: candidate.get(key, None) for key in _EVIDENCE_KEYS}
                return VerificationResult(
                    check_name=self.check_name,
                    status="candidate_match",
                    score=float(score),
                    evidence=evidence,
                    details={},
                )
            # Candidate returned but score is below threshold (or missing)
            details: dict = {}
            if score is not None:
                details["below_threshold_score"] = float(score)
            evidence = {key: candidate.get(key, None) for key in _EVIDENCE_KEYS}
            return VerificationResult(
                check_name=self.check_name,
                status="no_match",
                score=0.0,
                evidence=evidence,
                details=details,
            )

        # matcher returned None — no candidate at all
        return VerificationResult(
            check_name=self.check_name,
            status="no_match",
            score=0.0,
            evidence={key: None for key in _EVIDENCE_KEYS},
            details={},
        )
