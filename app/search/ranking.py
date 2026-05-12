"""Lightweight rank assignment and dedup helpers."""

from __future__ import annotations

from collections.abc import Iterable

from app.search.schemas import ImageResult, WebResult


def assign_ranks[T: (WebResult, ImageResult)](
    results: Iterable[T], start: int = 1
) -> list[T]:
    out: list[T] = []
    for i, item in enumerate(results, start=start):
        item.rank = i
        out.append(item)
    return out


def dedupe_by_url(results: Iterable[WebResult]) -> list[WebResult]:
    seen: set[str] = set()
    out: list[WebResult] = []
    for item in results:
        key = item.url
        if key:
            if key in seen:
                continue
            seen.add(key)
        out.append(item)
    return out
