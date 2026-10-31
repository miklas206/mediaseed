import pytest

from mediaseed.tracker_events import global_events


def test_only_explicit_live_global_freeleech_is_exposed():
    data = {"events": {"global": [
        {"type": "global_freeleech", "ends_at": "2030-01-01T00:00:00Z", "secret": "private"},
        {"type": "movie_club", "title": "unrelated"},
        {"type": "global_freeleech", "ends_at": "2000-01-01T00:00:00Z"},
    ]}}
    result = global_events(data, now=1700000000)
    assert result == [{"type": "global_freeleech", "endsAt": "2030-01-01T00:00:00+00:00"}]
    assert "upload" not in str(result) and "private" not in str(result)
    assert global_events({"events": {"global": []}}) == []


@pytest.mark.parametrize("events", [None, {}, {"global": None}, {"global": "freeleech"}, {"global": [{}] * 21},
    {"global": [{"type": "global_freeleech", "ends_at": "2030-01-01"}]},
    {"global": [{"type": "global_freeleech", "ends_at": "invalid"}]}])
def test_missing_or_invalid_evidence_remains_unknown(events):
    assert global_events({"events": events}) is None


def test_account_api_reuses_existing_request_and_masks_event_extras(tmp_path, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from mediaseed import features
    from mediaseed.app import create_app
    from test_app import sign_in

    original = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"stats": {}, "events": {"global": [
            {"type": "global_freeleech", "ends_at": "2030-01-01T00:00:00Z", "secret": "private-event-value"}]}})
    monkeypatch.setattr(features.httpx, "AsyncClient", lambda **kwargs: original(**{**kwargs, "transport": httpx.MockTransport(handler)}))
    with TestClient(create_app(tmp_path, "http://testserver")) as client:
        sign_in(client)
        client.post("/api/trackers", json={"name": "Synthetic", "api_key": "synthetic-key"})
        response = client.get("/api/trackers")
        assert response.status_code == 200
        assert response.json()[0]["globalEvents"][0]["type"] == "global_freeleech"
        assert "private-event-value" not in response.text and "synthetic-key" not in response.text
        assert client.get("/api/trackers").status_code == 200
        assert calls == ["/api/user"]
