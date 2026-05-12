"""Password hashing (scrypt) and constant-time verification.

Hash format: ``scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>``. The salt and hash
are urlsafe base64 (no padding) so the field stays ASCII and grep-safe.

scrypt parameters are fixed for simplicity. Increase ``N`` when you need to
re-bench against a faster CPU; the format keeps them inline so an old hash
keeps verifying after you bump the defaults for new users.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re

_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_HASH_BYTES = 32
# OpenSSL's scrypt enforces ``maxmem`` (32 MiB default on many builds). Our
# parameters fit in well under 32 MiB but we set a higher ceiling so we can
# bump ``N`` later without re-deriving this number.
_SCRYPT_MAXMEM = 128 * 1024 * 1024

_FORMAT_RE = re.compile(r"^scrypt\$(\d+)\$(\d+)\$(\d+)\$([\w\-]+)\$([\w\-]+)$")


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_HASH_BYTES,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    match = _FORMAT_RE.match(stored or "")
    if not match:
        return False
    try:
        n = int(match.group(1))
        r = int(match.group(2))
        p = int(match.group(3))
        salt = _b64d(match.group(4))
        expected = _b64d(match.group(5))
    except (ValueError, binascii.Error):
        return False
    if not (1 < n <= 2**20 and 1 <= r <= 32 and 1 <= p <= 16):
        return False
    candidate = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_SCRYPT_MAXMEM,
        dklen=len(expected),
    )
    return hmac.compare_digest(candidate, expected)
