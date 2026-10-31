from contextlib import contextmanager
import json
import sqlite3
import time

from mediaseed.intelligence import Intelligence, metadata


def store(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'old.sqlite') as db: yield db
    return database


def account(**changes):
    return {'status':'ok','username':'synthetic','stats':{'uploaded':100,'downloaded':20,'ratio':5,'buffer':200*1024**3,'warnings':0,'hit_and_runs':0},'ranks':{'automatic':'Member'},'notifications':{'dm':0,'notifications':0,'tickets':0},'events':[],**changes}


def test_account_history_alerts_upgrade_retention_and_identity(tmp_path):
    database=store(tmp_path)
    with database() as db:
        db.execute('CREATE TABLE original (value TEXT)');db.execute("INSERT INTO original VALUES ('preserved')")
    intelligence=Intelligence(database);now=time.time()
    first=account(api_key='private-token',download_link='private-passkey')
    intelligence.account('a',first,now-1000)
    next_account=account(stats={**first['stats'],'warnings':2,'hit_and_runs':1,'buffer':1},notifications={'dm':3,'notifications':1,'tickets':1},events=[{'type':'global_freeleech','title':'Global','endsAt':'2030-01-01T00:00:00Z'}])
    intelligence.account('a',next_account,now)
    intelligence.account('a',next_account,now+1)
    public=intelligence.public([{'id':'a','name':'Test'}])
    assert len(public['history'][0]['samples'])==2
    assert {x['kind'] for x in public['alerts']}=={'warnings','hit_and_runs','low_buffer','dm','notifications','tickets','event'}
    assert len(public['alerts'])==7
    assert 'private-' not in json.dumps(public)
    restored=Intelligence(database)
    assert restored.public([{'id':'a'}])['alerts']==public['alerts']
    assert restored.public([])['alerts']==[]
    restored.account('a',account(username='replacement'),now+2)
    assert restored.public([{'id':'a'}])['alerts']==[]
    assert len(restored.public([{'id':'a'}])['history'][0]['samples'])==1
    with database() as db: assert db.execute('SELECT value FROM original').fetchone()[0]=='preserved'


def test_torrent_observations_and_plans_need_fresh_positive_evidence(tmp_path,monkeypatch):
    database=store(tmp_path);intelligence=Intelligence(database);now=[time.time()]
    monkeypatch.setattr('mediaseed.intelligence.time.time',lambda:now[0])
    identifier='a'*40
    intelligence.remember(identifier,{'id':'a'},{'info_hash':identifier,'seeders':5,'leechers':0,'name':'Public name','size':100})
    row={'hash':identifier,'name':'Test','added_on':1,'uploaded':100,'size':100,'progress':1,'num_leechs':0,'category':'MediaSeed-RSS-synthetic'}
    credit={'credited_ratio':1,'ratio_limit':1,'account_credited_uploaded':100}
    intelligence.observe([row],[credit])
    assert intelligence.public([])['reviewStorage'][0]['hash']==identifier
    now[0]+=15;row['uploaded']=150
    intelligence.observe([row],[credit])
    public=intelligence.public([])
    assert public['results'][0]['observedUpload']==50
    assert public['suggestionOnly'] is True
    restored=Intelligence(database);now[0]+=15
    restored.observe([row],[credit])
    assert restored.public([])['results'][0]['observedUpload']==50
    row['added_on']=2;now[0]+=15
    restored.observe([row],[credit])
    assert restored.public([])['results'][0]['observedUpload']==0
    now[0]+=601
    restored.observe([row],[credit])
    assert restored.public([])['reviewStorage']==[]
    now[0]+=91
    assert restored.public([])['torrentDataFresh'] is False
    assert restored.public([])['results']==[]


def test_metadata_is_bounded_plain_text_and_has_no_private_links():
    public=metadata({'name':'<b>Name</b>','description':'<script>unsafe()</script><p>Hello</p>','info_hash':'a'*40,'download_link':'https://nordicbytes.org/torrents/download/key','screenshots':[{'url':'https://127.0.0.1/private'},{'url':'https://example.org/image.jpg'},{'url':'https://example.org/image?private-key=secret'}],'labels':[{'name':'<img src=x onerror=bad()>Label'}],'media_info':'x'*50000})
    assert public['name']=='Name'
    assert public['description']=='Hello'
    assert public['screenshots']==[{'url':'https://example.org/image.jpg','thumbnail':None}]
    assert public['labels'][0]['name']=='Label'
    assert len(public['media_info'])==24000
    assert 'private' not in json.dumps(public)


def test_stale_accounts_do_not_create_history_or_alerts(tmp_path):
    intelligence=Intelligence(store(tmp_path))
    intelligence.account('a',account(status='stale'))
    assert intelligence.public([{'id':'a'}])['history'][0]['samples']==[]
    assert intelligence.public([{'id':'a'}])['alerts']==[]


def test_downsampled_history_preserves_hidden_gaps_missing_values_and_resets():
    from mediaseed.intelligence import history_samples
    rows=[]
    for i in range(9):
        stats={'ratio':i/10,'buffer':-i,'uploaded':100+i,'downloaded':50+i,'seedbonus':10+i}
        if i==2:stats['seedbonus']=None
        if i==5:stats['uploaded']=1
        rows.append((100+i+(1 if i>=2 else 0),json.dumps({'stats':stats})))
    result=history_samples(rows,4)
    assert [r['at'] for r in result]==[90000,94500,98100]
    assert result[1]['gapBefore'] is True
    assert result[1]['gapMetrics']==['seedbonus']
    assert result[2]['gapBefore'] is False
    assert result[2]['gapMetrics']==['uploaded']
    assert result[2]['stats']['uploaded']==108
    assert len(history_samples(rows,1))==9
    assert history_samples([],1)==[]


def test_history_sampling_metadata_survives_existing_database_restart(tmp_path):
    database=store(tmp_path);intelligence=Intelligence(database);now=time.time()
    for i in range(300):intelligence.account('a',account(),now-(299-i)*900)
    first=intelligence.public([{'id':'a'}],days=7)['history'][0]
    assert first['sampleInterval']==1800
    assert len(first['samples'])<=288
    assert first['samples'][-1]['stats']['ratio']==5
    restored=Intelligence(database)
    assert restored.public([{'id':'a'}],days=7)['history'][0]==first
