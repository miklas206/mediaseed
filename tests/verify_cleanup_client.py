"""Opt-in real-client contract check in a disposable --network none container.

Never mount production /config, /downloads or secrets into this container.
Only a generated one-byte, tracker-free torrent and temporary files are used.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


def main():
    assert os.environ.get("MEDIASEED_CLEANUP_ISOLATED") == "1"
    assert {name for _, name in socket.if_nameindex()} == {"lo"}, "Require --network none"
    with tempfile.TemporaryDirectory(prefix="mediaseed-cleanup-fixture-") as directory:
        root = Path(directory)
        profile = root / "profile"
        config = profile / "qBittorrent" / "config"
        config.mkdir(parents=True)
        (config / "qBittorrent.conf").write_text("[Preferences]\nWebUI\\Address=127.0.0.1\nWebUI\\LocalHostAuth=false\nWebUI\\Port=8080\n")
        downloads = root / "downloads"
        downloads.mkdir()
        data = downloads / "fixture.dat"
        data.write_bytes(b"x")
        info = b"d6:lengthi1e4:name11:fixture.dat12:piece lengthi16384e6:pieces20:" + hashlib.sha1(b"x").digest() + b"e"
        identifier = hashlib.sha1(info).hexdigest()
        content = b"d4:info" + info + b"e"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(path, payload=None, upload=None):
            body = None if payload is None else urllib.parse.urlencode(payload).encode()
            content_type = "application/x-www-form-urlencoded"
            if upload:
                boundary = "mediaseed-fixture-boundary"
                body = b"".join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode() for key, value in payload.items())
                body += (f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="fixture.torrent"\r\nContent-Type: application/x-bittorrent\r\n\r\n').encode() + upload + f'\r\n--{boundary}--\r\n'.encode()
                content_type = "multipart/form-data; boundary=" + boundary
            request = urllib.request.Request("http://127.0.0.1:8080/api/v2/" + path, data=body, headers={"Referer": "http://127.0.0.1:8080", "Content-Type": content_type})
            with opener.open(request, timeout=5) as response:
                return response.read()

        def wait_for(predicate):
            for _ in range(80):
                try:
                    if predicate():
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
            raise AssertionError("Isolated client condition timed out")

        binary = shutil.which("qbittorrent-nox") or "/app/qbittorrent-nox"
        process = subprocess.Popen([binary, "--profile=" + str(profile), "--webui-port=8080", "--confirm-legal-notice"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for(lambda: call("app/version"))
            assert json.loads(call("torrents/info")) == []
            for remove_files in (False, True):
                tag = "MediaSeed-Cleanup-" + ("a" if remove_files else "b") * 24
                call("torrents/add", {"stopped": "true", "paused": "true", "savepath": str(downloads), "tags": tag}, content)
                wait_for(lambda: len(json.loads(call("torrents/info"))) == 1)
                row = json.loads(call("torrents/info"))[0]
                assert row["hash"] == identifier and tag in row["tags"]
                call("torrents/setShareLimits", {"hashes": identifier, "ratioLimit": -1, "seedingTimeLimit": -1, "inactiveSeedingTimeLimit": -1, "shareLimitAction": "Stop"})
                row = json.loads(call("torrents/info"))[0]
                assert row["ratio_limit"] == row["seeding_time_limit"] == row["inactive_seeding_time_limit"] == -1
                call("torrents/delete", {"hashes": identifier, "deleteFiles": str(remove_files).lower()})
                wait_for(lambda: not json.loads(call("torrents/info")))
                if remove_files:
                    wait_for(lambda: not data.exists())
                else:
                    assert data.read_bytes() == b"x"
            print("PASS isolated qBittorrent: exact info-hash, import tags, unlimited share limits, preserve-files and delete-files actions; no external network or live data")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


if __name__ == "__main__":
    main()
