"""
test_logging_utils.py
=====================
Lightweight pytest tests for :mod:`logging_utils`.

Run with::

    pytest tests/test_logging_utils.py -v
"""

import logging
import os
from pathlib import Path

import pytest

from evi_trace.utils import logging_utils
from evi_trace.utils.path_utils import PROJECT_ROOT
from evi_trace.utils.logging_utils import setup_logging, _PROJECT_ROOT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_logger():
    """Remove all managed handlers from the evi_trace logger to ensure a clean
    state before each test."""
    lg = logging.getLogger("evi_trace")
    lg.handlers = []
    return lg


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

class TestResolveLogPath:
    def test_relative_path_is_under_project_root(self):
        p = logging_utils._resolve_log_path("log.txt")
        assert p == PROJECT_ROOT / "log.txt"

    def test_relative_subdir(self):
        p = logging_utils._resolve_log_path("logs/run.log")
        assert p == PROJECT_ROOT / "logs" / "run.log"

    def test_absolute_path_unchanged(self, tmp_path):
        abs_p = str(tmp_path / "mylog.txt")
        p = logging_utils._resolve_log_path(abs_p)
        assert p == Path(abs_p)


# ---------------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------------

class TestFileCreation:
    def test_creates_log_file_relative(self, tmp_path, monkeypatch):
        """Relative log file is created relative to project root (monkeypatched)."""
        monkeypatch.setattr("evi_trace.utils.path_utils.PROJECT_ROOT", tmp_path)
        _fresh_logger()

        setup_logging(log_file="log.txt", console_level="INFO")

        assert (tmp_path / "log.txt").exists()

    def test_creates_log_file_absolute(self, tmp_path):
        """Absolute log file path is used as-is."""
        abs_log = str(tmp_path / "abs_log.txt")
        _fresh_logger()

        setup_logging(log_file=abs_log, console_level="INFO")

        assert Path(abs_log).exists()

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        """Parent directories are created automatically for relative paths."""
        monkeypatch.setattr("evi_trace.utils.path_utils.PROJECT_ROOT", tmp_path)
        _fresh_logger()

        setup_logging(log_file="nested/dir/run.log", console_level="INFO")

        assert (tmp_path / "nested" / "dir" / "run.log").exists()


# ---------------------------------------------------------------------------
# Log-level behaviour
# ---------------------------------------------------------------------------

class TestLogLevels:
    def test_debug_messages_go_to_file(self, tmp_path):
        """DEBUG records are written to the file even when console level is INFO."""
        log_path = tmp_path / "debug.log"
        _fresh_logger()

        logger = setup_logging(log_file=str(log_path), console_level="INFO")
        logger.debug("this is a debug message")

        content = log_path.read_text(encoding="utf-8")
        assert "this is a debug message" in content

    def test_console_handler_respects_configured_level(self, tmp_path, capsys):
        """DEBUG messages should NOT appear on stderr when console_level=WARNING."""
        log_path = tmp_path / "console.log"
        _fresh_logger()

        logger = setup_logging(log_file=str(log_path), console_level="WARNING")
        logger.debug("should not appear in console")
        logger.warning("should appear in console")

        captured = capsys.readouterr()
        assert "should not appear in console" not in captured.err
        assert "should appear in console" in captured.err

    def test_invalid_log_level_raises(self, tmp_path):
        _fresh_logger()
        with pytest.raises(ValueError, match="Invalid log level"):
            setup_logging(log_file=str(tmp_path / "x.log"), console_level="BANANA")


# ---------------------------------------------------------------------------
# Idempotency / no duplicate handlers
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_repeated_calls_do_not_duplicate_handlers(self, tmp_path):
        """Calling setup_logging twice should leave exactly 2 handlers (file + stream)."""
        log_path = tmp_path / "dup.log"
        _fresh_logger()

        setup_logging(log_file=str(log_path), console_level="INFO")
        setup_logging(log_file=str(log_path), console_level="INFO")

        lg = logging.getLogger("evi_trace")
        managed = [h for h in lg.handlers if getattr(h, "_evi_trace_managed", False)]
        assert len(managed) == 2  # exactly one file + one stream

    def test_repeated_calls_do_not_duplicate_log_lines(self, tmp_path):
        """A single log message written after two setup_logging calls must appear
        exactly once in the file."""
        log_path = tmp_path / "dup_lines.log"
        _fresh_logger()

        setup_logging(log_file=str(log_path), console_level="INFO", overwrite=False)
        setup_logging(log_file=str(log_path), console_level="INFO", overwrite=False)

        lg = logging.getLogger("evi_trace")
        lg.info("unique sentinel message XYZ")

        content = log_path.read_text(encoding="utf-8")
        assert content.count("unique sentinel message XYZ") == 1

    def test_overwrite_true_truncates_file(self, tmp_path):
        """When overwrite=True (default), a second call to setup_logging starts a
        fresh log file, discarding messages from the first run."""
        log_path = tmp_path / "overwrite.log"
        _fresh_logger()

        logger = setup_logging(log_file=str(log_path), console_level="INFO", overwrite=True)
        logger.info("first run message")

        # Re-initialise with overwrite=True – should truncate the file
        _fresh_logger()
        logger = setup_logging(log_file=str(log_path), console_level="INFO", overwrite=True)
        logger.info("second run message")

        content = log_path.read_text(encoding="utf-8")
        assert "first run message" not in content
        assert "second run message" in content
