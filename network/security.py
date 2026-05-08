"""
Security primitives for authenticated, encrypted device communication.

Design:
  - X25519 ECDH for key agreement during pairing (no pre-shared secrets needed)
  - HKDF-SHA256 to derive a 32-byte shared secret from the ECDH output
  - Fernet (AES-128-CBC + HMAC-SHA256) for payload encryption
  - HMAC-SHA256 over the outer packet fields as an additional integrity check
  - Nonce + timestamp window to block replay attacks
"""

import base64
import hashlib
import hmac
import os
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from utils.logger import get_logger

log = get_logger("security")

_HKDF_INFO = b"python-clipboard-sync-v1"


class SecurityManager:
    """Per-process ECDH key pair + per-connection nonce cache."""

    TIMESTAMP_TOLERANCE_SEC = 30
    NONCE_CACHE_MAX = 1000

    def __init__(self, keys_dir: Optional[Path] = None) -> None:
        self._seen_nonces: OrderedDict[str, float] = OrderedDict()
        self._private_key, self._public_key = self._load_or_generate(keys_dir)

    # ------------------------------------------------------------------
    # ECDH key management
    # ------------------------------------------------------------------

    def _load_or_generate(
        self, keys_dir: Optional[Path]
    ) -> Tuple[X25519PrivateKey, X25519PublicKey]:
        if keys_dir is not None:
            priv_path = keys_dir / "device.key"
            os.makedirs(keys_dir, exist_ok=True)
            if priv_path.exists():
                with open(priv_path, "rb") as f:
                    raw = f.read()
                priv = X25519PrivateKey.from_private_bytes(raw)
                log.info("Loaded existing ECDH private key")
                return priv, priv.public_key()
        priv = X25519PrivateKey.generate()
        if keys_dir is not None:
            raw = priv.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            with open(priv_path, "wb") as f:
                f.write(raw)
            log.info("Generated new ECDH private key")
        return priv, priv.public_key()

    @property
    def public_key_bytes(self) -> bytes:
        return self._public_key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key_bytes).decode()

    def derive_shared_secret(self, peer_public_key_b64: str) -> bytes:
        """Derive a 32-byte shared secret from the peer's X25519 public key."""
        peer_raw = base64.b64decode(peer_public_key_b64)
        peer_pub = X25519PublicKey.from_public_bytes(peer_raw)
        raw_shared = self._private_key.exchange(peer_pub)
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=_HKDF_INFO,
        )
        secret = hkdf.derive(raw_shared)
        log.debug("Shared secret derived via ECDH+HKDF")
        return secret

    # ------------------------------------------------------------------
    # Fernet payload encryption / decryption
    # ------------------------------------------------------------------

    @staticmethod
    def _fernet(shared_secret: bytes) -> Fernet:
        # Fernet expects a URL-safe base64-encoded 32-byte key
        key = base64.urlsafe_b64encode(shared_secret)
        return Fernet(key)

    @staticmethod
    def encrypt_payload(plaintext: str, shared_secret: bytes) -> str:
        """Returns a Fernet token (base64 string)."""
        token = SecurityManager._fernet(shared_secret).encrypt(plaintext.encode())
        return token.decode()

    @staticmethod
    def decrypt_payload(token: str, shared_secret: bytes) -> Optional[str]:
        """Returns decrypted string, or None if token is invalid."""
        try:
            return SecurityManager._fernet(shared_secret).decrypt(token.encode()).decode()
        except (InvalidToken, Exception) as exc:
            log.error(f"Payload decryption failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # HMAC-SHA256 packet signatures
    # ------------------------------------------------------------------

    @staticmethod
    def sign_message(message: str, shared_secret: bytes) -> str:
        mac = hmac.new(shared_secret, message.encode("utf-8"), hashlib.sha256)
        return mac.hexdigest()

    @staticmethod
    def verify_signature(message: str, signature: str, shared_secret: bytes) -> bool:
        expected = hmac.new(shared_secret, message.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # Nonce and timestamp anti-replay
    # ------------------------------------------------------------------

    @staticmethod
    def new_nonce() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def current_timestamp() -> int:
        return int(time.time())

    def is_timestamp_fresh(self, timestamp: int) -> bool:
        delta = abs(int(time.time()) - timestamp)
        if delta > self.TIMESTAMP_TOLERANCE_SEC:
            log.warning(f"Timestamp too old/new: delta={delta}s")
            return False
        return True

    def is_nonce_fresh(self, nonce: str) -> bool:
        """True only if this nonce has never been seen before."""
        if nonce in self._seen_nonces:
            log.warning(f"Replayed nonce detected: {nonce[:8]}…")
            return False
        self._seen_nonces[nonce] = time.time()
        # Evict oldest entries if cache is full
        while len(self._seen_nonces) > self.NONCE_CACHE_MAX:
            self._seen_nonces.popitem(last=False)
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def generate_secret() -> bytes:
        """Generate a random 32-byte secret (used for manual pairing if needed)."""
        return os.urandom(32)

    @staticmethod
    def secret_to_hex(secret: bytes) -> str:
        return secret.hex()

    @staticmethod
    def hex_to_secret(hex_str: str) -> bytes:
        return bytes.fromhex(hex_str)

    @staticmethod
    def verification_string(shared_secret: bytes) -> str:
        """Short human-readable code to display on both sides for MITM detection."""
        digest = hashlib.sha256(shared_secret).hexdigest()
        return f"{digest[:4]}-{digest[4:8]}-{digest[8:12]}"
