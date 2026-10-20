"""Durable, bounded recovery of missing tracker metadata and public posters."""
import hashlib
import json
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

from mediaseed.tracker_sites import identity, matching_accounts

MAX_ATTEMPTS = 5
DELAYS = (300, 900, 3600, 21600, 86400)


class Enrichment:
    def __init__(self, covers):
        self.covers = covers
        self.path = covers.root / "metadata-retries.sqlite"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (hash TEXT, scope TEXT, state TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(hash,scope))")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def scope(account):
        # Credentials only contribute to a one-way identity; never persist them.
        credential = identity(account).hex() if account.get("api_key") else ""
        return hashlib.sha256((str(account["id"]) + credential).encode()).hexdigest()

    def load(self, identifier, account):
        with self.connect() as db:
            row = db.execute("SELECT state FROM jobs WHERE hash=? AND scope=?", (identifier, self.scope(account))).fetchone()
        return json.loads(row[0]) if row else {"search": 0, "detail": 0, "image": 0, "refresh": 0, "variant": 0, "page": 0}

    def save(self, identifier, account, state):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)", (identifier, self.scope(account), json.dumps(state), time.time()))

    def due(self, state, stage):
        return state.get(stage, 0) < MAX_ATTEMPTS and time.time() >= state.get(stage + "_at", 0)

    def attempted(self, identifier, account, state, stage):
        state[stage] = state.get(stage, 0) + 1
        state[stage + "_at"] = time.time() + DELAYS[state[stage] - 1]
        # Reserve the attempt before I/O: cancellation/restart cannot reset it.
        self.save(identifier, account, state)

    async def run(self, torrents, accounts):
        from mediaseed.covers import poster_url

        budget = 6
        seen = set()
        for torrent in torrents:
            if not isinstance(torrent, dict):
                continue
            identifier = str(torrent.get("hash", "")).lower()
            path = self.covers.path(identifier)
            if path is None or identifier in seen:
                continue
            seen.add(identifier)
            # A long search cannot monopolize a tick and starve other torrents.
            allowance = min(2, budget)
            for account in matching_accounts(accounts, torrent.get("tracker")):
                if budget <= 0:
                    break
                state = self.load(identifier, account)
                try:
                    if not state.get("seeded") and self.covers.metadata_source:
                        saved = self.covers.metadata_source(identifier)
                        if isinstance(saved, dict) and saved.get("account") == account["id"]:
                            poster = poster_url(saved.get("poster"))
                            if poster and not state.get("poster"):
                                state["poster"] = poster
                            state["has_description"] = state.get("has_description", False) or bool(saved.get("description"))
                            state["has_media_info"] = state.get("has_media_info", False) or bool(saved.get("media_info"))
                        state["seeded"] = True
                        self.save(identifier, account, state)
                    if not state.get("matched") and self.due(state, "search") and allowance:
                        name = str(torrent.get("name", ""))[:200]
                        release = re.sub(r"\.(?:mkv|mp4|m4v|avi|mov|wmv|webm|m2ts|ts|iso|flac|mp3|m4a|ogg|wav)$", "", name, flags=re.IGNORECASE)
                        variants = list(dict.fromkeys([release, name, re.sub(r"[._]+", " ", release).strip(), re.sub(r"[._]+", " ", name).strip()]))
                        while allowance and self.due(state, "search"):
                            params = {"name": variants[state.get("variant", 0) % len(variants)], "perPage": 50}
                            if state.get("cursor"):
                                params["cursor"] = state["cursor"]
                            self.attempted(identifier, account, state, "search")
                            allowance -= 1
                            budget -= 1
                            data = await self.covers.tracker_read(account, "/api/torrents/filter", params)
                            rows = data.get("data", []) if isinstance(data, dict) else []
                            found = next((r for r in rows[:50] if isinstance(r, dict) and isinstance(r.get("attributes"), dict)
                                          and str(r["attributes"].get("info_hash", "")).lower() == identifier), None) if isinstance(rows, list) else None
                            if found:
                                attributes = found["attributes"]
                                self.observe(identifier, account, state, attributes)
                                record_id = str(found.get("id", ""))
                                if re.fullmatch(r"[1-9][0-9]{0,17}", record_id):
                                    state["record_id"] = record_id
                                state["matched"] = bool(state.get("record_id") or state.get("complete"))
                                self.save(identifier, account, state)
                                break
                            cursor = self.next_cursor(data)
                            if cursor and state.get("page", 0) < 2:
                                state.update(cursor=cursor, page=state.get("page", 0) + 1, search_at=0)
                            else:
                                state.update(cursor=None, page=0, variant=state.get("variant", 0) + 1)
                            self.save(identifier, account, state)

                    if state.get("record_id") and (not state.get("complete") or not state.get("detail_read")) and self.due(state, "detail") and allowance:
                        self.attempted(identifier, account, state, "detail")
                        allowance -= 1
                        budget -= 1
                        data = await self.covers.tracker_read(account, "/api/torrents/" + state["record_id"], None)
                        record = data.get("data", data) if isinstance(data, dict) else {}
                        attributes = record.get("attributes", {}) if isinstance(record, dict) else {}
                        if isinstance(record, dict) and str(record.get("id")) == state["record_id"] and str(attributes.get("info_hash", "")).lower() == identifier:
                            state["detail_read"] = True
                            self.observe(identifier, account, state, attributes)
                            self.save(identifier, account, state)

                    # Keep existing short-lived benefit evidence fresh after a
                    # successful enrichment, using the known exact record ID.
                    if state.get("complete") and state.get("detail_read") and state.get("record_id") and self.due(state, "refresh") and allowance:
                        self.attempted(identifier, account, state, "refresh")
                        allowance -= 1
                        budget -= 1
                        data = await self.covers.tracker_read(account, "/api/torrents/" + state["record_id"], None)
                        record = data.get("data", data) if isinstance(data, dict) else {}
                        attributes = record.get("attributes", {}) if isinstance(record, dict) else {}
                        if isinstance(record, dict) and str(record.get("id")) == state["record_id"] and str(attributes.get("info_hash", "")).lower() == identifier:
                            self.observe(identifier, account, state, attributes)
                            state.update(refresh=0, refresh_at=time.time() + 300)
                            self.save(identifier, account, state)
                except Exception as error:  # noqa: BLE001 - isolate one tracker job
                    logging.getLogger(__name__).warning("Metadata recovery deferred (%s)", type(error).__name__)

                # Image failures use the approved URL already in the job. They
                # do not cause another authenticated tracker lookup.
                poster = poster_url(state.get("poster"))
                if poster and self.due(state, "image"):
                    if path.is_file() and time.time() - path.stat().st_mtime < 86400:
                        state["image_at"] = path.stat().st_mtime + 86400
                        self.save(identifier, account, state)
                    elif budget:
                        self.attempted(identifier, account, state, "image")
                        budget -= 1
                        try:
                            await self.covers.download(poster, path)
                            if path.is_file() and time.time() - path.stat().st_mtime < 60:
                                state.update(image=0, image_at=time.time() + 86400)
                                self.save(identifier, account, state)
                        except Exception as error:  # noqa: BLE001 - retain independent image retry
                            logging.getLogger(__name__).warning("Cover recovery deferred (%s)", type(error).__name__)
                if state.get("matched"):
                    break
                if not allowance:
                    break
            if budget <= 0:
                # Unvisited torrents have no attempts/clock consumed, so they
                # run first as soon as earlier jobs enter their backoff.
                break
        identifiers = [str(t.get("hash", "")).lower() for t in torrents if isinstance(t, dict) and self.covers.path(str(t.get("hash", ""))) is not None][:600]
        scopes = [self.scope(a) for a in accounts]
        with self.connect() as db:
            if scopes:
                db.execute("DELETE FROM jobs WHERE scope NOT IN (" + ",".join("?" for _ in scopes) + ")", scopes)
            if identifiers:
                db.execute("DELETE FROM jobs WHERE rowid IN (SELECT rowid FROM jobs WHERE hash NOT IN (" + ",".join("?" for _ in identifiers) + ") ORDER BY updated DESC LIMIT -1 OFFSET 5000)", identifiers)

    def observe(self, identifier, account, state, attributes):
        from mediaseed.covers import poster_url
        from mediaseed.intelligence import metadata

        self.covers.remember(identifier, account, attributes)
        public = metadata(attributes)
        state["has_description"] = state.get("has_description", False) or bool(public.get("description"))
        state["has_media_info"] = state.get("has_media_info", False) or bool(public.get("media_info"))
        poster = poster_url(attributes.get("meta", {}).get("poster")) if isinstance(attributes.get("meta"), dict) else None
        if poster and poster != state.get("poster"):
            state.update(poster=poster, image=0, image_at=0)
        state["complete"] = bool(state["has_description"] and state["has_media_info"] and state.get("poster"))
        state["refresh_at"] = time.time() + 300

    @staticmethod
    def next_cursor(data):
        if not isinstance(data, dict):
            return None
        meta = data.get("meta", {})
        cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
        if not isinstance(cursor, str):
            links = data.get("links", {})
            link = links.get("next") if isinstance(links, dict) else None
            try:
                cursor = parse_qs(urlsplit(link).query).get("cursor", [None])[0] if isinstance(link, str) and len(link) <= 4096 else None
            except ValueError:
                return None
        return cursor if isinstance(cursor, str) and 0 < len(cursor) <= 2048 else None
