"""Top-level App coordinator — wires all subsystems together."""

import asyncio
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

        self._sessions: Dict[str, DeviceSession] = {}
        self._connected_ips: set = set()
        self._stop: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._stop = asyncio.Event()
        log.info(f"Starting ClipboardSync — device: {self._info.device_name} ({self._info.device_id[:8]}…)")
        log.info(f"TCP port: {self._cfg.tcp_port}  UDP port: {self._cfg.udp_port}")

        await self._server.start()

        asyncio.create_task(self._discovery.run_broadcaster())
        asyncio.create_task(self._discovery.run_listener())
        asyncio.create_task(self._clipboard_monitor.run())

        log.info("Ready. Waiting for devices…")
        await self._stop.wait()
        log.info("Shutting down")

    def request_stop(self) -> None:
        """Signal the run() coroutine to exit. Safe to call from any thread."""
        if self._stop:
            self._stop.set()

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
            return

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
        await self._clipboard_monitor.apply_received(text)

    # ------------------------------------------------------------------
    # CLI / GUI commands
    # ------------------------------------------------------------------

    async def cmd_pair(self, host: str) -> None:
        log.info(f"Initiating pairing with {host}:{self._cfg.tcp_port}…")
        await self._server.start()   # no-op if already running
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
        await asyncio.sleep(self._cfg.pairing_timeout_sec)

    async def cmd_ping(self, host: str) -> None:
        log.info(f"Pinging {host}:{self._cfg.tcp_port}…")
        await self._server.start()   # no-op if already running
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
        await asyncio.sleep(0.5)
        nonce = await session.send_ping()
        if nonce:
            await asyncio.sleep(3.0)
