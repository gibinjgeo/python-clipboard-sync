"""
Packet protocol: types, dataclass, builder, and signing helpers.

Packet wire format (newline-terminated JSON):
{
  "packet_type": "...",
  "device_id":   "...",
  "timestamp":   1234567890,
  "nonce":       "...",
  "payload":     { ... } | "<fernet-token-string>",
  "signature":   "<hmac-hex>"
}

For unauthenticated packets (pair_request, pair_response, identity):
  - payload is a plain dict
  - signature is "" (empty)

For authenticated packets (clipboard, ping, pong):
  - payload is a Fernet-encrypted JSON string (for clipboard)
    OR a plain dict (for ping/pong, no sensitive data)
  - signature = HMAC-SHA256(signing_string, shared_secret)

signing_string = "{type}:{device_id}:{timestamp}:{nonce}:{payload_repr}"
where payload_repr is json.dumps(payload, sort_keys=True) or the ciphertext string.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

from network.security import SecurityManager

# ---------------------------------------------------------------------------
# Packet type constants
# ---------------------------------------------------------------------------

PAIR_REQUEST  = "pair_request"
PAIR_RESPONSE = "pair_response"
PING          = "ping"
PONG          = "pong"
CLIPBOARD     = "clipboard"
IDENTITY      = "identity"   # UDP-only discovery beacon

# Packets that travel before a shared secret exists
UNAUTHENTICATED_TYPES = {PAIR_REQUEST, PAIR_RESPONSE, IDENTITY}


# ---------------------------------------------------------------------------
# Packet dataclass
# ---------------------------------------------------------------------------

@dataclass
class Packet:
    packet_type: str
    device_id:   str
    timestamp:   int
    nonce:       str
    payload:     Union[Dict[str, Any], str]  # dict for plain; str for encrypted
    signature:   str = ""

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps({
            "packet_type": self.packet_type,
            "device_id":   self.device_id,
            "timestamp":   self.timestamp,
            "nonce":       self.nonce,
            "payload":     self.payload,
            "signature":   self.signature,
        })

    def to_wire(self) -> bytes:
        """Newline-terminated JSON bytes ready for TCP send."""
        return (self.to_json() + "\n").encode("utf-8")

    @classmethod
    def from_json(cls, data: str) -> "Packet":
        d = json.loads(data)
        return cls(
            packet_type=d["packet_type"],
            device_id=d["device_id"],
            timestamp=int(d["timestamp"]),
            nonce=d["nonce"],
            payload=d["payload"],
            signature=d.get("signature", ""),
        )

    # ------------------------------------------------------------------
    # Signing helpers
    # ------------------------------------------------------------------

    def _signing_string(self) -> str:
        """Canonical string committed to by the HMAC signature."""
        if isinstance(self.payload, dict):
            payload_repr = json.dumps(self.payload, sort_keys=True)
        else:
            payload_repr = self.payload  # already a ciphertext string
        return f"{self.packet_type}:{self.device_id}:{self.timestamp}:{self.nonce}:{payload_repr}"

    def sign(self, shared_secret: bytes) -> None:
        self.signature = SecurityManager.sign_message(self._signing_string(), shared_secret)

    def verify(self, shared_secret: bytes) -> bool:
        return SecurityManager.verify_signature(
            self._signing_string(), self.signature, shared_secret
        )

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------

    def encrypt_payload(self, plain_payload: Dict[str, Any], shared_secret: bytes) -> None:
        """Encrypt a plain payload dict in-place."""
        plain_str = json.dumps(plain_payload, sort_keys=True)
        self.payload = SecurityManager.encrypt_payload(plain_str, shared_secret)

    def decrypt_payload(self, shared_secret: bytes) -> Optional[Dict[str, Any]]:
        """Decrypt the payload string and return the dict, or None on failure."""
        if not isinstance(self.payload, str):
            return self.payload  # already plain
        plain = SecurityManager.decrypt_payload(self.payload, shared_secret)
        if plain is None:
            return None
        return json.loads(plain)


# ---------------------------------------------------------------------------
# Packet builder
# ---------------------------------------------------------------------------

class PacketBuilder:
    """Creates pre-filled Packet objects for each packet type."""

    def __init__(self, device_id: str, security: SecurityManager) -> None:
        self._device_id = device_id
        self._security = security

    def _base(self, packet_type: str, payload: Union[dict, str]) -> Packet:
        return Packet(
            packet_type=packet_type,
            device_id=self._device_id,
            timestamp=SecurityManager.current_timestamp(),
            nonce=SecurityManager.new_nonce(),
            payload=payload,
        )

    # ------------------------------------------------------------------
    # Unauthenticated (no signature)
    # ------------------------------------------------------------------

    def identity(self, device_name: str, tcp_port: int) -> Packet:
        return self._base(IDENTITY, {
            "device_name": device_name,
            "tcp_port": tcp_port,
            "public_key": self._security.public_key_b64,
        })

    def pair_request(self, device_name: str) -> Packet:
        return self._base(PAIR_REQUEST, {
            "device_name": device_name,
            "public_key": self._security.public_key_b64,
        })

    def pair_response(self, accepted: bool) -> Packet:
        return self._base(PAIR_RESPONSE, {
            "accepted": accepted,
            "public_key": self._security.public_key_b64,
        })

    # ------------------------------------------------------------------
    # Authenticated (caller must call packet.sign(shared_secret))
    # ------------------------------------------------------------------

    def ping(self) -> Packet:
        return self._base(PING, {})

    def pong(self, reply_to_nonce: str) -> Packet:
        return self._base(PONG, {"reply_to_nonce": reply_to_nonce})

    def clipboard(
        self,
        content: str,
        shared_secret: bytes,
        content_type: str = "text/plain",
    ) -> Packet:
        pkt = self._base(CLIPBOARD, {})
        pkt.encrypt_payload(
            {"content_type": content_type, "content": content}, shared_secret
        )
        pkt.sign(shared_secret)
        return pkt
