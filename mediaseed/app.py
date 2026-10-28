import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import ssl
import time
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from mediaseed.appearance import install_theme_preferences, navigation_override, theme_override
from mediaseed.cleanup import CleanupManager, CleanupPolicy, cleanup_identity
from mediaseed.features import add_stopped, install_features, queue_summary
from mediaseed.tracker_credit import TrackerCredit
from mediaseed.storage import Storage

COOKIE = "mediaseed_session"
HASHER = PasswordHasher()
STATIC = Path(__file__).parent / "static"


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)
    language: Literal["en", "da"] = "en"


class Magnet(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    url: str = Field(min_length=20, max_length=8192)
    ratio: float = Field(default=1, ge=1, le=1000, allow_inf_nan=False)
    start: bool = False
    cleanup: CleanupPolicy = Field(default_factory=CleanupPolicy)
    location: str = Field(default="local", max_length=64)
    folder: str = Field(default="", max_length=1000)

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
        return value


class TorrentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    torrent_hash: str = Field(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")
    confirm_early: bool = False
    delete_files: bool = False


class DownloadSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maxActiveDownloads: int = Field(ge=1, le=100, strict=True)


class RssSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rssRefreshMinutes: int = Field(ge=1, le=1440, strict=True)


class VpnSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    provider: Literal["mediavpn"] | None = None
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
    storage_catalog = Storage(downloads)
    move_manager = None
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
    hub_browser_url = os.environ.get("MEDIASEED_HUB_BROWSER_URL", hub_url).rstrip("/")
    hub_parsed = urlsplit(hub_url)
    if hub_url and (parsed.scheme != "https" or hub_parsed.scheme != "https" or not hub_parsed.hostname or hub_parsed.path or hub_parsed.query or hub_parsed.fragment or hub_parsed.username or hub_parsed.password):
        raise ValueError("Managed SSO requires HTTPS app and hub origins")
    browser_hub = urlsplit(hub_browser_url)
    if hub_url and (browser_hub.scheme != "https" or not browser_hub.hostname or browser_hub.path or browser_hub.query or browser_hub.fragment or browser_hub.username or browser_hub.password):
        raise ValueError("Managed SSO requires an HTTPS browser hub origin")
    hub_ca = os.environ.get("MEDIASEED_HUB_CA_FILE")
    hub_tls = ssl.create_default_context(cafile=hub_ca) if hub_ca else True

    from mediaseed.http_pool import HttpPool
    hub_pool = HttpPool(base_url=hub_url or base, verify=hub_tls, timeout=5, follow_redirects=False, trust_env=False, transport=hub_transport)
    vpn_pool = HttpPool(base_url=vpn_url or base, timeout=45, follow_redirects=False, trust_env=False)

    async def hub_request(path, method="GET", payload=None, token=None, coalesce=False):
        try:
            response = await hub_pool.request(method, "/api/v1/sso/" + path, coalesce=coalesce, json=payload, headers={"Authorization": "Bearer " + token} if token else {})
            if response.status_code in {401, 403}:
                raise HTTPException(response.status_code, "MediaHub access denied")
            response.raise_for_status()
            return response.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(503, "MediaHub authentication unavailable") from None

    async def managed_origin(request=None):
        if request is not None and request.headers.get("host", "").lower() == urlsplit(base).netloc.lower():
            return base
        value = (await hub_request("origin?packageId=org.mediahub.mediaseed")).get("origin", "")
        candidate = urlsplit(value)
        if candidate.scheme != "https" or not candidate.hostname or candidate.path or candidate.query or candidate.fragment or candidate.username or candidate.password:
            raise HTTPException(503, "Invalid MediaHub app origin")
        return value

    async def vpn_request(path, method="GET", payload=None, content=None):
        if not vpn_url or not vpn_token_file:
            raise HTTPException(409, "MediaVPN integration is not configured")
        try:
            token = Path(vpn_token_file).read_text().strip()
            response = await vpn_pool.request(method, "/api/integration/" + path, json=payload if content is None else None, content=content, headers={"X-MediaSeed-Key": token})
            if response.status_code >= 400:
                raise HTTPException(response.status_code if response.status_code in {409, 413, 422} else 503, "MediaVPN could not verify the operation")
            return response.json()
        except (httpx.HTTPError, OSError, ValueError):
            raise HTTPException(503, "MediaVPN is unavailable; downloads are blocked") from None

    @contextmanager
    def database():
        with sqlite3.connect(data / "mediaseed.sqlite", timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            yield connection

    with database() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS vpn_selection (
                id INTEGER PRIMARY KEY CHECK(id=1), provider TEXT, profile_id TEXT,
                disconnect_pending INTEGER NOT NULL DEFAULT 0
            );
            INSERT OR IGNORE INTO vpn_selection(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS accounts (username TEXT PRIMARY KEY, password TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (address TEXT PRIMARY KEY, count INTEGER NOT NULL, started REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hub_attempts (digest TEXT PRIMARY KEY, verifier TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hub_sessions (digest TEXT PRIMARY KEY, token TEXT NOT NULL, user_id TEXT NOT NULL);
        """)
        if "language" not in {row["name"] for row in connection.execute("PRAGMA table_info(accounts)")}:
            connection.execute("ALTER TABLE accounts ADD COLUMN language TEXT NOT NULL DEFAULT 'da'")
    (data / "mediaseed.sqlite").chmod(0o600)
    vpn_lock = asyncio.Lock()

    def vpn_selection():
        with database() as connection:
            return dict(connection.execute("SELECT * FROM vpn_selection WHERE id=1").fetchone())

    def save_vpn(provider, profile_id, pending=False):
        with database() as connection:
            connection.execute("UPDATE vpn_selection SET provider=?,profile_id=?,disconnect_pending=? WHERE id=1", (provider, profile_id, int(pending)))

    async def disconnect_vpn():
        selection = vpn_selection()
        if selection["disconnect_pending"]:
            await vpn_request("settings", "PUT", {"enabled": False, "profileId": selection["profile_id"]})
            save_vpn(None, selection["profile_id"])

    async def vpn_status(discover=False):
        selection = vpn_selection()
        configured = bool(vpn_url and vpn_token_file)
        disconnected = {
            "provider": None, "enabled": False, "profileId": None,
            "ready": False, "downloadsEnabled": False, "vpn": "unverified",
            "profiles": [], "integrationAvailable": configured,
            "disconnectPending": bool(selection["disconnect_pending"]),
            "publicIp": None, "forwardedPort": None,
            "country": None, "checkedAt": time.time(),
            "reason": "MediaVPN er ikke valgt. Downloads er blokeret."
        }
        if not selection["provider"] and not discover:
            return disconnected
        if not configured:
            return {**disconnected, "reason": "MediaVPN is not available in this installation."}
        try:
            remote = await vpn_request("status")
        except HTTPException:
            return {**disconnected, "provider": selection["provider"], "profileId": selection["profile_id"] if selection["provider"] else None, "integrationAvailable": False, "reason": "MediaVPN is unavailable; downloads are blocked."}
        if not selection["provider"]:
            return {**disconnected, "profiles": remote.get("profiles", []), "reason": "Frakobling afventer MediaVPN. Downloads er blokeret." if selection["disconnect_pending"] else disconnected["reason"]}
        matches = remote.get("enabled") is True and remote.get("profileId") == selection["profile_id"]
        ready = matches and remote.get("ready") is True and remote.get("downloadsEnabled") is True
        return {**remote, "provider": "mediavpn", "profileId": selection["profile_id"], "enabled": True, "integrationAvailable": True, "disconnectPending": False, "ready": ready, "downloadsEnabled": ready, "vpn": "verified" if ready else "unverified", "reason": remote.get("reason") if matches else "Den valgte MediaVPN-profil er ikke tilkoblet; downloads er blokeret."}

    async def reconcile_disconnect():
        while True:
            try:
                async with vpn_lock:
                    await disconnect_vpn()
            except HTTPException:
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(app):
        workers = [asyncio.create_task(cleanup.run()), asyncio.create_task(credit.run()), asyncio.create_task(move_manager.run())]
        if vpn_url:
            workers += [asyncio.create_task(background()), asyncio.create_task(reconcile_disconnect())]
        yield
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with suppress(asyncio.CancelledError):
                await worker
        await hub_pool.close()
        await vpn_pool.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    if not hub_url:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[parsed.hostname])

    @app.middleware("http")
    async def security(request, call_next):
        origin = request.headers.get("origin")
        origins = {base}
        if hub_url and (request.url.hostname != parsed.hostname or origin not in {None, base}):
            try:
                origins.add(await managed_origin())
            except HTTPException as error:
                return JSONResponse({"detail": error.detail}, status_code=error.status_code)
        if hub_url and request.url.hostname not in {urlsplit(value).hostname for value in origins}:
            return Response(status_code=400)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in origins:
            return Response(status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
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
        with database() as connection:
            account = connection.execute("SELECT language FROM accounts LIMIT 1").fetchone()
        public = {"role": "administrator", "language": account[0] if account else "en"}
        if hub_url:
            if not managed:
                raise HTTPException(401, "Sign in through MediaHub")
            public = await hub_request("session?packageId=org.mediahub.mediaseed", token=managed["token"], coalesce=request.method in {"GET", "HEAD"})
            if public.get("id") != managed["user_id"] or public.get("role") not in {"administrator", "user", "viewer"}:
                raise HTTPException(403, "MediaHub access denied")
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path not in {"/api/auth/logout", "/api/auth/preferences", "/api/auth/navigation"}:
                if public["role"] == "viewer":
                    raise HTTPException(403, "Read-only app access")
                tracker_admin = request.url.path == "/api/trackers" or (request.url.path.startswith("/api/trackers/") and request.method == "PUT")
                if public["role"] != "administrator" and (tracker_admin or request.url.path in {"/api/vpn", "/api/download-settings", "/api/rss-settings"} or request.url.path.startswith(("/api/accounts", "/api/feeds", "/api/settings", "/api/runtime", "/api/ratio-scout"))):
                    raise HTTPException(403, "App administrator access required")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(request.headers.get("X-MediaSeed-CSRF", ""), session["csrf"]):
            raise HTTPException(403, "Session verification failed")
        return {**dict(session), **public}

    install_theme_preferences(app, database, authorized, bool(hub_url))

    @app.get("/auth/hub/start")
    async def hub_start(request: Request):
        if not hub_url:
            raise HTTPException(409, "MediaHub SSO is not configured")
        origin = await managed_origin(request)
        if request.headers.get("host", "").lower() != urlsplit(origin).netloc.lower():
            return RedirectResponse(origin + "/auth/hub/start", status_code=303, headers={"Cache-Control": "no-store"})
        state, verifier = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with database() as connection:
            connection.execute("DELETE FROM hub_attempts WHERE expires<=?", (time.time(),))
            if connection.execute("SELECT COUNT(*) FROM hub_attempts").fetchone()[0] >= 1000:
                raise HTTPException(429, "Try again later")
            connection.execute("INSERT INTO hub_attempts VALUES (?,?,?)", (hashlib.sha256(state.encode()).hexdigest(), verifier, time.time() + 300))
        try:
            local = origin == base and ipaddress.ip_address(urlsplit(origin).hostname).is_private
        except ValueError:
            local = False
        browser_origin = hub_url if local else hub_browser_url
        response = RedirectResponse(browser_origin + "/api/v1/sso/authorize?" + urlencode({"packageId": "org.mediahub.mediaseed", "state": state, "challenge": challenge, "appOrigin": origin}), status_code=303)
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
        return await credit.request(path, method, payload, files)

    async def raw_qbit(path, method="GET", payload=None, files=None):
        if method == "POST" and move_manager is not None:
            move_manager.guard(path, payload)
        if vpn_url:
            selection = vpn_selection()
            if selection["provider"] != "mediavpn" or selection["disconnect_pending"]:
                raise HTTPException(409, "Choose MediaVPN under VPN in MediaSeed first. Downloads are blocked.")
            if files:
                query = urlencode({"ratio": float(payload.get("ratioLimit", 1)), "savepath": payload.get("savepath", "/downloads"), "category": payload.get("category", ""), "start": "true" if payload.get("stopped") == "false" else "false", "tags": payload.get("tags", "")})
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
        return {"status": "healthy", "version": "0.1.28"}

    @app.get("/api/auth/status")
    async def status(request: Request):
        if hub_url:
            return {"needsSetup": False, "managed": True, "loginUrl": await managed_origin(request) + "/auth/hub/start"}
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
            connection.execute("INSERT INTO accounts (username,password,language) VALUES (?,?,?)", (body.username, HASHER.hash(body.password), body.language))
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
    def me(request: Request, session=Depends(authorized)):
        try:
            local = request.headers.get("host", "").lower() == urlsplit(base).netloc.lower() and ipaddress.ip_address(urlsplit(base).hostname).is_private
        except ValueError:
            local = False
        return {"hubHomeUrl": (hub_url if local else hub_browser_url) if hub_url else None, "managed": bool(hub_url), "themeOverride": theme_override(database, session.get("id")), "navigationOverride": navigation_override(database, session.get("id")), **{key: session[key] for key in ("csrf", "id", "username", "role", "language", "appearance", "theme") if key in session}}

    @app.get("/api/storage")
    def storage(session=Depends(authorized)):
        return storage_catalog.snapshot()

    @app.get("/api/storage/folders")
    def storage_folders(location: str = Query(default="local", max_length=64), folder: str = Query(default="", max_length=1000), session=Depends(authorized)):
        return storage_catalog.folders(location, folder)

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
        return await vpn_status(discover=True)

    @app.put("/api/vpn")
    async def configure_vpn(body: VpnSettings, session=Depends(authorized)):
        if body.enabled and (body.provider != "mediavpn" or not body.profileId):
            raise HTTPException(422, "Choose MediaVPN and a profile first.")
        if body.enabled and (not vpn_url or not vpn_token_file):
            raise HTTPException(409, "MediaVPN is not available in this installation.")
        async with vpn_lock:
            if body.enabled:
                save_vpn("mediavpn", body.profileId)
                await vpn_request("settings", "PUT", {"enabled": True, "profileId": body.profileId})
            else:
                previous = vpn_selection()
                save_vpn(None, previous["profile_id"], pending=bool(vpn_url and vpn_token_file))
                await disconnect_vpn()
        return await vpn_status(discover=True)

    async def read_torrents():
        response = await qbit("/api/v2/torrents/info", payload={"limit": 500})
        allowed = {"hash", "name", "progress", "state", "dlspeed", "upspeed", "ratio", "eta", "size", "seeding_time", "ratio_limit", "category", "num_seeds", "num_leechs", "num_complete", "num_incomplete", "downloaded", "uploaded", "added_on", "completion_on"}
        try:
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError()
            result = []
            swarm_samples = app.state.swarm.snapshot(rows[:500])
            credits = credit.observe_many(rows[:500])
            cleanup_rules = {identifier: rule for rule in cleanup.records() for identifier in rule["hashes"]}
            for row, credited in zip(rows[:500], credits):
                public = {key: value for key, value in row.items() if key in allowed}
                identifier = str(row.get("hash", ""))
                sample = swarm_samples.get(identifier.lower())
                if sample is not None:
                    public["swarm"] = sample
                rule = cleanup_rules.get(identifier.lower())
                public["cleanup"] = ({key: rule[key] for key in ("mode", "hours", "ratio", "delete_files", "status")}
                                     if rule and cleanup_identity(rule, row) else {"mode": "never", "status": "disabled"})
                if re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", identifier):
                    public.update(credited)
                    cached = data / "cover-cache" / (hashlib.sha256(identifier.lower().encode()).hexdigest() + ".img")
                    if cached.is_file():
                        public["cover"] = "/api/covers/" + identifier.lower()
                result.append(public)
            return result
        except (ValueError, AttributeError):
            raise HTTPException(503, "Invalid torrent client response") from None

    @app.get("/api/torrents")
    async def torrents(session=Depends(authorized)):
        return await read_torrents()

    @app.get("/api/dashboard")
    async def dashboard(session=Depends(authorized)):
        async def snapshot():
            try:
                return await read_torrents()
            except HTTPException:
                return None

        # One authorized request and one bounded torrent read for list, queue and traffic.
        # VPN evidence remains fresh; no readiness or action results are cached.
        observed, rows, queue = await asyncio.gather(vpn_status(), snapshot(), queue_observation())
        return {"runtime": observed, "torrents": rows, "queue": queue_summary(rows, queue.get("maxActiveDownloads")) if rows is not None else None}

    queue_cache = {}
    queue_lock = asyncio.Lock()

    async def queue_observation(force=False):
        async with queue_lock:
            if not force and time.time() - queue_cache.get("checkedAt", 0) < 60:
                return queue_cache
            try:
                preferences = (await qbit("/api/v2/app/preferences")).json()
                limit = preferences.get("max_active_downloads")
                if type(limit) is not int or limit < -1:
                    raise ValueError()
                result = {"maxActiveDownloads": limit if preferences.get("queueing_enabled") else -1,
                          "queueingEnabled": preferences.get("queueing_enabled") is True}
            except (HTTPException, ValueError, AttributeError):
                result = {"maxActiveDownloads": None, "queueingEnabled": None}
            queue_cache.clear()
            queue_cache.update(result, checkedAt=time.time())
            return queue_cache

    @app.get("/api/download-settings")
    async def download_settings(session=Depends(authorized)):
        result = await queue_observation(force=True)
        if result["maxActiveDownloads"] is None:
            raise HTTPException(503, "Download settings are unavailable. Connect the torrent client and try again.")
        return result

    @app.put("/api/download-settings")
    async def save_download_settings(body: DownloadSettings, session=Depends(authorized)):
        async with credit.lock:
            if vpn_url:
                observed = await vpn_status()
                if "queue-settings" not in observed.get("torrentCapabilities", []):
                    raise HTTPException(409, "Changing the download limit requires MediaVPN 0.2.18 or newer. Update MediaVPN separately.")
                await vpn_request("queue", "PUT", body.model_dump())
            else:
                await qbit("/api/v2/app/setPreferences", "POST", {"json": json.dumps({"max_active_downloads": body.maxActiveDownloads, "queueing_enabled": True})})
            result = await queue_observation(force=True)
            if result["maxActiveDownloads"] != body.maxActiveDownloads or not result["queueingEnabled"]:
                raise HTTPException(503, "The download limit could not be verified. Refresh the settings before trying again.")
            return result

    @app.get("/api/rss-settings")
    async def rss_settings(session=Depends(authorized)):
        try:
            preferences = (await qbit("/api/v2/app/preferences")).json()
        except ValueError:
            raise HTTPException(503, "RSS settings are unavailable. Connect the torrent client and try again.") from None
        minutes = preferences.get("rss_refresh_interval") if isinstance(preferences, dict) else None
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise HTTPException(503, "RSS settings are unavailable. Connect the torrent client and try again.")
        return {"rssRefreshMinutes": minutes}

    @app.put("/api/rss-settings")
    async def save_rss_settings(body: RssSettings, session=Depends(authorized)):
        async with credit.lock:
            if vpn_url:
                observed = await vpn_status()
                if "rss-settings" not in observed.get("torrentCapabilities", []):
                    raise HTTPException(409, "Changing the RSS interval requires MediaVPN 0.2.19 or newer. Update MediaVPN separately.")
                await vpn_request("rss", "PUT", body.model_dump())
            else:
                await qbit("/api/v2/app/setPreferences", "POST", {"json": json.dumps({"rss_refresh_interval": body.rssRefreshMinutes})})
            result = await rss_settings(session=session)
            if result["rssRefreshMinutes"] != body.rssRefreshMinutes:
                raise HTTPException(503, "The RSS interval could not be verified. Refresh the settings before trying again.")
            return result

    @app.get("/api/hub/overview")
    async def hub_overview(request: Request):
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if not hub_url or scheme.lower() != "bearer" or not 40 <= len(token) <= 128:
            raise HTTPException(401, "MediaHub overview authorization required")
        identity = await hub_request("session?packageId=org.mediahub.mediaseed", token=token)
        if identity.get("scope") != "overview" or identity.get("role") not in {"administrator", "user", "viewer"}:
            raise HTTPException(403, "MediaHub overview access denied")
        snapshot = await dashboard(session=identity)
        observed = snapshot["runtime"]
        return {"checkedAt": time.time(), "ready": bool(observed.get("downloadsEnabled")), "country": observed.get("country"), "torrents": snapshot["torrents"], "queue": snapshot["queue"], "trackers": app.state.tracker_overview()}

    @app.post("/api/torrents")
    async def add(body: Magnet, session=Depends(authorized)):
        params = parse_qs(urlsplit(body.url).query, keep_blank_values=True, max_num_fields=100)
        ignored = any(key in params for key in ("xs", "as", "ws"))
        # Keep the info hash and trackers without allowing arbitrary HTTP file fetches.
        magnet = "magnet:?" + urlencode({key: value for key, value in params.items() if key not in {"xs", "as", "ws"}}, doseq=True) if ignored else body.url
        result = await cleanup.add(add_stopped, body.cleanup, body.ratio, magnet=magnet, start=body.start, vpn_status=vpn_status, savepath=storage_catalog.destination(body.location, body.folder))
        if ignored:
            result["webSourcesIgnored"] = True
        return result

    @app.post("/api/torrents/upload")
    async def upload(request: Request, filename: str = Query(min_length=1, max_length=255), ratio: float = Query(default=1, ge=1, le=1000, allow_inf_nan=False), start: bool = Query(default=False), cleanup_mode: Literal["never", "time", "ratio", "both", "either"] = "never", cleanup_hours: float = Query(default=49, ge=1/60, le=87600, allow_inf_nan=False), cleanup_ratio: float = Query(default=1, ge=1, le=1000, allow_inf_nan=False), cleanup_delete_files: bool = False, location: str = Query(default="local", max_length=64), folder: str = Query(default="", max_length=1000), session=Depends(authorized)):
        savepath = storage_catalog.destination(location, folder)
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
        policy = CleanupPolicy(mode=cleanup_mode, hours=cleanup_hours, ratio=cleanup_ratio, delete_files=cleanup_delete_files)
        return await cleanup.add(add_stopped, policy, ratio, content=bytes(content), start=start, vpn_status=vpn_status, savepath=savepath)

    @app.post("/api/torrents/{action}")
    async def action(action: str, body: TorrentAction, session=Depends(authorized)):
        if action == "recheck":
            if body.delete_files:
                raise HTTPException(422, "File checking cannot delete files")
            if vpn_url:
                observed = await vpn_status()
                if "recheck" not in observed.get("torrentCapabilities", []):
                    raise HTTPException(409, "File checking requires MediaVPN 0.2.18 or newer. Update MediaVPN separately.")
            async with credit.lock:
                async def current():
                    response = await qbit("/api/v2/torrents/info", payload={"hashes": body.torrent_hash, "limit": 1})
                    try:
                        rows = response.json()
                    except ValueError:
                        raise HTTPException(503, "Invalid torrent client response") from None
                    if not isinstance(rows, list):
                        raise HTTPException(503, "Invalid torrent client response")
                    row = next((row for row in rows if isinstance(row, dict) and row.get("hash", "").lower() == body.torrent_hash.lower()), None)
                    if row is None:
                        raise HTTPException(404, "Torrent not found")
                    return row
                row = await current()
                if row.get("state", "").startswith("checking") or row.get("state") == "moving":
                    raise HTTPException(409, "The torrent is already checking or moving files. Wait until it finishes.")
                if row.get("state") in {"metaDL", "forcedMetaDL"}:
                    raise HTTPException(409, "Torrent metadata must be available before checking files")
                stopped = await qbit("/api/v2/torrents/stop", "POST", {"hashes": body.torrent_hash})
                if stopped.status_code not in {200, 204}:
                    raise HTTPException(503, "The torrent could not be stopped. File checking was not started.")
                for attempt in range(20):
                    row = await current()
                    # qBittorrent reports error/missingFiles ahead of its paused flag.
                    if row.get("state", "").startswith(("paused", "stopped")) or row.get("state") in {"error", "missingFiles"}:
                        break
                    await asyncio.sleep(0.25)
                else:
                    raise HTTPException(409, "The torrent was not confirmed stopped. File checking was not started.")
                checked = await qbit("/api/v2/torrents/recheck", "POST", {"hashes": body.torrent_hash})
                if checked.status_code not in {200, 204}:
                    raise HTTPException(503, "File checking was not accepted. The torrent remains paused.")
            return {"accepted": True, "stopped": True}
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
                credited = credit.observe({"hash": body.torrent_hash, "uploaded": observed.get("total_uploaded"), "downloaded": observed.get("total_downloaded"), "ratio": observed.get("share_ratio"), "added_on": observed.get("addition_date")})
                if float(credited.get("credited_ratio", observed.get("share_ratio", 0))) < float(credited.get("ratio_limit", 1)) and int(credited.get("seeding_time", observed.get("seeding_time", 0))) < 49 * 3600:
                    raise HTTPException(409, "Early stop: credited ratio is below the selected goal and seeding time is below 49 hours. Explicit confirmation required.")
            except (ValueError, AttributeError, TypeError):
                raise HTTPException(503, "Cannot verify seeding progress") from None
        path = "delete" if action == "remove" else "stop"
        async with cleanup.lock:
            await qbit("/api/v2/torrents/" + path, "POST", {"hashes": body.torrent_hash, "deleteFiles": "true" if action == "remove" and body.delete_files else "false"})
            if action == "remove":
                cleanup.forget(body.torrent_hash)
        return {"accepted": True} if action == "remove" else {"completed": True}

    @app.get("/api/torrents/{torrent_hash}/presence")
    async def presence(torrent_hash: str, session=Depends(authorized)):
        if not re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", torrent_hash):
            raise HTTPException(422, "Invalid torrent hash")
        response = await qbit("/api/v2/torrents/info", payload={"hashes": torrent_hash})
        try:
            rows = response.json()
            if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("hash"), str) for row in rows):
                raise ValueError()
            return {"present": any(row["hash"].lower() == torrent_hash.lower() for row in rows)}
        except (ValueError, TypeError):
            raise HTTPException(503, "Cannot verify torrent removal") from None

    def seeding_configured():
        with database() as db:
            return bool(hub_url) or bool(db.execute("SELECT 1 FROM accounts LIMIT 1").fetchone())

    credit = TrackerCredit(database, data, raw_qbit, configured=seeding_configured)
    cleanup = CleanupManager(database, qbit, vpn_status, credit=credit)
    credit.cleanup = cleanup.public

    @app.post("/api/torrents/{identifier}/cleanup/cancel")
    async def cancel_cleanup(identifier: str, session=Depends(authorized)):
        if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", identifier):
            raise HTTPException(422, "Invalid torrent hash")
        return await cleanup.cancel(identifier.lower())

    from mediaseed.moves import install_moves

    async def move_capable():
        if vpn_url:
            observed = await vpn_status()
            if "move-location" not in observed.get("torrentCapabilities", []):
                raise HTTPException(409, "Update MediaVPN to 0.2.33 or newer to move torrent files")
            if not observed.get("ready"):
                raise HTTPException(409, "The torrent client and its VPN must be ready before moving files")

    move_manager = install_moves(app, authorized, database, qbit, storage_catalog, cleanup, credit, move_capable)

    background = install_features(app, authorized, database, qbit, vpn_status=vpn_status, data_dir=data, cleanup=cleanup, credit=credit, storage=storage_catalog)

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
