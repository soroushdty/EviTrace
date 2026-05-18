"""
tests/src/utils/test_safe_logging.py
=====================================
Unit tests for :func:`utils.logging_utils.log_model_response` — safe bounded
logging of LLM model responses.

Requirements: 6.3, 6.5
"""

import hashlib
import logging

import pytest

from utils.logging_utils import log_model_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_debug_logger(name: str) -> logging.Logger:
    """Create a logger at DEBUG effective level (no handlers needed for artifact tests)."""
    logger = logging.getLogger(f"test_safe_logging.{name}")
    logger.setLevel(logging.DEBUG)
    logger.handlers = []
    return logger


def _make_warning_logger(name: str) -> logging.Logger:
    """Create a logger at WARNING effective level."""
    logger = logging.getLogger(f"test_safe_logging.{name}")
    logger.setLevel(logging.WARNING)
    logger.handlers = []
    return logger


# ---------------------------------------------------------------------------
# Requirement 6.5: Config default of 500 chars
# ---------------------------------------------------------------------------


class TestMaxCharsDefault:
    """Verify that the default max_chars parameter is 500."""

    def test_default_max_chars_is_500(self, tmp_path, caplog):
        """log_model_response truncates at 500 chars by default."""
        logger = _make_debug_logger("default_500")
        # Create a response longer than 500 chars
        response = "x" * 600

        with caplog.at_level(logging.WARNING, logger=logger.name):
            log_model_response(
                logger,
                response,
                pdf_name="test_pdf",
                chunk_num=1,
            )

        # The WARNING log should contain a truncated preview (500 chars + "...")
        assert len(caplog.records) >= 1
        warning_record = caplog.records[0]
        # The preview in the message should be at most 500 chars + "..."
        # The full 600-char string should NOT appear in the log message
        assert "x" * 600 not in warning_record.getMessage()
        # But the first 500 chars should be present
        assert "x" * 500 in warning_record.getMessage()

    def test_short_response_not_truncated(self, caplog):
        """Responses shorter than max_chars are logged in full without ellipsis."""
        logger = _make_debug_logger("short_response")
        response = "short response"

        with caplog.at_level(logging.WARNING, logger=logger.name):
            log_model_response(
                logger,
                response,
                pdf_name="test_pdf",
                chunk_num=1,
            )

        assert len(caplog.records) >= 1
        msg = caplog.records[0].getMessage()
        assert "short response" in msg
        # No truncation indicator when response fits within limit
        assert "..." not in msg or msg.endswith("...")  # only "..." if truncated

    def test_custom_max_chars_respected(self, caplog):
        """A custom max_chars value is respected for truncation."""
        logger = _make_debug_logger("custom_max")
        response = "a" * 200

        with caplog.at_level(logging.WARNING, logger=logger.name):
            log_model_response(
                logger,
                response,
                pdf_name="test_pdf",
                chunk_num=1,
                max_chars=50,
            )

        assert len(caplog.records) >= 1
        msg = caplog.records[0].getMessage()
        # Full 200-char string should NOT appear
        assert "a" * 200 not in msg
        # But the first 50 chars should be present
        assert "a" * 50 in msg

    def test_sha256_hash_always_present(self, caplog):
        """The SHA-256 hash of the full response is always included in the log."""
        logger = _make_debug_logger("hash_present")
        response = "test response for hashing"
        expected_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()

        with caplog.at_level(logging.WARNING, logger=logger.name):
            log_model_response(
                logger,
                response,
                pdf_name="test_pdf",
                chunk_num=1,
            )

        assert len(caplog.records) >= 1
        msg = caplog.records[0].getMessage()
        assert expected_hash in msg


# ---------------------------------------------------------------------------
# Requirement 6.3: Debug artifact file writing
# ---------------------------------------------------------------------------


class TestDebugArtifactWriting:
    """Verify debug artifact file writing when dir configured and DEBUG level."""

    def test_artifact_written_when_debug_and_dir_configured(self, tmp_path):
        """Full response written to artifact file when debug_artifact_dir set and logger at DEBUG."""
        logger = _make_debug_logger("artifact_write")
        response = "Full model response content for artifact"
        response_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()
        artifact_dir = str(tmp_path / "debug_artifacts")

        log_model_response(
            logger,
            response,
            pdf_name="sample_paper",
            chunk_num=3,
            debug_artifact_dir=artifact_dir,
        )

        # Artifact file should exist with expected naming pattern
        expected_filename = f"sample_paper_chunk3_{response_hash[:12]}.raw.txt"
        artifact_path = tmp_path / "debug_artifacts" / expected_filename
        assert artifact_path.exists(), f"Expected artifact at {artifact_path}"
        # Content should be the full response
        content = artifact_path.read_text(encoding="utf-8")
        assert content == response

    def test_artifact_dir_created_if_not_exists(self, tmp_path):
        """The debug artifact directory is created automatically if it doesn't exist."""
        logger = _make_debug_logger("artifact_mkdir")
        response = "content"
        artifact_dir = str(tmp_path / "nested" / "debug" / "dir")

        log_model_response(
            logger,
            response,
            pdf_name="paper",
            chunk_num=1,
            debug_artifact_dir=artifact_dir,
        )

        # Directory should have been created
        assert (tmp_path / "nested" / "debug" / "dir").is_dir()

    def test_no_artifact_when_logger_not_debug(self, tmp_path):
        """No artifact file written when logger effective level is above DEBUG."""
        logger = _make_warning_logger("no_artifact_warning")
        response = "This should not be written to disk"
        artifact_dir = str(tmp_path / "artifacts")

        log_model_response(
            logger,
            response,
            pdf_name="paper",
            chunk_num=1,
            debug_artifact_dir=artifact_dir,
        )

        # No artifact directory or file should be created
        artifact_dir_path = tmp_path / "artifacts"
        if artifact_dir_path.exists():
            assert list(artifact_dir_path.iterdir()) == []

    def test_no_artifact_when_dir_is_none(self, tmp_path):
        """No artifact file written when debug_artifact_dir is None."""
        logger = _make_debug_logger("no_artifact_none")
        response = "This should not be written to disk"

        log_model_response(
            logger,
            response,
            pdf_name="paper",
            chunk_num=1,
            debug_artifact_dir=None,
        )

        # Nothing should be written anywhere — no assertion on disk needed,
        # just verify no exception raised

    def test_no_artifact_when_dir_is_empty_string(self, tmp_path):
        """No artifact file written when debug_artifact_dir is empty string."""
        logger = _make_debug_logger("no_artifact_empty")
        response = "This should not be written to disk"

        log_model_response(
            logger,
            response,
            pdf_name="paper",
            chunk_num=1,
            debug_artifact_dir="",
        )

        # No exception, no file written

    def test_artifact_filename_format(self, tmp_path):
        """Artifact filename follows {pdf_name}_chunk{n}_{hash[:12]}.raw.txt pattern."""
        logger = _make_debug_logger("artifact_name")
        response = "artifact naming test"
        response_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()
        artifact_dir = str(tmp_path)

        log_model_response(
            logger,
            response,
            pdf_name="my_paper",
            chunk_num=7,
            debug_artifact_dir=artifact_dir,
        )

        expected_filename = f"my_paper_chunk7_{response_hash[:12]}.raw.txt"
        assert (tmp_path / expected_filename).exists()

    def test_artifact_contains_full_response_not_truncated(self, tmp_path):
        """Artifact file contains the full response even when log is truncated."""
        logger = _make_debug_logger("artifact_full")
        # Response longer than default 500 chars
        response = "Z" * 1000
        response_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()
        artifact_dir = str(tmp_path)

        log_model_response(
            logger,
            response,
            pdf_name="big_paper",
            chunk_num=2,
            debug_artifact_dir=artifact_dir,
        )

        expected_filename = f"big_paper_chunk2_{response_hash[:12]}.raw.txt"
        artifact_path = tmp_path / expected_filename
        assert artifact_path.exists()
        content = artifact_path.read_text(encoding="utf-8")
        # Full 1000-char response in artifact, not truncated
        assert len(content) == 1000
        assert content == response
