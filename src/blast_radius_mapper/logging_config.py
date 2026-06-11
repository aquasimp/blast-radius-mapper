"""
Structured logging for the Blast Radius Mapper.

All log messages use a consistent format with module context.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(verbose: bool = False) -> logging.Logger:
    """
    Configure and return the root logger for the blast radius mapper.

    Args:
        verbose: If True, set level to DEBUG.  Otherwise INFO.

    Returns:
        The configured root logger.
    """
    logger = logging.getLogger("blast_radius_mapper")

    if logger.handlers:
        # Already configured — avoid duplicate handlers on repeated calls.
        return logger

    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    fmt = logging.Formatter(
        "[%(levelname)s] %(name)s.%(funcName)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the blast_radius_mapper namespace."""
    return logging.getLogger(f"blast_radius_mapper.{name}")
