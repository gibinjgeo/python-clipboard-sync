#!/usr/bin/env python3
"""
ClipShare cloud daemon — syncs clipboard via Supabase instead of local network.

Usage:
    python cloud_client.py --room ABC123
    python cloud_client.py --room ABC123 --device "MyLaptop" --poll 0.5

Credentials (pick one):
    export SUPABASE_URL=https://xxxx.supabase.co
    export SUPABASE_KEY=your-anon-key

    or pass --supabase-url / --supabase-key flags.
"""

import argparse
import asyncio
import os
import platform
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

try:
    from supabase import create_client
except ImportError:
    sys.exit("supabase not installed — run: pip install supabase")

from clipboard.backend import create_clipboard_backend


class CloudSyncDaemon:
    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        room: str,
        device: str,
        poll: float = 1.0,
    ) -> None:
        self._sb        = create_client(supabase_url, supabase_key)
        self._room      = room
        self._device    = device
        self._device_id = str(uuid.uuid4())  # unique per run, prevents echo with web app
        self._poll      = poll
        self._cb        = create_clipboard_backend()

        self._local_hash:  Optional[str] = None
        self._remote_ts:   Optional[str] = None
        self._suppress_until: float = 0.0

    async def run(self) -> None:
        print(f"ClipShare  |  room={self._room}  device={self._device}")
        print("Press Ctrl-C to stop.\n")

        # Snapshot current clipboard so we don't immediately push stale content
        initial = await self._cb.get_text()
        if initial:
            self._local_hash = self._cb.hash_text(initial)

        while True:
            try:
                await self._push_if_changed()
                await self._pull_if_new()
            except Exception as exc:
                print(f"[error] {exc}")
            await asyncio.sleep(self._poll)

    # ── Local → cloud ──────────────────────────────────────────────────────────

    async def _push_if_changed(self) -> None:
        text = await self._cb.get_text()
        if not text:
            return

        h = self._cb.hash_text(text)
        if h == self._local_hash:
            return

        # If we just received this from the cloud, don't echo it back
        if time.monotonic() < self._suppress_until:
            self._local_hash = h
            return

        self._local_hash = h
        self._sb.table("clipboard_rooms").upsert({
            "room_code":  self._room,
            "content":    text,
            "sender":     self._device,
            "device_id":  self._device_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        print(f"[sent]  {_preview(text)}")

    # ── Cloud → local ──────────────────────────────────────────────────────────

    async def _pull_if_new(self) -> None:
        result = (
            self._sb.table("clipboard_rooms")
            .select("*")
            .eq("room_code", self._room)
            .execute()
        )
        if not result.data:
            return

        row     = result.data[0]
        ts      = row.get("updated_at", "")
        sender  = row.get("sender", "")
        content = row.get("content", "")

        if ts == self._remote_ts:
            return  # no change since last poll
        self._remote_ts = ts

        if row.get("device_id") == self._device_id:
            return  # our own push

        h = self._cb.hash_text(content)
        if h == self._local_hash:
            return  # already have this content locally

        self._local_hash     = h
        self._suppress_until = time.monotonic() + 3.0  # suppress echo
        await self._cb.set_text(content)
        print(f"[recv from {sender}]  {_preview(content)}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _preview(text: str, limit: int = 72) -> str:
    return repr(text[:limit] + ("…" if len(text) > limit else ""))


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="ClipShare cloud daemon")
    p.add_argument("--room",         required=True,
                   help="6-char room code (same on all devices)")
    p.add_argument("--device",       default=platform.node(),
                   help="Label shown to other devices (default: hostname)")
    p.add_argument("--poll",         type=float, default=1.0,
                   help="Poll interval in seconds (default: 1)")
    p.add_argument("--supabase-url", default=os.getenv("SUPABASE_URL"),
                   help="Supabase project URL (or set SUPABASE_URL env var)")
    p.add_argument("--supabase-key", default=os.getenv("SUPABASE_KEY"),
                   help="Supabase anon key  (or set SUPABASE_KEY env var)")
    args = p.parse_args()

    if not args.supabase_url or not args.supabase_key:
        sys.exit(
            "Missing Supabase credentials.\n"
            "  export SUPABASE_URL=https://xxxx.supabase.co\n"
            "  export SUPABASE_KEY=your-anon-key\n"
            "or pass --supabase-url and --supabase-key."
        )

    daemon = CloudSyncDaemon(
        supabase_url=args.supabase_url,
        supabase_key=args.supabase_key,
        room=args.room.upper(),
        device=args.device,
        poll=args.poll,
    )
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
