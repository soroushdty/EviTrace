"""
tests/test_logging_utils.py
============================
Tests for :mod:`pdf_extractor.utils.logging_utils`.

Run with::

    pytest tests/test_logging_utils.py -v
"""

import logging

import pytest

from pdf_extractor.utils.logging_utils import setup_logger, get_logger


def test_setup_logger_returns_logger_object():
    logger = setup_logger('test_logger')
    assert isinstance(logger, logging.Logger)


def test_get_logger_returns_same_instance():
    l1 = get_logger('same')
    l2 = get_logger('same')
    assert l1 is l2


def test_logger_propagation_disabled():
    logger = setup_logger('no_propagate')
    assert logger.propagate is False


def test_logger_level_settable():
    logger = setup_logger('level_test', level=logging.DEBUG)
    assert logger.level == logging.DEBUG
