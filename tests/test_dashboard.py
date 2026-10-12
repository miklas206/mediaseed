import httpx
from fastapi.testclient import TestClient

from mediaseed.app import create_app
from test_app import sign_in


def test_dashboard_reuses_one_bounded_sanitized_read_for_queue_and_traffic(tmp_path):
    calls = []
    rows = [{"hash": "a" * 40, "name": "Synthetic", "state": "downloading", "dlspeed": 4096, "upspeed": 0, "save_path": "private"},
            {"hash": "b" * 40, "state": "metaDL", "category": "MediaSeed-RSS-test"},
            {"hash": "c" * 40, "state": "queuedDL"}]

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/login"):
            return httpx.Response(204)
        assert request.url.path.endswith("/torrents/info")
        assert request.url.params["limit"] == "500"
        return httpx.Response(200, json=rows)

    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(handler))) as client:
        assert client.get("/api/dashboard").status_code == 401 and not calls
        sign_in(client)
        response = client.get("/api/dashboard")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        snapshot = response.json()
        assert "private" not in response.text and "save_path" not in response.text
        assert snapshot["torrents"][0]["dlspeed"] == 4096
        assert snapshot["queue"] == {"active": 2, "limit": 8, "waiting": 1, "rssActive": 1, "manualActive": 1, "boundedView": False}
        assert snapshot["runtime"]["downloadsEnabled"] is False
        assert len(calls) == 2  # login plus one list read, no duplicate queue/version query
        rows.clear()
        empty = client.get("/api/dashboard").json()
        assert empty["torrents"] == [] and empty["queue"]["active"] == 0


def test_dashboard_keeps_failed_reads_distinct_from_empty_lists(tmp_path):
    def handler(request):
        return httpx.Response(204) if request.url.path.endswith("/login") else httpx.Response(503)

    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic", transport=httpx.MockTransport(handler))) as client:
        sign_in(client)
        snapshot = client.get("/api/dashboard").json()
        assert snapshot["torrents"] is None and snapshot["queue"] is None
        assert not snapshot["runtime"]["downloadsEnabled"]
