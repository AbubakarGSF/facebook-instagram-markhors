"""Tests for crypto module."""

import os

from cryptography.fernet import Fernet

# Set a valid ENCRYPTION_KEY before importing crypto module
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import src.utils.crypto as crypto_mod


def test_encrypt_decrypt_roundtrip():
    """Test encrypt then decrypt roundtrip."""
    crypto_mod._get_cipher()  # Initialize cipher

    original = {"auth_token": "secret_token_123", "ct0": "secret_ct0_456"}
    encrypted = crypto_mod.encrypt_cookies(original)
    assert encrypted != original
    assert encrypted["auth_token"] != "secret_token_123"

    decrypted = crypto_mod.decrypt_cookies(encrypted)
    assert decrypted == original


def test_encrypt_decrypt_none_values():
    """Test encrypting/decrypting None values."""
    crypto_mod._get_cipher()

    data = {"auth_token": None, "ct0": "value"}
    encrypted = crypto_mod.encrypt_cookies(data)
    assert encrypted["auth_token"] is None
    assert encrypted["ct0"] is not None

    decrypted = crypto_mod.decrypt_cookies(encrypted)
    assert decrypted["auth_token"] is None
    assert decrypted["ct0"] == "value"


def test_generate_key():
    """Test key generation."""
    key = crypto_mod.generate_key()
    assert isinstance(key, str)
    assert len(key) > 0


def test_encrypt_decrypt_different_keys():
    """Test that different keys produce different encryption."""
    import cryptography.fernet

    key1 = crypto_mod.generate_key()
    key2 = crypto_mod.generate_key()

    cipher1 = cryptography.fernet.Fernet(key1.encode())
    cipher2 = cryptography.fernet.Fernet(key2.encode())

    data = {"auth_token": "secret_token"}
    enc1 = cipher1.encrypt(str(data["auth_token"]).encode()).decode()
    enc2 = cipher2.encrypt(str(data["auth_token"]).encode()).decode()
    assert enc1 != enc2