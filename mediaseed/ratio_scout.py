"""Bounded API discovery; fresh evidence and durable intent precede every import."""

import asyncio
import hashlib
import json
import math
import re
import time
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mediaseed.cleanup import source_hashes
from mediaseed.activity import Activity


class RatioSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    enabled: bool = Field(default=False, strict=True)
    automatic: bool = Field(default=False, strict=True)
    tracker: str = Field(default="", pattern=r"^(?:[a-f0-9]{24})?$")
    query: str = Field(default="", max_length=120, pattern=r"^[^\x00-\x1f]*$")
    location: str = Field(default="local", pattern=r"^[A-Za-z0-9-]{1,80}$")
    folder: str = Field(default="", max_length=1000)
    minSeeders: int = Field(default=1, ge=0, le=1000000, strict=True)
    maxSeeders: int = Field(default=2, ge=0, le=1000000, strict=True)
    minLeechers: int = Field(default=20, ge=1, le=1000000, strict=True)
    requireDouble: bool = Field(default=False, strict=True)
    maxGiB: int = Field(default=20, ge=1, le=10000, strict=True)
    reserveGiB: int = Field(default=20, ge=1, le=10000, strict=True)
    dailyLimit: int = Field(default=3, ge=1, le=100, strict=True)
    scanMinutes: int = Field(default=10, ge=5, le=60, strict=True)

    @model_validator(mode="after")
    def validate_settings(self):
        if self.maxSeeders < self.minSeeders:
            raise ValueError("Maximum seeders must be at least the minimum")
        if self.enabled and not self.tracker:
            raise ValueError("Choose a tracker before enabling discovery")
        if self.folder and (
            self.folder.startswith(("/", "\\"))
            or "\\" in self.folder
            or any(p in {"", ".", ".."} for p in self.folder.split("/"))
            or any(ord(c) < 32 for c in self.folder)
        ):
            raise ValueError("Choose a relative download folder")
        return self


def integer(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def candidate(row, settings, now):
    """Only API data is accepted; an unknown benefit never becomes freeleech."""
    if not isinstance(row, dict) or not re.fullmatch(
        r"[1-9][0-9]{0,17}", str(row.get("id", ""))
    ):
        return None
    a = row.get("attributes")
    if not isinstance(a, dict) or not isinstance(a.get("name"), str):
        return None
    identifier = str(a.get("info_hash", "")).lower()
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", identifier):
        return None
    seeds, leeches, size = (integer(a.get(k)) for k in ("seeders", "leechers", "size"))
    try:
        free = float(str(a.get("freeleech")).rstrip("%"))
        created = datetime.fromisoformat(str(a.get("created_at", "")))
        age = now - created.timestamp() if created.tzinfo else -1
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    featured = a.get("featured") is True
    double = featured or a.get("double_upload") is True
    # Exclude Green and uncertain dates. A 30-minute margin avoids clock skew.
    if age < 24 * 3600 + 1800 or (not featured and free != 100):
        return None
    if (
        seeds is None
        or leeches is None
        or size is None
        or not 0 < size <= settings.maxGiB * 1024**3
    ):
        return None
    if (
        not settings.minSeeders <= seeds <= settings.maxSeeders
        or leeches < settings.minLeechers
        or (settings.requireDouble and not double)
    ):
        return None
    if (
        settings.query.strip()
        and settings.query.strip().casefold() not in a["name"].casefold()
    ):
        return None
    return {
        "id": str(row["id"]),
        "hash": identifier,
        "name": a["name"][:300],
        "seeders": seeds,
        "leechers": leeches,
        "size": size,
        "freeleech": 100,
        "doubleUpload": double,
        "featured": featured,
        "checkedAt": now,
    }


class RatioScout:
    def __init__(
        self,
        database,
        accounts,
        tracker_read,
        qbit,
        vpn_status,
        storage,
        add_stopped,
        covers=None,
        credit=None,
    ):
        self.database, self.accounts, self.tracker_read = (
            database,
            accounts,
            tracker_read,
        )
        self.qbit, self.vpn_status, self.storage, self.add_stopped, self.covers = (
            qbit,
            vpn_status,
            storage,
            add_stopped,
            covers,
        )
        self.lock = asyncio.Lock()
        self.activity = Activity(database)
        self.action_lock = credit.lock if credit else asyncio.Lock()
        self.write_qbit = credit._request if credit else qbit
        self.next_scan = 0
        self.last_scan = 0
        self.next_request = 0
        self.status = "disabled"
        with database() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS ratio_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS ratio_candidates (id TEXT PRIMARY KEY, tracker TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS ratio_imports (hash TEXT PRIMARY KEY, created REAL NOT NULL, size INTEGER NOT NULL, location TEXT NOT NULL, status TEXT NOT NULL)"
            )
        with database() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS ratio_scan_state (tracker TEXT PRIMARY KEY, page_size INTEGER NOT NULL, not_before REAL NOT NULL)"
            )
        self.status = "waiting" if self.settings().enabled else "disabled"

    def settings(self):
        with self.database() as c:
            row = c.execute("SELECT payload FROM ratio_settings WHERE id=1").fetchone()
        return RatioSettings.model_validate_json(row[0]) if row else RatioSettings()

    async def save(self, settings):
        async with self.lock:
            previous = self.settings()
            if settings.enabled:
                self.account(settings)
                self.storage.destination(settings.location, settings.folder)
            with self.database() as c:
                c.execute(
                    "INSERT INTO ratio_settings VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                    (settings.model_dump_json(),),
                )
                c.execute("DELETE FROM ratio_candidates")
            _, deadline = self.scan_state(settings.tracker)
            if (
                deadline
                and settings.tracker == previous.tracker
                and settings.scanMinutes > previous.scanMinutes
            ):
                deadline += (settings.scanMinutes - previous.scanMinutes) * 60
                with self.database() as c:
                    c.execute(
                        "UPDATE ratio_scan_state SET not_before=? WHERE tracker=?",
                        (deadline, settings.tracker),
                    )
            if deadline:
                self.next_scan = max(
                    self.next_scan, time.monotonic() + max(0, deadline - time.time())
                )
            if self.last_scan:
                self.next_scan = max(
                    self.next_scan, self.last_scan + settings.scanMinutes * 60
                )
            self.status = "waiting" if settings.enabled else "disabled"
        return settings.model_dump()

    def scan_state(self, tracker):
        with self.database() as c:
            row = c.execute(
                "SELECT page_size,not_before FROM ratio_scan_state WHERE tracker=?",
                (tracker,),
            ).fetchone()
        return (max(1, min(10, row[0])), row[1]) if row else (10, 0)

    def plan_scan(self, tracker, page_size, delay):
        deadline = time.time() + delay
        with self.database() as c:
            c.execute(
                "INSERT INTO ratio_scan_state VALUES (?,?,?) ON CONFLICT(tracker) DO UPDATE SET page_size=excluded.page_size,not_before=excluded.not_before",
                (tracker, page_size, deadline),
            )
        self.next_scan = time.monotonic() + delay

    def account(self, settings):
        account = next(
            (a for a in self.accounts("trackers") if a["id"] == settings.tracker), None
        )
        if account is None:
            raise HTTPException(409, "Choose an available tracker for ratio discovery")
        return account

    async def read(self, account, path, params=None):
        if self.next_request - time.monotonic() > 1.1:
            raise HTTPException(
                503, "Tracker is cooling down; discovery will retry later"
            )
        if time.monotonic() < self.next_request:
            await asyncio.sleep(self.next_request - time.monotonic())
        self.next_request = time.monotonic() + 1
        try:
            return await self.tracker_read(account, path, params)
        except HTTPException as error:
            if error.status_code == 503:
                page_size, _ = self.scan_state(account["id"])
                self.plan_scan(account["id"], page_size, 900)
                self.next_request = time.monotonic() + 900
            raise

    def public(self):
        settings = self.settings()
        with self.database() as c:
            rows = c.execute(
                "SELECT payload FROM ratio_candidates WHERE tracker=?",
                (settings.tracker,),
            ).fetchall()
            candidates = [json.loads(r[0]) for r in rows]
            hashes = [r["hash"] for r in candidates]
            placeholders = ",".join("?" for _ in hashes)
            imports = (
                {
                    r[0]: r[1]
                    for r in c.execute(
                        "SELECT hash,status FROM ratio_imports WHERE hash IN ("
                        + placeholders
                        + ")",
                        hashes,
                    )
                }
                if hashes
                else {}
            )
            count = c.execute(
                "SELECT COUNT(*) FROM ratio_imports WHERE created>? AND status!='existing'",
                (time.time() - 86400,),
            ).fetchone()[0]
        page_size, deadline = self.scan_state(settings.tracker)
        for row in candidates:
            row["importStatus"] = imports.get(row["hash"])
            row["stale"] = (
                not 0 <= time.time() - row["checkedAt"] < settings.scanMinutes * 120
            )
        return {
            "status": self.status,
            "settings": settings.model_dump(),
            "candidates": sorted(
                candidates, key=lambda r: (-r["leechers"] / max(1, r["seeders"]), r["size"])
            )[:50],
            "dailyUsed": count,
            "scanMinutes": settings.scanMinutes,
            "resultLimit": page_size,
            "nextScanAt": deadline,
            "bounded": True,
        }

    async def tick(self):
        if self.lock.locked() or time.monotonic() < self.next_scan:
            return
        async with self.lock:
            settings = self.settings()
            if not settings.enabled:
                self.status = "disabled"
                return
            page_size, not_before = self.scan_state(settings.tracker)
            if time.time() < not_before:
                self.next_scan = time.monotonic() + not_before - time.time()
                return
            self.last_scan = time.monotonic()
            self.plan_scan(settings.tracker, page_size, settings.scanMinutes * 60)
            try:
                account = self.account(settings)
                result = await self.read(
                    account,
                    "/api/torrents/filter",
                    {
                        "name": settings.query.strip(),
                        "perPage": page_size,
                        "free[]": 100,
                        "sortField": "leechers",
                        "sortDirection": "desc",
                    },
                )
                rows = result.get("data") if isinstance(result, dict) else None
                if not isinstance(rows, list):
                    raise HTTPException(503, "Invalid tracker discovery response")
                matches = [
                    match
                    for row in rows[:page_size]
                    if (match := candidate(row, settings, time.time()))
                ]
                with self.database() as c:
                    c.execute("DELETE FROM ratio_candidates")
                    for match in matches:
                        key = hashlib.sha256(
                            (settings.tracker + ":" + match["hash"]).encode()
                        ).hexdigest()[:32]
                        match["candidateId"] = key
                        c.execute(
                            "INSERT INTO ratio_candidates VALUES (?,?,?)",
                            (key, settings.tracker, json.dumps(match)),
                        )
                self.status = "ready"
                if settings.automatic:
                    with self.database() as c:
                        imported = {
                            m["hash"]
                            for m in matches
                            if c.execute(
                                "SELECT 1 FROM ratio_imports WHERE hash=?", (m["hash"],)
                            ).fetchone()
                        }
                    choices = [
                        m
                        for m in sorted(
                            matches,
                            key=lambda r: (-r["leechers"] / max(1, r["seeders"]), r["size"]),
                        )
                        if m["hash"] not in imported
                    ]
                    for match in choices[:3]:
                        try:
                            await self._add(match["candidateId"], automatic=True)
                            break  # At most one import per five-minute scan.
                        except HTTPException as error:
                            self.status = str(error.detail)
                            match["blockedReason"] = self.status
                            self.activity.record(
                                "ratio",
                                match["hash"],
                                match["name"],
                                self.status,
                                torrent_hash=match["hash"],
                            )
                            with self.database() as c:
                                c.execute(
                                    "UPDATE ratio_candidates SET payload=? WHERE id=?",
                                    (json.dumps(match), match["candidateId"]),
                                )
                                handled = c.execute(
                                    "SELECT 1 FROM ratio_imports WHERE hash=?",
                                    (match["hash"],),
                                ).fetchone()
                            if handled or error.status_code >= 500:
                                break
            except HTTPException as error:
                self.status = str(error.detail)
                if error.detail == "Tracker response is too large":
                    smaller = 5 if page_size > 5 else 1
                    self.plan_scan(settings.tracker, smaller, settings.scanMinutes * 60)
                    self.status = (
                        "Tracker response too large; next scan requests fewer results"
                        if page_size > 1
                        else "Tracker response too large for a single result"
                    )
            except (ValueError, TypeError, KeyError, OSError):
                self.status = "Ratio discovery is temporarily unavailable"

    async def add(self, identifier):
        async with self.lock:
            return await self._add(identifier)

    async def _add(self, identifier, automatic=False):
        async with self.action_lock:
            try:
                return await self._add_locked(identifier, automatic)
            except HTTPException as error:
                with self.database() as db:
                    stored = db.execute(
                        "SELECT payload FROM ratio_candidates WHERE id=?", (identifier,)
                    ).fetchone()
                if stored:
                    row = json.loads(stored[0])
                    self.activity.record(
                        "ratio",
                        row["hash"],
                        row["name"],
                        str(error.detail),
                        torrent_hash=row["hash"],
                    )
                raise

    async def _add_locked(self, identifier, automatic=False):
        settings = self.settings()
        if not settings.enabled or (automatic and not settings.automatic):
            raise HTTPException(409, "Ratio discovery is disabled")
        with self.database() as c:
            row = c.execute(
                "SELECT tracker,payload FROM ratio_candidates WHERE id=?", (identifier,)
            ).fetchone()
        if row is None or row[0] != settings.tracker:
            raise HTTPException(404, "Candidate not found; wait for the next scan")
        previous = json.loads(row[1])
        with self.database() as c:
            if c.execute(
                "SELECT 1 FROM ratio_imports WHERE hash=?", (previous["hash"],)
            ).fetchone():
                raise HTTPException(
                    409, "Candidate already handled; automatic retry is blocked"
                )
        account = self.account(settings)
        data = await self.read(account, "/api/torrents/" + previous["id"])
        source = data.get("data") if isinstance(data, dict) else None
        fresh = candidate(source, settings, time.time())
        if (
            fresh is None
            or fresh["hash"] != previous["hash"]
            or fresh["id"] != previous["id"]
        ):
            raise HTTPException(409, "Candidate no longer matches; no download started")
        attributes = source["attributes"]
        address = urlsplit(str(attributes.get("download_link", "")))
        if (
            address.scheme != "https"
            or address.netloc != "nordicbytes.org"
            or address.fragment
            or not re.fullmatch(r"/torrents?/download/[^\s]+", address.path)
        ):
            raise HTTPException(502, "Tracker did not provide a safe torrent download")
        if not (await self.vpn_status()).get("downloadsEnabled"):
            raise HTTPException(409, "A verified VPN is required for ratio downloads")
        content = (
            await self.qbit("/api/v2/rss/torrent", payload={"url": address.geturl()})
        ).content
        metadata = source_hashes(content=content, include_metadata=True)
        if fresh["hash"] not in metadata["hashes"] or metadata["size"] != fresh["size"]:
            raise HTTPException(
                409, "Torrent metadata does not match the verified candidate"
            )
        existing = (
            await self.qbit("/api/v2/torrents/info", payload={"limit": 500})
        ).json()
        if (
            not isinstance(existing, list)
            or len(existing) >= 500
            or any(not isinstance(r, dict) for r in existing)
        ):
            raise HTTPException(409, "Cannot safely inspect the current torrent list")
        if any(str(r.get("hash", "")).lower() in metadata["hashes"] for r in existing):
            self.record(fresh, settings, "existing")
            raise HTTPException(
                409, "Torrent is already in the client; it was left unchanged"
            )
        destination = self.storage.destination(settings.location, settings.folder)
        capacity = next(
            (
                r
                for r in self.storage.snapshot()["locations"]
                if r["id"] == settings.location
            ),
            {},
        )
        left = 0
        for torrent in existing:
            if torrent.get("progress") != 1:
                remaining = integer(torrent.get("amount_left"))
                if remaining is None or remaining == 0:
                    raise HTTPException(409, "Remaining download sizes are unknown")
                left += remaining
        with self.database() as c:
            uncertain = c.execute(
                "SELECT COALESCE(SUM(size),0) FROM ratio_imports WHERE status='intent'"
            ).fetchone()[0]
            recent = c.execute(
                "SELECT COALESCE(SUM(size),0) FROM ratio_imports WHERE created>? AND status!='existing'",
                (time.time() - 86400,),
            ).fetchone()[0]
        free = capacity.get("freeBytes")
        if (
            type(free) is not int
            or not capacity.get("writable")
            or free - left - uncertain - fresh["size"] < settings.reserveGiB * 1024**3
        ):
            raise HTTPException(409, "Not enough unreserved storage for this candidate")
        account_data = await self.read(account, "/api/user")
        stats = account_data.get("stats", {}) if isinstance(account_data, dict) else {}
        if not isinstance(stats, dict):
            stats = {}
        uploaded, downloaded = stats.get("uploaded"), stats.get("downloaded")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0
            for v in (uploaded, downloaded)
        ):
            raise HTTPException(409, "Current account totals are unavailable")
        # Assume the full size costs download, even if a temporary discount expires.
        if (
            uploaded
            / max(1, downloaded + left + max(uncertain, recent) + fresh["size"])
            < 0.5
        ):
            raise HTTPException(409, "Ratio buffer is too small if freeleech expires")
        if (
            time.time() - fresh["checkedAt"] >= 30
            or self.settings() != settings
            or self.account(settings) != account
            or not (await self.vpn_status()).get("downloadsEnabled")
        ):
            raise HTTPException(409, "Settings or VPN changed; no download started")
        with self.database() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute(
                "SELECT 1 FROM ratio_imports WHERE hash=?", (fresh["hash"],)
            ).fetchone():
                raise HTTPException(
                    409, "Candidate already handled; automatic retry is blocked"
                )
            used = c.execute(
                "SELECT COUNT(*) FROM ratio_imports WHERE created>? AND status!='existing'",
                (time.time() - 86400,),
            ).fetchone()[0]
            if used >= settings.dailyLimit:
                raise HTTPException(409, "Rolling 24-hour download limit reached")
            c.execute(
                "INSERT INTO ratio_imports VALUES (?,?,?,?,?)",
                (
                    fresh["hash"],
                    time.time(),
                    fresh["size"],
                    settings.location,
                    "intent",
                ),
            )
        if self.covers:
            self.covers.remember(fresh["hash"], account, attributes)
        result = await self.add_stopped(
            self.write_qbit,
            1,
            content=content,
            start=False,
            savepath=destination,
            category="MediaSeed-Ratio",
        )
        if not result.get("added"):
            raise HTTPException(503, "Import not confirmed; automatic retry is blocked")
        observed = (
            await self.qbit(
                "/api/v2/torrents/info",
                payload={"hashes": "|".join(metadata["hashes"])},
            )
        ).json()
        if (
            not isinstance(observed, list)
            or len(observed) != 1
            or not isinstance(observed[0], dict)
            or str(observed[0].get("hash", "")).lower() not in metadata["hashes"]
            or str(observed[0].get("save_path", "")).rstrip("/")
            != destination.rstrip("/")
            or observed[0].get("state")
            not in {"stoppedDL", "stoppedUP", "pausedDL", "pausedUP"}
        ):
            raise HTTPException(
                503, "Paused import not verified; automatic retry is blocked"
            )
        actual = str(observed[0]["hash"]).lower()
        if (
            time.time() - fresh["checkedAt"] >= 30
            or self.settings() != settings
            or self.account(settings) != account
            or not (await self.vpn_status()).get("downloadsEnabled")
        ):
            self.record(fresh, settings, "paused")
            raise HTTPException(
                409, "Torrent was added paused because VPN or settings changed"
            )
        response = await self.write_qbit(
            "/api/v2/torrents/start", "POST", {"hashes": actual}
        )
        if not 200 <= response.status_code < 300:
            raise HTTPException(503, "Import not confirmed; automatic retry is blocked")
        self.record(fresh, settings, "started")
        return {"added": True, "startRequested": True, "hash": actual}

    def record(self, row, settings, status):
        self.activity.record(
            "ratio", row["hash"], row["name"], status, torrent_hash=row["hash"]
        )
        with self.database() as c:
            c.execute(
                "INSERT INTO ratio_imports VALUES (?,?,?,?,?) ON CONFLICT(hash) DO UPDATE SET status=excluded.status",
                (row["hash"], time.time(), row["size"], settings.location, status),
            )
