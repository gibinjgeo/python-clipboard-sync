"""Structured logging with tagged prefixes, file output, and GUI queue."""

import logging
import queue
import sys
from pathlib import Path

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
    "gui":       "[GUI]",
}

_FMT = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")

_root_logger = logging.getLogger("clipboardsync")
_root_logger.setLevel(logging.DEBUG)

_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setFormatter(_FMT)
_root_logger.addHandler(_stdout_handler)

# Queue that the GUI drains to display live log lines.
_gui_queue: queue.Queue = queue.Queue()


class _GuiQueueHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _gui_queue.put_nowait(self.format(record))
        except Exception:
            pass


def get_gui_log_queue() -> queue.Queue:
    return _gui_queue


def enable_gui_logging() -> None:
    """Attach the GUI queue handler (call once from the GUI thread)."""
    h = _GuiQueueHandler()
    h.setFormatter(_FMT)
    _root_logger.addHandler(h)


def setup_file_logging(log_file: Path) -> None:
    """Append log output to a rotating file (call after config is loaded)."""
    from logging.handlers import RotatingFileHandler
    log_file.parent.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    h.setFormatter(_FMT)
    _root_logger.addHandler(h)


class TaggedLogger:
    def __init__(self, tag: str) -> None:
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
