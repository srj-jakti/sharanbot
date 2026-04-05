"""Tests for Vault XOR obfuscation fallback."""

from pathlib import Path
from unittest.mock import patch

import pytest


def test_xor_roundtrip():
    """XOR encode/decode should be reversible."""
    from sharanbot.vault import _xor_encode, _xor_decode

    key = b"test-key-1234567890abcdef"
    plaintext = "my-secret-api-key-12345"
    encoded = _xor_encode(plaintext, key)
    assert encoded != plaintext
    decoded = _xor_decode(encoded, key)
    assert decoded == plaintext


def test_xor_different_keys_produce_different_output():
    from sharanbot.vault import _xor_encode

    plaintext = "secret"
    a = _xor_encode(plaintext, b"key-a-0000000000000000")
    b = _xor_encode(plaintext, b"key-b-0000000000000000")
    assert a != b


def test_vault_works_without_cryptography(tmp_path: Path):
    """Vault should use XOR fallback when cryptography is not installed."""
    vault_path = tmp_path / "vault.json"

    with patch("sharanbot.vault._HAS_FERNET", False):
        from sharanbot.vault import Vault

        v = Vault(vault_path, master_password="test-password")
        v.store("provider/openai/apiKey", "sk-test-12345")
        retrieved = v.retrieve("provider/openai/apiKey")
        assert retrieved == "sk-test-12345"

        # Verify it persisted
        assert vault_path.exists()

        # Reload from disk
        v2 = Vault(vault_path, master_password="test-password")
        assert v2.retrieve("provider/openai/apiKey") == "sk-test-12345"


def test_vault_xor_wrong_password_gives_garbage(tmp_path: Path):
    """Wrong password should produce garbage, not the original value."""
    vault_path = tmp_path / "vault.json"

    with patch("sharanbot.vault._HAS_FERNET", False):
        from sharanbot.vault import Vault

        v = Vault(vault_path, master_password="correct-password")
        v.store("key", "my-secret")

        v2 = Vault(vault_path, master_password="wrong-password")
        result = v2.retrieve("key")
        # XOR with wrong key produces garbage — should not match
        assert result != "my-secret"
