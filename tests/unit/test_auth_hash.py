from __future__ import annotations

from app.ads.auth import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("scrypt$")
    assert verify_password("correct horse battery", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_hash_uses_unique_salt_per_call():
    first = hash_password("samepassword")
    second = hash_password("samepassword")
    assert first != second
    assert verify_password("samepassword", first)
    assert verify_password("samepassword", second)


def test_verify_rejects_malformed_hash():
    assert verify_password("anything", "") is False
    assert verify_password("anything", "not-a-real-hash") is False
    assert verify_password("anything", "scrypt$bogus") is False
    # Out-of-range parameters get rejected too — keeps a tampered DB row from
    # exhausting RAM with an absurd N.
    assert verify_password("anything", "scrypt$99999999$8$1$abc$def") is False
