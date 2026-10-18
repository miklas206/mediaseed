import asyncio

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from mediaseed.features import add_stopped
from test_app import sign_in
from test_vpn_choice import integration


@pytest.mark.parametrize("start", [True, False])
@pytest.mark.parametrize("source", ["file", "magnet"])
def test_explicit_start_choice_preserves_policy_and_requires_vpn(start, source):
    calls = []

    async def ready():
        return {"downloadsEnabled": True}

    async def qbit(path, method, payload, files=None):
        calls.append((path, payload, files))
        return httpx.Response(200, json={"failure_count": 0, "success_count": 1, "pending_count": 0})

    arguments = {"content": b"dee"} if source == "file" else {"magnet": "magnet:?xt=urn:btih:" + "a" * 40}
    result = asyncio.run(add_stopped(qbit, 2, **arguments, start=start, vpn_status=ready))
    payload = calls[0][1]
    assert payload["stopped"] == payload["paused"] == ("false" if start else "true")
    assert payload["ratioLimit"] == "2" and payload["seedingTimeLimit"] == "2940"
    assert result["stopped"] is not start
    assert bool(calls[0][2]) == (source == "file")

    async def blocked():
        return {"downloadsEnabled": False}

    calls.clear()
    with pytest.raises(HTTPException) as error:
        asyncio.run(add_stopped(qbit, 2, **arguments, start=True, vpn_status=blocked))
    assert error.value.status_code == 409 and not calls


@pytest.mark.parametrize("source", ["file", "magnet"])
def test_start_api_choice_is_checked_server_side(tmp_path, monkeypatch, source):
    state, calls = integration(tmp_path, monkeypatch)
    with TestClient(create_app(tmp_path / "data", "http://testserver")) as client:
        sign_in(client)
        client.put("/api/vpn", json={"provider": "mediavpn", "enabled": True, "profileId": "a" * 24})

        def submit(start):
            if source == "file":
                return client.post(f"/api/torrents/upload?filename=test.torrent&start={str(start).lower()}", content=b"d4:infod4:name4:testee", headers={"Content-Type": "application/x-bittorrent"})
            return client.post("/api/torrents", json={"url": "magnet:?xt=urn:btih:" + "a" * 40, "start": start})

        assert submit(True).json()["startRequested"] is True
        assert submit(False).json()["stopped"] is True
        state["ready"] = state["downloadsEnabled"] = False
        count = len(calls)
        assert submit(True).status_code == 409
        assert not any(path.endswith(("/rpc", "/upload")) for _, path in calls[count:])
