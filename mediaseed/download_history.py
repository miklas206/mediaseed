"""Download-time observations, retained independently of current tracker events."""
import json
import math
import re
import time
from urllib.parse import urlsplit


class DownloadHistory:
    def __init__(self, database):
        self.database = database
        self.accounts = lambda: []
        with database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS download_history (hash TEXT, added REAL, payload TEXT NOT NULL, PRIMARY KEY(hash,added))")

    @staticmethod
    def history_unknown(history, own_upload=False):
        if own_upload is True:
            return False
        history = history if isinstance(history, dict) else {}
        observations = history.get("observations", [])
        # Match the list's visible history badges, including partial observations.
        if any(item.get("globalFreeleech") is True or (item.get("freeleech") or 0) > 0
               or any(item.get(key) is True for key in ("doubleUpload", "green", "featured", "internal")) for item in observations):
            return False
        known = (history.get("downloaded", 0) > 0 and not history.get("unknownBytes")
                 and sum(item.get("bytes", 0) for item in observations) >= history["downloaded"]
                 and all(item.get("globalFreeleech") is False and item.get("freeleech") == 0
                         and item.get("doubleUpload") is False and type(item.get("green")) is bool for item in observations))
        return not known

    def recovery_needed(self, torrents, owners):
        identifiers = list(dict.fromkeys(str(row.get("hash", "")).lower() for row in torrents if isinstance(row, dict)))
        if not identifiers:
            return {}
        with self.database() as db:
            saved = {(identifier, added): json.loads(payload) for identifier, added, payload in db.execute(
                "SELECT hash,added,payload FROM download_history WHERE hash IN (" + ",".join("?" for _ in identifiers) + ")", identifiers)}
        return {str(row.get("hash", "")).lower(): self.history_unknown(
            saved.get((str(row.get("hash", "")).lower(), row.get("added_on") or 0)),
            owners.get(str(row.get("hash", "")).lower())) for row in torrents if isinstance(row, dict)}

    def conditions(self, row, credit, now, accounts=None):
        accounts = self.accounts() if accounts is None else accounts
        try:
            host = urlsplit(row.get("tracker", "")).hostname
        except (ValueError, TypeError, AttributeError):
            host = None
        known = host in {"t.nordicbytes.org", "t.nordicswarm.org", "t.polarswarm.org", "nordicbytes.org"} or any(a.get("name") == credit.get("tracker_name") and a.get("name") for a in accounts)
        current = None
        if known:
            for account in accounts:
                if credit.get("tracker_id"):
                    if account.get("id") != credit["tracker_id"]: continue
                elif account.get("provider", "nordicbytes") != "nordicbytes":
                    continue
                age = now - (account.get("checkedAt") or 0)
                events = account.get("globalEvents")
                if account.get("status") == "ok" and 0 <= age <= 90 and isinstance(events, list):
                    active = any(e.get("type") == "global_freeleech" and self.end(e.get("endsAt")) > now for e in events if isinstance(e, dict))
                    current = active if current is not True else True
        benefits = credit.get("tracker_benefits") or {}
        return {"globalFreeleech": current, "green": credit.get("green_active") if credit.get("green_pending") is False else None,
                "freeleech": benefits.get("freeleech"), "doubleUpload": benefits.get("doubleUpload"),
                "featured": benefits.get("featured"), "internal": benefits.get("internal")}

    @staticmethod
    def end(value):
        from datetime import datetime
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError, AttributeError):
            return 0

    def observe(self, db, row, credit, now=None, accounts=None):
        now = time.time() if now is None else now
        identifier = str(row.get("hash", "")).lower()
        if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", identifier):
            return {}
        added = row.get("added_on") or 0
        if type(added) not in (int, float) or not math.isfinite(added) or added < 0:
            return {}
        downloaded = row.get("downloaded")
        if type(downloaded) not in (int, float) or not math.isfinite(downloaded) or downloaded < 0:
            return {}
        stored = db.execute("SELECT payload FROM download_history WHERE hash=? AND added=?", (identifier, added)).fetchone()
        record = json.loads(stored[0]) if stored else {"downloaded": downloaded, "unknownBytes": downloaded, "observations": [], "at": now}
        if downloaded < record["downloaded"]:
            return record
        delta = downloaded - record["downloaded"]
        # Completed, unchanged downloads retain their final evidence. Do not
        # rewrite every row on each dashboard/worker read or apply today's flags.
        if stored and record.get("completedAt") and not delta:
            return record
        conditions = self.conditions(row, credit, now, accounts)
        if delta:
            # A boundary, stale API or monitoring gap cannot establish which bytes
            # benefited. Keep evidence rather than backdating today's event.
            trusted = record.get("conditions") == conditions and 0 <= now-record["at"] <= 90
            item = {"at": now, "bytes": delta, **conditions}
            if not trusted:
                for key in conditions:
                    item[key] = None
            observations = record["observations"]
            if observations and all(observations[-1].get(k) == item.get(k) for k in ("globalFreeleech", "green", "freeleech", "doubleUpload", "featured", "internal")):
                observations[-1]["bytes"] += delta
                observations[-1]["until"] = now
            else:
                observations.append(item)
            # Bounded observations retain aggregate proof even over long downloads.
            if len(observations) > 256:
                record["unknownBytes"] += observations.pop(0)["bytes"]
        record.update(downloaded=downloaded, at=now, conditions=conditions)
        if row.get("progress") == 1 and not record.get("completedAt"):
            record["completedAt"] = now
        db.execute("INSERT OR REPLACE INTO download_history VALUES (?,?,?)", (identifier, added, json.dumps(record)))
        return record
