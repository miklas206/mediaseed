"""Fixed-host TMDB access in a separate, local metadata worker."""
import hmac
import http.client
import json
import os
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from mediaseed.upload_engine.releases import Releases
from mediaseed.upload_engine.tvdb import Tvdb, TvdbError
from mediaseed.upload_engine.catalogue_check import crosscheck


class MetadataError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def metadata_connection(address, timeout=5, source_address=None):
    """Prefer IPv4 on dual-stack hosts; share one connect budget across addresses."""
    addresses = socket.getaddrinfo(*address, type=socket.SOCK_STREAM)
    addresses.sort(key=lambda item: item[0] != socket.AF_INET)
    deadline = time.monotonic() + timeout
    error = OSError('No metadata host addresses')
    for family, kind, protocol, _, endpoint in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Metadata connection timed out') from error
        connection = socket.socket(family, kind, protocol)
        try:
            connection.settimeout(remaining)
            if source_address:
                connection.bind(source_address)
            connection.connect(endpoint)
            return connection
        except OSError as exc:
            connection.close()
            error = exc
    raise error


class MetadataHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = metadata_connection


class MetadataHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(MetadataHTTPSConnection, request, context=self._context)


class Tmdb:
    def __init__(self, root, opener=None):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.credential=self.root/'tmdb-credential.json'
        self.opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),MetadataHTTPSHandler())
        self.cache={}
        self.lock=threading.Lock()
        self.tvdb = None

    def configured(self):
        return self.credential.is_file()

    def read(self,path,credential,language=None):
        if not re.fullmatch(r'/3/(?:configuration|movie/\d+|tv/\d+(?:/external_ids|/season/\d+(?:/episode/\d+)?)?)',path):
            raise MetadataError('Ugyldig metadataforespørgsel')
        headers={'Accept':'application/json','User-Agent':'MediaUpload/0.1.10 (local upload preparation)'}
        params={}
        if re.fullmatch('[a-fA-F0-9]{32}',credential):params['api_key']=credential
        else:headers['Authorization']='Bearer '+credential
        if language:params['language']=language
        url='https://api.themoviedb.org'+path+('?' + urllib.parse.urlencode(params) if params else '')
        try:
            with self.opener.open(urllib.request.Request(url,headers=headers),timeout=5) as response:
                raw=response.read(1024**2+1)
                if len(raw)>1024**2:raise MetadataError('TMDB-svaret er for stort')
                data=json.loads(raw)
                if not isinstance(data,dict):raise MetadataError('TMDB returnerede et ukendt svar')
                return data
        except urllib.error.HTTPError as exc:
            if exc.code in {401,403}:raise MetadataError('TMDB-nøglen blev afvist') from None
            if exc.code==404:raise MetadataError('Filmen, serien eller afsnittet blev ikke fundet på TMDB') from None
            raise MetadataError('TMDB kunne ikke levere oplysningerne lige nu') from None
        except (OSError,ValueError,TimeoutError):
            raise MetadataError('TMDB kunne ikke kontaktes. Prøv igen senere') from None

    def configure(self,credential):
        if not isinstance(credential,str) or not re.fullmatch(r'[A-Za-z0-9._-]{32,4096}',credential):
            raise MetadataError('Angiv din TMDB API-nøgle eller dit API Read Access Token')
        self.read('/3/configuration',credential)
        with tempfile.NamedTemporaryFile(mode='w',dir=self.root,delete=False) as stream:
            temporary=Path(stream.name)
            json.dump({'credential':credential},stream)
            stream.flush();os.fsync(stream.fileno())
        temporary.chmod(0o600);temporary.replace(self.credential)
        with self.lock:self.cache.clear()
        return {'configured':True}

    def localized(self,path,credential):
        return self.read(path,credential,'en-US'),'en'

    def catalogue(self, identifier):
        if type(identifier) is not int or not 0 < identifier <= 2147483647:
            raise MetadataError('Vælg et gyldigt TMDB-serie-ID')
        if not self.configured():
            return {'status': 'not_configured', 'message': 'Tilknyt TMDB for at identificere afsnit og komplette pakker.'}
        key = ('catalogue', identifier)
        with self.lock:
            cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < (3600 if cached[1].get('crossCheck', {}).get('status', 'ok') == 'ok' else 30):
            return cached[1]
        credential = json.loads(self.credential.read_text())['credential']
        show = self.read(f'/3/tv/{identifier}', credential, 'en-US')
        if show.get('id') != identifier:
            raise MetadataError('TMDB returnerede en anden serie')
        seasons = show.get('seasons', [])
        if not seasons or len(seasons) > 40 or len({s.get('season_number') for s in seasons}) != len(seasons):
            raise MetadataError('Afsnitslisten mangler eller er for stor. Vælg en mere præcis upload.')
        def load(season):
            number = season.get('season_number')
            if type(number) is not int or not 0 <= number <= 10000:
                raise MetadataError('Ugyldigt sæsonnummer fra TMDB')
            data = self.read(f'/3/tv/{identifier}/season/{number}', credential, 'en-US')
            episodes = data.get('episodes', [])
            if data.get('season_number') != number or len(episodes) != season.get('episode_count'):
                raise MetadataError('TMDB-sæsonens afsnitsantal kunne ikke bekræftes. Prøv igen senere.')
            result = []
            for item in episodes:
                if item.get('season_number') != number or type(item.get('episode_number')) is not int or not 0 <= item['episode_number'] <= 10000:
                    raise MetadataError('Afsnitslisten fra TMDB indeholder ugyldige numre')
                result.append({'season': number, 'episode': item['episode_number'],
                               'title': str(item.get('name') or '')[:300], 'airDate': item.get('air_date')})
            return result
        with ThreadPoolExecutor(max_workers=8) as pool:
            episodes = [episode for group in pool.map(load, seasons) for episode in group]
        if not episodes or len(episodes) > 20000 or len({(e['season'], e['episode']) for e in episodes}) != len(episodes):
            raise MetadataError('Afsnitslisten kunne ikke bekræftes entydigt')
        result = {'status': 'ok', 'tmdb': identifier, 'title': str(show.get('name') or '')[:300],
                  'originalTitle': str(show.get('original_name') or '')[:300],
                  'seriesStatus': show.get('status'), 'nextEpisode': show.get('next_episode_to_air'), 'episodes': episodes}
        if self.tvdb is not None:
            if not self.tvdb.configured():
                result['crossCheck'] = {'status': 'not_configured', 'message': 'Krydstjek afventer TheTVDB. Tilknyt API-nøgle og eventuel PIN nederst på siden.'}
            else:
                try:
                    external = self.read(f'/3/tv/{identifier}/external_ids', credential)
                    if external.get('id') != identifier:
                        raise TvdbError('TMDB returnerede en anden serie ved TheTVDB-koblingen')
                    tvdb_id = external.get('tvdb_id')
                    if type(tvdb_id) is not int or not 0 < tvdb_id <= 2147483647:
                        raise TvdbError('TMDB har ikke et TheTVDB-ID for serien. Krydstjek kan ikke bekræftes.')
                    result = crosscheck(result, self.tvdb.catalogue(tvdb_id))
                except (MetadataError, TvdbError, ValueError) as exc:
                    result['crossCheck'] = {'status': 'unavailable', 'message': str(exc)}
        with self.lock:
            if len(self.cache) >= 128: self.cache.clear()
            self.cache[key] = (time.monotonic(), result)
        return result

    def details(self,kind,identifier,season=None,episode=None):
        if kind not in {'movie','tv'} or type(identifier) is not int or not 0<identifier<=2147483647:
            raise MetadataError('Vælg en gyldig film eller serie på TMDB')
        if any(value is not None and (type(value) is not int or not 0<=value<=10000) for value in [season,episode]):
            raise MetadataError('Ugyldigt afsnitsnummer')
        if not self.configured():return {'status':'not_configured','message':'Tilknyt TMDB for at hente handlingen automatisk'}
        key=(kind,identifier,season,episode)
        with self.lock:cached=self.cache.get(key)
        if cached and time.monotonic()-cached[0]<3600:return cached[1]
        credential=json.loads(self.credential.read_text())['credential']
        path=f'/3/{kind}/{identifier}'
        title_data,language=self.localized(path,credential)
        if title_data.get('id')!=identifier:raise MetadataError('TMDB returnerede et andet ID end det valgte')
        plot_data=title_data;scope='movie' if kind=='movie' else 'series'
        if kind=='tv' and season is not None and episode is not None:
            try:
                found,found_language=self.localized(path+f'/season/{season}/episode/{episode}',credential)
                if found.get('season_number')!=season or found.get('episode_number')!=episode:raise MetadataError('TMDB returnerede et andet afsnit end det valgte')
                if str(found.get('overview','')).strip():plot_data=found;language=found_language;scope='episode'
            except MetadataError as exc:
                if 'ikke fundet' not in str(exc):raise
        overview=str(plot_data.get('overview','')).strip()[:12000]
        if not overview:return {'status':'no_overview','message':'TMDB har ingen engelsk handling for det valgte indhold'}
        result={'status':'ok','title':str(title_data.get('title') or title_data.get('name') or '')[:300],'overview':overview,'language':language,'scope':scope,'tmdb':identifier,'kind':kind,'url':f'https://www.themoviedb.org/{kind}/{identifier}'}
        if kind=='movie' and re.fullmatch(r'tt\d{7,10}',str(title_data.get('imdb_id',''))):result['imdb']=title_data['imdb_id']
        if kind == 'movie' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(title_data.get('release_date', ''))):
            result['year'] = title_data['release_date'][:4]
        if scope=='episode':result.update(episodeTitle=str(plot_data.get('name',''))[:300],season=season,episode=episode)
        with self.lock:
            if len(self.cache)>=128:self.cache.clear()
            self.cache[key]=(time.monotonic(),result)
        return result


class MetadataClient:
    def request(self,path,body=None):
        raise NotImplementedError('Native metadata provider required')

    def release(self,name):
        try:return self.request('/release?'+urllib.parse.urlencode({'name':name}))
        except MetadataError:return {'status':'unavailable','message':'Scene-opslaget er midlertidigt utilgængeligt'}

    def details(self,body):
        if not body.tmdb:return {'status':'missing_id','message':'Indsæt et TMDB-link for at hente handlingen automatisk'}
        query={'kind':'movie' if body.kind=='movie' else 'tv','id':body.tmdb}
        if body.kind=='episode':
            reference = next((e for e in body.episode_overrides if e.file == body.videos[0]), None)
            match=re.search(r'(?i)S(\d{2})E(\d{2})',body.title) or re.search(r'(?i)S(\d{2})E(\d{2})',body.videos[0])
            if reference and reference.numbering_source == 'TMDB':query.update(season=reference.season,episode=reference.episode)
            elif reference:pass  # External/manual identities use the clearly labelled series synopsis.
            elif match:query.update(season=int(match[1]),episode=int(match[2]))
        try:return self.request('/details?'+urllib.parse.urlencode(query))
        except MetadataError as exc:return {'status':'unavailable','message':str(exc)}

    def catalogue(self, identifier):
        try: return self.request('/catalogue?' + urllib.parse.urlencode({'id': identifier}))
        except MetadataError as exc: return {'status': 'unavailable', 'message': str(exc)}

