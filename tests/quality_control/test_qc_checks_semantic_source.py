"""
tests/quality_control/test_qc_checks_semantic_source.py
---------------------------------------------------------
Unit tests for SemanticSourceVerificationCheck.

Task 4.4 — Requirements: 4.5, 4.7, 4.8, 4.9, 4.14

Test cases:
  1. Constructor raises ValueError for invalid on_index_unavailable values
  2. Constructor succeeds for all three valid values
  3. skip mode: unavailable store → status="unavailable", score=0.0, all six evidence keys None
  4. fail mode: unavailable store → RuntimeError raised
  5. degrade mode: unavailable store → matcher called, WARNING logged, VerificationResult returned
  6. No heavy imports: after importing SemanticSourceVerificationCheck,
     "sentence_transformers", "faiss", "torch" not in sys.modules
"""
from __future__ import annotations

import logging
import sys
from unittest.mock import MagicMock

import pytest

from quality_control.checks.semantic_source import (
    SemanticSourceVerificationCheck,
    _EVIDENCE_KEYS,
)
from quality_control.models import VerificationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNAVAILABLE_STORES = [
    None,           # None
    {},             # empty dict
    {"sentences": []},  # sentences key present but empty
    {"other_key": "value"},  # sentences key absent
]

_VALID_MODES = ["skip", "fail", "degrade"]


def _make_check(mode: str, matcher: MagicMock | None = None) -> SemanticSourceVerificationCheck:
    if matcher is None:
        matcher = MagicMock()
    return SemanticSourceVerificationCheck(matcher=matcher, on_index_unavailable=mode)


# ---------------------------------------------------------------------------
# 1. Constructor ValueError for invalid on_index_unavailable
# ---------------------------------------------------------------------------

class TestConstructorValidation:
    """Requirement 4.5: constructor raises ValueError for invalid on_index_unavailable."""

    @pytest.mark.parametrize("bad_value", ["invalid", "SKIP", "", "FAIL", "Degrade", "none", 42, None])
    def test_invalid_on_index_unavailable_raises_value_error(self, bad_value):
        with pytest.raises(ValueError, match="on_index_unavailable"):
            SemanticSourceVerificationCheck(
                matcher=MagicMock(),
                on_index_unavailable=bad_value,
            )

    def test_error_message_lists_valid_values(self):
        """The error message should mention all three valid values."""
        with pytest.raises(ValueError) as exc_info:
            SemanticSourceVerificationCheck(
                matcher=MagicMock(),
                on_index_unavailable="bad",
            )
        msg = str(exc_info.value)
        assert "skip" in msg
        assert "fail" in msg
        assert "degrade" in msg


# ---------------------------------------------------------------------------
# 2. Constructor succeeds for valid values
# ---------------------------------------------------------------------------

class TestConstructorValidValues:
    """Requirement 4.4: constructor accepts 'skip', 'fail', 'degrade'."""

    @pytest.mark.parametrize("mode", _VALID_MODES)
    def test_valid_mode_constructs_without_error(self, mode):
        check = SemanticSourceVerificationCheck(
            matcher=MagicMock(),
            on_index_unavailable=mode,
        )
        assert check.on_index_unavailable == mode

    @pytest.mark.parametrize("mode", _VALID_MODES)
    def test_check_name_class_var(self, mode):
        check = _make_check(mode)
        assert check.check_name == "semantic_source_verification"


# ---------------------------------------------------------------------------
# 3. skip mode: unavailable store → status="unavailable", score=0.0, all-None evidence
# ---------------------------------------------------------------------------

class TestSkipMode:
    """Requirement 4.7: skip mode returns unavailable result without calling matcher."""

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_skip_returns_unavailable_status(self, store):
        matcher = MagicMock()
        check = _make_check("skip", matcher)
        result = check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert isinstance(result, VerificationResult)
        assert result.status == "unavailable"

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_skip_returns_zero_score(self, store):
        check = _make_check("skip")
        result = check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert result.score == 0.0

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_skip_all_six_evidence_keys_are_none(self, store):
        check = _make_check("skip")
        result = check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert set(result.evidence.keys()) == set(_EVIDENCE_KEYS)
        for key in _EVIDENCE_KEYS:
            assert result.evidence[key] is None, f"Expected evidence[{key!r}] to be None"

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_skip_does_not_call_matcher(self, store):
        matcher = MagicMock()
        check = _make_check("skip", matcher)
        check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        matcher.assert_not_called()


# ---------------------------------------------------------------------------
# 4. fail mode: unavailable store → RuntimeError raised
# ---------------------------------------------------------------------------

class TestFailMode:
    """Requirement 4.8: fail mode raises RuntimeError when store is unavailable."""

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_fail_raises_runtime_error(self, store):
        check = _make_check("fail")
        with pytest.raises(RuntimeError):
            check.run(
                query="test query",
                sentence_store=store,
                embed_fn=MagicMock(),
                threshold=0.8,
                page_texts=None,
            )

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_fail_error_message_mentions_unavailable(self, store):
        check = _make_check("fail")
        with pytest.raises(RuntimeError) as exc_info:
            check.run(
                query="test query",
                sentence_store=store,
                embed_fn=MagicMock(),
                threshold=0.8,
                page_texts=None,
            )
        assert "unavailable" in str(exc_info.value).lower()

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_fail_does_not_call_matcher(self, store):
        matcher = MagicMock()
        check = _make_check("fail", matcher)
        with pytest.raises(RuntimeError):
            check.run(
                query="test query",
                sentence_store=store,
                embed_fn=MagicMock(),
                threshold=0.8,
                page_texts=None,
            )
        matcher.assert_not_called()


# ---------------------------------------------------------------------------
# 5. degrade mode: unavailable store → matcher called, WARNING logged, result returned
# ---------------------------------------------------------------------------

class TestDegradeMode:
    """Requirement 4.9: degrade mode calls matcher, emits WARNING, returns VerificationResult."""

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_degrade_calls_matcher(self, store):
        matcher = MagicMock(return_value=None)
        check = _make_check("degrade", matcher)
        check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts={"0": "some text"},
        )
        matcher.assert_called_once()

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_degrade_calls_matcher_with_none_store_and_page_texts(self, store):
        """Degrade mode calls matcher(query, None, page_texts, [])."""
        matcher = MagicMock(return_value=None)
        check = _make_check("degrade", matcher)
        page_texts = {"0": "some text"}
        check.run(
            query="my query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=page_texts,
        )
        matcher.assert_called_once_with("my query", None, page_texts, [])

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_degrade_emits_warning_log(self, store, caplog):
        matcher = MagicMock(return_value=None)
        check = _make_check("degrade", matcher)
        with caplog.at_level(logging.WARNING):
            check.run(
                query="test query",
                sentence_store=store,
                embed_fn=MagicMock(),
                threshold=0.8,
                page_texts=None,
            )
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) >= 1, "Expected at least one WARNING log record"

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_degrade_returns_verification_result(self, store):
        matcher = MagicMock(return_value=None)
        check = _make_check("degrade", matcher)
        result = check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert isinstance(result, VerificationResult)

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_degrade_matcher_returns_none_gives_all_none_evidence(self, store):
        matcher = MagicMock(return_value=None)
        check = _make_check("degrade", matcher)
        result = check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert set(result.evidence.keys()) == set(_EVIDENCE_KEYS)
        for key in _EVIDENCE_KEYS:
            assert result.evidence[key] is None

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_degrade_matcher_returns_dict_populates_evidence(self, store):
        fallback_result = {
            "found_sentence": "The quick brown fox",
            "page_index": 2,
            "prefix": "Before",
            "suffix": "After",
            "block_bbox": [0, 0, 100, 20],
            "span_bboxes": [[0, 0, 50, 10]],
            "confidence": 0.75,
        }
        matcher = MagicMock(return_value=fallback_result)
        check = _make_check("degrade", matcher)
        result = check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert result.evidence["found_sentence"] == "The quick brown fox"
        assert result.evidence["page_index"] == 2
        assert result.score == pytest.approx(0.75)

    @pytest.mark.parametrize("store", _UNAVAILABLE_STORES)
    def test_degrade_result_has_degraded_flag_in_details(self, store):
        matcher = MagicMock(return_value=None)
        check = _make_check("degrade", matcher)
        result = check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert result.details.get("degraded") is True


# ---------------------------------------------------------------------------
# 6. No heavy imports after importing SemanticSourceVerificationCheck
# ---------------------------------------------------------------------------

class TestNoHeavyImports:
    """Requirement 4.14: module must not cause sentence_transformers, faiss, or torch
    to appear in sys.modules at import time."""

    def test_sentence_transformers_not_in_sys_modules(self):
        assert "sentence_transformers" not in sys.modules, (
            "sentence_transformers was imported as a side-effect of importing "
            "SemanticSourceVerificationCheck"
        )

    def test_faiss_not_in_sys_modules(self):
        assert "faiss" not in sys.modules, (
            "faiss was imported as a side-effect of importing "
            "SemanticSourceVerificationCheck"
        )

    def test_torch_not_in_sys_modules(self):
        assert "torch" not in sys.modules, (
            "torch was imported as a side-effect of importing "
            "SemanticSourceVerificationCheck"
        )


# ---------------------------------------------------------------------------
# Additional edge-case unit tests
# ---------------------------------------------------------------------------

class TestAvailableStore:
    """When the sentence store IS available, the matcher is called normally."""

    def _available_store(self) -> dict:
        return {"sentences": ["sentence one", "sentence two"]}

    def test_available_store_calls_matcher(self):
        matcher = MagicMock(return_value={"score": 0.9, "found_sentence": "sentence one"})
        check = _make_check("skip", matcher)
        check.run(
            query="sentence one",
            sentence_store=self._available_store(),
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        matcher.assert_called_once()

    def test_available_store_above_threshold_returns_candidate_match(self):
        matcher = MagicMock(return_value={"score": 0.9})
        check = _make_check("skip", matcher)
        result = check.run(
            query="query",
            sentence_store=self._available_store(),
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert result.status == "candidate_match"
        assert result.score == pytest.approx(0.9)

    def test_available_store_below_threshold_returns_no_match(self):
        matcher = MagicMock(return_value={"score": 0.5})
        check = _make_check("skip", matcher)
        result = check.run(
            query="query",
            sentence_store=self._available_store(),
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert result.status == "no_match"
        assert result.score == 0.0
        assert result.details.get("below_threshold_score") == pytest.approx(0.5)

    def test_available_store_matcher_returns_none_gives_no_match(self):
        matcher = MagicMock(return_value=None)
        check = _make_check("skip", matcher)
        result = check.run(
            query="query",
            sentence_store=self._available_store(),
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert result.status == "no_match"
        assert result.score == 0.0


# ---------------------------------------------------------------------------
# Property 4: SemanticSourceVerificationCheck on_index_unavailable modes
# Feature: qc-migration, Property 4: SemanticSourceVerificationCheck on_index_unavailable modes
# ---------------------------------------------------------------------------

import logging
from hypothesis import given, settings
from hypothesis import strategies as st


# Strategies
_st_unavailable_store = st.sampled_from([None, {}, {"sentences": []}, {"other_key": "value"}])
_st_mode = st.sampled_from(["skip", "fail", "degrade"])


@given(
    store=_st_unavailable_store,
    mode=_st_mode,
)
@settings(max_examples=100)
def test_property4_on_index_unavailable_modes(store, mode):
    """
    Property 4: SemanticSourceVerificationCheck on_index_unavailable modes
    Validates: Requirements 4.7, 4.8, 4.9

    For any unavailable sentence store and any valid mode:
    - skip  → VerificationResult(status="unavailable", score=0.0, all six evidence keys None)
    - fail  → RuntimeError raised
    - degrade → matcher called, result is VerificationResult
    """
    matcher = MagicMock(return_value=None)
    check = SemanticSourceVerificationCheck(
        matcher=matcher,
        on_index_unavailable=mode,
    )

    if mode == "skip":
        result = check.run(
            query="test query",
            sentence_store=store,
            embed_fn=MagicMock(),
            threshold=0.8,
            page_texts=None,
        )
        assert isinstance(result, VerificationResult)
        assert result.status == "unavailable"
        assert result.score == 0.0
        assert set(result.evidence.keys()) == set(_EVIDENCE_KEYS)
        for key in _EVIDENCE_KEYS:
            assert result.evidence[key] is None, (
                f"Expected evidence[{key!r}] to be None for skip mode"
            )

    elif mode == "fail":
        with pytest.raises(RuntimeError):
            check.run(
                query="test query",
                sentence_store=store,
                embed_fn=MagicMock(),
                threshold=0.8,
                page_texts=None,
            )

    else:  # mode == "degrade"
        with caplog_context() as log_records:
            result = check.run(
                query="test query",
                sentence_store=store,
                embed_fn=MagicMock(),
                threshold=0.8,
                page_texts=None,
            )
        matcher.assert_called_once()
        assert isinstance(result, VerificationResult)
        warning_records = [r for r in log_records if r.levelno >= logging.WARNING]
        assert len(warning_records) >= 1, (
            "Expected at least one WARNING log record in degrade mode"
        )


# ---------------------------------------------------------------------------
# Helper: context-manager log capture for use outside pytest fixtures
# ---------------------------------------------------------------------------

import contextlib


@contextlib.contextmanager
def caplog_context(logger_name: str = "quality_control.checks.semantic_source"):
    """Capture log records from the given logger during the with-block."""
    records: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Handler()
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    # Also capture from root to catch propagated records
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        root_logger.removeHandler(handler)
        logger.setLevel(old_level)
