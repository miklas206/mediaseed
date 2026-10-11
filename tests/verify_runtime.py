import asyncio
import base64
import json
import os
from pathlib import Path
import sqlite3
import tempfile

import httpx
from mediaseed.app import create_app


async def verify():
    with tempfile.TemporaryDirectory() as root:
        app = create_app(data_dir=Path(root), base_url="http://testserver")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=60) as client:
            credentials = {"username": "verification", "password": "synthetic-verification-only"}
            assert (await client.post("/api/auth/setup", json=credentials)).status_code == 200
            response = await client.post("/api/auth/login", json=credentials)
            assert response.status_code == 200
            client.headers["X-MediaSeed-CSRF"] = response.json()["csrf"]
            for path in ("runtime", "client", "queue"):
                response = await client.get("/api/" + path)
                assert response.status_code == 200, (path, response.status_code, response.text)
                data = response.json()
                allowed = {key: data.get(key) for key in ("ready", "downloadsEnabled", "publicIp", "forwardedPort", "rssWorker", "version", "active", "limit", "waiting") if key in data}
                print(path, json.dumps(allowed))
    token = Path(os.environ["MEDIASEED_VPN_TOKEN_FILE"]).read_text().strip()
    async with httpx.AsyncClient(base_url=os.environ["MEDIASEED_VPN_URL"], headers={"X-MediaSeed-Key": token}, timeout=60, trust_env=False) as client:
        for path in ("app/preferences", "rss/items"):
            response = await client.post("/api/integration/rpc", json={"path": path, "method": "GET", "payload": {"withData": "true"} if path == "rss/items" else {}})
            assert response.status_code == 200, (path, response.status_code)
            data = json.loads(base64.b64decode(response.json()["body"]))
            if path == "app/preferences":
                fields = {key: data.get(key) for key in ("current_network_interface", "listen_port", "queueing_enabled", "max_active_downloads", "rss_processing_enabled", "rss_auto_downloading_enabled")}
                print("Native preferences", json.dumps(fields))
                assert data["max_active_downloads"] == 8 and data["queueing_enabled"] and data["rss_processing_enabled"] and not data["rss_auto_downloading_enabled"]
            else:
                assert isinstance(data, dict)
                print("Native RSS API verified")
    with sqlite3.connect(Path(os.environ["MEDIASEED_DATA"]) / "mediaseed.sqlite") as connection:
        feeds = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM settings WHERE kind='feeds'")]
        print(json.dumps({"savedFeeds": len(feeds), "activeFeeds": sum(bool(feed.get("enabled")) for feed in feeds), "accounts": connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]}))


if __name__ == "__main__":
    asyncio.run(verify())