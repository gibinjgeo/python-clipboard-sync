"""
Wayland clipboard backend using wl-clipboard (wl-copy / wl-paste).

Requirements:
  sudo apt install wl-clipboard

wl-paste reads the current clipboard content.
wl-copy sets the clipboard content.

Polling approach: run wl-paste every poll_interval seconds and compare hashes.
We use asyncio.create_subprocess_exec to avoid blocking the event loop.
"""

import asyncio
import hashlib
import subprocess
import shutil
from typing import Optional

from utils.logger import get_logger

log = get_logger("clipboard")


def _check_wl_clipboard() -> bool:
    return shutil.which("wl-paste") is not None and shutil.which("wl-copy") is not None


class WaylandClipboard:
    """Async clipboard backend for Linux Wayland via wl-clipboard."""

    def __init__(self) -> None:
        if not _check_wl_clipboard():
            log.error(
                "wl-clipboard not found. Install it: sudo apt install wl-clipboard"
            )
            raise RuntimeError("wl-clipboard not installed")
        log.info("Wayland clipboard backend initialised (wl-clipboard)")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_text(self) -> Optional[str]:
        """Return current clipboard text, or None on error / empty clipboard."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "wl-paste", "--no-newline",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                # wl-paste exits non-zero when clipboard is empty or has non-text
                err = stderr.decode(errors="replace").strip()
                if "Nothing is copied" in err or "no selection" in err.lower():
                    return None
                log.debug(f"wl-paste returned {proc.returncode}: {err}")
                return None
            return stdout.decode("utf-8", errors="replace")
        except FileNotFoundError:
            log.error("wl-paste not found")
            return None
        except Exception as exc:
            log.error(f"wl-paste error: {exc}")
            return None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def set_text(self, text: str) -> bool:
        """Set clipboard content. Returns True on success."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "wl-copy",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate(input=text.encode("utf-8"))
            if proc.returncode != 0:
                err = stderr.decode(errors="replace").strip()
                log.error(f"wl-copy failed (rc={proc.returncode}): {err}")
                return False
            return True
        except FileNotFoundError:
            log.error("wl-copy not found")
            return False
        except Exception as exc:
            log.error(f"wl-copy error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Hash helper
    # ------------------------------------------------------------------

    @staticmethod
    def hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
