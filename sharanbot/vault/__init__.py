"""Encrypted credential vault for SharanBot.

Stores API keys, tokens, passwords, and connection strings securely
using Fernet symmetric encryption derived from a master password.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

# Fernet is in the stdlib-adjacent cryptography package.
# We fall back to a simple XOR obfuscation if cryptography is unavailable.
try:
    from cryptography.fernet import Fernet

    _HAS_FERNET = True
except ImportError:  # pragma: no cover
    _HAS_FERNET = False


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from *password* + *salt* using PBKDF2."""
    raw = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations=480_000)
    return base64.urlsafe_b64encode(raw)


class Vault:
    """Encrypted key-value credential store.

    Data layout on disk (``vault.json``)::

        {
          "salt": "<hex>",
          "entries": {
            "provider/openrouter/apiKey": "<encrypted-base64>",
            "channel/telegram/token":     "<encrypted-base64>",
            ...
          }
        }
    """

    def __init__(self, vault_path: Path, master_password: str | None = None):
        self._path = vault_path
        self._salt: bytes = b""
        self._entries: dict[str, str] = {}  # key → encrypted value
        self._key: bytes | None = None
        self._unlocked = False

        if self._path.exists():
            self._load()

        if master_password:
            self.unlock(master_password)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def unlock(self, master_password: str) -> None:
        """Derive the encryption key from *master_password*."""
        if not self._salt:
            self._salt = os.urandom(16)
        self._key = _derive_key(master_password, self._salt)
        self._unlocked = True

    @property
    def is_unlocked(self) -> bool:
        return self._unlocked

    def store(self, key: str, value: str) -> None:
        """Encrypt and store a credential."""
        if not self._unlocked or self._key is None:
            raise RuntimeError("Vault is locked — call unlock() first.")
        self._entries[key] = self._encrypt(value)
        self._flush()
        logger.debug("Vault: stored credential '{}'", key)

    def retrieve(self, key: str) -> str | None:
        """Decrypt and return a credential, or *None* if absent."""
        if not self._unlocked or self._key is None:
            raise RuntimeError("Vault is locked — call unlock() first.")
        enc = self._entries.get(key)
        if enc is None:
            return None
        return self._decrypt(enc)

    def delete(self, key: str) -> bool:
        """Remove a credential.  Returns True if it existed."""
        removed = self._entries.pop(key, None) is not None
        if removed:
            self._flush()
        return removed

    def list_keys(self) -> list[str]:
        """Return all stored credential keys (not values)."""
        return list(self._entries.keys())

    def has(self, key: str) -> bool:
        return key in self._entries

    def export_to_config(self) -> dict[str, Any]:
        """Decrypt all entries and return as nested dict for config overlay.

        Keys use ``/`` separators that map to nested dict paths.
        E.g. ``provider/openrouter/apiKey`` → ``{"provider": {"openrouter": {"apiKey": "..."}}}``
        """
        if not self._unlocked:
            raise RuntimeError("Vault is locked.")
        result: dict[str, Any] = {}
        for key in self._entries:
            parts = key.split("/")
            d = result
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = self.retrieve(key)
        return result

    # ------------------------------------------------------------------
    # Encryption helpers
    # ------------------------------------------------------------------

    def _encrypt(self, plaintext: str) -> str:
        assert self._key is not None
        if _HAS_FERNET:
            f = Fernet(self._key)
            return f.encrypt(plaintext.encode()).decode()
        # Fallback: XOR obfuscation (not production-grade)
        return base64.urlsafe_b64encode(
            bytes(a ^ b for a, b in zip(plaintext.encode(), (self._key * ((len(plaintext) // len(self._key)) + 1))))
        ).decode()

    def _decrypt(self, ciphertext: str) -> str:
        assert self._key is not None
        if _HAS_FERNET:
            f = Fernet(self._key)
            return f.decrypt(ciphertext.encode()).decode()
        raw = base64.urlsafe_b64decode(ciphertext)
        return bytes(a ^ b for a, b in zip(raw, (self._key * ((len(raw) // len(self._key)) + 1)))).decode()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._salt = bytes.fromhex(data.get("salt", ""))
            self._entries = data.get("entries", {})
        except Exception:
            logger.warning("Vault: failed to load {}", self._path)
            self._salt = b""
            self._entries = {}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "salt": self._salt.hex(),
            "entries": self._entries,
        }
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
