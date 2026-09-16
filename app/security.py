from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def generate_api_key() -> tuple[str, str, str]:
    """Return raw key, searchable prefix and irreversible secret hash."""
    prefix = f"ptm_live_{secrets.token_hex(4)}"
    secret = secrets.token_urlsafe(32)
    return f"{prefix}.{secret}", prefix, hash_secret(secret)


def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        secret.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode().rstrip("="),
            base64.urlsafe_b64encode(digest).decode().rstrip("="),
        ]
    )


def verify_secret(secret: str, encoded: str) -> bool:
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(raw_salt + "=" * (-len(raw_salt) % 4))
        expected = base64.urlsafe_b64decode(raw_digest + "=" * (-len(raw_digest) % 4))
        actual = hashlib.scrypt(
            secret.encode(),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def split_api_key(raw_key: str) -> tuple[str, str] | None:
    if "." not in raw_key:
        return None
    prefix, secret = raw_key.split(".", 1)
    if not prefix.startswith("ptm_live_") or not secret:
        return None
    return prefix, secret
