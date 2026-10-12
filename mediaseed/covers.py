import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import httpx


def poster_url(value):
    if not isinstance(value, str) or len(value) > 500:
        return None
    address = urlsplit(value)
    prefix = r"/tmdb/t/p/" if address.netloc == "tmdbc.org" else r"/t/p/"
    if address.scheme != "https" or address.netloc not in {"tmdbc.org", "image.tmdb.org"} or address.query or address.fragment:
        return None
    return value if re.fullmatch(prefix + r"(?:w[0-9]{2,4}|original)/[A-Za-z0-9_-]+\.(?:jpg|png|webp)", address.path) else None


def matching_attributes(data, identifier):
    rows = data.get("data", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return None
    for row in rows[:50]:
        attributes = row.get("attributes", {}) if isinstance(row, dict) else {}
        if not isinstance(attributes, dict) or str(attributes.get("info_hash", "")).lower() != identifier.lower():
            continue
        return attributes
    return None


def matching_poster(data, identifier):
    attributes = matching_attributes(data, identifier)
    meta = attributes.get("meta") if attributes else None
    return poster_url(meta.get("poster")) if isinstance(meta, dict) else None


class Covers:
    def __init__(self, root, accounts, tracker_read, qbit, username=None):
        self.root = Path(root) / "cover-cache"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.accounts = accounts
        self.tracker_read = tracker_read
        self.qbit = qbit
        self.checked = {}
        self.lock = asyncio.Lock()
        self.username = username or (lambda identifier: None)

    def remember(self, identifier, account, attributes):
        if str(attributes.get("info_hash", "")).lower() != identifier.lower():
            return
        featured = attributes.get("featured") is True
        try:
            freeleech = float(str(attributes.get("freeleech")).rstrip("%"))
        except (ValueError, TypeError):
            freeleech = None
        username = self.username(account["id"])
        evidence = {"status": "matched", "infoHash": identifier.lower(), "tracker": account["name"][:80],
                    "checkedAt": time.time(), "createdAt": attributes.get("created_at") if isinstance(attributes.get("created_at"), str) and len(attributes["created_at"]) <= 80 else None,
                    "ownUpload": bool(username and attributes.get("uploader") == username),
                    "freeleech": 100 if featured else freeleech if freeleech in {0, 25, 50, 75, 100} else None,
                    "featured": featured, "doubleUpload": True if featured else attributes.get("double_upload") if type(attributes.get("double_upload")) is bool else None,
                    "internal": attributes.get("internal") is True, "refundable": attributes.get("refundable") is True}
        path = self.path(identifier).with_suffix(".json")
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(evidence)); temporary.chmod(0o600); temporary.replace(path)

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
            budget = 6
            for torrent in torrents[:500]:
                if not isinstance(torrent, dict):
                    continue
                identifier = torrent.get("hash", "")
                path = self.path(identifier)
                if path is None or time.monotonic() - self.checked.get(identifier, -1000) < 300:
                    continue
                if budget == 0:
                    break
                self.checked[identifier] = time.monotonic()
                try:
                    for account in accounts:
                        name = str(torrent.get("name", ""))[:200]
                        release = re.sub(r"\.(?:mkv|mp4|m4v|avi|mov|wmv|webm|m2ts|ts|iso|flac|mp3|m4a|ogg|wav)$", "", name, flags=re.I)
                        variants = list(dict.fromkeys([release, name, re.sub(r"[._]+", " ", release).strip(), re.sub(r"[._]+", " ", name).strip()]))
                        matched = False
                        for variant in variants:
                            cursor = None
                            for page in range(3):
                                if budget == 0:
                                    break
                                budget -= 1
                                params = {"name": variant, "perPage": 50}
                                if cursor:
                                    params["cursor"] = cursor
                                data = await self.tracker_read(account, "/api/torrents/filter", params)
                                attributes = matching_attributes(data, identifier)
                                if attributes:
                                    self.remember(identifier, account, attributes)
                                    meta = attributes.get("meta")
                                    poster = poster_url(meta.get("poster")) if isinstance(meta, dict) else None
                                    if poster and (not path.exists() or time.time() - path.stat().st_mtime >= 86400):
                                        await self.download(poster, path)
                                    matched = True
                                    break
                                links = data.get("links", {}) if isinstance(data, dict) else {}
                                next_link = links.get("next") if isinstance(links, dict) else None
                                meta = data.get("meta", {}) if isinstance(data, dict) else {}
                                next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
                                if isinstance(next_cursor, str) and 0 < len(next_cursor) <= 2048:
                                    cursor = next_cursor
                                    continue
                                if not isinstance(next_link, str) or len(next_link) > 4096:
                                    break
                                next_address = urlsplit(next_link)
                                cursor = parse_qs(next_address.query).get("cursor", [None])[0]
                                if not cursor or len(cursor) > 2048:
                                    break
                            if matched or budget == 0:
                                break
                        if matched or budget == 0:
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
        for path in self.root.glob("*.json"):
            if time.time() - path.stat().st_mtime > 7 * 86400:
                path.unlink(missing_ok=True)
