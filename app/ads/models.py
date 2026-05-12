"""Pydantic models for the ads layer."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

MatchMode = Literal["exact", "phrase"]


def tokenize_query(text: str) -> list[str]:
    """Split text into normalized word tokens.

    Matches the same NFKC + lower used by ``normalize_query`` so that
    bid tokens compare apples-to-apples with user-query tokens at
    auction time. Empty/whitespace-only input returns ``[]``."""
    if not text:
        return []
    norm = unicodedata.normalize("NFKC", text).lower()
    return _TOKEN_RE.findall(norm)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=False)


class User(_Strict):
    id: int
    username: str
    wallet: int
    created_at: str


class Bid(_Strict):
    id: int
    user_id: int
    username: str
    query_normalized: str
    query_tokens: str
    match_mode: MatchMode
    title: str
    url: str
    snippet: str = ""
    amount: int = Field(ge=1)
    created_at: str
    updated_at: str


class AdCreative(_Strict):
    """User-supplied form payload for a bid placement."""

    title: Annotated[str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)]
    url: Annotated[str, StringConstraints(min_length=8, max_length=400, strip_whitespace=True)]
    snippet: Annotated[str, StringConstraints(max_length=240, strip_whitespace=True)] = ""
    amount: int = Field(ge=1, le=1_000_000)
    match_mode: MatchMode = "exact"


class AuctionWinner(_Strict):
    bid_id: int
    advertiser: str
    title: str
    url: str
    snippet: str
    bid_amount: int
    charged: int
    match_mode: MatchMode
