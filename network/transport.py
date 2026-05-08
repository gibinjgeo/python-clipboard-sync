"""
Low-level asyncio TCP connection wrapper.

Each Connection wraps a (StreamReader, StreamWriter) pair.
Packets are sent as newline-terminated UTF-8 JSON lines.
Incoming lines are parsed into Packet objects and dispatched
via an asyncio.Queue so that the handler coroutine can process them
without blocking the reader loop.
"""

import asyncio
import json
from typing import Callable, Coroutine, Optional

from network.protocol import Packet
from utils.logger import get_logger

log = get_logger("network")

_READ_LIMIT = 256 * 1024  # 256 KB max per packet


class Connection:
    """Represents one live TCP connection to a peer device."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer_addr: str = "",
    ) -> None:
        self._reader = reader
        self._writer = writer
        self.peer_addr = peer_addr or writer.get_extra_info("peername", ("?", 0))[0]
        self._closed = False

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send_packet(self, packet: Packet) -> bool:
        """Serialize and send a packet. Returns False if send failed."""
        if self._closed:
            return False
        try:
            self._writer.write(packet.to_wire())
            await self._writer.drain()
            log.debug(f"→ {self.peer_addr} [{packet.packet_type}]")
            return True
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            log.error(f"Send failed to {self.peer_addr}: {exc}")
            await self.close()
            return False

    # ------------------------------------------------------------------
    # Receive
    # ------------------------------------------------------------------

    async def receive_packet(self) -> Optional[Packet]:
        """Read one newline-delimited JSON packet. Returns None on EOF/error."""
        try:
            line = await self._reader.readline()
            if not line:
                log.debug(f"EOF from {self.peer_addr}")
                return None
            if len(line) > _READ_LIMIT:
                log.error(f"Oversized packet from {self.peer_addr} ({len(line)} bytes) — dropping")
                return None
            text = line.decode("utf-8").strip()
            packet = Packet.from_json(text)
            log.debug(f"← {self.peer_addr} [{packet.packet_type}]")
            return packet
        except (json.JSONDecodeError, KeyError) as exc:
            log.error(f"Malformed packet from {self.peer_addr}: {exc}")
            return None
        except (ConnectionResetError, OSError) as exc:
            log.error(f"Connection error from {self.peer_addr}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except OSError:
            pass
        log.debug(f"Connection closed: {self.peer_addr}")

    @property
    def is_closed(self) -> bool:
        return self._closed
