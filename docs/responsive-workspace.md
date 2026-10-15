# Responsive workspace (0.1.17)

The three transfer/torrent cards and their existing graphs remain. A measured, VPN-gated route animation sits above them. Download status cards link to VPN, tracker accounts and download/file-check filters. The old RSS sidebar becomes recent automatic activity from a bounded local history; it does not fetch feeds more frequently. Client details are collapsed.

Widths and spacing adapt automatically from mobile to 1440 CSS pixels of content, matching MediaHub Apps. Text stays at a stable readable scale instead of enlarging with the viewport. Dock navigation uses the same icon-above-label geometry, selected outline and theme variants as MediaHub. Route and transfer icons use uniform SVG dimensions. No screen-specific scaling setting is required. App headers are 56 pixels on desktop with the logo aligned left. Both apps inherit the signed-in Hub user's navigation: sidebar, topbar, top dock, bottom dock or launcher. Settings can override it for that app/account; following Hub removes the override. Standalone apps use a local browser preference. Existing theme overrides are preserved. A Back to MediaHub button uses the configured LAN Hub origin for local private-IP access, otherwise the configured browser Hub origin. Standalone apps hide it. Mobile displays the accessible arrow without the long label.

RSS feeds use a list and modal editor. Required badges use independent checkboxes; every selected badge remains required. Tracker accounts appear once, with a modal editor and existing search/actions. Torrent pause/start uses accessible themed SVG controls.

Other media requires an approved storage location. Core 0.6.37 provisions it on both installation and upgrade and can recover older omitted Other approvals from a matching Plex storage policy. No arbitrary host paths are inferred. The same storage API supplies manual, RSS and ratio selection.

New automatic-activity and per-user navigation tables are additive and idempotent. Existing accounts, feeds, credentials, preferences and torrents are retained. Activity is deduplicated, bounded to 1000 records, and exposes ten recent entries without feed URLs or credentials. Reduced-motion preferences stop the new route animation.

Validation: Python tests cover persistence, authenticated navigation updates, activity retention and older databases. Isolated Chromium with synthetic APIs checks 320, 390, 768, 1440, 1920, 2560 and 3840 pixel viewports, all five navigation modes, retained transfer cards, badge selection and a single tracker account view. This is not live server installation evidence.

## Status balance and add-dialog alignment — 0.1.22

Desktop status uses 1:2:1 columns with the tracker in the centre. Connection and queue retain their natural height and align vertically to the centre; at 1100px and below all three stack. Tracker account/rank/warnings share a compact footer. The side cards reuse the verified connection IP/forwarded port/check time and current seeding/stopped counts; unavailable torrent snapshots do not become zero counts and unverified VPN observations do not display stale IP information. Transfer graphs are unchanged.

The add-dialog estimate aligns with the source buttons below their legend. Destination and cleanup fieldsets align at the start of the second grid row. The first-row offset is desktop-only; mobile retains its natural single-column order. Verified with synthetic APIs and both magnet and five-file modes across 320–2560px viewports.
