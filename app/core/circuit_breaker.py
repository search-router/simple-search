"""Async circuit breaker — closed / open / half-open."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app.core.errors import CircuitOpenError

State = Literal["closed", "open", "half_open"]


@dataclass
class _BreakerState:
    state: State = "closed"
    failures: int = 0
    opened_at: float = 0.0
    half_open_in_flight: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CircuitBreaker:
    """Per-name circuit breaker, suitable for a single-process deployment."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
        half_open_max_requests: int = 1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        self._threshold = failure_threshold
        self._recovery = recovery_timeout_seconds
        self._half_open_max = half_open_max_requests
        self._states: dict[str, _BreakerState] = {}
        self._clock = clock or time.monotonic

    def register(self, name: str) -> None:
        """Pre-allocate a breaker state so the hot path can skip the dict-miss branch."""
        if name not in self._states:
            self._states[name] = _BreakerState()

    def _get(self, name: str) -> _BreakerState:
        # asyncio is single-threaded; ``setdefault`` is atomic between awaits,
        # so the previous ``_states_lock`` was pure overhead per call.
        state = self._states.get(name)
        if state is None:
            state = _BreakerState()
            self._states[name] = state
        return state

    async def state_of(self, name: str) -> State:
        bs = self._get(name)
        async with bs.lock:
            self._maybe_recover(bs)
            return bs.state

    async def acquire(self, name: str) -> _BreakerCtx:
        bs = self._get(name)
        async with bs.lock:
            self._maybe_recover(bs)
            if bs.state == "open":
                raise CircuitOpenError(backend=name)
            if bs.state == "half_open":
                if bs.half_open_in_flight >= self._half_open_max:
                    raise CircuitOpenError(backend=name)
                bs.half_open_in_flight += 1
        return _BreakerCtx(self, name, bs)

    def _maybe_recover(self, bs: _BreakerState) -> None:
        if bs.state == "open" and (self._clock() - bs.opened_at) >= self._recovery:
            bs.state = "half_open"
            bs.half_open_in_flight = 0

    async def _record_success(self, bs: _BreakerState) -> None:
        async with bs.lock:
            bs.failures = 0
            bs.state = "closed"
            bs.half_open_in_flight = 0
            bs.opened_at = 0.0

    async def _record_failure(self, bs: _BreakerState) -> None:
        async with bs.lock:
            bs.failures += 1
            if bs.state == "half_open":
                bs.state = "open"
                bs.opened_at = self._clock()
                bs.half_open_in_flight = 0
                return
            if bs.failures >= self._threshold:
                bs.state = "open"
                bs.opened_at = self._clock()


class _BreakerCtx:
    """Async context manager that records success or failure on exit."""

    def __init__(self, breaker: CircuitBreaker, name: str, state: _BreakerState) -> None:
        self._breaker = breaker
        self._name = name
        self._state = state

    async def __aenter__(self) -> _BreakerCtx:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, *_: object) -> None:
        if exc_type is None:
            await self._breaker._record_success(self._state)
        else:
            await self._breaker._record_failure(self._state)
