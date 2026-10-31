import sqlite3
from contextlib import contextmanager
from mediaseed.download_history import DownloadHistory


def test_download_history_survives_event_expiry_restart_and_upgrade(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'old.db') as db:
            yield db
    with database() as db:
        db.execute('CREATE TABLE settings (value TEXT)')
        db.execute("INSERT INTO settings VALUES ('preserved')")
    history = DownloadHistory(database)
    history.accounts = lambda: [{'name':'NB','status':'ok','checkedAt':100,'globalEvents':[{'type':'global_freeleech','endsAt':'2030-01-01T00:00:00Z'}]}]
    row = {'hash':'a'*40,'added_on':1,'downloaded':0,'progress':0,'tracker':'https://t.nordicbytes.org/secret/announce'}
    with database() as db:
        history.observe(db,row,{},100)
        row['downloaded']=100
        record=history.observe(db,row,{},110)
        assert record['observations'][0]['globalFreeleech'] is True
    history=DownloadHistory(database)
    row.update(downloaded=200,progress=1)
    with database() as db:
        record=history.observe(db,row,{},120)
        assert record['observations'][0]['globalFreeleech'] is True
        assert record['observations'][1]['globalFreeleech'] is None
        assert record['completedAt']==120
        assert 'secret' not in str(record)
        assert db.execute('SELECT value FROM settings').fetchone()[0]=='preserved'
        # Re-added torrents cannot inherit previous download conditions.
        row['added_on']=2
        record=history.observe(db,row,{},130)
        assert record['observations']==[] and record['unknownBytes']==200


def test_completed_old_download_is_not_backfilled(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'new.db') as db:
            yield db
    history=DownloadHistory(database)
    history.accounts=lambda:[{'status':'ok','checkedAt':100,'globalEvents':[{'type':'global_freeleech','endsAt':'2030-01-01T00:00:00Z'}]}]
    row={'hash':'a'*40,'downloaded':200,'progress':1,'tracker':'https://t.nordicbytes.org/a'}
    with database() as db:
        assert history.observe(db,row,{},100)['observations']==[]
        assert history.observe(db,row,{},110)['observations']==[]


def test_finished_records_do_not_write_or_read_accounts_again(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'finished.db') as db:
            yield db
    history=DownloadHistory(database)
    row={'hash':'b'*40,'added_on':1,'downloaded':100,'progress':1}
    with database() as db:
        original=history.observe(db,row,{},100)
        before=db.total_changes
        history.accounts=lambda: (_ for _ in ()).throw(AssertionError('Unneeded account query'))
        assert history.observe(db,row,{},200)==original
        assert db.total_changes==before


def test_uncertain_intervals_do_not_invent_double_upload_or_green(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'interval.db') as db:
            yield db
    history=DownloadHistory(database)
    row={'hash':'c'*40,'added_on':1,'downloaded':0,'progress':0}
    credit={'green_active':True,'green_pending':False,'tracker_benefits':{'freeleech':100,'doubleUpload':True}}
    with database() as db:
        history.observe(db,row,credit,100)
        row['downloaded']=100
        record=history.observe(db,row,credit,110)
        assert record['observations'][0]['doubleUpload'] is True
        row['downloaded']=200
        record=history.observe(db,row,credit,300)
        assert record['observations'][-1]['green'] is None
        assert record['observations'][-1]['doubleUpload'] is None
        assert record['observations'][-1]['freeleech'] is None


def test_global_freeleech_only_applies_to_matching_tracker(tmp_path):
    @contextmanager
    def database():
        with sqlite3.connect(tmp_path/'different-sites.db') as db:yield db
    history=DownloadHistory(database)
    accounts=[{'id':'nb','name':'Same name','provider':'nordicbytes','status':'ok','checkedAt':100,'globalEvents':[{'type':'global_freeleech','endsAt':'2030-01-01T00:00:00Z'}]}, {'id':'other','name':'Same name','provider':'unit3d','status':'ok','checkedAt':100,'globalEvents':[]}]
    credit={'tracker_id':'other','tracker_name':'Same name','green_pending':False,'green_active':False}
    assert history.conditions({'tracker':'https://t.sagmire.org/announce'},credit,110,accounts)['globalFreeleech'] is False
    credit['tracker_id']='nb'
    assert history.conditions({'tracker':'https://t.nordicbytes.org/announce'},credit,110,accounts)['globalFreeleech'] is True
