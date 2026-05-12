from __future__ import annotations

import httpx
import pytest

from app.backends.base import BaseBackend
from app.core.errors import (
    BackendAuthError,
    BackendBadRequestError,
    BackendQuotaError,
    BackendServerError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from app.search.schemas import (
    BackendCapabilities,
    BackendHealth,
)


class _Stub(BaseBackend):
    name = "stub"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (400, BackendBadRequestError),
        (401, BackendAuthError),
        (403, BackendAuthError),
        (402, BackendQuotaError),
        (429, BackendQuotaError),
        (500, BackendServerError),
        (502, BackendServerError),
        (503, BackendUnavailableError),
        (599, BackendServerError),
    ],
)
def test_map_http_error_status_codes(status: int, exc: type[Exception]) -> None:
    err = BaseBackend.map_http_error(status, backend="x")
    assert isinstance(err, exc)
    assert err.backend == "x"  # type: ignore[attr-defined]
    assert err.details["http_status"] == status  # type: ignore[attr-defined]


def test_map_http_error_truncates_body_preview() -> None:
    err = BaseBackend.map_http_error(500, backend="x", body="A" * 1000)
    body = err.details["body_preview"]  # type: ignore[attr-defined]
    assert len(body) == 240  # capped


def test_map_http_error_omits_body_when_none() -> None:
    err = BaseBackend.map_http_error(500, backend="x", body=None)
    assert "body_preview" not in err.details  # type: ignore[attr-defined]


def test_map_http_error_unknown_status_falls_back_to_bad_request() -> None:
    err = BaseBackend.map_http_error(399, backend="x")
    assert isinstance(err, BackendBadRequestError)


def test_map_transport_error_routes_timeout() -> None:
    err = BaseBackend.map_transport_error(httpx.ReadTimeout("slow"), backend="x")
    assert isinstance(err, BackendTimeoutError)


def test_map_transport_error_routes_other_to_unavailable() -> None:
    err = BaseBackend.map_transport_error(httpx.ConnectError("boom"), backend="x")
    assert isinstance(err, BackendUnavailableError)


@pytest.mark.asyncio
async def test_default_healthcheck_is_ok_when_probe_succeeds() -> None:
    stub = _Stub()
    health = await stub.healthcheck()
    assert isinstance(health, BackendHealth)
    assert health.status == "ok"
    assert health.latency_ms is not None
    assert health.last_error is None


@pytest.mark.asyncio
async def test_healthcheck_reports_down_on_probe_exception() -> None:
    class _Bad(_Stub):
        async def _healthcheck_probe(self) -> None:
            raise RuntimeError("nope")

    health = await _Bad().healthcheck()
    assert health.status == "down"
    assert health.last_error == "nope"


def test_http_property_raises_when_no_client_attached() -> None:
    stub = _Stub()  # no http argument
    with pytest.raises(RuntimeError, match="httpx"):
        _ = stub.http
