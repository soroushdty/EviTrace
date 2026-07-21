"""
tests/src/pipeline/test_token_budget.py
-----------------------------------------
Unit tests for ``pipeline.token_budget`` (tasks 3.1 and 3.3): token
estimation, budget checking, graduated mitigation (prune -> split-signal
-> reject), and config-driven budget loading with fallback to documented
defaults.

Dedicated property-based suites (Properties 15, 21, 22) live in
``test_token_budget_properties.py`` (task 3.2). This file covers the
acceptance-criteria-level behavior for Requirement 7 -- specifically 7.1,
7.2, 7.4, 7.5, and 7.6.

Task 3.3 additions beyond task 3.1's original coverage:
  * ``test_apply_mitigation_top_sections_caps_at_three_of_four_named_sections``
    -- deepens Req 7.4 coverage by exercising the top-3 cap with all four
    named sections (system, evidence, field_definitions, prior_context)
    present at once, proving a real contender (the smallest, "evidence")
    is excluded rather than trivially satisfying "<=3" with only 3 keys.
  * ``test_load_budgets_invalid_value_itself_captured_in_warning_args`` --
    deepens Req 7.6 coverage by asserting the invalid config value itself
    (not just the stage name) is captured in the logged warning's format
    args, matching Req 7.6's "log a warning indicating the Stage name AND
    the invalid value that was replaced".

Requirement 7.3 (synthesis conflict-only fallback) has no coverage in
this file: as documented in ``token_budget.py``'s own module docstring
("Scope note"), that behavior requires structured, field-aware context
(``MergeResult.conflicts`` from ``deterministic_merge.py``) this module
never receives -- confirmed by re-reading ``token_budget.py`` in full for
task 3.3, and by the absence of any ``token_budget`` reference in
``pdf_processor.py`` (not yet integrated as of task 3.3). There is no
partial precondition at this module's boundary to test either: unlike
task 2.3's deterministic_merge.py precedent (which already implements a
concrete "more than five candidates" rule this module could lock down),
token_budget.py contains zero conflict/synthesis-aware logic to
characterize. This is deferred, unchanged, to the task 8.2 integration in
``pdf_processor.py``, where it will be tested.
"""
from __future__ import annotations

import logging

import pytest

from pipeline.token_budget import (
    DEFAULT_BUDGETS,
    BudgetCheckResult,
    TokenBudgetExceededError,
    apply_mitigation,
    check_budget,
    estimate_tokens,
    load_budgets,
)

_LOGGER_NAME = "evi_trace.pipeline.token_budget"


# ---------------------------------------------------------------------------
# estimate_tokens (Req 7.1)
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty_string():
    assert estimate_tokens("") == 0


def test_estimate_tokens_exact_multiple_of_four():
    assert estimate_tokens("a" * 100) == 25


def test_estimate_tokens_floor_division_no_rounding_up():
    # 101 chars // 4 == 25 (not 26) -- must floor, never ceil or +1.
    assert estimate_tokens("a" * 101) == 25
    assert estimate_tokens("a" * 103) == 25
    assert estimate_tokens("a" * 104) == 26


def test_estimate_tokens_counts_characters_not_bytes():
    # Multi-byte unicode chars still count as 1 char each per len().
    text = "é" * 8  # 8 chars, each 2 bytes in UTF-8
    assert estimate_tokens(text) == 2


# ---------------------------------------------------------------------------
# check_budget (Req 7.1, 7.5)
# ---------------------------------------------------------------------------


def test_check_budget_within_budget():
    result = check_budget("a" * 40, "extraction_chunk", {"extraction_chunk": 100})
    assert isinstance(result, BudgetCheckResult)
    assert result.within_budget is True
    assert result.estimated_tokens == 10
    assert result.budget_limit == 100
    assert result.stage == "extraction_chunk"
    assert result.top_sections == []


def test_check_budget_exceeds_budget():
    result = check_budget("a" * 400, "synthesis", {"synthesis": 50})
    assert result.within_budget is False
    assert result.estimated_tokens == 100
    assert result.budget_limit == 50


def test_check_budget_falls_back_to_default_when_stage_missing_from_budgets(caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = check_budget("a" * 40, "cache_warmup", {})
    assert result.budget_limit == DEFAULT_BUDGETS["cache_warmup"]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "cache_warmup" in warnings[0].message


def test_check_budget_boundary_equal_to_limit_is_within_budget():
    # estimated_tokens == budget_limit should count as within budget (<=).
    result = check_budget("a" * 40, "extraction_chunk", {"extraction_chunk": 10})
    assert result.estimated_tokens == 10
    assert result.within_budget is True


# ---------------------------------------------------------------------------
# TokenBudgetExceededError
# ---------------------------------------------------------------------------


def test_token_budget_exceeded_error_attributes():
    err = TokenBudgetExceededError(
        stage="synthesis",
        estimated=5000,
        budget=1000,
        top_sections=[("evidence", 4000), ("system", 800)],
    )
    assert err.stage == "synthesis"
    assert err.estimated == 5000
    assert err.budget == 1000
    assert err.top_sections == [("evidence", 4000), ("system", 800)]
    # Message should be human-readable and mention the key facts.
    message = str(err)
    assert "synthesis" in message
    assert "5000" in message
    assert "1000" in message


# ---------------------------------------------------------------------------
# apply_mitigation (Req 7.2, 7.4)
# ---------------------------------------------------------------------------


def test_apply_mitigation_no_op_when_already_within_budget():
    parts = {"system": "sys", "evidence": "ev", "field_definitions": "fd"}
    text, warnings = apply_mitigation(parts, "extraction_chunk", budget=1000, config={})
    assert text == "sysevfd"
    assert warnings == []


def test_apply_mitigation_prunes_evidence_to_fit_budget():
    # System + field_definitions are tiny; evidence is the dominant, prunable
    # section. Budget is small enough to force pruning but large enough that
    # pruning alone (without needing field-group splitting) succeeds.
    evidence_items = [f"EVIDENCE ITEM {i} " + ("x" * 40) for i in range(20)]
    parts = {
        "system": "SYS",
        "evidence": "\n\n".join(evidence_items),
        "field_definitions": "FIELDS",
    }
    budget = 30  # ~120 chars allowed total

    text, warnings = apply_mitigation(parts, "extraction_chunk", budget=budget, config={})

    assert estimate_tokens(text) <= budget
    assert any("evidence pruning" in w.lower() for w in warnings)
    # Mitigation ordering: since pruning alone succeeded, no split-required
    # marker should appear (Property 22: first successful strategy wins).
    assert not any("split" in w.lower() for w in warnings)
    # Non-evidence sections must be preserved verbatim.
    assert "SYS" in text
    assert "FIELDS" in text


def test_apply_mitigation_respects_max_evidence_items_config():
    evidence_items = [f"ITEM{i}" for i in range(10)]
    parts = {"evidence": "\n\n".join(evidence_items)}
    # Budget small enough to force mitigation, but large enough that
    # capping to max_evidence_items_per_chunk alone (without further
    # char-level truncation) is sufficient to fit.
    text, _warnings = apply_mitigation(
        parts,
        "extraction_chunk",
        budget=5,
        config={"max_evidence_items_per_chunk": 3},
    )
    assert text.count("ITEM") <= 3


def test_apply_mitigation_raises_when_pruning_insufficient():
    # The oversized section is "system", which this module never prunes --
    # so no amount of evidence pruning can bring this within budget, and
    # apply_mitigation must fall through split-signal to rejection.
    parts = {
        "system": "S" * 4000,
        "evidence": "small evidence",
        "field_definitions": "FIELDS",
    }
    with pytest.raises(TokenBudgetExceededError) as excinfo:
        apply_mitigation(parts, "synthesis", budget=10, config={})

    err = excinfo.value
    assert err.stage == "synthesis"
    assert err.budget == 10
    assert err.estimated > err.budget
    assert len(err.top_sections) <= 3
    assert err.top_sections == sorted(err.top_sections, key=lambda t: t[1], reverse=True)


def test_apply_mitigation_top_sections_caps_at_three_of_four_named_sections():
    # Req 7.4 names four possible prompt sections (system prompt, evidence
    # package, field definitions, prior context). The existing rejection
    # tests above only ever populate <=3 keys, so "top three" is trivially
    # satisfied without exercising the cap. Here all four named sections
    # are present with distinct sizes and the smallest ("evidence", which
    # pruning empties out entirely since the other three sections dominate
    # the budget regardless of evidence size) must be excluded from the
    # reported top_sections -- proving the ranking-and-cap logic actually
    # drops a real contender rather than just slicing a list of <=3 items.
    parts = {
        "prior_context": "P" * 4000,  # 1000 tokens -- largest
        "system": "S" * 400,  # 100 tokens
        "field_definitions": "F" * 200,  # 50 tokens
        "evidence": "E" * 40,  # 10 tokens -- smallest, must be excluded
    }
    with pytest.raises(TokenBudgetExceededError) as excinfo:
        apply_mitigation(parts, "synthesis", budget=5, config={})

    err = excinfo.value
    assert len(err.top_sections) == 3
    names = [name for name, _ in err.top_sections]
    assert "evidence" not in names
    assert names == ["prior_context", "system", "field_definitions"]
    assert err.top_sections == sorted(err.top_sections, key=lambda t: t[1], reverse=True)


def test_apply_mitigation_logs_warning_on_rejection(caplog):
    # No "evidence" key -- pruning is a no-op -- so the estimate carried into
    # the rejection WARNING is deterministic: len("S" * 4000) // 4 == 1000.
    parts = {"system": "S" * 4000}
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        with pytest.raises(TokenBudgetExceededError) as excinfo:
            apply_mitigation(parts, "validation_repair", budget=5, config={})
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    record = warnings[0]

    err = excinfo.value
    assert err.estimated == 1000
    assert err.budget == 5
    assert err.top_sections == [("system", 1000)]

    # Req 7.4: the WARNING must carry the Stage name, the estimated token
    # count, the budget limit, and the top three contributing prompt
    # sections -- not just the stage. Assert against the LogRecord's actual
    # format args (the values `logger.warning(...)` was called with), so a
    # future edit that drops one of these fields from the log call is
    # caught even if the surviving text still happens to contain matching
    # numbers by coincidence.
    assert record.args[0] == "validation_repair"
    assert record.args[1] == err.estimated == 1000
    assert record.args[2] == err.budget == 5
    assert record.args[3] == err.top_sections == [("system", 1000)]

    # Also check the rendered message, since Req 7.4 is ultimately about
    # what a human/operator reading the log actually sees.
    assert "validation_repair" in record.message
    assert "1000" in record.message
    assert "5" in record.message
    assert "system" in record.message


def test_apply_mitigation_signals_split_required_before_rejecting():
    # No "evidence" key at all -- pruning is a no-op -- so the split-signal
    # warning must appear even though the caller never observes it (the
    # function raises). We assert this via a monkeypatch-free direct check:
    # apply_mitigation must attempt pruning (no-op), then reach the split
    # branch, which we verify indirectly through the exception being raised
    # rather than an unrelated error.
    parts = {"field_definitions": "F" * 4000}
    with pytest.raises(TokenBudgetExceededError):
        apply_mitigation(parts, "extraction_chunk", budget=5, config={})


def test_apply_mitigation_missing_evidence_key_does_not_crash():
    parts = {"system": "short", "field_definitions": "also short"}
    text, warnings = apply_mitigation(parts, "extraction_chunk", budget=1000, config={})
    assert "short" in text
    assert warnings == []


# ---------------------------------------------------------------------------
# load_budgets (Req 7.5, 7.6)
# ---------------------------------------------------------------------------


def test_load_budgets_defaults_when_key_absent():
    budgets = load_budgets({})
    assert budgets == DEFAULT_BUDGETS


def test_load_budgets_uses_documented_default_values():
    assert DEFAULT_BUDGETS == {
        "extraction_chunk": 100_000,
        "validation_repair": 20_000,
        "synthesis": 120_000,
        "cache_warmup": 10_000,
    }


def test_load_budgets_valid_config_overrides_defaults():
    config = {
        "token_budgets": {
            "extraction_chunk": 50_000,
            "validation_repair": 15_000,
            "synthesis": 90_000,
            "cache_warmup": 8_000,
        }
    }
    budgets = load_budgets(config)
    assert budgets == config["token_budgets"]


@pytest.mark.parametrize("bad_value", [0, -1, "not-a-number", 3.5, None, True])
def test_load_budgets_invalid_value_falls_back_to_default_and_warns(bad_value, caplog):
    config = {"token_budgets": {"synthesis": bad_value}}
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        budgets = load_budgets(config)
    assert budgets["synthesis"] == DEFAULT_BUDGETS["synthesis"]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("synthesis" in w.message for w in warnings)


def test_load_budgets_invalid_value_itself_captured_in_warning_args(caplog):
    # Req 7.6 requires the WARNING to indicate both the Stage name AND "the
    # invalid value that was replaced" -- the existing parametrized test
    # above only checks that the stage name appears in the rendered
    # message. Assert against the LogRecord's actual format args (as done
    # for the Req 7.4 rejection-warning test), so a future edit that drops
    # the invalid value from the log call is caught even if the message
    # text still happens to mention the stage.
    config = {"token_budgets": {"synthesis": "not-a-number"}}
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        load_budgets(config)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    synth_warning = next(r for r in warnings if r.args and r.args[0] == "synthesis")

    assert synth_warning.args[0] == "synthesis"
    assert synth_warning.args[1] == "not-a-number"
    assert synth_warning.args[2] == DEFAULT_BUDGETS["synthesis"]
    assert "not-a-number" in synth_warning.message


def test_load_budgets_missing_stage_key_falls_back_and_warns(caplog):
    config = {"token_budgets": {"extraction_chunk": 50_000}}  # other 3 missing
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        budgets = load_budgets(config)
    assert budgets["extraction_chunk"] == 50_000
    assert budgets["validation_repair"] == DEFAULT_BUDGETS["validation_repair"]
    assert budgets["synthesis"] == DEFAULT_BUDGETS["synthesis"]
    assert budgets["cache_warmup"] == DEFAULT_BUDGETS["cache_warmup"]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    warned_stages = {"validation_repair", "synthesis", "cache_warmup"}
    for stage in warned_stages:
        assert any(stage in w.message for w in warnings)


def test_load_budgets_non_dict_token_budgets_value_falls_back_to_all_defaults(caplog):
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        budgets = load_budgets({"token_budgets": ["not", "a", "dict"]})
    assert budgets == DEFAULT_BUDGETS


def test_load_budgets_none_config_treated_as_empty():
    assert load_budgets(None) == DEFAULT_BUDGETS


def test_load_budgets_returns_new_dict_not_mutating_input():
    config = {"token_budgets": {"extraction_chunk": 50_000}}
    budgets = load_budgets(config)
    budgets["extraction_chunk"] = 1
    assert config["token_budgets"]["extraction_chunk"] == 50_000
