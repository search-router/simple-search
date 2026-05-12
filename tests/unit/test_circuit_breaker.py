from __future__ import annotations

import pytest

from app.core.circuit_breaker import CircuitBreaker
from app.core.errors import CircuitOpenError


class _Clock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold():
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=10, clock=clock)

    for _ in range(3):
        with pytest.raises(RuntimeError):
            async with await breaker.acquire("alt"):
                raise RuntimeError("boom")

    with pytest.raises(CircuitOpenError):
        await breaker.acquire("alt")

    assert await breaker.state_of("alt") == "open"


@pytest.mark.asyncio
async def test_breaker_recovers_via_half_open():
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=5, clock=clock)

    with pytest.raises(RuntimeError):
        async with await breaker.acquire("alt"):
            raise RuntimeError("boom")

    assert await breaker.state_of("alt") == "open"

    clock.advance(6)
    assert await breaker.state_of("alt") == "half_open"

    async with await breaker.acquire("alt"):
        pass

    assert await breaker.state_of("alt") == "closed"


@pytest.mark.asyncio
async def test_half_open_max_requests():
    clock = _Clock()
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=1,
        half_open_max_requests=1,
        clock=clock,
    )

    with pytest.raises(RuntimeError):
        async with await breaker.acquire("name"):
            raise RuntimeError()
    clock.advance(2)
    first = await breaker.acquire("name")  # half-open probe
    assert isinstance(first, object)

    with pytest.raises(CircuitOpenError):
        await breaker.acquire("name")  # second probe is blocked

    # close probe successfully and the breaker reopens to closed
    async with first:
        pass
    assert await breaker.state_of("name") == "closed"


@pytest.mark.asyncio
async def test_state_of_unknown_name_is_closed():
    """Querying a never-seen backend must report 'closed', not raise or auto-open."""
    breaker = CircuitBreaker()
    assert await breaker.state_of("unseen") == "closed"


@pytest.mark.asyncio
async def test_breakers_are_isolated_per_name():
    """Tripping one backend's breaker must not affect another's."""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60)
    with pytest.raises(RuntimeError):
        async with await breaker.acquire("a"):
            raise RuntimeError("boom")
    assert await breaker.state_of("a") == "open"
    assert await breaker.state_of("b") == "closed"
    # Acquiring on 'b' still works.
    async with await breaker.acquire("b"):
        pass


@pytest.mark.asyncio
async def test_breaker_rejects_non_positive_threshold():
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker(failure_threshold=-1)


@pytest.mark.asyncio
async def test_half_open_failure_reopens_circuit():
    clock = _Clock()
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=5, clock=clock)

    with pytest.raises(RuntimeError):
        async with await breaker.acquire("svc"):
            raise RuntimeError("boom")
    clock.advance(6)
    assert await breaker.state_of("svc") == "half_open"

    with pytest.raises(RuntimeError):
        async with await breaker.acquire("svc"):
            raise RuntimeError("still bad")
    # Failed half-open probe must trip immediately back to open.
    assert await breaker.state_of("svc") == "open"
