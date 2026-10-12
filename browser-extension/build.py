"""Build deterministic, dependency-free browser packages shipped by MediaSeed."""
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT.parent / "mediaseed" / "static"
FILES = ["common.js", "bridge.js", "background.js", "worker.js", "capture.js", "popup.js", "popup.html", "popup.css", "README.md"]


def package(browser):
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    if browser != "chromium":
        manifest["background"] = {"scripts": ["common.js", "bridge.js", "background.js"]}
    if browser == "firefox":
        manifest["browser_specific_settings"] = {"gecko": {
            "id": "mediaseed@mediahub.local", "strict_min_version": "140.0",
            "data_collection_permissions": {"required": ["none"]},
        }}
    # Git's Windows checkout may use CRLF; packages must match on Linux too.
    contents = {name: (ROOT / name).read_text(encoding="utf-8").encode() for name in FILES}
    contents["mediaseed.svg"] = (STATIC / "mediaseed.svg").read_text(encoding="utf-8").encode()
    contents.update({f"icons/{size}.png": (ROOT / "icons" / f"{size}.png").read_bytes() for size in (16, 32, 48, 128)})
    contents["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 12, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return stream.getvalue()


def main():
    output = STATIC / "extensions"
    check = "--check" in sys.argv
    if not check:
        output.mkdir(exist_ok=True)
    for browser in ("chromium", "firefox", "safari"):
        target = output / ("mediaseed-" + browser + ("-source" if browser == "safari" else "") + ".zip")
        data = package(browser)
        if check:
            if not target.exists() or target.read_bytes() != data:
                raise SystemExit(f"Rebuild extension package: {target.name}")
        else:
            target.write_bytes(data)
        print(f"{'Checked' if check else 'Built'} {target.name}: {len(data)} bytes")


if __name__ == "__main__":
    main()
