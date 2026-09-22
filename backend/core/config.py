"""Security and configuration module.

Traces to: NFR-3 (Security: API key protection, unmasked prevention in logs, encryption utilities).
"""

import base64
import os


def mask_api_key(api_key: str | None) -> str:
    """Mask an API key for safe logging, representation, and error messages.

    Covers NFR-3(b): API key MUST NOT appear in plain text in logs.
    Example: 'sk-abcdef123456' -> 'sk-...3456'
    """
    if not api_key:
        return "***"
    key = api_key.strip()
    if len(key) <= 8:
        return "***"
    return f"{key[:3]}...{key[-4:]}"


def get_master_key() -> bytes:
    """Retrieve 32-byte master key from environment for at-rest storage encryption."""
    secret = os.getenv("CLIPPER_SECRET_KEY", "clipper-turbo-default-master-key-32b")
    return secret.encode("utf-8")[:32].ljust(32, b"0")


def encrypt_at_rest(plain_text: str) -> str:
    """Encrypt secret string for at-rest persistence.

    Covers NFR-3(c): MUST be encrypted at rest when persisted.
    """
    if not plain_text:
        return ""
    key = get_master_key()
    data_bytes = plain_text.encode("utf-8")
    encrypted_bytes = bytes(b ^ key[i % len(key)] for i, b in enumerate(data_bytes))
    return base64.b64encode(encrypted_bytes).decode("ascii")


def decrypt_at_rest(cipher_text: str) -> str:
    """Decrypt a secret that was encrypted for at-rest storage."""
    if not cipher_text:
        return ""
    key = get_master_key()
    encrypted_bytes = base64.b64decode(cipher_text.encode("ascii"))
    data_bytes = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted_bytes))
    return data_bytes.decode("utf-8")
