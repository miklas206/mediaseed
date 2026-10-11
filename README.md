# MediaSeed

Independent seedbox application for MediaHub, inspired by FjordSeed's separate-app model. This repository is an early preview, not a replacement for the existing Seedbox installation.

## Implemented

- Independent FastAPI runtime, SQLite account database and static application UI.
- Administrator setup, Argon2 password storage, expiring HttpOnly sessions, CSRF protection, login throttling and strict host/origin checks.
- Authenticated qBittorrent 5 API adapter: bounded torrent count, stop a torrent and remove a torrent without deleting downloaded files. Adding and starting torrents is rejected until the VPN gate exists.
- No dependency on MediaHub Core, Agent or FjordVPN. No Docker socket in the application container.
- Downloads UI with search, status filters, sorting, speed/ratio/seeding-time display and a detail dialog for files, tracker hosts and peers.
- Per-torrent ratio configuration with 49-hour seeding limits, and explicit early-stop confirmation when neither ratio 1 nor 49 hours is reached. Torrent removal always preserves files.
- Independent RSS and NordicBytes account configuration: create/edit/remove, relative folders, feed start timestamp and ratio goals. Feed URLs/passkeys and API keys are not returned to the browser. Workers/synchronization are not connected yet.

Source credentials are stored in the private SQLite database, not encrypted at rest. Protect the data volume and its backups. Use only synthetic credentials in this preview until the encrypted credential store is integrated.

## Preview

Run `docker compose up --build` and open `http://127.0.0.1:18800`. The default Compose starts only the application. The first local visitor creates its administrator account: do not expose a fresh instance to other users before setup. Behind HTTPS, set `MEDIASEED_BASE_URL` to the exact external origin before starting.

For local development: install this package and run `uvicorn mediaseed.app:create_app --factory --host 127.0.0.1 --port 18800`. Set `MEDIASEED_DATA` to a private, disposable local directory. Tests use a synthetic qBittorrent transport and never start downloads.

## Local LXC storage

No external disk, NFS share or manually configured mount is required. Compose automatically creates independent `appdata` and `downloads` volumes under Docker's data directory on the LXC disk. The image initializes their directory ownership for UID 1000. These volumes persist across application recreation; do not use `down -v` unless intentionally deleting app data.

`GET /api/storage` reports real local download-volume capacity and write access after login; the Downloads screen shows free space. Downloads share the capacity of that underlying filesystem, so filling it can affect the LXC and other apps. A warning appears below 2 GiB free. This is not a quota or a download admission gate: torrent execution remains disabled pending the VPN supervisor.

For a private LAN preview set `MEDIASEED_BIND` to the LXC address and `MEDIASEED_BASE_URL` to `http://<LXC-address>:18800`, then start Compose. Keep it off the public tunnel and perform administrator setup on a trusted LAN. `MEDIASEED_PORT` changes the external port. This does not add the app to MediaHub's App Store yet. Optional external storage and torrent-runtime migration are separate follow-up work.

The adapter reads `MEDIASEED_QBIT_URL`, `MEDIASEED_QBIT_USER`, and `MEDIASEED_QBIT_PASSWORD_FILE`. These are trusted operator configuration, never request parameters. The secret file must not be committed. There is deliberately no connected torrent service in preview Compose.

## Before replacing Seedbox

The migration is not complete. Required next steps:

1. Extract the existing fail-closed VPN supervisor and approved storage checks into this application. A shared Gluetun network alone is not a complete replacement for those checks.
2. Add a private Gluetun/qBittorrent Compose project, interface binding, fresh tunnel verification, port-forward synchronization, namespace recreation and stop-on-failure behavior. Starting torrents is rejected until that gate exists.
3. Move existing RSS, tracker, retention, uploads, VPN profile/location and diagnostic workflows with focused regression tests.
4. Add scoped MediaHub managed login, manifest, install/update lifecycle, and links. Local login must continue to work without the hub.
5. Rehearse opt-in migration with disposable data, test desktop/mobile UI, backups and rollback; only then switch the beta installation.

No production service, beta account, torrent, download, VPN key or shared storage is changed by this repository. Do not connect the adapter to the existing live qBittorrent during preview: stop/remove are real operations.

`/healthz` reports application process availability only, not VPN protection or torrent health.

## Verification

`pytest tests -q` tests independent setup/login/logout, CSRF, host/origin rejection, filtered torrent reads, invalid hashes and the disabled start gate. Real VPN operation, Compose build and full feature parity are not covered by these tests.

See [FjordSeed feature checklist](docs/fjordseed-feature-checklist.md) for the explicit remaining scope. Browser checks cover source creation/editing, masked credentials, search, torrent details and responsive layouts with synthetic data; no real VPN or tracker account was contacted.