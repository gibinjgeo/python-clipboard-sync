"""
Pairing state machine.

Flow:
  Initiator (A)                        Responder (B)
  ──────────────────────────────────────────────────
  send pair_request (with A.pub_key)
                                       recv pair_request
                                       prompt user: accept? [y/n]
                                       derive shared_secret = ECDH(B.priv, A.pub)
                                       send pair_response (accepted=True, B.pub_key)
                                       store paired device
  recv pair_response
  derive shared_secret = ECDH(A.priv, B.pub)
  store paired device

After pairing both sides display verification_string(shared_secret) for MITM check.
"""

import asyncio
import time
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Callable, Awaitable, Optional

from network.protocol import Packet, PAIR_REQUEST, PAIR_RESPONSE
from network.security import SecurityManager
from storage.paired_devices import PairedDevice, PairedDeviceStore
from utils.logger import get_logger

log = get_logger("pairing")


class PairState(Enum):
    UNPAIRED          = auto()
    REQUESTED_BY_US   = auto()   # we sent pair_request, waiting for response
    REQUESTED_BY_PEER = auto()   # peer sent pair_request, waiting for user input
    PAIRED            = auto()


class PairingSession:
    """Tracks in-flight pairing state for one device connection."""

    TIMEOUT_SEC = 30

    def __init__(self) -> None:
        self.state = PairState.UNPAIRED
        self.started_at: float = 0.0
        self.peer_public_key_b64: str = ""
        self.peer_device_name: str = ""
        self.peer_device_id: str = ""

    def start_outgoing(self, peer_id: str, peer_name: str) -> None:
        self.state = PairState.REQUESTED_BY_US
        self.started_at = time.time()
        self.peer_device_id = peer_id
        self.peer_device_name = peer_name

    def start_incoming(self, peer_id: str, peer_name: str, peer_pub: str) -> None:
        self.state = PairState.REQUESTED_BY_PEER
        self.started_at = time.time()
        self.peer_device_id = peer_id
        self.peer_device_name = peer_name
        self.peer_public_key_b64 = peer_pub

    def is_timed_out(self) -> bool:
        return (time.time() - self.started_at) > self.TIMEOUT_SEC


class PairingManager:
    """
    Manages pairing requests for all active connections.

    The app passes a send_fn coroutine so PairingManager can send packets
    without holding a reference to a Connection object.
    """

    def __init__(
        self,
        own_device_id: str,
        own_device_name: str,
        security: SecurityManager,
        store: PairedDeviceStore,
        on_pair_complete: Optional[Callable[[str, bytes], Awaitable[None]]] = None,
    ) -> None:
        self._own_id = own_device_id
        self._own_name = own_device_name
        self._security = security
        self._store = store
        self._on_pair_complete = on_pair_complete

    # ------------------------------------------------------------------
    # Outgoing pairing
    # ------------------------------------------------------------------

    def build_pair_request(self) -> Packet:
        from network.protocol import PacketBuilder
        builder = PacketBuilder(self._own_id, self._security)
        return builder.pair_request(self._own_name)

    # ------------------------------------------------------------------
    # Incoming pairing
    # ------------------------------------------------------------------

    async def handle_pair_request(
        self,
        packet: Packet,
        send_fn: Callable[[Packet], Awaitable[bool]],
    ) -> Optional[bytes]:
        """
        Handle an incoming pair_request.  Prompts the user interactively.
        Returns the derived shared_secret if accepted, else None.
        """
        peer_id   = packet.device_id
        payload   = packet.payload  # plain dict for pair_request
        peer_name = payload.get("device_name", "Unknown")
        peer_pub  = payload.get("public_key", "")

        log.info(f"Incoming pair request from: {peer_name} ({peer_id[:8]}…)")

        if self._store.is_paired(peer_id):
            log.info("Device already paired — auto-accepting re-pair")
            accepted = True
        else:
            accepted = await _prompt_user(
                f"\n[PAIRING] Accept pairing from '{peer_name}'? [y/N]: "
            )

        from network.protocol import PacketBuilder
        builder = PacketBuilder(self._own_id, self._security)
        response = builder.pair_response(accepted)
        await send_fn(response)

        if not accepted:
            log.info(f"Pairing rejected: {peer_name}")
            return None

        # Derive shared secret from peer's public key
        try:
            shared_secret = self._security.derive_shared_secret(peer_pub)
        except Exception as exc:
            log.error(f"Key exchange failed: {exc}")
            return None

        self._persist_paired(peer_id, peer_name, shared_secret, peer_pub)

        vcode = SecurityManager.verification_string(shared_secret)
        log.info(f"Paired with {peer_name}. Verification code: {vcode}")

        if self._on_pair_complete:
            await self._on_pair_complete(peer_id, shared_secret)

        return shared_secret

    async def handle_pair_response(
        self,
        packet: Packet,
        peer_name: str,
    ) -> Optional[bytes]:
        """
        Handle an incoming pair_response (after we sent pair_request).
        Returns the derived shared_secret if accepted, else None.
        """
        payload  = packet.payload
        accepted = payload.get("accepted", False)
        peer_pub = payload.get("public_key", "")
        peer_id  = packet.device_id

        if not accepted:
            log.info(f"Pairing rejected by {peer_name}")
            return None

        try:
            shared_secret = self._security.derive_shared_secret(peer_pub)
        except Exception as exc:
            log.error(f"Key exchange failed: {exc}")
            return None

        self._persist_paired(peer_id, peer_name, shared_secret, peer_pub)

        vcode = SecurityManager.verification_string(shared_secret)
        log.info(f"Successfully paired with {peer_name}. Verification code: {vcode}")

        if self._on_pair_complete:
            await self._on_pair_complete(peer_id, shared_secret)

        return shared_secret

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist_paired(
        self,
        peer_id: str,
        peer_name: str,
        shared_secret: bytes,
        peer_pub: str,
    ) -> None:
        device = PairedDevice(
            device_id=peer_id,
            device_name=peer_name,
            shared_secret_hex=shared_secret.hex(),
            peer_public_key_b64=peer_pub,
            last_seen=datetime.now(timezone.utc).isoformat(),
        )
        self._store.add_or_update(device)


async def _prompt_user(prompt: str) -> bool:
    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(None, lambda: input(prompt).strip().lower())
    return answer in ("y", "yes")
