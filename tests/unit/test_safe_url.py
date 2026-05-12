"""``safe_url`` blocks non-http(s) URL schemes before they reach templates."""

from __future__ import annotations

import pytest

from app.search.normalizer import safe_url


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/x",
        "http://example.com/",
    ],
)
def test_safe_url_passes_http_and_https_through(value):
    assert safe_url(value) == value


def test_safe_url_trims_whitespace():
    assert safe_url("  https://example.com/y  ") == "https://example.com/y"


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>x()</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "//example.com/no-scheme",
        "example.com",
        "",
        None,
    ],
)
def test_safe_url_strips_unsafe_or_missing_schemes(value):
    assert safe_url(value) == ""
