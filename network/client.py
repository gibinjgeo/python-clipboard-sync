"""
TCP client: connects to a known peer (discovered via UDP) and returns a DeviceSession.
"""

import asyncio
from typing import Callable, Awaitable, Optional

from network.security import SecurityManager
from network.server import DeviceSession, OnClipboardReceived
from network.transport import Connection
from pairing.pair import PairingManager
from storage.paired_devices import PairedDeviceStore
from utils.logger import get_logger

log = get_logger("network")

_CONNECT_TIMEOUT = 10.0  # seconds


async def connect_to_peer(
    host: str,
    port: int,
    own_device_id: str,
    own_device_name: str,
    security: SecurityManager,
    store: PairedDeviceStore,
    pairing_manager: PairingManager,
    on_clipboard: OnClipboardReceived,
    initiate_pairing: bool = False,
) -> Optional[DeviceSession]:
    """
    Open a TCP connection to host:port.

    If initiate_pairing=True we immediately send a pair_request
    (used for first-time connections to unpaired devices).
    Returns a running DeviceSession task, or None on failure.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_CONNECT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.error(f"Connection to {host}:{port} timed out")
        return None
    except OSError as exc:
        log.error(f"Connection to {host}:{port} failed: {exc}")
        return None

    conn = Connection(reader, writer, peer_addr=host)
    session = DeviceSession(
        conn, own_device_id, own_device_name,
        security, store, pairing_manager, on_clipboard,
    )

    if initiate_pairing:
        await session.send_pair_request()

    asyncio.create_task(session.run())
    log.info(f"Connected to {host}:{port}")
    return session
