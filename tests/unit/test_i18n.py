from __future__ import annotations

from pathlib import Path

import pytest

from app.core.i18n import Translator, parse_bcp47, resolve_direction

TRANSLATIONS = Path(__file__).resolve().parents[2] / "app" / "ui" / "translations"


def test_parse_bcp47_basic():
    tag = parse_bcp47("ar-SA")
    assert tag is not None
    assert tag.language == "ar"
    assert tag.region == "SA"
    assert tag.script is None


def test_parse_bcp47_invalid():
    assert parse_bcp47("") is None
    assert parse_bcp47("###") is None


def test_parse_bcp47_full_with_script():
    tag = parse_bcp47("zh-Hant-HK")
    assert tag is not None
    assert tag.language == "zh"
    assert tag.script == "Hant"
    assert tag.region == "HK"


@pytest.mark.parametrize(
    ("language", "text", "requested", "expected"),
    [
        ("en", None, "auto", "ltr"),
        ("ar", None, "auto", "rtl"),
        ("he", None, "auto", "rtl"),
        ("ar", None, "ltr", "ltr"),  # explicit overrides
        ("en", "hello مرحبا", "auto", "ltr"),  # first-strong is L
        (None, "مرحبا hello", "auto", "rtl"),  # first-strong is R
        (None, None, "auto", "ltr"),  # nothing → ltr
    ],
)
def test_resolve_direction(language, text, requested, expected):
    assert resolve_direction(language, text, requested) == expected


def test_translator_fallback_chain():
    translator = Translator.from_directory(TRANSLATIONS, default="en")
    assert translator.t("nav.home", "en-US") == "Home"
    assert translator.t("nav.home", "ru") == "Главная"
    assert translator.t("nav.home", "ar-SA") == "الرئيسية"
    # Missing locale falls back to default English.
    assert translator.t("nav.home", "fr-FR") == "Home"


def test_translator_interpolation():
    translator = Translator.from_directory(TRANSLATIONS, default="en")
    rendered = translator.t("pagination.page", "en", page=3)
    assert "3" in rendered


def test_translator_missing_key_returns_key():
    translator = Translator.from_directory(TRANSLATIONS, default="en")
    assert translator.t("nope.missing.key") == "nope.missing.key"


def test_translator_interpolation_keeps_template_on_missing_var():
    # ``{name}`` would raise KeyError; we must return the raw template instead.
    t = Translator({"en": {"hello": "Hello {name}"}}, default="en")
    assert t.t("hello", "en") == "Hello {name}"


def test_translator_full_then_base_fallback():
    t = Translator(
        {"en": {"k": "EN"}, "ru": {"k": "RU base"}, "ru-RU": {"k": "RU full"}},
        default="en",
    )
    assert t.t("k", "ru-RU") == "RU full"
    assert t.t("k", "ru-BY") == "RU base"  # falls back to base ru
    assert t.t("k", "fr-FR") == "EN"  # falls back to default


def test_resolve_direction_neutral_text_falls_back_to_ltr():
    # First-strong char heuristic returns None on punctuation/digits → ltr.
    from app.core.i18n import resolve_direction

    assert resolve_direction(None, "12345 ...", "auto") == "ltr"


def test_translator_unbalanced_brace_in_template_does_not_crash():
    # A stray ``{`` would raise ``ValueError`` from str.format if variables are passed.
    # The translator must swallow the error and return the raw template.
    t = Translator({"en": {"k": "open brace { with {n}"}}, default="en")
    assert t.t("k", "en", n=5) == "open brace { with {n}"


def test_translator_invalid_format_spec_does_not_crash():
    # ``{n:Q}`` raises ValueError because ``Q`` is not a known format code.
    t = Translator({"en": {"k": "value: {n:Q}"}}, default="en")
    assert t.t("k", "en", n=5) == "value: {n:Q}"


def test_translator_attribute_access_in_template_does_not_crash():
    # ``{x.attr}`` against a primitive raises AttributeError; ``{x[0]}`` against an int
    # raises TypeError. Both must be swallowed to keep the page rendering.
    t = Translator({"en": {"k": "value: {x.does_not_exist}"}}, default="en")
    assert t.t("k", "en", x=42) == "value: {x.does_not_exist}"
    t2 = Translator({"en": {"k": "value: {x[0]}"}}, default="en")
    assert t2.t("k", "en", x=42) == "value: {x[0]}"


def test_translator_has_reports_only_exact_locale():
    """``has`` must check the literal locale; it must not perform fallback."""
    t = Translator({"en": {"foo": "Foo"}, "ru": {"bar": "Бар"}}, default="en")
    assert t.has("en", "foo") is True
    assert t.has("en", "bar") is False  # bar exists in ru, not en
    assert t.has("ru", "bar") is True
    assert t.has("fr", "foo") is False  # locale missing entirely


def test_translator_supported_lists_locales_alphabetically():
    t = Translator({"ru": {}, "en": {}, "ar": {}}, default="en")
    assert t.supported() == ["ar", "en", "ru"]


def test_translator_default_locale_added_when_missing_from_directory(tmp_path):
    """``from_directory`` must guarantee the default locale exists, even with no file."""
    (tmp_path / "ru.json").write_text('{"hi": "Привет"}', encoding="utf-8")
    t = Translator.from_directory(tmp_path, default="en")
    assert "en" in t.supported()
    # Falling back to default produces the key when the default has no value.
    assert t.t("hi", "fr") == "hi"


def test_translator_empty_string_translation_is_returned_verbatim():
    """An empty translation is intentional and must not trigger fallback."""
    t = Translator(
        {"en": {"k": "English"}, "ru": {"k": ""}},
        default="en",
    )
    assert t.t("k", "ru") == ""
    assert t.t("k", "en") == "English"


def test_translator_fallback_chain_skips_locale_missing_only_one_key():
    """Per-key fallback: ``ru`` may have most keys, fall back to default for one."""
    t = Translator(
        {"en": {"a": "A", "b": "B"}, "ru": {"a": "А"}},
        default="en",
    )
    assert t.t("a", "ru") == "А"
    assert t.t("b", "ru") == "B"  # missing in ru → fall back to en


def test_parse_bcp47_three_segment_tag():
    tag = parse_bcp47("kk-Latn-KZ")
    assert tag is not None
    assert tag.language == "kk"
    assert tag.script == "Latn"
    assert tag.region == "KZ"


def test_parse_bcp47_un_region_code():
    tag = parse_bcp47("es-419")
    assert tag is not None
    assert tag.language == "es"
    assert tag.region == "419"
    assert tag.script is None


def test_parse_bcp47_normalizes_case():
    tag = parse_bcp47("EN-LATN-us")
    assert tag is not None
    assert tag.language == "en"
    assert tag.script == "Latn"
    assert tag.region == "US"


def test_parse_bcp47_whitespace_only_is_invalid():
    assert parse_bcp47("   ") is None


def test_parsed_tag_to_locale_round_trips_through_parse():
    tag = parse_bcp47("zh-Hant-HK")
    assert tag is not None
    assert tag.to_locale() == "zh-Hant-HK"


def test_parsed_tag_to_locale_omits_missing_segments():
    tag = parse_bcp47("ar")
    assert tag is not None
    assert tag.to_locale() == "ar"
