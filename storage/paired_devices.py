"""Persistent storage for paired device records."""

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from utils.logger import get_logger

log = get_logger("storage")


@dataclass
class PairedDevice:
    device_id: str
    device_name: str
    shared_secret_hex: str      # hex-encoded 32-byte ECDH-derived secret
    peer_public_key_b64: str    # base64 peer X25519 public key (for verification)
    last_seen: str              # ISO-8601 UTC timestamp


class PairedDeviceStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._devices: Dict[str, PairedDevice] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def all(self) -> List[PairedDevice]:
        return list(self._devices.values())

    def get(self, device_id: str) -> Optional[PairedDevice]:
        return self._devices.get(device_id)

    def is_paired(self, device_id: str) -> bool:
        return device_id in self._devices

    def add_or_update(self, device: PairedDevice) -> None:
        self._devices[device.device_id] = device
        self._save()
        log.info(f"Paired device saved: {device.device_name} ({device.device_id[:8]}…)")

    def remove(self, device_id: str) -> None:
        if device_id in self._devices:
            name = self._devices[device_id].device_name
            del self._devices[device_id]
            self._save()
            log.info(f"Removed paired device: {name}")

    def touch(self, device_id: str) -> None:
        """Update last_seen timestamp without changing other fields."""
        if device_id in self._devices:
            self._devices[device_id].last_seen = _utcnow()
            self._save()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path) as f:
                records = json.load(f)
            for r in records:
                d = PairedDevice(**r)
                self._devices[d.device_id] = d
            log.info(f"Loaded {len(self._devices)} paired device(s)")
        except Exception as exc:
            log.error(f"Failed to load paired devices: {exc}")

    def _save(self) -> None:
        os.makedirs(self._path.parent, exist_ok=True)
        records = [asdict(d) for d in self._devices.values()]
        with open(self._path, "w") as f:
            json.dump(records, f, indent=2)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
