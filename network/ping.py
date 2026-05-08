"""
Ping / pong manager.

Sending a ping:
  - Build a PING packet, record the nonce + send time
  - When a PONG with matching reply_to_nonce arrives, compute latency

Receiving a ping:
  - Immediately send a PONG with reply_to_nonce = ping.nonce
"""

import asyncio
import time
from typing import Callable, Awaitable, Dict, Optional

from network.protocol import Packet, PacketBuilder, PING, PONG
from network.security import SecurityManager
from utils.logger import get_logger

log = get_logger("ping")


class PingManager:
    def __init__(
        self,
        device_id: str,
        security: SecurityManager,
        send_fn: Callable[[Packet], Awaitable[bool]],
        shared_secret: bytes,
    ) -> None:
        self._builder = PacketBuilder(device_id, security)
        self._send = send_fn
        self._secret = shared_secret
        self._pending: Dict[str, float] = {}  # nonce → sent_time

    # ------------------------------------------------------------------
    # Send ping
    # ------------------------------------------------------------------

    async def send_ping(self) -> str:
        """Send a ping, return its nonce so callers can correlate the pong."""
        pkt = self._builder.ping()
        pkt.sign(self._secret)
        self._pending[pkt.nonce] = time.monotonic()
        sent = await self._send(pkt)
        if sent:
            log.info(f"Ping sent (nonce={pkt.nonce[:8]}…)")
        return pkt.nonce

    # ------------------------------------------------------------------
    # Handle incoming ping → send pong
    # ------------------------------------------------------------------

    async def handle_ping(self, packet: Packet) -> None:
        log.info(f"Ping received from {packet.device_id[:8]}… — sending pong")
        pong = self._builder.pong(packet.nonce)
        pong.sign(self._secret)
        await self._send(pong)

    # ------------------------------------------------------------------
    # Handle incoming pong
    # ------------------------------------------------------------------

    def handle_pong(self, packet: Packet) -> Optional[float]:
        """Record pong and return round-trip latency in ms, or None."""
        reply_to = packet.payload.get("reply_to_nonce", "") if isinstance(packet.payload, dict) else ""
        sent_at = self._pending.pop(reply_to, None)
        if sent_at is None:
            log.debug("Received pong with unknown nonce — ignoring")
            return None
        latency_ms = (time.monotonic() - sent_at) * 1000
        log.info(f"Pong received — latency: {latency_ms:.1f} ms")
        return latency_ms
