"""Operator-approved download locations shared with the torrent client."""

import json
import os
import shutil
from pathlib import Path, PurePosixPath

from fastapi import HTTPException


class Storage:
    def __init__(self, downloads, configuration=None):
        self.downloads = downloads
        self.locations = [
            {"id": "local", "label": "Local downloads", "path": "/downloads"}
        ]
        rows = json.loads(
            configuration or os.environ.get("MEDIASEED_STORAGE_ROOTS", "[]")
        )
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError("Invalid approved storage locations")
        for row in rows:
            identifier, path = row["id"], row["path"]
            if (
                not isinstance(identifier, str)
                or not identifier.isascii()
                or not identifier.replace("-", "").isalnum()
                or identifier == "local"
                or any(r["id"] == identifier for r in self.locations)
                or path != "/downloads/media/" + identifier
                or not isinstance(row["label"], str)
            ):
                raise ValueError("Invalid approved storage location")
            self.locations.append(
                {"id": identifier, "label": row["label"][:100], "path": path}
            )

    def directory(self, location, relative="", writable=False):
        row = next((r for r in self.locations if r["id"] == location), None)
        if (
            row is None
            or len(relative) > 1000
            or "\\" in relative
            or any(ord(c) < 32 for c in relative)
        ):
            raise HTTPException(422, "Choose an approved download folder")
        parts = relative.split("/") if relative else []
        if any(p in {"", ".", ".."} for p in parts) or relative.startswith("/"):
            raise HTTPException(422, "Choose an approved download folder")
        # The local-volume browser cannot bypass the named media location checks.
        if location == "local" and parts[:1] == ["media"]:
            raise HTTPException(422, "Choose the media storage location")
        root = self.downloads / PurePosixPath(row["path"]).relative_to("/downloads")
        path = root.joinpath(*parts)
        try:
            if not path.is_dir() or path.resolve(strict=True) != path:
                raise OSError()
            # os.access uses this app's real uid and supplementary groups.
            if writable and not os.access(path, os.W_OK | os.X_OK):
                raise OSError()
        except OSError:
            raise HTTPException(
                409, "Download folder is unavailable or read-only"
            ) from None
        return row, path

    def destination(self, location, relative):
        row, _ = self.directory(location, relative, writable=True)
        return row["path"] + ("/" + relative if relative else "")

    def snapshot(self):
        result = []
        for row in self.locations:
            observed = {**row, "available": False, "writable": False}
            try:
                _, path = self.directory(row["id"])
                usage = shutil.disk_usage(path)
                observed.update(
                    available=True,
                    writable=os.access(path, os.W_OK | os.X_OK),
                    totalBytes=usage.total,
                    freeBytes=usage.free,
                    usedBytes=usage.used,
                    lowSpace=usage.free < 2 * 1024**3,
                )
            except (OSError, HTTPException):
                pass
            result.append(observed)
        default = next(
            (r["id"] for r in result if r["id"] == "downloads" and r["writable"]),
            "local",
        )
        return {
            **result[0],
            "type": "local",
            "externalMountRequired": False,
            "locations": result,
            "defaultLocation": default,
        }

    def folders(self, location, relative):
        _, path = self.directory(location, relative)
        rows = []
        truncated = False
        try:
            with os.scandir(path) as entries:
                for scanned, entry in enumerate(entries):
                    if scanned >= 2000 or len(rows) >= 200:
                        truncated = True
                        break
                    if entry.name.startswith(".") or (
                        location == "local" and not relative and entry.name == "media"
                    ):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        rows.append(
                            {
                                "name": entry.name,
                                "writable": os.access(entry.path, os.W_OK | os.X_OK),
                            }
                        )
        except OSError:
            raise HTTPException(
                409, "Download folder is unavailable or read-only"
            ) from None
        return {
            "location": location,
            "path": relative,
            "folders": sorted(rows[:200], key=lambda r: r["name"].casefold()),
            "truncated": truncated,
            "writable": os.access(path, os.W_OK | os.X_OK),
        }
