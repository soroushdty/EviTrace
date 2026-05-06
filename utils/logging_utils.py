"""
logging_utils.py
================
Centralized logging and instrumentation utilities for the EviTrace parser pipeline.

Provides:
- :func:`get_logger` – Centralized logger getter for all modules.
- :func:`get_root_logger` – Get the root EviTrace logger.
- :func:`setup_logging` – Wire up file and console handlers (idempotent).
- :func:`log_cache_usage` – Log token counts and prompt-cache hits.

Usage examples::

    from utils.logging_utils import get_logger, setup_logging

    # Get a logger for your module (preferred)
    logger = get_logger(__name__)
    logger.info("Message")

    # Initialize logging once at startup
    setup_logging(log_file="log.txt", console_level="INFO")
"""

from __future__ import annotations

import logging
import os
from typing import Any

from utils.path_utils import PROJECT_ROOT as _PROJECT_ROOT
from utils.path_utils import resolve_log_path as _resolve_log_path

_FILE_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(module)s:%(lineno)d | %(message)s"
)
_CONSOLE_FORMAT = "%(levelname)-8s | %(message)s"

# Sentinel logger name used to identify handlers set up by this module so
# that duplicate detection works correctly across repeated calls.
_ROOT_LOGGER_NAME = "evi_trace"


# ============================================================================
# Centralized Logger Access
# ============================================================================

def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given module name.

    All loggers are children of the root ``"evi_trace"`` logger, so they
    inherit its handlers and configuration.

    Parameters
    ----------
    name : str
        The logger name, typically ``__name__`` from a module.

    Returns
    -------
    logging.Logger
        A logger instance for the given name.

    Examples
    --------
    >>> logger = get_logger(__name__)
    >>> logger.info("Module initialized")
    """
    return logging.getLogger(name)


def get_root_logger() -> logging.Logger:
    """Get the root EviTrace logger instance.

    Returns
    -------
    logging.Logger
        The root ``"evi_trace"`` logger.
    """
    return logging.getLogger(_ROOT_LOGGER_NAME)


# ============================================================================
# Cache and API Usage Logging
# ============================================================================

def _get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    """Get an attribute or dictionary key robustly.

    Parameters
    ----------
    obj : Any
        Object to read from (can be a dict or object with attributes).
    name : str
        Attribute name or dictionary key.
    default : Any, optional
        Default value if the attribute/key is not found.

    Returns
    -------
    Any
        The attribute/key value or the default.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def log_cache_usage(response: Any, tag: str, logger: logging.Logger | None = None) -> None:
    """Log token counts and prompt-cache hits from an OpenAI API response.

    Extracts usage statistics and cache hit rates from OpenAI response objects
    (both SDK objects and plain dicts) and logs them at INFO level.

    Parameters
    ----------
    response : Any
        OpenAI API response object (from ``AsyncOpenAI`` or similar).
    tag : str
        A tag/identifier for the log message (e.g., ``"PDF extraction [1/5]"``).
    logger : logging.Logger, optional
        Logger instance to use. If None, uses the root EviTrace logger.

    Examples
    --------
    >>> response = await client.messages.create(...)
    >>> log_cache_usage(response, "chunk 1 extraction")
    """
    if logger is None:
        logger = get_root_logger()

    usage = _get_attr_or_key(response, "usage")
    if not usage:
        logger.info(f"{tag} usage unavailable")
        return

    input_tokens = _get_attr_or_key(usage, "input_tokens", 0) or 0
    output_tokens = _get_attr_or_key(usage, "output_tokens", 0) or 0
    details = _get_attr_or_key(usage, "input_tokens_details")
    cached_tokens = 0
    if details:
        cached_tokens = _get_attr_or_key(details, "cached_tokens", 0) or 0

    hit_rate = (cached_tokens / input_tokens * 100) if input_tokens else 0.0
    logger.info(
        f"{tag} tokens: input={input_tokens}, cached={cached_tokens}, "
        f"cache_hit={hit_rate:.1f}%, output={output_tokens}"
    )


# ============================================================================
# Logging Setup
# ============================================================================


def setup_logging(
    log_file: str = "log.txt",
    console_level: str = "INFO",
    file_level: int = logging.DEBUG,
    overwrite: bool = True,
) -> logging.Logger:
    """Initialise logging for the EviTrace parser pipeline.

    Sets up two handlers on the root ``"evi_trace"`` logger:

    * **File handler** – always at *file_level* (default :data:`logging.DEBUG`),
      writing detailed records to *log_file*.
    * **Stream handler** – at *console_level* so console output stays concise.

    The function is idempotent: if handlers that were previously installed by
    this function are already attached to the logger they are removed before
    the new handlers are added, preventing duplicate log lines across
    repeated cell executions.

    Parameters
    ----------
    log_file:
        Path to the log file.  Relative paths are resolved relative to the
        project root; absolute paths are used as-is.  Parent directories are
        created automatically.  Defaults to ``"log.txt"`` (project root).
    console_level:
        Log level string for the console (stream) handler.  Accepted values
        (case-insensitive): ``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
        ``"ERROR"``, ``"CRITICAL"``.  Defaults to ``"INFO"``.
    file_level:
        Numeric log level for the file handler.  Defaults to
        :data:`logging.DEBUG`.
    overwrite:
        When *True* (default) the log file is opened in write mode so each
        pipeline run starts with a fresh file.  Pass *False* to append.

    Returns
    -------
    logging.Logger
        The configured ``"evi_trace"`` logger instance.

    Raises
    ------
    ValueError
        If *console_level* is not a recognised log-level name.
    """
    # Validate and resolve the console level string
    numeric_console_level = getattr(logging, console_level.upper(), None)
    if not isinstance(numeric_console_level, int):
        raise ValueError(
            f"Invalid log level: {console_level!r}. "
            "Expected one of DEBUG, INFO, WARNING, ERROR, CRITICAL."
        )

    # Resolve the log file path
    resolved_path = _resolve_log_path(log_file)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    # Obtain (or create) the named logger
    logger = logging.getLogger(_ROOT_LOGGER_NAME)

    # Remove any handlers previously added by this function to avoid
    # duplicate output on repeated runs.
    logger.handlers = [
        h for h in logger.handlers
        if not getattr(h, "_evi_trace_managed", False)
    ]

    # Logger itself should pass everything through; individual handlers
    # apply their own level filters.
    logger.setLevel(logging.DEBUG)

    # ------------------------------------------------------------------
    # File handler
    # ------------------------------------------------------------------
    file_mode = "w" if overwrite else "a"
    fh = logging.FileHandler(str(resolved_path), mode=file_mode, encoding="utf-8")
    fh.setLevel(file_level)
    fh.setFormatter(logging.Formatter(_FILE_FORMAT))
    fh._evi_trace_managed = True  # type: ignore[attr-defined]
    logger.addHandler(fh)

    # ------------------------------------------------------------------
    # Console / stream handler
    # ------------------------------------------------------------------
    sh = logging.StreamHandler()
    sh.setLevel(numeric_console_level)
    sh.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    sh._evi_trace_managed = True  # type: ignore[attr-defined]
    logger.addHandler(sh)

    logger.info(
        "Logging initialised | file=%s (level=DEBUG) | console level=%s",
        resolved_path,
        console_level.upper(),
    )

    return logger
