import json

import pytest
from fastapi import HTTPException

from mediaseed.storage import Storage


@pytest.mark.parametrize("upgrade", [False, True])
def test_destination_staging_is_automatic_and_preserves_existing_files(tmp_path, upgrade):
    final = tmp_path / "media/movies/Film æ"
    final.mkdir(parents=True)
    catalog = Storage(tmp_path, json.dumps([{"id": "movies", "label": "Film", "path": "/downloads/media/movies"}]))
    if upgrade:
        (final / "existing.mkv").write_bytes(b"existing completed data")
        (final / ".incomplete").mkdir()
        (final / ".incomplete/partial").write_bytes(b"partial")
    destination = catalog.destination("movies", "Film æ")
    for _ in range(2):
        assert catalog.incomplete(destination) == destination + "/.incomplete"
    assert (final / ".incomplete").stat().st_dev == final.stat().st_dev
    assert catalog.folders("movies", "Film æ")["folders"] == []
    if upgrade:
        assert (final / "existing.mkv").read_bytes() == b"existing completed data"
        assert (final / ".incomplete/partial").read_bytes() == b"partial"
    with pytest.raises(HTTPException):
        catalog.destination("movies", "Film æ/.incomplete")


@pytest.mark.parametrize("obstacle", ["symlink", "file", "mount", "io-error"])
def test_staging_must_be_a_real_directory_on_the_destination_mount(tmp_path, monkeypatch, obstacle):
    catalog = Storage(tmp_path)
    target = tmp_path / ".incomplete"
    if obstacle == "symlink":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        target.symlink_to(elsewhere)
    elif obstacle == "file":
        target.write_text("preserve")
    elif obstacle == "mount":
        monkeypatch.setattr("mediaseed.storage.os.path.ismount", lambda path: path == target)
    else:
        def failed_write(**kwargs):
            raise OSError(5, "Synthetic network I/O failure")
        monkeypatch.setattr("mediaseed.storage.tempfile.TemporaryFile", failed_write)
    with pytest.raises(HTTPException, match="incomplete") as error:
        catalog.incomplete("/downloads")
    assert error.value.status_code == 409


@pytest.mark.parametrize("upload", [False, True])
def test_older_controller_blocks_additions_with_an_update_requirement(tmp_path, monkeypatch, upload):
    from fastapi.testclient import TestClient
    from mediaseed.app import create_app
    from test_app import sign_in
    from test_vpn_choice import integration

    state, calls = integration(tmp_path, monkeypatch)
    state.pop("torrentCapabilities")
    with TestClient(create_app(tmp_path / "state", "http://testserver")) as client:
        sign_in(client)
        assert client.put("/api/vpn", json={"provider": "mediavpn", "enabled": True, "profileId": "a" * 24}).status_code == 200
        calls.clear()
        response = (client.post("/api/torrents/upload?filename=synthetic.torrent", content=b"dee", headers={"Content-Type": "application/x-bittorrent"})
                    if upload else client.post("/api/torrents", json={"url": "magnet:?xt=urn:btih:" + "a" * 40}))
        assert response.status_code == 409
        assert "Update MediaVPN" in response.json()["detail"]
        assert ("POST", "/api/integration/upload") not in calls
