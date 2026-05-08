"""
Abstract clipboard interface, platform factory, and ClipboardMonitor.

ClipboardMonitor polls the clipboard every poll_interval seconds.
On a real change it calls on_change(text) — which the app uses to
broadcast the new content to paired devices.

Loop prevention:
  When we RECEIVE clipboard content from the network, we:
    1. Set the local clipboard (set_text)
    2. Record the content hash in _received_hashes with current timestamp
  When the monitor detects a change:
    1. Compute the new hash
    2. If hash is in _received_hashes AND the entry is recent (< suppress_window_sec), skip
    3. Otherwise fire on_change — this prevents echoing received content back to the sender
"""

import asyncio
import hashlib
import sys
import time
from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Dict, Optional

from utils.logger import get_logger

log = get_logger("clipboard")


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class ClipboardBackend(ABC):
    @abstractmethod
    async def get_text(self) -> Optional[str]:
        ...

    @abstractmethod
    async def set_text(self, text: str) -> bool:
        ...

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_clipboard_backend() -> ClipboardBackend:
    """Return the correct backend for the current platform."""
    if sys.platform == "win32":
        from clipboard.windows import WindowsClipboard
        return WindowsClipboard()  # type: ignore[return-value]
    else:
        # Assume Wayland Linux; fall back to pyperclip if wl-clipboard missing
        try:
            from clipboard.linux_wayland import WaylandClipboard
            return WaylandClipboard()  # type: ignore[return-value]
        except RuntimeError:
            log.warning("Falling back to pyperclip (wl-clipboard not available)")
            return _PyperclipFallback()


class _PyperclipFallback(ClipboardBackend):
    """Last-resort fallback using pyperclip (X11 / non-Wayland)."""

    def __init__(self) -> None:
        try:
            import pyperclip  # type: ignore
            self._pc = pyperclip
            log.info("Clipboard backend: pyperclip (fallback)")
        except ImportError:
            raise RuntimeError("pyperclip not installed: pip install pyperclip")

    async def get_text(self) -> Optional[str]:
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._pc.paste)
        except Exception as exc:
            log.error(f"pyperclip paste error: {exc}")
            return None

    async def set_text(self, text: str) -> bool:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._pc.copy, text)
            return True
        except Exception as exc:
            log.error(f"pyperclip copy error: {exc}")
            return False


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class ClipboardMonitor:
    """
    Polls clipboard on a fixed interval and calls on_change when content changes.
    Tracks received hashes to prevent loop-back echoing.
    """

    def __init__(
        self,
        backend: ClipboardBackend,
        poll_interval: float,
        suppress_window_sec: float,
        on_change: Callable[[str], Awaitable[None]],
    ) -> None:
        self._backend = backend
        self._poll_interval = poll_interval
        self._suppress_window = suppress_window_sec
        self._on_change = on_change

        self._last_hash: Optional[str] = None
        # hash → timestamp of when we received it from network
        self._received_hashes: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Called by the network layer when clipboard data arrives from a peer
    # ------------------------------------------------------------------

    async def apply_received(self, text: str) -> None:
        """Set local clipboard from network content and suppress echo."""
        h = self._backend.hash_text(text)
        self._received_hashes[h] = time.monotonic()
        self._last_hash = h
        success = await self._backend.set_text(text)
        if success:
            log.info(f"Clipboard received from network ({len(text)} chars)")
        else:
            log.error("Failed to set clipboard from network data")
        self._evict_old_received()

    # ------------------------------------------------------------------
    # Main polling loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        log.info("Clipboard monitor started")
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                text = await self._backend.get_text()
                if text is None:
                    continue
                h = self._backend.hash_text(text)
                if h == self._last_hash:
                    continue  # no change

                # Content changed — check if it was recently received from network
                if self._is_received_recently(h):
                    log.debug("Clipboard change suppressed (recently received from network)")
                    self._last_hash = h
                    continue

                self._last_hash = h
                log.info(f"Clipboard changed locally ({len(text)} chars) — broadcasting")
                await self._on_change(text)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error(f"Clipboard monitor error: {exc}")

    # ------------------------------------------------------------------
    # Loop-prevention helpers
    # ------------------------------------------------------------------

    def _is_received_recently(self, content_hash: str) -> bool:
        received_at = self._received_hashes.get(content_hash)
        if received_at is None:
            return False
        return (time.monotonic() - received_at) < self._suppress_window

    def _evict_old_received(self) -> None:
        now = time.monotonic()
        expired = [
            h for h, t in self._received_hashes.items()
            if (now - t) > self._suppress_window * 4
        ]
        for h in expired:
            del self._received_hashes[h]
