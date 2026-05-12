from __future__ import annotations

from app.ads.auction import select_winner
from app.ads.models import Bid


def _bid(bid_id: int, amount: int, created_at: str = "2026-01-01T00:00:00+00:00") -> Bid:
    return Bid(
        id=bid_id,
        user_id=bid_id * 10,
        username=f"user{bid_id}",
        query_normalized="купить телефон",
        query_tokens="купить телефон",
        match_mode="exact",
        title=f"ad {bid_id}",
        url=f"https://example.com/{bid_id}",
        snippet="",
        amount=amount,
        created_at=created_at,
        updated_at=created_at,
    )


def test_select_winner_empty_returns_none():
    assert select_winner([]) is None


def test_select_winner_single_charges_half_of_own_bid():
    bid = _bid(1, 50)
    pick = select_winner([bid])
    assert pick is not None
    winner, charge = pick
    assert winner.id == 1
    # Sole bidder: reserve price = bid // 2.
    assert charge == 25


def test_select_winner_single_floors_half_at_one():
    # bid // 2 == 0 must still bill at least 1 coin per click.
    bid = _bid(1, 1)
    pick = select_winner([bid])
    assert pick is not None
    _, charge = pick
    assert charge == 1


def test_select_winner_second_price_charges_runner_up_plus_one():
    high = _bid(1, 80)
    low = _bid(2, 25)
    pick = select_winner([high, low])
    assert pick is not None
    winner, charge = pick
    assert winner.id == 1
    assert charge == 26


def test_select_winner_charge_is_capped_at_winner_bid():
    # Runner-up + 1 > winner.amount → cap at winner.amount so the model stays truthful.
    high = _bid(1, 50)
    almost = _bid(2, 50)  # tie -> first stays, but feed manually
    pick = select_winner([high, almost])
    assert pick is not None
    winner, charge = pick
    assert winner.id == 1
    # runner_up=50 + 1 = 51, capped to 50.
    assert charge == 50


def test_select_winner_respects_tie_ordering_provided_by_caller():
    # Caller guarantees amount DESC, created_at ASC. The first bid in the list wins.
    older = _bid(1, 60, "2025-01-01T00:00:00+00:00")
    newer = _bid(2, 60, "2026-01-01T00:00:00+00:00")
    pick = select_winner([older, newer])
    assert pick is not None
    winner, _ = pick
    assert winner.id == 1
