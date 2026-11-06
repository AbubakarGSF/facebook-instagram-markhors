"""Application-layer encryption for sensitive data at rest.

Uses Fernet (AES-128 in CBC mode with HMAC-SHA256 authentication) to encrypt
session cookies before they are written to Supabase. The encryption key must be
provided via ENCRYPTION_KEY env var (base64-encoded 32 bytes).
"""

import base64
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_cipher: Optional[Fernet] = None


def _get_cipher() -> Optional[Fernet]:
    global _cipher
    if _cipher is None:
        key_b64 = os.getenv("ENCRYPTION_KEY", "").strip()
        if not key_b64:
            logger.warning("ENCRYPTION_KEY not set - cookies will be stored in plaintext")
            return None
        try:
            _cipher = Fernet(key_b64.encode())
        except Exception as e:
            logger.error(f"Invalid ENCRYPTION_KEY: {e}")
            _cipher = None
    return _cipher


def encrypt_cookies(cookies: dict) -> dict:
    """Encrypt cookie values in a dict. Returns a new dict with encrypted values."""
    cipher = _get_cipher()
    if cipher is None:
        return cookies
    encrypted: dict = {}
    for k, v in cookies.items():
        if v is None:
            encrypted[k] = None
        else:
            encrypted[k] = cipher.encrypt(str(v).encode()).decode()
    return encrypted


def decrypt_cookies(cookies: dict) -> dict:
    """Decrypt cookie values in a dict. Returns a new dict with decrypted values."""
    cipher = _get_cipher()
    if cipher is None:
        return cookies
    decrypted: dict = {}
    for k, v in cookies.items():
        if v is None:
            decrypted[k] = None
        else:
            try:
                decrypted[k] = cipher.decrypt(str(v).encode()).decode()
            except InvalidToken:
                # If decryption fails, return as-is (may be plaintext from before encryption)
                decrypted[k] = v
    return decrypted


def generate_key() -> str:
    """Generate a new Fernet key (base64-encoded)."""
    return Fernet.generate_key().decode()


def _get_cipher() -> Optional[Fernet]:
    global _cipher
    if _cipher is None:
        import os
        key_b64 = os.getenv("ENCRYPTION_KEY", "").strip()
        if not key_b64:
            logger.warning("ENCRYPTION_KEY not set - cookies will be stored in plaintext")
            return None
        try:
            _cipher = Fernet(key_b64.encode())
        except Exception as e:
            logger.error(f"Invalid ENCRYPTION_KEY: {e}")
            _cipher = None
    return _cipher


def encrypt_cookies(cookies: dict) -> dict:
    """Encrypt cookie values in a dict. Returns a new dict with encrypted values."""
    cipher = _get_cipher()
    if cipher is None:
        return cookies
    encrypted: dict = {}
    for k, v in cookies.items():
        if v is None:
            encrypted[k] = None
        else:
            encrypted[k] = cipher.encrypt(str(v).encode()).decode()
    return encrypted


def decrypt_cookies(cookies: dict) -> dict:
    """Decrypt cookie values in a dict. Returns a new dict with decrypted values."""
    cipher = _get_cipher()
    if cipher is None:
        return cookies
    decrypted: dict = {}
    for k, v in cookies.items():
        if v is None:
            decrypted[k] = None
        else:
            try:
                decrypted[k] = cipher.decrypt(str(v).encode()).decode()
            except InvalidToken:
                # If decryption fails, return as-is (may be plaintext from before encryption)
                decrypted[k] = v
    return decrypted


def generate_key() -> str:
    """Generate a new Fernet key (base64-encoded)."""
    return Fernet.generate_key().decode()


# Optional[Fernet] import for type hint
from typing import Optional
