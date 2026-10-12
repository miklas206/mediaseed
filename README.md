# MediaSeed

Browserudvidelsen findes via **Browserudvidelse** i appens topbjælke eller `/static/extensions.html`. Siden genkender browseren og tilbyder Chromium-/Firefox-pakker samt Safari-kilde. Se [installation og tilladelser](browser-extension/README.md). Byg pakker med `python browser-extension/build.py` efter ændringer i udvidelsen.

The connection panel above the app tabs shows MediaSeed → MediaVPN → Internet,
the observed VPN country, public IP, forwarded port and latest check time.
Binary streams follow measured qBittorrent transfer speeds: green upload moves
toward Internet; blue download moves toward MediaSeed. Simultaneous transfers
use separate lanes, and idle traffic stops both. Each lane uses one continuous,
gap-free binary strip. Twelve characters move past every 2.4 seconds, then fresh
random bits replace only the offscreen part so the visible text stays aligned
at the animation boundary. Motion also requires a verified VPN route with
downloads enabled. Missing or invalid traffic measurements stop
the streams, independently of connection readiness, after at most 30 seconds.
A failed VPN request or missing VPN refresh for 30 seconds clears connected
state. Reduced-motion preferences replace motion with static binary text.
Country comes from MediaVPN's provider geofeed match for the current public IP;
missing location is unknown, never guessed from profile names or generic GeoIP.
Country geolocation does not prove physical location.

The signed-in page polls `/api/dashboard` every five seconds. A single bounded
torrent read supplies the list, traffic and queue counters alongside fresh VPN
status. Client version refreshes in the background every five minutes and local
storage every minute. Slow auxiliary reads cannot hold up the dashboard, and
failed client reads remain distinct from an empty torrent list. Action checks
and VPN readiness are never cached; additions, start and stop refresh the list
directly, with stale list responses prevented from overwriting a newer result.

Independent seedbox application for MediaHub, inspired by FjordSeed's separate-app model. This repository is an early preview, not a replacement for the existing Seedbox installation.

## Implemented

- Verified MediaVPN integration with live tunnel/interface/forwarded-port gates and restricted machine RPC. No Docker socket or VPN keys in MediaSeed. VPN stop closes the start gate and stops its managed client; namespace changes recreate the client using preserved named volumes.
- NordicBytes RSS automation: feeds are paused by default. Explicit activation requires verified VPN. Native qBittorrent RSS fetches inside the tunnel; publication dates, fixed tracker download URLs and durable SQLite intent/history gate automated stopped import and subsequent start. Ambiguous imports are not automatically retried. Existing user-paused torrents are not resumed. Currently only NordicBytes HTTPS feed/download endpoints are supported; other sources remain saved but cannot activate.
- Shared native queue with maximum eight active downloads and RSS priority for new RSS imports, plus source filtering and queue counters. RSS origin is stored as a category. Pause/deletion of a feed never deletes its torrent data.
- Cached NordicBytes account statistics refreshed at most every five minutes, including ratio/upload/download/bonus/warnings; remote private fields and credentials are excluded.
- Automatic tracker poster lookup for exact infohash matches. Only approved TMDB image hosts are fetched without tracker credentials. Authenticated local cache: 2 MiB/image, 64 MiB total, 24-hour refresh and seven-day cleanup. Missing/mismatched posters do not produce guessed artwork.
- Torrent details show fresh exact-hash tracker benefit evidence (freeleech, double upload, featured/internal/refundable) when supplied by NordicBytes. Evidence expires after five minutes; it does not invent Green-credit or tracker-accounted ratio.

Remaining FjordSeed parity includes selected-badge RSS filters, durable Green-credit accounting, detailed benefit/ratio previews, verified archive moves, generic tracker support and generic installation/SSO provisioning. TCP relay and country/profile-generation UI remain MediaVPN follow-ups. These features are not implemented by this release.

In managed SSO mode, opening MediaSeed automatically starts MediaHub login when
the app session is missing. An active session at the configured hub origin logs
in as the same user without another password prompt. Denied access or an
unavailable hub displays an error without a redirect loop. No first manual app
login is required; standalone login remains available outside managed mode.
Set `MEDIASEED_HUB_BROWSER_URL` to the public HTTPS hub origin when it differs
from `MEDIASEED_HUB_URL`, which remains the verified server-side identity endpoint.
An app domain saved in MediaHub settings becomes the registered opening and SSO
callback origin together. MediaSeed reads it over verified TLS and accepts only
its local host and that current domain. Set up domain DNS, HTTPS and proxy
routing first, preserving the original Host header. Reset restores the local
HTTPS origin.

- Independent FastAPI runtime, SQLite account database and static application UI.
- Administrator setup, Argon2 password storage, expiring HttpOnly sessions, CSRF protection, login throttling and strict host/origin checks.
- Authenticated qBittorrent 5 API adapter: bounded torrent count, stopped torrent addition, gated start, stop and removal without deleting downloaded files. Starts require fresh MediaVPN verification.
- No dependency on MediaHub Core, Agent or FjordVPN. No Docker socket in the application container.
- Downloads UI with search, status filters, sorting, speed/ratio/seeding-time display and a detail dialog for files, tracker hosts and peers.
- Per-torrent ratio configuration with 49-hour seeding limits, and explicit early-stop confirmation when neither ratio 1 nor 49 hours is reached. Torrent removal always preserves files.
- Independent RSS and NordicBytes account configuration: create/edit/remove, relative folders, feed start timestamp and ratio goals. Feed URLs/passkeys and API keys are not returned to the browser.
- Tracker search: select a saved NordicBytes account, search releases, and import a selected torrent file directly into qBittorrent in stopped state with a ratio goal and 49-hour seeding limit. The API key and private download link stay on the server. Only HTTPS downloads from NordicBytes are allowed; redirects and oversized responses are rejected. Starting requires fresh MediaVPN verification. Other tracker providers are not implemented.
- Manual addition: Downloads > Tilfoej torrent offers magnet links or a selected/dropped .torrent file (maximum 4 MiB), plus a ratio goal. Dropping one .torrent file anywhere on the signed-in page opens this dialog with the file selected; upload still requires confirmation. Choose **Tilfoej og start** for immediate VPN-gated start, or **Tilfoej paa pause** to wait. The list refreshes directly after import even while a full status poll is running. Viewer accounts cannot add files. Both paths preserve the ratio goal and 49-hour seeding limit. Start is validated by MediaSeed and again by the MediaVPN controller immediately before delivery to qBittorrent; paused addition remains the API default. Magnet metadata can remain pending while the client has no Internet access; this is reported separately from completed addition. No arbitrary HTTP torrent URLs or magnet external file sources are accepted. Uploads are not saved in the app data volume; qBittorrent validates the torrent contents.

Source credentials are stored in the private SQLite database, not encrypted at rest. Protect the data volume and its backups. Use only synthetic credentials in this preview until the encrypted credential store is integrated.

## Preview

Run `docker compose up --build` and open `http://127.0.0.1:18800`. Compose starts MediaSeed and a real LinuxServer qBittorrent client with persistent configuration and shared local downloads. A one-shot initializer generates a separate internal password, protected by file permissions, without printing it. Existing client configuration is never overwritten. The first local visitor creates its administrator account: do not expose a fresh instance to other users before setup. Behind HTTPS, set `MEDIASEED_BASE_URL` to the exact external origin before starting.

For local development: install this package and run `uvicorn mediaseed.app:create_app --factory --host 127.0.0.1 --port 18800`. Set `MEDIASEED_DATA` to a private, disposable local directory. Tests use a synthetic qBittorrent transport and never start downloads.

## Local LXC storage

No external disk, NFS share or manually configured mount is required. Compose automatically creates independent `appdata` and `downloads` volumes under Docker's data directory on the LXC disk. The image initializes their directory ownership for UID 1000. These volumes persist across application recreation; do not use `down -v` unless intentionally deleting app data.

`GET /api/storage` reports real local download-volume capacity and write access after login; the Downloads screen shows free space. Downloads share the capacity of that underlying filesystem, so filling it can affect the LXC and other apps. A warning appears below 2 GiB free. This is not a quota or a download admission gate: torrent execution remains disabled pending the VPN supervisor.

For a private LAN preview set `MEDIASEED_BIND` to the LXC address and `MEDIASEED_BASE_URL` to `http://<LXC-address>:18800`, then start Compose. Keep it off the public tunnel and perform administrator setup on a trusted LAN. `MEDIASEED_PORT` changes the external port. This does not add the app to MediaHub's App Store yet. Optional external storage and torrent-runtime migration are separate follow-up work.

The adapter reads `MEDIASEED_QBIT_URL`, `MEDIASEED_QBIT_USER`, and `MEDIASEED_QBIT_PASSWORD_FILE`. Compose configures these automatically using private `clientconfig` and `clientsecrets` volumes. qBittorrent has no published ports and only an internal Docker network, with no Internet access. `/api/client` proves authenticated connectivity and reports the actual client version. This installs the client, but does not enable downloading: a verified VPN runtime remains required. Never attach the client to an Internet-enabled network to bypass the gate. Preserve all four named volumes during updates and rollback.

## Before replacing Seedbox

## MediaVPN Integration

Use the opt-in compose.vpn.yaml overlay with MEDIASEED_VPN_URL and MEDIASEED_VPN_TOKEN_PATH. Provision a protected copy of MediaVPN's integration token, readable by UID 1000, on the app host. Mount only that file read-only; never the VPN data directory or Docker socket. This only makes MediaVPN available as a choice; it does not select or attach it. In MediaVPN, start a Proton profile with NAT-PMP port-forwarding enabled. In MediaSeed > VPN, explicitly select MediaVPN and a profile, then choose **Gem VPN-valg**. Readiness requires a fresh verified tunnel, assigned port, correct VPN namespace and tun0 binding. Start is checked again server-side for each operation. The old isolated client is retained stopped for rollback; do not run it concurrently on the shared configuration volume. Do not run Compose up for the qbittorrent service after attachment.

The saved VPN choice belongs to MediaSeed. A new installation or an upgrade from the old implicit integration starts with no selected provider and downloads blocked; visiting the VPN page only discovers profiles. Previously explicit choices persist across restarts. **Frakobl MediaVPN** closes the local download gate, disables the controller and removes only the attached client container with volumes preserved. The VPN profile itself stays running and its keys are untouched. MediaVPN can then be uninstalled without uninstalling MediaSeed. If the controller is unavailable, disconnection remains pending and retries after restart; the app never falls back to direct Internet access.

For an existing implicit deployment, take application/database/Compose backups, stop and detach the old attached client using the controller's `enabled:false` operation, and verify it is absent after upgrading MediaVPN. Keep qBittorrent configuration/download volumes and the isolated original client. Do not silently import the controller's old enabled flag as MediaSeed's new choice.

MediaVPN owns the attached client and synchronizes its port after tunnel changes. Native queueing is limited to eight downloads. Stopping the seedbox does not delete torrents or files. Automatic RSS, tracker account refresh, covers and complete FjordSeed/FjordVPN parity are still pending. An installed integration is not a completed first-time installer or independent network-leak certification.

The migration is not complete. Required next steps:

1. Extract the existing fail-closed VPN supervisor and approved storage checks into this application. A shared Gluetun network alone is not a complete replacement for those checks.
2. Add a private Gluetun/qBittorrent Compose project, interface binding, fresh tunnel verification, port-forward synchronization, namespace recreation and stop-on-failure behavior. Starting torrents is rejected until that gate exists.
3. Move existing RSS, tracker, retention, uploads, VPN profile/location and diagnostic workflows with focused regression tests.
4. Add scoped MediaHub managed login, manifest, install/update lifecycle, and links. Local login must continue to work without the hub.
5. Rehearse opt-in migration with disposable data, test desktop/mobile UI, backups and rollback; only then switch the beta installation.

No production service, beta account, torrent, download, VPN key or shared storage is changed by this repository. Do not connect the adapter to the existing live qBittorrent during preview: stop/remove are real operations.

`/healthz` reports application process availability only, not VPN protection or torrent health.

## Verification

For a VPN-enabled installation, upgrades must include both `compose.yaml` and `compose.vpn.yaml` and the existing trusted `MEDIASEED_VPN_URL` / `MEDIASEED_VPN_TOKEN_PATH` operator variables. Omitting the overlay recreates the app without the machine token and closes its VPN gate; do not treat the standalone preview command as an integration upgrade.

The opt-in scripts `tests/verify_runtime.py` and `tests/verify_rss_runtime.py` run inside the installed MediaSeed container. The first checks live VPN/native RSS/queue readiness using a disposable account without changing the live account. The second uses synthetic RSS metadata and a tracker-free one-byte torrent, starts only its exact generated hash, proves restart-dedup, and removes only that test torrent without deleting files. It does not activate live user feeds or prove a real provider feed download.

`pytest tests -q` tests independent setup/login/logout, CSRF, host/origin rejection, filtered torrent reads, invalid hashes and the disabled start gate. Real VPN operation, Compose build and full feature parity are not covered by these tests.

See [FjordSeed feature checklist](docs/fjordseed-feature-checklist.md) for the explicit remaining scope. Browser checks cover source creation/editing, masked credentials, search, torrent details and responsive layouts with synthetic data; no real VPN or tracker account was contacted.

Automatic cleanup for new manual imports is documented in [automatic-cleanup.md](docs/automatic-cleanup.md). It defaults off and supports time, ratio, both/either goals, torrent-only removal or explicit file deletion, and cancellation under Details.

Managed MediaHub Overview uses `GET /api/hub/overview` with a short-lived, package-bound SSO bearer scoped to `overview`. It rechecks hub session and app role, returns the existing bounded dashboard snapshot and cached tracker statistics, and never fetches tracker APIs on demand. API keys, account URLs and usernames are excluded. The bearer does not create an app login or authorize torrent changes.
