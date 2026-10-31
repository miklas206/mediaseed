"""Explicit read-only source roots and a separate writable preparation volume."""
import json
import os
from pathlib import Path

from fastapi import HTTPException


class Storage:
    def __init__(self, configuration, work):
        self.work = Path(work).resolve(strict=True)
        self.locations = json.loads(configuration)
        if not isinstance(self.locations, list) or not self.locations:
            raise ValueError("At least one approved media root is required")
        seen = set()
        for row in self.locations:
            if not isinstance(row.get("id"), str) or not row["id"].replace("-", "").isalnum() or row["id"] in seen:
                raise ValueError("Invalid source identifier")
            seen.add(row["id"])
            path = Path(row["path"])
            if not path.is_absolute() or ".." in path.parts or path.is_symlink():
                raise ValueError("Expected absolute media root")

        # Shortcuts stay strictly inside an already approved library root.
        # Derive them for both existing configuration and fresh installs.
        library = next((row for row in self.locations if row["id"] == "library"), None)
        if library:
            for identifier, label in [("tv", "TV-serier"), ("movies", "Film")]:
                path = Path(library["path"]) / identifier
                if identifier not in seen and path.is_dir() and not path.is_symlink():
                    shortcut = {"id": identifier, "label": label, "path": str(path)}
                    if library.get('clientPath'):
                        shortcut['clientPath'] = library['clientPath'].rstrip('/') + '/' + identifier
                    self.locations.append(shortcut)

    def client_path(self, path):
        for row in self.locations:
            if row.get('clientPath'):
                try:
                    relative = Path(path).relative_to(row['path'])
                except ValueError:
                    continue
                return (Path(row['clientPath']) / relative).as_posix()
        return None

    def directory(self, location, relative="", writable=False):
        row = next((r for r in self.locations if r["id"] == location), None)
        if not row or not isinstance(relative, str) or "\\" in relative or len(relative) > 1000:
            raise HTTPException(422, "Vælg en godkendt mediemappe")
        parts = relative.split("/") if relative else []
        if any(not p or p.startswith(".") or any(ord(c) < 32 for c in p) for p in parts):
            raise HTTPException(422, "Vælg en almindelig undermappe")
        path = Path(row["path"]).joinpath(*parts)
        try:
            if not path.is_dir() or path.resolve(strict=True) != path or not os.access(path, os.R_OK | os.X_OK):
                raise OSError()
            if writable:
                raise OSError()
        except OSError:
            raise HTTPException(409, "Mediemappen er utilgængelig. Kilder er altid skrivebeskyttede") from None
        return row, path

    def output(self):
        if self.work.resolve(strict=True) != self.work or not os.access(self.work, os.W_OK | os.X_OK):
            raise HTTPException(409, "Uploadarbejdsområdet er ikke skrivbart")
        return self.work

    def folders(self, location, relative=""):
        _, path = self.directory(location, relative)
        rows = []
        with os.scandir(path) as entries:
            for n, entry in enumerate(entries):
                if n >= 2000:
                    raise HTTPException(422, "For mange mapper. Vælg en mere præcis sti")
                if not entry.name.startswith(".") and entry.name.casefold() not in {"sample", "samples"} and entry.is_dir(follow_symlinks=False):
                    rows.append(entry.name)
        return {"folders": sorted(rows, key=str.casefold), "path": relative}
