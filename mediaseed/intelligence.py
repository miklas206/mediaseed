"""Bounded public torrent metadata; private tracker credentials are never stored."""
import ipaddress
import json
import math
import re
import time
from html.parser import HTMLParser
from urllib.parse import urlsplit


def numeric(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.blocked = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}: self.blocked += 1
        if tag in {'p','br','li','div'}: self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in {'script','style'} and self.blocked: self.blocked -= 1
    def handle_data(self, data):
        if not self.blocked: self.parts.append(data)


def text(value, limit=300):
    if not isinstance(value, str): return None
    parser = PlainText()
    parser.feed(value[:max(2000,limit*2)])
    return ''.join(parser.parts).strip()[:limit]


def link(value):
    if not isinstance(value,str) or len(value)>1000: return None
    try:
        u=urlsplit(value)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.port not in (None,443): return None
        host=u.hostname.lower()
        if '.' not in host or host.endswith(('.local','.internal','.localhost')): return None
        try:
            if not ipaddress.ip_address(host).is_global: return None
        except ValueError: pass
        # Do not publish signed/private download links as media resources.
        if u.query or u.fragment or '/download/' in u.path or '/announce' in u.path: return None
        return value
    except ValueError: return None


def metadata(attributes):
    a=attributes if isinstance(attributes,dict) else {}
    result={k:text(a.get(k),n) for k,n in [('name',300),('release_year',20),('category',100),('type',100),('resolution',100),('uploader',100),('created_at',80),('description',16000),('media_info',24000)]}
    for key in ['size','num_file','seeders','leechers','times_completed','category_id','type_id','resolution_id','tmdb_id','imdb_id','tvdb_id','mal_id','igdb_id','tpdb_id']:
        value=numeric(a.get(key)); result[key]=value if value is not None and value>=0 else None
    try: free=float(str(a.get('freeleech','')).rstrip('%'))
    except ValueError: free=None
    result.update(freeleech=free if free in (0,25,50,75,100) else None)
    for key in ['double_upload','featured','internal','refundable']:
        result[key]=a.get(key) if type(a.get(key)) is bool else None
    meta=a.get('meta') if isinstance(a.get('meta'),dict) else {}
    result['poster']=link(meta.get('poster'));result['genres']=text(meta.get('genres'),500)
    result['labels']=[{'name':text(x.get('name'),100),'group':text(x.get('group'),100)} for x in (a.get('labels') or [])[:30] if isinstance(x,dict)] if isinstance(a.get('labels'),list) else []
    result['screenshots']=[{'url':link(x.get('url')),'thumbnail':link(x.get('thumb_url'))} for x in (a.get('screenshots') or [])[:20] if isinstance(x,dict) and link(x.get('url'))] if isinstance(a.get('screenshots'),list) else []
    result['files']=[{'name':text(x.get('name'),500),'size':numeric(x.get('size'))} for x in (a.get('files') or [])[:1000] if isinstance(x,dict)] if isinstance(a.get('files'),list) else []
    return result


class Intelligence:
    def __init__(self, database):
        self.database = database
        with database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS insight_metadata (hash TEXT PRIMARY KEY, checked REAL NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS insight_swarm (hash TEXT PRIMARY KEY, checked REAL NOT NULL, seeders INTEGER, leechers INTEGER)')

    def remember(self,identifier,account,attributes):
        if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}',identifier): return
        if str(attributes.get('info_hash','')).lower()!=identifier: return
        record=metadata(attributes);record['account']=account['id']
        with self.database() as db:
            previous=db.execute('SELECT payload FROM insight_metadata WHERE hash=?',(identifier,)).fetchone()
            if previous:
                saved=json.loads(previous[0])
                if saved.get('account')==account['id']:
                    # Sparse search results must not erase details already read
                    # from the full record. Live benefit/swarm facts stay fresh.
                    live={'account','seeders','leechers','times_completed','freeleech','double_upload','featured','internal','refundable'}
                    for key,value in saved.items():
                        if key not in live and record.get(key) in (None,'',[]):
                            record[key]=value
            db.execute('INSERT OR REPLACE INTO insight_metadata VALUES (?,?,?)',(identifier,time.time(),json.dumps(record)))
            db.execute('INSERT OR REPLACE INTO insight_swarm VALUES (?,?,?,?)',(identifier,time.time(),record.get('seeders'),record.get('leechers')))
            db.execute('DELETE FROM insight_metadata WHERE hash IN (SELECT hash FROM insight_metadata ORDER BY checked DESC LIMIT -1 OFFSET 2000)')

    def media(self,identifier):
        with self.database() as db: row=db.execute('SELECT checked,payload FROM insight_metadata WHERE hash=?',(identifier,)).fetchone()
        return {'checkedAt':row[0],**json.loads(row[1])} if row else None
