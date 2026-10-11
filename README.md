# MediaSeed

Independent seedbox application for MediaHub, inspired by FjordSeed's separate-app model. This repository is an early preview, not a replacement for the existing Seedbox installation.

## Implemented

- Independent FastAPI runtime, SQLite account database and static application UI.
- Administrator setup, Argon2 password storage, expiring HttpOnly sessions, CSRF protection, login throttling and strict host/origin checks.
- Authenticated qBittorrent 5 API adapter: bounded torrent count, stop a torrent and remove a torrent without deleting downloaded files. Adding and starting torrents is rejected until the VPN gate exists.
- No dependency on MediaHub Core, Agent or FjordVPN. No Docker socket in the application container.

## Preview

Run `docker compose up --build` and open `http://127.0.0.1:18800`. The default Compose starts only the application. The first local visitor creates its administrator account: do not expose a fresh instance to other users before setup. Behind HTTPS, set `MEDIASEED_BASE_URL` to the exact external origin before starting.

For local development: install this package and run `uvicorn mediaseed.app:create_app --factory --host 127.0.0.1 --port 18800`. Set `MEDIASEED_DATA` to a private, disposable local directory. Tests use a synthetic qBittorrent transport and never start downloads.

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