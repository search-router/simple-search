"""Service-level error hierarchy and the public ``ApiError`` envelope."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """The inner ``error`` object of the public API envelope."""

    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str
    backend: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ApiError(BaseModel):
    """Top-level API error envelope. Stable across endpoints."""

    model_config = ConfigDict(populate_by_name=True)

    request_id: str
    error: ErrorDetail


class ServiceError(Exception):
    """Base class for every controlled error inside the service."""

    code: str = "internal_error"
    http_status: int = 500
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        backend: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.backend = backend
        self.details: dict[str, Any] = details or {}

    def as_detail(self) -> ErrorDetail:
        return ErrorDetail(
            code=self.code,
            message=self.message,
            backend=self.backend,
            details=self.details,
        )


class InvalidRequestError(ServiceError):
    code = "invalid_request"
    http_status = 400
    message = "Invalid request"


class UnsupportedBackendError(ServiceError):
    code = "unsupported_backend"
    http_status = 400
    message = "Unknown or disabled backend"


class UnsupportedCapabilityError(ServiceError):
    code = "unsupported_capability"
    http_status = 400
    message = "Backend does not support this capability"


class RateLimitedError(ServiceError):
    code = "rate_limited"
    http_status = 429
    message = "Too many requests"


# --- backend-side errors ----------------------------------------------------

class BackendError(ServiceError):
    """Anything that goes wrong inside a backend adapter."""


class BackendBadRequestError(BackendError):
    code = "backend_bad_response"
    http_status = 400
    message = "Backend rejected the request"


class BackendAuthError(BackendError):
    code = "backend_auth_error"
    http_status = 502
    message = "Backend authentication failed"


class BackendQuotaError(BackendError):
    code = "backend_quota_error"
    http_status = 502
    message = "Backend quota exceeded"


class BackendServerError(BackendError):
    code = "backend_unavailable"
    http_status = 502
    message = "Backend internal error"


class BackendUnavailableError(BackendError):
    code = "backend_unavailable"
    http_status = 503
    message = "Backend temporarily unavailable"


class BackendTimeoutError(BackendError):
    code = "backend_timeout"
    http_status = 504
    message = "Backend request timed out"


class BackendBadResponseError(BackendError):
    code = "backend_bad_response"
    http_status = 502
    message = "Backend returned an unparseable response"


class CircuitOpenError(BackendError):
    code = "backend_unavailable"
    http_status = 503
    message = "Circuit breaker is open"


class ConfigurationError(ServiceError):
    code = "internal_error"
    http_status = 500
    message = "Service configuration error"
