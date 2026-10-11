import hashlib
import os
import secrets
import sqlite3
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from mediaseed.features import install_features

COOKIE = "mediaseed_session"
HASHER = PasswordHasher()
STATIC = Path(__file__).parent / "static"


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)


class Magnet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=20, max_length=8192, pattern=r"^magnet:\?xt=urn:bt(?:ih|mh):")


class TorrentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    torrent_hash: str = Field(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
    confirm_early: bool = False


def create_app(data_dir=None, base_url=None, qbit_url=None, qbit_password=None, transport=None, downloads_dir=None):
    data = Path(data_dir or os.environ.get("MEDIASEED_DATA", "/data"))
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    downloads = Path(downloads_dir or os.environ.get("MEDIASEED_DOWNLOADS", str(data / "downloads")))
    if not downloads.is_absolute() or ".." in downloads.parts or downloads.is_symlink():
        raise ValueError("Expected a trusted absolute downloads directory")
    downloads.mkdir(parents=True, exist_ok=True, mode=0o700)
    if downloads.resolve() != downloads:
        raise ValueError("Downloads directory must not contain symlink components")
    base = (base_url or os.environ.get("MEDIASEED_BASE_URL", "http://127.0.0.1:18800")).rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path or parsed.username:
        raise ValueError("Expected an HTTP(S) origin without path or credentials")
    endpoint = qbit_url or os.environ.get("MEDIASEED_QBIT_URL", "http://vpn:8080")
    qbit_user = os.environ.get("MEDIASEED_QBIT_USER", "admin")
    password_file = os.environ.get("MEDIASEED_QBIT_PASSWORD_FILE")
    password = qbit_password or (Path(password_file).read_text().strip() if password_file else None)

    @contextmanager
    def database():
        with sqlite3.connect(data / "mediaseed.sqlite", timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            yield connection

    with database() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (username TEXT PRIMARY KEY, password TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (address TEXT PRIMARY KEY, count INTEGER NOT NULL, started REAL NOT NULL);
        """)
    (data / "mediaseed.sqlite").chmod(0o600)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname])

    @app.middleware("http")
    async def security(request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != base:
            return Response(status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def authorized(request: Request):
        token = request.cookies.get(COOKIE, "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with database() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE digest=? AND expires>?", (digest, time.time())).fetchone()
        if not session:
            raise HTTPException(401, "Sign in required")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(request.headers.get("X-MediaSeed-CSRF", ""), session["csrf"]):
            raise HTTPException(403, "Session verification failed")
        return dict(session)

    async def qbit(path, method="GET", payload=None):
        if not password:
            raise HTTPException(503, "Torrent client is not configured")
        try:
            async with httpx.AsyncClient(base_url=endpoint, timeout=8, follow_redirects=False, trust_env=False, transport=transport) as client:
                response = await client.post("/api/v2/auth/login", data={"username": qbit_user, "password": password}, headers={"Referer": endpoint})
                if response.status_code != 200 or response.text.strip() != "Ok.":
                    raise HTTPException(503, "Torrent client authentication unavailable")
                if method == "GET":
                    response = await client.get(path, params=payload)
                else:
                    response = await client.post(path, data=payload)
                response.raise_for_status()
                return response
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Torrent client unavailable") from None

    @app.get("/healthz")
    def health():
        return {"status": "healthy", "version": "0.1.0"}

    @app.get("/api/auth/status")
    def status():
        with database() as connection:
            return {"needsSetup": connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0}

    @app.post("/api/auth/setup")
    def setup(body: Credentials):
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
                raise HTTPException(409, "Account already configured")
            connection.execute("INSERT INTO accounts VALUES (?,?)", (body.username, HASHER.hash(body.password)))
        return {"created": True}

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        address = request.client.host if request.client else "local"
        now = time.time()
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM attempts WHERE started<?", (now - 300,))
            attempt = connection.execute("SELECT count FROM attempts WHERE address=?", (address,)).fetchone()
            if attempt and attempt[0] >= 10:
                raise HTTPException(429, "Try again later")
            connection.execute("INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(address) DO UPDATE SET count=count+1", (address, now))
            account = connection.execute("SELECT password FROM accounts WHERE username=?", (body.username,)).fetchone()
        try:
            if not account or not HASHER.verify(account[0], body.password):
                raise HTTPException(401, "Invalid credentials")
        except VerificationError:
            raise HTTPException(401, "Invalid credentials") from None
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with database() as connection:
            connection.execute("DELETE FROM sessions WHERE expires<?", (now,))
            connection.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), csrf, now + 3600 * 12))
        response.set_cookie(COOKIE, token, httponly=True, secure=parsed.scheme == "https", samesite="strict", max_age=43200)
        return {"csrf": csrf}

    @app.get("/api/auth/me")
    def me(session=Depends(authorized)):
        return {"csrf": session["csrf"]}

    @app.get("/api/storage")
    def storage(session=Depends(authorized)):
        try:
            capacity = shutil.disk_usage(downloads)
        except OSError:
            raise HTTPException(503, "Local downloads storage unavailable") from None
        return {"label": "Local downloads", "type": "local", "totalBytes": capacity.total, "freeBytes": capacity.free, "usedBytes": capacity.used, "writable": os.access(downloads, os.W_OK), "lowSpace": capacity.free < 2 * 1024**3, "externalMountRequired": False}

    @app.post("/api/auth/logout")
    def logout(response: Response, session=Depends(authorized)):
        with database() as connection:
            connection.execute("DELETE FROM sessions WHERE digest=?", (session["digest"],))
        response.delete_cookie(COOKIE)
        return {"signedOut": True}

    @app.get("/api/client")
    async def client_status(session=Depends(authorized)):
        response = await qbit("/api/v2/app/version")
        version = response.text.strip()
        if not version.startswith("v") or len(version) > 40:
            raise HTTPException(503, "Invalid torrent client version")
        return {"name": "qBittorrent", "version": version, "connected": True, "downloadsEnabled": False}

    @app.get("/api/torrents")
    async def torrents(session=Depends(authorized)):
        response = await qbit("/api/v2/torrents/info", payload={"limit": 500})
        allowed = {"hash", "name", "progress", "state", "dlspeed", "upspeed", "ratio", "eta", "size", "seeding_time", "ratio_limit", "category", "num_seeds", "num_leechs", "downloaded", "uploaded"}
        try:
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError()
            return [{key: value for key, value in row.items() if key in allowed} for row in rows[:500]]
        except (ValueError, AttributeError):
            raise HTTPException(503, "Invalid torrent client response") from None

    @app.post("/api/torrents")
    async def add(body: Magnet, session=Depends(authorized)):
        raise HTTPException(409, "Adding torrents is disabled until the VPN gate is implemented")

    @app.post("/api/torrents/{action}")
    async def action(action: str, body: TorrentAction, session=Depends(authorized)):
        if action not in {"stop", "remove"}:
            raise HTTPException(409, "Starting downloads is disabled until the VPN gate is implemented")
        if not body.confirm_early:
            properties = await qbit("/api/v2/torrents/properties", payload={"hash": body.torrent_hash})
            try:
                observed = properties.json()
                if float(observed.get("share_ratio", 0)) < 1 and int(observed.get("seeding_time", 0)) < 49 * 3600:
                    raise HTTPException(409, "Early stop: ratio is below 1 and seeding time is below 49 hours. Explicit confirmation required.")
            except (ValueError, AttributeError, TypeError):
                raise HTTPException(503, "Cannot verify seeding progress") from None
        path = "delete" if action == "remove" else "stop"
        await qbit("/api/v2/torrents/" + path, "POST", {"hashes": body.torrent_hash, "deleteFiles": "false"})
        return {"completed": True}

    install_features(app, authorized, database, qbit)

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app