"""
UDP-based device discovery.

Reuses KDE Connect's port 1716 concept:
  - Every device broadcasts a JSON identity packet over UDP every N seconds.
  - Every device listens on the same port for packets from others.
  - On discovery, a callback is called with (device_id, device_name, ip, tcp_port, public_key_b64).

Identity UDP payload (NOT a full signed Packet — it's sent in plaintext intentionally):
{
  "packet_type": "identity",
  "device_id":   "...",
  "device_name": "...",
  "tcp_port":    1716,
  "public_key":  "<b64>"
}
"""

import asyncio
import json
import socket
from typing import Callable, Awaitable

from utils.helpers import get_local_ip, get_broadcast_address
from utils.logger import get_logger

log = get_logger("discovery")

_MAX_UDP_PACKET = 8192


class Discovery:
    """Broadcasts our identity and listens for peer identities via UDP."""

    def __init__(
        self,
        device_id: str,
        device_name: str,
        tcp_port: int,
        udp_port: int,
        public_key_b64: str,
        broadcast_interval: float,
        on_device_found: Callable[[str, str, str, int, str], Awaitable[None]],
        own_device_id: str,
    ) -> None:
        self._device_id = device_id
        self._device_name = device_name
        self._tcp_port = tcp_port
        self._udp_port = udp_port
        self._public_key_b64 = public_key_b64
        self._interval = broadcast_interval
        self._on_device_found = on_device_found
        self._own_device_id = own_device_id

    def _identity_payload(self) -> bytes:
        data = {
            "packet_type": "identity",
            "device_id":   self._device_id,
            "device_name": self._device_name,
            "tcp_port":    self._tcp_port,
            "public_key":  self._public_key_b64,
        }
        return (json.dumps(data) + "\n").encode("utf-8")

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    async def run_broadcaster(self) -> None:
        """Periodically send UDP identity broadcasts."""
        local_ip = get_local_ip()
        broadcast_addr = get_broadcast_address(local_ip)
        payload = self._identity_payload()

        log.info(f"Starting UDP broadcaster → {broadcast_addr}:{self._udp_port}")

        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)

        try:
            while True:
                try:
                    await loop.sock_sendto(sock, payload, (broadcast_addr, self._udp_port))
                    log.debug(f"Identity broadcast → {broadcast_addr}:{self._udp_port}")
                except OSError as exc:
                    log.error(f"Broadcast error: {exc}")
                await asyncio.sleep(self._interval)
        finally:
            sock.close()

    # ------------------------------------------------------------------
    # Listening
    # ------------------------------------------------------------------

    async def run_listener(self) -> None:
        """Listen for UDP identity packets from other devices."""
        log.info(f"Listening for UDP broadcasts on port {self._udp_port}")

        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # Windows doesn't have SO_REUSEPORT
        sock.bind(("", self._udp_port))
        sock.setblocking(False)

        try:
            while True:
                try:
                    data, addr = await loop.sock_recvfrom(sock, _MAX_UDP_PACKET)
                except OSError as exc:
                    log.error(f"Listener recv error: {exc}")
                    await asyncio.sleep(1)
                    continue

                sender_ip = addr[0]
                await self._handle_identity(data, sender_ip)
        finally:
            sock.close()

    async def _handle_identity(self, data: bytes, sender_ip: str) -> None:
        try:
            text = data.decode("utf-8").strip()
            d = json.loads(text)
            if d.get("packet_type") != "identity":
                return
            device_id   = d["device_id"]
            device_name = d["device_name"]
            tcp_port    = int(d["tcp_port"])
            public_key  = d.get("public_key", "")

            # Ignore our own broadcasts
            if device_id == self._own_device_id:
                return

            log.info(f"Discovered: {device_name} ({device_id[:8]}…) at {sender_ip}:{tcp_port}")
            await self._on_device_found(device_id, device_name, sender_ip, tcp_port, public_key)

        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.debug(f"Ignored malformed UDP packet from {sender_ip}: {exc}")
