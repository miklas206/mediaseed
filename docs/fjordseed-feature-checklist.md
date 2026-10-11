# FjordSeed reference and MediaSeed scope

Reference: qlerup/fjordseed main README and source inventory reviewed 2026-10-11. This is a behavioral reference, not copied implementation. Existing MediaHub storage/VPN safety contracts must still be preserved. Do not describe the preview as feature-complete.

| Workflow | MediaSeed status | Remaining |
| --- | --- | --- |
| Torrent status, speed, ratio, progress | API and UI implemented | Real client verification, polling, pagination |
| Search/filter/sort | Implemented locally | Category/tag management |
| Details: files, tracker hosts, peers | Implemented, credentials excluded | File priority and peer controls |
| Stop/remove torrent | Implemented, files always preserved | Real-client integration test |
| Early-stop warning | Server confirmation below ratio 1 and 49 hours | Tracker-specific minimum goals, persisted custom-goal evaluation |
| Seeding goal | qBittorrent ratio and 49-hour limit API/UI | Trusted supervisor enforcement and explicit OR-goal validation |
| Add torrent/magnet | Blocked safely | Two-stage metadata preparation, file upload, path/ratio/action selection, exact-hash duplicate checks |
| Start/resume | Blocked safely | Fresh VPN/interface/port gate and namespace recreation |
| Download queue | Not implemented | Shared maximum 8, slot reservation across manual and RSS sources |
| Completion file actions | Not implemented | Keep/delete/verified archive with storage markers and rollback |
| RSS configuration | Independent saved CRUD implemented | Native VPN-bound qBittorrent RSS engine, item list, queue, pause/resume, events, duplicate/date filtering |
| NordicBytes account | Saved CRUD with masked key implemented | Bounded API adapter, refresh, account ratio/upload/download/bonus/HnR/freeleech/Green status |
| Benefits per torrent | Not implemented | Exact infohash matching and evidence-based benefit display |
| Green credit | Not implemented | Durable tracker-seeding accounting; never invent credit from qBittorrent ratio |
| VPN | Explicitly unverified | Own Gluetun project, profile provisioning, gate, supervisor, port synchronization, restart recovery |
| Hub integration | Not implemented | Scoped managed login, installation/update manifest, independent local access |

FjordSeed uses FjordVPN. MediaSeed is intended to own its VPN runtime instead; this architectural difference is not complete yet. The app's health endpoint reports process availability only. Never interpret saved feed/account settings as a running RSS/tracker integration.

## Data boundaries

No operations on the old Seedbox, beta or production were performed for this change. RSS/account deletion only deletes the local source setting, not torrents/downloads. Torrent remove sends `deleteFiles=false`. Source URLs are trusted configuration but must only be consumed by the future VPN-bound worker, not fetched directly by this web server.