import json
import secrets
import re
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SeedingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ratio: float = Field(ge=1, le=1000, allow_inf_nan=False)


class FeedSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(default="", max_length=4096)
    folder: str = Field(default="", max_length=120)
    ratio: float = Field(default=1, ge=1, le=1000, allow_inf_nan=False)
    start_at: str | None = Field(default=None, max_length=40)

    @field_validator("folder")
    @classmethod
    def folder_shape(cls, value):
        if value and (value.startswith(("/", "\\")) or "\\" in value or any(part in {"", ".", ".."} for part in value.split("/")) or any(ord(char) < 32 for char in value)):
            raise ValueError("Expected a relative download folder")
        return value

    @field_validator("url")
    @classmethod
    def address_shape(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Expected an HTTPS feed URL without userinfo")
        return value

    @field_validator("start_at")
    @classmethod
    def date_shape(cls, value):
        if value is not None:
            from datetime import datetime
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Start date must include a time zone")
            return parsed.isoformat()
        return value


class TrackerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(default="nordicbytes", pattern="^nordicbytes$")
    api_key: str = Field(default="", max_length=512)


def install_features(app, authorized, database, qbit):
    with database() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS settings (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,id))")

    def torrent_hash(value):
        import re
        if not re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", value):
            raise HTTPException(422, "Invalid torrent hash")
        return value.lower()

    async def read_json(path, params=None):
        response = await qbit(path, payload=params)
        try:
            return response.json()
        except ValueError:
            raise HTTPException(503, "Invalid torrent client response") from None

    def rows(kind):
        with database() as connection:
            return [{"id": row["id"], **json.loads(row["payload"])} for row in connection.execute("SELECT id,payload FROM settings WHERE kind=? ORDER BY rowid", (kind,))]

    def save(kind, payload, identifier=None):
        existing = next((row for row in rows(kind) if row["id"] == identifier), None)
        if identifier and existing is None:
            raise HTTPException(404, "Settings not found")
        secret = "url" if kind == "feeds" else "api_key"
        if not payload[secret]:
            if not existing:
                raise HTTPException(422, "A source credential is required")
            payload[secret] = existing[secret]
        identifier = identifier or secrets.token_hex(12)
        with database() as connection:
            connection.execute("INSERT INTO settings VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload", (kind, identifier, json.dumps(payload)))
        return {"saved": True, "id": identifier}

    def tracker_account(identifier):
        account = next((row for row in rows("trackers") if row["id"] == identifier), None)
        if account is None:
            raise HTTPException(404, "Tracker account not found")
        return account

    async def tracker_read(account, path, params=None, binary=False):
        limit = 4 * 1024 * 1024 if binary else 1024 * 1024
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                headers = {} if binary else {"Authorization": "Bearer " + account["api_key"], "Accept": "application/json"}
                async with client.stream("GET", "https://nordicbytes.org" + path, params=params, headers=headers) as response:
                    if response.status_code in (401, 403):
                        raise HTTPException(502, "Tracker API key lacks access")
                    if response.status_code == 429:
                        raise HTTPException(503, "Tracker rate limit reached; try again later")
                    if response.status_code != 200:
                        raise HTTPException(502, "Tracker request failed")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > limit:
                            raise HTTPException(502, "Tracker response is too large")
            return bytes(content) if binary else json.loads(content)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "Tracker unavailable or invalid response") from None

    @app.get("/api/trackers/{identifier}/search")
    async def tracker_search(identifier: str, query: str = Query(default="", max_length=200), session=Depends(authorized)):
        account = tracker_account(identifier)
        data = await tracker_read(account, "/api/torrents/filter", {"name": query, "perPage": 50})
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise HTTPException(502, "Invalid tracker search response")
        results = []
        for row in data["data"][:50]:
            if not isinstance(row, dict) or not re.fullmatch(r"[1-9][0-9]{0,17}", str(row.get("id", ""))):
                continue
            attributes = row.get("attributes")
            if not isinstance(attributes, dict) or not isinstance(attributes.get("name"), str):
                continue
            result = {"id": str(row["id"]), "name": attributes["name"][:300]}
            for field in ("size", "seeders", "leechers"):
                value = attributes.get(field)
                result[field] = value if type(value) is int and 0 <= value <= 2**63 - 1 else None
            results.append(result)
        return {"results": results, "limit": 50, "downloadsEnabled": False}

    @app.post("/api/trackers/{identifier}/torrents/{torrent_id}")
    async def tracker_import(identifier: str, torrent_id: str, body: SeedingPolicy, session=Depends(authorized)):
        if not re.fullmatch(r"[1-9][0-9]{0,17}", torrent_id):
            raise HTTPException(422, "Invalid tracker torrent ID")
        account = tracker_account(identifier)
        data = await tracker_read(account, "/api/torrents/" + torrent_id)
        row = data.get("data") if isinstance(data, dict) else None
        attributes = row.get("attributes") if isinstance(row, dict) else None
        if not isinstance(attributes, dict) or str(row.get("id")) != torrent_id:
            raise HTTPException(502, "Invalid tracker torrent response")
        link = attributes.get("download_link")
        if not isinstance(link, str) or len(link) > 4096:
            raise HTTPException(502, "Tracker did not provide a torrent download")
        address = urlsplit(link)
        if address.scheme != "https" or address.netloc != "nordicbytes.org" or address.fragment or not address.path.startswith("/torrents/download/"):
            raise HTTPException(502, "Unsafe tracker download address")
        content = await tracker_read(account, address.path + ("?" + address.query if address.query else ""), binary=True)
        if not content.startswith(b"d") or not content.endswith(b"e"):
            raise HTTPException(502, "Tracker did not return a torrent file")
        response = await qbit("/api/v2/torrents/add", "POST", {"stopped": "true", "paused": "true", "savepath": "/downloads", "ratioLimit": str(body.ratio), "seedingTimeLimit": "2940"}, files={"torrents": ("tracker.torrent", content, "application/x-bittorrent")})
        if response.status_code != 204 and response.text.strip() != "Ok.":
            raise HTTPException(502, "Torrent client rejected the torrent file")
        return {"added": True, "stopped": True, "downloadsEnabled": False}

    @app.get("/api/transfer")
    async def transfer(session=Depends(authorized)):
        result = await read_json("/api/v2/transfer/info")
        if not isinstance(result, dict):
            raise HTTPException(503, "Invalid transfer response")
        return {key: result.get(key) for key in ("dl_info_speed", "up_info_speed", "dl_info_data", "up_info_data", "connection_status")}

    @app.get("/api/runtime")
    def runtime(session=Depends(authorized)):
        return {"vpn": "unverified", "downloadsEnabled": False, "reason": "VPN supervisor is not connected", "rssWorker": "not_connected", "trackerSync": "not_connected"}

    @app.get("/api/torrents/{identifier}/details")
    async def details(identifier: str, session=Depends(authorized)):
        identifier = torrent_hash(identifier)
        properties = await read_json("/api/v2/torrents/properties", {"hash": identifier})
        if not isinstance(properties, dict):
            raise HTTPException(503, "Invalid torrent properties")
        allowed = {"total_size", "total_downloaded", "total_uploaded", "seeding_time", "share_ratio", "addition_date", "completion_date", "peers", "seeds", "dl_limit", "up_limit", "pieces_have", "pieces_num"}
        files = await read_json("/api/v2/torrents/files", {"hash": identifier})
        trackers = await read_json("/api/v2/torrents/trackers", {"hash": identifier})
        peers = await read_json("/api/v2/sync/torrentPeers", {"hash": identifier})
        if not isinstance(files, list) or not isinstance(trackers, list) or not isinstance(peers, dict) or not isinstance(peers.get("peers", {}), dict):
            raise HTTPException(503, "Invalid torrent details")
        return {
            "properties": {key: value for key, value in properties.items() if key in allowed},
            "files": [{key: row.get(key) for key in ("index", "name", "size", "progress", "priority")} for row in files[:1000]],
            "trackers": [{"host": urlsplit(row.get("url", "")).hostname or "DHT / PeX / LSD", "status": row.get("status"), "num_peers": row.get("num_peers")} for row in trackers[:100]],
            "peers": [{key: row.get(key) for key in ("ip", "port", "client", "progress", "dl_speed", "up_speed")} for row in list(peers.get("peers", {}).values())[:200]],
        }

    @app.post("/api/torrents/{identifier}/policy")
    async def policy(identifier: str, body: SeedingPolicy, session=Depends(authorized)):
        await qbit("/api/v2/torrents/setShareLimits", "POST", {"hashes": torrent_hash(identifier), "ratioLimit": str(body.ratio), "seedingTimeLimit": "2940", "inactiveSeedingTimeLimit": "-1"})
        return {"saved": True, "ratio": body.ratio, "seedingMinutes": 2940}

    @app.get("/api/feeds")
    def feeds(session=Depends(authorized)):
        return [{key: value for key, value in row.items() if key != "url"} | {"configured": True, "paused": True, "status": "not_connected"} for row in rows("feeds")]

    @app.post("/api/feeds")
    def add_feed(body: FeedSettings, session=Depends(authorized)):
        return save("feeds", body.model_dump())

    @app.put("/api/feeds/{identifier}")
    def edit_feed(identifier: str, body: FeedSettings, session=Depends(authorized)):
        return save("feeds", body.model_dump(), identifier)

    @app.get("/api/trackers")
    def accounts(session=Depends(authorized)):
        return [{key: value for key, value in row.items() if key != "api_key"} | {"configured": True, "status": "not_connected", "stats": None} for row in rows("trackers")]

    @app.post("/api/trackers")
    def add_account(body: TrackerSettings, session=Depends(authorized)):
        return save("trackers", body.model_dump())

    @app.put("/api/trackers/{identifier}")
    def edit_account(identifier: str, body: TrackerSettings, session=Depends(authorized)):
        return save("trackers", body.model_dump(), identifier)

    @app.delete("/api/settings/{kind}/{identifier}")
    def remove_settings(kind: str, identifier: str, session=Depends(authorized)):
        if kind not in {"feeds", "trackers"}:
            raise HTTPException(404, "Settings not found")
        with database() as connection:
            cursor = connection.execute("DELETE FROM settings WHERE kind=? AND id=?", (kind, identifier))
            if not cursor.rowcount:
                raise HTTPException(404, "Settings not found")
        return {"removed": True, "filesPreserved": True}