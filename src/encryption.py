import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String, TypeDecorator

from src.config import settings


class EncryptedString(TypeDecorator):
    """SQLAlchemy type that transparently encrypts/decrypts string values using AES-256-GCM."""

    impl = String
    cache_ok = True

    def _get_key(self) -> bytes:
        return bytes.fromhex(settings.encryption_key)

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        key = self._get_key()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, value.encode("utf-8"), None)
        # Store as hex: nonce + ciphertext
        return (nonce + ciphertext).hex()

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        key = self._get_key()
        aesgcm = AESGCM(key)
        raw = bytes.fromhex(value)
        nonce = raw[:12]
        ciphertext = raw[12:]
        return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
