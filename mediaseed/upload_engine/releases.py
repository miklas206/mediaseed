"""Exact scene metadata and original NFOs, using the public srrDB API."""
import base64
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import zlib


def release_name(value):
    value=value.rsplit('/',1)[-1]
    value=re.sub(r'(?i)\.(mkv|mp4|m4v|avi|ts|m2ts|mov)$','',value)
    return re.sub(r'\[[^\]]*\]','',value).strip()


def original_group(value):
    """Recognize explicit release suffixes, including '(1080p ... Ghost)'."""
    name = release_name(value)
    match = re.search(r'-([A-Za-z0-9][A-Za-z0-9_-]{0,31})$', name)
    if match and not re.search(r'(?i)(?:WEB-DL|WEB-Rip|source-[a-z0-9]+)$', name):
        return match[1]
    match = re.search(r'\(([^()]*)\)$', name)
    if match and re.search(r'(?i)\b(?:\d{3,4}p|BluRay|WEB|[xh]26[45])\b', match[1]):
        tag = match[1].split()[-1]
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', tag) and not re.fullmatch(r'(?i)(?:BluRay|WEB|WEB-DL|HEVC|AVC|[xh]26[45]|HDR|SDR|DV|AAC|EAC3|DTS|Atmos|10bit)', tag):
            return tag
    return ''


def preserve_group_title(body, title):
    if body.release_owner != 'other':
        return title
    # Preserve the original pack's technical wording when its folder explicitly
    # identifies the same season; never use an episode name as a pack title.
    if body.kind in {'season', 'series'}:
        folder = body.folder.rsplit('/', 1)[-1]
        marker = re.search(r'(?i)S\d{2}(?:-S\d{2})?(?!\d|E\d)', title)
        if marker and re.search(r'(?i)(?:^|[ ._(])'+re.escape(marker[0])+r'(?!\d|E\d)', folder):
            title = folder
    title = re.sub(r'(?i)(?:[ ._-]'+re.escape(body.original_group)+r')\)?$', '', title)
    title = re.sub(r'[^A-Za-z0-9._-]+', '.', title).strip('._-')
    result = title + '-' + body.original_group
    if len(result) > 240:
        raise ValueError('Originaltitlen er for lang. Forkort trackerens titel uden at ændre gruppenavnet.')
    return result


class Releases:
    def __init__(self,opener):
        self.opener=opener
        self.cache={}
        self.lock=threading.Lock()
        self.last_request=0.0

    def read(self,url,limit):
        # Bound request rate to this community metadata service.
        delay=0.5-(time.monotonic()-self.last_request)
        if delay>0:time.sleep(delay)
        self.last_request=time.monotonic()
        with self.opener.open(urllib.request.Request(url,headers={'User-Agent':'MediaUpload/0.1.10 (release metadata)','Accept':'application/json, text/plain'}),timeout=6) as response:
            data=response.read(limit+1)
            if len(data)>limit:raise ValueError('Response too large')
            return data

    def lookup(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,239}',name):
            return {'status':'unknown','message':'Releasenavnet kunne ikke slås op'}
        with self.lock:
            cached=self.cache.get(name.casefold())
            if cached and time.monotonic()-cached[0]<(60 if cached[1].get('status')=='unavailable' else 3600):return cached[1]
            result=self.fetch(name)
            if len(self.cache)>=32:self.cache.clear()
            self.cache[name.casefold()]=(time.monotonic(),result)
            return result

    def fetch(self,name):
        try:
            data=json.loads(self.read('https://api.srrdb.com/v1/details/'+urllib.parse.quote(name,safe=''),1024**2))
            found=data.get('name','')
            if found.casefold()!=name.casefold():
                return {'status':'unknown','message':'Intet præcist Scene-match i srrDB. Fravær beviser ikke P2P.'}
            result={'status':'ok','releaseType':'scene','release':found,'source':'srrDB','url':'https://www.srrdb.com/release/details/'+urllib.parse.quote(found,safe=''),'nfo':None}
            for entry in data.get('files',[]):
                filename=entry.get('name','')
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,239}\.nfo',filename,re.I):continue
                size=entry.get('size')
                if type(size) is not int or not 0<size<=262144:continue
                url='https://www.srrdb.com/download/file/'+urllib.parse.quote(found,safe='')+'/'+urllib.parse.quote(filename,safe='')
                try:
                    raw=self.read(url,262144)
                    if len(raw)!=size or not re.fullmatch(r'[a-fA-F0-9]{8}',entry.get('crc','')) or f'{zlib.crc32(raw):08X}'!=entry['crc'].upper():continue
                    if raw.lstrip().lower().startswith((b'<!doctype',b'<html')):continue
                    result['nfo']={'name':filename,'content':base64.b64encode(raw).decode(),'url':url,'crc':entry['crc'].upper()}
                    break
                except (OSError,ValueError):continue
            return result
        except (OSError,ValueError,TypeError,AttributeError):
            return {'status':'unavailable','message':'Scene-opslaget er midlertidigt utilgængeligt. Status er ikke gættet.'}
