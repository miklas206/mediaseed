# Scoped file-check watchdog

`recheck_watch.py` watches one explicitly configured existing torrent. It polls
the existing MediaVPN integration through the app container every minute. It
never invokes torrent start, adds a torrent, moves/deletes files, restarts apps,
remounts NAS storage or changes VPN configuration.

The operator chooses the exact infohash, expected byte/file counts, save path
and a representative readable file. Removed torrents and changed paths/sizes
stop the watch. An active file check is never interrupted, including a slow
`checkingResumeData`; a 30-minute lack of movement is recorded as `stalled_check`.
Actual error/missing-file states wait before recovery. Recovery requires the
selected NAS location to pass the app's existing approval/symlink checks, a
successful read, a ready VPN and an acknowledged stop before requesting recheck.
The torrent remains paused after checking. Partial results are reported without
starting a new download. Completion requires all files and all pieces verified.

Recovery reservations are written before I/O and survive cancellation, process
restarts and upgrades. There are at most five recovery attempts, separated by
2 minutes, 5 minutes, 15 minutes, 1 hour and 3 hours. A watch expires after 72
hours by default (maximum 168). Read-only outage polling is not a new recovery
attempt. Terminal watches make no more client requests. State and a rotating
2 MiB event journal are owner-only. An optional client log directory supplies
scoped file-error diagnostics, with URLs/tokens redacted.

The watchdog reads the existing machine token inside the already running app.
It does not export credentials or make tracker API calls. The service runs on
the Docker host as root because access to Docker is host-administrator access;
this does not grant the MediaSeed app new privileges. Its host network access
is restricted to Unix sockets. Worker timeouts and an exclusive ledger lock
bound each invocation and prevent concurrent recovery actions.

Example configuration (all identifiers below are synthetic):

```json
{
  "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "container": "existing-mediaseed-app",
  "save_path": "/downloads/media/nas-example/files/library",
  "probe_file": "Synthetic.Release/E01.mkv",
  "size": 123456789,
  "file_count": 12,
  "max_retries": 5,
  "hours": 72
}
```

Copy a reviewed config outside the workspace and run as root on the existing
Docker host:

```sh
python3 ops/install_recheck_watch.py --config /root/recheck-job.json --name mediaseed-julepakke-watch
```

The installer uses only Python's standard library and the existing Docker/
systemd runtime, creates and enables the timer, and immediately samples status.
It stops with a prerequisite error when the runtime is absent. Re-running it
with the same configuration updates the code/units while preserving the ledger,
retry budget and credentials. A changed configuration is rejected, preserving
the old job. An app image update leaves the host-side watch and its ledger intact.
No app/database migration or app/container restart is needed.

Status, event journal and configuration are under
`/opt/mediahub/maintenance/mediaseed-julepakke-watch/`.

```sh
systemctl status mediaseed-julepakke-watch.timer
cat /opt/mediahub/maintenance/mediaseed-julepakke-watch/state.json
journalctl -u mediaseed-julepakke-watch.service
```

Stop with `systemctl disable --now mediaseed-julepakke-watch.timer`; preserve the
ledger, especially the retry count. Disabling or uninstalling the watch does
not alter the torrent. It does not post chat/email notifications.

Validation: 11 synthetic tests cover the actual embedded worker's operation
sequence, storage/VPN/stop gates, stalled checks, persistent five-attempt limits,
cancellation, verification without starting, incomplete/manual/removed jobs,
outages, expiry, redacted file errors, and idempotent fresh/upgrade installation.
Run `python3 -m unittest discover -s ops -p 'test_*.py' -v`.
FjordSeed reference: `qlerup/fjordseed`, `main`,
`a6e5699ce59a76aa996629fc25f486b6e8d0a0fb`; its exact-hash recheck/stop sequencing
was inspected. This operator watch uses MediaSeed's own existing integration.
