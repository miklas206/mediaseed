# Account language

The interface and browser extension support Danish and English. Hub-managed login uses the current account language returned by MediaHub's SSO session. Open pages recheck it on focus and once per minute. Standalone administrators select a language at creation; existing standalone accounts keep Danish during the additive database migration. Credentials, session validation, VPN requirements and API values are unchanged.

`static/i18n.js` contains English source keys, Danish translations and the translator. Capture only static document labels; use `t()` with placeholders for dynamic display text. Never translate torrent names, file names, tracker names, URLs or wire values. Dates, numbers and country names use the account locale. Add messages to the app catalog and keep `browser-extension/i18n.js` synchronized, then rebuild archives with `python browser-extension/build.py`.

Extension 1.2.2 reads language from the connected MediaSeed identity. Tracker-page helpers have no DOM translation engine, so third-party website content is preserved. Settings and source-page import feedback use the saved account language.

Verification: `pytest tests`, `node --test tests/*.mjs`. Localization tests cover static labels, literal keys, account persistence, old-schema credential preservation and safe reversible DOM translation. No browser, real tracker download or VPN connection is needed.
