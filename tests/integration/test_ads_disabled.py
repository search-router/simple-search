"""Ensure the search service is fully usable with ``ads.enabled=false``."""

from __future__ import annotations


def test_search_still_works_without_ads(client_no_ads):
    response = client_no_ads.get(
        "/search", params={"q": "купить телефон", "type": "web"}
    )
    assert response.status_code == 200
    # No ad partial — the banner element class never appears.
    assert "ad-banner" not in response.text


def test_cabinet_returns_404_when_ads_disabled(client_no_ads):
    response = client_no_ads.get("/cabinet", follow_redirects=False)
    assert response.status_code == 404


def test_login_returns_404_when_ads_disabled(client_no_ads):
    response = client_no_ads.get("/login", follow_redirects=False)
    assert response.status_code == 404


def test_home_page_renders_without_session_header(client_no_ads):
    response = client_no_ads.get("/")
    assert response.status_code == 200
    # No nav link to /login or /cabinet when ads are off.
    assert 'href="/login"' not in response.text
    assert 'href="/cabinet"' not in response.text
