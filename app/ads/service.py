"""Orchestrates registration, login, bid placement, and the auction.

Pure auction logic lives in :mod:`app.ads.auction`. This module owns the
I/O: SQLite reads, impression recording, and click billing.
"""

from __future__ import annotations

import logging
import unicodedata
from urllib.parse import urlparse

from app.ads.auction import select_winner
from app.ads.auth import hash_password, verify_password
from app.ads.errors import (
    InsufficientFundsError,
    InvalidBidError,
    InvalidCredentialsError,
    UsernameTakenError,
)
from app.ads.models import (
    USERNAME_RE,
    AdCreative,
    AuctionWinner,
    Bid,
    MatchMode,
    User,
    tokenize_query,
)
from app.ads.storage import AdsStore
from app.core.config import AdsConfig

_MIN_USERNAME_LEN = 3
_MAX_USERNAME_LEN = 32
_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 128
_MAX_QUERY_LEN = 200
_AUCTION_CANDIDATE_LIMIT = 20


def normalize_query(q: str) -> str:
    """Match the same NFKC + lower + strip used at search time."""
    if not q:
        return ""
    return unicodedata.normalize("NFKC", q).strip().lower()


def _validate_credentials(username: str, password: str) -> str:
    name = (username or "").strip()
    if not (_MIN_USERNAME_LEN <= len(name) <= _MAX_USERNAME_LEN):
        raise InvalidCredentialsError(
            f"Username length must be {_MIN_USERNAME_LEN}-{_MAX_USERNAME_LEN} chars"
        )
    if not USERNAME_RE.match(name):
        raise InvalidCredentialsError(
            "Username must contain only letters, digits, '_', '.', '-'"
        )
    if not (_MIN_PASSWORD_LEN <= len(password or "") <= _MAX_PASSWORD_LEN):
        raise InvalidCredentialsError(
            f"Password length must be {_MIN_PASSWORD_LEN}-{_MAX_PASSWORD_LEN} chars"
        )
    return name


class AdsService:
    def __init__(self, store: AdsStore, config: AdsConfig, logger: logging.Logger) -> None:
        self._store = store
        self._config = config
        self._log = logger

    @property
    def suggested_queries(self) -> list[str]:
        """Optional example queries surfaced to the user as suggestions.

        No longer gates anything — advertisers can target any text."""
        return list(self._config.queries)

    @property
    def signup_balance(self) -> int:
        return self._config.signup_balance

    # ----- auth -----

    async def register(self, username: str, password: str) -> User:
        name = _validate_credentials(username, password)
        existing = await self._store.get_user_by_username(name)
        if existing is not None:
            raise UsernameTakenError()
        password_hash = hash_password(password)
        user = await self._store.create_user(
            username=name,
            password_hash=password_hash,
            wallet=self._config.signup_balance,
        )
        self._log.info(
            "user_registered",
            extra={"user_id": user.id, "wallet": user.wallet},
        )
        return user

    async def login(self, username: str, password: str) -> User:
        if not username or not password:
            raise InvalidCredentialsError()
        record = await self._store.get_user_by_username(username.strip())
        if record is None:
            self._log.info("user_login_failed", extra={"reason": "no_user"})
            raise InvalidCredentialsError()
        user, password_hash = record
        if not verify_password(password, password_hash):
            self._log.info(
                "user_login_failed",
                extra={"user_id": user.id, "reason": "bad_password"},
            )
            raise InvalidCredentialsError()
        self._log.info("user_login_success", extra={"user_id": user.id})
        return user

    async def get_user(self, user_id: int) -> User | None:
        return await self._store.get_user(user_id)

    # ----- bids -----

    async def place_bid(
        self,
        user: User,
        query: str,
        payload: AdCreative,
    ) -> Bid:
        normalized = normalize_query(query)
        if not normalized:
            raise InvalidBidError("Query must not be empty")
        if len(normalized) > _MAX_QUERY_LEN:
            raise InvalidBidError(
                f"Query is too long (max {_MAX_QUERY_LEN} characters)"
            )
        tokens = tokenize_query(normalized)
        if payload.match_mode == "phrase" and not tokens:
            raise InvalidBidError(
                "Phrase match requires at least one word in the query"
            )
        if payload.amount > user.wallet:
            raise InsufficientFundsError(
                f"Bid amount ({payload.amount}) exceeds wallet balance ({user.wallet})"
            )
        parsed = urlparse(payload.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InvalidBidError("URL must start with http:// or https://")
        bid = await self._store.upsert_bid(
            user_id=user.id,
            query_normalized=normalized,
            query_tokens=" ".join(tokens),
            match_mode=payload.match_mode,
            title=payload.title,
            url=payload.url,
            snippet=payload.snippet,
            amount=payload.amount,
        )
        self._log.info(
            "bid_upserted",
            extra={
                "user_id": user.id,
                "bid_id": bid.id,
                "query_normalized": normalized,
                "match_mode": payload.match_mode,
                "amount": payload.amount,
            },
        )
        return bid

    async def delete_bid(self, user: User, query: str, match_mode: MatchMode) -> bool:
        normalized = normalize_query(query)
        deleted = await self._store.delete_bid(user.id, normalized, match_mode)
        if deleted:
            self._log.info(
                "bid_deleted",
                extra={
                    "user_id": user.id,
                    "query_normalized": normalized,
                    "match_mode": match_mode,
                },
            )
        return deleted

    async def list_user_bids(self, user_id: int) -> list[Bid]:
        return await self._store.list_user_bids(user_id)

    async def user_stats_summary(self, user_id: int) -> dict[str, int]:
        return await self._store.user_stats_summary(user_id)

    async def user_bid_stats(self, user_id: int) -> dict[int, dict[str, int]]:
        return await self._store.user_bid_stats(user_id)

    async def record_click(self, bid_id: int, request_id: str | None) -> Bid | None:
        """Record a click for ``bid_id`` and return the bid (for redirect).

        Billing happens here, not at impression time (CPC model). The store
        looks up the price reserved by the auction on the originating
        impression and debits the advertiser's wallet by that amount; the
        call is idempotent on ``(bid_id, request_id)``.

        Returns ``None`` if the bid no longer exists (e.g., deleted between
        impression and click) — caller should fall back to a safe redirect."""
        bid = await self._store.get_bid(bid_id)
        if bid is None:
            return None
        stored, charged = await self._store.record_click(bid_id, request_id)
        if not stored:
            return None
        self._log.info(
            "ad_click",
            extra={
                "bid_id": bid_id,
                "advertiser_id": bid.user_id,
                "request_id": request_id,
                "charged": charged,
            },
        )
        return bid

    # ----- auction -----

    async def run_auction(self, query: str, request_id: str) -> AuctionWinner | None:
        normalized = normalize_query(query)
        if not normalized:
            return None

        # Idempotency: if this request_id already recorded an impression,
        # replay the same ad at the same quoted price so a browser refresh
        # doesn't create a second auction.
        replayed = await self._store.get_impression(request_id)
        if replayed is not None:
            return AuctionWinner(
                bid_id=int(replayed["bid_id"]),
                advertiser=str(replayed["username"]),
                title=str(replayed["title"]),
                url=str(replayed["url"]),
                snippet=str(replayed["snippet"]),
                bid_amount=int(replayed["amount"]),
                charged=int(replayed["charged"]),
                match_mode=replayed["match_mode"],
            )

        candidates = await self._collect_candidates(normalized)
        pick = select_winner(candidates)
        if pick is None:
            self._log.debug(
                "auction_skipped",
                extra={"query_normalized": normalized, "reason": "no_candidates"},
            )
            return None
        winner, price = pick
        outcome = await self._store.record_impression(
            bid_id=winner.id,
            user_id=winner.user_id,
            price=price,
            request_id=request_id,
            query_normalized=normalized,
        )
        if outcome == "duplicate":
            # Concurrent request already recorded for this id — replay it.
            replayed = await self._store.get_impression(request_id)
            if replayed is None:
                return None
            return AuctionWinner(
                bid_id=int(replayed["bid_id"]),
                advertiser=str(replayed["username"]),
                title=str(replayed["title"]),
                url=str(replayed["url"]),
                snippet=str(replayed["snippet"]),
                bid_amount=int(replayed["amount"]),
                charged=int(replayed["charged"]),
                match_mode=replayed["match_mode"],
            )
        self._log.info(
            "auction_won",
            extra={
                "user_id": winner.user_id,
                "bid_id": winner.id,
                "query_normalized": normalized,
                "match_mode": winner.match_mode,
                "price": price,
            },
        )
        return AuctionWinner(
            bid_id=winner.id,
            advertiser=winner.username,
            title=winner.title,
            url=winner.url,
            snippet=winner.snippet,
            bid_amount=winner.amount,
            charged=price,
            match_mode=winner.match_mode,
        )

    async def _collect_candidates(self, normalized_query: str) -> list[Bid]:
        """Union exact-match and phrase-match candidates, sorted by amount.

        Exact bids are looked up by ``query_normalized = ?`` (indexed).
        Phrase bids come from a TTL-cached pool of ``(bid, token_set)`` pairs
        so the per-request work is one ``issubset`` check, not a SQLite scan
        and a token-string split, per bid.
        """
        user_tokens = frozenset(tokenize_query(normalized_query))

        exact = await self._store.list_exact_candidates(
            normalized_query, limit=_AUCTION_CANDIDATE_LIMIT
        )

        phrase_pool = await self._store.list_phrase_bids_with_tokens()
        phrase_matches: list[Bid] = []
        for bid, bid_tokens in phrase_pool:
            if bid_tokens and bid_tokens.issubset(user_tokens):
                phrase_matches.append(bid)
                if len(phrase_matches) >= _AUCTION_CANDIDATE_LIMIT:
                    break

        combined = exact + phrase_matches
        # Caller depends on amount DESC, created_at ASC for second-price math.
        combined.sort(key=lambda b: (-b.amount, b.created_at, b.id))
        return combined[: _AUCTION_CANDIDATE_LIMIT * 2]
