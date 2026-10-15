# Build ratio — MediaSeed 0.1.15

The **Build ratio** tab discovers freeleech candidates through the configured
NordicBytes API account. Discovery and automatic downloading are disabled on
fresh installations and on upgrade. Existing RSS rules, tracker credentials,
storage, torrents and cleanup policies are preserved.

Select a tracker, an approved storage location and optionally an existing
relative subfolder. Enable discovery and choose **Suggestions only** to review
candidates, or explicitly select **Automatic downloads**. Save the rules.
Only administrators can save or approve; ordinary users can read results.

Defaults:

| Rule | Default |
| --- | --- |
| Seeders | 1–2 |
| Leechers | At least 20 |
| Freeleech | Confirmed 100%, always required |
| Double upload | Optional |
| Maximum torrent size | 20 GiB |
| Storage reserve | 20 GiB |
| Download allowance | 3 per rolling 24 hours |

Title substring, seeder bounds, minimum leechers, double upload, size, free
space and allowance are configurable. This first version has one rule set for
one tracker. It requests the API's freeleech filter and leecher-descending sort,
then inspects up to 50 results every five minutes, without
scraping or walking the full catalog. Refreshing the UI only reads the cached
scan. Rule changes clear old candidates but do not bypass the scan interval.
API calls from this worker are at least one second apart; HTTP 503/rate-limit
responses postpone requests for 15 minutes. These are application defaults,
not a claim that the tracker's unpublished API limits allow a particular rate.

Candidates require known nonnegative integer counts and size, a valid info-hash,
and an exact API identity. Green torrents and unknown publication dates are
excluded using a 24-hour plus 30-minute publication-age threshold. Rank is
leechers per seeder descending, then size ascending. Each scan attempts at most
three candidates and imports at most one; blocked reasons are displayed.

Approval and automatic import share the same safeguards:

1. Read the specific torrent again through the API. Recheck all filters and
   discounts; download metadata through the verified MediaVPN client namespace.
2. Verify the original torrent metadata's hash and size against the API result.
   Inspect the client for duplicates. At 500 torrents the bounded inspection
   refuses imports rather than assuming the list is complete.
3. Resolve and validate approved writable storage again. Reserve the candidate
   size, all existing unfinished downloads, uncertain imports and the selected
   free-space margin. Counting unfinished downloads on other filesystems is
   deliberately conservative.
4. Fetch account totals and estimate ratio assuming the whole candidate costs
   download, even if currently freeleech. Include unfinished downloads and all
   ratio imports in the last 24 hours to tolerate delayed tracker accounting.
   These may overlap and make the estimate conservative. Require an estimated
   ratio of at least 0.5; unknown totals block the action. This is an estimate,
   not a guarantee against external account activity or stale upstream totals.
5. Persist import intent before any add request. Add paused with ratio 1 and
   49-hour seeding safety settings. The existing Green accounting controller
   remains responsible for conservative native share limits. Verify paused
   identity and download folder before requesting start. Evidence older than
   30 seconds, changed settings/account or lost VPN leaves the torrent paused.
6. Count pending/uncertain attempts against the allowance and never retry them
   automatically. A timeout or crash retains durable intent. Inspect the
   torrent list and control it manually if necessary. No cleanup/delete action
   is registered. Files remain after seeding goals are reached.

New imports use the `MediaSeed-Ratio` category and appear as **Build ratio** in
the torrent list/source filter; queue counts distinguish them from manual/RSS
downloads. Native qBittorrent creates the category during add. They share the
existing download queue; this feature does not bypass that queue.

Fresh/upgrade schema creation is additive and idempotent. The SQLite import
ledger survives restart; already handled hashes stay excluded even if a user
later removes a torrent. There are no new packages, Docker permissions or
mounts. The feature uses existing MediaVPN integration endpoints and does not
require a MediaHub Core update. MediaVPN 0.2.19 is needed for the separately
released configurable RSS interval, not for ratio discovery itself.

The supplied NordicBytes rules require actual seeding/upload, prohibit fake
accounting and scraping, require ratio above 0.4, and require 48 hours after
completion or torrent ratio 1:1 even for freeleech. Upload and demand are never
guaranteed; time-limited discounts can expire. No real torrents were imported
or started during development or verification.

Validation uses synthetic tracker/metadata/client transports, including the
real Green credit controller. Covers defaults and upgrade persistence, strict
input, auth/roles/CSRF, secrets omitted from public results, stale/changed
benefits, metadata mismatch, duplicates, quotas, space and ratio reservations,
VPN loss, uncertain writes, restart deduplication and API backoff. Isolated
Chromium checks at 1440/390/320 cover translated UI, saving automatic opt-in,
approval success/failure, duplicate prevention and horizontal overflow.

Final local checks: 185 Python tests passed, 1 skipped; 50 JavaScript tests
passed; targeted Ruff and diff checks passed. Read-only checks against the
configured tracker returned HTTP 200 for filtered discovery and account totals,
and confirmed the expected field types. Pinned tracker entries can affect API
order, so final candidate ranking is performed locally. No live settings were
changed, no downloads started, and the new application has not been deployed.

API references: [UNIT3D API filters and sort parameters](https://github.com/HDInnovations/UNIT3D/blob/master/app/Http/Controllers/API/TorrentController.php),
[qBittorrent add/category handling](https://github.com/qbittorrent/qBittorrent/blob/release-5.2.0/src/base/bittorrent/sessionimpl.cpp).
