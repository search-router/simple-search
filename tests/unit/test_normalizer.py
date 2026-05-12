from app.search.normalizer import clamp, coerce_int, domain_of


def test_domain_of_strips_www():
    assert domain_of("https://www.example.com/foo") == "example.com"


def test_domain_of_returns_none():
    assert domain_of(None) is None
    assert domain_of("") is None


def test_clamp():
    assert clamp(0, 1, 100) == 1
    assert clamp(150, 1, 100) == 100
    assert clamp(50, 1, 100) == 50


def test_coerce_int():
    assert coerce_int("42") == 42
    assert coerce_int(None, default=7) == 7
    assert coerce_int("abc", default=None) is None


def test_coerce_int_strips_whitespace():
    assert coerce_int("  42 ") == 42


def test_coerce_int_empty_string_returns_default():
    assert coerce_int("", default=99) == 99


def test_coerce_int_negative():
    assert coerce_int("-5") == -5


def test_coerce_int_float_string_returns_default():
    # Floats as strings are not coerced — caller must round/cast explicitly.
    assert coerce_int("3.5", default=0) == 0


def test_domain_of_lowercases_and_drops_port():
    assert domain_of("https://EXAMPLE.com:8080/path") == "example.com"


def test_domain_of_strips_only_leading_www():
    assert domain_of("https://wwwexample.com") == "wwwexample.com"
    # ``www2`` is a legitimate subdomain — must not be stripped.
    assert domain_of("https://www2.example.com") == "www2.example.com"


def test_domain_of_handles_url_without_scheme():
    # urlparse can't extract a hostname from a bare ``foo.com/path``.
    assert domain_of("example.com/path") is None


def test_clamp_inverted_bounds_returns_lo():
    # max(lo, min(hi, value)) collapses to lo when lo > hi.
    assert clamp(50, 100, 1) == 100


def test_domain_of_handles_ipv6_literal_url():
    # urlparse hands back a bracket-less hostname for ``[::1]``; the helper
    # must surface it instead of silently dropping it.
    assert domain_of("https://[::1]:8080/x") == "::1"
    assert domain_of("https://[2001:db8::1]/path") == "2001:db8::1"


def test_domain_of_returns_none_for_whitespace_only_url():
    assert domain_of("   ") is None


def test_domain_of_strips_trailing_whitespace_from_hostname():
    # ``urlparse`` keeps trailing whitespace inside the netloc when no path
    # delimiter follows the host. The helper must normalize that away so the
    # rendered domain is consistent and ``WebResult.domain`` doesn't carry
    # a ragged value into cache keys or templates.
    assert domain_of("https://example.com   ") == "example.com"
    assert domain_of("  https://example.com  ") == "example.com"
    assert domain_of("\n\thttps://example.com\n") == "example.com"


def test_domain_of_handles_tab_in_netloc():
    """A tab embedded in the netloc must be stripped, not surfaced."""
    assert domain_of("https://example.com\t") == "example.com"
