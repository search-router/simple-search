"""``LoginThrottle`` locks accounts after repeated failures and resets cleanly."""

from __future__ import annotations

import time

from app.ads.throttle import LoginThrottle


def test_throttle_locks_after_max_attempts():
    throttle = LoginThrottle(max_attempts=3, window_seconds=60)
    assert not throttle.is_locked("alice")
    throttle.record_failure("alice")
    throttle.record_failure("alice")
    assert not throttle.is_locked("alice")
    throttle.record_failure("alice")
    assert throttle.is_locked("alice")


def test_throttle_is_per_username():
    throttle = LoginThrottle(max_attempts=2, window_seconds=60)
    throttle.record_failure("alice")
    throttle.record_failure("alice")
    assert throttle.is_locked("alice")
    assert not throttle.is_locked("bob")


def test_throttle_reset_unlocks():
    throttle = LoginThrottle(max_attempts=2, window_seconds=60)
    throttle.record_failure("alice")
    throttle.record_failure("alice")
    assert throttle.is_locked("alice")
    throttle.reset("alice")
    assert not throttle.is_locked("alice")


def test_throttle_expires_after_window(monkeypatch):
    throttle = LoginThrottle(max_attempts=2, window_seconds=0.01)
    throttle.record_failure("alice")
    throttle.record_failure("alice")
    assert throttle.is_locked("alice")
    time.sleep(0.02)
    assert not throttle.is_locked("alice")


def test_throttle_zero_max_disables():
    throttle = LoginThrottle(max_attempts=0, window_seconds=60)
    for _ in range(100):
        throttle.record_failure("alice")
    assert not throttle.is_locked("alice")


def test_throttle_username_normalized():
    """The key normalizes case and whitespace so ``Alice`` and ``alice`` share state."""
    throttle = LoginThrottle(max_attempts=2, window_seconds=60)
    throttle.record_failure("Alice")
    throttle.record_failure(" alice ")
    assert throttle.is_locked("ALICE")
