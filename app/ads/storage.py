"""SQLite-backed persistence for the ads layer.

A single shared ``aiosqlite.Connection`` is owned by the FastAPI lifespan.
SQLite is a single-writer store; a "pool" of writers only buys you
``database is locked`` errors. The aiosqlite connection serializes its
own queue, so concurrent /search auctions take turns cleanly.

Datetimes are stored as ISO-8601 UTC strings to sidestep the Py3.12
``datetime`` adapter ``DeprecationWarning`` that ``filterwarnings=error``
would promote to a failure.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.ads.models import Bid, MatchMode, User

# Phrase bids are scanned in full on every ``/search`` auction. The pool
# changes only when an advertiser places/edits/deletes a bid, so a short
# in-process TTL absorbs the per-request cost without making the cabinet
# feel stale. Wallet-driven shifts (a click depletes a wallet below its bid
# amount) propagate after at most this window — that's a fairness lag, not
# a billing one (CPC still pays at most ``wallet``).
_PHRASE_POOL_TTL_SECONDS = 5.0

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        wallet INTEGER NOT NULL CHECK (wallet >= 0),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bids (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        query_normalized TEXT NOT NULL,
        query_tokens TEXT NOT NULL DEFAULT '',
        match_mode TEXT NOT NULL DEFAULT 'exact'
            CHECK (match_mode IN ('exact', 'phrase')),
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        snippet TEXT NOT NULL DEFAULT '',
        amount INTEGER NOT NULL CHECK (amount >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id, query_normalized, match_mode)
    )
    """,
    "CREATE INDEX IF NOT EXISTS bids_exact_by_query "
    "ON bids(query_normalized, amount DESC) WHERE match_mode = 'exact'",
    "CREATE INDEX IF NOT EXISTS bids_phrase_by_amount "
    "ON bids(amount DESC) WHERE match_mode = 'phrase'",
    """
    CREATE TABLE IF NOT EXISTS impressions (
        id INTEGER PRIMARY KEY,
        bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        query_normalized TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE,
        charged INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS impressions_by_user_bid ON impressions(user_id, bid_id)",
    """
    CREATE TABLE IF NOT EXISTS clicks (
        id INTEGER PRIMARY KEY,
        bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        request_id TEXT,
        charged INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS clicks_by_user_bid ON clicks(user_id, bid_id)",
    # Pay-per-click idempotency: a second click hit on the same impression
    # (same bid_id+request_id) must replay the first click, not bill twice.
    "CREATE UNIQUE INDEX IF NOT EXISTS clicks_unique_by_request "
    "ON clicks(bid_id, request_id) WHERE request_id IS NOT NULL",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_user(row: aiosqlite.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        wallet=row["wallet"],
        created_at=row["created_at"],
    )


def _row_to_bid(row: aiosqlite.Row) -> Bid:
    return Bid(
        id=row["id"],
        user_id=row["user_id"],
        username=row["username"],
        query_normalized=row["query_normalized"],
        query_tokens=row["query_tokens"] or "",
        match_mode=row["match_mode"],
        title=row["title"],
        url=row["url"],
        snippet=row["snippet"] or "",
        amount=row["amount"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_BID_COLUMNS = (
    "b.id, b.user_id, u.username, b.query_normalized, b.query_tokens, "
    "b.match_mode, b.title, b.url, b.snippet, b.amount, "
    "b.created_at, b.updated_at"
)


class AdsStore:
    """Thin async wrapper around a shared aiosqlite connection."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._phrase_pool: list[tuple[Bid, frozenset[str]]] | None = None
        self._phrase_pool_expires_at: float = 0.0

    @classmethod
    async def open(cls, db_path: str) -> AdsStore:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        return cls(conn)

    async def init_schema(self) -> None:
        # Upgrade older deployments in place — earlier versions had a
        # 2-column UNIQUE and no match_mode/query_tokens. CREATE TABLE
        # IF NOT EXISTS won't change an existing table's constraints,
        # so we rebuild when we detect the old shape.
        await self._migrate_bids_table()
        for stmt in _SCHEMA:
            await self._conn.execute(stmt)
        await self._migrate_clicks_to_cpc()
        await self._conn.commit()

    async def _migrate_clicks_to_cpc(self) -> None:
        """Pay-per-click migration: add ``clicks.charged`` and backfill from
        matching impressions so historical click rows keep their spend.

        Pre-CPC builds debited on impression and left clicks unpriced. After
        the switch, ``spent`` aggregates ``clicks.charged`` — without this
        backfill, the cabinet would suddenly show ``spent = 0``."""
        async with self._conn.execute("PRAGMA table_info('clicks')") as cur:
            cols = {r["name"] for r in await cur.fetchall()}
        if "charged" not in cols:
            await self._conn.execute(
                "ALTER TABLE clicks ADD COLUMN charged INTEGER NOT NULL DEFAULT 0"
            )
            await self._conn.execute(
                """
                UPDATE clicks
                SET charged = COALESCE(
                    (SELECT i.charged FROM impressions i
                     WHERE i.bid_id = clicks.bid_id
                       AND i.request_id = clicks.request_id), 0)
                WHERE request_id IS NOT NULL
                """
            )

    async def _migrate_bids_table(self) -> None:
        async with self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='bids'"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return  # fresh DB; CREATE TABLE will set up the latest schema
        existing_sql = (row["sql"] or "").lower()
        if "unique(user_id, query_normalized, match_mode)" in existing_sql:
            return  # already on the new shape

        async with self._conn.execute("PRAGMA table_info('bids')") as cur:
            old_cols = {r["name"] for r in await cur.fetchall()}
        tokens_expr = "query_tokens" if "query_tokens" in old_cols else "''"
        mode_expr = "match_mode" if "match_mode" in old_cols else "'exact'"

        # Rebuild dance recommended by the SQLite docs: copy into a new
        # table with the desired constraints, then swap names. All legacy
        # rows default to 'exact' mode; query_tokens stays empty (only
        # phrase bids need a non-empty token list).
        await self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            await self._conn.execute("BEGIN")
            await self._conn.execute(
                """
                CREATE TABLE bids_new (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    query_normalized TEXT NOT NULL,
                    query_tokens TEXT NOT NULL DEFAULT '',
                    match_mode TEXT NOT NULL DEFAULT 'exact'
                        CHECK (match_mode IN ('exact', 'phrase')),
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    snippet TEXT NOT NULL DEFAULT '',
                    amount INTEGER NOT NULL CHECK (amount >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, query_normalized, match_mode)
                )
                """
            )
            await self._conn.execute(
                f"""
                INSERT INTO bids_new(
                    id, user_id, query_normalized, query_tokens, match_mode,
                    title, url, snippet, amount, created_at, updated_at
                )
                SELECT
                    id, user_id, query_normalized,
                    COALESCE({tokens_expr}, ''),
                    COALESCE({mode_expr}, 'exact'),
                    title, url, snippet, amount, created_at, updated_at
                FROM bids
                """
            )
            await self._conn.execute("DROP TABLE bids")
            await self._conn.execute("ALTER TABLE bids_new RENAME TO bids")
            await self._conn.execute("COMMIT")
        except Exception:
            await self._conn.execute("ROLLBACK")
            raise
        finally:
            await self._conn.execute("PRAGMA foreign_keys = ON")

    async def aclose(self) -> None:
        await self._conn.close()

    # ----- users -----

    async def create_user(self, username: str, password_hash: str, wallet: int) -> User:
        now = _now_iso()
        cursor = await self._conn.execute(
            "INSERT INTO users(username, password_hash, wallet, created_at) "
            "VALUES (?, ?, ?, ?)",
            (username, password_hash, wallet, now),
        )
        await self._conn.commit()
        user_id = cursor.lastrowid
        return User(id=int(user_id or 0), username=username, wallet=wallet, created_at=now)

    async def get_user_by_username(self, username: str) -> tuple[User, str] | None:
        async with self._conn.execute(
            "SELECT id, username, password_hash, wallet, created_at "
            "FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        user = User(
            id=row["id"],
            username=row["username"],
            wallet=row["wallet"],
            created_at=row["created_at"],
        )
        return user, row["password_hash"]

    async def get_user(self, user_id: int) -> User | None:
        async with self._conn.execute(
            "SELECT id, username, wallet, created_at FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_user(row) if row else None

    # ----- bids -----

    def _invalidate_phrase_pool(self) -> None:
        """Drop the cached phrase-bid pool so the next auction re-reads SQLite.

        Called from every write that can change pool composition (place/edit/
        delete a bid). Wallet changes are not invalidated here — the TTL
        absorbs that and any over-pick is harmless (CPC clamps at wallet).
        """
        self._phrase_pool = None
        self._phrase_pool_expires_at = 0.0

    async def upsert_bid(
        self,
        *,
        user_id: int,
        query_normalized: str,
        query_tokens: str,
        match_mode: MatchMode,
        title: str,
        url: str,
        snippet: str,
        amount: int,
    ) -> Bid:
        now = _now_iso()
        await self._conn.execute(
            """
            INSERT INTO bids(
                user_id, query_normalized, query_tokens, match_mode,
                title, url, snippet, amount,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, query_normalized, match_mode) DO UPDATE SET
                query_tokens = excluded.query_tokens,
                title = excluded.title,
                url = excluded.url,
                snippet = excluded.snippet,
                amount = excluded.amount,
                updated_at = excluded.updated_at
            """,
            (
                user_id, query_normalized, query_tokens, match_mode,
                title, url, snippet, amount, now, now,
            ),
        )
        await self._conn.commit()
        if match_mode == "phrase":
            self._invalidate_phrase_pool()
        bid = await self.get_user_bid(user_id, query_normalized, match_mode)
        assert bid is not None
        return bid

    async def get_user_bid(
        self, user_id: int, query_normalized: str, match_mode: MatchMode
    ) -> Bid | None:
        async with self._conn.execute(
            f"""
            SELECT {_BID_COLUMNS}
            FROM bids b JOIN users u ON u.id = b.user_id
            WHERE b.user_id = ? AND b.query_normalized = ? AND b.match_mode = ?
            """,
            (user_id, query_normalized, match_mode),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_bid(row) if row else None

    async def list_user_bids(self, user_id: int) -> list[Bid]:
        async with self._conn.execute(
            f"""
            SELECT {_BID_COLUMNS}
            FROM bids b JOIN users u ON u.id = b.user_id
            WHERE b.user_id = ?
            ORDER BY b.created_at ASC
            """,
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_bid(row) for row in rows]

    async def delete_bid(
        self, user_id: int, query_normalized: str, match_mode: MatchMode
    ) -> bool:
        cursor = await self._conn.execute(
            "DELETE FROM bids "
            "WHERE user_id = ? AND query_normalized = ? AND match_mode = ?",
            (user_id, query_normalized, match_mode),
        )
        await self._conn.commit()
        deleted = (cursor.rowcount or 0) > 0
        if deleted and match_mode == "phrase":
            self._invalidate_phrase_pool()
        return deleted

    async def list_exact_candidates(
        self, query_normalized: str, limit: int = 10
    ) -> list[Bid]:
        async with self._conn.execute(
            f"""
            SELECT {_BID_COLUMNS}
            FROM bids b JOIN users u ON u.id = b.user_id
            WHERE b.match_mode = 'exact'
              AND b.query_normalized = ?
              AND u.wallet >= b.amount
            ORDER BY b.amount DESC, b.created_at ASC
            LIMIT ?
            """,
            (query_normalized, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_bid(row) for row in rows]

    async def list_phrase_bids(self) -> list[Bid]:
        """Return all paid-affordable phrase bids ordered by amount DESC.

        The service layer filters them by token-subset against the user
        query. The table scan is acceptable for the demo scale; if the
        catalog grows, replace with a token-index lookup."""
        async with self._conn.execute(
            f"""
            SELECT {_BID_COLUMNS}
            FROM bids b JOIN users u ON u.id = b.user_id
            WHERE b.match_mode = 'phrase' AND u.wallet >= b.amount
            ORDER BY b.amount DESC, b.created_at ASC
            """
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_bid(row) for row in rows]

    async def list_phrase_bids_with_tokens(
        self,
    ) -> list[tuple[Bid, frozenset[str]]]:
        """Cached pool of ``(bid, token_set)`` pairs for the auction filter.

        Same data as :meth:`list_phrase_bids` but cached in-process for
        :data:`_PHRASE_POOL_TTL_SECONDS` and with each bid's token list
        pre-materialized as a ``frozenset`` — the auction needs sets for
        ``issubset`` checks, so we'd otherwise rebuild them per request.
        Invalidated by :meth:`upsert_bid` / :meth:`delete_bid`.
        """
        now = time.monotonic()
        cached = self._phrase_pool
        if cached is not None and now < self._phrase_pool_expires_at:
            return cached
        bids = await self.list_phrase_bids()
        snapshot = [(b, frozenset(b.query_tokens.split())) for b in bids]
        self._phrase_pool = snapshot
        self._phrase_pool_expires_at = now + _PHRASE_POOL_TTL_SECONDS
        return snapshot

    async def record_impression(
        self,
        *,
        bid_id: int,
        user_id: int,
        price: int,
        request_id: str,
        query_normalized: str,
    ) -> str:
        """Record an impression with the auction's CPC quote — no debit yet.

        Wallets only move on click (see :meth:`record_click`). ``charged``
        on the impression row stores the price-per-click reserved by the
        auction; if a click comes in for this ``request_id``, that's the
        amount it pays.

        Returns ``"recorded"`` on success or ``"duplicate"`` if this
        ``request_id`` already produced an impression (idempotency on
        browser refresh: the same ad is shown again at the same price).
        """
        now = _now_iso()
        try:
            await self._conn.execute("BEGIN IMMEDIATE")
            async with self._conn.execute(
                "SELECT id FROM impressions WHERE request_id = ?",
                (request_id,),
            ) as cur:
                existing = await cur.fetchone()
            if existing is not None:
                await self._conn.rollback()
                return "duplicate"
            await self._conn.execute(
                "INSERT INTO impressions(bid_id, user_id, query_normalized, "
                "request_id, charged, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (bid_id, user_id, query_normalized, request_id, price, now),
            )
            await self._conn.commit()
            return "recorded"
        except Exception:
            await self._conn.rollback()
            raise

    # ----- clicks & stats -----

    async def get_bid(self, bid_id: int) -> Bid | None:
        async with self._conn.execute(
            f"""
            SELECT {_BID_COLUMNS}
            FROM bids b JOIN users u ON u.id = b.user_id
            WHERE b.id = ?
            """,
            (bid_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_bid(row) if row else None

    async def record_click(
        self, bid_id: int, request_id: str | None
    ) -> tuple[bool, int]:
        """Atomically bill a click and persist it. Returns ``(stored, charged)``.

        Billing rules:

        * The CPC price is read from the originating impression row
          (``impressions.charged`` for ``(bid_id, request_id)``). A click
          without a matching impression — e.g. ``r=`` missing or forged —
          is logged with ``charged = 0`` so we don't bill on unverifiable
          clicks.
        * Duplicate clicks for the same ``(bid_id, request_id)`` replay
          the original charge (idempotent — see the partial unique index
          ``clicks_unique_by_request``).
        * If the advertiser's wallet can no longer cover the CPC price,
          the click is still recorded but ``charged`` falls to ``0``.
          That keeps CTR honest without lying about money that wasn't
          actually moved.
        """
        async with self._conn.execute(
            "SELECT user_id FROM bids WHERE id = ?", (bid_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False, 0
        user_id = int(row["user_id"])
        now = _now_iso()
        try:
            await self._conn.execute("BEGIN IMMEDIATE")
            if request_id is not None:
                async with self._conn.execute(
                    "SELECT charged FROM clicks "
                    "WHERE bid_id = ? AND request_id = ?",
                    (bid_id, request_id),
                ) as cur:
                    existing = await cur.fetchone()
                if existing is not None:
                    await self._conn.rollback()
                    return True, int(existing["charged"])
                async with self._conn.execute(
                    "SELECT charged FROM impressions "
                    "WHERE bid_id = ? AND request_id = ?",
                    (bid_id, request_id),
                ) as cur:
                    impression = await cur.fetchone()
                price = int(impression["charged"]) if impression is not None else 0
            else:
                price = 0

            charged = 0
            if price > 0:
                cursor = await self._conn.execute(
                    "UPDATE users SET wallet = wallet - ? "
                    "WHERE id = ? AND wallet >= ?",
                    (price, user_id, price),
                )
                if (cursor.rowcount or 0) == 1:
                    charged = price

            await self._conn.execute(
                "INSERT INTO clicks(bid_id, user_id, request_id, charged, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (bid_id, user_id, request_id, charged, now),
            )
            await self._conn.commit()
            return True, charged
        except Exception:
            await self._conn.rollback()
            raise

    async def user_stats_summary(self, user_id: int) -> dict[str, int]:
        async with self._conn.execute(
            "SELECT COUNT(*) AS n FROM impressions WHERE user_id = ?",
            (user_id,),
        ) as cur:
            imp_row = await cur.fetchone()
        async with self._conn.execute(
            # CPC: spend comes from actually billed clicks, not impressions.
            "SELECT COUNT(*) AS n, COALESCE(SUM(charged), 0) AS spent "
            "FROM clicks WHERE user_id = ?",
            (user_id,),
        ) as cur:
            click_row = await cur.fetchone()
        return {
            "impressions": int(imp_row["n"] if imp_row else 0),
            "spent": int(click_row["spent"] if click_row else 0),
            "clicks": int(click_row["n"] if click_row else 0),
        }

    async def user_bid_stats(self, user_id: int) -> dict[int, dict[str, int]]:
        """Per-bid aggregates of impressions, total spent, and clicks."""
        stats: dict[int, dict[str, int]] = {}
        async with self._conn.execute(
            "SELECT bid_id, COUNT(*) AS n "
            "FROM impressions WHERE user_id = ? GROUP BY bid_id",
            (user_id,),
        ) as cur:
            async for row in cur:
                stats[int(row["bid_id"])] = {
                    "impressions": int(row["n"]),
                    "spent": 0,
                    "clicks": 0,
                }
        async with self._conn.execute(
            "SELECT bid_id, COUNT(*) AS n, COALESCE(SUM(charged), 0) AS spent "
            "FROM clicks WHERE user_id = ? GROUP BY bid_id",
            (user_id,),
        ) as cur:
            async for row in cur:
                entry = stats.setdefault(
                    int(row["bid_id"]),
                    {"impressions": 0, "spent": 0, "clicks": 0},
                )
                entry["clicks"] = int(row["n"])
                entry["spent"] = int(row["spent"])
        return stats

    async def get_impression(self, request_id: str) -> dict[str, Any] | None:
        async with self._conn.execute(
            """
            SELECT i.bid_id, i.user_id, i.charged, b.title, b.url, b.snippet,
                   b.amount, b.match_mode, u.username
            FROM impressions i
            JOIN bids b ON b.id = i.bid_id
            JOIN users u ON u.id = i.user_id
            WHERE i.request_id = ?
            """,
            (request_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "bid_id": row["bid_id"],
            "user_id": row["user_id"],
            "charged": row["charged"],
            "title": row["title"],
            "url": row["url"],
            "snippet": row["snippet"] or "",
            "amount": row["amount"],
            "match_mode": row["match_mode"],
            "username": row["username"],
        }
