"""Durable native torrent moves with independent before/after content verification."""
import asyncio
import hashlib
import json
import os
import re
import stat
import time
import uuid
from contextvars import ContextVar
from pathlib import PurePosixPath

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

INTERNAL = ContextVar("mediaseed_move", default=False)
IDENTITY = re.compile(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\Z")


class MoveChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hashes: list[str] = Field(min_length=1, max_length=100)
    location: str = Field(max_length=64)
    folder: str = Field(default="", max_length=1000)


def file_path(root, name):
    path = PurePosixPath(name)
    if (not name or not path.parts or path.is_absolute() or str(path) != name or "\\" in name
            or any(part in {".", "..", ".incomplete"} for part in path.parts)
            or any(ord(c) < 32 for c in name)):
        raise HTTPException(409, "Torrent contains an unsafe file path")
    result = root.joinpath(*path.parts)
    # Refuse links in any component, including dangling destination links.
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise HTTPException(409, "Torrent files must not contain symbolic links")
    return result


def digest(path, size, progress):
    if path.resolve(strict=True) != path:
        raise HTTPException(409, "Torrent files must not contain symbolic links")
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size != size:
            raise HTTPException(409, "Torrent file is missing or has the wrong size")
        value = hashlib.sha256()
        while block := stream.read(4 * 1024**2):
            value.update(block)
            progress(len(block))
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise HTTPException(409, "Torrent file changed during verification")
        return value.hexdigest()


class Moves:
    def __init__(self, database, qbit, storage, cleanup, credit, capable):
        self.database, self.qbit, self.storage = database, qbit, storage
        self.cleanup, self.credit, self.capable = cleanup, credit, capable
        self.lock = asyncio.Lock()
        self.progress = {}
        with database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS torrent_moves (id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL)")

    def records(self):
        with self.database() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT document FROM torrent_moves ORDER BY created DESC LIMIT 50")]

    def get(self, identifier):
        with self.database() as db:
            row = db.execute("SELECT document FROM torrent_moves WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, "Move not found")
        return json.loads(row[0])

    def save(self, job):
        with self.database() as db:
            db.execute("INSERT INTO torrent_moves VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,document=excluded.document",
                       (job["id"], job["created"], job["status"], json.dumps(job)))
            # Completed history is bounded; active/recovery records are retained.
            db.execute("DELETE FROM torrent_moves WHERE status='complete' AND id NOT IN (SELECT id FROM torrent_moves ORDER BY created DESC LIMIT 50)")

    def guard(self, path, payload):
        if INTERNAL.get():
            return
        identifiers = str((payload or {}).get("hashes", (payload or {}).get("hash", ""))).lower().split("|")
        if any(job["status"] != "complete" and any(row["hash"] in identifiers for row in job["items"]) for job in self.records()):
            raise HTTPException(409, "This torrent has an unfinished move. Finish or review the move first.")

    async def read(self, path, payload):
        response = await self.qbit("/api/v2/torrents/" + path, payload=payload)
        if response.status_code != 200:
            raise HTTPException(503, "Torrent client observation failed")
        try:
            return response.json()
        except ValueError:
            raise HTTPException(503, "Torrent client returned an invalid observation") from None

    async def torrent(self, identifier):
        rows = await self.read("info", {"hashes": identifier, "limit": 1})
        if not isinstance(rows, list):
            raise HTTPException(503, "Torrent client returned an invalid observation")
        row = next((r for r in rows if isinstance(r, dict) and str(r.get("hash", "")).lower() == identifier), None)
        if not row:
            raise HTTPException(409, "Torrent is no longer available")
        return row

    def root(self, destination):
        selected = self.storage.describe(destination)
        if selected is None:
            raise HTTPException(409, "Torrent is outside approved storage")
        return self.storage.directory(selected["location"], selected["folder"], writable=True)[1]

    async def create(self, choice):
        hashes = [value.lower() for value in choice.hashes]
        if any(not IDENTITY.fullmatch(value) for value in hashes) or len(set(hashes)) != len(hashes):
            raise HTTPException(422, "Choose distinct torrent identities")
        await self.capable()
        if self.lock.locked():
            raise HTTPException(409, "A move is already running")
        async with self.lock, self.cleanup.lock, self.credit.lock:
            if any(job["status"] != "complete" for job in self.records()):
                raise HTTPException(409, "Finish or review the current move before starting another")
            destination = self.storage.destination(choice.location, choice.folder)
            target = self.root(destination)
            items, reserved, top_owners = [], set(), {}
            for identifier in hashes:
                row = await self.torrent(identifier)
                if (row.get("progress") != 1 or row.get("state") not in {"uploading", "stalledUP", "forcedUP", "stoppedUP", "pausedUP", "queuedUP"}
                        or row.get("auto_tmm") is True):
                    raise HTTPException(409, "Only completed torrents with manual folder management can be moved")
                source = str(row.get("save_path", "")).rstrip("/")
                origin = self.root(source)
                if source == destination or origin == target:
                    raise HTTPException(409, "Choose a different, separate destination folder")
                files = await self.read("files", {"hash": identifier})
                if not isinstance(files, list) or not 1 <= len(files) <= 20000:
                    raise HTTPException(409, "Torrent file list is unavailable or too large")
                manifest = []
                for file in files:
                    name, size = file.get("name"), file.get("size")
                    if not isinstance(name, str) or type(size) is not int or size < 0 or file.get("progress") != 1:
                        raise HTTPException(409, "Every torrent file must be downloaded before moving")
                    src, dst = file_path(origin, name), file_path(target, name)
                    # Never merge into an existing release folder, even if its names differ.
                    top = target / PurePosixPath(name).parts[0]
                    source_top = origin / PurePosixPath(name).parts[0]
                    if target == source_top or target.is_relative_to(source_top):
                        raise HTTPException(409, "The destination is inside this torrent's source files")
                    if top_owners.get(str(top), identifier) != identifier:
                        raise HTTPException(409, "Selected torrents have conflicting destination folders")
                    top_owners[str(top)] = identifier
                    if dst.exists() or top.exists() or str(dst) in reserved:
                        raise HTTPException(409, "The destination already contains a file or folder with this name")
                    info = src.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                        raise HTTPException(409, "Source files are missing or incomplete")
                    reserved.add(str(dst))
                    manifest.append({"name": name, "size": size})
                items.append({"hash": identifier, "name": str(row.get("name", ""))[:500], "added": row.get("added_on"),
                              "source": source, "paused": row["state"].startswith(("stopped", "paused")), "phase": "queued", "files": manifest, "verifiedFiles": 0})
            if sum(len(row["files"]) for row in items) > 20000:
                raise HTTPException(422, "Choose at most 20000 files per move")
            required = sum(f["size"] for row in items for f in row["files"] if self.root(row["source"]).stat().st_dev != target.stat().st_dev)
            import shutil
            if shutil.disk_usage(target).free < required:
                raise HTTPException(409, "The destination does not have enough free space")
            job = {"id": uuid.uuid4().hex, "created": time.time(), "status": "queued", "destination": destination,
                   "location": choice.location, "folder": choice.folder, "items": items, "error": None}
            self.save(job)
            return self.public(job)

    def public(self, job):
        return {key: job[key] for key in ("id", "created", "status", "location", "folder", "error")} | {
            "items": [{"hash": row["hash"], "name": row["name"], "phase": row["phase"], "fileCount": len(row["files"]),
                       "verifiedFiles": row["verifiedFiles"], "bytes": sum(f["size"] for f in row["files"]),
                       "checkedBytes": self.progress.get((job["id"], row["hash"]), 0)} for row in job["items"]]}

    async def process(self, job):
        target = self.root(job["destination"])
        for row in job["items"]:
            if row["phase"] == "complete":
                continue
            observed = await self.torrent(row["hash"])
            if observed.get("added_on") != row["added"] or observed.get("progress") != 1:
                raise HTTPException(409, "Torrent identity or completion changed; files were preserved")
            current = str(observed.get("save_path", "")).rstrip("/")
            if current not in {row["source"], job["destination"]}:
                raise HTTPException(409, "Torrent location changed outside this move")
            if row["phase"] in {"queued", "hashing"}:
                origin = self.root(row["source"])
                row["phase"] = job["status"] = "hashing"
                self.save(job)
                self.progress[(job["id"], row["hash"])] = sum(f["size"] for f in row["files"] if "sha256" in f)
                def progress(count, key=(job["id"], row["hash"])):
                    self.progress[key] = self.progress.get(key, 0) + count
                for file in row["files"]:
                    if "sha256" not in file:
                        file["sha256"] = await asyncio.to_thread(digest, file_path(origin, file["name"]), file["size"], progress)
                        self.save(job)
                # Recheck collisions just before asking the client to change files.
                if any((target / PurePosixPath(f["name"]).parts[0]).exists() for f in row["files"]):
                    raise HTTPException(409, "A destination file appeared; no files were overwritten")
                row["phase"] = job["status"] = "moving"
                row["moveStarted"] = time.time()
                self.save(job)  # Persist intent before an ambiguous RPC timeout.
                result = await self.qbit("/api/v2/torrents/setLocation", "POST", {"hashes": row["hash"], "location": job["destination"]})
                if result.status_code not in {200, 204}:
                    raise HTTPException(503, "The torrent client did not accept the move")
                row["moveAccepted"] = True
                self.save(job)
            if row["phase"] == "moving":
                observed = await self.torrent(row["hash"])
                current = str(observed.get("save_path", "")).rstrip("/")
                # An interrupted RPC may never have reached qBittorrent. Retry only
                # if the original location is unchanged and no destination exists.
                if (not row.get("moveAccepted") and current == row["source"]
                        and observed.get("state") != "moving"
                        and not any((target / PurePosixPath(f["name"]).parts[0]).exists() for f in row["files"])):
                    result = await self.qbit("/api/v2/torrents/setLocation", "POST", {"hashes": row["hash"], "location": job["destination"]})
                    if result.status_code not in {200, 204}:
                        raise HTTPException(503, "The torrent client did not accept the move")
                    row["moveAccepted"] = True
                    self.save(job)
                while True:
                    observed = await self.torrent(row["hash"])
                    if observed.get("added_on") != row["added"]:
                        raise HTTPException(409, "Torrent identity changed during the move")
                    current = str(observed.get("save_path", "")).rstrip("/")
                    if current == job["destination"] and observed.get("state") != "moving":
                        break
                    if observed.get("state") in {"error", "missingFiles"} or time.time() - row["moveStarted"] > 7200:
                        raise HTTPException(409, "The torrent client could not finish moving the files")
                    if current not in {row["source"], job["destination"]}:
                        raise HTTPException(409, "Torrent location changed during the move")
                    await asyncio.sleep(2)
                row["phase"] = job["status"] = "verifying"
                self.save(job)
            self.progress[(job["id"], row["hash"])] = 0
            row["verifiedFiles"] = 0
            def progress(count, key=(job["id"], row["hash"])):
                self.progress[key] = self.progress.get(key, 0) + count
            for file in row["files"]:
                checksum = await asyncio.to_thread(digest, file_path(target, file["name"]), file["size"], progress)
                if checksum != file["sha256"]:
                    raise HTTPException(409, "Destination content verification failed; review the preserved files")
                row["verifiedFiles"] += 1
                self.save(job)
            observed = await self.torrent(row["hash"])
            if (str(observed.get("save_path", "")).rstrip("/") != job["destination"] or observed.get("progress") != 1
                    or observed.get("state") in {"moving", "error", "missingFiles"} or observed.get("added_on") != row["added"]):
                raise HTTPException(409, "Final torrent location or completion could not be confirmed")
            if observed.get("state", "").startswith(("stopped", "paused")) != row["paused"]:
                raise HTTPException(409, "Files are verified, but the torrent's seeding or pause status changed")
            original = self.storage.downloads / PurePosixPath(row["source"]).relative_to("/downloads")
            if any(file_path(original, f["name"]).exists() for f in row["files"]):
                raise HTTPException(409, "Destination files are verified, but source copies remain; review the move")
            row["phase"] = "complete"
            self.save(job)
        job.update(status="complete", error=None)
        self.save(job)
        for row in job["items"]:
            self.progress.pop((job["id"], row["hash"]), None)

    async def tick(self):
        async with self.lock:
            job = next((j for j in reversed(self.records()) if j["status"] not in {"complete", "needs_attention"}), None)
            if job is None:
                return
            token = INTERNAL.set(True)
            try:
                await self.capable()
                await self.process(job)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - persist recovery state without exposing transport errors
                job.update(status="needs_attention", error=error.detail if isinstance(error, HTTPException) else "Move could not be verified. Check storage and the torrent client; files were preserved.")
                self.save(job)
            finally:
                INTERNAL.reset(token)

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(2)


def install_moves(app, authorized, database, qbit, storage, cleanup, credit, capable):
    manager = Moves(database, qbit, storage, cleanup, credit, capable)
    app.state.moves = manager
    dependency = Depends(authorized)

    @app.post("/api/torrent-moves", status_code=202)
    async def create(body: MoveChoice, session=dependency):
        try:
            return await manager.create(body)
        except OSError:
            raise HTTPException(409, "Source or destination storage is unavailable") from None

    @app.get("/api/torrent-moves")
    async def list_moves(session=dependency):
        return [manager.public(job) for job in manager.records()]

    @app.get("/api/torrent-moves/{identifier}")
    async def get(identifier: str, session=dependency):
        return manager.public(manager.get(identifier))

    @app.post("/api/torrent-moves/{identifier}/retry", status_code=202)
    async def retry(identifier: str, session=dependency):
        async with manager.lock:
            job = manager.get(identifier)
            if job["status"] == "needs_attention":
                job.update(status="queued", error=None)
                manager.save(job)
            return manager.public(job)

    return manager
