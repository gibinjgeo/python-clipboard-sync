"""Structured logging with tagged prefixes."""

import logging
import sys

_TAG_MAP = {
    "discovery": "[DISCOVERY]",
    "pairing":   "[PAIRING]",
    "security":  "[SECURITY]",
    "clipboard": "[CLIPBOARD]",
    "ping":      "[PING]",
    "network":   "[NETWORK]",
    "error":     "[ERROR]",
    "app":       "[APP]",
    "storage":   "[STORAGE]",
}

_root_handler_installed = False


def _ensure_root_handler() -> None:
    global _root_handler_installed
    if _root_handler_installed:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
    )
    root = logging.getLogger("clipboardsync")
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    _root_handler_installed = True


class TaggedLogger:
    """Wraps a standard Logger and prepends a category tag to every message."""

    def __init__(self, tag: str) -> None:
        _ensure_root_handler()
        self._prefix = _TAG_MAP.get(tag.lower(), f"[{tag.upper()}]")
        self._log = logging.getLogger(f"clipboardsync.{tag}")

    def debug(self, msg: str) -> None:
        self._log.debug(f"{self._prefix} {msg}")

    def info(self, msg: str) -> None:
        self._log.info(f"{self._prefix} {msg}")

    def warning(self, msg: str) -> None:
        self._log.warning(f"{self._prefix} {msg}")

    def error(self, msg: str) -> None:
        self._log.error(f"{self._prefix} {msg}")


def get_logger(tag: str) -> TaggedLogger:
    return TaggedLogger(tag)
