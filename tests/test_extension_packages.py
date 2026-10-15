import importlib.util
import io
import json
import zipfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from mediaseed.app import create_app


def test_extension_packages_match_source_and_are_served(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("extension_build", root / "browser-extension" / "build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    with TestClient(create_app(tmp_path, "http://testserver", qbit_password="synthetic",
                               transport=httpx.MockTransport(lambda request: httpx.Response(200, text="Ok.")))) as client:
        page = client.get("/static/extensions.html")
        assert page.status_code == 200
        assert "extension-browser" in page.text
        assert "Browserudvidelse" in client.get("/").text
        for browser in ("chromium", "firefox", "safari"):
            name = f"mediaseed-{browser}{'-source' if browser == 'safari' else ''}.zip"
            response = client.get("/static/extensions/" + name)
            assert response.status_code == 200
            assert response.content == builder.package(browser)
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["manifest_version"] == 3
                assert "host_permissions" not in manifest
                assert set(manifest["permissions"]) == {"storage", "contextMenus", "scripting", "activeTab"}
                assert "externally_connectable" not in manifest
                assert manifest["version"] == "1.3.0"
                assert manifest["action"]["default_icon"] == manifest["icons"]
                for size in (16, 32, 48, 128):
                    import struct
                    png = archive.read(manifest["icons"][str(size)])
                    assert png[:8] == b"\x89PNG\r\n\x1a\n"
                    assert struct.unpack(">II", png[16:24]) == (size, size)
                if browser == "chromium":
                    assert manifest["background"]["service_worker"] == "worker.js"
                else:
                    assert manifest["background"]["scripts"] == ["i18n.js", "common.js", "bridge.js", "background.js"]
                assert all(not x.startswith(("/", "..")) for x in archive.namelist())
                assert archive.read("mediaseed.svg") == (root / "mediaseed" / "static" / "mediaseed.svg").read_text(encoding="utf-8").encode()
