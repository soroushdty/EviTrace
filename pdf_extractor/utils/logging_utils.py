"""
evi_trace/utils/logging_utils.py
================
Logging initialisation helpers for the EviTrace parser pipeline.

Provides :func:`setup_logging`, which wires up a rotating/plain file handler
at DEBUG level and an optional console handler at a configurable level.
Calling the function repeatedly (e.g. when the pipeline is re-run in an
interactive session) is safe: duplicate handlers are detected and removed
before the new ones are added.

Usage example::

    from evi_trace.utils.logging_utils import setup_logging
    setup_logging(log_file="log.txt", console_level="INFO")
"""

import logging
import os
from .path_utils import PROJECT_ROOT as _PROJECT_ROOT
from .path_utils import resolve_log_path as _resolve_log_path

_FILE_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(module)s:%(lineno)d | %(message)s"
)
_CONSOLE_FORMAT = "%(levelname)-8s | %(message)s"

# Sentinel logger name used to identify handlers set up by this module so
# that duplicate detection works correctly across repeated calls.
_ROOT_LOGGER_NAME = "evi_trace"


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
