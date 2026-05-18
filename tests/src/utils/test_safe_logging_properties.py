"""
tests/src/utils/test_safe_logging_properties.py
================================================
Property-based tests for safe logging of model responses (Property 11).

**Validates: Requirements 6.1, 6.2, 6.4**
"""

from __future__ import annotations

import hashlib
import logging

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from utils.logging_utils import log_model_response


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate response strings that exceed a given max_log_response_chars limit.
# We use max_chars values between 10 and 200 to keep tests fast, and generate
# responses that are strictly longer than max_chars.
_max_chars_strategy = st.integers(min_value=10, max_value=200)


def _response_exceeding_limit(max_chars: int) -> st.SearchStrategy[str]:
    """Generate a response string strictly longer than max_chars."""
    # min_size = max_chars + 1 ensures the response always exceeds the limit
    return st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        min_size=max_chars + 1,
        max_size=max_chars + 500,
    )


# ---------------------------------------------------------------------------
# Property 11: Log truncation with hash correlation
# ---------------------------------------------------------------------------


@given(
    max_chars=_max_chars_strategy,
    data=st.data(),
)
@settings(max_examples=100)
def test_truncated_log_includes_sha256_hash_and_respects_limit(
    max_chars: int,
    data: st.DataObject,
) -> None:
    """For any model response exceeding max_log_response_chars, the WARNING log
    message SHALL be truncated to at most max_log_response_chars characters AND
    SHALL include the SHA-256 hex digest of the full response.

    **Validates: Requirements 6.1, 6.2**
    """
    response = data.draw(_response_exceeding_limit(max_chars))
    assume(len(response) > max_chars)

    # Set up a logger that captures WARNING messages
    logger = logging.getLogger("test_prop11_truncation")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    class _CaptureHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = _CaptureHandler()
    handler.setLevel(logging.WARNING)
    logger.addHandler(handler)

    # Call log_model_response with no artifact dir
    log_model_response(
        logger,
        response,
        pdf_name="test_pdf",
        chunk_num=1,
        max_chars=max_chars,
        debug_artifact_dir=None,
    )

    # There should be exactly one WARNING record
    warning_records = [r for r in handler.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1, "Expected exactly one WARNING log record"

    log_message = warning_records[0].getMessage()

    # The log message must include the SHA-256 hash of the full response
    expected_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()
    assert expected_hash in log_message, (
        f"SHA-256 hash not found in log message. Expected: {expected_hash}"
    )

    # The log message must NOT contain the full response (it should be truncated)
    # The truncated preview is at most max_chars characters of the response + "..."
    # So the full response should not appear verbatim in the log message
    assert response not in log_message, (
        "Full response should not appear in log message when it exceeds max_chars"
    )

    # The preview portion in the log should be at most max_chars from the response
    # (the log format adds metadata around it, but the response content portion
    # should be truncated)
    truncated_preview = response[:max_chars] + "..."
    assert truncated_preview in log_message, (
        "Truncated preview (response[:max_chars] + '...') not found in log message"
    )


@given(
    max_chars=_max_chars_strategy,
    data=st.data(),
)
@settings(max_examples=100)
def test_full_response_never_in_log_output_without_artifact_dir(
    max_chars: int,
    data: st.DataObject,
) -> None:
    """When no debug-artifact directory is configured, the full response SHALL NOT
    appear in any log output or disk file, regardless of log level.

    **Validates: Requirements 6.4**
    """
    response = data.draw(_response_exceeding_limit(max_chars))
    assume(len(response) > max_chars)

    # Set up a logger at DEBUG level (most permissive) to capture ALL messages
    logger = logging.getLogger("test_prop11_no_artifact")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    class _CaptureHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = _CaptureHandler()
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    # Call with no artifact dir configured — full response must never appear
    log_model_response(
        logger,
        response,
        pdf_name="test_pdf",
        chunk_num=0,
        max_chars=max_chars,
        debug_artifact_dir=None,
    )

    # Check ALL log records — the full response must not appear in any of them
    for record in handler.records:
        msg = record.getMessage()
        assert response not in msg, (
            f"Full response found in log output at level {record.levelname} "
            f"when no artifact dir is configured"
        )
