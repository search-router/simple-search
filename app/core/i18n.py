"""BCP-47 parsing, direction resolution, and translation lookup."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Direction = Literal["ltr", "rtl", "auto"]

RTL_LANGUAGES: frozenset[str] = frozenset({"ar", "he", "fa", "ur", "yi", "dv", "ps"})

_BCP47_RE = re.compile(
    r"^(?P<language>[A-Za-z]{2,3})"
    r"(?:-(?P<script>[A-Za-z]{4}))?"
    r"(?:-(?P<region>[A-Za-z]{2}|\d{3}))?"
    r"(?:-(?P<rest>[A-Za-z0-9-]+))?$"
)


@dataclass(frozen=True)
class ParsedTag:
    language: str
    script: str | None
    region: str | None

    def to_locale(self) -> str:
        parts = [self.language]
        if self.script:
            parts.append(self.script)
        if self.region:
            parts.append(self.region)
        return "-".join(parts)


def parse_bcp47(tag: str | None) -> ParsedTag | None:
    """Parse a BCP-47 tag. Returns ``None`` for empty/invalid input."""
    if not tag:
        return None
    match = _BCP47_RE.match(tag.strip())
    if not match:
        return None
    language = (match.group("language") or "").lower()
    script = match.group("script")
    region = match.group("region")
    return ParsedTag(
        language=language,
        script=script.title() if script else None,
        region=region.upper() if region and region.isalpha() else region,
    )


def resolve_direction(
    language: str | None,
    text: str | None = None,
    requested: Direction = "auto",
) -> Literal["ltr", "rtl"]:
    """Decide whether a piece of UI should render LTR or RTL.

    Order:
    1. Explicit ``ltr`` / ``rtl`` from the request wins.
    2. RTL language code wins next.
    3. First-strong-character heuristic on the text.
    4. Fallback to ``ltr``.
    """
    if requested in ("ltr", "rtl"):
        return requested
    parsed = parse_bcp47(language) if language else None
    if parsed and parsed.language in RTL_LANGUAGES:
        return "rtl"
    if text:
        first = _first_strong(text)
        if first is not None:
            return first
    return "ltr"


def _first_strong(text: str) -> Literal["ltr", "rtl"] | None:
    for ch in text:
        category = unicodedata.bidirectional(ch)
        if category in ("L", "LRE", "LRO", "LRI"):
            return "ltr"
        if category in ("R", "AL", "RLE", "RLO", "RLI"):
            return "rtl"
    return None


DirectionFn = Callable[[str | None], Literal["ltr", "rtl"]]


def _const_ltr(_text: str | None = None) -> Literal["ltr", "rtl"]:
    return "ltr"


def _const_rtl(_text: str | None = None) -> Literal["ltr", "rtl"]:
    return "rtl"


def _first_strong_resolver(text: str | None = None) -> Literal["ltr", "rtl"]:
    if text:
        first = _first_strong(text)
        if first is not None:
            return first
    return "ltr"


@lru_cache(maxsize=128)
def make_direction_resolver(
    language: str | None,
    requested: Direction = "auto",
) -> DirectionFn:
    """Return a fast per-text direction resolver with the language step hoisted.

    Backends produce N results per response, all sharing the same language tag.
    ``parse_bcp47`` (a regex match) running per-result is wasted work — pre-run
    it once here and return a closure that handles only the per-text case.

    The function is pure in ``(language, requested)``, so we memoize the
    resolver itself: every search request hands its language tag to the same
    underlying handler instead of rebuilding the closure.
    """
    if requested in ("ltr", "rtl"):
        return _const_rtl if requested == "rtl" else _const_ltr
    parsed = parse_bcp47(language) if language else None
    if parsed and parsed.language in RTL_LANGUAGES:
        return _const_rtl
    return _first_strong_resolver


# --- translator -------------------------------------------------------------

class Translator:
    """JSON-backed translator with simple ``{var}`` interpolation and fallback."""

    def __init__(self, translations: dict[str, dict[str, str]], default: str = "en") -> None:
        self._translations = translations
        self._default = default

    @classmethod
    def from_directory(cls, directory: Path, default: str = "en") -> Translator:
        translations: dict[str, dict[str, str]] = {}
        for path in sorted(directory.glob("*.json")):
            locale = path.stem
            with path.open("r", encoding="utf-8") as fh:
                translations[locale] = json.load(fh)
        if default not in translations:
            translations.setdefault(default, {})
        return cls(translations, default=default)

    def supported(self) -> list[str]:
        return sorted(self._translations.keys())

    def t(self, key: str, locale: str | None = None, /, **vars: Any) -> str:
        chain = self._fallback_chain(locale)
        for candidate in chain:
            value = self._translations.get(candidate, {}).get(key)
            if value is not None:
                return _format(value, vars)
        _missing_warning(key)
        return key

    def has(self, locale: str, key: str) -> bool:
        return key in self._translations.get(locale, {})

    def _fallback_chain(self, locale: str | None) -> list[str]:
        chain: list[str] = []
        parsed = parse_bcp47(locale) if locale else None
        if parsed:
            full = parsed.to_locale()
            if full and full not in chain:
                chain.append(full)
            base = parsed.language
            if base and base not in chain:
                chain.append(base)
        if self._default not in chain:
            chain.append(self._default)
        return chain


def _format(template: str, variables: dict[str, Any]) -> str:
    if not variables:
        return template
    try:
        return template.format(**variables)
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        # Stray braces, missing positionals, bad format specs, attr/index access — none
        # of which translators reliably anticipate — must never crash a render.
        return template


@lru_cache(maxsize=1024)
def _missing_warning(key: str) -> None:
    logger.warning("translation_missing", extra={"key": key})
