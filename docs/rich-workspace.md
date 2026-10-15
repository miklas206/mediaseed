# MediaSeed visual workspace 0.1.8

The Downloads list shows authenticated cached posters, exact-hash tracker badges,
size/source, ETA, both transfer directions and credited seeding progress. Unknown
metadata stays unknown; missing images leave a fixed-size placeholder. Titles are
rendered as text and never guessed from a filename. Existing search/filter/sort,
start/pause, removal confirmations and role restrictions remain in place.

Torrent details use a responsive right-hand dialog with Overview, Seeding, Files,
Connections and Activity tabs. Activity explains current state and links the concept
of controller history to MediaHub Logs; it does not invent historical events.
The latest selected torrent wins asynchronous responses. Escape/close and keyboard
tab navigation retain native dialog behavior. Reduced motion is supported.

The add dialog offers an explicit read-only metadata/account-ratio preview. It
parses a bounded local torrent file or reads existing exact-hash cached metadata
for a magnet. It does not fetch magnet source URLs or start/import a torrent.
Fresh account observations determine estimates; unknown freeleech is charged as a
normal download. Missing size/account evidence yields an explicit unknown result.

RSS adds optional plain include/exclude title fragments, a maximum total torrent
size and required verified tracker badges. Exclusions take precedence. Title
matching normalizes case/punctuation. Size equality is allowed; missing/oversized
metadata blocks an import with a size cap. All required badges must match one fresh
exact info-hash observation. Lookup/API failures do not bypass requirements.
The existing VPN, feed-change, storage and duplicate-intent gates remain enforced.

Existing feeds without the new fields keep their previous behavior. New fields
default to empty/no limit; blank feed URLs preserve saved credentials. A private
additive rss_observations table is initialized automatically alongside the existing
history on fresh installs and upgrades. It retains up to 1,000 latest observations
globally, exposing the last 100 titles/status/timestamps for a chosen existing feed.
Feed/download URLs, keys and passkeys are not returned. Existing intent history,
credentials, torrents and media remain unchanged. No new dependencies/mounts or
host requirements are introduced. Pause filtered feeds before downgrading to an
older version that does not enforce these filters.

Visual verification uses isolated Playwright Chromium with synthetic authenticated
API fixtures, not installed Chrome or any Proxmox/FjordSeed instance. Desktop,
tablet/mobile, long names, missing covers, details/tabs, RSS history, add preview
and empty-list states are inspected. Tests do not prove real tracker poster/API
availability or real torrent/VPN performance.
