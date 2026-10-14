"""Logging setup for OrbitDesk."""
from __future__ import annotations

import logging

from .config import settings

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root = logging.getLogger("orbitdesk")
        root.setLevel(settings.log_level.upper())
        if not root.handlers:
            root.addHandler(handler)
        _CONFIGURED = True
    return logging.getLogger(f"orbitdesk.{name}")
