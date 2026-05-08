"""
python_clipboard_sync — entry point.

Usage:
  python main.py                     # Start daemon (discover + sync clipboard)
  python main.py --pair <ip>         # Initiate pairing with a specific device
  python main.py --ping <ip>         # Send a ping to a specific device
  python main.py --list-paired       # List all paired devices
  python main.py --unpair <device_id># Remove a paired device
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Dict, Optional

from config import Config, DeviceInfo
from clipboard.backend import create_clipboard_backend, ClipboardMonitor
from network.discovery import Discovery
from network.security import SecurityManager
from network.server import ClipboardSyncServer, DeviceSession
from network.client import connect_to_peer
from pairing.pair import PairingManager
from storage.paired_devices import PairedDeviceStore
from utils.logger import get_logger

log = get_logger("app")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class App:
    """Top-level coordinator: wires all subsystems together."""

    def __init__(self, config: Config, device_info: DeviceInfo) -> None:
        self._cfg = config
        self._info = device_info

        config.ensure_dirs()

        self._store = PairedDeviceStore(config.paired_devices_file)
        self._security = SecurityManager(keys_dir=config.keys_dir)

        self._pairing_mgr = PairingManager(
            own_device_id=device_info.device_id,
            own_device_name=device_info.device_name,
            security=self._security,
            store=self._store,
            on_pair_complete=self._on_pair_complete,
        )

        clipboard_backend = create_clipboard_backend()
        self._clipboard_monitor = ClipboardMonitor(
            backend=clipboard_backend,
            poll_interval=config.clipboard_poll_interval,
            suppress_window_sec=config.clipboard_suppress_window_sec,
            on_change=self._on_local_clipboard_change,
        )

        self._server = ClipboardSyncServer(
            host="0.0.0.0",
            port=config.tcp_port,
            own_device_id=device_info.device_id,
            own_device_name=device_info.device_name,
            security=self._security,
            store=self._store,
            pairing_manager=self._pairing_mgr,
            on_clipboard=self._on_remote_clipboard,
        )

        self._discovery = Discovery(
            device_id=device_info.device_id,
            device_name=device_info.device_name,
            tcp_port=config.tcp_port,
            udp_port=config.udp_port,
            public_key_b64=self._security.public_key_b64,
            broadcast_interval=config.broadcast_interval,
            on_device_found=self._on_device_discovered,
            own_device_id=device_info.device_id,
        )

        # Active outbound sessions keyed by peer device_id
        self._sessions: Dict[str, DeviceSession] = {}
        # Track IPs we've already connected to (avoid duplicate connections)
        self._connected_ips: set = set()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def run(self) -> None:
        log.info(f"Starting ClipboardSync — device: {self._info.device_name} ({self._info.device_id[:8]}…)")
        log.info(f"TCP port: {self._cfg.tcp_port}  UDP port: {self._cfg.udp_port}")

        await self._server.start()

        asyncio.create_task(self._discovery.run_broadcaster())
        asyncio.create_task(self._discovery.run_listener())
        asyncio.create_task(self._clipboard_monitor.run())

        log.info("Ready. Waiting for devices…")
        await asyncio.Event().wait()  # run forever

    # ------------------------------------------------------------------
    # Discovery callback
    # ------------------------------------------------------------------

    async def _on_device_discovered(
        self,
        device_id: str,
        device_name: str,
        ip: str,
        tcp_port: int,
        public_key_b64: str,
    ) -> None:
        if ip in self._connected_ips:
            return

        is_paired = self._store.is_paired(device_id)
        log.info(
            f"Device seen: {device_name} @ {ip}:{tcp_port} "
            f"[{'paired' if is_paired else 'unpaired'}]"
        )

        if not is_paired:
            # Do not auto-connect to unpaired devices.
            # The user must run --pair <ip> explicitly.
            return

        # Auto-reconnect to previously paired devices
        self._connected_ips.add(ip)
        session = await connect_to_peer(
            host=ip,
            port=tcp_port,
            own_device_id=self._info.device_id,
            own_device_name=self._info.device_name,
            security=self._security,
            store=self._store,
            pairing_manager=self._pairing_mgr,
            on_clipboard=self._on_remote_clipboard,
            initiate_pairing=False,
        )
        if session:
            self._sessions[device_id] = session

    # ------------------------------------------------------------------
    # Pairing callback
    # ------------------------------------------------------------------

    async def _on_pair_complete(self, peer_id: str, shared_secret: bytes) -> None:
        log.info(f"Pairing complete with {peer_id[:8]}…")

    # ------------------------------------------------------------------
    # Clipboard callbacks
    # ------------------------------------------------------------------

    async def _on_local_clipboard_change(self, text: str) -> None:
        """Broadcast locally-changed clipboard to all authenticated sessions."""
        if not self._sessions:
            log.debug("No active sessions — nothing to broadcast")
            return
        log.info(f"Broadcasting clipboard to {len(self._sessions)} device(s)")
        dead = []
        for peer_id, session in self._sessions.items():
            if not session.is_authenticated:
                continue
            ok = await session.send_clipboard(text)
            if not ok:
                dead.append(peer_id)
        for peer_id in dead:
            del self._sessions[peer_id]

    async def _on_remote_clipboard(self, device_id: str, text: str) -> None:
        """Apply clipboard received from a remote device."""
        await self._clipboard_monitor.apply_received(text)

    # ------------------------------------------------------------------
    # CLI commands
    # ------------------------------------------------------------------

    async def cmd_pair(self, host: str) -> None:
        """Initiate pairing with a device at a given IP."""
        log.info(f"Initiating pairing with {host}:{self._cfg.tcp_port}…")
        await self._server.start()
        session = await connect_to_peer(
            host=host,
            port=self._cfg.tcp_port,
            own_device_id=self._info.device_id,
            own_device_name=self._info.device_name,
            security=self._security,
            store=self._store,
            pairing_manager=self._pairing_mgr,
            on_clipboard=self._on_remote_clipboard,
            initiate_pairing=True,
        )
        if session is None:
            log.error("Could not connect")
            return
        # Wait a moment for the response
        await asyncio.sleep(self._cfg.pairing_timeout_sec)

    async def cmd_ping(self, host: str) -> None:
        """Ping a known device and print latency."""
        log.info(f"Pinging {host}:{self._cfg.tcp_port}…")
        await self._server.start()
        session = await connect_to_peer(
            host=host,
            port=self._cfg.tcp_port,
            own_device_id=self._info.device_id,
            own_device_name=self._info.device_name,
            security=self._security,
            store=self._store,
            pairing_manager=self._pairing_mgr,
            on_clipboard=self._on_remote_clipboard,
            initiate_pairing=False,
        )
        if session is None:
            return
        # Brief wait for session authentication from server side
        await asyncio.sleep(0.5)
        nonce = await session.send_ping()
        if nonce:
            # Wait for pong
            await asyncio.sleep(3.0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="python_clipboard_sync — lightweight KDE Connect-style clipboard sync"
    )
    p.add_argument("--pair", metavar="IP", help="Initiate pairing with device at IP")
    p.add_argument("--ping", metavar="IP", help="Ping a paired device at IP")
    p.add_argument("--list-paired", action="store_true", help="List paired devices")
    p.add_argument("--unpair", metavar="DEVICE_ID", help="Remove a paired device")
    p.add_argument("--set-name", metavar="NAME", help="Change this device's display name")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = Config.load()
    info = DeviceInfo.load_or_create(cfg.data_dir)
    app = App(cfg, info)

    if args.set_name:
        info.device_name = args.set_name
        info.save(cfg.data_dir)
        print(f"Device name set to: {info.device_name}")
        return

    if args.list_paired:
        store = PairedDeviceStore(cfg.paired_devices_file)
        devices = store.all()
        if not devices:
            print("No paired devices.")
        for d in devices:
            print(f"  {d.device_name:30s}  id={d.device_id}  last_seen={d.last_seen}")
        return

    if args.unpair:
        store = PairedDeviceStore(cfg.paired_devices_file)
        store.remove(args.unpair)
        return

    if args.pair:
        asyncio.run(app.cmd_pair(args.pair))
        return

    if args.ping:
        asyncio.run(app.cmd_ping(args.ping))
        return

    # Default: run daemon
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
