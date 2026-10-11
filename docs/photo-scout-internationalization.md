# Photo Scout UI languages

The browser's first preferred language selects the interface: English (`en`), Simplified Chinese (`zh-Hans`, including CN/SG), or Traditional Chinese (`zh-Hant`, including TW/HK/MO). Explicit Chinese script tags take precedence over region. Unsupported languages fall back to English.

`photo-scout-site/i18n-catalog.js` contains the UI message catalogs. `i18n.js` loads before the app, sets the document language and translates known messages, placeholders and accessibility labels. It observes only new or changed UI nodes so asynchronous history, progress, photo and avatar panels also translate. A translated node is not rewritten again; there is no translation API, additional model call, or polling. English UI text remains a stable catalog key.

Unknown content, model-generated recommendations, user input, comments, place names, account names, provider attribution, URLs and API enum values remain unchanged. Mark any additional user-content container `data-i18n-ignore`. UI code must not use the displayed label as application state; legacy status comparisons use `PhotoScoutI18n.source(element)` to read the original message. Form option `value` attributes stay independent from localized labels.

To add a language, add its catalog and locale resolver branch. When adding a new UI message, add both Chinese translations. Do not translate arbitrary words within user-provided content. Parameterized messages use explicit patterns in `i18n.js`.

Checks:

- `node --test tests/photo-scout-*.test.cjs`
- `NODE_PATH=<Playwright installation> CHROME_PATH=<Chrome executable> node tests/browser/photo-scout-i18n.cjs`

The browser check serves a local fixture, stubs service API responses, and verifies English, both Chinese variants and unsupported-language fallback at mobile and desktop widths. It does not start paid searches or image generation.
