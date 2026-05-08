"""Utility helpers: device ID generation, network info."""

import hashlib
import platform
import socket
import uuid
from typing import Optional


def generate_device_id() -> str:
    """Stable device ID derived from machine hostname + MAC address."""
    hostname = platform.node()
    mac = uuid.getnode()
    raw = f"{hostname}-{mac}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def default_device_name() -> str:
    return platform.node() or "ClipboardSyncDevice"


def get_local_ip() -> str:
    """Best-effort local LAN IP (not loopback)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def get_broadcast_address(local_ip: str) -> str:
    """Compute /24 broadcast from a local IP."""
    parts = local_ip.rsplit(".", 1)
    return f"{parts[0]}.255"


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_windows() -> bool:
    return platform.system() == "Windows"
