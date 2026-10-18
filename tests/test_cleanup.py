import asyncio
import hashlib
import sqlite3
from contextlib import contextmanager

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_app import sign_in

from mediaseed.app import create_app
from mediaseed.cleanup import CleanupManager, CleanupPolicy, reached, source_hashes
from mediaseed.features import add_stopped

HASH = "a" * 40
MAGNET = "magnet:?xt=urn:btih:" + HASH


@pytest.mark.parametrize("mode,seconds,ratio,expected", [
    ("never", 200000, 5, False), ("time", 3599, 5, False), ("time", 3600, 0, True),
    ("ratio", 0, 2, True), ("ratio", 100000, 1.9, False),
    ("both", 3600, 1.9, False), ("both", 3599, 2, False), ("both", 3600, 2, True),
    ("either", 3600, 0, True), ("either", 0, 2, True), ("either", 3599, 1.9, False),
])
def test_cleanup_thresholds(mode, seconds, ratio, expected):
    rule = CleanupPolicy(mode=mode, hours=1, ratio=2).model_dump()
    row = {"progress": 1, "state": "stalledUP", "seeding_time": seconds, "ratio": ratio, "num_leechs": 0, "upspeed": 0}
    assert reached(rule, row) is expected
    for patch in ({"progress": 0.99}, {"num_leechs": 1}, {"num_leechs": None}, {"upspeed": 1}, {"upspeed": None}, {"state": "missingFiles"}, {"seeding_time": float("nan")}, {"ratio": float("inf")}):
        assert not reached(rule, {**row, **patch})


def test_policy_validation_and_exact_info_hash():
    info = b"d6:lengthi1e4:name4:test12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaae"
    assert source_hashes(content=b"d4:info" + info + b"e") == [hashlib.sha1(info).hexdigest()]
    assert source_hashes(magnet=MAGNET) == [HASH]
    with pytest.raises(HTTPException):
        source_hashes(content=b"dee")
    for body in ({"mode": "anything"}, {"hours": 0}, {"ratio": float("nan")}, {"deleteEverything": True}):
        with pytest.raises(ValidationError):
            CleanupPolicy(**body)


def manager(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "cleanup.sqlite") as db:
            yield db

    state = {"rows": [], "ready": True, "pending": False}
    calls = []

    async def qbit(path, method="GET", payload=None, files=None):
        calls.append((path, method, payload, files))
        if path.endswith("/info"):
            return httpx.Response(200, json=state["rows"])
        if path.endswith("/add"):
            state["row"] = {"hash": HASH, "tags": payload["tags"], "state": "stalledUP", "progress": 1, "seeding_time": 0, "ratio": 0, "num_leechs": 0, "upspeed": 0}
            if not state["pending"]:
                state["rows"] = [state["row"]]
        return httpx.Response(204)

    async def vpn():
        return {"downloadsEnabled": state["ready"]}

    return CleanupManager(database, qbit, vpn), state, calls


@pytest.mark.parametrize("delete_files", [False, True])
def test_new_import_is_tagged_paused_then_armed_and_only_exact_torrent_is_removed(tmp_path, delete_files):
    async def scenario():
        m, state, calls = manager(tmp_path)
        await m.add(add_stopped, CleanupPolicy(mode="both", hours=1, ratio=2, delete_files=delete_files), 1, magnet=MAGNET, start=True)
        add = next(c for c in calls if c[0].endswith("/add"))
        assert add[2]["paused"] == "true" and add[2]["tags"].startswith("MediaSeed-Cleanup-")
        limits = next(c for c in calls if c[0].endswith("/setShareLimits"))
        assert limits[2]["ratioLimit"] == limits[2]["seedingTimeLimit"] == "-1"
        assert any(c[0].endswith("/start") for c in calls)
        state["rows"][0].update(seeding_time=3600, ratio=1.9)
        await m.tick(); assert not any(c[0].endswith("/delete") for c in calls)
        # Restart preserves the rule; elapsed wall time is irrelevant.
        restarted = CleanupManager(m.database, m.qbit, m.vpn_status)
        state["rows"][0]["ratio"] = 2
        await restarted.tick()
        deleted = [c for c in calls if c[0].endswith("/delete")]
        assert len(deleted) == 1 and deleted[0][2] == {"hashes": HASH, "deleteFiles": str(delete_files).lower()}
        assert restarted.public(HASH)["mode"] == "never"
        await restarted.tick(); assert len([c for c in calls if c[0].endswith("/delete")]) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["ratio", "never"])
def test_pending_metadata_and_vpn_loss_do_not_start_or_delete_unconfigured_import(tmp_path, mode):
    async def scenario():
        m, state, calls = manager(tmp_path); state["pending"] = True
        await m.add(add_stopped, CleanupPolicy(mode=mode, ratio=2), 1, magnet=MAGNET, start=True)
        assert m.public(HASH)["status"] == "waiting"
        assert not any(c[0].endswith(("/start", "/delete")) for c in calls)
        state["rows"] = [state["row"]]; state["ready"] = False
        await m.tick(); assert not any(c[0].endswith(("/start", "/delete")) for c in calls)
        state["ready"] = True; await m.tick()
        assert len([c for c in calls if c[0].endswith("/start")]) == 1
    asyncio.run(scenario())


def test_existing_or_replaced_torrent_never_inherits_destructive_rule(tmp_path):
    async def scenario():
        m, state, calls = manager(tmp_path)
        state["rows"] = [{"hash": HASH, "tags": ""}]
        with pytest.raises(HTTPException):
            await m.add(add_stopped, CleanupPolicy(mode="time", delete_files=True), 1, magnet=MAGNET)
        assert not any(c[0].endswith("/add") for c in calls)
        state["rows"] = []
        await m.add(add_stopped, CleanupPolicy(mode="time", delete_files=True), 1, magnet=MAGNET)
        state["rows"][0].update(tags="", ratio=99, seeding_time=999999)
        await m.tick()
        assert m.public(HASH)["status"] == "identity_mismatch"
        assert not any(c[0].endswith("/delete") for c in calls)
        await m.cancel(HASH); assert m.public(HASH)["mode"] == "never"
    asyncio.run(scenario())


def test_read_failure_and_cancellation_with_client_offline_preserve_files(tmp_path):
    async def scenario():
        m, state, calls = manager(tmp_path)
        state["ready"] = False
        with pytest.raises(HTTPException):
            await m.add(add_stopped, CleanupPolicy(mode="time"), 1, magnet=MAGNET, start=True)
        assert not calls
        state["ready"] = True
        await m.add(add_stopped, CleanupPolicy(mode="either", delete_files=True), 1, magnet=MAGNET)
        state["rows"][0].update(seeding_time=999999, ratio=99)

        async def offline(*args, **kwargs):
            raise HTTPException(503, "synthetic offline")

        m.qbit = offline
        with pytest.raises(HTTPException):
            await m.tick()
        assert m.public(HASH)["observationFailed"] is True
        assert not any(c[0].endswith("/delete") for c in calls)
        assert (await m.cancel(HASH))["stopLimitsRestored"] is False
        assert m.public(HASH)["mode"] == "never"
    asyncio.run(scenario())


def test_add_api_accepts_cleanup_for_files_and_magnets_and_requires_auth(tmp_path):
    rows, calls = [], []
    info = b"d6:lengthi1e4:name4:test12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaae"
    digest = hashlib.sha1(info).hexdigest()

    def handler(request):
        from urllib.parse import parse_qs
        if request.url.path.endswith("/info"):
            wanted = request.url.params.get("hashes", "").split("|")
            return httpx.Response(200, json=[r for r in rows if r["hash"] in wanted])
        if request.url.path.endswith("/add"):
            if "multipart" in request.headers.get("content-type", ""):
                import re
                tag = re.search(rb'MediaSeed-Cleanup-[a-f0-9]{24}', request.content).group().decode()
                identifier = digest
            else:
                tag = parse_qs(request.content.decode())["tags"][0]; identifier = HASH
            rows.append({"hash": identifier, "tags": tag, "state": "stoppedDL", "progress": 0})
        calls.append(request)
        return httpx.Response(204)

    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(handler))) as client:
        assert client.post("/api/torrents", json={"url": MAGNET}).status_code == 401
        sign_in(client)
        response = client.post("/api/torrents", json={"url": MAGNET, "cleanup": {"mode": "both", "hours": 3, "ratio": 2, "delete_files": True}})
        assert response.status_code == 200 and response.json()["cleanup"]["mode"] == "both"
        response = client.post("/api/torrents/upload?filename=test.torrent&cleanup_mode=time&cleanup_hours=2", content=b"d4:info" + info + b"e", headers={"Content-Type": "application/x-bittorrent"})
        assert response.status_code == 200 and response.json()["cleanup"]["delete_files"] is False
        assert client.post("/api/torrents", json={"url": MAGNET, "cleanup": {"mode": "typo"}}).status_code == 422
        assert client.post(f"/api/torrents/{HASH}/cleanup/cancel", json={}).status_code == 200


def test_keep_forever_is_unlimited_before_start_and_after_restart(tmp_path):
    async def scenario():
        m, state, calls = manager(tmp_path)
        result = await m.add(add_stopped, CleanupPolicy(), 2, magnet=MAGNET, start=True)
        assert not result['stopped'] and m.public(HASH)['mode'] == 'never'
        paths = [path for path, *_ in calls]
        assert paths.index('/api/v2/torrents/setShareLimits') < paths.index('/api/v2/torrents/start')
        state['rows'][0].update(seeding_time=200 * 3600, ratio=100)
        restarted = CleanupManager(m.database, m.qbit, m.vpn_status)
        await restarted.tick()
        assert not any(path.endswith(('/stop', '/delete')) for path, *_ in calls)
        assert not restarted.records(), 'Retained torrents do not consume cleanup capacity'
    asyncio.run(scenario())
