"""Credential-free account history, alerts, metadata and local seeding evidence."""
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import math
import re
import time
from urllib.parse import urlsplit
from mediaseed.tracker_sites import origin, matching_accounts


def numeric(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def history_samples(samples, stride, interval=900, keys=('ratio','buffer','uploaded','downloaded','seedbonus')):
    """Keep discontinuities visible even when intermediate observations are omitted."""
    selected=set(range(0,len(samples),stride))
    if samples:selected.add(len(samples)-1)
    result=[];previous=None;gap=False;missing=set()
    for index,(bucket,payload) in enumerate(samples):
        row=json.loads(payload);stats=row.get('stats') or {}
        if previous and bucket-previous[0]>1:gap=True
        for key in keys:
            value=numeric(stats.get(key))
            if value is None:missing.add(key)
            if previous and key in ('uploaded','downloaded','real_uploaded','real_downloaded'):
                before=numeric((previous[1].get('stats') or {}).get(key))
                if value is not None and before is not None and value<before:missing.add(key)
        if index in selected:
            result.append({**row,'at':bucket*interval,'gapBefore':gap,'gapMetrics':sorted(missing)})
            gap=False;missing=set()
        previous=(bucket,row)
    return result


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
    def __init__(self,database):
        self.database=database
        self.latest=[]
        self.checked_at=None
        self.last_observed=0
        with database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS insight_accounts (account TEXT PRIMARY KEY, identity TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS insight_samples (account TEXT, bucket INTEGER, payload TEXT NOT NULL, PRIMARY KEY(account,bucket))')
            db.execute('CREATE TABLE IF NOT EXISTS insight_alerts (id TEXT PRIMARY KEY, account TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS insight_metadata (hash TEXT PRIMARY KEY, checked REAL NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS insight_swarm (hash TEXT PRIMARY KEY, checked REAL NOT NULL, seeders INTEGER, leechers INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS insight_transfers (hash TEXT, added REAL, uploaded REAL, observed REAL, at REAL, PRIMARY KEY(hash,added))')
            db.execute('CREATE TABLE IF NOT EXISTS insight_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')

    def settings(self):
        with self.database() as db: row=db.execute('SELECT payload FROM insight_settings WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {'lowBufferGiB':10}

    def configure(self,settings):
        with self.database() as db: db.execute('INSERT OR REPLACE INTO insight_settings VALUES (1,?)',(json.dumps(settings),))
        return settings

    def account(self,identifier,public,now=None):
        if public.get('status')!='ok': return
        now=time.time() if now is None else now
        # Only explicitly selected public fields persist; no tokens or API URLs.
        current={k:public.get(k) for k in ['stats','ranks','premium','notifications','globalEvents','events','lastSeen']}
        site=public.get('base_url','https://nordicbytes.org')
        identity=hashlib.sha256(((site+'|' if site!='https://nordicbytes.org' else '')+str(public.get('username',''))).encode()).hexdigest()
        threshold=self.settings()['lowBufferGiB']*1024**3
        with self.database() as db:
            row=db.execute('SELECT identity,payload FROM insight_accounts WHERE account=?',(identifier,)).fetchone()
            if row and row[0]!=identity:
                db.execute('DELETE FROM insight_samples WHERE account=?',(identifier,));db.execute('DELETE FROM insight_alerts WHERE account=?',(identifier,));row=None
            previous=json.loads(row[1]) if row else {}
            def alert(kind,value):
                record={'kind':kind,'value':value}
                key=hashlib.sha256(f'{identifier}:{kind}:{value}:{int(now//900)}'.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO insight_alerts VALUES (?,?,?,?)',(key,identifier,now,json.dumps(record)))
            for source,keys in [('stats',['hit_and_runs','warnings']),('notifications',['dm','notifications','tickets'])]:
                for key in keys:
                    value=(current.get(source) or {}).get(key)
                    old=(previous.get(source) or {}).get(key)
                    if numeric(value) is not None and value>0 and (numeric(old) is None or value>old): alert(key,value)
            value=(current.get('stats') or {}).get('buffer');old=(previous.get('stats') or {}).get('buffer')
            current['lowBuffer']=numeric(value) is not None and value<threshold
            if current['lowBuffer'] and not previous.get('lowBuffer'): alert('low_buffer',value)
            expiry=(current.get('premium') or {}).get('expiresAt')
            try: expires=datetime.fromisoformat(expiry.replace('Z','+00:00')).timestamp() if isinstance(expiry,str) else 0
            except (ValueError,OverflowError): expires=0
            current['premiumExpiring']=bool(now<expires<=now+72*3600)
            if current['premiumExpiring'] and not previous.get('premiumExpiring'): alert('premium_expiring',expiry)
            if previous.get('ranks') and current.get('ranks')!=previous['ranks']: alert('rank_changed',current.get('ranks'))
            oldevents=previous.get('events') or []
            for event in current.get('events') or []:
                if event not in oldevents: alert('event',event)
            payload=json.dumps(current,sort_keys=True)
            if not row or row[1]!=payload: db.execute('INSERT OR REPLACE INTO insight_accounts VALUES (?,?,?)',(identifier,identity,payload))
            bucket=int(now//900)
            saved=db.execute('SELECT payload FROM insight_samples WHERE account=? AND bucket=?',(identifier,bucket)).fetchone()
            if not saved or saved[0]!=payload: db.execute('INSERT OR REPLACE INTO insight_samples VALUES (?,?,?)',(identifier,bucket,payload))
            db.execute('DELETE FROM insight_samples WHERE bucket<?',(int((now-90*86400)//900),))
            db.execute('DELETE FROM insight_alerts WHERE id NOT IN (SELECT id FROM insight_alerts ORDER BY created DESC LIMIT 500)')

    def remember(self,identifier,account,attributes):
        if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}',identifier): return
        if str(attributes.get('info_hash','')).lower()!=identifier: return
        record=metadata(attributes);record['account']=account['id'];record['tracker_origin']=origin(account)
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

    def upload_owners(self, rows, accounts):
        """Display-only ownership from exact-hash metadata and current API identity."""
        identifiers = list(dict.fromkeys(str(row.get('hash', '')).lower() for row in rows[:500]
            if isinstance(row, dict) and re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', str(row.get('hash', '')))))
        if not identifiers:
            return {}
        verified = {a['id']: a for a in accounts if isinstance(a.get('username'), str) and a['username'].strip()}
        with self.database() as db:
            saved = db.execute('SELECT hash,payload FROM insight_metadata WHERE hash IN (' + ','.join('?' for _ in identifiers) + ')', identifiers).fetchall()
        trackers = {str(row.get('hash', '')).lower(): row.get('tracker') for row in rows[:500] if isinstance(row, dict)}
        result = {}
        for identifier, payload in saved:
            meta = json.loads(payload)
            account = verified.get(meta.get('account'))
            if not account or account not in matching_accounts([account], trackers.get(identifier)) or meta.get('tracker_origin', 'https://nordicbytes.org') != origin(account):
                continue
            uploader = meta.get('uploader')
            if not isinstance(uploader, str) or not uploader.strip() or uploader.strip().casefold() in {'anonymous', 'anonym', 'anonymous uploader'}:
                continue
            result[identifier] = uploader.strip() == account['username'].strip()
        return result

    def observe(self,rows,credits):
        now=time.time()
        if 0<=now-self.last_observed<5: return
        self.last_observed=now
        result=[]
        with self.database() as db:
            identifiers=[str(row.get('hash','')).lower() for row in rows[:500] if isinstance(row,dict)]
            placeholders=','.join('?' for _ in identifiers)
            cache={r[0]:(r[1],{'seeders':r[2],'leechers':r[3]}) for r in db.execute('SELECT hash,checked,seeders,leechers FROM insight_swarm WHERE hash IN ('+placeholders+')',identifiers)} if identifiers else {}
            for row,credit in zip(rows[:500],credits):
                if not isinstance(row,dict): continue
                identifier=str(row.get('hash','')).lower()
                if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}',identifier): continue
                uploaded=numeric(row.get('uploaded'));added=numeric(row.get('added_on')) or 0
                prior=db.execute('SELECT uploaded,observed,at FROM insight_transfers WHERE hash=? AND added=?',(identifier,added)).fetchone()
                measured=prior[1] if prior else 0
                if uploaded is not None and uploaded>=0 and (not prior or uploaded>=prior[0]):
                    if prior: measured+=uploaded-prior[0]
                    if not prior or uploaded!=prior[0]:db.execute('INSERT OR REPLACE INTO insight_transfers VALUES (?,?,?,?,?)',(identifier,added,uploaded,measured,now))
                at,meta=cache.get(identifier,(0,{}))
                fresh=0<=now-at<=600
                source=str(row.get('category') or '')
                result.append({'hash':identifier,'name':str(row.get('name') or '')[:300],'size':numeric(row.get('size')),'uploaded':uploaded,'observedUpload':measured,'creditedUploaded':credit.get('account_credited_uploaded'),'creditedRatio':credit.get('credited_ratio'),'targetRatio':credit.get('ratio_limit'),'source':source[:100], 'progress':numeric(row.get('progress')),'localLeechers':numeric(row.get('num_leechs')),'seeders':meta.get('seeders') if fresh else None,'leechers':meta.get('leechers') if fresh else None,'metadataCheckedAt':at or None,'seedingSeconds':credit.get('seeding_time',row.get('seeding_time')),'state':str(row.get('state') or '')[:40]})
            db.execute('DELETE FROM insight_swarm WHERE hash NOT IN (SELECT hash FROM insight_metadata)')
        self.latest=result;self.checked_at=now

    def public(self,accounts,days=7):
        now=time.time();ids=[a['id'] for a in accounts]
        with self.database() as db:
            history=[];alerts=[]
            for identifier in ids[:10]:
                samples=db.execute('SELECT bucket,payload FROM insight_samples WHERE account=? AND bucket>=? ORDER BY bucket DESC LIMIT 8640',(identifier,int((now-days*86400)//900))).fetchall()[::-1]
                # Bound the wire/chart size while retaining first/last samples.
                stride=max(1,math.ceil(len(samples)/287))
                history.append({'account':identifier,'sampleInterval':stride*900,'samples':history_samples(samples,stride)})
                alerts.extend({'account':identifier,'createdAt':at,**json.loads(p)} for at,p in db.execute('SELECT created,payload FROM insight_alerts WHERE account=? ORDER BY created DESC LIMIT 30',(identifier,)))
        rows=self.latest if self.checked_at and 0<=now-self.checked_at<=90 else []
        keep=[];review=[];groups={}
        for row in rows:
            size=row.get('size') or 0
            row={**row,'uploadPerGiB':row['uploaded']/size if size and row['uploaded'] is not None else None}
            source=row['source'] or 'manual';group=groups.setdefault(source,{'source':source,'torrents':0,'size':0,'observedUpload':0,'totalUpload':0})
            group['torrents']+=1;group['size']+=size;group['observedUpload']+=row['observedUpload'];group['totalUpload']+=row['uploaded'] or 0
            reasons=[]
            if row.get('localLeechers') is not None and row['localLeechers']>0:reasons.append('connected_downloaders')
            if row.get('leechers') is not None and row['leechers']>0:reasons.append('tracker_demand')
            if row.get('seeders') is not None and row['seeders']<=2:reasons.append('rare_seed')
            if reasons:keep.append({**row,'reasons':reasons})
            # Suggestions only: never approve removal, infer tracker H&R clearance
            # or map shared files to reclaimable bytes from this estimate.
            goal=row.get('creditedRatio');target=row.get('targetRatio')
            if row.get('progress')==1 and numeric(goal) is not None and numeric(target) is not None and goal>=target and row.get('localLeechers')==0 and row.get('leechers')==0 and row.get('seeders') is not None and row['seeders']>2:
                review.append({**row,'reasons':['local_goal_met','no_observed_demand']})
        for group in groups.values():group['uploadPerGiB']=group['totalUpload']/group['size'] if group['size'] else None
        return {'checkedAt':now,'torrentCheckedAt':self.checked_at,'torrentDataFresh':bool(rows),'bounded':True,'torrentLimit':500,'accounts':accounts,'history':history,'alerts':sorted(alerts,key=lambda x:x['createdAt'],reverse=True)[:50],'settings':self.settings(),'keepSeeding':sorted(keep,key=lambda r:(-(r.get('localLeechers') or 0),r.get('seeders') or 0))[:30],'reviewStorage':sorted(review,key=lambda r:-(r.get('size') or 0))[:30],'results':sorted(groups.values(),key=lambda r:-r['observedUpload'])[:100],'topTorrents':sorted(rows,key=lambda r:-r['observedUpload'])[:20],'selectedBytes':sum(r.get('size') or 0 for r in rows),'remainingBytes':sum(max(0,(r.get('size') or 0)*(1-(r.get('progress') or 0))) for r in rows),'suggestionOnly':True}
