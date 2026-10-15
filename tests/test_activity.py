import sqlite3
from contextlib import contextmanager

from fastapi.testclient import TestClient
from test_app import sign_in

from mediaseed.activity import Activity
from mediaseed.app import create_app


def test_activity_is_persistent_bounded_and_read_only(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path / "activity.sqlite") as db:
            yield db
    activity = Activity(database)
    for index in range(1005):
        activity.record("rss", str(index), "Synthetic release", "added", feed="a" * 24)
    first = activity.public()
    assert len(first["entries"]) == 10
    activity.record("rss", "1004", "Synthetic release", "added", feed="a" * 24)
    assert Activity(database).public() == first
    with database() as db:
        assert db.execute("SELECT COUNT(*) FROM automatic_activity").fetchone()[0] == 1000
    with TestClient(create_app(tmp_path / "app", "http://testserver")) as client:
        assert client.get("/api/activity").status_code == 401
        sign_in(client)
        assert client.get("/api/activity").json() == {"entries": []}
