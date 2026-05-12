"""End-to-end tests for the cabinet, auction, and search injection."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

ALICE = {"username": "alice", "password": "alice-pass-1234"}
BOB = {"username": "bob", "password": "bob-pass-5678"}


def _register(client: TestClient, who: dict[str, str]) -> TestClient:
    response = client.post("/auth/register", data=who, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/cabinet"
    return client


def _login(client: TestClient, who: dict[str, str]) -> None:
    response = client.post("/auth/login", data=who, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/cabinet"


def _logout(client: TestClient) -> None:
    response = client.post("/auth/logout", follow_redirects=False)
    assert response.status_code == 303


def _wallet(client: TestClient) -> int:
    """Read the current wallet balance from the cabinet page."""
    response = client.get("/cabinet")
    assert response.status_code == 200
    match = re.search(r'class="wallet-card__value">\s*(\d+)\s*<', response.text)
    assert match, f"wallet not found in cabinet HTML: {response.text[:500]}"
    return int(match.group(1))


@pytest.fixture
def client_alice(client) -> TestClient:
    """A logged-in 'alice' client."""
    _register(client, ALICE)
    return client


def test_register_grants_signup_balance(client):
    _register(client, ALICE)
    assert _wallet(client) == 1000


def test_register_rejects_duplicate_username(client):
    """Duplicate-username failures must look identical to other invalid-
    signup failures so an attacker cannot probe for existing accounts."""
    _register(client, ALICE)
    _logout(client)
    response = client.post("/auth/register", data=ALICE, follow_redirects=False)
    # Generic 400 instead of 409 — same response shape as a short-password
    # rejection, no leaking of which constraint failed.
    assert response.status_code == 400


def test_register_rejects_short_password(client):
    response = client.post(
        "/auth/register",
        data={"username": "shortp", "password": "abc"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_login_with_wrong_password_returns_401(client):
    _register(client, ALICE)
    _logout(client)
    response = client.post(
        "/auth/login",
        data={"username": ALICE["username"], "password": "nope-nope-1234"},
        follow_redirects=False,
    )
    assert response.status_code == 401


def test_cabinet_redirects_anonymous_to_login(client):
    response = client.get("/cabinet", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def _click_from(search_text: str) -> str:
    match = re.search(r'href="(/ads/click/\d+\?r=[^"]+)"', search_text)
    assert match, f"click href not found: {search_text[:600]}"
    return match.group(1)


def test_place_bid_then_ad_renders_on_search(client_alice):
    response = client_alice.post(
        "/cabinet/bid",
        data={
            "query": "купить телефон",
            "title": "Лучшие смартфоны",
            "url": "https://example.com/phones",
            "snippet": "Скидки до 30%",
            "amount": "50",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    search = client_alice.get("/search", params={"q": "купить телефон", "type": "web"})
    assert search.status_code == 200
    body = search.text
    assert "Лучшие смартфоны" in body
    assert "https://example.com/phones" in body
    # CPC: the impression alone doesn't move money — wallet only changes on click.
    assert _wallet(client_alice) == 1000

    click = client_alice.get(_click_from(body), follow_redirects=False)
    assert click.status_code == 302
    # Sole bidder: reserve CPC = bid // 2 = 50 // 2 = 25 deducted on click.
    assert _wallet(client_alice) == 1000 - 25


def test_second_price_charges_runner_up_plus_one(client_alice):
    """Bob outbids Alice on the same query — Bob's ad shows, and Bob pays
    Alice's amount + 1 (second-price) when the click lands."""
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "купить телефон",
            "title": "Alice ad",
            "url": "https://alice.example/phones",
            "amount": "30",
        },
        follow_redirects=False,
    )
    _logout(client_alice)
    _register(client_alice, BOB)
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "купить телефон",
            "title": "Bob ad",
            "url": "https://bob.example/phones",
            "amount": "80",
        },
        follow_redirects=False,
    )
    search = client_alice.get("/search", params={"q": "купить телефон", "type": "web"})
    assert search.status_code == 200
    assert "Bob ad" in search.text
    assert "Alice ad" not in search.text
    # Bob (the current session) hasn't been clicked yet — still at the signup balance.
    assert _wallet(client_alice) == 1000

    click = client_alice.get(_click_from(search.text), follow_redirects=False)
    assert click.status_code == 302
    # Bob pays Alice's bid (30) + 1 = 31, deducted from 1000 on click.
    assert _wallet(client_alice) == 1000 - 31


def test_search_with_no_matching_bid_shows_no_ad(client_alice):
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "купить телефон",
            "title": "Should not appear",
            "url": "https://example.com/x",
            "amount": "20",
        },
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search", params={"q": "случайный запрос вне списка", "type": "web"}
    )
    assert search.status_code == 200
    assert "Should not appear" not in search.text
    # No charge — wallet unchanged.
    assert _wallet(client_alice) == 1000


def test_arbitrary_query_can_be_bid_on(client_alice):
    """No more whitelist — bidder can pick any free-text query."""
    response = client_alice.post(
        "/cabinet/bid",
        data={
            "query": "обои с котами 2026",
            "title": "Cat wallpapers",
            "url": "https://example.com/cats",
            "amount": "5",
            "match_mode": "exact",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    search = client_alice.get(
        "/search", params={"q": "обои с котами 2026", "type": "web"}
    )
    assert search.status_code == 200
    assert "Cat wallpapers" in search.text


def test_phrase_match_fires_when_all_tokens_present(client_alice):
    """Phrase-mode bid on 'pizza dubai' should match any query that contains
    both tokens, in any order, regardless of surrounding words."""
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "pizza dubai",
            "title": "Best pizza in Dubai",
            "url": "https://example.com/pizza",
            "amount": "10",
            "match_mode": "phrase",
        },
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search",
        params={"q": "where to get pizza near dubai marina", "type": "web"},
    )
    assert search.status_code == 200
    assert "Best pizza in Dubai" in search.text


def test_phrase_match_skips_when_token_missing(client_alice):
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "pizza dubai",
            "title": "Phrase ad",
            "url": "https://example.com/pizza",
            "amount": "10",
            "match_mode": "phrase",
        },
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search", params={"q": "pizza near me", "type": "web"}
    )
    assert search.status_code == 200
    assert "Phrase ad" not in search.text
    assert _wallet(client_alice) == 1000


def test_exact_bid_does_not_fire_on_substring_match(client_alice):
    """Exact mode requires the user query to equal the bid query, not contain it."""
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "pizza",
            "title": "Exact-only pizza",
            "url": "https://example.com/pizza",
            "amount": "10",
            "match_mode": "exact",
        },
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search", params={"q": "best pizza near me", "type": "web"}
    )
    assert "Exact-only pizza" not in search.text


def test_exact_and_phrase_bids_can_coexist_per_query(client_alice):
    """A user can hold both an exact and a phrase bid for the same query text —
    they're stored as distinct rows and compete against each other."""
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "пицца",
            "title": "Exact ad",
            "url": "https://example.com/exact",
            "amount": "5",
            "match_mode": "exact",
        },
        follow_redirects=False,
    )
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "пицца",
            "title": "Phrase ad",
            "url": "https://example.com/phrase",
            "amount": "50",
            "match_mode": "phrase",
        },
        follow_redirects=False,
    )
    # User query equals the bid text — both match; phrase outbids exact.
    search = client_alice.get("/search", params={"q": "пицца", "type": "web"})
    assert "Phrase ad" in search.text
    assert "Exact ad" not in search.text


def test_bid_amount_above_wallet_is_rejected(client_alice):
    response = client_alice.post(
        "/cabinet/bid",
        data={
            "query": "купить телефон",
            "title": "Way too expensive",
            "url": "https://example.com/x",
            "amount": "9999",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_delete_bid_removes_ad_from_search(client_alice):
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "pizza near me",
            "title": "Delete me",
            "url": "https://example.com/pizza",
            "amount": "10",
        },
        follow_redirects=False,
    )
    search = client_alice.get("/search", params={"q": "pizza near me", "type": "web"})
    assert "Delete me" in search.text

    client_alice.post(
        "/cabinet/bid/delete",
        data={"query": "pizza near me"},
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search", params={"q": "pizza near me", "type": "web", "_": "fresh"}
    )
    assert "Delete me" not in search.text


def test_ad_shown_on_image_search_too(client_alice):
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "hotel in dubai",
            "title": "Burj Stay",
            "url": "https://example.com/hotel",
            "amount": "5",
        },
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search", params={"q": "hotel in dubai", "type": "images"}
    )
    assert search.status_code == 200
    assert "Burj Stay" in search.text


def test_query_normalization_matches_uppercase(client_alice):
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "hotel in dubai",
            "title": "Stay here",
            "url": "https://example.com/hotel",
            "amount": "10",
        },
        follow_redirects=False,
    )
    search = client_alice.get("/search", params={"q": "HOTEL IN DUBAI", "type": "web"})
    assert "Stay here" in search.text


def test_url_with_bad_scheme_is_rejected(client_alice):
    response = client_alice.post(
        "/cabinet/bid",
        data={
            "query": "купить телефон",
            "title": "javascript ad",
            "url": "javascript:alert(1)",
            "amount": "10",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_cabinet_shows_spent_and_click_stats(client_alice):
    """End-to-end stats: impressions are quoted but free; only the click
    moves money. The cabinet shows impressions, clicks, CTR, and spend."""
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "stat query",
            "title": "Stats ad",
            "url": "https://example.com/stats",
            "amount": "7",
        },
        follow_redirects=False,
    )
    # Two distinct searches → two impressions, neither bills (CPC).
    for _ in range(2):
        search = client_alice.get(
            "/search",
            params={"q": "stat query", "type": "web"},
            headers={"x-request-id": f"req-{_}"},
        )
        assert "Stats ad" in search.text
    assert _wallet(client_alice) == 1000

    # Pull the click-href out of the rendered banner.
    fresh = client_alice.get(
        "/search",
        params={"q": "stat query", "type": "web"},
        headers={"x-request-id": "req-click"},
    )
    match = re.search(r'href="(/ads/click/\d+\?r=req-click)"', fresh.text)
    assert match, fresh.text[:600]
    click_resp = client_alice.get(match.group(1), follow_redirects=False)
    assert click_resp.status_code == 302
    assert click_resp.headers["location"] == "https://example.com/stats"
    # Sole bidder reserve = bid // 2 = 7 // 2 = 3.
    assert _wallet(client_alice) == 1000 - 3

    cabinet = client_alice.get("/cabinet")
    assert cabinet.status_code == 200
    body = cabinet.text
    # Aggregate summary cards are present.
    assert "ads.stat_spent" not in body  # keys should be translated
    assert "Потрачено" in body or "Spent" in body
    # 3 impressions: 2 from the initial loop + 1 from the click-fetch render.
    # 1 click billed 3 coins → spent = 3.
    assert ">3<" in body  # impression count AND spent both render as ">3<"
    assert ">1<" in body  # click count


def test_repeat_click_on_same_impression_is_idempotent(client_alice):
    """Reloading the click URL must replay, not double-bill."""
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "idem query",
            "title": "Idem ad",
            "url": "https://example.com/idem",
            "amount": "5",
        },
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search",
        params={"q": "idem query", "type": "web"},
        headers={"x-request-id": "req-idem"},
    )
    href = _click_from(search.text)
    client_alice.get(href, follow_redirects=False)
    client_alice.get(href, follow_redirects=False)
    client_alice.get(href, follow_redirects=False)
    # Three identical click URLs, one impression → billed exactly once at
    # the sole-bidder reserve (bid // 2 = 5 // 2 = 2).
    assert _wallet(client_alice) == 1000 - 2


def test_click_with_forged_request_id_is_not_billed(client_alice):
    """A click URL pointing at a request_id that never produced an
    impression for this bid must not move money — defends advertisers from
    URL-tampering."""
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "forge query",
            "title": "Forge ad",
            "url": "https://example.com/forge",
            "amount": "5",
        },
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search",
        params={"q": "forge query", "type": "web"},
        headers={"x-request-id": "req-real"},
    )
    href = _click_from(search.text)
    bogus = re.sub(r"r=[^&]+", "r=req-not-a-real-impression", href)
    click_resp = client_alice.get(bogus, follow_redirects=False)
    assert click_resp.status_code == 302
    # Click was logged for CTR honesty but charged=0.
    assert _wallet(client_alice) == 1000


def test_click_on_deleted_bid_redirects_to_home(client_alice):
    client_alice.post(
        "/cabinet/bid",
        data={
            "query": "soon gone",
            "title": "Gone",
            "url": "https://example.com/gone",
            "amount": "2",
        },
        follow_redirects=False,
    )
    search = client_alice.get(
        "/search",
        params={"q": "soon gone", "type": "web"},
        headers={"x-request-id": "req-gone"},
    )
    match = re.search(r'href="(/ads/click/(\d+))\?r=req-gone"', search.text)
    assert match, search.text[:400]
    bid_id = match.group(2)
    client_alice.post(
        "/cabinet/bid/delete",
        data={"query": "soon gone"},
        follow_redirects=False,
    )
    click_resp = client_alice.get(
        f"/ads/click/{bid_id}?r=req-gone", follow_redirects=False
    )
    assert click_resp.status_code == 302
    assert click_resp.headers["location"] == "/"


def test_logout_clears_session(client_alice):
    _logout(client_alice)
    cabinet = client_alice.get("/cabinet", follow_redirects=False)
    assert cabinet.status_code == 303
    assert cabinet.headers["location"] == "/login"
