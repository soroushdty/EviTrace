"""
quality_control/checks/extractor_agreement.py
---------------------------------------------
Optional extractor-agreement reporting check.

``ExtractorAgreementCheck`` compares two extractor branch payloads and emits
an agreement report dict.  It is purely observational: the result is stored
in ``ctx.metrics_hierarchy["semantic_verification"]["extractor_agreement"]``
and must NOT influence ``ctx.decision``, ``ctx.reports``, or ``ctx.unified``.

All matching logic is delegated to injected callables:

- ``exact_matcher(primary_sentence, candidate_sentence) -> bool``
- ``semantic_matcher(primary_sentence, candidate_sentence) -> float``  (optional)

No inline matching, embedding, or sentence segmentation logic lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ExtractorAgreementCheck:
    """Compare two extractor branch payloads and emit an agreement report.

    Parameters
    ----------
    exact_matcher:
        Injected callable with signature
        ``(primary_sentence: str, candidate_sentence: str) -> bool``.
        Returns ``True`` when the two sentences are considered an exact match.
    semantic_matcher:
        Optional injected callable with signature
        ``(primary_sentence: str, candidate_sentence: str) -> float``.
        Returns a similarity score in ``[0.0, 1.0]``.  When ``None``, the
        check operates in exact-only mode and ``semantic_threshold`` is
        reported as ``0.0``.
    """

    exact_matcher: Callable
    semantic_matcher: Callable | None = None

    def run(
        self,
        primary_blocks: list,
        candidate_blocks: list,
        config: dict,
    ) -> dict:
        """Run the extractor-agreement check and return a report dict.

        The check is a no-op (returns a skipped report) unless
        ``quality_control.semantic_verification.extractor_agreement.enabled``
        is ``True`` in *config*.

        Parameters
        ----------
        primary_blocks:
            List of block dicts from the primary extractor branch.
        candidate_blocks:
            List of block dicts from the candidate extractor branch.
        config:
            Full config dict (same shape as the one passed to
            ``run_quality_control``).

        Returns
        -------
        dict
            Report with keys: ``primary_sentence_count``,
            ``candidate_sentence_count``, ``exact_match_count``,
            ``near_match_count``, ``unmatched_primary_count``,
            ``unmatched_candidate_count``, ``agreement_rate``,
            ``semantic_threshold``, ``examples``.
        """
        # ------------------------------------------------------------------
        # Read config
        # ------------------------------------------------------------------
        ea_cfg: dict = (
            config
            .get("quality_control", {})
            .get("semantic_verification", {})
            .get("extractor_agreement", {})
        )

        enabled: bool = ea_cfg.get("enabled", False)

        if not enabled:
            return {
                "primary_sentence_count": 0,
                "candidate_sentence_count": 0,
                "exact_match_count": 0,
                "near_match_count": 0,
                "unmatched_primary_count": 0,
                "unmatched_candidate_count": 0,
                "agreement_rate": 0.0,
                "semantic_threshold": 0.0,
                "examples": {
                    "unmatched_primary": [],
                    "unmatched_candidate": [],
                    "near_matches": [],
                },
                "status": "skipped",
            }

        len_filter: int = ea_cfg.get("len_filter", 40)
        max_examples: int = ea_cfg.get("max_examples", 10)
        semantic_threshold: float = (
            config
            .get("quality_control", {})
            .get("semantic_verification", {})
            .get("similarity_threshold", 0.85)
        )

        # ------------------------------------------------------------------
        # Extract sentences from blocks
        # ------------------------------------------------------------------
        primary_sentences: list[str] = _extract_sentences(primary_blocks)
        raw_candidate_sentences: list[str] = _extract_sentences(candidate_blocks)

        # Discard candidate sentences shorter than len_filter
        candidate_sentences: list[str] = [
            s for s in raw_candidate_sentences if len(s) >= len_filter
        ]

        primary_sentence_count = len(primary_sentences)
        candidate_sentence_count = len(candidate_sentences)

        # ------------------------------------------------------------------
        # Matching
        # ------------------------------------------------------------------
        exact_match_count = 0
        near_match_count = 0

        # Track which candidate sentences have been matched (by index)
        matched_candidate_indices: set[int] = set()

        # For each primary sentence, try exact then semantic
        unmatched_primary: list[str] = []
        near_matches: list[dict] = []

        for primary_sent in primary_sentences:
            exact_matched = False

            # Pass 1: exact matching against all candidates
            for ci, cand_sent in enumerate(candidate_sentences):
                if ci in matched_candidate_indices:
                    continue
                if self.exact_matcher(primary_sent, cand_sent):
                    exact_match_count += 1
                    matched_candidate_indices.add(ci)
                    exact_matched = True
                    break

            if exact_matched:
                continue

            # Pass 2: semantic matching (only for unmatched candidates)
            semantic_matched = False
            if self.semantic_matcher is not None:
                best_score: float = 0.0
                best_cand: str = ""
                best_ci: int = -1

                for ci, cand_sent in enumerate(candidate_sentences):
                    if ci in matched_candidate_indices:
                        continue
                    score: float = self.semantic_matcher(primary_sent, cand_sent)
                    if score > best_score:
                        best_score = score
                        best_cand = cand_sent
                        best_ci = ci

                if best_score >= semantic_threshold and best_ci >= 0:
                    near_match_count += 1
                    matched_candidate_indices.add(best_ci)
                    semantic_matched = True
                    if len(near_matches) < max_examples:
                        near_matches.append(
                            {
                                "primary": primary_sent,
                                "candidate": best_cand,
                                "score": best_score,
                            }
                        )
            # else: semantic_matcher is None → exact-only mode (req 5.19).
            # No semantic comparison is attempted; the report is produced
            # using exact-match results only and semantic_threshold is 0.0.

            if not exact_matched and not semantic_matched:
                unmatched_primary.append(primary_sent)

        # Unmatched candidates
        unmatched_candidate: list[str] = [
            s for ci, s in enumerate(candidate_sentences)
            if ci not in matched_candidate_indices
        ]

        unmatched_primary_count = len(unmatched_primary)
        unmatched_candidate_count = len(unmatched_candidate)

        # ------------------------------------------------------------------
        # Agreement rate
        # ------------------------------------------------------------------
        if primary_sentence_count > 0:
            agreement_rate = (exact_match_count + near_match_count) / primary_sentence_count
        else:
            agreement_rate = 0.0

        # ------------------------------------------------------------------
        # Semantic threshold in report
        # ------------------------------------------------------------------
        reported_semantic_threshold = 0.0 if self.semantic_matcher is None else semantic_threshold

        # ------------------------------------------------------------------
        # Cap examples
        # ------------------------------------------------------------------
        return {
            "primary_sentence_count": primary_sentence_count,
            "candidate_sentence_count": candidate_sentence_count,
            "exact_match_count": exact_match_count,
            "near_match_count": near_match_count,
            "unmatched_primary_count": unmatched_primary_count,
            "unmatched_candidate_count": unmatched_candidate_count,
            "agreement_rate": agreement_rate,
            "semantic_threshold": reported_semantic_threshold,
            "examples": {
                "unmatched_primary": unmatched_primary[:max_examples],
                "unmatched_candidate": unmatched_candidate[:max_examples],
                "near_matches": near_matches[:max_examples],
            },
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_sentences(blocks: list) -> list[str]:
    """Extract text sentences from a list of block dicts.

    Each block is expected to be a dict.  The text is taken from
    ``block.get("text", "")``.  Empty strings are discarded.

    This function contains no sentence-segmentation logic — it treats the
    ``"text"`` value of each block as a single "sentence" unit, consistent
    with the task specification.
    """
    sentences: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text: str = block.get("text", "")
        if text:
            sentences.append(text)
    return sentences
