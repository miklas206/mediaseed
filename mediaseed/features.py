import asyncio
import base64
import hashlib
import json
import math
import re
import secrets
import time
from typing import Literal
from urllib.parse import urlsplit, parse_qs

import httpx
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mediaseed.cleanup import source_hashes, CleanupPolicy, announce_hosts
from mediaseed.covers import Covers, matching_attributes
from mediaseed.swarm import SwarmMonitor
from mediaseed.rss_worker import RssWorker
from mediaseed.activity import Activity
from mediaseed import feed_uploads, feed_series
from mediaseed.nordic_ranks import rank_progress
from mediaseed.tracker_events import global_events
from mediaseed.download_history import DownloadHistory
from mediaseed.background import run_background
from mediaseed.traffic_history import TrafficHistory
from mediaseed.intelligence import Intelligence, metadata, text
from mediaseed.tracker_reads import TrackerReads
from mediaseed.tracker_sites import origin, identity, normalize_origin, download_address, secure_client, account_data, matching_accounts


class SeedingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ratio: float = Field(ge=1, le=1000, allow_inf_nan=False)


class FeedState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class FeedSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(default="", max_length=4096)
    location: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9-]{1,80}$")
    folder: str = Field(default="", max_length=1000)
    ratio: float = Field(default=1, ge=1, le=1000, allow_inf_nan=False)
    cleanup: CleanupPolicy = Field(default_factory=CleanupPolicy)
    start_at: str | None = Field(default=None, max_length=40)
    include: list[str] = Field(default_factory=list, max_length=50)
    exclude: list[str] = Field(default_factory=list, max_length=50)
    max_bytes: int | None = Field(default=None, ge=1, le=100 * 1024 ** 4)
    badges: list[Literal["freeleech", "double_upload", "featured", "internal", "refundable"]] = Field(default_factory=list, max_length=5)

    @field_validator("include", "exclude")
    @classmethod
    def fragments(cls, values):
        if any(not any(c.isalnum() for c in value) or len(value) > 120 or any(ord(c) < 32 for c in value) for value in values):
            raise ValueError("Use up to 50 plain title fragments of 120 characters")
        return list(dict.fromkeys(value.strip() for value in values))

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
    provider: str = Field(default="nordicbytes", pattern="^(nordicbytes|unit3d)$")
    base_url: str = Field(default="", max_length=300)
    api_key: str = Field(default="", max_length=512, pattern=r"^[^\x00-\x20\x7f]*$")

    @field_validator("api_key", mode="before")
    @classmethod
    def trim_key(cls, value):
        return value.strip() if isinstance(value,str) else value

    @field_validator("base_url")
    @classmethod
    def website(cls, value):
        return normalize_origin(value) if value else value



def queue_summary(torrents, limit=None):
    active = [row for row in torrents if isinstance(row, dict) and row.get("state") in {"downloading", "stalledDL", "metaDL", "forcedDL"}]
    waiting = sum(isinstance(row, dict) and row.get("state") == "queuedDL" for row in torrents)
    rss = sum(str(row.get("category", "")).startswith("MediaSeed-RSS-") for row in active)
    ratio = sum(row.get("category") == "MediaSeed-Ratio" for row in active)
    return {"limit": limit, "active": len(active), "waiting": waiting, "rssActive": rss, "manualActive": len(active) - rss - ratio, "boundedView": len(torrents) >= 500, **({"ratioActive": ratio} if ratio else {})}


async def add_stopped(qbit, ratio, content=None, magnet=None, start=False, vpn_status=None, tag=None, savepath="/downloads", category=None):
    if start and (vpn_status is None or not (await vpn_status()).get("downloadsEnabled")):
        raise HTTPException(409, "Starting requires a verified VPN. Choose add on pause instead.")
    paused = "false" if start else "true"
    payload = {"stopped": paused, "paused": paused, "savepath": savepath, "ratioLimit": str(ratio), "seedingTimeLimit": "2940", "shareLimitAction": "Stop"}
    if tag:
        payload["tags"] = tag
    if category:
        payload["category"] = category
    files = None
    if magnet is not None:
        payload["urls"] = magnet
    else:
        files = {"torrents": ("upload.torrent", content, "application/x-bittorrent")}
    response = await qbit("/api/v2/torrents/add", "POST", payload, files=files)
    accepted = response.status_code == 204 or response.text.strip() == "Ok."
    pending = False
    outcome = {}
    try:
        outcome = response.json()
    except ValueError:
        pass
    if not accepted and isinstance(outcome, dict) and outcome.get("failure_count") == 0:
        accepted = outcome.get("success_count") == 1 and outcome.get("pending_count") == 0
        pending = magnet is not None and outcome.get("success_count") == 0 and outcome.get("pending_count") == 1
    if not accepted and not pending:
        raise HTTPException(502, "Torrent client rejected the torrent file")
    result = {"added": accepted, "pending": pending, "stopped": not start, "downloadsEnabled": start}
    if start:
        result["startRequested"] = True
    return result



class InsightSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lowBufferGiB: int = Field(default=10,ge=0,le=100000,strict=True)


def install_features(app, authorized, database, qbit, vpn_status=None, data_dir=None, cleanup=None, credit=None, storage=None):
    from mediaseed.torrent_import import install_import_status
    install_import_status(app,authorized,qbit,storage,cleanup)
    tracker_cache = {}
    intelligence = Intelligence(database)
    traffic = TrafficHistory(database)
    shared_reads = TrackerReads()
    app.state.tracker_reads = shared_reads
    activity = Activity(database)

    @app.get("/api/activity")
    def automatic_activity(session=Depends(authorized)):
        return activity.public()
    tracker_lock = asyncio.Lock()
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
        incoming_key = bool(payload.get(secret))
        if not payload[secret]:
            if not existing:
                raise HTTPException(422, "A source credential is required")
            payload[secret] = existing[secret]
        if kind == "feeds":
            if payload.get("location") is not None:
                if storage is None:
                    raise HTTPException(409, "Download folder is unavailable or read-only")
                storage.destination(payload["location"], payload["folder"])
            payload["enabled"] = existing.get("enabled", False) if existing else False
        if kind == "trackers":
            if payload["provider"] == "nordicbytes":
                if payload.get("base_url") not in ("", "https://nordicbytes.org"):
                    raise HTTPException(422, "Choose UNIT3D for another tracker website")
                payload["base_url"] = "https://nordicbytes.org"
            elif not payload.get("base_url"):
                raise HTTPException(422, "A tracker website address is required")
            elif payload["base_url"] == "https://nordicbytes.org":
                payload["provider"] = "nordicbytes"
            if existing and origin(existing) != origin(payload) and not incoming_key:
                raise HTTPException(422, "Enter an API key when changing tracker website")
        identifier = identifier or secrets.token_hex(12)
        with database() as connection:
            connection.execute("INSERT INTO settings VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload", (kind, identifier, json.dumps(payload)))
        return {"saved": True, "id": identifier, **({"start_at": payload.get("start_at")} if kind == "feeds" else {})}

    def tracker_account(identifier):
        account = next((row for row in rows("trackers") if row["id"] == identifier), None)
        if account is None:
            raise HTTPException(404, "Tracker account not found")
        return account

    async def fetch_tracker(account, path, params=None, binary=False):
        limit = 4 * 1024 * 1024 if binary else 1024 * 1024
        credential=identity(account)
        denied=shared_reads.blocked.get(credential)
        if not binary and denied and time.monotonic()<denied[0]:
            raise HTTPException(denied[1],denied[2])
        try:
            if shared_reads.client is None:
                shared_reads.client = secure_client(httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False))
            client = shared_reads.client
            headers = {"Cookie":""} if binary else {"Authorization": "Bearer " + account["api_key"], "Accept": "application/json", "Cookie":""}
            async with client.stream("GET", origin(account) + path, params=params, headers=headers) as response:
                if response.status_code in (401, 403):
                    shared_reads.blocked[credential]=(time.monotonic()+60,502,"Tracker API key lacks access")
                    if len(shared_reads.blocked)>100: shared_reads.blocked.pop(next(iter(shared_reads.blocked)))
                    raise HTTPException(502, "Tracker API key lacks access")
                if response.status_code == 429:
                    retry=response.headers.get('Retry-After','60')
                    retry=int(retry) if retry.isdecimal() else 60
                    shared_reads.blocked[credential]=(time.monotonic()+max(1,min(300,retry)),503,"Tracker rate limit reached; try again later")
                    raise HTTPException(503, "Tracker rate limit reached; try again later")
                if response.status_code == 404 and path == "/api/user":
                    raise HTTPException(502, "This tracker does not provide a compatible account API")
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

    async def tracker_read(account, path, params=None, binary=False, cache_seconds=0):
        if binary:
            return await fetch_tracker(account,path,params,binary=True)
        return await shared_reads.read(account,path,params,lambda: fetch_tracker(account,path,params),ttl=cache_seconds)

    async def cached_tracker_read(account,path,params=None):
        return await tracker_read(account,path,params,cache_seconds=30)

    @app.get("/api/insights")
    def insights(days: int = Query(default=7,ge=1,le=90), session=Depends(authorized)):
        return {**intelligence.public(tracker_overview(),days), 'traffic':traffic.public(days)}

    @app.put("/api/insights/settings")
    def insight_settings(body: InsightSettings, session=Depends(authorized)):
        if session.get('role') != 'administrator':
            raise HTTPException(403,'Administrator access required')
        return intelligence.configure(body.model_dump())

    @app.get("/api/torrents/{identifier}/media")
    def torrent_media(identifier: str, session=Depends(authorized)):
        return {"metadata":intelligence.media(torrent_hash(identifier))}

    @app.get("/api/trackers/{identifier}/search")
    async def tracker_search(identifier: str, query: str = Query(default="", max_length=200), free: int | None = Query(default=None,ge=0,le=100), double: bool = False, featured: bool = False, availability: Literal["all","alive","dead"] = "all", session=Depends(authorized)):
        account = tracker_account(identifier)
        params={"name": query, "perPage": 50}
        if free is not None:
            if free not in {0,25,50,75,100}: raise HTTPException(422,'Choose a supported freeleech percentage')
            params['free']=free
        if double: params['doubleup']=1
        if featured: params['featured']=1
        if availability!='all': params[availability]=1
        data = await tracker_read(account, "/api/torrents/filter", params, cache_seconds=30)
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
            public_metadata=metadata(attributes)
            result.update({key:public_metadata[key] for key in ['category','resolution','release_year','freeleech','double_upload','featured','internal','refundable','genres']})
            identifier_hash=str(attributes.get('info_hash','')).lower()
            intelligence.remember(identifier_hash,account,attributes)
            results.append(result)
        return {"results": results, "limit": 50, "downloadsEnabled": False, "checkedAt":time.time(), "bounded":True}

    def tracker_username(identifier):
        cached = tracker_cache.get(identifier)
        account = next((a for a in rows("trackers") if a["id"] == identifier), None)
        return cached["public"].get("username") if cached and account and cached["key"] == identity(account) and time.monotonic() - cached["checked"] < 360 and cached["public"].get("status") == "ok" else None
    covers = Covers(data_dir, rows, cached_tracker_read, qbit, username=tracker_username) if data_dir else None
    rss_worker = RssWorker(database, rows, qbit, vpn_status, storage=storage) if vpn_status else None
    if rss_worker:
        rss_worker.cleanup = cleanup
    if covers:
        covers.metadata_sink = intelligence.remember
        covers.metadata_source = intelligence.media

    @app.post("/api/torrents/{identifier}/metadata/refresh", status_code=202)
    async def refresh_torrent_metadata(identifier: str, session=Depends(authorized)):
        if session.get("role") == "viewer":
            raise HTTPException(403, "Operator access required")
        identifier = torrent_hash(identifier)
        torrents = await read_json("/api/v2/torrents/info", {"hashes": identifier})
        torrent = next((row for row in torrents if isinstance(row, dict) and str(row.get("hash", "")).lower() == identifier), None) if isinstance(torrents, list) else None
        if torrent is None:
            raise HTTPException(404, "Torrent not found")
        if not covers:
            raise HTTPException(409, "Tracker metadata refresh is unavailable")
        result = await covers.refresh(torrent)
        if result == "unavailable":
            raise HTTPException(409, "Configure a matching tracker API account to fetch images and metadata")
        if result == "cooldown":
            raise HTTPException(429, "Tracker refresh already requested; try again in five minutes")
        if result != "queued":
            raise HTTPException(503, "Metadata refresh queue is busy; try again later")
        # Discard short-lived search responses so a formerly unpublished upload
        # can be discovered on the next worker tick.
        credentials = {identity(account) for account in matching_accounts(rows("trackers")[:10], torrent.get("tracker"))}
        for key in list(shared_reads.saved):
            if key[0] in credentials:
                shared_reads.saved.pop(key, None)
        return {"status": "queued"}

    async def verify_badges(identifier, title):
        for account in [a for a in rows("trackers") if a.get("provider","nordicbytes")=="nordicbytes"][:3]:
            result = await tracker_read(account, "/api/torrents/filter", {"name": title[:200], "perPage": 50})
            for row in result.get("data", [])[:50]:
                attributes = row.get("attributes", {})
                if str(attributes.get("info_hash", "")).lower() == identifier:
                    covers.remember(identifier, account, attributes)
                    return benefits(identifier, session={})
        return {"status": "unknown"}

    if rss_worker:
        rss_worker.benefits_lookup = verify_badges

    @app.post("/api/torrent-preview")
    async def preview(request: Request, session=Depends(authorized)):
        content = bytearray()
        async for part in request.stream():
            content.extend(part)
            if len(content) > 4 * 1024 * 1024:
                raise HTTPException(413, "Torrent file exceeds 4 MiB")
        if request.headers.get("content-type", "").startswith("application/x-bittorrent"):
            metadata = source_hashes(content=bytes(content), include_metadata=True, include_trackers=True)
        else:
            try:
                body = json.loads(content)
                magnet = body["magnet"]
                if not isinstance(magnet, str) or len(magnet) > 8192 or not magnet.startswith("magnet:?"):
                    raise ValueError()
                hashes = source_hashes(magnet=magnet)
                if not hashes or any(not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value) for value in hashes):
                    raise ValueError()
                metadata = {"name": parse_qs(urlsplit(magnet).query).get("dn", [None])[0], "size": None, "hashes": hashes,
                            "tracker_hosts": announce_hosts({b"announce-list":[[v.encode() for v in parse_qs(urlsplit(magnet).query).get("tr",[])]]})}
            except (KeyError, ValueError, TypeError):
                raise HTTPException(422, "Invalid torrent source") from None
        selected = {}
        for host in metadata.get("tracker_hosts",[]):
            for account in matching_accounts(rows("trackers"), "https://"+host+"/announce"):
                selected[account["id"]] = account
        response = {"name":metadata["name"],"size":metadata["size"],"estimates":[],"benefits":{},"metadataPending":False}
        if len(selected) != 1:
            return {**response,"lookupStatus":"tracker_unknown" if not selected else "tracker_ambiguous"}
        account = next(iter(selected.values()))
        response["tracker"] = {"id":account["id"],"name":account["name"]}
        try:
            attributes = None
            name = re.sub(r"\.(?:mkv|mp4|avi|m4v|iso)$", "", metadata.get("name") or "", flags=re.I)[:200]
            prefix = re.split(r"[ ._-](?:19|20)\d{2}(?:[ ._-]|$)",name,maxsplit=1)[0]
            names = list(dict.fromkeys(filter(None,[name,re.sub(r"[._]+"," ",name),prefix,re.sub(r"[._]+"," ",prefix)])))
            queries = [{"name":name,"perPage":50} for name in names] if names else [{"info_hash":metadata["hashes"][0],"perPage":50}]
            budget = 6
            for query in queries:
                cursor = None
                for page in range(2):
                    if not budget: break
                    budget -= 1
                    params = {**query,**({"cursor":cursor} if cursor else {})}
                    result = await tracker_read(account,"/api/torrents/filter",params,cache_seconds=30)
                    attributes = next((a for identifier in metadata["hashes"] if (a:=matching_attributes(result,identifier))),None)
                    if attributes: break
                    meta = result.get("meta",{}) if isinstance(result,dict) else {}
                    cursor = meta.get("next_cursor") if isinstance(meta,dict) else None
                    if not isinstance(cursor,str) or not 0 < len(cursor) <= 2048: break
                if attributes or not budget: break
            if not attributes:
                return {**response,"lookupStatus":"torrent_unmatched"}
            api_size = attributes.get("size")
            if type(api_size) is not int or api_size < 0 or (metadata["size"] is not None and metadata["size"] != api_size):
                return {**response,"lookupStatus":"torrent_unmatched"}
            current = (await refresh_accounts([account]))[0]
            stats = current.get("stats") or {}
            if current.get("status") != "ok":
                return {**response,"lookupStatus":"api_unavailable"}
            identifier = str(attributes["info_hash"]).lower()
            if covers:
                covers.remember(identifier,account,attributes)
                response["benefits"] = benefits(identifier,session={})
            events = current.get("globalEvents")
            global_free = any(e.get("type")=="global_freeleech" and DownloadHistory.end(e.get("endsAt"))>time.time() for e in events) if isinstance(events,list) else None
            response["benefits"]["globalFreeleech"] = global_free
            free = response["benefits"].get("freeleech")
            charged = 0 if global_free is True or free == 100 else api_size*(1-free/100) if free in {0,25,50,75} and global_free is False else None
            uploaded, downloaded = stats.get("uploaded"),stats.get("downloaded")
            if any(type(value) not in (int,float) or not math.isfinite(value) or value<0 for value in (uploaded,downloaded)):
                return {**response,"lookupStatus":"api_unavailable"}
            current_ratio = stats.get("ratio")
            if type(current_ratio) not in (int,float) or not math.isfinite(current_ratio) or current_ratio < 0:
                current_ratio = uploaded/downloaded if downloaded else None
            response.update(size=api_size,lookupStatus="matched",estimates=[{
                "name":account["name"],"accountId":account["id"],"uploaded":uploaded,"downloaded":downloaded,
                "current":current_ratio,
                "projected":current_ratio if charged==0 else uploaded/(downloaded+charged) if charged is not None and downloaded+charged else None,
                "charged":charged,"chargeKnown":charged is not None,"globalFreeleech":global_free}])
            return response
        except HTTPException:
            return {**response,"lookupStatus":"api_unavailable"}

    @app.get("/api/feeds/{identifier}/history")
    def feed_history(identifier: str, page: int = Query(default=1,ge=1,le=100000), pageSize: int = Query(default=25,ge=1,le=50),
                     search: str = Query(default='',max_length=120), view: Literal['downloads', 'series'] = 'downloads', sort: Literal['upload', 'average', 'recent'] = 'upload',
                     series: str | None = Query(default=None,pattern=r'^[a-f0-9]{64}$'), session=Depends(authorized)):
        if not any(row["id"] == identifier for row in rows("feeds")):
            raise HTTPException(404, "Feed not found")
        with database() as db:
            stats = feed_uploads.summary(db,identifier)
            if series is not None:
                return {**feed_series.members(db,identifier,series,page,pageSize), 'upload': stats}
            if view == 'series':
                return {**feed_series.groups(db,identifier,page,pageSize,sort,search), 'upload': stats}
            count = stats['downloads']
            values = db.execute("SELECT hash,title,added,credited FROM rss_downloads WHERE feed=? ORDER BY added DESC,hash LIMIT ? OFFSET ?", (identifier,pageSize,(page-1)*pageSize)).fetchall()
        return {"entries":[{"hash":h,"title":title or h,"created":at,"status":"added","creditedUploadedBytes":uploaded} for h,title,at,uploaded in values],"page":page,"pageSize":pageSize,"total":count,"upload":stats}

    @app.get("/api/covers/{identifier}")
    def cover(identifier: str, session=Depends(authorized)):
        path = covers.path(identifier) if covers else None
        if path is None or not path.is_file():
            raise HTTPException(404, "Cover unavailable")
        with path.open("rb") as content:
            prefix = content.read(12)
        media_type = "image/jpeg" if prefix.startswith(b"\xff\xd8\xff") else "image/png" if prefix.startswith(b"\x89PNG") else "image/webp"
        return FileResponse(path, media_type=media_type)

    @app.get("/api/torrents/{identifier}/benefits")
    def benefits(identifier: str, session=Depends(authorized)):
        path = covers.path(torrent_hash(identifier)) if covers else None
        if path is None or not path.with_suffix(".json").is_file():
            return {"status": "unknown"}
        try:
            evidence = json.loads(path.with_suffix(".json").read_text())
            if evidence.get("infoHash") != identifier.lower() or not 0 <= time.time() - evidence.get("checkedAt", 0) < 300:
                return {"status": "unknown"}
            return evidence
        except (ValueError, OSError, TypeError):
            return {"status": "unknown"}

    async def tracker_file(identifier, torrent_id):
        if not re.fullmatch(r"[1-9][0-9]{0,17}", torrent_id):
            raise HTTPException(422, "Invalid tracker torrent ID")
        account = tracker_account(identifier)
        data = await tracker_read(account, "/api/torrents/" + torrent_id)
        row = data.get("data", data) if isinstance(data, dict) else None
        attributes = row.get("attributes") if isinstance(row, dict) else None
        if not isinstance(attributes, dict) or str(row.get("id")) != torrent_id:
            raise HTTPException(502, "Invalid tracker torrent response")
        link = attributes.get("download_link")
        if not isinstance(link, str) or len(link) > 4096:
            raise HTTPException(502, "Tracker did not provide a torrent download")
        address = download_address(account, link)
        content = await tracker_read(account, address.path + ("?" + address.query if address.query else ""), binary=True)
        if not content.startswith(b"d") or not content.endswith(b"e"):
            raise HTTPException(502, "Tracker did not return a torrent file")
        hashes = source_hashes(content=content)
        if not hashes:
            raise HTTPException(502, "Tracker did not return a torrent file")
        expected_hash = str(attributes.get("info_hash", "")).lower()
        if expected_hash and expected_hash not in hashes:
            raise HTTPException(502, "Torrent file does not match the selected release")
        if covers:
            for info_hash in hashes:
                covers.remember(info_hash, account, attributes)
        return content, attributes

    @app.post("/api/trackers/{identifier}/torrents/{torrent_id}/prepare")
    async def tracker_prepare(identifier: str, torrent_id: str, session=Depends(authorized)):
        content, attributes = await tracker_file(identifier, torrent_id)
        name = attributes.get("name")
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', name if isinstance(name, str) else 'download')[:220].strip(' .') or 'download'
        if not name.lower().endswith('.torrent'):
            name += '.torrent'
        return {"name": name, "base64": base64.b64encode(content).decode('ascii')}

    @app.post("/api/trackers/{identifier}/torrents/{torrent_id}")
    async def tracker_import(identifier: str, torrent_id: str, body: SeedingPolicy, session=Depends(authorized)):
        content, _ = await tracker_file(identifier, torrent_id)
        await add_stopped(qbit, body.ratio, content=content)
        return {"added": True, "stopped": True, "downloadsEnabled": False}

    @app.get("/api/transfer")
    async def transfer(session=Depends(authorized)):
        result = await read_json("/api/v2/transfer/info")
        if not isinstance(result, dict):
            raise HTTPException(503, "Invalid transfer response")
        return {key: result.get(key) for key in ("dl_info_speed", "up_info_speed", "dl_info_data", "up_info_data", "connection_status")}

    @app.get("/api/queue")
    async def queue_status(session=Depends(authorized)):
        torrents = await read_json("/api/v2/torrents/info", {"limit": 500})
        if not isinstance(torrents, list):
            raise HTTPException(503, "Invalid queue response")
        preferences = await read_json("/api/v2/app/preferences", {})
        limit = preferences.get("max_active_downloads") if isinstance(preferences, dict) else None
        if isinstance(preferences, dict) and preferences.get("queueing_enabled") is False:
            limit = -1
        return queue_summary(torrents, limit)

    @app.get("/api/runtime")
    async def runtime(session=Depends(authorized)):
        observed = await vpn_status() if vpn_status else {"vpn": "unverified", "downloadsEnabled": False, "reason": "VPN supervisor is not connected"}
        return {**observed, "rssWorker": "vpn_bound" if rss_worker and observed.get("downloadsEnabled") else "blocked", "trackerSync": "configured" if rows("trackers") else "not_configured"}

    @app.get("/api/torrents/{identifier}/details")
    async def details(identifier: str, section: Literal["all","overview","files","connections"] = "all", session=Depends(authorized)):
        identifier = torrent_hash(identifier)
        if section == "files":
            files = await read_json("/api/v2/torrents/files", {"hash":identifier})
            if not isinstance(files,list): raise HTTPException(503,"Invalid torrent details")
            return {"files":[{key:row.get(key) for key in ("index","name","size","progress","priority")} for row in files[:1000]]}
        if section == "connections":
            trackers,peers = await asyncio.gather(read_json("/api/v2/torrents/trackers",{"hash":identifier}),read_json("/api/v2/sync/torrentPeers",{"hash":identifier}))
            if not isinstance(trackers,list) or not isinstance(peers,dict) or not isinstance(peers.get("peers",{}),dict): raise HTTPException(503,"Invalid torrent details")
            return {"trackers":[{"host":urlsplit(row.get("url","")).hostname or "DHT / PeX / LSD","status":row.get("status"),"num_peers":row.get("num_peers")} for row in trackers[:100]],"peers":[{key:row.get(key) for key in ("ip","port","client","progress","dl_speed","up_speed")} for row in list(peers.get("peers",{}).values())[:200]]}
        properties = await read_json("/api/v2/torrents/properties", {"hash": identifier})
        if not isinstance(properties, dict):
            raise HTTPException(503, "Invalid torrent properties")
        allowed = {"total_size", "total_downloaded", "total_uploaded", "seeding_time", "share_ratio", "addition_date", "completion_date", "peers", "seeds", "dl_limit", "up_limit", "pieces_have", "pieces_num"}
        if section == "overview":
            return {"properties":{key:value for key,value in properties.items() if key in allowed},"downloadFolder":storage.describe(properties.get("save_path")) if storage else None,"cleanup":cleanup.public(identifier) if cleanup else {"mode":"never"},"credit":credit.observe({"hash":identifier,"uploaded":properties.get("total_uploaded"),"downloaded":properties.get("total_downloaded"),"ratio":properties.get("share_ratio"),"added_on":properties.get("addition_date")}) if credit else {}}
        files = await read_json("/api/v2/torrents/files", {"hash": identifier})
        trackers = await read_json("/api/v2/torrents/trackers", {"hash": identifier})
        peers = await read_json("/api/v2/sync/torrentPeers", {"hash": identifier})
        if not isinstance(files, list) or not isinstance(trackers, list) or not isinstance(peers, dict) or not isinstance(peers.get("peers", {}), dict):
            raise HTTPException(503, "Invalid torrent details")
        return {
            "properties": {key: value for key, value in properties.items() if key in allowed},
            "downloadFolder": storage.describe(properties.get("save_path")) if storage else None,
            "files": [{key: row.get(key) for key in ("index", "name", "size", "progress", "priority")} for row in files[:1000]],
            "trackers": [{"host": urlsplit(row.get("url", "")).hostname or "DHT / PeX / LSD", "status": row.get("status"), "num_peers": row.get("num_peers")} for row in trackers[:100]],
            "peers": [{key: row.get(key) for key in ("ip", "port", "client", "progress", "dl_speed", "up_speed")} for row in list(peers.get("peers", {}).values())[:200]],
            "cleanup": cleanup.public(identifier) if cleanup else {"mode": "never"},
            "credit": credit.observe({"hash": identifier, "uploaded": properties.get("total_uploaded"), "downloaded": properties.get("total_downloaded"), "ratio": properties.get("share_ratio"), "added_on": properties.get("addition_date")}) if credit else {},
        }

    @app.post("/api/torrents/{identifier}/policy")
    async def policy(identifier: str, body: SeedingPolicy, session=Depends(authorized)):
        if cleanup and cleanup.public(identifier)["mode"] != "never":
            raise HTTPException(409, "Cancel automatic cleanup before changing the stop ratio")
        await qbit("/api/v2/torrents/setShareLimits", "POST", {"hashes": torrent_hash(identifier), "ratioLimit": str(body.ratio), "seedingTimeLimit": "2940", "inactiveSeedingTimeLimit": "-1", "shareLimitAction": "Stop"})
        return {"saved": True, "ratio": body.ratio, "seedingMinutes": -1, "unlimited": True}

    def feed_summary(identifier):
        with database() as db:
            return feed_uploads.summary(db,identifier)

    @app.get("/api/feeds")
    def feeds(session=Depends(authorized)):
        return [{key: value for key, value in row.items() if key != "url"} | {"configured": True, "upload":feed_summary(row["id"]), "paused": not row.get("enabled", False), "status": rss_worker.reports.get(row["id"], "Afventer VPN/feedposter") if rss_worker and row.get("enabled") else "Pauset"} for row in rows("feeds")]

    @app.get("/api/feeds/{identifier}")
    def feed_details(identifier: str, session=Depends(authorized)):
        if session.get("role") != "administrator":
            raise HTTPException(403, "App administrator access required")
        feed = next((row for row in rows("feeds") if row["id"] == identifier), None)
        if feed is None:
            raise HTTPException(404, "Feed not found")
        return feed

    @app.put("/api/feeds/{identifier}/state")
    async def feed_state(identifier: str, body: FeedState, session=Depends(authorized)):
        feed = next((row for row in rows("feeds") if row["id"] == identifier), None)
        if feed is None:
            raise HTTPException(404, "Feed not found")
        if body.enabled:
            if rss_worker is None:
                raise HTTPException(409, "VPN is not verified; feed activation blocked")
            rss_worker.destination(feed)
            if urlsplit(feed["url"]).netloc != "nordicbytes.org":
                raise HTTPException(422, "Only NordicBytes HTTPS RSS sources are enabled")
            if not (await vpn_status()).get("downloadsEnabled"):
                raise HTTPException(409, "VPN is not verified; feed activation blocked")
        with database() as connection:
            row = connection.execute("SELECT payload FROM settings WHERE kind='feeds' AND id=?", (identifier,)).fetchone()
            if row is None:
                raise HTTPException(404, "Feed not found")
            payload = json.loads(row["payload"])
            payload["enabled"] = body.enabled
            connection.execute("UPDATE settings SET payload=? WHERE kind='feeds' AND id=?", (json.dumps(payload), identifier))
        return {"saved": True, "enabled": body.enabled}

    @app.post("/api/feeds")
    def add_feed(body: FeedSettings, session=Depends(authorized)):
        return save("feeds", body.model_dump())

    @app.put("/api/feeds/{identifier}")
    def edit_feed(identifier: str, body: FeedSettings, session=Depends(authorized)):
        return save("feeds", body.model_dump(), identifier)

    async def refresh_accounts(selected=None):
        results = []
        async with tracker_lock:
            for account in (selected if selected is not None else rows("trackers")[:10]):
                cached = tracker_cache.get(account["id"])
                if not cached or cached["key"] != identity(account) or time.monotonic() - cached["checked"] >= 60:
                    try:
                        data = account_data(await tracker_read(account, "/api/user"))
                        stats = {}
                        for field in ("uploaded", "downloaded", "ratio", "buffer", "seeding", "leeching", "seedbonus", "hit_and_runs", "seeding_size", "total_uploads", "avg_seedtime", "total_seedtime"):
                            value = data["stats"].get(field)
                            stats[field] = value if type(value) in (int, float) and math.isfinite(value) else None
                        warnings = data["stats"].get("warnings")
                        stats["warnings"] = sum(value for key, value in warnings.items() if key in {"hnr", "manual"} and type(value) is int and value >= 0) if isinstance(warnings, dict) else None
                        # Rank identity uses the same cached account response, never another request.
                        ranks = {}
                        for source, target in (("autogroup", "automatic"), ("uploadgroup", "upload"), ("internalgroup", "internal")):
                            group = data.get(source)
                            title = group.get("title") if isinstance(group, dict) else None
                            if isinstance(title, str) and title.strip():
                                ranks[target] = title.strip()[:80]
                        if not ranks.get("automatic") and isinstance(data.get("group"), str) and data["group"].strip():
                            ranks["automatic"] = data["group"].strip()[:80]
                        notifications={key:data.get('events',{}).get(key) if type(data.get('events',{}).get(key)) is int and data['events'][key]>=0 else None for key in ['dm','notifications','tickets']} if isinstance(data.get('events'),dict) else {}
                        premium=data.get('premium') if isinstance(data.get('premium'),dict) else {}
                        event_rows=data.get('events',{}).get('global',[]) if isinstance(data.get('events'),dict) else []
                        public_events=[]
                        for event in event_rows[:20] if isinstance(event_rows,list) else []:
                            if not isinstance(event,dict): continue
                            until=credit.download_history.end(event.get('ends_at')) if credit else 0
                            if until>time.time(): public_events.append({'type':text(event.get('type'),80),'title':text(event.get('title'),150),'endsAt':event.get('ends_at')[:80]})
                        public = {"status": "ok", "stats": stats, "ranks": ranks, "rankProgress": rank_progress(ranks, stats) if account.get("provider", "nordicbytes") == "nordicbytes" else None, "base_url": origin(account), "globalEvents": global_events(data), "username": str(data.get("username", ""))[:80], "checkedAt": time.time(), "notifications":notifications, "premium":{"type":text(premium.get('type'),80),"expiresAt":text(premium.get('expires_at'),80)}, "events":public_events,"lastSeen":text(data.get('last_seen'),80)}
                        intelligence.account(account['id'],public)
                    except (HTTPException, ValueError, TypeError):
                        previous = cached["public"] if cached and cached["key"] == identity(account) else {}
                        public = {**previous, "status": "stale" if previous.get("stats") else "error", "error": "Tracker account statistics are unavailable"}
                    cached = {"key": identity(account), "checked": time.monotonic(), "public": public}
                    tracker_cache[account["id"]] = cached
                results.append({key: value for key, value in account.items() if key != "api_key"} | {"configured": True, **cached["public"]})
        return results

    @app.get("/api/trackers")
    async def accounts(session=Depends(authorized)):
        return await refresh_accounts()

    @app.post("/api/trackers/test")
    async def test_account(body: TrackerSettings, session=Depends(authorized)):
        if session.get("role") != "administrator":
            raise HTTPException(403, "Administrator access required")
        account = body.model_dump()
        if not account["api_key"] or (account["provider"] == "unit3d" and not account["base_url"]):
            raise HTTPException(422, "Enter a tracker website and API key")
        if account["provider"] == "nordicbytes" and account["base_url"] not in ("", "https://nordicbytes.org"):
            raise HTTPException(422, "Choose UNIT3D for another tracker website")
        account_data(await tracker_read(account, "/api/user"))
        return {"connected": True, "base_url": origin(account)}

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

    swarm = SwarmMonitor(database, rows, qbit, covers.tracker_read if covers else tracker_read,
                         lambda identifier, account: covers.enrichment.load(identifier, account).get('record_id') if covers else None)
    app.state.swarm = swarm
    if covers:
        covers.swarm_sink = swarm.observe

    async def traffic_tick():
        try:
            values = (await qbit('/api/v2/transfer/info')).json()
            if not traffic.observe(values):
                # Standalone qBittorrent exposes these via sync. MediaVPN supplies
                # only the two totals on transfer/info; its whitelist stays closed.
                values = (await qbit('/api/v2/sync/maindata')).json()
                traffic.observe(values.get('server_state') if isinstance(values,dict) else None)
        except (HTTPException, ValueError, TypeError):
            if traffic.failure!='upgrade_required':traffic.failure = 'unavailable'

    async def background():
        async def account_tick():
            if rows("trackers"):
                await accounts(session={})
        jobs = [("accounts", account_tick), ("swarm", swarm.tick), ("traffic", traffic_tick)]
        for name, worker in (("rss", rss_worker), ("covers", covers)):
            if worker:
                jobs.append((name, worker.tick))
        await run_background(jobs, intervals={"covers": 60,"traffic":60})

    def tracker_overview():
        # Overview must not fetch third-party APIs or expose credentials/URLs.
        result = []
        for account in rows("trackers")[:10]:
            cached = tracker_cache.get(account["id"])
            fresh = cached and cached["key"] == identity(account)
            public = cached["public"] if fresh else {}
            result.append({"id": account["id"], "name": account["name"], "provider": account.get("provider","nordicbytes"), "base_url": origin(account), "status": public.get("status", "pending"), "stats": public.get("stats"), "ranks": public.get("ranks", {}), "rankProgress": public.get("rankProgress"), "checkedAt": public.get("checkedAt"), "globalEvents": public.get("globalEvents"), **{key:public.get(key) for key in ["premium","notifications","events","lastSeen"]}})
        return result

    app.state.tracker_overview = tracker_overview
    if credit:
        credit.tracker_accounts = lambda: [{k:a[k] for k in ("id","name","provider","base_url") if k in a} for a in rows("trackers")]
        credit.download_history.accounts = tracker_overview
        credit.observer = intelligence.observe
        credit.upload_owners = lambda torrents: intelligence.upload_owners(torrents,
            [{**account, "username": tracker_username(account["id"])} for account in rows("trackers")[:10]])
        if covers:
            covers.recovery_needed = lambda torrents: credit.download_history.recovery_needed(torrents, credit.upload_owners(torrents))
    return background
