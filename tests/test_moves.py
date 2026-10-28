import asyncio
import json
import shutil
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_app import sign_in

from mediaseed.app import create_app
from mediaseed.moves import INTERNAL, MoveChoice, Moves
from mediaseed.storage import Storage


@pytest.fixture
def rig(tmp_path):
    downloads = tmp_path / "downloads"
    source, target = downloads / "media/downloads", downloads / "media/movies"
    source.mkdir(parents=True)
    target.mkdir()
    storage = Storage(downloads, json.dumps([
        {"id": "downloads", "label": "Downloads", "path": "/downloads/media/downloads"},
        {"id": "movies", "label": "Film", "path": "/downloads/media/movies"}]))
    rows, manifests, calls = {}, {}, []
    for index, name in [("a", "First.Movie"), ("b", "Second.Movie")]:
        identifier = index * 40
        (source / name).mkdir()
        (source / name / "movie.mkv").write_bytes(index.encode() * 160)
        (source / name / "subtitle.srt").write_bytes(b"subtitle")
        rows[identifier] = {"hash": identifier, "name": name, "progress": 1,
            "state": "stoppedUP" if index == "b" else "stalledUP", "added_on": 123,
            "save_path": "/downloads/media/downloads", "auto_tmm": False}
        manifests[identifier] = [{"name": name + "/movie.mkv", "size": 160, "progress": 1},
            {"name": name + "/subtitle.srt", "size": 8, "progress": 1}]

    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "state.sqlite") as db:
            yield db

    async def qbit(path, method="GET", payload=None):
        payload = payload or {}
        identifier = payload.get("hashes", payload.get("hash"))
        calls.append((path, method, dict(payload)))
        if path.endswith("/info"):
            data = [dict(rows[identifier])] if identifier in rows else []
        elif path.endswith("/files"):
            data = manifests[identifier]
        elif path.endswith("/setLocation"):
            for name in {f["name"].split("/")[0] for f in manifests[identifier]}:
                shutil.move(str(source / name), str(target / name))
            rows[identifier]["save_path"] = payload["location"]
            data = None
        else:
            data = None
        return httpx.Response(200, json=data)

    async def capable():
        pass

    cleanup, credit = SimpleNamespace(lock=asyncio.Lock()), SimpleNamespace(lock=asyncio.Lock())
    manager = Moves(database, qbit, storage, cleanup, credit, capable)
    return SimpleNamespace(manager=manager, rows=rows, files=manifests, calls=calls,
        source=source, target=target, database=database, qbit=qbit, storage=storage,
        cleanup=cleanup, credit=credit, capable=capable)


def create(rig, **kwargs):
    choice = MoveChoice(hashes=kwargs.pop("hashes", list(rig.rows)), location="movies", **kwargs)
    return asyncio.run(rig.manager.create(choice))


def test_batch_native_move_verifies_content_and_preserves_pause_and_unrelated_files(rig):
    (rig.source / "keep.txt").write_text("leave me")
    before = {identifier: row["state"] for identifier, row in rig.rows.items()}
    job = create(rig)
    assert job["status"] == "queued"
    assert not any(path.endswith("setLocation") for path, *_ in rig.calls)
    asyncio.run(rig.manager.tick())
    observed = rig.manager.get(job["id"])
    assert observed["status"] == "complete"
    assert [row["verifiedFiles"] for row in observed["items"]] == [2, 2]
    assert {identifier: row["state"] for identifier, row in rig.rows.items()} == before
    assert (rig.source / "keep.txt").read_text() == "leave me"
    assert not (rig.source / "First.Movie").exists()
    assert (rig.target / "Second.Movie/subtitle.srt").read_bytes() == b"subtitle"
    assert len([call for call in rig.calls if call[0].endswith("setLocation")]) == 2
    assert all(call[1] == "GET" or call[0].endswith("setLocation") for call in rig.calls)


@pytest.mark.parametrize("change", ["collision", "incomplete", "symlink", "traversal", "duplicate", "auto", "wrong_size"])
def test_preflight_refuses_unsafe_or_incomplete_batches_without_moving_any_file(rig, change):
    identifiers = list(rig.rows)
    if change == "collision":
        (rig.target / "Second.Movie").mkdir()
    elif change == "incomplete":
        rig.rows[identifiers[1]]["progress"] = .9
    elif change == "symlink":
        p = rig.source / "First.Movie/movie.mkv"
        p.unlink()
        p.symlink_to(rig.source / "First.Movie/subtitle.srt")
    elif change == "traversal":
        rig.files[identifiers[0]][0]["name"] = "../outside"
    elif change == "duplicate":
        identifiers *= 2
    elif change == "auto":
        rig.rows[identifiers[1]]["auto_tmm"] = True
    elif change == "wrong_size":
        rig.files[identifiers[1]][0]["size"] += 1
    with pytest.raises(HTTPException):
        create(rig, hashes=identifiers)
    assert not any(path.endswith("setLocation") for path, *_ in rig.calls)


def test_same_size_corruption_is_detected_and_never_reported_complete(rig):
    job = create(rig, hashes=["a" * 40])
    original = rig.manager.qbit

    async def corrupt(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[0].endswith("setLocation"):
            (rig.target / "First.Movie/movie.mkv").write_bytes(b"x" * 160)
        return result

    rig.manager.qbit = corrupt
    asyncio.run(rig.manager.tick())
    observed = rig.manager.get(job["id"])
    assert observed["status"] == "needs_attention"
    assert "verification failed" in observed["error"]
    assert (rig.target / "First.Movie/movie.mkv").exists()
    assert observed["items"][0]["phase"] != "complete"


def test_restart_recovers_accepted_move_without_issuing_it_twice(rig):
    result = create(rig, hashes=["a" * 40])
    original = rig.manager.qbit

    async def crash(*args, **kwargs):
        result = await original(*args, **kwargs)
        if args[0].endswith("setLocation"):
            raise asyncio.CancelledError()
        return result

    rig.manager.qbit = crash
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rig.manager.tick())
    reopened = Moves(rig.database, rig.qbit, rig.storage, rig.cleanup, rig.credit, rig.capable)
    asyncio.run(reopened.tick())
    assert reopened.get(result["id"])["status"] == "complete"
    assert len([call for call in rig.calls if call[0].endswith("setLocation")]) == 1


def test_queued_move_blocks_cleanup_deletion_and_user_controls(rig):
    create(rig, hashes=["a" * 40])
    for operation in ["delete", "stop", "start", "setFilePriority"]:
        with pytest.raises(HTTPException):
            rig.manager.guard("/api/v2/torrents/" + operation, {"hashes": "a" * 40})
    rig.manager.guard("/api/v2/torrents/delete", {"hashes": "b" * 40})
    token = INTERNAL.set(True)
    try:
        rig.manager.guard("/api/v2/torrents/setLocation", {"hashes": "a" * 40})
    finally:
        INTERNAL.reset(token)


def test_move_endpoints_require_login_and_csrf_and_migrate_existing_database(tmp_path):
    app = create_app(tmp_path, "http://testserver")
    client = TestClient(app)
    body = {"hashes": ["a" * 40], "location": "local"}
    assert client.get("/api/torrent-moves").status_code == 401
    assert client.post("/api/torrent-moves", json=body).status_code == 401
    sign_in(client)
    client.headers["X-MediaSeed-CSRF"] = "invalid"
    assert client.post("/api/torrent-moves", json=body).status_code == 403
    assert client.get("/api/torrent-moves").json() == []
    reopened = create_app(tmp_path, "http://testserver")
    assert reopened.state.moves.records() == []
