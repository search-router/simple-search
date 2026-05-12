"""Request id generation."""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# Map every byte (0–255) to a single alphabet char by masking to the low 5
# bits. This lets us turn one ``token_bytes`` call into the full random
# segment without the per-character ``secrets.choice`` overhead.
_BYTE_TO_ALPHABET = bytes(ord(_ALPHABET[b & 0x1F]) for b in range(256))


def new_request_id() -> str:
    """Return a sortable, opaque request id like ``req_01HX...``."""
    millis = int(time.time() * 1000)
    timestamp_part = _b32_encode(millis, length=10)
    random_part = secrets.token_bytes(8).translate(_BYTE_TO_ALPHABET).decode("ascii")
    return f"req_{timestamp_part}{random_part}"


def _b32_encode(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))
