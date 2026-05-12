from __future__ import annotations

from app.core.ids import _ALPHABET, new_request_id


def test_new_request_id_format():
    rid = new_request_id()
    assert rid.startswith("req_")
    body = rid[4:]
    # 10 timestamp chars + 8 random chars = 18
    assert len(body) == 18
    assert all(ch in _ALPHABET for ch in body)


def test_new_request_id_is_unique_across_calls():
    ids = {new_request_id() for _ in range(50)}
    assert len(ids) == 50


def test_new_request_id_alphabet_excludes_confusable_chars():
    # Crockford-ish base32: must exclude the characters most often confused.
    for forbidden in ("I", "L", "O", "U"):
        assert forbidden not in _ALPHABET
