from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.search.schemas import (
    ImageSearchRequest,
    WebResult,
    WebSearchRequest,
)


def test_web_search_request_minimum():
    req = WebSearchRequest(q="hello")
    assert req.backend == "auto"
    assert req.limit == 10
    assert req.cache is True


def test_web_search_request_rejects_empty_query():
    with pytest.raises(ValidationError):
        WebSearchRequest(q="")


def test_web_search_request_rejects_oversize_query():
    with pytest.raises(ValidationError):
        WebSearchRequest(q="x" * 401)


@pytest.mark.parametrize("limit", [0, 101])
def test_web_search_request_rejects_bad_limits(limit: int):
    with pytest.raises(ValidationError):
        WebSearchRequest(q="ok", limit=limit)


def test_web_search_request_rejects_extra_fields():
    with pytest.raises(ValidationError):
        WebSearchRequest.model_validate({"q": "ok", "foo": "bar"})


def test_image_filters_default_factory():
    req = ImageSearchRequest(q="cat")
    assert req.image_filters.size is None


def test_web_result_excludes_raw_in_dump():
    result = WebResult(
        rank=1,
        title="t",
        url="https://example.com",
        domain="example.com",
        provider="search_router",
        raw={"secret": "value"},
    )
    payload = result.model_dump()
    assert "raw" not in payload
    json_payload = result.model_dump_json()
    assert "secret" not in json_payload


def test_web_search_request_strips_whitespace_query():
    """The query is stripped before length validation, so trailing spaces are kept off the wire."""
    req = WebSearchRequest(q="  hello  ")
    assert req.q == "hello"


def test_web_search_request_rejects_whitespace_only_query():
    """Stripped-to-empty query must trigger min_length validation."""
    with pytest.raises(ValidationError):
        WebSearchRequest(q="     ")


def test_image_search_request_rejects_extra_fields_in_filters():
    """``image_filters`` is a strict nested model — unknown keys must be rejected."""
    with pytest.raises(ValidationError):
        ImageSearchRequest.model_validate({"q": "cat", "image_filters": {"foo": "bar"}})


def test_image_search_request_rejects_bad_filter_enum():
    with pytest.raises(ValidationError):
        ImageSearchRequest.model_validate(
            {"q": "cat", "image_filters": {"size": "ginormous"}}
        )


def test_web_search_request_negative_page_rejected():
    with pytest.raises(ValidationError):
        WebSearchRequest(q="ok", page=-1)
