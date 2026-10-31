"""Authenticated, bounded TheTVDB v4 catalogue access. No title-based ID guesses."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request


class TvdbError(ValueError):
    pass


class Tvdb:
    def __init__(self, root, opener):
        self.root = Path(root)
        self.credential = self.root / 'tvdb-credential.json'
        self.opener = opener
        self.lock = threading.Lock()
        self.token = None

    def configured(self):
        return self.credential.is_file()

    def request(self, path, token=None, body=None, timeout=5):
        if not (path == '/login' and body is not None or
                re.fullmatch(r'/series/[1-9]\d*/episodes/official/eng\?page=\d{1,2}', path) and body is None):
            raise TvdbError('Ugyldigt TheTVDB-opslag')
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': 'MediaUpload/0.1.18'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            request = urllib.request.Request('https://api4.thetvdb.com/v4' + path,
                headers=headers, data=json.dumps(body).encode() if body is not None else None)
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise TvdbError('TheTVDB-svaret er for stort')
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get('status') != 'success' or not isinstance(result.get('data'), dict):
                raise TvdbError('TheTVDB returnerede et ukendt svar')
            return result
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise TvdbError('TheTVDB afviste adgangen. Kontrollér API-nøgle, PIN og API-adgang.') from None
            raise TvdbError('TheTVDB kunne ikke levere afsnitslisten.') from None
        except (OSError, ValueError) as exc:
            if isinstance(exc, TvdbError):
                raise
            raise TvdbError('TheTVDB er midlertidigt utilgængelig. Prøv igen senere.') from None

    def login(self, credentials):
        token = self.request('/login', body=credentials)['data'].get('token')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._-]{20,8192}', token):
            raise TvdbError('TheTVDB returnerede ikke et gyldigt læsetoken')
        return token

    def configure(self, apikey, pin=''):
        if not isinstance(apikey, str) or not re.fullmatch(r'[A-Za-z0-9._-]{8,4096}', apikey):
            raise TvdbError('Angiv din TheTVDB API-nøgle')
        if not isinstance(pin, str) or pin and not re.fullmatch(r'[A-Za-z0-9._-]{1,256}', pin):
            raise TvdbError('Angiv en gyldig TheTVDB-PIN, eller lad feltet stå tomt')
        credentials = {'apikey': apikey}
        if pin:
            credentials['pin'] = pin
        # Keep previous credentials if validation fails.
        token = self.login(credentials)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=self.root, delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(credentials, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.chmod(0o600)
                temporary.replace(self.credential)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
            self.token = (time.monotonic(), token)
        return {'configured': True}

    def catalogue(self, identifier):
        if type(identifier) is not int or not 0 < identifier <= 2147483647:
            raise TvdbError('Serien har ikke et gyldigt TheTVDB-ID')
        if not self.configured():
            raise TvdbError('Tilknyt TheTVDB med API-nøgle og eventuel PIN for at krydstjekke katalogerne.')
        with self.lock:
            if not self.token or time.monotonic() - self.token[0] > 86400:
                self.token = (time.monotonic(), self.login(json.loads(self.credential.read_text())))
            token = self.token[1]
        deadline = time.monotonic() + 20
        episodes, seen = [], set()
        for page in range(40):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TvdbError('TheTVDB-krydstjekket tog for lang tid. Prøv igen.')
            try:
                result = self.request(f'/series/{identifier}/episodes/official/eng?page={page}',
                                      token=token, timeout=min(5, remaining))
            except TvdbError:
                with self.lock:
                    self.token = None
                raise
            data = result['data']
            series = data.get('series')
            if not isinstance(series, dict) or series.get('id') != identifier:
                raise TvdbError('TheTVDB returnerede en anden serie')
            rows, links = data.get('episodes'), result.get('links')
            if not isinstance(rows, list) or not isinstance(links, dict) or 'next' not in links:
                raise TvdbError('TheTVDB-listens fuldstændighed kunne ikke bekræftes')
            for item in rows:
                if not isinstance(item, dict):
                    raise TvdbError('TheTVDB returnerede et ugyldigt afsnit')
                season, number = item.get('seasonNumber'), item.get('number')
                name, episode_id = item.get('name'), item.get('id')
                if (any(type(n) is not int or not 0 <= n <= 10000 for n in [season, number]) or
                    type(episode_id) is not int or episode_id <= 0 or
                    not isinstance(name, str) or not name.strip() or len(name) > 300 or
                    item.get('seriesId') != identifier or (season, number) in seen):
                    raise TvdbError('TheTVDB returnerede ugyldig eller gentagen afsnitsnummerering')
                seen.add((season, number))
                episodes.append({'season': season, 'episode': number, 'title': name,
                                 'airDate': item.get('aired'), 'tvdbEpisode': episode_id})
            if len(episodes) > 20000:
                raise TvdbError('TheTVDB-listen er for stor')
            if links['next'] is None:
                if not episodes:
                    raise TvdbError('TheTVDB har ingen bekræftet afsnitsliste')
                return {'tvdb': identifier, 'episodes': episodes}
            # Never follow provider-supplied URLs, including pagination links.
            if not rows or not isinstance(links['next'], str) or not links['next']:
                raise TvdbError('TheTVDB returnerede ugyldig sideinddeling')
        raise TvdbError('TheTVDB-listen har for mange sider')
