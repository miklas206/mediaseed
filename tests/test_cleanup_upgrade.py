import json
import sqlite3
from mediaseed.cleanup import CleanupPolicy, reached
from mediaseed.cleanup_upgrade import upgrade_cleanup_hours


def test_fifty_active_hours_boundary():
    policy=CleanupPolicy(mode='time').model_dump()
    assert policy['hours']==50
    row={'state':'stalledUP','progress':1,'ratio':0,'num_leechs':0,'upspeed':0}
    for seconds in [3600,49*3600,50*3600-1]:
        assert not reached(policy,{**row,'seeding_time':seconds})
    assert reached(policy,{**row,'seeding_time':50*3600})
    assert not reached(policy,{**row,'seeding_time':50*3600,'progress':.99})
    assert not reached(policy,{**row,'seeding_time':50*3600,'num_leechs':1})


def test_upgrade_extends_old_rules_and_feeds_once_preserving_identity_and_credentials():
    db=sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE torrent_cleanup(id TEXT PRIMARY KEY,payload TEXT);CREATE TABLE settings(kind TEXT,id TEXT,payload TEXT);')
    records={}
    for i,(mode,hours) in enumerate([('time',1),('time',49),('both',49),('either',49),('time',72),('ratio',1),('never',1)]):
        record={'mode':mode,'hours':hours,'hashes':['a'*40],'tag':'unchanged','delete_files':True,'status':'active','added_on':123}
        records[str(i)]=record.copy();db.execute('INSERT INTO torrent_cleanup VALUES (?,?)',(str(i),json.dumps(record)))
    feed={'url':'https://tracker.example/rss?private=synthetic','cleanup':{'mode':'time','hours':49,'ratio':1},'location':'movies','enabled':True}
    db.execute('INSERT INTO settings VALUES (?,?,?)',('feeds','f',json.dumps(feed)))
    upgrade_cleanup_hours(db)
    for identifier,payload in db.execute('SELECT id,payload FROM torrent_cleanup'):
        expected=records[identifier].copy()
        if expected['mode'] in {'time','both','either'} and expected['hours']<50:expected['hours']=50
        assert json.loads(payload)==expected
    updated=json.loads(db.execute('SELECT payload FROM settings').fetchone()[0]);feed['cleanup']['hours']=50;assert updated==feed
    # Subsequent explicit user edits are not repeatedly rewritten at restart.
    edited={**records['0'],'hours':60};db.execute('UPDATE torrent_cleanup SET payload=? WHERE id=?',(json.dumps(edited),'0'))
    upgrade_cleanup_hours(db)
    assert json.loads(db.execute("SELECT payload FROM torrent_cleanup WHERE id='0'").fetchone()[0])==edited
    assert db.execute('SELECT COUNT(*) FROM cleanup_migrations').fetchone()[0]==1


def test_fresh_database_without_settings_is_supported():
    db=sqlite3.connect(':memory:');db.execute('CREATE TABLE torrent_cleanup(id TEXT PRIMARY KEY,payload TEXT)')
    upgrade_cleanup_hours(db);upgrade_cleanup_hours(db)
    assert db.execute('SELECT COUNT(*) FROM cleanup_migrations').fetchone()[0]==1
