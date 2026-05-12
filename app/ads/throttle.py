"""In-memory per-account login throttle.

A fixed-window counter keyed by the lowercased username. Once the per-window
ceiling is reached, further attempts return immediately with a synthetic
failure so a brute-force loop cannot exhaust scrypt CPU. The window slides
every successful login, so legitimate users are never locked out
permanently.

Lives in-process — fine for single-replica demo deploys. Move to Redis if
you ever run more than one worker.
"""

from __future__ import annotations

import time


class LoginThrottle:
    """Counts failed attempts per username over a rolling window."""

    def __init__(self, *, max_attempts: int = 10, window_seconds: float = 300.0) -> None:
        self._max = max(max_attempts, 0)
        self._window = window_seconds
        self._state: dict[str, tuple[int, float]] = {}

    def _key(self, username: str) -> str:
        return (username or "").strip().lower()

    def is_locked(self, username: str) -> bool:
        if self._max == 0:
            return False
        entry = self._state.get(self._key(username))
        if entry is None:
            return False
        count, reset_at = entry
        if time.monotonic() >= reset_at:
            return False
        return count >= self._max

    def record_failure(self, username: str) -> None:
        if self._max == 0:
            return
        key = self._key(username)
        now = time.monotonic()
        entry = self._state.get(key)
        if entry is None or now >= entry[1]:
            self._state[key] = (1, now + self._window)
            return
        count, reset_at = entry
        self._state[key] = (count + 1, reset_at)

    def reset(self, username: str) -> None:
        self._state.pop(self._key(username), None)
