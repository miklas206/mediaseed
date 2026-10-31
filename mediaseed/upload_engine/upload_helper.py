"""Local upload preparation. Never posts to a tracker or changes source media."""

import base64
import errno
import logging
import traceback
from contextlib import nullcontext
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from mediaseed.upload_comparison import measured_facts
from mediaseed.upload_engine.media_tags import ALIASES as TRACK_LANGUAGE_ALIASES, subtitle_languages, tag_summary
from mediaseed.upload_engine.autofacts import title_complete, complete_title
from mediaseed.upload_engine.releases import release_name, preserve_group_title, original_group
from mediaseed.upload_engine.tracker import TrackerSettings, normalize_announce
from mediaseed.upload_engine.source_service import service_info, metadata_services
from mediaseed.upload_engine.description import generate_description, subtitle_text
from mediaseed.upload_engine.nfo import generate_nfo, plain_description, readable_credits
from mediaseed.upload_engine.mediainfo_report import combined_report
from mediaseed.upload_engine.metadata import MetadataClient, MetadataError
from mediaseed.upload_engine.episode_plan import plan_episodes, uploads_for_plan, episode_uploads
from mediaseed.upload_engine.artifact_exports import ArtifactExports
from mediaseed.upload_engine.release_naming import NamingError, release_names, suggested_folder_name, tagged_folder_name, dotted_folder_name, folder_suggestions

VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov"}
SUBS = {".srt", ".ass", ".ssa", ".vtt"}
LANGUAGES = {"da": "dan", "sv": "swe", "no": "nor", "fi": "fin", "en": "eng"}
ALIASES = {alias: code for alias, code in TRACK_LANGUAGE_ALIASES.items() if code in LANGUAGES}
RULE_VERSION = "user-supplied-2026-10-20"


class Subtitle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str = Field(min_length=1, max_length=1000)
    video: str = Field(min_length=1, max_length=1000)
    language: Literal["da", "sv", "no", "fi", "en"]


class EpisodeReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file: str = Field(min_length=1, max_length=1000)
    season: int = Field(ge=0, le=10000)
    episode: int = Field(ge=0, le=10000)
    episode_title: str = Field(default='', max_length=300)
    release_title: str = Field(default='', max_length=240)
    numbering_source: Literal['TMDB', 'TheTVDB', 'Manual'] = 'TMDB'


class UploadOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    file: str = Field(min_length=1, max_length=1000)
    title: str | None = Field(default=None, max_length=240)
    synopsis: str | None = Field(default=None, max_length=12000)
    description_override: str | None = Field(default=None, max_length=50000)
    source_service: Literal['', 'auto', 'none', 'other', 'iP', 'DRTV', 'TV2', 'NF', 'AMZN', 'DSNP', 'ATVP'] | None = None
    source_credit: str | None = Field(default=None, min_length=1, max_length=2000)
    source_type: Literal['WEB', 'WEB-DL', 'WEBRip', 'BluRay', 'HDTV', 'DVD', 'Other'] | None = None
    edition: Literal['', 'EXTENDED', 'EXTENDED.EDITION', 'UNRATED', 'THEATRICAL', 'FINAL.CUT', 'DIRECTORS.CUT', 'REDUX'] | None = None
    release_format: Literal['', 'REMUX'] | None = None
    release_type: Literal['unknown', 'p2p', 'scene'] | None = None
    screenshots: bool | None = None
    screenshot_count: int | None = Field(default=None, ge=1, le=5, strict=True)
    destination_layout: Literal['folder', 'flat', 'series', 'episode', 'series_episodes'] | None = None
    destination_name: str | None = Field(default=None, max_length=200)
    series_name: str | None = Field(default=None, max_length=200)


class Preparation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    location: str = Field(default="local", max_length=64)
    folder: str = Field(default="", max_length=1000)
    profile: Literal["generic", "nordicbytes"] = "nordicbytes"
    title: str = Field(min_length=1, max_length=240)
    kind: Literal["movie", "episode", "season", "series"] = "movie"
    tmdb: int = Field(ge=1, le=2147483647)
    synopsis: str = Field(default="", max_length=12000)
    description_override: str | None = Field(default=None, max_length=50000)
    source_service: Literal['', 'auto', 'none', 'other', 'iP', 'DRTV', 'TV2', 'NF', 'AMZN', 'DSNP', 'ATVP'] = ''
    source_credit: str = Field(min_length=1, max_length=2000)
    source_type: Literal["WEB", "WEB-DL", "WEBRip", "BluRay", "HDTV", "DVD", "Other"] = "WEB-DL"
    videos: list[str] = Field(min_length=1, max_length=200)
    subtitles: list[Subtitle] = Field(default_factory=list, max_length=800)
    subtitles_verified: bool = False
    source_verified: bool = False
    complete: bool = False
    duplicates_checked: bool = False
    edition: Literal['', 'EXTENDED', 'EXTENDED.EDITION', 'UNRATED', 'THEATRICAL', 'FINAL.CUT', 'DIRECTORS.CUT', 'REDUX'] = ''
    release_format: Literal['', 'REMUX'] = ''
    release_type: Literal["unknown", "p2p", "scene"] = "unknown"
    screenshots: bool = True
    screenshot_count: int = Field(default=2, ge=1, le=5, strict=True)
    include_nfo: bool = False  # Existing queued jobs retain their original payload.
    announce: SecretStr | None = None
    torrent_mode: Literal['tracker', 'local'] = 'tracker'
    media_mode: Literal['copy', 'source', 'release', 'in_place'] = 'copy'  # Older queued jobs keep their original behavior.
    release_owner: Literal['self', 'other'] = 'self'
    original_group: str = Field(default='', max_length=32, pattern=r'^(?:[A-Za-z0-9][A-Za-z0-9_-]{0,31})?$')
    catalogue_title: str = Field(default='', max_length=300)
    upload_strategy: Literal['single', 'auto'] = 'single'
    episode_overrides: list[EpisodeReference] = Field(default_factory=list, max_length=200)
    destination_location: str | None = Field(default=None,max_length=64)
    destination_folder: str = Field(default="",max_length=1000)
    destination_layout: Literal['folder', 'flat', 'series', 'episode', 'series_episodes'] = 'folder'
    destination_name: str = Field(default='', max_length=200)
    series_name: str = Field(default='', max_length=200)
    upload_options: list[UploadOptions] = Field(default_factory=list, max_length=200)

    @model_validator(mode='after')
    def preserve_other_release(self):
        if self.release_owner == 'other':
            if not self.original_group:
                raise ValueError('Angiv den oprindelige releasegruppe, fx Ghost')
            if self.media_mode in {'release','in_place'} or self.subtitles:
                raise ValueError('En anden gruppes release skal bevare originalfilerne uden MDTECH-omdøbning eller nye tekster')
        return self

    @field_validator("title", "source_credit", "synopsis")
    @classmethod
    def plain_text(cls, value):
        if any(ord(c) < 32 and c not in "\n\r\t" for c in value) or "[" in value or "]" in value:
            raise ValueError("Use plain text without BBCode brackets")
        return value.strip()

    @field_validator('destination_name','series_name')
    @classmethod
    def folder_name(cls, value):
        value = value.strip()
        if len(value.encode('utf-8')) > 200:
            raise ValueError('Mappenavnet er for langt')
        if value and (value.startswith('.') or any(c in value for c in '/\\:<>"|?*') or any(ord(c) < 32 for c in value)):
            raise ValueError('Vælg et almindeligt mappenavn uden skråstreger')
        return value

    @field_validator('description_override')
    @classmethod
    def description_text(cls, value):
        if value is not None and any(ord(c) < 32 and c not in '\n\r\t' for c in value):
            raise ValueError('Beskrivelsen indeholder ugyldige kontroltegn')
        return value


def apply_upload_options(body, children):
    options = {o.file: o for o in body.upload_options}
    if len(options) != len(body.upload_options) or any(file not in body.videos for file in options):
        raise ValueError('Hvert indstillingsvalg skal høre til én valgt video')
    result = []
    for child in children:
        selected = options.get(child.videos[0]) if len(child.videos) == 1 else None
        update = selected.model_dump(exclude={'file'}, exclude_none=True) if selected else {}
        if update.get('title') and child.media_mode not in {'release','in_place'} and child.kind == 'episode' and child.episode_overrides:
            reference = child.episode_overrides[0]
            markers = re.findall(r'(?i)S(\d{1,3})E(\d{1,4})(?!\d)', update['title'])
            if len(markers) != 1 or tuple(map(int, markers[0])) != (reference.season, reference.episode):
                raise ValueError('Uploadtitlen skal indeholde det valgte SxxEyy-nummer')
        # Revalidate user fields; technical release names are still measured later.
        prepared = Preparation.model_validate({**child.model_dump(), **update, 'upload_options': []})
        if prepared.release_owner == 'other':
            prepared.title = preserve_group_title(prepared, prepared.title)
            prepared.source_credit = re.sub(r'(?im)^Original group not identified[ \t]*(?:\r?\n|$)', '', prepared.source_credit).strip()
            if not re.search(r'(?im)^Original group:\s*'+re.escape(prepared.original_group)+r'\s*$', prepared.source_credit):
                prepared.source_credit = (prepared.source_credit[:1900] + '\nOriginal group: ' + prepared.original_group)
        if prepared.destination_layout == 'series' and prepared.destination_name:
            prepared.destination_name = tagged_folder_name(prepared.destination_name, prepared.original_group if prepared.release_owner == 'other' else 'MDTECH')
        if prepared.destination_layout in {'episode','series_episodes'}:
            if prepared.destination_name:prepared.destination_name=dotted_folder_name(prepared.destination_name, prepared.original_group if prepared.release_owner == 'other' else 'MDTECH')
            if prepared.series_name:prepared.series_name=dotted_folder_name(prepared.series_name, prepared.original_group if prepared.release_owner == 'other' else 'MDTECH')
        result.append(prepared)
    return result


class DescriptionRequest(Preparation):
    tmdb: int | None = Field(default=None, ge=1, le=2147483647)


def safe_file(root, relative, extensions):
    if not isinstance(relative, str) or len(relative) > 1000 or "\\" in relative:
        raise ValueError("Ugyldigt filvalg")
    parts = relative.split("/")
    if any(not p or p.startswith(".") or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError("Vælg en almindelig fil i mediemappen")
    path = root.joinpath(*parts)
    if path.suffix.lower() not in extensions or path.resolve(strict=True) != path or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Filen er utilgængelig eller en genvej")
    return path


def inspect_folder(root, recursive=True):
    result = []
    scanned = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        scanned += len(dirs) + len(files)
        if scanned > 10000:
            raise ValueError("Mappen er for stor. Vælg en mere præcis undermappe")
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.casefold() not in {"sample", "samples"} and not (Path(current) / d).is_symlink())
        if not recursive or len(Path(current).relative_to(root).parts) > 8:
            dirs[:] = []
        for name in sorted(files):
            path = Path(current) / name
            if name.startswith(".") or path.suffix.lower() not in VIDEO | SUBS or path.stem.casefold() == "sample" or path.stem.casefold().endswith(".sample"):
                continue
            relative = path.relative_to(root).as_posix()
            try:
                safe_file(root, relative, VIDEO | SUBS)
                result.append({"file": relative, "size": path.stat().st_size, "kind": "video" if path.suffix.lower() in VIDEO else "subtitle", "language": next((ALIASES[t.lower()] for t in re.split(r"[._ -]", path.stem) if t.lower() in ALIASES), "")})
            except (ValueError, OSError):
                continue
            if len(result) > 1000:
                raise ValueError("Mappen har over 1000 mediefiler. Vælg en mindre undermappe")
    return result


def excluded_sidecars(root, inputs):
    """Report omitted sidecars without reading, modifying or following source files."""
    omitted = []
    scanned = 0
    for directory in sorted({path.parent for path, _ in inputs}):
        for path in sorted(directory.iterdir()):
            scanned += 1
            if scanned > 10000:
                raise ValueError("For mange sidefiler. Vælg en mere præcis mediemappe")
            if path.name.startswith("."):
                continue
            sample = path.stem.casefold() == "sample" or path.stem.casefold().endswith(".sample") or path.name.casefold() in {"sample", "samples"}
            if path.is_symlink():
                reason = "Genvej udeladt"
            elif sample:
                reason = "SAMPLE udeladt (regel 1.9)"
            elif path.is_dir() or path.suffix.lower() in VIDEO:
                continue
            elif path.suffix.lower() in SUBS:
                reason = "Ingen løse undertekster; kun valgte tekster indlejres i videoen"
            elif path.suffix.lower() == ".nfo":
                reason = "Original NFO er kun et separat uploadbilag (regel 1.5 / 1.9)"
            else:
                reason = "Sidefil udeladt; TXT, reklame, arkiver og andre uvedkommende filer medtages ikke (regel 1.6 / 1.14)"
            omitted.append({"file": path.relative_to(root).as_posix(), "reason": reason})
    return omitted


def verify_payload(content, files, nfo_name=None):
    """Allow the exact selected videos and the explicitly generated NFO."""
    expected = {relative for _, relative in files}
    actual = set()
    for path in content.rglob("*"):
        if path.is_symlink():
            raise ValueError("Uploadkopien indeholder en genvej")
        if path.is_file():
            relative = path.relative_to(content).as_posix()
            if path.suffix.lower() not in VIDEO and relative != nfo_name:
                raise ValueError("Uploadkopien indeholder en uvedkommende fil")
            actual.add(relative)
    if actual != expected:
        raise ValueError("Uploadkopiens indhold stemmer ikke med de valgte videoer")


def command(args, timeout=120):
    # Bound output in memory and never expose tool stderr / local paths.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            # Minimal Debian hosts may advertise an ungenerated en_US locale.
            # C.UTF-8 is provided by the runtime and keeps MKVToolNix Unicode-safe.
            result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=timeout, check=False,
                                    env={**os.environ, 'LC_ALL': 'C.UTF-8', 'LANG': 'C.UTF-8'})
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Medieværktøjet kunne ikke køres eller overskred tidsgrænsen") from None
        if result.returncode not in ({0, 1} if Path(args[0]).name == "mkvmerge" else {0}):
            raise ValueError("Medieværktøjet afviste en fil. Kontrollér filformat og underteksternes kodning")
        out.seek(0, 2)
        if out.tell() > 4 * 1024**2:
            raise ValueError("Medierapporten er for stor")
        out.seek(0)
        return out.read().decode("utf-8", "replace")


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, str):
        value = value.encode("utf-8")
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(x) for x in value) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b"e"


def make_torrent(files, name, announce, progress=None, *, profile="generic", single_file=False):
    if single_file and (len(files) != 1 or name != files[0][0].name):
        raise ValueError("En enkeltfils-torrent skal bruge originalfilens præcise navn")
    total = sum(p.stat().st_size for p, _ in files)
    piece_length = 256 * 1024
    while math.ceil(total / piece_length) * 20 > 800000:
        piece_length *= 2
    pieces, buffer = [], bytearray()
    completed, last_update = 0, 0.0
    for path, relative in files:
        before = path.stat()
        with path.open("rb") as stream:
            while chunk := stream.read(piece_length - len(buffer)):
                buffer.extend(chunk)
                completed += len(chunk)
                if progress and (time.monotonic() - last_update >= 5 or completed == total):
                    progress(completed, total)
                    last_update = time.monotonic()
                if len(buffer) == piece_length:
                    pieces.append(hashlib.sha1(buffer).digest())
                    buffer.clear()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("En fil blev ændret under hashing. Prøv igen når filerne er færdige")
    if buffer:
        pieces.append(hashlib.sha1(buffer).digest())
    info = {"name": name, "private": 1, "piece length": piece_length, "pieces": b"".join(pieces), "files": [{"length": p.stat().st_size, "path": rel.split("/")} for p, rel in files]}
    if single_file:
        info.pop('files')
        info['length'] = files[0][0].stat().st_size
    if profile == "nordicbytes":
        info["source"] = "NordicBytes"
    result = bencode({"announce": announce, "created by": "MediaUpload", "info": info})
    if len(result) > 1024**2:
        raise ValueError("Torrentfilen overskrider NordicBytes-grænsen på 1 MiB")
    return result


def decode_torrent(data):
    """Read our bounded torrent artifacts without changing their binary fields."""
    if not data or len(data)>1024**2:raise ValueError('Invalid torrent size')
    nodes=0
    def read(index,depth=0):
        nonlocal nodes
        nodes+=1
        if depth>32 or nodes>100000 or index>=len(data):raise ValueError('Invalid torrent nesting')
        marker=data[index:index+1]
        if marker==b'i':
            end=data.find(b'e',index+1)
            value=data[index+1:end] if end>=0 else b''
            if not re.fullmatch(rb'0|-?[1-9][0-9]{0,19}',value):raise ValueError('Invalid torrent integer')
            return int(value),end+1
        if marker in {b'd',b'l'}:
            dictionary=marker==b'd';result={} if dictionary else [];index+=1
            while index<len(data) and data[index:index+1]!=b'e':
                value,index=read(index,depth+1)
                if dictionary:
                    if not isinstance(value,bytes) or value in result:raise ValueError('Invalid torrent key')
                    child,index=read(index,depth+1);result[value]=child
                else:result.append(value)
            if index>=len(data):raise ValueError('Truncated torrent')
            return result,index+1
        colon=data.find(b':',index)
        size=data[index:colon] if colon>=0 else b''
        if not re.fullmatch(rb'0|[1-9][0-9]{0,7}',size):raise ValueError('Invalid torrent string')
        end=colon+1+int(size)
        if end>len(data):raise ValueError('Truncated torrent')
        return data[colon+1:end],end
    torrent,end=read(0)
    if end!=len(data) or not isinstance(torrent,dict) or not isinstance(torrent.get(b'info'),dict):raise ValueError('Invalid torrent structure')
    return torrent


def nordicbytes_download(data):
    """Correct old NordicBytes downloads, preserving saved artifacts and media."""
    torrent=decode_torrent(data)
    info=torrent[b'info']
    source=info.get(b'source')
    if info.get(b'private')!=1 or source not in (None,b'NordicBytes'):raise ValueError('Unexpected NordicBytes torrent metadata')
    if source==b'NordicBytes':return data
    info[b'source']=b'NordicBytes'
    corrected=bencode(torrent)
    if len(corrected)>1024**2:raise ValueError('Corrected torrent exceeds size limit')
    return corrected


def track_languages(tracks):
    return subtitle_languages(tracks)


def evaluate(body, videos, nfo_count=0, *, conversion_verified=False):
    checks = []
    def add(rule, ok, text):
        checks.append({"rule": rule, "status": "ok" if ok else "missing", "text": text})
    add("1.15", all(v.get("report") for v in videos), "MediaInfo fra alle endelige videofiler")
    add("2.0 / 3.0", bool(body.tmdb), "TMDB-ID angivet (identiteten skal kontrolleres i forhåndsvisningen)")
    if body.profile == "nordicbytes":
        add("1.8 / 1.10", bool(re.fullmatch(r"[A-Za-z0-9 ._-]+", body.title) and re.search(r"-[A-Za-z0-9]+$", body.title)), "Titlen bruger almindelige tegn og slutter med en gruppetag")
        nordic = bool(re.search(r"(?:^|[ ._-])nordic(?:[ ._-]|$)", body.title, re.I))
        add("1.11", not nordic or all({"da", "sv", "no", "fi"} <= set(v["languages"]) for v in videos), "NORDIC kræver DK, SE, NO og FI i hver video")
        add("1.4 / 1.13", body.source_verified, "Original kilde, releasenavne og kvalitet er kontrolleret; ingen uofficiel opskalering")
        if body.media_mode in {'release','in_place'}:
            tagged = bool(original_group(body.folder)) or any(original_group(file) for file in body.videos)
            add('1.4 MDTECH', not tagged or bool(body.subtitles) or conversion_verified, 'MDTECH kræver eget rip eller bearbejdning med originalkreditering. Et eksisterende releasetag må ikke blot udskiftes.')
        add("1.4.2 / 1.13", not body.subtitles or body.subtitles_verified, "Ekstra tekster er kontrolleret for sprog og synkronisering og er ikke maskin-/AI-oversatte")
        add("1.9", body.release_type != "unknown" and (body.release_type != "scene" or nfo_count > 0), "Scene/P2P er afklaret; Scene kræver original NFO som uploadbilag")
        add("1.12 / 8", body.duplicates_checked, "Dubletter og eventuel trumping er kontrolleret på siden")
        if body.kind in {"season", "series"}:
            add("3.3 / 3.4", body.complete, "Alle afsnit er med og færdigudsendte; en serieboks indeholder hele den afsluttede serie")
        if body.kind != "movie":
            add("3.1", bool(re.search(r"S\d{2}(?:E\d{2})?", body.title, re.I)), "TV-titlen bruger Sxx/Exx-format")
        add("2.2 / 3.6", title_complete(body.title), "Titlen indeholder opløsning, kilde og videocodec")
        resolution = re.search(r"(?:^|[ ._-])(\d{3,4})[pi](?:[ ._-]|$)", body.title, re.I)
        actual_videos = [track for video in videos for track in video.get("technical", []) if track.get("@type") == "Video"]
        if resolution and actual_videos:
            expected = int(resolution[1])
            # Cropped widescreen releases can have fewer active vertical pixels.
            widths = {720: 1280, 1080: 1920, 2160: 3840}
            matches = all(int(track.get("Height", 0)) == expected or (expected in widths and int(track.get("Width", 0)) == widths[expected] and 0 < int(track.get("Height", 0)) <= expected) for track in actual_videos)
            add("1.16", matches, "Opløsningen i titlen stemmer med alle videofiler (inklusive beskåret widescreen)")
        if actual_videos:
            formats = {str(track.get("Format", "")).upper() for track in actual_videos}
            claims_avc = bool(re.search(r"[xh][ .]?264|AVC", body.title, re.I))
            claims_hevc = bool(re.search(r"[xh][ .]?265|HEVC", body.title, re.I))
            add("1.16 codec", (not claims_avc or formats <= {"AVC"}) and (not claims_hevc or formats <= {"HEVC"}), "Videocodec i titlen stemmer med MediaInfo")
        if body.kind == "movie":
            add("2.2", bool(re.search(r"(?:19|20)\d{2}", body.title)), "Filmtitlen indeholder udgivelsesåret")
    return checks


class UploadHelper:
    def __init__(self, data, storage, database, destinations):
        self.data, self.storage, self.database = data, storage, database
        self.tracker = TrackerSettings(data)
        self.destinations = destinations
        self.artifact_exports = ArtifactExports(os.environ.get('MEDIASEED_UPLOAD_EXPORT_ROOT', ''),
            lambda content, result: nordicbytes_download(content) if result.get('profile') == 'nordicbytes' else content)
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.worker = None
        self.preview_lock = threading.Lock()
        self.recheck_lock = threading.Lock()
        self.publication_guard = nullcontext
        self.media_cache = {}
        self.metadata = MetadataClient()
        self.incoming = data / "incoming-subtitles"
        self.incoming.mkdir(exist_ok=True, mode=0o700)
        with database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS upload_jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS upload_queue (id TEXT PRIMARY KEY, request TEXT NOT NULL)")
            for interrupted in db.execute("SELECT j.id,q.request FROM upload_jobs j JOIN upload_queue q ON j.id=q.id WHERE j.state='working'").fetchall():
                pending=json.loads(interrupted['request'])
                if pending.get('operation')=='export':
                    restored=pending['result'];restored['destinationError']='Kopieringen blev afbrudt. Den oprindelige uploadpakke er bevaret. Vælg en ny destinationsmappe for at prøve igen.'
                    db.execute("UPDATE upload_jobs SET state='done',result=? WHERE id=?",(json.dumps(restored),interrupted['id']))
            db.execute("UPDATE upload_jobs SET state='interrupted',result=? WHERE state='working'", (json.dumps({"message": "Forberedelsen blev afbrudt af en genstart. Slet kladden og opret den igen."}),))
            db.execute("DELETE FROM upload_queue WHERE id NOT IN (SELECT id FROM upload_jobs WHERE state='queued')")
        (data / "mediaupload.sqlite").chmod(0o600)

    def start(self):
        if self.artifact_exports.root is not None:
            with self.database() as db:
                completed = db.execute("SELECT id,result FROM upload_jobs WHERE state='done'").fetchall()
            for row in completed:
                self.save(row['id'], 'done', json.loads(row['result']))
        self.stopping.clear()
        self.worker = threading.Thread(target=self.process_queue, name="mediaupload-queue", daemon=True)
        self.worker.start()
        self.wake.set()

    def stop(self):
        self.stopping.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=30)

    def process_queue(self):
        with self.database() as db:
            legacy=db.execute("SELECT id,owner,result FROM upload_jobs WHERE state='done'").fetchall()
        for row in legacy:
            saved=json.loads(row['result'])
            if saved.get('mediaMode')=='in_place' and saved.get('automaticChecksVersion',0)<3:
                try:self.refresh_review_checks(row['id'],row['owner'])
                except Exception as exc:logging.getLogger(__name__).warning('Conversion review update %s deferred: %s',row['id'],type(exc).__name__)
        while not self.stopping.is_set():
            self.wake.wait()
            self.wake.clear()
            while not self.stopping.is_set():
                with self.database() as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT j.id,q.request FROM upload_jobs j JOIN upload_queue q ON q.id=j.id WHERE j.state='queued' ORDER BY j.created,j.id LIMIT 1").fetchone()
                    if row is None:
                        break
                    db.execute("UPDATE upload_jobs SET state='working' WHERE id=?", (row['id'],))
                try:
                    request=json.loads(row['request'])
                    if request.get('operation')=='export':self.export_existing(row['id'],request)
                    else:
                        # 0.1.12 queued local-torrent requests predate the mode
                        # field; 0.1.13 tracker-side requests omit announce.
                        if 'torrent_mode' not in request and request.get('announce'):
                            request['torrent_mode']='local'
                        self.run(row['id'], Preparation.model_validate(request))
                except Exception:
                    self.save(row['id'], "error", {"message": "Opgaven kunne ikke klargøres. Kontrollér filerne og opret den igen."})
                finally:
                    with self.database() as db:
                        db.execute("DELETE FROM upload_queue WHERE id=?", (row['id'],))

    def owner(self, session):
        if session.get("role") == "viewer":
            raise HTTPException(403, "Uploadforberedelse kræver skriveadgang")
        return str(session.get("id") or "local")

    def subtitle_file(self, root, relative):
        if relative.startswith("@uploaded/"):
            parts = relative.split("/")
            if len(parts) != 3 or not re.fullmatch(r"[a-f0-9]{24}", parts[1]):
                raise ValueError("Ugyldig uploadet undertekst")
            return safe_file(self.incoming, "/".join(parts[1:]), SUBS)
        return safe_file(root, relative, SUBS)

    def capabilities(self):
        return {name: bool(shutil.which(name)) for name in ("mediainfo", "mkvmerge", "ffmpeg", "ffprobe")}

    def row(self, identifier, owner):
        if not re.fullmatch(r"[a-f0-9]{24}", identifier):
            raise HTTPException(404, "Kladden findes ikke")
        with self.database() as db:
            row = db.execute("SELECT * FROM upload_jobs WHERE id=? AND owner=?", (identifier, owner)).fetchone()
        if not row:
            raise HTTPException(404, "Kladden findes ikke")
        return dict(row)

    def preview(self, body):
        if body.release_owner == 'other':
            body = apply_upload_options(body, [body])[0]
        _, root = self.storage.directory(body.location, body.folder)
        videos = []
        for relative in body.videos[:20]:
            path = safe_file(root, relative, VIDEO)
            info = path.stat()
            key = (str(path), info.st_size, info.st_mtime_ns)
            cached = self.media_cache.get(key)
            if cached is None or time.monotonic() - cached[0] > 300:
                raw = json.loads(command(["mediainfo", "--Output=JSON", str(path)], timeout=20))
                tracks = raw.get("media", {}).get("track", [])
                if not any(t.get("@type") == "Video" for t in tracks):
                    raise ValueError("En valgt fil har ikke et identificeret videospor")
                if len(self.media_cache) >= 32:
                    self.media_cache.clear()
                self.media_cache[key] = (time.monotonic(), tracks)
            else:
                tracks = cached[1]
            extras = [{"file": Path(sub.file).name, "language": sub.language} for sub in body.subtitles if sub.video == relative]
            videos.append({"comparison": measured_facts(tracks,info.st_size), "file": relative, "sourceServices": sorted(metadata_services(tracks)), "technical": [{k: t[k] for k in ("@type", "Default", "Format", "Width", "Height", "FrameRate", "BitRate", "Channels", "Language", "ScanType", "HDR_Format", "HDR_Format_Compatibility", "Format_AdditionalFeatures", "Format_Commercial_IfAny", "ChannelLayout") if k in t} for t in tracks if t.get("@type") in {"Video", "Audio"}], "languages": sorted(track_languages(tracks)), "addedSubtitles": extras, "sizeBytes": info.st_size})
        metadata = self.metadata.details(body)
        naming = {}
        if body.release_owner == 'other':
            naming = {'releaseTitle': complete_title(body, videos),
                      'outputFiles': {v['file']: v['file'] for v in videos}}
        if body.media_mode in {'release','in_place'}:
            observed = [dict(v, languages=sorted(set(v['languages']) | {s['language'] for s in v['addedSubtitles']})) for v in videos]
            try:
                title, names = release_names(body, observed, metadata)
                body = body.model_copy(update={'title': title})
                naming = {'releaseTitle': title, 'outputFiles': names}
            except NamingError as exc:
                naming = {'namingError': str(exc)}
        automatic = generate_description(body.model_copy(update={'description_override': None}), videos, preview=True, metadata=metadata)
        return {"description": generate_description(body, videos, preview=True, metadata=metadata), "automaticDescription": automatic, "nfo": generate_nfo(body, videos, metadata, preview=True), "observedVideos": videos, "sourceService": service_info(body, videos), "mediaTags": tag_summary([dict(v, languages=sorted(set(v["languages"]) | {s["language"] for s in v["addedSubtitles"]})) for v in videos]), "basedOn": "observed-media-and-tmdb", "plotIncluded": metadata.get("status") == "ok", "metadata": metadata, "suggestedFolderName": suggested_folder_name(body, metadata), **folder_suggestions(body, list(naming.get("outputFiles", {}).values()) or [v["file"] for v in videos], metadata), **naming}

    def automatic_release(self, files, artifacts, original_names, manual_type="unknown"):
        names=list(original_names)
        evidence=[]
        for filename in files:
            name=release_name(filename)
            info=self.metadata.release(name)
            item={k:v for k,v in info.items() if k!='nfo'}
            item['requestedRelease']=name
            nfo=info.get('nfo')
            if nfo:
                raw=base64.b64decode(nfo['content'],validate=True)
                if len(raw)>262144:raise ValueError("Original NFO er for stor")
                existing=next((index for index,name in enumerate(names,1) if name.casefold()==nfo['name'].casefold() and (artifacts/f'original-{index:03}.nfo').is_file() and (artifacts/f'original-{index:03}.nfo').read_bytes()==raw),None)
                if existing is None:
                    names.append(nfo['name']);existing=len(names)
                (artifacts/f'original-{existing:03}.nfo').write_bytes(raw)
                item['originalNfo']=nfo['name']
                item['nfoSource']=nfo['url']
            evidence.append(item)
        all_scene=bool(evidence) and all(item.get('releaseType')=='scene' for item in evidence)
        release_type='scene' if all_scene else manual_type
        ready=all_scene and all(item.get('originalNfo') for item in evidence)
        if not all_scene:ready=release_type!='unknown' and (release_type!='scene' or bool(names))
        message='Scene fundet ved præcist srrDB-opslag; original NFO er klar som separat uploadbilag' if all_scene and ready else 'Scene/P2P er afklaret' if ready else 'Scene fundet, men original NFO kunne ikke hentes' if all_scene else 'Scene/P2P kunne ikke bekræftes automatisk. Intet match er ikke i sig selv bevis for P2P.'
        return names,release_type,{'rule':'1.9','status':'ok' if ready else 'missing','text':message},evidence

    def progress(self, identifier, body, phase, completed, total):
        self.save(identifier, "working", {"title": body.title, "message": phase, "progress": {"phase": phase, "completedBytes": completed, "totalBytes": total, "percent": round(100 * completed / total, 1) if total else 0}})

    def copy_media(self, original, target, progress):
        total, completed, last_update = original.stat().st_size, 0, 0.0
        with original.open("rb") as source, target.open("xb") as destination:
            while chunk := source.read(4 * 1024**2):
                destination.write(chunk)
                completed += len(chunk)
                if time.monotonic() - last_update >= 5 or completed == total:
                    progress(completed, total)
                    last_update = time.monotonic()

    def conversion_check(self,result,artifacts):
        if result.get('mediaMode')!='in_place' or len(result.get('videos',[]))!=1:return None
        content=Path(result.get('contentFolder',''))
        path=content if (result.get('source') or {}).get('singleFile') else content/result['videos'][0]['file']
        try:credit=result.get('description','')+'\n'+(artifacts/'captain-quack.nfo').read_text(encoding='utf-8')
        except (OSError,UnicodeError):return None
        return self.in_place.credited_conversion(self.storage,path,credit)

    def repair_conversion_nfo(self,identifier,result,artifacts,proof,previous_result):
        if not proof or result.get('mediaMode')!='in_place':return None
        video=result['videos'][0];old=video.get('originalFile');new=proof['originalFile']
        if not old:return None
        from mediaseed.upload_nfo_repair import original_file_text,subtitle_field_text,updated_torrent,NfoEdit
        from mediaseed.upload_engine.artifact_exports import attachment_stem
        nfo_path=artifacts/'captain-quack.nfo';before=nfo_path.read_bytes();text=before.decode('utf-8')
        if old!=new:text=original_file_text(text,old,new)
        text=subtitle_field_text(text,subtitle_text(video));after=text.encode('utf-8')
        if after==before:return None
        with self.in_place.conversions.database() as db:
            if db.execute('SELECT 1 FROM uploader_publications WHERE job=?',(identifier,)).fetchone():raise ValueError('Pakken er allerede sendt eller afventer kvittering; NFO og torrent bliver ikke ændret')
        target=Path(result['contentFolder']) if result['source'].get('singleFile') else Path(result['contentFolder'])/video['file']
        base=self.in_place.conversions.storage.downloads/'media/converted-movies'
        self.in_place.unseeded('/downloads/media/converted-movies/'+target.relative_to(base).as_posix())
        changes={nfo_path:after};torrent_path=artifacts/'upload.torrent';torrent_before=torrent_after=None
        if result.get('payloadNfoFile'):
            name=result['payloadNfoFile']
            if Path(name).name!=name:raise ValueError('NFO-placeringen kunne ikke bekræftes')
            payload=target.parent/name
            if payload.is_symlink() or payload.read_bytes()!=before:raise ValueError('NFO-filen i mediemappen er ændret; eksisterende ændringer bevares')
            changes[payload]=after
            if result.get('hasTorrent'):
                torrent_before=torrent_path.read_bytes();torrent_after=updated_torrent(torrent_before,target,video['file'],name,before,after)
                changes[torrent_path]=torrent_after
        exported=result.get('artifactExport')
        if exported:
            folder=Path(exported['folder']);marker=folder/'.mediaupload-export.json'
            if folder.resolve()!=folder or marker.is_symlink() or json.loads(marker.read_text()).get('job')!=identifier:raise ValueError('Uploadbilagenes ejerskab kunne ikke bekræftes')
            stem=attachment_stem(result)
            for name,previous,updated in [(stem+'.nfo',before,after),(stem+'.torrent',torrent_before,torrent_after)]:
                if previous is None:continue
                path=folder/name
                if path.is_symlink() or path.read_bytes()!=previous:raise ValueError('Et gemt uploadbilag er ændret; eksisterende ændringer bevares')
                changes[path]=updated
        video['originalFile']=new
        result['description']=result.get('description','').replace('[b]Original source files[/b]\n'+old,'[b]Original source files[/b]\n'+new)
        result['description']=re.sub(r'(?m)^Identified subtitles: .*\.$',lambda _: 'Identified subtitles: '+subtitle_text(video)+'.',result['description'])
        for name,text in [('description.bbcode.txt',result['description']),('description.txt',re.sub(r'\[/?[a-zA-Z][^\]\r\n]*\]','',result['description']))]:
            path=artifacts/name
            if path.is_file():changes[path]=text.encode('utf-8')
        result['nfoRevision']=secrets.token_hex(16)
        changes[artifacts/'checklist.json']=json.dumps({k:v for k,v in result.items() if k!='videos'},ensure_ascii=False,indent=2).encode('utf-8')
        return NfoEdit.prepare(self.in_place.root,identifier,result['nfoRevision'],changes,previous_result)

    def refresh_review_checks(self,identifier,owner):
        with self.publication_guard(),self.recheck_lock:
            row=self.row(identifier,owner)
            if row['state']!='done':raise HTTPException(409,"Vent til klargøringen er færdig")
            result=json.loads(row['result'])
            artifacts=self.data/'upload-helper'/identifier
            if not artifacts.is_dir():raise HTTPException(404,"Kladden findes ikke")
            videos=result.get('videos',[])
            if not videos:raise HTTPException(409,"Kladden mangler gemte medieoplysninger")
            for video in videos:
                tracks=video.get('comparison',{}).get('subtitles')
                if tracks is not None:video['languages']=sorted({t['language'] for t in tracks if t.get('language')})
            files=[video['file'] for video in videos]
            model=Preparation(title=result['title'],videos=files,source_credit='Original source retained',source_type='Other',tmdb=result.get('metadata',{}).get('tmdb') or 1)
            result['title']=complete_title(model,videos)
            names,release_type,release_check,evidence=self.automatic_release(files,artifacts,result.get('originalNfoFiles',[]),result.get('releaseType','unknown'))
            conversion_proof=self.conversion_check(result,artifacts)
            if conversion_proof:result['conversionProof']=conversion_proof
            else:result.pop('conversionProof',None)
            result.update(originalNfoFiles=names,releaseEvidence=evidence,releaseType=release_type,automaticChecksVersion=3,automaticChecksAt=time.time())
            for check in result['checks']:
                if check['rule']=='1.9' and (check['status']!='ok' or release_check['status']=='ok'):check.update(release_check)
                elif check['rule']=='1.4 MDTECH' and result.get('mediaMode')=='in_place':check['status']='ok' if conversion_proof else 'missing'
                elif check['rule']=='2.2 / 3.6':check['status']='ok' if title_complete(result['title']) else 'missing'
            result['readyForReview']=all(check['status']=='ok' for check in result['checks'])
            result['message']='Klar til din gennemgang' if result['readyForReview'] else 'Kladden er lavet, men der mangler kontroller'
            edit=self.repair_conversion_nfo(identifier,result,artifacts,conversion_proof,row['result'])
            try:
                with self.database() as db:
                    updated=db.execute("UPDATE upload_jobs SET result=? WHERE id=? AND owner=? AND state='done' AND result=?",(json.dumps(result,ensure_ascii=False),identifier,owner,row['result'])).rowcount
                    if not updated:raise HTTPException(409,"Kladden er ændret. Genindlæs siden")
            except Exception:
                if edit:edit.rollback()
                raise
            if edit:edit.commit()
            if not edit:(artifacts/'checklist.json').write_text(json.dumps({k:v for k,v in result.items() if k!='videos'},ensure_ascii=False,indent=2),encoding='utf-8')
            return {'updated':True,'readyForReview':result['readyForReview']}


    def run(self, identifier, body):
        artifacts = self.data / "upload-helper" / identifier
        output = None
        published_fd = None
        published_file = None
        edit = None
        try:
            _, root = self.storage.directory(body.location, body.folder)
            direct = body.media_mode in {'source','in_place'}
            flat = not direct and body.destination_layout == 'flat'
            series = not direct and body.destination_layout == 'series'
            staged = flat or series
            episode_folder = not direct and body.destination_layout in {'episode','series_episodes'}
            if episode_folder and (len(body.videos)!=1 or not body.destination_location):
                raise ValueError('Mapper pr. video kræver én fil pr. upload og en valgt destination')
            if series and (body.kind != 'episode' or not body.catalogue_title or not body.destination_location):
                raise ValueError('En fælles seriemappe kræver identificerede enkeltstående afsnit og en valgt destination')
            if staged and (len(body.videos) != 1 or not body.destination_location):
                raise ValueError('En separat episodefil kræver én video og en valgt destination')
            if direct and (body.subtitles or body.destination_location):
                raise ValueError('Direkte brug af originalfiler kan ikke kombineres med ekstra undertekster eller en kopidestination')
            destination = None if direct else self.destinations.directory(body.destination_location,body.destination_folder)[1] if body.destination_location else self.storage.output()
            inputs = [(safe_file(root, v, VIDEO), v) for v in body.videos]
            snapshots = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p, _ in inputs}
            release_files, naming_metadata = {}, None
            observed = []
            if body.media_mode in {'release','in_place'}:
                observed = []
                for original, relative in inputs:
                    tracks = json.loads(command(['mediainfo', '--Output=JSON', str(original)]))['media']['track']
                    languages = track_languages(tracks) | {s.language for s in body.subtitles if s.video == relative}
                    observed.append({'file': relative, 'technical': tracks, 'languages': sorted(languages)})
                naming_metadata = self.metadata.details(body)
                title, release_files = release_names(body, observed, naming_metadata)
                if len(set(release_files.values())) != len(inputs):
                    raise NamingError('Flere videoer får samme MDTECH-navn. Kontrollér afsnitsidentifikationen.')
                body = body.model_copy(update={'title': title})
            targets = [str(Path(v).with_suffix(".mkv")) if any(s.video == v for s in body.subtitles) else v for v in body.videos]
            if len(set(targets)) != len(targets):
                raise ValueError("Valgte filer får samme navn efter indlejring. Vælg dem i separate kladder")
            excluded = excluded_sidecars(root, inputs)
            subs = [(self.subtitle_file(root, s.file), s) for s in body.subtitles]
            total = sum(p.stat().st_size for p, _ in inputs) + sum(p.stat().st_size for p, _ in subs)
            if not direct and shutil.disk_usage(destination).free < total + max(1024**3, total // 20):
                raise ValueError("Der er ikke plads til en separat uploadkopi samt 1 GiB sikkerhedsmargin")
            exported=None
            if direct:
                content = Path(os.path.commonpath([str(p.parent) for p, _ in inputs]))
                single_file = len(inputs) == 1 and not body.include_nfo
                torrent_name = inputs[0][0].name if single_file else content.name
                seed_parent = content if single_file else content.parent
            elif staged:
                content = Path(tempfile.mkdtemp(prefix='.mediaupload-', dir=destination))
                output = content
                seed_parent = destination
            elif episode_folder:
                filenames = list(release_files.values()) or [Path(body.videos[0]).with_suffix('.mkv').name if any(s.video==body.videos[0] for s in body.subtitles) else Path(body.videos[0]).name]
                suggestions = folder_suggestions(body, filenames, naming_metadata)
                relative = body.destination_folder
                if body.destination_layout == 'series_episodes':
                    series_name = dotted_folder_name(body.series_name, body.original_group if body.release_owner == 'other' else 'MDTECH') if body.series_name else suggestions['suggestedSeriesFolderName']
                    if body.kind!='episode' or not series_name:raise ValueError('Vælg en identificeret serie til den fælles mappe')
                    self.destinations.series_directory(body.destination_location, relative, series_name, body.tmdb)
                    relative = '/'.join(filter(None,[relative,series_name]))
                episode_name = dotted_folder_name(body.destination_name, body.original_group if body.release_owner == 'other' else 'MDTECH') if body.destination_name else suggestions['suggestedEpisodeFolderName']
                content,exported=self.destinations.reserve(body.destination_location,relative,episode_name)
                exported.update(layout=body.destination_layout,folderName=episode_name)
                output=content
                seed_parent=content.parent
            elif body.destination_location:
                content,exported=self.destinations.reserve(body.destination_location,body.destination_folder,body.destination_name or body.title)
                exported['folderName'] = content.name
                output=content
                seed_parent=content.parent
            else:
                workspace = destination / ".upload-helper"
                workspace.mkdir(exist_ok=True, mode=0o700)
                if workspace.resolve() != workspace or not workspace.is_dir():
                    raise ValueError("Uploadmappen er ikke en almindelig mappe")
                output = workspace / identifier
                output.mkdir(mode=0o700)
                content = output / body.title
                content.mkdir()
                seed_parent=output
            artifacts.mkdir(parents=True, mode=0o700)
            videos, files = [], []
            for index, (original, relative) in enumerate(inputs, 1):
                self.save(identifier, "working", {"title": body.title, "message": f"Forbereder video {index} af {len(inputs)}: {Path(relative).name}"})
                mapped = [(p, s) for p, s in subs if s.video == relative]
                target_relative = release_files[relative] if release_files else original.relative_to(content).as_posix() if direct else str(Path(relative).with_suffix(".mkv")) if mapped else relative
                if staged or episode_folder:
                    target_relative = Path(target_relative).name
                target = original if direct else content / target_relative
                if not direct:
                    target.parent.mkdir(parents=True, exist_ok=True)
                if not direct and target.exists():
                    raise ValueError("Flere valgte filer giver samme navn i uploadkopien")
                before = original.stat()
                if direct:
                    pass  # Read and hash the approved original; never write to its directory.
                elif mapped or body.media_mode in {'release','in_place'} and original.suffix.lower() != '.mkv':
                    args = ["mkvmerge", "-o", str(target), str(original)]
                    for subpath, setting in mapped:
                        args += ["--language", "0:" + LANGUAGES[setting.language], "--default-track-flag", "0:no", str(subpath)]
                    command(args, timeout=7200)
                else:
                    self.copy_media(original, target, lambda completed, total: self.progress(identifier, body, f"Kopierer video {index} af {len(inputs)}", completed, total))
                after = original.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("Kildefilen blev ændret under forberedelsen")
                raw = command(["mediainfo", "--Output=JSON", str(target)])
                tracks = json.loads(raw)["media"]["track"]
                if not any(t.get("@type") == "Video" for t in tracks):
                    raise ValueError("En valgt fil indeholder ikke et identificeret videospor")
                report = command(["mediainfo", str(target)])
                # MediaInfo must be genuine; redact only private server paths.
                report = report.replace(str(target), target.name).replace(str(content), ".")
                (artifacts / f"mediainfo-{index:03}.txt").write_text(report, encoding="utf-8")
                languages = sorted(track_languages(tracks))
                if not {s.language for _, s in mapped} <= set(languages):
                    raise ValueError("MediaInfo kunne ikke bekræfte sprogene for de tilføjede tekster")
                technical = [{k: t[k] for k in ("@type", "Default", "Format", "Format_Profile", "Width", "Height", "FrameRate", "BitRate", "Channels", "Language", "HDR_Format", "ScanType", "HDR_Format_Compatibility", "Format_AdditionalFeatures", "Format_Commercial_IfAny", "ChannelLayout") if k in t} for t in tracks if t.get("@type") in {"Video", "Audio"}]
                general = next((t for t in tracks if t.get('@type') == 'General'), {})
                try:
                    duration_seconds = float(general.get('Duration', 0))
                except (TypeError, ValueError):
                    duration_seconds = 0
                videos.append({"comparison": measured_facts(tracks,target.stat().st_size), "sourceServices": sorted(metadata_services(tracks) or next((metadata_services(v['technical']) for v in observed if v['file']==relative),set())), "container": general.get('Format'), "durationSeconds": duration_seconds if math.isfinite(duration_seconds) else 0, "sizeBytes": target.stat().st_size, "technical": technical, "file": target_relative, "languages": languages, "report": report, "addedSubtitles": [{"file": p.name, "language": s.language} for p, s in mapped]})
                if body.media_mode in {'release','in_place'}:
                    videos[-1]['originalFile'] = relative
                files.append((target, target_relative))
                if body.screenshots and index == 1:
                    duration = json.loads(command(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-of", "json", str(target)]))["format"].get("duration", 0)
                    duration = float(duration)
                    if not math.isfinite(duration) or duration <= 0:
                        raise ValueError("Videolængden kunne ikke bestemmes til screenshots")
                    # Spread the requested frames over the first video, away from its edges.
                    for n in range(1, body.screenshot_count + 1):
                        fraction = n / (body.screenshot_count + 1)
                        command(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-ss", str(duration * fraction), "-i", str(target), "-frames:v", "1", "-vf", "scale=1280:-2", "-threads", "1", str(artifacts / f"screenshot-{index:03}-{n}.png")], timeout=120)
            nfos = set()
            for directory in {root} | {p.parent for p, _ in inputs}:
                for path in directory.iterdir():
                    if path.suffix.lower() == ".nfo" and path.is_file() and path.resolve() == path and path.stat().st_size <= 1024**2:
                        nfos.add(path)
                        if len(nfos) > 20:
                            raise ValueError("Der er over 20 NFO-filer. Vælg en mere præcis mediemappe")
            for index, path in enumerate(sorted(nfos), 1):
                shutil.copyfile(path, artifacts / f"original-{index:03}.nfo")
            names,release_type,release_check,release_evidence=self.automatic_release([v for _,v in inputs],artifacts,[p.name for p in sorted(nfos)],body.release_type)
            if body.media_mode in {'release','in_place'}:
                verified_title, verified_names = release_names(body, [dict(video, file=relative) for video, (_, relative) in zip(videos, inputs)], naming_metadata)
                if verified_title != body.title or verified_names != release_files:
                    raise NamingError('De færdige filers spor svarer ikke til de planlagte navne. Pakken er ikke oprettet.')
            facts=body.model_copy(update={'title':body.title if body.media_mode in {'release','in_place'} else complete_title(body,videos),'release_type':release_type})
            conversion_proof=self.in_place.credited_conversion(self.storage,inputs[0][0],body.source_credit) if body.media_mode=='in_place' and len(inputs)==1 else None
            if conversion_proof:videos[0]['originalFile']=conversion_proof['originalFile']
            checks = evaluate(facts, videos, len(names),conversion_verified=bool(conversion_proof))
            checks=[release_check if check['rule']=='1.9' else check for check in checks]
            metadata = naming_metadata if naming_metadata is not None else self.metadata.details(body)
            (artifacts / 'captain-quack.nfo').write_text(generate_nfo(facts, videos, metadata), encoding='utf-8')
            nfo_name = None
            if body.include_nfo:
                if staged:
                    raise ValueError('NFO i torrenten kræver en separat uploadmappe.')
                nfo_name = Path(files[0][1]).with_suffix('.nfo').name if len(files)==1 else body.title+'.nfo'
                nfo_path = content / nfo_name
                if not direct:
                    with nfo_path.open('xb') as destination:
                        destination.write((artifacts / 'captain-quack.nfo').read_bytes())
                    files.append((nfo_path, nfo_name))
            if not direct:
                verify_payload(content, files, nfo_name)
            if staged:
                if series:
                    name = tagged_folder_name(body.destination_name, body.original_group if body.release_owner == 'other' else 'MDTECH') if body.destination_name else suggested_folder_name(body, naming_metadata)
                    parent, exported = self.destinations.series_directory(body.destination_location, body.destination_folder, name, body.tmdb)
                else:
                    row, parent = self.destinations.directory(body.destination_location, body.destination_folder)
                    exported = {'location': body.destination_location, 'folder': body.destination_folder,
                                'label': row['label'], 'layout': 'flat',
                                'clientSavePath': self.destinations.client_path(row, body.destination_folder)}
                seed_parent = parent
                published_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                original, filename = files[0]
                try:
                    os.link(original, filename, dst_dir_fd=published_fd, follow_symlinks=False)
                except FileExistsError:
                    raise ValueError('Videofilen findes allerede i destinationen. Eksisterende filer overskrives ikke') from None
                info = original.stat()
                published_file = (filename, info.st_dev, info.st_ino)
                content = parent / filename
                files = [(content, filename)]
                exported['clientContentPath'] = exported['clientSavePath'] + '/' + filename
            if direct and (body.include_nfo or body.media_mode=='in_place'):
                if len(inputs)!=1:raise ValueError('Klargør én konverteret film ad gangen i samme mappe')
                original,relative=inputs[0]
                new_name=Path(release_files[relative]).name if body.media_mode=='in_place' else None
                edit=self.in_place.begin(identifier,self.storage,original,new_name,body.include_nfo)
                final=edit.apply((artifacts/'captain-quack.nfo').read_bytes() if body.include_nfo else None)
                files=[(final,final.name)];snapshots={final:(final.stat().st_size,final.stat().st_mtime_ns)};inputs=[(final,relative)]
                videos[0]['file']=final.name
                if final.name!=original.name:
                    videos[0]['report']=videos[0]['report'].replace(original.name,final.name)
                    (artifacts/'mediainfo-001.txt').write_text(videos[0]['report'],encoding='utf-8')
                content=final.parent;single_file=not body.include_nfo;torrent_name=final.name if single_file else content.name;seed_parent=content if single_file else content.parent
                if body.include_nfo:
                    nfo_name=final.with_suffix('.nfo').name;files.append((final.with_suffix('.nfo'),nfo_name))
            if body.torrent_mode == 'local':
                announce = body.announce.get_secret_value().strip() if body.announce else ''
                if not announce:
                    raise ValueError('Trackeradresse mangler. Gem passkey eller vælg nem upload på trackeren.')
                (artifacts / 'upload.torrent').write_bytes(make_torrent(files, torrent_name if direct else content.name, announce, profile=body.profile,
                    single_file=staged or direct and single_file,
                    progress=lambda completed,total:self.progress(identifier,body,'Beregner torrentens hashes',completed,total)))
                checks.append({'rule':'1.7','status':'ok','text':'Privat torrentfil oprettet, højst 1 MiB'})
            checks.append({"rule": "1.6 / 1.9 / 1.14", "status": "ok", "text": f"Uploadkopien er kontrolleret: {len(videos)} valgte videoer" + (' og egen NFO. ' if nfo_name else '. ') + f"{len(excluded)} sidefiler eller SAMPLE-elementer udeladt"})
            checks.append({"rule": "handling", "status": "ok" if metadata.get("status") == "ok" else "missing", "text": "Handling hentet fra TMDB" if metadata.get("status") == "ok" else metadata.get("message", "Handlingen kunne ikke hentes")})
            description = generate_description(facts, videos, metadata=metadata)
            (artifacts / "description.bbcode.txt").write_text(description, encoding="utf-8")
            (artifacts / "description.txt").write_text(plain_description(facts, videos, metadata), encoding="utf-8")
            if direct and any(p.resolve(strict=True) != p or snapshots[p] != (p.stat().st_size, p.stat().st_mtime_ns) for p, _ in inputs):
                raise ValueError('En originalfil blev ændret under klargøringen. Vent til filerne er færdige og prøv igen.')
            result = {"title": facts.title, "releaseName": body.title, "profile": body.profile, "ruleVersion": RULE_VERSION, "originalNfoFiles": names, "releaseEvidence": release_evidence, "releaseType": release_type, "automaticChecksVersion": 3, "automaticChecksAt": time.time(), "hasTorrent": False, "screenshotCount": body.screenshot_count if body.screenshots else 0, "generatedNfo": "captain-quack.nfo", "descriptionFile": "description.txt", "torrentCreation": "tracker", "excludedFiles": excluded, "checks": checks, "readyForReview": all(c["status"] == "ok" for c in checks), "description": description, "metadata": metadata, "videos": videos, "seedingFolder": str(seed_parent), "contentFolder":str(content), "destination":exported, "message": "Klar til din gennemgang" if all(c["status"] == "ok" for c in checks) else "Kladden er lavet, men der mangler kontroller", "notes": ["Seedingmappen indeholder en separat kopi. Originalerne er bevaret.", "Staff har sidste ord. Uden trusted-uploader-rang skal upload godkendes af staff.", "Screenshots vises her og uploades via trackerens godkendte billedforløb.", "Captain Quack-NFO og original NFO er separate uploadbilag. Den genererede NFO erstatter ikke original Scene-NFO.", "Beskrivelse, MediaInfo og screenshots er uploadbilag og ligger ikke i selve torrenten.", "Brug trackerens nemme upload til at oprette torrenten. Hjælperen opretter ingen torrentfil."]}
            result.update(hasTorrent=body.torrent_mode=='local', torrentCreation=body.torrent_mode,
                          episodeMapping=[e.model_dump(exclude={'release_title'}) for e in body.episode_overrides])
            result['releaseOwner'] = body.release_owner
            result['originalGroup'] = body.original_group if body.release_owner == 'other' else None
            if body.release_owner == 'other':
                result['notes'][3] = 'Originale videofilnavne og releasegruppe er bevaret. Vores NFO krediterer den oprindelige udgiver.'
            result['mediaMode'] = body.media_mode
            if conversion_proof:result['conversionProof']=conversion_proof
            result['sourceService'] = service_info(body, videos)
            result['sourceType'] = body.source_type
            result['mediaTags'] = tag_summary(videos)
            result['payloadNfoFile'] = nfo_name
            if nfo_name:
                result['notes'][3] = 'Egen Captain Quack-NFO er med i torrenten og som separat bilag. Original NFO bevares som separat bilag.'
            if body.media_mode=='in_place':
                result['notes'][0]='Den konverterede video klargøres i samme mappe uden kopiering. Filnavn og konverteringskvittering er opdateret.'
            elif body.media_mode=='release':
                result['notes'][0] = 'MDTECH-uploadkopier med navne fra MediaInfo. Originale kildefiler og deres navne er bevaret.'
            if direct:
                result.update(source={'location': body.location, 'folder': body.folder,
                                     'torrentName': torrent_name, 'singleFile': single_file,
                                     'clientSavePath': str(Path('/downloads/media/converted-movies')/seed_parent.relative_to(self.in_place.conversions.storage.downloads/'media/converted-movies')) if edit else self.storage.client_path(seed_parent),
                                     'clientContentPath':str(Path('/downloads/media/converted-movies')/content.relative_to(self.in_place.conversions.storage.downloads/'media/converted-movies')) if edit else self.storage.client_path(content)},
                              contentFolder=str(inputs[0][0] if single_file else content))
                result['notes'][0] = ('Den konverterede video er omdøbt i samme mappe uden en mediekopi. Lyd og billede er uændret.' if body.media_mode=='in_place' else 'Filerne bruges direkte uden mediekopi eller omdøbning.')
                for check in checks:
                    if check['rule'] == '1.6 / 1.9 / 1.14':
                        check['text'] = f'Torrenten bruger kun {len(files)} valgte originalvideoer. Andre filer medtages ikke; originalmappen ændres ikke.'
            result['notes'][-1] = ('Torrentfilen indeholder din private trackeradresse. Del den ikke med andre.'
                                    if body.torrent_mode=='local' else 'Trackeren opretter torrenten via nem upload.')
            (artifacts / "checklist.json").write_text(json.dumps({k: v for k, v in result.items() if k not in {"videos"}}, ensure_ascii=False, indent=2), encoding="utf-8")
            self.save(identifier, "done", result)
            if edit:edit.commit()
            if staged:
                shutil.rmtree(output, ignore_errors=True)
        except Exception as exc:
            if edit:edit.rollback()
            if published_fd is not None and published_file:
                filename, device, inode = published_file
                try:
                    info = os.stat(filename, dir_fd=published_fd, follow_symlinks=False)
                    if (info.st_dev, info.st_ino) == (device, inode):
                        os.unlink(filename, dir_fd=published_fd)
                except FileNotFoundError:
                    pass
            if output is not None:
                shutil.rmtree(output, ignore_errors=True)
            shutil.rmtree(artifacts, ignore_errors=True)
            # Keep credentials, tool output and media paths out of operator logs.
            frames=' > '.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in traceback.extract_tb(exc.__traceback__))
            logging.getLogger(__name__).error('Upload preparation %s failed: %s errno=%s at %s',identifier,type(exc).__name__,getattr(exc,'errno',None),frames)
            message=str(exc) if isinstance(exc,ValueError) else {
                errno.EACCES:'MediaSeed mangler skriveadgang til klargøringens mappe. Ingen eksisterende filer overskrives.',
                errno.EPERM:'MediaSeed har ikke tilladelse til at klargøre filerne i den valgte mappe.',
                errno.EROFS:'Den valgte placering er skrivebeskyttet. Vælg klargøring i Konverterede Film eller en separat uploadkopi.',
                errno.ENOSPC:'Der er ikke ledig plads til at klargøre uploadpakken.',
                errno.EDQUOT:'Pladsgrænsen for klargøring af uploadpakken er nået.',
                errno.ENOENT:'En fil eller mappe mangler. Vælg filmen igen og opret en ny kladde.',
            }.get(getattr(exc,'errno',None),'Forberedelsen fejlede. Fejlen er registreret med uploadpakkens ID, så årsagen kan undersøges.')
            self.save(identifier, "error", {"message": message,"errorReference":identifier})
        finally:
            if published_fd is not None:
                os.close(published_fd)

    def export_existing(self, identifier, request):
        result=request['result']
        output=None
        try:
            name=(result.get('destination') or {}).get('folderName') or result.get('releaseName') or result['title']
            if result.get('destination'):
                previous=result['destination']
                _,parent=self.destinations.directory(previous['location'],previous.get('folder',''))
            else:
                parent=self.storage.output()/'.upload-helper'/identifier
            source=parent/((result.get('destination') or {}).get('folderName') or name)
            if source.resolve(strict=True)!=source or not source.is_dir():raise ValueError('Den klargjorte mediekopi findes ikke længere')
            files=[(safe_file(source,v['file'],VIDEO),v['file']) for v in result['videos']]
            nfo_name=result.get('payloadNfoFile')
            if nfo_name:files.append((safe_file(source,nfo_name,{'.nfo'}),nfo_name))
            verify_payload(source,files,nfo_name)
            _,destination=self.destinations.directory(request['location'],request['folder'])
            total=sum(p.stat().st_size for p,_ in files)
            if shutil.disk_usage(destination).free<total+max(1024**3,total//20):raise ValueError('Der er ikke plads på mediedrevet til kopien og 1 GiB sikkerhedsmargin')
            output,mapping=self.destinations.reserve(request['location'],request['folder'],name)
            from types import SimpleNamespace
            progress_body=SimpleNamespace(title=result['title'])
            for index,(original,relative) in enumerate(files,1):
                before=original.stat()
                target=output/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                self.copy_media(original,target,lambda done,total:self.progress(identifier,progress_body,f'Kopierer til mediedrevet · fil {index} af {len(files)}',done,total))
                after=original.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Mediekopien blev ændret under kopiering')
            verify_payload(output,[(output/relative,relative) for _,relative in files],nfo_name)
            result.update(seedingFolder=str(output.parent),contentFolder=str(output),destination=mapping)
            result.pop('destinationError',None)
        except Exception as exc:
            if output is not None:shutil.rmtree(output,ignore_errors=True)
            result['destinationError']=str(exc) if isinstance(exc,ValueError) else 'Kopiering til mediedrevet fejlede. Kontrollér adgang, ledig plads og destination. Den tidligere pakke er bevaret.'
        self.save(identifier,'done',result)

    def save(self, identifier, state, result):
        if state == 'done' and self.artifact_exports.root is not None:
            try:
                result['artifactExport'] = self.artifact_exports.publish(identifier, result, self.data/'upload-helper'/identifier)
                result.pop('artifactExportError', None)
            except (OSError, ValueError) as exc:
                result['artifactExportError'] = str(exc) if isinstance(exc, ValueError) else 'Uploadbilagene kunne ikke gemmes i media/upload. Kontrollér adgang og ledig plads; pakken kan stadig hentes her.'
        with self.database() as db:
            db.execute("UPDATE upload_jobs SET state=?,result=? WHERE id=?", (state, json.dumps(result, ensure_ascii=False), identifier))


def install_upload_helper(app, authorized, data, storage, database, destinations):
    helper = UploadHelper(data, storage, database, destinations)
    app.state.upload_helper = helper

    @app.get('/destinations')
    def destinations_overview(session=Depends(authorized)):
        helper.owner(session)
        return helper.destinations.overview()

    @app.get('/destinations/folders')
    def destination_folders(location:str=Query(max_length=64),folder:str=Query(default='',max_length=1000),session=Depends(authorized)):
        helper.owner(session)
        return helper.destinations.folders(location,folder)

    class ExportDestination(BaseModel):
        model_config=ConfigDict(extra='forbid')
        location:str=Field(min_length=1,max_length=64)
        folder:str=Field(default='',max_length=1000)

    @app.post('/upload-helper/{identifier}/destination')
    def export_destination(identifier:str,body:ExportDestination,session=Depends(authorized)):
        owner=helper.owner(session)
        _,parent=helper.destinations.directory(body.location,body.folder)
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM upload_jobs WHERE id=? AND owner=?',(identifier,owner)).fetchone()
            if not row:raise HTTPException(404,'Kladden findes ikke')
            if row['state']!='done':raise HTTPException(409,'Vent til opgaven er færdig')
            result=json.loads(row['result'])
            if result.get('mediaMode') == 'source':
                raise HTTPException(409, 'Denne pakke bruger originalfilerne direkte og skal ikke kopieres')
            name=(result.get('destination') or {}).get('folderName') or result.get('releaseName') or result['title']
            previous=result.get('destination') or {}
            if previous.get('location')==body.location and previous.get('folder','')==body.folder and (parent/name).is_dir():return {'id':identifier,'state':'done'}
            if os.path.lexists(parent/name):raise HTTPException(409,'Releasemappen findes allerede. Vælg en anden destinationsmappe; eksisterende filer overskrives ikke')
            if db.execute("SELECT COUNT(*) FROM upload_jobs WHERE owner=? AND state IN ('queued','working')",(owner,)).fetchone()[0]>=20:raise HTTPException(409,'Der er allerede 20 opgaver i kø eller under klargøring')
            request={'operation':'export','result':result,'location':body.location,'folder':body.folder}
            db.execute("UPDATE upload_jobs SET state='queued',result=?,created=? WHERE id=?",(json.dumps({'title':result['title'],'message':'Venter på kopiering til mediedrevet'}),time.time(),identifier))
            db.execute('INSERT INTO upload_queue VALUES (?,?)',(identifier,json.dumps(request)))
        helper.wake.set()
        return {'id':identifier,'state':'queued'}

    @app.get("/upload-helper")
    def overview(session=Depends(authorized)):
        owner = helper.owner(session)
        with database() as db:
            rows = db.execute("SELECT id,state,result,created FROM upload_jobs WHERE owner=? ORDER BY CASE WHEN state IN ('working','queued') THEN 0 ELSE 1 END,created DESC LIMIT 40", (owner,)).fetchall()
            positions = {r[0]: n for n, r in enumerate(db.execute("SELECT id FROM upload_jobs WHERE state='queued' ORDER BY created,id"), 1)}
        jobs = []
        for row in rows:
            result = json.loads(row["result"])
            if "videos" in result:
                result["videos"] = [{k: v for k, v in video.items() if k != "report"} for video in result["videos"]]
            jobs.append({"id": row["id"], "state": row["state"], "created": row["created"], **result, **({"queuePosition": positions.get(row["id"])} if row["state"] == "queued" else {})})
        return {"tools": helper.capabilities(), "ruleVersion": RULE_VERSION, "jobs": jobs}

    class MetadataConfiguration(BaseModel):
        model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
        credential: SecretStr = Field(min_length=32, max_length=4096)

    class TvdbConfiguration(BaseModel):
        model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
        apikey: SecretStr = Field(min_length=8, max_length=4096)
        pin: SecretStr = Field(default=SecretStr(''), max_length=256)

    class TrackerConfiguration(BaseModel):
        model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
        passkey: SecretStr = Field(min_length=32, max_length=32)

    @app.get("/tracker/settings")
    def tracker_settings(session=Depends(authorized)):
        helper.owner(session)
        return {"configured": helper.tracker.configured()}

    @app.put("/tracker/settings")
    def configure_tracker(body: TrackerConfiguration, session=Depends(authorized)):
        helper.owner(session)
        try:
            return helper.tracker.configure(body.passkey.get_secret_value())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/metadata/settings")
    def metadata_settings(session=Depends(authorized)):
        helper.owner(session)
        try:
            status=helper.metadata.request('/status')
            return {"configured": bool(status.get("configured")), 'tvdbConfigured': bool(status.get('tvdbConfigured'))}
        except MetadataError:
            return {"configured": False, "message": "TMDB-forbindelsen er midlertidigt utilgængelig"}

    @app.put("/metadata/settings")
    def configure_metadata(body: MetadataConfiguration, session=Depends(authorized)):
        helper.owner(session)
        try:
            return helper.metadata.request("/configuration", {"credential": body.credential.get_secret_value()})
        except MetadataError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.put('/metadata/tvdb/settings')
    def configure_tvdb(body: TvdbConfiguration, session=Depends(authorized)):
        helper.owner(session)
        try:
            return helper.metadata.request('/tvdb/configuration', {
                'apikey': body.apikey.get_secret_value(), 'pin': body.pin.get_secret_value()})
        except MetadataError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/description/preview")
    def preview(body: DescriptionRequest, session=Depends(authorized)):
        helper.owner(session)
        if not helper.capabilities()["mediainfo"]:
            raise HTTPException(409, "Beskrivelsen afventer installation af MediaInfo")
        if not helper.preview_lock.acquire(blocking=False):
            raise HTTPException(409, "En beskrivelse er ved at blive lavet. Prøv igen om et øjeblik")
        try:
            return helper.preview(body)
        except NamingError as exc:
            raise HTTPException(422, str(exc)) from None
        except (ValueError, OSError):
            raise HTTPException(422, "Beskrivelsen kunne ikke laves. Kontrollér at videoen er tilgængelig og færdigdownloadet") from None
        finally:
            helper.preview_lock.release()

    @app.post("/subtitles", status_code=201)
    async def upload_subtitle(request: Request, filename: str = Query(min_length=1, max_length=180), session=Depends(authorized)):
        helper.owner(session)
        if Path(filename).name != filename or filename.startswith(".") or "\\" in filename or any(ord(c) < 32 for c in filename) or Path(filename).suffix.lower() not in SUBS:
            raise HTTPException(422, "Vælg en SRT-, ASS-, SSA- eller VTT-fil")
        received = bytearray()
        async for chunk in request.stream():
            received.extend(chunk)
            if len(received) > 1024**2:
                raise HTTPException(413, "En undertekst må højst være 1 MiB")
        try:
            text = received.decode("utf-8-sig")
            if not text.strip() or "\0" in text:
                raise ValueError()
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(422, "Gem underteksten som UTF-8 tekst og prøv igen") from None
        existing = list(helper.incoming.glob("*/*"))
        if len(existing) >= 1000 or sum(p.stat().st_size for p in existing if p.is_file()) + len(received) > 50 * 1024**2:
            raise HTTPException(409, "Der ligger 50 MiB eller 1000 importerede tekster i testmiljøet. Ryd op før du importerer flere")
        identifier = secrets.token_hex(12)
        target = helper.incoming / identifier
        target.mkdir(mode=0o700)
        (target / filename).write_bytes(received)
        language = next((ALIASES[t.lower()] for t in re.split(r"[._ -]", Path(filename).stem) if t.lower() in ALIASES), "")
        return {"file": "@uploaded/" + identifier + "/" + filename, "displayName": filename, "size": len(received), "kind": "subtitle", "language": language}

    @app.get("/upload-helper/files")
    def browse(location: str = Query(default="local", max_length=64), folder: str = Query(default="", max_length=1000), recursive: bool = True, session=Depends(authorized)):
        helper.owner(session)
        _, root = storage.directory(location, folder)
        try:
            return {"files": inspect_folder(root, recursive=recursive)}
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, ValueError) else "Mappen kunne ikke læses") from None

    @app.post('/upload-helper/plan')
    def plan(body: Preparation, session=Depends(authorized)):
        helper.owner(session)
        if body.kind=='movie':
            return {'status':'ready','mode':'movie','complete':False,'entries':[], 'message':'Film klargøres som én upload.'}
        _,root=storage.directory(body.location,body.folder)
        try:
            for file in body.videos: safe_file(root,file,VIDEO)
        except (ValueError,OSError):
            raise HTTPException(422,'En valgt fil er ikke tilgængelig') from None
        result=plan_episodes(body,helper.metadata.catalogue(body.tmdb))
        if result['status']=='ready':
            try: children=apply_upload_options(body,uploads_for_plan(body,result))
            except ValueError as exc:
                result.update(status='needs_review',message=str(exc))
                return result
            result['uploads']=[{'title':child.title,'kind':child.kind,'videos':child.videos,'catalogue_title':child.catalogue_title,
                               'episode_overrides':[e.model_dump() for e in child.episode_overrides],
                               **{key:getattr(child,key) for key in ['synopsis','source_credit','source_service','source_type','edition','release_format','release_type','screenshots','screenshot_count','destination_layout','destination_name','series_name']}} for child in children]
        elif result['status']=='needs_review' and result['entries']:
            try: children=apply_upload_options(body,episode_uploads(body,result))
            except ValueError as exc:
                result['message'] += ' ' + str(exc)
                return result
            result['previewUploads']=[{'title':child.title,'kind':child.kind,'videos':child.videos,'catalogue_title':child.catalogue_title,
                                      'episode_overrides':[e.model_dump() for e in child.episode_overrides],
                               **{key:getattr(child,key) for key in ['synopsis','source_credit','source_service','source_type','edition','release_format','release_type','screenshots','screenshot_count','destination_layout','destination_name','series_name']}} for child in children]
        return result

    @app.post("/upload-helper", status_code=202)
    def prepare(body: Preparation, session=Depends(authorized)):
        owner = helper.owner(session)
        if not body.title or body.title.startswith(".") or any(c in body.title for c in '/\\:') or any(ord(c) < 32 for c in body.title):
            raise HTTPException(422, "Vælg en almindelig titel uden skråstreger eller kolon")
        if any(any(part.casefold() in {"sample", "samples"} for part in Path(v).parts[:-1]) or Path(v).stem.casefold() == "sample" or Path(v).stem.casefold().endswith(".sample") for v in body.videos):
            raise HTTPException(422, "SAMPLE-filer medtages ikke i uploadpakken")
        if len(set(body.videos)) != len(body.videos):
            raise HTTPException(422, "Samme video er valgt flere gange")
        if any(s.video not in body.videos for s in body.subtitles) or len({s.file for s in body.subtitles}) != len(body.subtitles):
            raise HTTPException(422, "Hver undertekst skal være knyttet til én valgt video")
        if body.torrent_mode=='local':
            try:
                announce=body.announce.get_secret_value().strip() if body.announce else ''
                if not announce and body.profile=='nordicbytes':announce=helper.tracker.announce()
                if not announce:raise ValueError('Gem din passkey eller angiv trackerens HTTPS announce-URL. Du kan også vælge nem upload på trackeren.')
                body.announce=SecretStr(normalize_announce(announce,body.profile))
            except ValueError as exc:raise HTTPException(422,str(exc)) from None
        else:
            body.announce=None
        if len({e.file for e in body.episode_overrides}) != len(body.episode_overrides) or any(e.file not in body.videos for e in body.episode_overrides):
            raise HTTPException(422,'Hvert afsnitsvalg skal høre til én valgt video')
        children=[body]
        if body.upload_strategy=='auto' and body.kind!='movie':
            result=plan_episodes(body,helper.metadata.catalogue(body.tmdb))
            if result['status']!='ready':raise HTTPException(422,result['message'])
            try: children=uploads_for_plan(body,result)
            except ValueError as exc:raise HTTPException(422,str(exc)) from None
        elif body.profile=='nordicbytes' and body.kind!='movie' and len(body.videos)>1:
            raise HTTPException(422,'Brug automatisk opdeling, så pakkens fuldstændighed kan kontrolleres før klargøring.')
        try: children=apply_upload_options(body,children)
        except ValueError:raise HTTPException(422,'Kontrollér indstillingerne for de enkelte uploads') from None
        if any(c.include_nfo and (c.destination_layout in {'flat','series'}) for c in children):
            raise HTTPException(422,'NFO i torrenten kræver en separat uploadmappe. Vælg en mappe pr. fil eller en samlet mappe.')
        if any(c.description_override is not None and not c.description_override.strip() for c in children):
            raise HTTPException(422,'Skriv en beskrivelse til hver fil, eller vælg automatisk beskrivelse.')
        if any(c.destination_layout in {'flat','series','episode','series_episodes'} and (len(c.videos)!=1 or not c.destination_location or c.media_mode in {'source','in_place'}) for c in children):
            raise HTTPException(422,'Enkeltfiler og fælles seriemappe kræver én video pr. upload og en valgt kopidestination.')
        if any(c.destination_layout in {'series','series_episodes'} and (c.kind!='episode' or not c.catalogue_title) for c in children):
            raise HTTPException(422,'En fælles seriemappe kræver identificerede enkeltstående afsnit.')
        if len(children)>20:
            raise HTTPException(422,'Vælg højst 20 separate uploads ad gangen.')
        titles=[child.title for child in children]
        if len(set(t.casefold() for t in titles))!=len(titles):raise HTTPException(422,'Flere uploads får samme navn. Ret titlerne før klargøring.')
        for child in children:
            if not child.title or child.title.startswith('.') or any(c in child.title for c in '/\\:') or any(ord(c)<32 for c in child.title):
                raise HTTPException(422,'En uploadtitel indeholder ugyldige tegn. Ret titlen i afsnitsoversigten.')
        if body.media_mode in {'source','in_place'} and (body.subtitles or body.destination_location):
            raise HTTPException(422, 'Klargøring i samme mappe kræver ingen ekstra undertekster og ingen kopidestination. Vælg separat kopi for at indlejre tekster.')
        required = {"mediainfo"} | ({"mkvmerge"} if body.subtitles or body.media_mode in {'release','in_place'} else set()) | ({"ffmpeg", "ffprobe"} if any(c.screenshots for c in children) else set())
        missing = required - {k for k, v in helper.capabilities().items() if v}
        if missing:
            raise HTTPException(409, "Medieværktøjer mangler: " + ", ".join(sorted(missing)) + ". Opdatér MediaUpload-containeren til den nyeste version.")
        _, root = storage.directory(body.location, body.folder)
        if body.media_mode=='in_place' and body.release_format=='REMUX':raise HTTPException(422,'En konverteret film er genkodet og kan ikke mærkes REMUX')
        if body.media_mode in {'source','in_place'}:
            if body.subtitles or body.destination_location:
                raise HTTPException(422, 'Klargøring i samme mappe kræver ingen ekstra undertekster og ingen kopidestination. Vælg separat kopi for at indlejre tekster.')
            if body.include_nfo or body.media_mode=='in_place':
                if len(body.videos)!=1:raise HTTPException(422,'Klargør én konverteret film ad gangen i samme mappe')
                try:helper.in_place.locate(storage,safe_file(root,body.videos[0],VIDEO))
                except (ValueError,OSError) as exc:raise HTTPException(409,str(exc)) from None
        elif body.destination_location:
            _,parent=helper.destinations.directory(body.destination_location,body.destination_folder)
            if any(child.destination_layout=='folder' and os.path.lexists(parent/(child.destination_name or child.title)) for child in children):raise HTTPException(409,"En releasemappe findes allerede. Vælg en anden destinationsmappe")
        else:storage.output()
        try:
            for v in body.videos:
                safe_file(root, v, VIDEO)
            for s in body.subtitles:
                helper.subtitle_file(root, s.file)
        except (ValueError, OSError):
            raise HTTPException(422, "En valgt fil er utilgængelig eller uden for mediemappen") from None
        output_names={}
        if body.upload_strategy=='auto' and body.kind!='movie' or body.media_mode in {'release','in_place'} or any(c.source_service=='auto' for c in children):
            # Resolve missing quality from observed media before reserving any
            # destination or torrent root, so site and torrent names agree.
            for child in children:
                try:
                    preview=helper.preview(child)
                    if preview.get('namingError'):raise NamingError(preview['namingError'])
                    if preview.get('sourceService',{}).get('status')=='needs_input':raise NamingError(preview['sourceService']['message'])
                except NamingError as exc:
                    raise HTTPException(422,str(exc)) from None
                except (ValueError,OSError):
                    raise HTTPException(422,'Medieoplysningerne kunne ikke læses før klargøring.') from None
                output_names[id(child)]=preview.get('outputFiles',{})
                child.title=preview['releaseTitle'] if child.media_mode in {'release','in_place'} else complete_title(child,preview['observedVideos'])
                if len(child.title)>240:
                    raise HTTPException(422,'En uploadtitel er for lang efter mediekontrollen. Forkort titlen.')
            if len({child.title.casefold() for child in children})!=len(children):
                raise HTTPException(422,'Flere uploads får samme titel efter mediekontrollen.')
        if body.media_mode not in {'source','in_place'} and body.destination_location:
            targets=[]
            for child in children:
                if child.destination_layout in {'episode','series_episodes'}:
                    original=child.videos[0]
                    filename=output_names.get(id(child),{}).get(original) or (str(Path(original).with_suffix('.mkv')) if any(s.video==original for s in child.subtitles) else original)
                    suggestions=folder_suggestions(child,[filename])
                    episode_parent=parent
                    if child.destination_layout=='series_episodes':
                        series_name=dotted_folder_name(child.series_name, child.original_group if child.release_owner == 'other' else 'MDTECH') if child.series_name else suggestions['suggestedSeriesFolderName']
                        try:episode_parent,_=helper.destinations.series_directory(child.destination_location,child.destination_folder,series_name,child.tmdb,create=False)
                        except ValueError as exc:raise HTTPException(409,str(exc)) from None
                    targets.append(episode_parent/(dotted_folder_name(child.destination_name, child.original_group if child.release_owner == 'other' else 'MDTECH') if child.destination_name else suggestions['suggestedEpisodeFolderName']))
                elif child.destination_layout in {'flat','series'}:
                    original=child.videos[0]
                    name=output_names.get(id(child),{}).get(original) or (str(Path(original).with_suffix('.mkv')) if any(s.video==original for s in child.subtitles) else original)
                    if child.destination_layout=='series':
                        folder_name=tagged_folder_name(child.destination_name, child.original_group if child.release_owner == 'other' else 'MDTECH') if child.destination_name else suggested_folder_name(child)
                        try: series_parent,_=helper.destinations.series_directory(child.destination_location,child.destination_folder,folder_name,child.tmdb,create=False)
                        except ValueError as exc:raise HTTPException(409,str(exc)) from None
                        targets.append(series_parent/Path(name).name)
                    else:targets.append(parent/Path(name).name)
                else:targets.append(parent/(child.destination_name or child.title))
            if len({str(path).casefold() for path in targets})!=len(targets):
                raise HTTPException(422,'Flere uploads får samme sti til seeding. Vælg forskellige mappenavne eller automatisk navngivning.')
            if any(os.path.lexists(path) for path in targets):
                raise HTTPException(409,'En fil eller mappe findes allerede i destinationen. Eksisterende indhold overskrives ikke.')
        jobs=[]
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM upload_jobs WHERE owner=? AND state IN ('working','queued')", (owner,)).fetchone()[0] + len(children) > 20:
                raise HTTPException(409, "Du har 20 opgaver i kø eller under klargøring. Vent til en af dem er færdig")
            for index,child in enumerate(children):
                identifier=secrets.token_hex(12)
                request=child.model_dump(mode='json',exclude={'announce'})
                if child.torrent_mode=='local':request['announce']=child.announce.get_secret_value()
                db.execute("INSERT INTO upload_jobs VALUES (?,?,?,?,?)", (identifier, owner, "queued", json.dumps({"title": child.title, "location": child.location}), time.time()+index/1000))
                db.execute("INSERT INTO upload_queue VALUES (?,?)", (identifier, json.dumps(request)))
                jobs.append({'id':identifier,'title':child.title,'state':'queued'})
        helper.wake.set()
        return {"id": jobs[0]['id'], "state": "queued", 'jobs':jobs, 'uploadCount':len(jobs)}

    @app.post("/upload-helper/{identifier}/refresh-checks")
    def refresh_checks(identifier: str, session=Depends(authorized)):
        try:return helper.refresh_review_checks(identifier,helper.owner(session))
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
        except OSError:raise HTTPException(409,'Bilagene kunne ikke opdateres. Kontrollér adgang og plads; eksisterende filer er bevaret.') from None

    @app.get("/upload-helper/{identifier}")
    def details(identifier: str, session=Depends(authorized)):
        row = helper.row(identifier, helper.owner(session))
        return {"id": identifier, "state": row["state"], **json.loads(row["result"])}

    def saved_report(identifier, session):
        row = helper.row(identifier, helper.owner(session))
        if row['state'] != 'done':
            raise HTTPException(409, 'Vent til pakken er færdig')
        result = json.loads(row['result'])
        try:
            report = combined_report(result)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return result, report

    @app.get('/upload-helper/{identifier}/mediainfo')
    def all_mediainfo(identifier: str, session=Depends(authorized)):
        result, report = saved_report(identifier, session)
        return {'text': report, 'files': len(result['videos'])}

    @app.get("/upload-helper/{identifier}/asset/{name}")
    def image(identifier: str, name: str, session=Depends(authorized)):
        if name == 'mediainfo.txt':
            result, report = saved_report(identifier, session)
            filename = re.sub(r'[^A-Za-z0-9._-]', '_', result.get('title') or 'upload')[:150] + '.MediaInfo.txt'
            return Response(report, media_type='text/plain; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="'+filename+'"', 'Cache-Control': 'no-store'})
        row = helper.row(identifier, helper.owner(session))
        if row["state"] != "done" or not re.fullmatch(r"screenshot-001-[1-5]\.png|original-\d{3}\.nfo|captain-quack\.nfo|description(?:\.bbcode)?\.txt", name):
            raise HTTPException(404)
        path = data / "upload-helper" / identifier / name
        if not path.is_file():
            raise HTTPException(404)
        result = json.loads(row['result'])
        filename = name
        if name == 'captain-quack.nfo':
            filename = re.sub(r'[\x00-\x1f\x7f/\\:<>"|?*]', '_', str(result.get('title') or 'Captain Quack')).strip(' .')[:200] + '.nfo'
            original = path.read_bytes()
            try:
                formatted = readable_credits(original.decode('utf-8')).encode('utf-8')
            except UnicodeDecodeError:
                formatted = original
            if formatted != original:
                from urllib.parse import quote
                return Response(formatted, media_type='application/octet-stream', headers={'Content-Disposition': "attachment; filename*=UTF-8''"+quote(filename), 'Cache-Control': 'no-store'})
        return FileResponse(path, media_type="image/png" if name.endswith(".png") else "application/octet-stream", filename=filename if not name.endswith('.png') else None, headers={"Cache-Control": "no-store"})

    @app.get("/upload-helper/{identifier}/download")
    def download(identifier: str, session=Depends(authorized)):
        row = helper.row(identifier, helper.owner(session))
        if row["state"] != "done":
            raise HTTPException(409, "Uploadpakken er ikke færdig")
        path = data / "upload-helper" / identifier / "upload.torrent"
        if not path.is_file():
            raise HTTPException(409, "Torrenten oprettes nu på trackeren. Hent NFO og beskrivelse under uploadbilag.")
        result=json.loads(row['result'])
        original=result.get('releaseName') or result.get('title') or 'torrent'
        filename=re.sub(r'[\x00-\x1f\x7f/\\:<>"|?*]','_',str(original)).strip(' .')[:240] or 'torrent'
        response=FileResponse(path, media_type="application/x-bittorrent", filename=filename+'.torrent', headers={"Cache-Control": "no-store"})
        if result.get('profile')=='nordicbytes':
            try:
                with path.open('rb') as stream:payload=nordicbytes_download(stream.read(1024**2+1))
            except (ValueError,TypeError,IndexError,OverflowError):
                raise HTTPException(409,'Torrentfilens metadata kunne ikke rettes til NordicBytes. Brug torrentfilen fra trackeren eller opret pakken igen') from None
            return Response(payload,media_type='application/x-bittorrent',headers={'Cache-Control':'no-store','Content-Disposition':response.headers['content-disposition']})
        return response

    @app.delete("/upload-helper/{identifier}")
    def remove(identifier: str, session=Depends(authorized)):
        row = helper.row(identifier, helper.owner(session))
        if row["state"] in {"working", "queued"}:
            raise HTTPException(409, "Vent til forberedelsen er færdig")
        # Remove only small attachments. Prepared media may already be seeding.
        shutil.rmtree(data / "upload-helper" / identifier, ignore_errors=True)
        with database() as db:
            db.execute("DELETE FROM upload_jobs WHERE id=?", (identifier,))
        return {"removed": True, "filesPreserved": True}
