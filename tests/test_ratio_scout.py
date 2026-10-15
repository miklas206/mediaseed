import asyncio
import copy
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_app import sign_in

from mediaseed.app import create_app
from mediaseed.cleanup import source_hashes
from mediaseed.features import add_stopped
from mediaseed.ratio_scout import RatioScout, RatioSettings, candidate

GIB = 1024**3
CONTENT = b"d4:infod6:lengthi1073741824e4:name4:testee"
HASH = source_hashes(content=CONTENT)[0]


@pytest.fixture
def rig(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "db") as c:
            yield c

    state = SimpleNamespace(
        ready=True,
        free=100 * GIB,
        uploaded=100 * GIB,
        downloaded=10 * GIB,
        existing=[],
        calls=[],
        reads=[],
        content=CONTENT,
        fail=False,
        lose_vpn=False,
    )
    source = {
        "id": "12",
        "attributes": {
            "name": "Synthetic release",
            "info_hash": HASH,
            "seeders": 1,
            "leechers": 21,
            "size": GIB,
            "freeleech": 100,
            "double_upload": True,
            "created_at": datetime.fromtimestamp(time.time() - 172800, UTC).isoformat(),
            "download_link": "https://nordicbytes.org/torrents/download/12/synthetic-private-key",
        },
    }
    account = {
        "id": "a" * 24,
        "api_key": "synthetic-secret",
        "name": "Synthetic tracker",
    }

    async def read(account, path, params=None):
        state.reads.append((path, params))
        scout.next_request = 0  # Timing is tested separately; these test policy.
        if path.endswith("/filter"):
            return {"data": [copy.deepcopy(source)]}
        if path == "/api/user":
            return {
                "stats": {"uploaded": state.uploaded, "downloaded": state.downloaded}
            }
        return {"data": copy.deepcopy(source)}

    async def qbit(path, method="GET", payload=None, files=None):
        state.calls.append((path, method, payload))
        if path.endswith("rss/torrent"):
            return httpx.Response(200, content=state.content)
        if path.endswith("torrents/info"):
            if payload.get("hashes"):
                return httpx.Response(
                    200,
                    json=[
                        r
                        for r in state.existing
                        if r["hash"] in payload["hashes"].split("|")
                    ],
                )
            return httpx.Response(200, json=state.existing)
        if path.endswith("torrents/add"):
            if state.fail:
                raise HTTPException(503, "Synthetic uncertain write")
            assert (
                payload["stopped"] == "true" and payload["seedingTimeLimit"] == "2940"
            )
            assert payload["ratioLimit"] == "1"
            state.existing.append(
                {
                    "hash": HASH,
                    "progress": 0,
                    "amount_left": GIB,
                    "state": "stoppedDL",
                    "save_path": payload["savepath"],
                }
            )
            if state.lose_vpn:
                state.ready = False
            return httpx.Response(200, text="Ok.")
        return httpx.Response(204)

    async def vpn():
        return {"downloadsEnabled": state.ready}

    storage = SimpleNamespace(
        destination=lambda location, folder: (
            "/downloads" + ("/" + folder if folder else "")
        ),
        snapshot=lambda: {
            "locations": [{"id": "local", "freeBytes": state.free, "writable": True}]
        },
    )
    scout = RatioScout(
        database, lambda kind: [account], read, qbit, vpn, storage, add_stopped
    )
    settings = RatioSettings(enabled=True, tracker=account["id"])
    return scout, settings, source, state, database


def test_fresh_disabled_upgrade_persistence_and_private_data(rig):
    scout, settings, _source, state, database = rig
    assert not scout.settings().enabled and not scout.settings().automatic

    async def check():
        await scout.save(settings)
        await scout.tick()
        public = scout.public()
        assert len(public["candidates"]) == 1
        assert not any(method == "POST" for _, method, _ in state.calls)
        assert "synthetic-private-key" not in json.dumps(public)
        assert "synthetic-secret" not in json.dumps(public)
        assert public["candidates"][0]["seeders"] == 1
        assert state.reads == [
            (
                "/api/torrents/filter",
                {
                    "name": "",
                    "perPage": 10,
                    "free[]": 100,
                    "sortField": "leechers",
                    "sortDirection": "desc",
                },
            )
        ]
        await scout.tick()
        assert len(state.reads) == 1
        restarted = RatioScout(
            database,
            scout.accounts,
            scout.tracker_read,
            scout.qbit,
            scout.vpn_status,
            scout.storage,
            add_stopped,
        )
        assert restarted.settings() == settings
        assert len(restarted.public()["candidates"]) == 1

    asyncio.run(check())


@pytest.mark.parametrize(
    "field,value",
    [
        ("seeders", 0),
        ("seeders", 3),
        ("seeders", None),
        ("seeders", True),
        ("leechers", 19),
        ("leechers", None),
        ("freeleech", 50),
        ("freeleech", None),
        ("size", 30 * GIB),
        ("created_at", None),
        ("created_at", "invalid"),
        ("created_at", datetime.now(UTC).isoformat()),
        ("info_hash", "bad"),
    ],
)
def test_unknown_or_nonmatching_data_is_not_a_candidate(rig, field, value):
    _, settings, source, _, _ = rig
    source["attributes"][field] = value
    assert candidate(source, settings, time.time()) is None


def test_manual_approval_rechecks_and_never_repeats_or_deletes(rig):
    scout, settings, _, state, database = rig

    async def check():
        await scout.save(settings)
        await scout.tick()
        key = scout.public()["candidates"][0]["candidateId"]
        result = await scout.add(key)
        assert result["startRequested"]
        assert state.reads[-2][0] == "/api/torrents/12"
        assert state.reads[-1][0] == "/api/user"
        with pytest.raises(HTTPException):
            await scout.add(key)
        assert sum(path.endswith("/add") for path, _, _ in state.calls) == 1
        assert sum(path.endswith("/start") for path, _, _ in state.calls) == 1
        assert not any(path.endswith("/delete") for path, _, _ in state.calls)
        with database() as c:
            assert (
                c.execute("SELECT status FROM ratio_imports").fetchone()[0] == "started"
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "reason",
    ["benefit", "vpn", "space", "ratio", "metadata", "daily", "existing", "remaining"],
)
def test_approval_blocks_changed_or_unsafe_conditions_without_import(rig, reason):
    scout, settings, source, state, database = rig

    async def check():
        await scout.save(settings)
        await scout.tick()
        key = scout.public()["candidates"][0]["candidateId"]
        if reason == "benefit":
            source["attributes"]["freeleech"] = 0
        if reason == "vpn":
            state.ready = False
        if reason == "space":
            state.free = 20 * GIB
        if reason == "ratio":
            state.uploaded = GIB
        if reason == "metadata":
            state.content = CONTENT.replace(b"4:test", b"4:diff")
        if reason == "remaining":
            state.existing = [{"hash": "b" * 40, "progress": 0.5}]
        if reason == "existing":
            state.existing = [{"hash": HASH, "progress": 1}]
        if reason == "daily":
            with database() as c:
                for i in range(settings.dailyLimit):
                    c.execute(
                        "INSERT INTO ratio_imports VALUES (?,?,?,?,?)",
                        (str(i), time.time(), GIB, "local", "started"),
                    )
        with pytest.raises(HTTPException):
            await scout.add(key)
        assert not any(method == "POST" for _, method, _ in state.calls)

    asyncio.run(check())


@pytest.mark.parametrize("uncertain", [True, False])
def test_uncertain_import_or_vpn_loss_never_retries_or_starts(rig, uncertain):
    scout, settings, _, state, _database = rig

    async def check():
        await scout.save(settings)
        await scout.tick()
        key = scout.public()["candidates"][0]["candidateId"]
        state.fail, state.lose_vpn = uncertain, not uncertain
        with pytest.raises(HTTPException):
            await scout.add(key)
        state.ready = True
        with pytest.raises(HTTPException):
            await scout.add(key)
        assert sum(path.endswith("/add") for path, _, _ in state.calls) == 1
        assert not any(path.endswith("/start") for path, _, _ in state.calls)

    asyncio.run(check())


def test_automatic_mode_is_explicit_and_survives_restart_with_dedup(rig):
    scout, settings, _, state, _database = rig

    async def check():
        await scout.save(settings.model_copy(update={"automatic": True}))
        await scout.tick()
        assert sum(path.endswith("/start") for path, _, _ in state.calls) == 1
        scout.next_scan = 0
        await scout.tick()
        assert sum(path.endswith("/add") for path, _, _ in state.calls) == 1

    asyncio.run(check())


@pytest.mark.parametrize(
    "changes",
    [
        {"minSeeders": 0},
        {"minSeeders": 3, "maxSeeders": 2},
        {"dailyLimit": True},
        {"enabled": True},
        {"folder": "../escape"},
        {"unrelated": True},
    ],
)
def test_strict_settings(changes):
    with pytest.raises(ValidationError):
        RatioSettings(**changes)


def test_real_credit_controller_keeps_seeding_protection_without_deadlock(
    rig, tmp_path
):
    from mediaseed.tracker_credit import TrackerCredit

    scout, settings, _, state, database = rig
    raw = scout.qbit
    with database() as c:
        c.execute("CREATE TABLE settings (kind TEXT)")
        c.execute("INSERT INTO settings VALUES ('trackers')")

    async def transport(path, method="GET", payload=None, files=None):
        if path.endswith("/torrents/add"):
            assert float(payload["ratioLimit"]) == 2
            payload = {**payload, "ratioLimit": "1"}
        return await raw(path, method, payload, files)

    credit = TrackerCredit(database, tmp_path, transport)
    scout.action_lock, scout.write_qbit = credit.lock, credit._request

    async def check():
        await scout.save(settings)
        await scout.tick()
        key = scout.public()["candidates"][0]["candidateId"]
        await asyncio.wait_for(scout.add(key), timeout=3)
        limits = [
            p for path, method, p in state.calls if path.endswith("/setShareLimits")
        ]
        assert limits and limits[-1]["seedingTimeLimit"] == "2940"
        assert limits[-1]["shareLimitAction"] == "Stop"

    asyncio.run(check())


def test_rate_limit_cools_down_without_hanging_manual_requests(rig):
    scout, settings, _, state, _ = rig

    async def limited(*args):
        raise HTTPException(503, "Tracker rate limit reached; try again later")

    async def check():
        await scout.save(settings)
        scout.tracker_read = limited
        await scout.tick()
        assert scout.next_scan > time.monotonic() + 890
        with pytest.raises(HTTPException):
            await asyncio.wait_for(scout.read({}, "/api/user"), timeout=0.1)
        assert not state.calls

    asyncio.run(check())


def test_stale_candidates_are_labeled_and_changed_rules_invalidate_them(rig):
    scout, settings, _, _, database = rig

    async def check():
        await scout.save(settings)
        await scout.tick()
        row = scout.public()["candidates"][0]
        row["checkedAt"] -= 1201
        with database() as c:
            c.execute("UPDATE ratio_candidates SET payload=?", (json.dumps(row),))
        assert scout.public()["candidates"][0]["stale"]
        await scout.save(settings.model_copy(update={"maxSeeders": 5}))
        assert scout.public()["candidates"] == []
        with pytest.raises(HTTPException):
            await scout.add(row["candidateId"])

    asyncio.run(check())


def test_failed_start_response_keeps_uncertain_intent_and_blocks_retry(rig):
    scout, settings, _, state, database = rig
    raw = scout.qbit

    async def rejected(path, method="GET", payload=None, files=None):
        if path.endswith("/start"):
            return httpx.Response(503)
        return await raw(path, method, payload, files)

    scout.write_qbit = rejected

    async def check():
        await scout.save(settings)
        await scout.tick()
        key = scout.public()["candidates"][0]["candidateId"]
        with pytest.raises(HTTPException):
            await scout.add(key)
        with database() as c:
            assert (
                c.execute("SELECT status FROM ratio_imports").fetchone()[0] == "intent"
            )
        with pytest.raises(HTTPException):
            await scout.add(key)
        assert sum(path.endswith("/add") for path, _, _ in state.calls) == 1

    asyncio.run(check())


def test_saving_rules_cannot_force_more_frequent_tracker_scans(rig):
    scout, settings, _, state, _ = rig

    async def check():
        await scout.save(settings)
        await scout.tick()
        await scout.save(settings.model_copy(update={"maxSeeders": 4}))
        await scout.tick()
        assert len(state.reads) == 1

    asyncio.run(check())


def test_ratio_category_is_separate_in_queue_counts():
    from mediaseed.features import queue_summary

    result = queue_summary(
        [
            {"state": "downloading", "category": "MediaSeed-Ratio"},
            {"state": "downloading", "category": ""},
        ],
        8,
    )
    assert result["ratioActive"] == 1 and result["manualActive"] == 1


def test_api_is_authenticated_csrf_guarded_and_additive_for_old_database(tmp_path):
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        assert client.get("/api/ratio-scout").status_code == 401
        assert client.put("/api/ratio-scout/settings", json={}).status_code == 401
        sign_in(client)
        assert (
            client.post(
                "/api/trackers",
                json={"name": "Synthetic", "api_key": "preserved-secret"},
            ).status_code
            == 200
        )
        assert client.get("/api/ratio-scout").json()["settings"]["enabled"] is False
        assert (
            client.put("/api/ratio-scout/settings", json={"dailyLimit": 5}).status_code
            == 200
        )
        cookies = client.cookies
        client.headers["X-MediaSeed-CSRF"] = "wrong"
        assert client.put("/api/ratio-scout/settings", json={}).status_code == 403
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        client.cookies.update(cookies)
        assert client.get("/api/ratio-scout").json()["settings"]["dailyLimit"] == 5
    with sqlite3.connect(tmp_path / "mediaseed.sqlite") as c:
        assert (
            "preserved-secret"
            in c.execute(
                "SELECT payload FROM settings WHERE kind='trackers'"
            ).fetchone()[0]
        )


def test_small_scan_persists_deadline_and_adapts_without_immediate_retries(rig):
    scout, settings, source, state, database = rig
    attempts = []

    async def sized(account, path, params=None):
        attempts.append(params["perPage"])
        if params["perPage"] > 1:
            raise HTTPException(502, "Tracker response is too large")
        return {"data": [copy.deepcopy(source)]}

    async def check():
        await scout.save(settings)
        scout.tracker_read = sized
        await scout.tick()
        assert attempts == [10]
        assert scout.public()["resultLimit"] == 5
        assert scout.public()["nextScanAt"] > time.time() + 590
        await scout.tick()
        assert attempts == [10]
        restarted = RatioScout(
            database,
            scout.accounts,
            sized,
            scout.qbit,
            scout.vpn_status,
            scout.storage,
            add_stopped,
        )
        await restarted.tick()
        assert attempts == [10]  # Restart cannot bypass the scheduled deadline.
        for expected in [5, 1]:
            with database() as db:
                db.execute("UPDATE ratio_scan_state SET not_before=0")
            restarted.next_scan = 0
            await restarted.tick()
            assert attempts[-1] == expected
        assert attempts == [10, 5, 1]
        assert restarted.public()["status"] == "ready"
        assert len(restarted.public()["candidates"]) == 1
        assert not state.calls
        assert restarted.settings() == settings

    asyncio.run(check())


def test_old_settings_upgrade_defaults_and_configurable_scan_interval(rig):
    scout, settings, _, state, database = rig
    legacy = settings.model_dump()
    legacy.pop("scanMinutes")
    with database() as db:
        db.execute("INSERT INTO ratio_settings VALUES (1,?)", (json.dumps(legacy),))
    restarted = RatioScout(
        database,
        scout.accounts,
        scout.tracker_read,
        scout.qbit,
        scout.vpn_status,
        scout.storage,
        add_stopped,
    )
    assert restarted.settings().scanMinutes == 10
    assert restarted.settings().tracker == settings.tracker

    async def check():
        await restarted.save(
            restarted.settings().model_copy(update={"scanMinutes": 30})
        )
        await restarted.tick()
        assert restarted.public()["scanMinutes"] == 30
        assert restarted.public()["nextScanAt"] > time.time() + 1790
        await restarted.save(restarted.settings())
        restarted.next_scan = 0
        await restarted.tick()
        assert len(state.reads) == 1  # Saving rules cannot trigger more tracker calls.
        await restarted.save(
            restarted.settings().model_copy(update={"scanMinutes": 60})
        )
        assert restarted.public()["nextScanAt"] > time.time() + 3590
        again = RatioScout(
            database,
            scout.accounts,
            scout.tracker_read,
            scout.qbit,
            scout.vpn_status,
            scout.storage,
            add_stopped,
        )
        await again.tick()
        assert len(state.reads) == 1
        assert len(restarted.public()["candidates"]) == 0

    asyncio.run(check())


@pytest.mark.parametrize("minutes", [4, 61, True, "10"])
def test_scan_interval_validation(minutes):
    with pytest.raises(ValidationError):
        RatioSettings(scanMinutes=minutes)
