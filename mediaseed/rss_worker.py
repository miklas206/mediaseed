import asyncio
import hashlib
import re
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from fastapi import HTTPException

from mediaseed.cleanup import source_hashes
from mediaseed.activity import Activity
from mediaseed import feed_uploads


def title_matches(title, feed):
    def normalize(value):
        return ' '.join(re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', value).casefold().replace('_', ' ')).split())
    title = normalize(title)
    include, exclude = feed.get('include', []), feed.get('exclude', [])
    return (not include or any(normalize(value) in title for value in include)) and not any(normalize(value) in title for value in exclude)


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
        self.benefits_lookup = None
        self.metadata_budget = 6
        self.activity = Activity(database)
        with database() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS rss_history (token TEXT PRIMARY KEY, feed TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, torrent_hash TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS rss_observations (feed TEXT NOT NULL, token TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(feed,token))")

        with database() as connection:
            feed_uploads.migrate(connection)

    def observe(self, feed, token, article, status):
        if status in {'added', 'import_unconfirmed', 'metadata_unknown', 'badges_unverified', 'too_large', 'size_unknown'}:
            self.activity.record('rss', token, article.get('title', ''), status, feed=feed['id'])
        with self.database() as connection:
            connection.execute("INSERT INTO rss_observations VALUES (?,?,?,?,?) ON CONFLICT(feed,token) DO UPDATE SET status=excluded.status,created=excluded.created WHERE NOT (rss_observations.status='added' AND excluded.status='already_processed')", (feed['id'], token, str(article.get('title', ''))[:300], status, time.time()))
            connection.execute("DELETE FROM rss_observations WHERE rowid NOT IN (SELECT rowid FROM rss_observations ORDER BY created DESC LIMIT 1000)")

    def history(self, identifier):
        with self.database() as connection:
            rows = connection.execute("SELECT title,status,created FROM rss_observations WHERE feed=? ORDER BY created DESC LIMIT 100", (identifier,)).fetchall()
        return [dict(zip(('title','status','created'), row)) for row in rows]

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
            self.metadata_budget = 6
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
            self.activity.record('rss', feed['id'], feed['name'], 'feed_error', feed=feed['id'])
            self.reports[feed["id"]] = "Feed kunne ikke hentes"
            return
        for article in articles[:500]:
            if not isinstance(article, dict) or article.get("isRead"):
                continue
            observation = hashlib.sha256((str(article.get('id', '')) + str(article.get('title', ''))).encode()).hexdigest()
            if not date_matches(article, feed.get('start_at')):
                self.observe(feed, observation, article, 'before_start_date')
                continue
            if not title_matches(str(article.get('title', '')), feed):
                self.observe(feed, observation, article, 'title_filtered')
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
                    self.observe(feed, observation, article, 'already_processed')
                    continue
            if self.metadata_budget <= 0:
                self.reports[feed['id']] = 'Waiting for the next bounded metadata check'
                return
            self.metadata_budget -= 1
            response = await self.qbit("/api/v2/rss/torrent", payload={"url": link})
            content = response.content
            if feed.get('max_bytes') or feed.get('badges'):
                try:
                    metadata = source_hashes(content=content, include_metadata=True)
                except HTTPException:
                    self.observe(feed, observation, article, 'metadata_unknown')
                    continue
                if feed.get('max_bytes') and (metadata['size'] is None or metadata['size'] > feed['max_bytes']):
                    self.observe(feed, observation, article, 'size_unknown' if metadata['size'] is None else 'too_large')
                    continue
                if feed.get('badges'):
                    try:
                        evidence = await self.benefits_lookup(metadata['hashes'][0], str(article.get('title', ''))) if self.benefits_lookup else {}
                        flags = {'freeleech': evidence.get('freeleech') == 100, 'double_upload': evidence.get('doubleUpload') is True,
                                 'featured': evidence.get('featured') is True, 'internal': evidence.get('internal') is True, 'refundable': evidence.get('refundable') is True}
                        valid = evidence.get('status') == 'matched' and evidence.get('infoHash') in metadata['hashes'] and 0 <= time.time() - evidence.get('checkedAt', 0) < 300
                        if not valid or not all(flags.get(flag) for flag in feed['badges']):
                            self.observe(feed, observation, article, 'badges_unverified')
                            continue
                    except Exception:
                        self.observe(feed, observation, article, 'badges_unverified')
                        continue
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
                self.observe(feed, observation, article, 'import_unconfirmed')
                self.reports[feed["id"]] = "Tilføjelse ikke bekræftet; automatisk genforsøg blokeret"
                return
            identifier = identifiers[0].lower()
            self.observe(feed, observation, article, 'added')
            with self.database() as connection:
                feed_uploads.record(connection, feed["id"], identifier, article.get("title", ""), time.time())
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
