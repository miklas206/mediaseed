# Torrent and feed workspace — 0.1.19

Minimum and maximum seeders accept zero. Ranking uses leechers divided by max(1, seeders), so zero-seeder candidates remain reviewable without division errors. Existing rules and discovery scheduling are retained.

The add dialog uses two aligned desktop rows: source/files beside the prominent automatic account-ratio estimate with no separate stop goal, then the destination beside the visible cleanup selector and seeding guidance. Actions stay pinned below both columns. It supports one or multiple torrent files; batch estimates sum each file's estimated download charge against the same cached account totals. Unknown size or unavailable account data stays unknown. Preview requests parse local metadata and cached evidence only; they do not add torrents or query the tracker. Mobile uses one column. Folder explanations and VPN guidance are collapsed; additional cleanup fields appear only when their mode is selected.

Details open immediately from the list snapshot. Overview requests properties only; Files and Connections are fetched when selected. Connection reads run concurrently. A session-scoped in-memory cache coalesces requests for 15 seconds, with explicit refresh, accurate timestamps, mutation invalidation and protection against late responses. All server-side start/check/remove safeguards are unchanged. The legacy all-details API remains supported.

A separate estimated account-upload value preserves the conservative Green seeding-goal accounting. It adds double-upload credit only across fresh, matching observed intervals. Past bonuses are not retroactively inferred from today's badge. All displayed account credit is explicitly an estimate; the tracker account card remains authoritative for account totals.

Confirmed RSS imports are attributed by feed and info hash in an additive, indexed SQLite table. History is paginated and shows confirmed additions only, in a modal. Two original RSS history tokens do not count twice. Current feed attribution separates reimports to another feed. Recorded upload remains after a torrent is removed, and old/out-of-order observations cannot recredit the same upload. Unknown old credit stays unknown, with a missing-measurements count. Legacy confirmed hashes are backfilled idempotently; names become available when observed in the client. Existing settings, source secrets, media and torrents are retained.

Previously confirmed tracker names survive expiration of the five-minute benefit cache. Freeleech/double-upload flags still require fresh evidence; stable identity does not extend bonus validity or generate tracker requests.

App dock geometry follows the supplied MediaHub reference: thin accent frame, accent top edge, icon above label, icon accent rail, muted inactive labels and a bordered selected button. MediaSeed and MediaVPN keep their own destinations and inherited navigation preference.

Local verification uses synthetic client/tracker responses and isolated Chromium; no production installation or real torrent operations are required. Tests cover zero-seeder discovery, individual and aggregate credit, removal/reimport, old-database backfill, history pagination, section-specific reads, metadata expiry, modal batch estimates and cache behavior.


In 0.1.29 the storage and subfolder selectors align side by side on desktop, with
parent navigation and the current path below them. Mobile keeps a single column.
The hidden legacy ratio input remains disabled and outside the estimate card;
retained torrents and RSS feeds do not ask for it. Ratio cleanup goals remain
available only when a ratio-based cleanup mode is selected. Static asset URLs
are versioned so an update replaces cached older forms.


In 0.1.30 measured upload and download rates control independent, bounded packet
populations on a single end-to-end route. Packet sizes have distinct speeds
(large faster than medium, medium faster than small). Existing packets finish
their journey when the rate decreases; blocked VPN or invalid/stale measurements
clear the decorative traffic immediately. Resize observation keeps the track
aligned with icon centres, and opaque icons render above the packets. Reduced
motion shows static markers. These are throughput illustrations, not captured
network packets. No network or storage permissions change.

### Seeding progress follows cleanup

Torrent list and dashboard snapshots include the saved, identity-matched cleanup policy. The list displays `Ratio 1,3 / 3` for a ratio-3 cleanup rule, and its 16px progress bar shows 43%, even after 72 hours. Ratio and time modes use their respective goals; BOTH uses the lower progress and EITHER the higher. Completed torrents retain this goal display when paused or in an error state. Without cleanup there is no assumed ratio-1 or 48/49-hour finish line. Account credited upload remains visible, while the ratio uses the same conservative credit as the existing cleanup worker. Cleanup enforcement is unchanged.

Bars use blue for download, green for active seeding, amber for waiting, grey for pause, purple for checking/moving, and red for errors. Status text remains available. Seeding details also show the cleanup goal. Existing policies need no migration or new dependencies; the normal packaged static assets include the changes, with cache versions updated. Synthetic tests cover persistence, identity replacement, ratio/time/AND/OR progress, stale measurements, desktop/mobile layout and all bar colours.
