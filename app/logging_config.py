"""Structured logging setup (stdlib ``logging`` -- never ``print``)."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO") -> None:
    """Install a single stderr handler on the root logger. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.setLevel(numeric)
    # Replace uvicorn's default handlers so output is consistent.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)

    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(noisy)
        lg.handlers = []
        lg.propagate = True

    # These libraries are extremely chatty at INFO.
    logging.getLogger("multipart").setLevel(logging.WARNING)
    logging.getLogger("python_multipart").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
