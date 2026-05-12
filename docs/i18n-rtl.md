# Languages and RTL

The service accepts BCP-47 language tags everywhere — `language` (the search
target language) and `ui_locale` (the UI strings) are decoupled. UI strings
fall back along the chain `xx-YY → xx → en`, so adding a regional variant is
free.

## Direction resolution

`app.core.i18n.resolve_direction(language, text, requested)` decides between
`ltr` and `rtl`:

1. Explicit `direction="ltr"` or `"rtl"` from the request always wins.
2. RTL language code wins next: `ar`, `he`, `fa`, `ur`, `yi`, `dv`, `ps`.
3. First-strong-character heuristic over the text via
   `unicodedata.bidirectional()` — cheap, no extra dependencies.
4. Default `ltr`.

## CSS strategy

All component CSS uses logical properties: `padding-block`, `padding-inline`,
`margin-inline-start`, `inset-inline-end`, `text-align: start/end`. There is
no separate RTL stylesheet. Setting `<html dir="rtl">` flips the layout.

Result text uses `dir="auto"` on every `<article>` and `.image-tile__overlay`
so a mixed-script snippet like `python مكتبة البحث` resolves correctly inside
both LTR and RTL pages.

## Adding a new translation

1. Drop a `<locale>.json` into `app/ui/translations/`.
2. Add the locale to `i18n.supported_locales` in `config.yaml`.
3. Tests under `tests/ui/test_render.py` already exercise LTR + RTL — extend
   them if your locale needs special-case keys.

## Things to avoid

- Don't reverse strings manually anywhere — keep Unicode in logical order.
- Don't ship icons whose direction depends on text direction (carets,
  arrows) without an `[dir="rtl"]` flip; the pagination chevrons in
  `components.css` use `transform: scaleX(-1)` for that reason.
- Don't translate the user's query without an explicit `translate_query=true`
  parameter.
