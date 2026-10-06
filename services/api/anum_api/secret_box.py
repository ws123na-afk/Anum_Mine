"""Encryption at rest for tenant secrets such as model provider API keys.

Secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using ``ANUM_SECRETS_KEY``.
The setting may hold several comma-separated keys: the first encrypts, all of them
decrypt, so keys can be rotated without a flag day. Outside ``ANUM_ENVIRONMENT=local``
the key is required (``Settings`` refuses to start without it). In local development a
deterministic dev key is derived and a warning is logged; it offers no real protection.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from threading import Lock

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .settings import settings

logger = logging.getLogger("anum.secrets")

_LOCAL_DEV_KEY_MATERIAL = b"anum-local-development-secrets-key-v1"


class SecretDecryptionError(RuntimeError):
    """A stored secret could not be decrypted with the configured keys."""


class SecretCipher:
    def __init__(self, keys: list[bytes]) -> None:
        if not keys:
            raise ValueError("at least one secrets key is required")
        self._fernet = MultiFernet([Fernet(key) for key in keys])

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            # Never include the token or key material in the error.
            raise SecretDecryptionError("stored secret could not be decrypted") from exc

    @classmethod
    def from_settings(cls) -> SecretCipher:
        raw = settings.secrets_key.get_secret_value().strip() if settings.secrets_key else ""
        if raw:
            return cls([key.strip().encode("ascii") for key in raw.split(",") if key.strip()])
        if settings.environment.strip().lower() not in {"local", "test"}:
            raise RuntimeError("ANUM_SECRETS_KEY is required outside ANUM_ENVIRONMENT=local or test")
        logger.warning(
            "ANUM_SECRETS_KEY is not set; using a derived local development key. "
            "Stored model credentials are not protected. Set ANUM_SECRETS_KEY outside local."
        )
        return cls([base64.urlsafe_b64encode(hashlib.sha256(_LOCAL_DEV_KEY_MATERIAL).digest())])


_cipher: SecretCipher | None = None
_cipher_lock = Lock()


def secret_cipher() -> SecretCipher:
    """The process-wide cipher, built from settings on first use."""
    global _cipher
    with _cipher_lock:
        if _cipher is None:
            _cipher = SecretCipher.from_settings()
        return _cipher


def reset_secret_cipher() -> None:
    """Forget the cached cipher (tests, or after a settings change)."""
    global _cipher
    with _cipher_lock:
        _cipher = None
