import asyncio
import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from fastapi import HTTPException


def date_matches(article, start):
    if not start:
        return True
    try:
        value = article.get("date", "")
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            stamp = parsedate_to_datetime(value)
        threshold = datetime.fromisoformat(start.replace("Z", "+00:00"))
        return stamp.tzinfo is not None and stamp <= datetime.now(timezone.utc) and stamp >= threshold
    except (TypeError, ValueError, AttributeError):
        return False


class RssWorker:
    def __init__(self, database, rows, qbit, vpn_status, storage=None):
        self.database, self.rows, self.qbit, self.vpn_status = database, rows, qbit, vpn_status
        self.storage = storage
        self.lock = asyncio.Lock()
        self.reports = {}
        with database() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS rss_history (token TEXT PRIMARY KEY, feed TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, torrent_hash TEXT)")

    def destination(self, feed):
        folder = feed.get("folder", "")
        if feed.get("location") is not None:
            if self.storage is None:
                raise HTTPException(409, "Download folder is unavailable or read-only")
            return self.storage.destination(feed["location"], folder)
        # Preserve legacy local feed paths without silently moving old downloads.
        if folder and (not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", folder) or folder.split("/")[0] == "media"):
            raise HTTPException(422, "Choose an approved download folder")
        return "/downloads" + ("/" + folder if folder else "")

    async def tick(self):
        async with self.lock:
            if not (await self.vpn_status()).get("downloadsEnabled"):
                return
            feeds = [row for row in self.rows("feeds") if row.get("enabled")][:20]
            if not feeds:
                return
            response = await self.qbit("/api/v2/rss/items", payload={"withData": "true"})
            items = response.json()
            if not isinstance(items, dict):
                return
            for feed in feeds:
                try:
                    await self.process(feed, items)
                except HTTPException as error:
                    self.reports[feed["id"]] = str(error.detail)
                except Exception:
                    self.reports[feed["id"]] = "Afventer VPN eller gyldige feedposter"

    async def process(self, feed, items):
        if urlsplit(feed["url"]).netloc != "nordicbytes.org":
            self.reports[feed["id"]] = "Kun NordicBytes-feeds er aktiveret endnu"
            return
        destination = self.destination(feed)
        item_path = "MediaSeed-RSS-" + feed["id"]
        item = items.get(item_path)
        if not isinstance(item, dict):
            configured = {"MediaSeed-RSS-" + row["id"] for row in self.rows("feeds")}
            stale = [path for path, row in items.items()
                     if isinstance(row, dict) and row.get("url") == feed["url"]
                     and re.fullmatch(r"MediaSeed-RSS-[a-f0-9]{24}", path)
                     and path not in configured]
            if stale:
                for path in stale:
                    await self.qbit("/api/v2/rss/removeItem", "POST", {"path": path})
                self.reports[feed["id"]] = "Gammel feedregistrering ryddet; synkroniserer"
                return
            categories = (await self.qbit("/api/v2/torrents/categories")).json()
            if not isinstance(categories, dict):
                raise ValueError("Invalid client categories")
            if item_path not in categories:
                await self.qbit("/api/v2/torrents/createCategory", "POST", {"category": item_path, "savePath": destination})
            await self.qbit("/api/v2/rss/addFeed", "POST", {"url": feed["url"], "path": item_path})
            self.reports[feed["id"]] = "Feed oprettet i VPN-klienten; afventer poster"
            return
        if item.get("url") != feed["url"]:
            await self.qbit("/api/v2/rss/removeItem", "POST", {"path": item_path})
            self.reports[feed["id"]] = "Feedadresse ændret; synkroniserer"
            return
        articles = item.get("articles", [])
        if item.get("hasError") or not isinstance(articles, list):
            self.reports[feed["id"]] = "Feed kunne ikke hentes"
            return
        for article in articles[:500]:
            if not isinstance(article, dict) or article.get("isRead") or not date_matches(article, feed.get("start_at")):
                continue
            link = article.get("torrentURL") or article.get("link")
            if not isinstance(link, str) or len(link) > 4096:
                continue
            address = urlsplit(link)
            if address.scheme != "https" or address.netloc != "nordicbytes.org" or not re.match(r"^/torrents?/download/[^/]+", address.path) or address.fragment:
                continue
            token = hashlib.sha256((feed["id"] + "\n" + str(article.get("id", "")) + "\n" + link).encode()).hexdigest()
            with self.database() as connection:
                if connection.execute("SELECT 1 FROM rss_history WHERE token=?", (token,)).fetchone():
                    continue
            response = await self.qbit("/api/v2/rss/torrent", payload={"url": link})
            content = response.content
            destination = self.destination(feed)
            if next((row for row in self.rows("feeds") if row["id"] == feed["id"]), None) != feed:
                self.reports[feed["id"]] = "Feed changed; waiting for the next refresh"
                return
            file_token = hashlib.sha256(content).hexdigest()
            with self.database() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT 1 FROM rss_history WHERE token IN (?,?)", (token, file_token)).fetchone():
                    continue
                connection.execute("INSERT INTO rss_history VALUES (?,?,?,strftime('%s','now'),NULL)", (token, feed["id"], "intent"))
                connection.execute("INSERT INTO rss_history VALUES (?,?,?,strftime('%s','now'),NULL)", (file_token, feed["id"], "intent"))
            response = await self.qbit("/api/v2/torrents/add", "POST", {"stopped": "true", "paused": "true", "savepath": destination, "autoTMM": "false", "ratioLimit": str(feed["ratio"]), "seedingTimeLimit": "2940", "category": item_path}, files={"torrents": ("rss.torrent", content, "application/x-bittorrent")})
            outcome = response.json()
            identifiers = outcome.get("added_torrent_ids", []) if isinstance(outcome, dict) else []
            if outcome.get("success_count") != 1 or outcome.get("failure_count") != 0 or len(identifiers) != 1 or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", str(identifiers[0])):
                self.reports[feed["id"]] = "Tilføjelse ikke bekræftet; automatisk genforsøg blokeret"
                return
            identifier = identifiers[0].lower()
            with self.database() as connection:
                connection.execute("UPDATE rss_history SET status='added',torrent_hash=? WHERE token IN (?,?)", (identifier, token, file_token))
            current = next((row for row in self.rows("feeds") if row["id"] == feed["id"]), None)
            if current == feed and (await self.vpn_status()).get("downloadsEnabled"):
                self.destination(feed)
                await self.qbit("/api/v2/torrents/topPrio", "POST", {"hashes": identifier})
                await self.qbit("/api/v2/torrents/start", "POST", {"hashes": identifier})
                self.reports[feed["id"]] = "Torrent tilføjet til VPN-downloadkø"
            else:
                self.reports[feed["id"]] = "Torrent tilføjet stoppet; feed eller VPN ændret"
            return
        self.reports[feed["id"]] = "Afventer nye poster efter startdato"
