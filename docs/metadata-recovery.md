# Covers and tracker metadata recovery

MediaSeed automatically recovers missing tracker descriptions, MediaInfo, file
lists, screenshots and approved public cover images. It matches the exact
infohash before accepting a search result or a full torrent record. A name match
alone never attaches metadata from another release or tracker account.

The worker can make at most six remote calls per 15-second tick, with at most
two tracker calls per torrent in a tick. Unvisited torrents consume no attempts
or cooldown. Pagination resumes from its saved cursor on a later tick instead
of repeatedly starting at the first page.

Search, full-record retrieval and cover download each have their own maximum
of **five total attempts**. Failed/missing results wait 5 minutes, 15 minutes,
1 hour and 6 hours before the next attempt. Attempts and pagination are stored
in `cover-cache/metadata-retries.sqlite` before I/O and survive refreshes,
cancellation and restarts. Enqueuing an exhausted torrent does not reset it.
Changing the configured tracker identity/credential starts a new scoped job.
The database stores no API keys, passkeys, tracker download links or raw API
responses.

An image retry reuses its already approved public CDN URL and never requires a
new tracker search. Responses still have a 2 MiB limit, image signature checks,
no redirects, no proxy inheritance and no tracker credentials. A tracker default
poster is not treated as an actual title cover. Truly unavailable source data
remains unavailable after the attempt limit; the app does not invent it.

Successful jobs retain the existing five-minute benefit freshness workflow,
using the known exact record rather than repeating name searches. Successful
benefit refreshes are maintenance reads, not missing-metadata retries. Their
consecutive failures also stop at five attempts. A cached image is refreshed
after 24 hours. Sparse observations preserve existing static descriptions,
MediaInfo and file lists for the same tracker account; absent live swarm and
benefit fields remain unknown rather than being relabelled as fresh.

Fresh installations and upgrades create the additive retry database
automatically. Existing covers, metadata, credentials, torrents, download
locations and media are preserved. There are no new packages or manual setup
steps. The existing app image includes the new module through the ordinary
`COPY mediaseed` build. Rollback may leave the unused additive database in place.

Verification uses synthetic HTTP transports and covers exhaustion across
restart, cancellation, pagination, global budgets, other-account fallback,
independent image retries, exact hashes, credential changes, sparse metadata,
fresh startup and preservation of an older database/cache.

Missing covers are prioritized over maintenance of torrents that already have
images. Saved, account-matching approved poster URLs can repair an image during
a tracker outage without repeating discovery. Full record reads accept both
UNIT3D's `data` wrapper and NordicBytes' unwrapped resource, validating the
record ID and infohash in either form.

Original live-deployment verification: 309 Python tests passed in the mixed
workspace, including the actual
unwrapped response shape and the shared API/image budget with multiple
accounts. The runtime image also passed isolated startup/asset and recovery
checks as UID 1000 with a read-only root and no network or production volumes.
The live app was updated with an online SQLite/Compose backup and a preserved
previous image; only MediaSeed's app was recreated. Existing unrelated source
changes were retained. That first deployment did not create a release tag or push the mixed
working tree.

Fjord reference: `qlerup/fjordseed`, default branch `main`, commit
`1622f243d8d5279d291b6ba9781a488bd8ec3fa4`. Its bounded public-image downloads,
cache limits and credential isolation are preserved; MediaSeed adds persistent
attempt accounting and its own exact-hash metadata lookup.


## Independent GitHub publication

The recovery fix has been separated from the other pending application changes
and validated against the published `main` baseline. Its dependencies include
only metadata sanitization/storage and tracker identity/announce matching; the
unrelated account dashboards, additional tracker features and download changes
are excluded. The recovery worker is identical to the installed version.
The ordinary app startup creates the additive metadata tables and retry ledger;
`/api/torrents/{hash}/media` exposes saved metadata through normal authentication.

The isolated publication checkout passes **246 Python tests and 57 JavaScript
tests**. Added API tests verify authenticated access, actual worker callbacks,
plain-text metadata, exclusion of private download links, image delivery,
restart persistence and upgrades preserving older credentials and covers.
The five-attempt, cancellation, backoff and budget tests are retained.
Publication does not restart or replace the already running app.

The built wheel was installed into an empty package directory and passed startup,
module inclusion, automatic schema creation and unauthenticated API rejection.
No dependency, version, release tag or runtime deployment change is needed.
