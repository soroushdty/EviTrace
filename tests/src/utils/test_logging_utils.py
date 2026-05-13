"""
tests/pdf_extractor/test_logging_utils.py
==========================================
Tests for :mod:`utils.logging_utils`.
"""

import logging
import pytest
from utils.logging_utils import get_logger, setup_logging


def test_get_logger_returns_logger_object():
    logger = get_logger("test_logger")
    assert isinstance(logger, logging.Logger)


def test_get_logger_returns_same_instance():
    l1 = get_logger("same")
    l2 = get_logger("same")
    assert l1 is l2


def test_setup_logging_returns_logger(tmp_path):
    log_file = str(tmp_path / "test.log")
    logger = setup_logging(log_file=log_file, console_level="WARNING")
    assert isinstance(logger, logging.Logger)


def test_setup_logging_accepts_debug_level(tmp_path):
    log_file = str(tmp_path / "debug.log")
    logger = setup_logging(log_file=log_file, console_level="DEBUG")
    assert isinstance(logger, logging.Logger)
