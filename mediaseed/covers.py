import asyncio
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def poster_url(value):
    if not isinstance(value, str) or len(value) > 500:
        return None
    address = urlsplit(value)
    prefix = r"/tmdb/t/p/" if address.netloc == "tmdbc.org" else r"/t/p/"
    if address.scheme != "https" or address.netloc not in {"tmdbc.org", "image.tmdb.org"} or address.query or address.fragment:
        return None
    return value if re.fullmatch(prefix + r"(?:w[0-9]{2,4}|original)/[A-Za-z0-9_-]+\.(?:jpg|png|webp)", address.path) else None


def matching_poster(data, identifier):
    rows = data.get("data", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return None
    for row in rows[:50]:
        attributes = row.get("attributes", {}) if isinstance(row, dict) else {}
        if not isinstance(attributes, dict) or str(attributes.get("info_hash", "")).lower() != identifier.lower():
            continue
        meta = attributes.get("meta")
        return poster_url(meta.get("poster")) if isinstance(meta, dict) else None
    return None


class Covers:
    def __init__(self, root, accounts, tracker_read, qbit):
        self.root = Path(root) / "cover-cache"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.accounts = accounts
        self.tracker_read = tracker_read
        self.qbit = qbit
        self.checked = {}
        self.lock = asyncio.Lock()

    def path(self, identifier):
        if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", identifier):
            return None
        return self.root / (hashlib.sha256(identifier.lower().encode()).hexdigest() + ".img")

    async def tick(self):
        async with self.lock:
            accounts = self.accounts("trackers")[:3]
            if not accounts:
                return
            response = await self.qbit("/api/v2/torrents/info", payload={"limit": 500})
            torrents = response.json()
            if not isinstance(torrents, list):
                return
            budget = 3
            for torrent in torrents[:500]:
                if not isinstance(torrent, dict):
                    continue
                identifier = torrent.get("hash", "")
                path = self.path(identifier)
                if path is None or time.monotonic() - self.checked.get(identifier, -1000) < 300:
                    continue
                if path.exists() and time.time() - path.stat().st_mtime < 86400:
                    continue
                if budget == 0:
                    break
                budget -= 1
                self.checked[identifier] = time.monotonic()
                try:
                    for account in accounts:
                        data = await self.tracker_read(account, "/api/torrents/filter", {"name": str(torrent.get("name", ""))[:200], "perPage": 50})
                        poster = matching_poster(data, identifier)
                        if poster:
                            await self.download(poster, path)
                            break
                except Exception:
                    continue
            self.cleanup()
            if len(self.checked) > 1000:
                self.checked = {key: value for key, value in self.checked.items() if time.monotonic() - value < 300}

    async def download(self, url, path):
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 2 * 1024 * 1024:
                        return
        if not (content.startswith(b"\xff\xd8\xff") or content.startswith(b"\x89PNG\r\n\x1a\n") or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")):
            return
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.chmod(0o600)
        temporary.replace(path)

    def cleanup(self):
        files = sorted(self.root.glob("*.img"), key=lambda item: item.stat().st_mtime, reverse=True)
        total = 0
        for path in files:
            size = path.stat().st_size
            if time.time() - path.stat().st_mtime > 7 * 86400 or total + size > 64 * 1024 * 1024:
                path.unlink(missing_ok=True)
            else:
                total += size