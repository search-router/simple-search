"""Pure, side-effect-free auction logic.

Caller pre-filters candidates so every bid here is paid-affordable by its
owner. Pricing is second-price (GSP): winner's CPC is
``runner_up_amount + 1``. When the winner is alone, there is no
second-price signal, so the reserve price is half the winner's own bid
(rounded down, never below ``1``) — that keeps a sole bidder from
pocketing infinite ranking for ``1`` coin a click while still being
cheaper than naming their own price.

The returned price is the *cost-per-click* quoted on the impression; the
advertiser is not billed until a click actually happens (see
``AdsStore.record_click`` for the debit path).
"""

from __future__ import annotations

from app.ads.models import Bid


def select_winner(candidates: list[Bid]) -> tuple[Bid, int] | None:
    """Return ``(winner, cpc_price)`` or ``None`` if no candidates.

    Candidates must arrive sorted by ``amount DESC, created_at ASC`` so the
    older bid wins on ties. With two or more candidates the CPC is
    ``min(winner.amount, runner_up.amount + 1)``; with a single candidate
    it is ``max(1, winner.amount // 2)``. Either way the price is capped
    at the winner's bid so the model stays truthful."""
    if not candidates:
        return None
    winner = candidates[0]
    if len(candidates) == 1:
        return winner, max(1, winner.amount // 2)
    runner_up = candidates[1]
    charge = min(winner.amount, runner_up.amount + 1)
    return winner, max(charge, 1)
