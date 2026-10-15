# RSS refresh interval

MediaSeed 0.1.14 exposes **Settings → RSS refresh** to administrators. The
interval is an integer number of minutes, from 1 through 1440. Follow the
tracker's required minimum; this range is a technical limit, not a statement
that a tracker permits one-minute polling.

Update MediaVPN to 0.2.19 first, then MediaSeed. No MediaHub Core update is
required. Older MediaVPN versions produce an explicit upgrade message before
any setting is written. Reads show the actual client preference, rather than
assuming the new default has already been installed.

Managed clients default to five minutes when no MediaSeed RSS interval has
been saved. MediaVPN persists a selected interval in its existing seedbox
settings, preserving profile selection, download limits and other fields. Its
regular reconciliation applies and verifies the value for an existing client
and after a client restart/recreation. Saving does not recreate feeds, change
their URLs or rules, or explicitly start or remove torrents. Existing enabled
RSS rules can naturally discover new matching torrents sooner.

Preview installation adds the five-minute qBittorrent default during client
initialization, including an older configuration missing that preference.
An explicit existing preview interval, credentials and other configuration are
preserved. qBittorrent persists subsequent changes made through the settings API.

The tracker fetch interval is separate from MediaSeed's background rule checks:
the worker waits 15 seconds after each processing cycle. Requests and other
background work can extend that delay. Feeds created by MediaSeed inherit the
client refresh interval; no manual force-refresh loop is added.

Validation: synthetic tests cover fresh and older initialization, preservation
of credentials and settings, restart/recreation, strict input, authentication,
administrator permissions, CSRF, older MediaVPN capability checks and native
read-back verification. Full Python suites: MediaSeed 146 passed/1 skipped;
MediaVPN 228 passed/1 skipped. Seed JavaScript suite: 50 passed. Isolated
Chromium checks at 1440/390/320 verify translated settings, saving, scrolling
and existing pagination/recheck/storage flows. No production install, actual
RSS request or torrent action was performed for this change.

## Responsive RSS processing — 0.1.23

RSS now has its own non-overlapping background loop. Slow covers, ratio discovery and account requests no longer delay the next RSS cycle. Each job retains its own cache/deadline checks; the RSS fetch interval remains the user's native client preference.

A cycle can import multiple received articles, within the existing shared six-metadata-request budget. It does not raise the concurrent download limit: imports still use the native queue and verified VPN/storage/start safeguards. Another feed's previously imported file is remembered by article token so duplicate metadata cannot repeatedly consume the processing budget. Existing history, credentials, start dates and filters are retained; no live migration or forced RSS fetch is required. Shutdown cancels all child loops through TaskGroup.
