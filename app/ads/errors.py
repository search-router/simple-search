"""Ads-layer error hierarchy. All extend ``ServiceError`` so the public
JSON envelope handler works unchanged; UI handlers catch these and render
a form with an inline message."""

from __future__ import annotations

from app.core.errors import ServiceError


class AdsError(ServiceError):
    code = "ads_error"
    http_status = 400
    message = "Ads request failed"


class InvalidCredentialsError(AdsError):
    code = "invalid_credentials"
    http_status = 401
    message = "Invalid username or password"


class UsernameTakenError(AdsError):
    code = "username_taken"
    http_status = 409
    message = "Username is already taken"


class InvalidBidError(AdsError):
    code = "invalid_bid"
    http_status = 400
    message = "Bid is invalid"


class InsufficientFundsError(AdsError):
    code = "insufficient_funds"
    http_status = 400
    message = "Wallet balance is too low"


class AuthRequiredError(AdsError):
    code = "auth_required"
    http_status = 401
    message = "Authentication required"
