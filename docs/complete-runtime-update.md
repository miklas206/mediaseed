# Complete runtime update — 0.1.39

The release retains the conversion, native upload, tracker aliases, import receipts, torrent views and browser extension already present in the deployed application. Updating from the older published source must not remove these modules. Theme-aware branding and Settings → Connections use the existing application shell and saved preferences.

The application image now includes ffmpeg, MediaInfo and mkvtoolnix. Existing Compose bindings and auxiliary workers are preserved by MediaHub 0.6.79. No runtime databases, credentials or media are included in this source release.

Validation:
- Python: 685 passed; three existing catalog/removed ratio-scout endpoint expectations still fail.
- Node: 71 passed; nine old drop harness cases do not mock the new import receipt lookup.
- Real browser: connection controls passed desktop/mobile, save/reopen, unlimited, viewer permissions, old-provider compatibility and overflow checks.
- MediaVPN compatibility: destination-local staging, fresh VPN observations and physical traffic counters are retained in 0.2.36.

The broad-suite failures are recorded rather than reported as passing; the connection-settings checks pass. Full UI catalog coverage remains follow-up work.
