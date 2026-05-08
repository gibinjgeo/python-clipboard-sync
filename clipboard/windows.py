"""
Windows clipboard backend.

Primary: win32clipboard (pywin32) — supports Unicode CF_UNICODETEXT natively.
Fallback: pyperclip — simpler but may have issues with non-ASCII on some builds.

Clipboard change detection is done by polling GetClipboardSequenceNumber().
This is a fast Windows API call that increments on every clipboard change,
avoiding the need to read and hash clipboard content on every tick.
"""

import asyncio
import hashlib
import sys
from typing import Optional

from utils.logger import get_logger

log = get_logger("clipboard")

if sys.platform == "win32":
    try:
        import win32clipboard  # type: ignore
        import win32con        # type: ignore
        _HAS_WIN32 = True
    except ImportError:
        _HAS_WIN32 = False
        log.warning("pywin32 not found; falling back to pyperclip")

    try:
        import pyperclip  # type: ignore
        _HAS_PYPERCLIP = True
    except ImportError:
        _HAS_PYPERCLIP = False
else:
    _HAS_WIN32 = False
    _HAS_PYPERCLIP = False


class WindowsClipboard:
    """Async-compatible Windows clipboard backend."""

    def __init__(self) -> None:
        if not (_HAS_WIN32 or _HAS_PYPERCLIP):
            raise RuntimeError(
                "No Windows clipboard library found. "
                "Install: pip install pywin32  (or pip install pyperclip)"
            )
        self._last_seq: int = 0
        backend = "win32clipboard" if _HAS_WIN32 else "pyperclip"
        log.info(f"Windows clipboard backend initialised ({backend})")

    # ------------------------------------------------------------------
    # Change detection
    # ------------------------------------------------------------------

    async def has_changed(self) -> bool:
        """Return True if clipboard content changed since last call."""
        loop = asyncio.get_event_loop()
        seq = await loop.run_in_executor(None, self._get_sequence_number)
        if seq != self._last_seq:
            self._last_seq = seq
            return True
        return False

    def _get_sequence_number(self) -> int:
        if _HAS_WIN32:
            try:
                # GetClipboardSequenceNumber is much cheaper than opening clipboard
                import ctypes
                return ctypes.windll.user32.GetClipboardSequenceNumber()
            except Exception:
                return 0
        # Fallback: no efficient change detection; always report changed
        return id(self._get_text_sync())

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_text(self) -> Optional[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_text_sync)

    def _get_text_sync(self) -> Optional[str]:
        if _HAS_WIN32:
            try:
                win32clipboard.OpenClipboard()
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    return text
                return None
            except Exception as exc:
                log.error(f"win32clipboard read error: {exc}")
                return None
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
        elif _HAS_PYPERCLIP:
            try:
                return pyperclip.paste()
            except Exception as exc:
                log.error(f"pyperclip read error: {exc}")
                return None
        return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def set_text(self, text: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._set_text_sync, text)

    def _set_text_sync(self, text: str) -> bool:
        if _HAS_WIN32:
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True
            except Exception as exc:
                log.error(f"win32clipboard write error: {exc}")
                return False
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    pass
        elif _HAS_PYPERCLIP:
            try:
                pyperclip.copy(text)
                return True
            except Exception as exc:
                log.error(f"pyperclip write error: {exc}")
                return False
        return False

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
