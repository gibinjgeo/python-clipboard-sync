"""Central configuration with load/save support."""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

from utils.helpers import generate_device_id, default_device_name

_DEFAULT_DATA_DIR = str(Path.home() / ".python_clipboard_sync")


@dataclass
class Config:
    # Network
    udp_port: int = 1716          # Same port as KDE Connect for potential interop
    tcp_port: int = 1716
    broadcast_interval: float = 10.0   # seconds between UDP identity broadcasts

    # Security / anti-replay
    timestamp_tolerance_sec: int = 30
    nonce_cache_max_size: int = 1000
    pairing_timeout_sec: int = 30

    # Clipboard
    clipboard_poll_interval: float = 0.5   # seconds
    clipboard_suppress_window_sec: float = 2.0  # ignore echo for this long after receiving

    # Paths
    data_dir: str = _DEFAULT_DATA_DIR

    @property
    def config_file(self) -> Path:
        return Path(self.data_dir) / "config.json"

    @property
    def paired_devices_file(self) -> Path:
        return Path(self.data_dir) / "paired_devices.json"

    @property
    def keys_dir(self) -> Path:
        return Path(self.data_dir) / "keys"

    def ensure_dirs(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.keys_dir, exist_ok=True)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        cfg_path = cfg.config_file
        if cfg_path.exists():
            with open(cfg_path) as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        return cfg

    def save(self) -> None:
        self.ensure_dirs()
        with open(self.config_file, "w") as f:
            json.dump(asdict(self), f, indent=2)


@dataclass
class DeviceInfo:
    device_id: str
    device_name: str

    @property
    def _file_path(self) -> Path:
        return Path(_DEFAULT_DATA_DIR) / "device.json"

    @classmethod
    def load_or_create(cls, data_dir: str = _DEFAULT_DATA_DIR) -> "DeviceInfo":
        path = Path(data_dir) / "device.json"
        if path.exists():
            with open(path) as f:
                d = json.load(f)
            return cls(device_id=d["device_id"], device_name=d["device_name"])
        info = cls(
            device_id=generate_device_id(),
            device_name=default_device_name(),
        )
        os.makedirs(data_dir, exist_ok=True)
        info.save(data_dir)
        return info

    def save(self, data_dir: str = _DEFAULT_DATA_DIR) -> None:
        path = Path(data_dir) / "device.json"
        with open(path, "w") as f:
            json.dump({"device_id": self.device_id, "device_name": self.device_name}, f, indent=2)
