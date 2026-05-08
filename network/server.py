"""
TCP server: accepts incoming connections and drives the per-connection protocol.

Each connected peer gets a DeviceSession that handles:
  - Pairing handshake
  - Authenticated packet dispatch (clipboard, ping, pong)
  - Security validation (HMAC, nonce, timestamp)
"""

import asyncio
from typing import Callable, Awaitable, Dict, Optional

from network.protocol import (
    Packet, PacketBuilder,
    PAIR_REQUEST, PAIR_RESPONSE, PING, PONG, CLIPBOARD,
    UNAUTHENTICATED_TYPES,
)
from network.security import SecurityManager
from network.transport import Connection
from network.ping import PingManager
from pairing.pair import PairingManager
from storage.paired_devices import PairedDeviceStore
from utils.logger import get_logger

log = get_logger("network")


# Callback types
OnClipboardReceived = Callable[[str, str], Awaitable[None]]   # (device_id, text)


class DeviceSession:
    """
    Handles all protocol logic for one connected peer.

    Lifecycle:
      1. Connection established (client or server)
      2. If the peer is already paired → session is ready to send/recv
      3. Otherwise wait for pair_request / send pair_request
    """

    def __init__(
        self,
        conn: Connection,
        own_device_id: str,
        own_device_name: str,
        security: SecurityManager,
        store: PairedDeviceStore,
        pairing_manager: PairingManager,
        on_clipboard: OnClipboardReceived,
    ) -> None:
        self._conn = conn
        self._own_id = own_device_id
        self._own_name = own_device_name
        self._security = security
        self._store = store
        self._pairing_mgr = pairing_manager
        self._on_clipboard = on_clipboard

        self._peer_id: Optional[str] = None
        self._peer_name: str = ""
        self._shared_secret: Optional[bytes] = None
        self._ping_mgr: Optional[PingManager] = None
        self._authenticated = False
        self._awaiting_pair_response = False  # True after we send a pair_request

    # ------------------------------------------------------------------
    # Public API used by the app layer
    # ------------------------------------------------------------------

    @property
    def peer_id(self) -> Optional[str]:
        return self._peer_id

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    async def send_clipboard(self, text: str) -> bool:
        if not self._authenticated or self._shared_secret is None:
            return False
        builder = PacketBuilder(self._own_id, self._security)
        pkt = builder.clipboard(text, self._shared_secret)
        return await self._conn.send_packet(pkt)

    async def send_ping(self) -> Optional[str]:
        if self._ping_mgr is None:
            return None
        return await self._ping_mgr.send_ping()

    async def send_pair_request(self, peer_name: str = "") -> None:
        pkt = self._pairing_mgr.build_pair_request()
        await self._conn.send_packet(pkt)
        self._peer_name = peer_name
        self._awaiting_pair_response = True
        log.info(f"Pair request sent to {self._conn.peer_addr}")

    # ------------------------------------------------------------------
    # Main receive loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Read packets in a loop until connection closes."""
        log.info(f"Session started: {self._conn.peer_addr}")

        # Check if this peer was already paired (we may receive the first message)
        # We don't know the peer_id yet until first packet arrives.

        while True:
            pkt = await self._conn.receive_packet()
            if pkt is None:
                log.info(f"Session ended: {self._conn.peer_addr}")
                break
            await self._dispatch(pkt)

        await self._conn.close()

    # ------------------------------------------------------------------
    # Packet dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, pkt: Packet) -> None:
        ptype = pkt.packet_type

        if ptype in UNAUTHENTICATED_TYPES:
            await self._handle_unauthenticated(pkt)
            return

        # All other packets require authentication
        if not self._authenticate_packet(pkt):
            return

        if ptype == PING:
            await self._handle_ping(pkt)
        elif ptype == PONG:
            self._handle_pong(pkt)
        elif ptype == CLIPBOARD:
            await self._handle_clipboard(pkt)
        else:
            log.debug(f"Unknown packet type: {ptype}")

    # ------------------------------------------------------------------
    # Unauthenticated handlers
    # ------------------------------------------------------------------

    async def _handle_unauthenticated(self, pkt: Packet) -> None:
        ptype = pkt.packet_type

        if ptype == PAIR_REQUEST:
            self._peer_id = pkt.device_id
            self._peer_name = pkt.payload.get("device_name", "Unknown") if isinstance(pkt.payload, dict) else "Unknown"
            secret = await self._pairing_mgr.handle_pair_request(
                pkt, self._conn.send_packet
            )
            if secret is not None:
                self._activate_session(pkt.device_id, secret)

        elif ptype == PAIR_RESPONSE:
            if not self._awaiting_pair_response:
                log.warning("Received pair_response but no pairing was in progress")
                return
            self._awaiting_pair_response = False
            # peer_id comes from the response packet itself
            self._peer_id = pkt.device_id
            secret = await self._pairing_mgr.handle_pair_response(pkt, self._peer_name)
            if secret is not None:
                self._activate_session(pkt.device_id, secret)

    # ------------------------------------------------------------------
    # Security validation
    # ------------------------------------------------------------------

    def _authenticate_packet(self, pkt: Packet) -> bool:
        peer_id = pkt.device_id

        # Look up shared secret from store (device must be paired)
        record = self._store.get(peer_id)
        if record is None:
            log.warning(f"Received packet from unpaired device {peer_id[:8]}… — dropped")
            return False

        secret = SecurityManager.hex_to_secret(record.shared_secret_hex)

        if not self._security.is_timestamp_fresh(pkt.timestamp):
            log.warning(f"Stale timestamp from {peer_id[:8]}…")
            return False

        if not self._security.is_nonce_fresh(pkt.nonce):
            log.warning(f"Duplicate nonce from {peer_id[:8]}…")
            return False

        if not pkt.verify(secret):
            log.warning(f"HMAC verification failed for packet from {peer_id[:8]}…")
            return False

        # Update local state if not yet activated
        if not self._authenticated:
            self._activate_session(peer_id, secret)

        self._store.touch(peer_id)
        return True

    # ------------------------------------------------------------------
    # Authenticated packet handlers
    # ------------------------------------------------------------------

    async def _handle_ping(self, pkt: Packet) -> None:
        if self._ping_mgr:
            await self._ping_mgr.handle_ping(pkt)

    def _handle_pong(self, pkt: Packet) -> None:
        if self._ping_mgr:
            self._ping_mgr.handle_pong(pkt)

    async def _handle_clipboard(self, pkt: Packet) -> None:
        if self._shared_secret is None:
            return
        inner = pkt.decrypt_payload(self._shared_secret)
        if inner is None:
            log.error("Failed to decrypt clipboard payload")
            return
        content = inner.get("content", "")
        content_type = inner.get("content_type", "text/plain")
        if content_type != "text/plain":
            log.debug(f"Ignoring non-text clipboard ({content_type})")
            return
        peer_id = pkt.device_id
        log.info(f"Clipboard received from {self._peer_name or peer_id[:8]}… ({len(content)} chars)")
        await self._on_clipboard(peer_id, content)

    # ------------------------------------------------------------------
    # Session activation
    # ------------------------------------------------------------------

    def _activate_session(self, peer_id: str, secret: bytes) -> None:
        self._peer_id = peer_id
        self._shared_secret = secret
        self._authenticated = True
        self._ping_mgr = PingManager(
            self._own_id, self._security, self._conn.send_packet, secret
        )
        log.info(f"Session authenticated with {self._peer_name or peer_id[:8]}…")


# ---------------------------------------------------------------------------
# TCP Server
# ---------------------------------------------------------------------------

class ClipboardSyncServer:
    """Listens for incoming TCP connections and spawns DeviceSessions."""

    def __init__(
        self,
        host: str,
        port: int,
        own_device_id: str,
        own_device_name: str,
        security: SecurityManager,
        store: PairedDeviceStore,
        pairing_manager: PairingManager,
        on_clipboard: OnClipboardReceived,
    ) -> None:
        self._host = host
        self._port = port
        self._own_id = own_device_id
        self._own_name = own_device_name
        self._security = security
        self._store = store
        self._pairing_mgr = pairing_manager
        self._on_clipboard = on_clipboard
        self._sessions: Dict[str, DeviceSession] = {}  # peer_id → session
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        if self._server is not None:
            return  # already listening
        try:
            self._server = await asyncio.start_server(
                self._handle_connection, self._host, self._port,
                reuse_address=True,
            )
        except OSError as exc:
            raise OSError(
                f"Cannot bind to port {self._port} — is another instance already running? ({exc})"
            ) from exc
        addrs = [str(s.getsockname()) for s in self._server.sockets]
        log.info(f"TCP server listening on {addrs}")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn = Connection(reader, writer)
        session = DeviceSession(
            conn, self._own_id, self._own_name,
            self._security, self._store, self._pairing_mgr, self._on_clipboard,
        )
        asyncio.create_task(session.run())

    # ------------------------------------------------------------------
    # Broadcast to all authenticated sessions
    # ------------------------------------------------------------------

    async def broadcast_clipboard(self, text: str, source_device_id: Optional[str] = None) -> None:
        """Send clipboard to all authenticated peers (except the source)."""
        # Collect sessions from the store
        for record in self._store.all():
            if record.device_id == source_device_id:
                continue
            # We find sessions by iterating active ones
            # In practice, sessions are tracked per-connection not per-device-id
            # The app layer handles routing; this is a placeholder for direct session map
        # NOTE: Full session-tracking is done in App (main.py).
        pass

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            log.info("TCP server stopped")
