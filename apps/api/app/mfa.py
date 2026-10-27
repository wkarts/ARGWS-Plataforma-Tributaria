"""TOTP (RFC 6238) and one-time recovery codes with encrypted storage."""
from __future__ import annotations

import base64
from hashlib import sha256
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings


def _cipher() -> Fernet:
    # A dedicated domain-separated key; never store a TOTP secret unencrypted.
    material = sha256(("fiscal-totp-v1:" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(material))


def encrypt_seed(seed: str) -> str:
    return _cipher().encrypt(seed.encode("ascii")).decode("ascii")


def decrypt_seed(encrypted: str) -> str:
    try:
        return _cipher().decrypt(encrypted.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, UnicodeError):
        raise ValueError("O segredo 2FA não pode ser aberto") from None


def new_seed() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp(seed: str, counter: int) -> str:
    key = base64.b32decode(seed + "=" * (-len(seed) % 8), casefold=False)
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


def valid_counter(seed: str, value: str, *, current_time: float | None = None,
                  previous_counter: int = -1) -> int | None:
    if len(value) != 6 or not value.isascii() or not value.isdecimal():
        return None
    current = int((current_time if current_time is not None else time.time()) // 30)
    for counter in (current - 1, current, current + 1):
        if counter > previous_counter and hmac.compare_digest(totp(seed, counter), value):
            return counter
    return None


def recovery_codes() -> tuple[list[str], list[str]]:
    clear = [secrets.token_hex(8).upper() for _ in range(8)]
    return clear, [sha256(value.encode()).hexdigest() for value in clear]


def recover(code: str, stored: list) -> tuple[bool, list]:
    candidate = sha256(code.strip().upper().encode()).hexdigest()
    found = any(hmac.compare_digest(candidate, value) for value in stored)
    return found, [item for item in stored if not hmac.compare_digest(candidate, item)]


def enrollment_uri(email: str, seed: str) -> str:
    label = quote("Gestão Fiscal:" + email, safe="")
    return f"otpauth://totp/{label}?secret={seed}&issuer=Gest%C3%A3o%20Fiscal&algorithm=SHA1&digits=6&period=30"
