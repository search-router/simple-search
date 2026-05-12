from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter, mask_secrets


def test_mask_secrets_in_dict():
    out = mask_secrets({"api_key": "abc", "value": 1})
    assert out == {"api_key": "***", "value": 1}


def test_mask_secrets_recurses_into_nested_collections():
    payload = {
        "headers": {"Authorization": "Bearer x", "User-Agent": "ok"},
        "list": [{"X-Api-Key": "k"}, {"public": "y"}],
        "tup": ({"token": "t"},),
    }
    out = mask_secrets(payload)
    assert out["headers"] == {"Authorization": "***", "User-Agent": "ok"}
    assert out["list"][0] == {"X-Api-Key": "***"}
    assert out["list"][1] == {"public": "y"}
    assert isinstance(out["tup"], tuple)
    assert out["tup"][0] == {"token": "***"}


def test_mask_secrets_pattern_matches_dashed_and_cased_keys():
    assert mask_secrets({"x-api-key": "v"}) == {"x-api-key": "***"}
    assert mask_secrets({"Authorization": "v"}) == {"Authorization": "***"}
    assert mask_secrets({"id_token": "v"}) == {"id_token": "***"}


def test_mask_secrets_passthrough_for_scalars():
    assert mask_secrets("hello") == "hello"
    assert mask_secrets(42) == 42
    assert mask_secrets(None) is None


def test_json_formatter_includes_extra_and_masks_secrets():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.api_key = "topsecret"
    record.user = "alice"
    out = json.loads(formatter.format(record))
    assert out["message"] == "hello"
    assert out["api_key"] == "***"
    assert out["user"] == "alice"
    assert out["level"] == "INFO"
