"""Persistent, conservative NordicBytes Green upload accounting.

Only exact info-hash evidence supplies a publication date. Unknown history is
discounted until classified; expiry never retroactively credits Green upload.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time

from fastapi import HTTPException
from mediaseed.cleanup import source_hashes
from mediaseed import feed_uploads


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else 0


def publication(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.timestamp() if date.tzinfo and date.timestamp() > 0 else None
    except (TypeError, AttributeError, ValueError, OverflowError, OSError):
        return None


class TrackerCredit:
    def __init__(self, database, root, raw_qbit):
        self.database, self.root, self.raw_qbit = database, Path(root), raw_qbit
        self.lock = asyncio.Lock()
        self._connection = None
        self.cleanup = lambda identifier: {"mode": "never"}
        with database() as db:
            feed_uploads.initialize(db)
            db.execute("CREATE TABLE IF NOT EXISTS torrent_credit (hash TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        if self._connection is not None:
            yield self._connection
        else:
            with self.database() as db:
                yield db

    def observe_many(self, rows):
        # A dashboard snapshot commits once, not once per torrent. This block
        # has no await, so other asyncio tasks cannot reuse the transaction.
        with self.database() as db:
            previous, self._connection = self._connection, db
            try:
                return [self.observe(row) if isinstance(row, dict) else {} for row in rows]
            finally:
                self._connection = previous

    def enabled(self):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM settings WHERE kind='trackers' LIMIT 1").fetchone())

    def load(self, identifier):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM torrent_credit WHERE hash=?", (identifier.lower(),)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, identifier, record):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO torrent_credit VALUES (?,?)", (identifier.lower(), json.dumps(record)))

    def forget(self, identifier):
        with self.connection() as db:
            db.execute("DELETE FROM torrent_credit WHERE hash=?", (identifier.lower(),))

    def evidence(self, identifier):
        path = self.root / "cover-cache" / (hashlib.sha256(identifier.lower().encode()).hexdigest() + ".json")
        try:
            data = json.loads(path.read_text())
            return data if data.get("infoHash") == identifier.lower() and data.get("status") == "matched" else {}
        except (OSError, ValueError, TypeError):
            return {}

    def register(self, identifier, target):
        record = self.load(identifier) or {"uploaded": 0, "discounted": 0, "unclassified": 0, "at": time.time()}
        record["target"] = target
        self.save(identifier, record)

    def observe(self, row, now=None):
        identifier = str(row.get("hash", "")).lower()
        if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", identifier):
            return {}
        record = self.load(identifier)
        if record is None and not self.enabled():
            return {}
        now = time.time() if now is None else now
        added = number(row.get("added_on"))
        if record is None or (added and record.get("added_on") and added != record["added_on"]):
            record = {"target": max(1, number(row.get("ratio_limit"))), "uploaded": 0,
                      "discounted": 0, "unclassified": 0, "at": added or now}
        evidence = self.evidence(identifier) or record.get("policy", {})
        if evidence:
            record["policy"] = evidence
        tracker_name = evidence.get("tracker") if isinstance(evidence.get("tracker"),str) and evidence.get("infoHash")==identifier else None
        created = publication(evidence.get("createdAt"))
        # Like FjordSeed, allow 30 minutes for announce/clock skew. The API
        # publication time, never the local add time, starts the 24-hour rule.
        until = created + 24 * 3600 + 1800 if created else None
        exempt = evidence.get("ownUpload") is True
        pending = not exempt and until is None
        if until and until > now + 25 * 3600:
            pending, until, created = True, None, None
        uploaded = number(row.get("uploaded"))
        if type(row.get("uploaded")) not in (int, float) or not math.isfinite(row["uploaded"]) or row["uploaded"] < record["uploaded"]:
            # Overlapping RPC reads can return out of order. Neither a stale
            # snapshot nor an unconfirmed counter reset may erase lost credit.
            # A different added_on identity is reset separately above.
            return {"credited_ratio": 0, "credited_uploaded": None, "green_uploaded": None,
                    "green_pending": True, "green_active": False, "green_until": until,
                    "credit_observation_stale": True, "tracker_name": tracker_name, "account_credited_uploaded": record.get("account_credited_uploaded"), "account_credit_estimated":True, "ratio_limit": record["target"], "native_ratio_limit": record["target"] * 2}
        delta = max(0, uploaded - record["uploaded"])
        if not exempt and until and (record["at"] < until or not record.get("classified")) and (not added or added < until):
            record["discounted"] += delta
            if now >= until and not record.get("classified") and delta:
                record["estimated"] = True
        elif pending:
            record["unclassified"] += delta
        if not pending:
            if not exempt and until and (not added or added < until):
                record["discounted"] += record["unclassified"]
                if record["unclassified"] and now >= until:
                    record["estimated"] = True
            record["unclassified"] = 0
            if exempt:
                record["discounted"] = 0
        discounted = min(uploaded, record["discounted"] + (record["unclassified"] if pending else 0))
        credited = uploaded - discounted / 2
        denominator = number(row.get("downloaded"))
        if not denominator and number(row.get("ratio")):
            denominator = uploaded / row["ratio"]
        active = bool(not exempt and until and until > now)
        native = record["target"] * 2 if active or pending else record["target"] + (discounted / 2 / denominator if denominator else 0)
        # Bonus credit is accumulated only across two fresh matching observations.
        # Older unobserved bonus periods cannot be reconstructed from today's badge.
        fresh_double = evidence.get("doubleUpload") is True and 0 <= now-number(evidence.get("checkedAt")) < 300
        bonus = number(record.get("account_bonus"))
        base_delta = min(delta,max(0,credited-number(record.get("account_base",credited))))
        if fresh_double and record.get("account_double") and record.get("at",0) >= number(evidence.get("checkedAt")):
            bonus += base_delta
        record.update(account_bonus=bonus,account_double=fresh_double,account_base=credited,account_credited_uploaded=credited+bonus)
        with self.connection() as db:
            feed_uploads.observe(db,row,credited+bonus)
        record.update(uploaded=uploaded, at=now, classified=not pending)
        raw_seconds = row.get("seeding_time")
        if type(raw_seconds) in (int, float) and math.isfinite(raw_seconds) and raw_seconds >= 0:
            moving = row.get("state") == "moving"
            clock = record.get("seed_clock")
            seconds = clock["seconds"] if clock else raw_seconds
            if clock and not moving and not clock["moving"]:
                seconds += max(0, raw_seconds - clock["raw"])
            record["seed_clock"] = {"raw": raw_seconds, "seconds": seconds, "moving": moving}
        if added:
            record["added_on"] = added
        self.save(identifier, record)
        return {"credited_ratio": credited / denominator if denominator else number(row.get("ratio")) / (2 if active or pending else 1),
                "credited_uploaded": credited, "account_credited_uploaded": credited+bonus, "account_credit_estimated": True, "green_uploaded": discounted, "green_until": until,
                "green_active": active, "green_pending": pending, "green_history_estimated": bool(record.get("estimated")),
                "ratio_limit": record["target"], "native_ratio_limit": native,
                "green_rule_until": created + 24 * 3600 if created and not exempt else None,
                **({"seeding_time": record["seed_clock"]["seconds"]} if "seed_clock" in record else {}),
                "tracker_benefits": evidence if 0 <= now - number(evidence.get("checkedAt")) < 300 else None,
                "tracker_name": tracker_name,
                "tracker_rule": "NordicBytes Green: 0,5× upload i 24 timer + 30 minutters sikkerhedsmargin"}

    async def request(self, path, method="GET", payload=None, files=None):
        if method == "POST" and path.rsplit("/", 1)[-1] in {"add", "delete", "setShareLimits", "start"}:
            async with self.lock:
                return await self._request(path, method, payload, files)
        return await self.raw_qbit(path, method, payload, files=files)

    async def _request(self, path, method="GET", payload=None, files=None):
        payload = dict(payload or {})
        if method == "POST" and path.endswith("/torrents/add") and self.enabled():
            target = float(payload.get("ratioLimit", 1))
            hashes = source_hashes(content=files["torrents"][1] if files else None, magnet=payload.get("urls"))
            if any(self.load(identifier) for identifier in hashes):
                existing = (await self.raw_qbit("/api/v2/torrents/info", payload={"hashes": "|".join(hashes)})).json()
                if not isinstance(existing, list):
                    raise HTTPException(503, "Cannot verify existing torrent identity")
                present = {str(r.get("hash", "")).lower() for r in existing if isinstance(r, dict)}
                for identifier in hashes:
                    if identifier not in present:
                        self.forget(identifier)
            for identifier in hashes:
                if self.load(identifier) is None:
                    self.register(identifier, target)
            # New imports begin with a conservative native goal, including RSS
            # and extension uploads. Persist the selected goal before the write.
            payload["ratioLimit"] = str(min(1000, target * 2))
            if target > 500 and payload.get("stopped") == "false":
                # Configure the durable worker-owned goal while paused first.
                payload.update(stopped="true", paused="true")
                result = await self.raw_qbit(path, method, payload, files=files)
                accepted = result.status_code == 204 or result.text.strip() == "Ok."
                try:
                    outcome = result.json()
                    accepted = accepted or (isinstance(outcome, dict) and outcome.get("failure_count") == 0 and (outcome.get("success_count") == 1 or outcome.get("pending_count") == 1))
                except ValueError:
                    pass
                if not accepted:
                    return result
                for identifier in hashes[:1]:
                    await self.raw_qbit("/api/v2/torrents/setShareLimits", "POST", self.unlimited(identifier))
                    await self.raw_qbit("/api/v2/torrents/start", "POST", {"hashes": identifier})
                return result
        elif method == "POST" and path.endswith("/torrents/start"):
            identifier = str(payload.get("hashes", "")).lower()
            record = self.load(identifier)
            if (record or self.enabled()) and self.cleanup(identifier).get("mode") == "never":
                current = (await self.raw_qbit("/api/v2/torrents/info", payload={"hashes": identifier})).json()
                if not isinstance(current, list):
                    raise HTTPException(503, "Cannot verify tracker credit before start")
                credit = next((self.observe(r) for r in current if isinstance(r, dict) and str(r.get("hash", "")).lower() == identifier), {})
                native = credit.get("native_ratio_limit", record["target"] * 2 if record else 2)
                limits = self.unlimited(identifier) if native > 1000 else {"hashes": identifier, "ratioLimit": str(native), "seedingTimeLimit": "2940", "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Stop"}
                await self.raw_qbit("/api/v2/torrents/setShareLimits", "POST", limits)
        elif method == "POST" and path.endswith("/torrents/setShareLimits") and float(payload.get("ratioLimit", -1)) >= 1:
            identifier = str(payload.get("hashes", "")).lower()
            if self.enabled() or self.load(identifier):
                target = float(payload["ratioLimit"])
                self.register(identifier, target)
                payload["ratioLimit"] = str(target * 2)
                if target * 2 > 1000:
                    payload = self.unlimited(identifier)
        result = await self.raw_qbit(path, method, payload, files=files)
        if method == "POST" and path.endswith("/torrents/delete"):
            self.forget(str(payload.get("hashes", "")))
        return result

    @staticmethod
    def unlimited(identifier):
        return {"hashes": identifier, "ratioLimit": "-1", "seedingTimeLimit": "-1", "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Stop"}

    async def tick(self):
        if not self.enabled():
            with self.database() as db:
                if not db.execute("SELECT 1 FROM torrent_credit LIMIT 1").fetchone():
                    return
        async with self.lock:
            rows = (await self.raw_qbit("/api/v2/torrents/info", payload={"limit": 500})).json()
            if not isinstance(rows, list):
                return
            writes = 0
            credits = self.observe_many(rows[:500])
            for row, credit in zip(rows[:500], credits):
                if not isinstance(row, dict):
                    continue
                identifier = row.get("hash", "")
                if not credit or self.cleanup(identifier).get("mode") != "never":
                    continue
                if credit.get("credit_observation_stale"):
                    continue
                if writes >= 8:
                    continue
                native = credit["native_ratio_limit"]
                limits = self.unlimited(identifier) if native > 1000 else {"hashes": identifier, "ratioLimit": str(native), "seedingTimeLimit": "2940", "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Stop"}
                desired = -1 if native > 1000 else native
                if type(row.get("ratio_limit")) in (int, float) and abs(row["ratio_limit"] - desired) > 0.000001:
                    await self.raw_qbit("/api/v2/torrents/setShareLimits", "POST", limits)
                    writes += 1
                if native > 1000 and number(row.get("progress")) >= 1 and row.get("state") in {"uploading", "stalledUP", "forcedUP"} and (credit["credited_ratio"] >= credit["ratio_limit"] or number(credit.get("seeding_time", row.get("seeding_time"))) >= 49 * 3600):
                    await self.raw_qbit("/api/v2/torrents/stop", "POST", {"hashes": identifier})
                    writes += 1

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                pass  # Native conservative stop limits survive API outages.
            await asyncio.sleep(15)
