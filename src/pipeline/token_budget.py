"""
pipeline/token_budget.py
-----------------------------------------
Token budget estimation, checking, and graduated mitigation (Requirement 7:
Token Budget Enforcement).

Data models
-----------
BudgetCheckResult
    Result of a single ``check_budget`` call: whether a prompt fits within
    its stage's Token_Budget, the estimated token count, the budget limit
    applied, the stage name, and (when computable) the top contributing
    prompt sections.

TokenBudgetExceededError
    Raised by :func:`apply_mitigation` when a prompt still exceeds its
    Token_Budget after all mitigation strategies have been exhausted.

Functions
---------
estimate_tokens
    Chars-divided-by-4 token count heuristic (Req 7.1).
check_budget
    Cheap within-budget check for a single flattened prompt string.
apply_mitigation
    Graduated mitigation: (a) evidence pruning, (b) request-splitting
    signal, (c) rejection (Req 7.2, 7.4).
load_budgets
    Resolve the ``token_budgets`` config section into a validated
    ``{stage: limit}`` mapping, falling back to documented defaults for any
    missing/invalid entry (Req 7.5, 7.6).

Scope note (see task 3.1 boundary)
-----------------------------------
This module receives only flattened prompt text (a ``dict[str, str]`` of
named sections), never the structured evidence items or field-definition
lists that live in ``evidence_index.py`` / ``pdf_processor.py``. Two
consequences follow, both intentional and documented rather than
fabricated:

* Evidence pruning (mitigation strategy (a)) operates on the *evidence*
  section's raw text, splitting it into "items" on blank-line boundaries
  (``"\\n\\n"``) -- a convention this module defines for itself, not one
  guaranteed by the upstream evidence-serialization format. It cannot
  identify or protect individual Evidence_IDs by confidence label, because
  confidence labels are not present in the flat text it receives. Full
  Property-21-style ("preserve high-confidence Evidence_IDs") pruning
  requires structured evidence data and is deferred to the task 8.2
  integration in ``pdf_processor.py``, which has that data.
* Request splitting (mitigation strategy (b)) into field groups of at most
  5 fields requires field-definition metadata this module does not
  receive. ``apply_mitigation`` can only *signal* that splitting is needed
  (via the returned warnings list, before falling through to rejection if
  splitting alone would have been the only viable strategy); it cannot
  itself perform the split. Actual field-group splitting is implemented by
  the task 8.2 integration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from utils.logging_utils import get_logger

logger = get_logger(__name__)

# Documented default Token_Budget values (Req 7.5).
DEFAULT_BUDGETS: dict[str, int] = {
    "extraction_chunk": 100_000,
    "validation_repair": 20_000,
    "synthesis": 120_000,
    "cache_warmup": 10_000,
}

# Canonical ordering used when concatenating named prompt sections into a
# single prompt string, matching the section names called out in
# Requirement 7.4 (system prompt, evidence package, field definitions,
# prior context). Any other keys present in a `prompt_parts` mapping are
# appended afterwards in sorted order, for determinism.
_SECTION_ORDER = ("system", "evidence", "field_definitions", "prior_context")

# Delimiter this module uses to split a flattened evidence-section string
# into individually prunable "items". This is a convention local to this
# module (see module docstring "Scope note"), not a guarantee made by the
# upstream evidence serialization format.
_EVIDENCE_ITEM_DELIMITER = "\n\n"


@dataclass
class BudgetCheckResult:
    """Result of checking a prompt's estimated token count against budget.

    Attributes
    ----------
    within_budget:
        ``True`` iff ``estimated_tokens <= budget_limit``.
    estimated_tokens:
        ``estimate_tokens(prompt_text)`` for the checked prompt.
    budget_limit:
        The Token_Budget applied for ``stage``.
    stage:
        The stage name the check was performed for.
    top_sections:
        The top contributing prompt sections ranked by estimated token
        size, as ``(section_name, estimated_tokens)`` pairs. ``check_budget``
        only receives a single flattened prompt string (no section
        breakdown), so this is always ``[]`` there; it is populated by
        :func:`apply_mitigation`'s rejection path, which does receive a
        section breakdown (Req 7.4).
    """

    within_budget: bool
    estimated_tokens: int
    budget_limit: int
    stage: str
    top_sections: list[tuple[str, int]] = field(default_factory=list)


class TokenBudgetExceededError(Exception):
    """Raised when a prompt exceeds its Token_Budget after all mitigation.

    Carries ``stage``, ``estimated``, ``budget``, and ``top_sections`` as
    plain attributes (not just embedded in the message string) so callers
    can act on them programmatically (Req 7.2, 7.4).
    """

    def __init__(
        self,
        stage: str,
        estimated: int,
        budget: int,
        top_sections: list[tuple[str, int]],
    ) -> None:
        self.stage = stage
        self.estimated = estimated
        self.budget = budget
        self.top_sections = top_sections
        super().__init__(
            f"Token budget exceeded for stage {stage!r}: estimated "
            f"{estimated} tokens exceeds budget {budget} "
            f"(top sections: {top_sections})"
        )


def estimate_tokens(text: str) -> int:
    """Estimate token count using the chars-divided-by-4 heuristic.

    Exactly ``len(text) // 4`` -- floor division, no rounding up, no +1
    (Requirement 7.1, Correctness Property 15).
    """
    return len(text) // 4


def _ordered_items(prompt_parts: dict[str, str]) -> list[tuple[str, str]]:
    """Return ``(name, text)`` pairs in canonical section order.

    Known section names (see ``_SECTION_ORDER``) come first in their fixed
    order; any additional keys follow, sorted alphabetically for
    determinism (needed so the joined prompt text -- and therefore its
    token estimate -- does not depend on dict insertion order).
    """
    ordered_keys = [key for key in _SECTION_ORDER if key in prompt_parts]
    remaining_keys = sorted(key for key in prompt_parts if key not in _SECTION_ORDER)
    ordered_keys.extend(remaining_keys)
    return [(key, prompt_parts[key]) for key in ordered_keys]


def _join_parts(prompt_parts: dict[str, str]) -> str:
    return "".join(text for _, text in _ordered_items(prompt_parts))


def _rank_top_sections(prompt_parts: dict[str, str], top_n: int = 3) -> list[tuple[str, int]]:
    """Rank prompt sections by estimated token size, descending (Req 7.4)."""
    sizes = [(name, estimate_tokens(text)) for name, text in prompt_parts.items()]
    sizes.sort(key=lambda item: item[1], reverse=True)
    return sizes[:top_n]


def check_budget(
    prompt_text: str,
    stage: str,
    budgets: dict[str, int],
) -> BudgetCheckResult:
    """Check whether ``prompt_text`` fits within ``stage``'s Token_Budget.

    ``budgets`` is expected to already be a validated ``{stage: limit}``
    mapping (typically produced by :func:`load_budgets`). If ``stage`` is
    absent from ``budgets``, this falls back to the documented default for
    that stage (or the ``extraction_chunk`` default if the stage itself is
    unrecognized) and logs a WARNING, rather than raising -- callers can
    still get a usable result even if they pass a partial mapping.

    Only receives a single flattened prompt string, so ``top_sections`` is
    always ``[]`` here (see :class:`BudgetCheckResult` docstring).
    """
    budget_limit = budgets.get(stage)
    if budget_limit is None:
        budget_limit = DEFAULT_BUDGETS.get(stage, DEFAULT_BUDGETS["extraction_chunk"])
        logger.warning(
            "check_budget: stage %r not present in budgets mapping; "
            "falling back to default budget %d",
            stage,
            budget_limit,
        )

    estimated = estimate_tokens(prompt_text)
    return BudgetCheckResult(
        within_budget=estimated <= budget_limit,
        estimated_tokens=estimated,
        budget_limit=budget_limit,
        stage=stage,
        top_sections=[],
    )


def _prune_evidence(
    prompt_parts: dict[str, str],
    budget: int,
    config: dict[str, Any],
) -> tuple[dict[str, str], bool]:
    """Attempt mitigation (a): reduce the "evidence" section to fit budget.

    Returns ``(new_parts, pruned)`` where ``new_parts`` is a copy of
    ``prompt_parts`` with a (possibly) reduced "evidence" entry, and
    ``pruned`` indicates whether the evidence text actually changed.

    Pruning proceeds in stages, each only applied if still over budget:
    1. Cap item count at ``config["max_evidence_items_per_chunk"]`` (if a
       valid positive int), keeping the earliest items.
    2. Drop trailing items one at a time until the full joined prompt
       estimate fits, or only one item remains.
    3. Cap total evidence chars at ``config["max_evidence_chars_per_chunk"]``
       (if a valid positive int).
    4. As a last resort, truncate the remaining evidence text in
       proportional slices until the estimate fits (or the text is empty).

    See module docstring "Scope note" for why this operates on flat text
    (item boundaries via blank-line delimiter) rather than on structured,
    confidence-labeled evidence records.
    """
    parts = dict(prompt_parts)
    evidence = parts.get("evidence")
    if not evidence:
        return parts, False

    def estimate_with_evidence(ev_text: str) -> int:
        candidate = dict(parts)
        candidate["evidence"] = ev_text
        return estimate_tokens(_join_parts(candidate))

    items = evidence.split(_EVIDENCE_ITEM_DELIMITER)

    max_items = config.get("max_evidence_items_per_chunk")
    if isinstance(max_items, int) and not isinstance(max_items, bool) and max_items > 0:
        items = items[:max_items]

    current = _EVIDENCE_ITEM_DELIMITER.join(items)

    # Drop trailing items while still over budget (keep at least one item).
    while len(items) > 1 and estimate_with_evidence(current) > budget:
        items = items[:-1]
        current = _EVIDENCE_ITEM_DELIMITER.join(items)

    max_chars = config.get("max_evidence_chars_per_chunk")
    if (
        isinstance(max_chars, int)
        and not isinstance(max_chars, bool)
        and max_chars > 0
        and len(current) > max_chars
    ):
        current = current[:max_chars]

    # Last resort: proportional char-level truncation.
    while current and estimate_with_evidence(current) > budget:
        cut = max(1, len(current) // 10)
        current = current[:-cut]

    parts["evidence"] = current
    return parts, current != evidence


def apply_mitigation(
    prompt_parts: dict[str, str],
    stage: str,
    budget: int,
    config: dict[str, Any],
) -> tuple[str, list[str]]:
    """Apply graduated mitigation: prune -> split-signal -> reject.

    Mitigation strategies are attempted in strict order (Req 7.2,
    Correctness Property 22) and the first one that brings the estimate
    within budget is used without attempting subsequent strategies:

    (a) Evidence pruning -- see :func:`_prune_evidence`.
    (b) Request splitting into field groups of at most 5 fields -- this
        module cannot perform the split itself (no field-definition data;
        see module docstring "Scope note"). It can only append a
        split-required marker to the returned warnings and continue.
    (c) Rejection -- raises :class:`TokenBudgetExceededError` with the
        stage, the best-effort post-pruning estimate, the budget, and the
        top three contributing sections (Req 7.4), and logs a WARNING.

    Returns ``(mitigated_prompt, warnings)`` on success (including the
    trivial case where no mitigation was needed at all).
    """
    config = config or {}
    warnings: list[str] = []

    full_text = _join_parts(prompt_parts)
    estimated = estimate_tokens(full_text)
    if estimated <= budget:
        return full_text, warnings

    # (a) Evidence pruning.
    pruned_parts, pruned = _prune_evidence(prompt_parts, budget, config)
    pruned_text = _join_parts(pruned_parts)
    estimated_after_prune = estimate_tokens(pruned_text)

    if pruned:
        warnings.append(
            f"evidence pruning applied for stage {stage!r}: estimated tokens "
            f"reduced from {estimated} to {estimated_after_prune} "
            f"(budget {budget})"
        )

    if estimated_after_prune <= budget:
        return pruned_text, warnings

    # (b) Request splitting -- signal only (see module docstring).
    warnings.append(
        f"request splitting required for stage {stage!r}: field-group "
        "splitting (<=5 fields per sub-request) cannot be performed by "
        "token_budget.py because it does not receive field-definition "
        "metadata; deferred to the pdf_processor.py integration (task 8.2)."
    )

    # (c) Rejection.
    top_sections = _rank_top_sections(pruned_parts)
    logger.warning(
        "Token budget exceeded for stage %r after evidence pruning: "
        "estimated=%d budget=%d top_sections=%s",
        stage,
        estimated_after_prune,
        budget,
        top_sections,
    )
    raise TokenBudgetExceededError(
        stage=stage,
        estimated=estimated_after_prune,
        budget=budget,
        top_sections=top_sections,
    )


def _is_valid_budget_value(value: Any) -> bool:
    """A valid Token_Budget value is a positive, non-bool int (Req 7.6)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def load_budgets(config: "dict | None") -> dict[str, int]:
    """Resolve the ``token_budgets`` config section into stage limits.

    For each of the four documented stages (extraction_chunk,
    validation_repair, synthesis, cache_warmup): if the configured value is
    missing, non-integer, zero, or negative, the documented default for
    that stage is used instead, and a WARNING is logged naming the stage
    and the invalid value that was replaced (Req 7.6). Valid values pass
    through unchanged (Req 7.5).
    """
    raw = (config or {}).get("token_budgets", {})
    if not isinstance(raw, dict):
        logger.warning(
            "Invalid token_budgets config value %r (expected a mapping of "
            "stage -> limit); using documented defaults for all stages",
            raw,
        )
        raw = {}

    budgets: dict[str, int] = {}
    for stage, default in DEFAULT_BUDGETS.items():
        value = raw.get(stage)
        if _is_valid_budget_value(value):
            budgets[stage] = value
        else:
            logger.warning(
                "Invalid token_budgets.%s value %r; using default %d",
                stage,
                value,
                default,
            )
            budgets[stage] = default

    return budgets
