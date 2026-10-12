import asyncio
import base64
import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import ssl
import time
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mediaseed.features import add_stopped, install_features

COOKIE = "mediaseed_session"
HASHER = PasswordHasher()
STATIC = Path(__file__).parent / "static"


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)


class Magnet(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    url: str = Field(min_length=20, max_length=8192)
    ratio: float = Field(default=1, ge=1, le=1000, allow_inf_nan=False)

    @field_validator("url")
    @classmethod
    def magnet_shape(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError("Invalid magnet link")
        value = value.strip()
        parsed = urlsplit(value)
        if parsed.scheme != "magnet" or parsed.netloc or parsed.path or parsed.fragment:
            raise ValueError("Invalid magnet link")
        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=100)
        topics = params.get("xt", [])
        if not topics or not all(re.fullmatch(r"urn:btih:(?:[a-fA-F0-9]{40}|[a-zA-Z2-7]{32})|urn:btmh:1220[a-fA-F0-9]{64}", topic) for topic in topics):
            raise ValueError("Invalid magnet info hash")
        if any(key in params for key in ("xs", "as", "ws")):
            raise ValueError("External file sources are not supported")
        return value


class TorrentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    torrent_hash: str = Field(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
    confirm_early: bool = False


class VpnSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    profileId: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")


def create_app(data_dir=None, base_url=None, qbit_url=None, qbit_password=None, transport=None, downloads_dir=None, hub_transport=None):
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
    vpn_url = os.environ.get("MEDIASEED_VPN_URL", "").rstrip("/")
    vpn_token_file = os.environ.get("MEDIASEED_VPN_TOKEN_FILE")
    hub_url = os.environ.get("MEDIASEED_HUB_URL", "").rstrip("/")
    hub_parsed = urlsplit(hub_url)
    if hub_url and (parsed.scheme != "https" or hub_parsed.scheme != "https" or not hub_parsed.hostname or hub_parsed.path or hub_parsed.query or hub_parsed.fragment or hub_parsed.username or hub_parsed.password):
        raise ValueError("Managed SSO requires HTTPS app and hub origins")
    hub_ca = os.environ.get("MEDIASEED_HUB_CA_FILE")
    hub_tls = ssl.create_default_context(cafile=hub_ca) if hub_ca else True

    async def hub_request(path, method="GET", payload=None, token=None):
        try:
            async with httpx.AsyncClient(base_url=hub_url, verify=hub_tls, timeout=5, follow_redirects=False, trust_env=False, transport=hub_transport) as client:
                response = await client.request(method, "/api/v1/sso/" + path, json=payload, headers={"Authorization": "Bearer " + token} if token else {})
                if response.status_code in {401, 403}:
                    raise HTTPException(response.status_code, "MediaHub access denied")
                response.raise_for_status()
                return response.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(503, "MediaHub authentication unavailable") from None

    async def vpn_request(path, method="GET", payload=None, content=None):
        if not vpn_url or not vpn_token_file:
            raise HTTPException(409, "MediaVPN integration is not configured")
        try:
            token = Path(vpn_token_file).read_text().strip()
            async with httpx.AsyncClient(base_url=vpn_url, timeout=45, follow_redirects=False, trust_env=False, headers={"X-MediaSeed-Key": token}) as client:
                response = await client.request(method, "/api/integration/" + path, json=payload if content is None else None, content=content)
                if response.status_code >= 400:
                    raise HTTPException(response.status_code if response.status_code in {409, 413, 422} else 503, "MediaVPN could not verify the operation")
                return response.json()
        except (httpx.HTTPError, OSError, ValueError):
            raise HTTPException(503, "MediaVPN is unavailable; downloads are blocked") from None

    async def vpn_status():
        if not vpn_url:
            return {"ready": False, "downloadsEnabled": False, "vpn": "unverified", "profiles": [], "reason": "MediaVPN integration is not configured"}
        return await vpn_request("status")

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
            CREATE TABLE IF NOT EXISTS hub_attempts (digest TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hub_sessions (digest TEXT PRIMARY KEY, token TEXT NOT NULL, user_id TEXT NOT NULL);
        """)
    (data / "mediaseed.sqlite").chmod(0o600)
    @asynccontextmanager
    async def lifespan(app):
        worker = asyncio.create_task(background()) if vpn_url else None
        yield
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
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

    async def authorized(request: Request):
        token = request.cookies.get(COOKIE, "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with database() as connection:
            session = connection.execute("SELECT * FROM sessions WHERE digest=? AND expires>?", (digest, time.time())).fetchone()
            managed = connection.execute("SELECT * FROM hub_sessions WHERE digest=?", (digest,)).fetchone()
        if not session:
            raise HTTPException(401, "Sign in required")
        public = {"role": "administrator"}
        if hub_url:
            if not managed:
                raise HTTPException(401, "Sign in through MediaHub")
            public = await hub_request("session?packageId=org.mediahub.mediaseed", token=managed["token"])
            if public.get("id") != managed["user_id"] or public.get("role") not in {"administrator", "user", "viewer"}:
                raise HTTPException(403, "MediaHub access denied")
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path != "/api/auth/logout":
                if public["role"] == "viewer":
                    raise HTTPException(403, "Read-only app access")
                tracker_admin = request.url.path == "/api/trackers" or (request.url.path.startswith("/api/trackers/") and request.method == "PUT")
                if public["role"] != "administrator" and (tracker_admin or request.url.path == "/api/vpn" or request.url.path.startswith(("/api/accounts", "/api/feeds", "/api/settings", "/api/runtime"))):
                    raise HTTPException(403, "App administrator access required")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(request.headers.get("X-MediaSeed-CSRF", ""), session["csrf"]):
            raise HTTPException(403, "Session verification failed")
        return {**dict(session), **public}

    @app.get("/auth/hub/start")
    def hub_start():
        if not hub_url:
            raise HTTPException(409, "MediaHub SSO is not configured")
        state, verifier = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with database() as connection:
            connection.execute("DELETE FROM hub_attempts WHERE expires<=?", (time.time(),))
            if connection.execute("SELECT COUNT(*) FROM hub_attempts").fetchone()[0] >= 1000:
                raise HTTPException(429, "Try again later")
            connection.execute("INSERT INTO hub_attempts VALUES (?,?,?)", (hashlib.sha256(state.encode()).hexdigest(), verifier, time.time() + 300))
        response = RedirectResponse(hub_url + "/api/v1/sso/authorize?" + urlencode({"packageId": "org.mediahub.mediaseed", "state": state, "challenge": challenge}), status_code=303)
        response.set_cookie("mediaseed_sso_state", state, httponly=True, secure=True, samesite="lax", max_age=300, path="/auth/hub")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/auth/hub/callback")
    async def hub_callback(request: Request, code: str = Query(min_length=40, max_length=128), state: str = Query(min_length=43, max_length=128)):
        if not hub_url or not secrets.compare_digest(state, request.cookies.get("mediaseed_sso_state", "")):
            raise HTTPException(403, "Login verification failed")
        with database() as connection:
            attempt = connection.execute("DELETE FROM hub_attempts WHERE digest=? AND expires>? RETURNING verifier", (hashlib.sha256(state.encode()).hexdigest(), time.time())).fetchone()
        if not attempt:
            raise HTTPException(401, "Login attempt expired")
        identity = await hub_request("exchange", "POST", {"packageId": "org.mediahub.mediaseed", "code": code, "verifier": attempt["verifier"]})
        public = identity.get("user", {})
        if not isinstance(identity.get("token"), str) or not isinstance(identity.get("expires"), int) or not isinstance(public.get("id"), str) or public.get("role") not in {"administrator", "user", "viewer"}:
            raise HTTPException(503, "Invalid MediaHub identity")
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with database() as connection:
            connection.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            connection.execute("DELETE FROM hub_sessions WHERE digest NOT IN (SELECT digest FROM sessions)")
            connection.execute("INSERT INTO sessions VALUES (?,?,?)", (digest, csrf, min(identity["expires"], time.time() + 43200)))
            connection.execute("INSERT INTO hub_sessions VALUES (?,?,?)", (digest, identity["token"], public["id"]))
        response = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE, token, httponly=True, secure=True, samesite="strict", max_age=43200)
        response.delete_cookie("mediaseed_sso_state", path="/auth/hub")
        return response

    async def qbit(path, method="GET", payload=None, files=None):
        if vpn_url:
            if files:
                query = urlencode({"ratio": float(payload.get("ratioLimit", 1)), "savepath": payload.get("savepath", "/downloads"), "category": payload.get("category", "")})
                result = await vpn_request("upload?" + query, "POST", content=files["torrents"][1])
            else:
                result = await vpn_request("rpc", "POST", {"path": path.removeprefix("/api/v2/"), "method": method, "payload": payload or {}})
            try:
                return httpx.Response(result["status"], content=base64.b64decode(result["body"], validate=True))
            except (KeyError, ValueError, TypeError):
                raise HTTPException(503, "Invalid MediaVPN client response") from None
        if not password:
            raise HTTPException(503, "Torrent client is not configured")
        try:
            async with httpx.AsyncClient(base_url=endpoint, headers={"Referer": endpoint}, timeout=8, follow_redirects=False, trust_env=False, transport=transport) as client:
                response = await client.post("/api/v2/auth/login", data={"username": qbit_user, "password": password}, headers={"Referer": endpoint})
                if not (response.status_code == 204 or (response.status_code == 200 and response.text.strip() == "Ok.")):
                    raise HTTPException(503, "Torrent client authentication unavailable")
                if method == "GET":
                    response = await client.get(path, params=payload)
                else:
                    response = await client.post(path, data=payload, files=files)
                if path == "/api/v2/torrents/add" and response.status_code == 415:
                    raise HTTPException(422, "Invalid torrent file")
                response.raise_for_status()
                return response
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Torrent client unavailable") from None

    @app.get("/healthz")
    def health():
        return {"status": "healthy", "version": "0.1.0"}

    @app.get("/api/auth/status")
    def status():
        if hub_url:
            return {"needsSetup": False, "managed": True, "loginUrl": "/auth/hub/start"}
        with database() as connection:
            return {"needsSetup": connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0}

    @app.post("/api/auth/setup")
    def setup(body: Credentials):
        if hub_url:
            raise HTTPException(403, "Users are managed by MediaHub")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
                raise HTTPException(409, "Account already configured")
            connection.execute("INSERT INTO accounts VALUES (?,?)", (body.username, HASHER.hash(body.password)))
        return {"created": True}

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        if hub_url:
            raise HTTPException(403, "Sign in through MediaHub")
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
        return {key: session[key] for key in ("csrf", "id", "username", "role") if key in session}

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
            connection.execute("DELETE FROM hub_sessions WHERE digest=?", (session["digest"],))
        response.delete_cookie(COOKIE)
        return {"signedOut": True}

    @app.get("/api/client")
    async def client_status(session=Depends(authorized)):
        response = await qbit("/api/v2/app/version")
        version = response.text.strip()
        if not version.startswith("v") or len(version) > 40:
            raise HTTPException(503, "Invalid torrent client version")
        return {"name": "qBittorrent", "version": version, "connected": True, "downloadsEnabled": (await vpn_status())["downloadsEnabled"]}

    @app.get("/api/vpn")
    async def vpn_overview(session=Depends(authorized)):
        return await vpn_status()

    @app.put("/api/vpn")
    async def configure_vpn(body: VpnSettings, session=Depends(authorized)):
        return await vpn_request("settings", "PUT", body.model_dump())

    @app.get("/api/torrents")
    async def torrents(session=Depends(authorized)):
        response = await qbit("/api/v2/torrents/info", payload={"limit": 500})
        allowed = {"hash", "name", "progress", "state", "dlspeed", "upspeed", "ratio", "eta", "size", "seeding_time", "ratio_limit", "category", "num_seeds", "num_leechs", "downloaded", "uploaded"}
        try:
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError()
            result = []
            for row in rows[:500]:
                public = {key: value for key, value in row.items() if key in allowed}
                identifier = str(row.get("hash", ""))
                if re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", identifier):
                    cached = data / "cover-cache" / (hashlib.sha256(identifier.lower().encode()).hexdigest() + ".img")
                    if cached.is_file():
                        public["cover"] = "/api/covers/" + identifier.lower()
                result.append(public)
            return result
        except (ValueError, AttributeError):
            raise HTTPException(503, "Invalid torrent client response") from None

    @app.post("/api/torrents")
    async def add(body: Magnet, session=Depends(authorized)):
        return await add_stopped(qbit, body.ratio, magnet=body.url)

    @app.post("/api/torrents/upload")
    async def upload(request: Request, filename: str = Query(min_length=1, max_length=255), ratio: float = Query(default=1, ge=1, le=1000, allow_inf_nan=False), session=Depends(authorized)):
        if not filename.lower().endswith(".torrent"):
            raise HTTPException(422, "Choose a .torrent file")
        if request.headers.get("content-type", "").split(";")[0] not in {"application/x-bittorrent", "application/octet-stream"}:
            raise HTTPException(415, "Expected a torrent file upload")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 4 * 1024 * 1024:
                raise HTTPException(413, "Torrent file exceeds 4 MiB")
        if not content.startswith(b"d") or not content.endswith(b"e"):
            raise HTTPException(422, "Invalid torrent file")
        return await add_stopped(qbit, ratio, content=bytes(content))

    @app.post("/api/torrents/{action}")
    async def action(action: str, body: TorrentAction, session=Depends(authorized)):
        if action == "start":
            if not (await vpn_status())["downloadsEnabled"]:
                raise HTTPException(409, "Starting downloads is disabled until the VPN gate is implemented")
            await qbit("/api/v2/torrents/start", "POST", {"hashes": body.torrent_hash})
            return {"completed": True}
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

    background = install_features(app, authorized, database, qbit, vpn_status=vpn_status, data_dir=data)

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app