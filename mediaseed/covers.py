import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from mediaseed.enrichment import Enrichment
from mediaseed.tracker_sites import origin, matching_accounts


def poster_url(value):
    if not isinstance(value, str) or len(value) > 500:
        return None
    try:
        address = urlsplit(value)
    except ValueError:
        return None
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
        self.pending = {}
        self.lock = asyncio.Lock()
        self.swarm_sink = None
        self.metadata_sink = None
        self.metadata_source = None
        self.recovery_needed = lambda torrents: {}
        self.username = username or (lambda identifier: None)
        self.enrichment = Enrichment(self)

    def remember(self, identifier, account, attributes):
        if str(attributes.get("info_hash", "")).lower() != identifier.lower():
            return
        if self.swarm_sink:
            self.swarm_sink(identifier.lower(), account, attributes)
        if self.metadata_sink:
            try:
                self.metadata_sink(identifier.lower(),account,attributes)
            except Exception as error:
                logging.getLogger(__name__).warning("Metadata observation failed (%s)",type(error).__name__)
        featured = attributes.get("featured") is True
        featured_bonus = featured and account.get("provider","nordicbytes") == "nordicbytes"
        try:
            freeleech = float(str(attributes.get("freeleech")).rstrip("%"))
        except (ValueError, TypeError):
            freeleech = None
        username = self.username(account["id"])
        evidence = {"status": "matched", "infoHash": identifier.lower(), "tracker": account["name"][:80],
                    "size": attributes.get("size") if type(attributes.get("size")) is int and attributes["size"] >= 0 else None,
                    "trackerId": account["id"], "baseUrl": origin(account), "greenRule": account.get("provider","nordicbytes") == "nordicbytes",
                    "checkedAt": time.time(), "createdAt": attributes.get("created_at") if isinstance(attributes.get("created_at"), str) and len(attributes["created_at"]) <= 80 else None,
                    "ownUpload": bool(username and attributes.get("uploader") == username),
                    "freeleech": 100 if featured_bonus else freeleech if freeleech in {0, 25, 50, 75, 100} else None,
                    "featured": featured, "doubleUpload": True if featured_bonus else attributes.get("double_upload") if type(attributes.get("double_upload")) is bool else None,
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
            accounts = self.accounts("trackers")[:10]
            if not accounts:
                return
            response = await self.qbit("/api/v2/torrents/info", payload={"limit": 500})
            torrents = response.json()
            if not isinstance(torrents, list):
                return
            observed = {row.get("hash"): row for row in torrents[:500] if isinstance(row, dict)}
            candidates = [*({**pending, **observed.get(pending.get("hash"), {})} for pending in self.pending.values()), *torrents[:500]]
            def priority(torrent):
                path = self.path(str(torrent.get("hash", ""))) if isinstance(torrent, dict) else None
                return 0 if path is not None and not path.is_file() else 1
            candidates.sort(key=priority)
            # An announce host must match a configured account. Do not probe
            # every account for local/unidentified torrents with no tracker.
            candidates = [row for row in candidates if isinstance(row, dict) and row.get("tracker")
                          and self.path(str(row.get("hash", ""))) is not None and matching_accounts(accounts, row["tracker"])]
            candidates = list({str(row["hash"]).lower(): row for row in candidates}.values())[:500]
            needed = self.recovery_needed(candidates)
            manual = set()
            identifiers = list(dict.fromkeys(str(row["hash"]).lower() for row in candidates))
            if identifiers:
                scopes = {self.enrichment.scope(account) for account in accounts}
                with self.enrichment.connect() as db:
                    for identifier, scope, payload in db.execute("SELECT hash,scope,state FROM jobs WHERE hash IN (" + ",".join("?" for _ in identifiers) + ")", identifiers):
                        state = json.loads(payload)
                        if scope in scopes and state.get("manual_at") and (state.get("force_image") or not state.get("detail_read")):
                            manual.add(identifier)
            candidates = [row for row in candidates if str(row["hash"]).lower() in manual
                          or not self.path(str(row["hash"])).is_file()
                          or needed.get(str(row["hash"]).lower(), True)]
            await self.enrichment.run(candidates, accounts)
            self.pending = {key: value for key, value in self.pending.items() if key not in observed}
            self.cleanup()

    def enqueue(self, identifier, name):
        if not self.accounts("trackers") or self.path(identifier) is None or not isinstance(name, str) or not name.strip():
            return False
        if time.monotonic() - self.checked.get(identifier, -1000) < 300:
            return False
        if len(self.pending) < 100:
            self.pending[identifier] = {'hash': identifier, 'name': name[:500]}
            return True
        return identifier in self.pending

    async def refresh(self, torrent):
        identifier = torrent["hash"].lower()
        accounts = matching_accounts(self.accounts("trackers")[:10], torrent.get("tracker"))
        if not accounts:
            return "unavailable"
        async with self.lock:
            if len(self.pending) >= 100 and identifier not in self.pending:
                return "busy"
            if not self.enrichment.request_refresh(identifier, accounts):
                return "cooldown"
            # Run through the ordinary bounded worker, with the current tracker
            # identity; never synthesize a match from the torrent name.
            self.pending[identifier] = dict(torrent)
        return "queued"

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
