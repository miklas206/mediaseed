import asyncio
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from urllib.parse import urlencode

import httpx
from mediaseed.rss_worker import RssWorker


async def verify():
    feed_id = secrets.token_hex(12)
    name = ("MediaSeed-RSS-verification-" + secrets.token_hex(8)).encode()
    info = b"d6:lengthi1e4:name" + str(len(name)).encode() + b":" + name + b"12:piece lengthi16384e6:pieces20:" + hashlib.sha1(b"x").digest() + b"e"
    content = b"d4:info" + info + b"e"
    identifier = hashlib.sha1(info).hexdigest()
    feed = {"id": feed_id, "enabled": True, "name": "Synthetic test", "url": "https://nordicbytes.org/rss/synthetic-verification", "folder": "", "ratio": 2, "start_at": "2026-01-01T00:00:00Z"}
    token = Path(os.environ["MEDIASEED_VPN_TOKEN_FILE"]).read_text().strip()
    async with httpx.AsyncClient(base_url=os.environ["MEDIASEED_VPN_URL"], headers={"X-MediaSeed-Key": token}, timeout=60, trust_env=False) as client:
        async def status():
            response = await client.get("/api/integration/status")
            response.raise_for_status()
            return response.json()

        starts = []

        async def qbit(path, method="GET", payload=None, files=None):
            if path == "/api/v2/rss/items":
                return httpx.Response(200, json={"MediaSeed-RSS-" + feed_id: {"url": feed["url"], "articles": [{"id": "synthetic", "date": "2026-01-02T00:00:00Z", "torrentURL": "https://nordicbytes.org/torrents/download/12/synthetic"}]}})
            if path == "/api/v2/rss/torrent":
                return httpx.Response(200, content=content)
            if files:
                query = urlencode({"ratio": payload["ratioLimit"], "savepath": "/downloads"})
                response = await client.post("/api/integration/upload?" + query, content=files["torrents"][1])
            else:
                response = await client.post("/api/integration/rpc", json={"path": path.removeprefix("/api/v2/"), "method": method, "payload": payload or {}})
            response.raise_for_status()
            result = response.json()
            if path.endswith("/start"):
                assert payload["hashes"] == identifier
                starts.append(identifier)
            return httpx.Response(result["status"], content=base64.b64decode(result["body"]))

        with tempfile.TemporaryDirectory() as root:
            @contextmanager
            def database():
                with sqlite3.connect(Path(root) / "rss.sqlite") as connection:
                    connection.row_factory = sqlite3.Row
                    yield connection
            try:
                assert (await status())["ready"]
                worker = RssWorker(database, lambda kind: [feed], qbit, status)
                await worker.tick()
                assert len(starts) == 1, worker.reports
                await RssWorker(database, lambda kind: [feed], qbit, status).tick()
                assert len(starts) == 1, "Restart repeated the RSS start"
                response = await qbit("/api/v2/torrents/info", payload={"hashes": identifier})
                rows = response.json()
                assert len(rows) == 1 and rows[0]["ratio_limit"] == 2
                print(json.dumps({"syntheticRssImport": True, "exactHashStart": True, "restartDuplicateBlocked": True, "state": rows[0]["state"], "ratio": rows[0]["ratio_limit"]}))
            finally:
                await qbit("/api/v2/torrents/delete", "POST", {"hashes": identifier, "deleteFiles": "false"})
                response = await qbit("/api/v2/torrents/info", payload={"hashes": identifier})
                assert response.json() == []
                print("Only the synthetic torrent removed; no real feeds activated")


if __name__ == "__main__":
    asyncio.run(verify())