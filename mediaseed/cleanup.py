"""Opt-in per-torrent cleanup bound to an import tag or confirmed add identity."""
import asyncio
import base64
import hashlib
import json
import math
import secrets
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from mediaseed.cleanup_upgrade import upgrade_cleanup_hours

MAX_CLEANUP_RULES = 500
OBSERVATION_BATCH = 100  # Stay below MediaVPN's 12 KiB RPC payload bound.


class CleanupPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    mode: Literal["never", "time", "ratio", "both", "either"] = "never"
    hours: float = Field(default=50, ge=1 / 60, le=87600, allow_inf_nan=False)
    ratio: float = Field(default=1, ge=1, le=1000, allow_inf_nan=False)
    delete_files: bool = False


class CleanupUpdate(CleanupPolicy):
    added_on: float = Field(gt=0, allow_inf_nan=False)
    confirm_delete_files: bool = False


def cleanup_identity(rule, row):
    """Imported rules use their tag; edited rules also bind the client add time."""
    tag, added = rule.get("tag"), rule.get("added_on")
    if not tag and not added:
        return False
    if tag and tag not in [x.strip() for x in str(row.get("tags", "")).split(",")]:
        return False
    return added is None or (type(row.get("added_on")) in (int, float) and row["added_on"] == added)


def announce_hosts(tree):
    # Only hostnames leave this parser; announce passkeys remain private.
    values = [tree.get(b"announce")] if isinstance(tree,dict) else []
    tiers = tree.get(b"announce-list",[]) if isinstance(tree,dict) else []
    if isinstance(tiers,list):
        for tier in tiers[:100]:
            if isinstance(tier,list): values.extend(tier[:100])
    hosts = []
    for value in values:
        try:
            address = urlsplit(value.decode("utf8") if isinstance(value,bytes) else str(value))
            host = address.hostname
            if address.scheme in {"http","https","udp"} and host and not address.username and not address.password:
                hosts.append(host.lower())
        except (ValueError,UnicodeError): pass
    return list(dict.fromkeys(hosts))[:100]


def source_hashes(magnet=None, content=None, include_metadata=False, include_trackers=False):
    if magnet:
        topics = parse_qs(urlsplit(magnet).query).get("xt", [])
        hashes = []
        for topic in topics:
            value = topic.rsplit(":", 1)[-1]
            if topic.startswith("urn:btih:"):
                hashes.append(base64.b32decode(value.upper()).hex() if len(value) == 32 else value.lower())
            elif topic.startswith("urn:btmh:1220"):
                hashes.extend([value[4:].lower(), value[4:44].lower()])
        hashes = list(dict.fromkeys(hashes))
        if len(hashes) > 3:
            raise HTTPException(422, "Magnet contains ambiguous info hashes")
        return hashes
    # Hash the original info dictionary bytes, never re-encode them.
    index, count, info_slice, info_value = 0, 0, None, None

    def parse(depth=0):
        nonlocal index, count, info_slice, info_value
        count += 1
        if depth > 32 or count > 100000 or index >= len(content):
            raise ValueError()
        marker = content[index:index + 1]
        if marker == b"i":
            end = content.index(b"e", index + 1)
            value = int(content[index + 1:end]); index = end + 1
            return value
        if marker in (b"d", b"l"):
            index += 1; value = {} if marker == b"d" else []
            while content[index:index + 1] != b"e":
                key = parse(depth + 1)
                if marker == b"l":
                    value.append(key)
                else:
                    if not isinstance(key, bytes) or key in value:
                        raise ValueError()
                    start = index
                    value[key] = parse(depth + 1)
                    if depth == 0 and key == b"info":
                        info_slice, info_value = content[start:index], value[key]
            index += 1
            return value
        colon = content.index(b":", index)
        size = int(content[index:colon]); index = colon + 1
        if size < 0 or size > len(content) - index:
            raise ValueError()
        value = content[index:index + size]; index += size
        return value

    try:
        tree = parse()
        if index != len(content) or not isinstance(info_value, dict):
            raise ValueError()
        if include_metadata:
            size = info_value.get(b"length")
            if b"files" in info_value:
                if not isinstance(info_value[b"files"], list) or any(not isinstance(item, dict) for item in info_value[b"files"]):
                    raise ValueError()
                lengths = [item.get(b"length") for item in info_value[b"files"] if isinstance(item, dict)]
                size = sum(lengths) if lengths and all(type(value) is int and value >= 0 for value in lengths) else None
            if b"file tree" in info_value:
                def tree_size(tree):
                    if not isinstance(tree, dict):
                        raise ValueError()
                    if b"" in tree:
                        length = tree[b""].get(b"length")
                        if type(length) is not int or length < 0:
                            raise ValueError()
                        return length
                    return sum(tree_size(value) for value in tree.values())
                size = tree_size(info_value[b"file tree"])
            if size is not None and (type(size) is not int or not 0 <= size <= 2 ** 63 - 1):
                raise ValueError()
            name = info_value.get(b"name.utf-8", info_value.get(b"name", b""))
            return {"size": size if type(size) is int and size >= 0 else None,
                    "name": name.decode("utf-8", errors="replace")[:300] if isinstance(name, bytes) else None,
                    "hashes": source_hashes(content=content),
                    **({"tracker_hosts": announce_hosts(tree)} if include_trackers else {})}
        if info_value.get(b"meta version") == 2 and b"pieces" not in info_value:
            digest = hashlib.sha256(info_slice).hexdigest()
            return [digest, digest[:40]]
        return [hashlib.sha1(info_slice).hexdigest()]
    except (ValueError, IndexError, TypeError, OverflowError):
        raise HTTPException(422, "Torrent metadata is invalid; automatic cleanup was not enabled") from None


def reached(policy, row):
    try:
        if any(type(row.get(key)) not in (int, float) for key in ("progress", "seeding_time", "ratio")):
            return False
        progress, seconds, ratio = (float(row[key]) for key in ("progress", "seeding_time", "ratio"))
        if "credited_ratio" in row:
            if type(row["credited_ratio"]) not in (int, float):
                return False
            ratio = float(row["credited_ratio"])
        if not all(math.isfinite(x) and x >= 0 for x in (progress, seconds, ratio)) or progress < 1:
            return False
        if row.get("state") not in {"uploading", "stalledUP", "pausedUP", "stoppedUP", "queuedUP", "forcedUP"}:
            return False
        # Do not cut off connected downloaders; missing peer evidence fails closed.
        speed = row.get("upspeed")
        if type(row.get("num_leechs")) is not int or row["num_leechs"] != 0 or type(speed) not in (int, float) or not math.isfinite(speed) or speed != 0:
            return False
        time_met, ratio_met = seconds >= policy["hours"] * 3600, ratio >= policy["ratio"]
        return {"never": False, "time": time_met, "ratio": ratio_met,
                "both": time_met and ratio_met, "either": time_met or ratio_met}[policy["mode"]]
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


class CleanupManager:
    def __init__(self, database, qbit, vpn_status, credit=None):
        self.database, self.qbit, self.vpn_status = database, qbit, vpn_status
        self.credit = credit
        self.lock = asyncio.Lock()
        self.observation_failed = False
        with database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS torrent_cleanup (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            upgrade_cleanup_hours(db)

    def records(self):
        with self.database() as db:
            return [dict(json.loads(row[1]), id=row[0]) for row in db.execute("SELECT id,payload FROM torrent_cleanup")]

    def save(self, record):
        with self.database() as db:
            db.execute("INSERT OR REPLACE INTO torrent_cleanup VALUES (?,?)", (record["id"], json.dumps(record)))

    def require_capacity(self):
        if sum(r["mode"] != "never" for r in self.records()) >= MAX_CLEANUP_RULES:
            raise HTTPException(409, f"Automatic cleanup supports {MAX_CLEANUP_RULES} active imports; cancel an existing rule first")

    def public(self, identifier):
        row = next((r for r in self.records() if identifier.lower() in r["hashes"]), None)
        return {**{key: row[key] for key in ("mode", "hours", "ratio", "delete_files", "status")}, "observationFailed": self.observation_failed} if row else {"mode": "never", "status": "disabled"}

    def forget(self, identifier):
        with self.database() as db:
            for row in self.records():
                if identifier.lower() in row["hashes"]:
                    db.execute("DELETE FROM torrent_cleanup WHERE id=?", (row["id"],))

    async def torrent(self, identifier):
        rows = (await self.qbit("/api/v2/torrents/info", payload={"hashes": identifier})).json()
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or str(rows[0].get("hash", "")).lower() != identifier:
            raise HTTPException(409, "Torrent is unavailable; reload its cleanup settings")
        added = rows[0].get("added_on")
        if type(added) not in (int, float) or not math.isfinite(added) or added <= 0:
            raise HTTPException(409, "Torrent identity could not be confirmed")
        return rows[0]

    async def inspect(self, identifier):
        row = await self.torrent(identifier)
        return {"policy": self.public(identifier), "added_on": row["added_on"]}

    async def update(self, identifier, body):
        if body.mode != "never" and body.delete_files and not body.confirm_delete_files:
            raise HTTPException(422, "Confirm automatic deletion of downloaded files")
        async with self.lock:
            row = await self.torrent(identifier)
            if row["added_on"] != body.added_on:
                raise HTTPException(409, "Torrent changed; reload its cleanup settings")
            if body.mode == "never":
                return await self._cancel(identifier)
            existing = next((r for r in self.records() if identifier in r["hashes"]), None)
            if existing and not cleanup_identity(existing, row):
                raise HTTPException(409, "Cleanup blocked: torrent identity could not be confirmed")
            if not existing:
                self.require_capacity()
            policy = CleanupPolicy(**body.model_dump(include=set(CleanupPolicy.model_fields)))
            record = {**(existing or {}), **policy.model_dump(),
                      "id": existing["id"] if existing else secrets.token_hex(12),
                      "hashes": [identifier], "added_on": body.added_on,
                      "tag": existing.get("tag") if existing else None,
                      "status": "active", "start": False}
            # Saving never starts, stops or removes a torrent. The normal worker
            # enforces the selected rule on a later fresh, identity-bound sample.
            self.save(record)
            return {"saved": True, "policy": self.public(identifier)}

    async def add(self, add_fn, policy, ratio, **args):
        if policy.mode == "never" and not args.get("start"):
            # Paused imports cannot reach a stop limit. The start adapter clears
            # all native limits before activation, including pending magnets.
            return await add_fn(self.qbit, ratio, **args)
        if args.get("start") and not (await self.vpn_status()).get("downloadsEnabled"):
            raise HTTPException(409, "Starting requires a verified VPN; choose add on pause")
        hashes = source_hashes(magnet=args.get("magnet"), content=args.get("content"))
        if not hashes:
            raise HTTPException(422, "Cannot identify this torrent for automatic cleanup")
        async with self.lock:
            if policy.mode != "never":
                self.require_capacity()
            rows = (await self.qbit("/api/v2/torrents/info", payload={"hashes": "|".join(hashes)})).json()
            if policy.mode == "never" and isinstance(rows, list) and rows and all(isinstance(row, dict) and str(row.get("hash", "")).lower() in hashes for row in rows):
                # A duplicate import must not acquire a new rule or identity tag.
                if args.get("start"):
                    await self.qbit("/api/v2/torrents/start", "POST", {"hashes": rows[0]["hash"]})
                return {"added": True, "stopped": not args.get("start", False), "existing": True}
            if not isinstance(rows, list) or rows or any(set(r["hashes"]) & set(hashes) for r in self.records()):
                raise HTTPException(409, "Torrent already exists; its cleanup settings were not changed")
            identifier = secrets.token_hex(12)
            record = {**policy.model_dump(), "id": identifier, "hashes": hashes, "tag": "MediaSeed-Cleanup-" + identifier,
                      "status": "preparing", "start": args.get("start", False), "stop_ratio": ratio}
            self.save(record)
            try:
                # Configure limits while paused. A pending magnet is armed later.
                result = await add_fn(self.qbit, ratio, **{**args, "start": False, "tag": record["tag"]})
            except Exception:
                self.forget(hashes[0]); raise
            record["status"] = "waiting"; self.save(record)
            try:
                await self._tick(identifier)
            except Exception:
                pass  # Durable waiting state; no unconfigured automatic start.
            current = self.public(hashes[0])
            configured = current.get("status") == "active" or (policy.mode == "never" and current.get("status") == "disabled")
            return {**result, "cleanup": policy.model_dump(), "startRequested": record["start"],
                    "stopped": not (configured and record["start"]), "cleanupPending": not configured}

    async def tick(self):
        async with self.lock:
            try:
                await self._tick()
                self.observation_failed = False
            except Exception:
                self.observation_failed = True
                raise

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                pass  # Never delete without a successful, current observation.
            await asyncio.sleep(15)

    async def _tick(self, identifier=None):
        rules = [r for r in self.records() if r["status"] in {"waiting", "active"} and (identifier is None or r["id"] == identifier)]
        if not rules:
            return
        hashes = list(dict.fromkeys(h for r in rules for h in r["hashes"]))
        rows = []
        for offset in range(0, len(hashes), OBSERVATION_BATCH):
            batch = (await self.qbit("/api/v2/torrents/info", payload={"hashes": "|".join(hashes[offset:offset + OBSERVATION_BATCH])})).json()
            if not isinstance(batch, list):
                return
            rows.extend(batch)
        for rule in rules:
            row = next((r for r in rows if isinstance(r, dict) and r.get("hash", "").lower() in rule["hashes"]), None)
            if row is None:
                continue
            identifier = row["hash"].lower()
            if not cleanup_identity(rule, row):
                rule["status"] = "identity_mismatch"; self.save(rule); continue
            if rule["status"] == "active" and any(key in row and row[key] != -1 for key in ("ratio_limit", "seeding_time_limit", "inactive_seeding_time_limit")):
                await self.qbit("/api/v2/torrents/setShareLimits", "POST", {"hashes": identifier, "ratioLimit": "-1", "seedingTimeLimit": "-1", "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Stop"})
            if rule["status"] == "waiting":
                await self.qbit("/api/v2/torrents/setShareLimits", "POST", {"hashes": identifier, "ratioLimit": "-1", "seedingTimeLimit": "-1", "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Stop"})
                if rule["start"]:
                    if not (await self.vpn_status()).get("downloadsEnabled"):
                        continue
                    await self.qbit("/api/v2/torrents/start", "POST", {"hashes": identifier})
                rule["status"] = "active"; self.save(rule)
            if rule["mode"] == "never":
                # Retention needs durable preparation, not a permanent cleanup
                # record. The seeding worker repairs any later native drift.
                self.forget(identifier)
                continue
            observed = {**row, **self.credit.observe(row)} if self.credit else row
            if reached(rule, observed):
                await self.qbit("/api/v2/torrents/delete", "POST", {"hashes": identifier, "deleteFiles": "true" if rule["delete_files"] else "false"})
                # Deletion is accepted asynchronously by qBittorrent. Drop the
                # policy now so a later re-import never inherits this action.
                self.forget(identifier)

    async def cancel(self, identifier):
        async with self.lock:
            return await self._cancel(identifier)

    async def _cancel(self, identifier):
        rule = next((r for r in self.records() if identifier.lower() in r["hashes"]), None)
        if rule:
            # Disable destruction durably before attempting a remote write.
            self.forget(identifier)
            try:
                rows = (await self.qbit("/api/v2/torrents/info", payload={"hashes": identifier})).json()
                if isinstance(rows, list) and any(isinstance(r, dict) and str(r.get("hash", "")).lower() == identifier and cleanup_identity(rule, r) for r in rows):
                    await self.qbit("/api/v2/torrents/setShareLimits", "POST", {"hashes": identifier, "ratioLimit": "-1", "seedingTimeLimit": "-1", "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Stop"})
            except (HTTPException, ValueError):
                return {"saved": True, "mode": "never", "stopLimitsRestored": False}
        return {"saved": True, "mode": "never"}
