import json,sqlite3
from pathlib import Path
from contextlib import contextmanager
import pytest
from mediaseed.upload_engine.upload_helper import make_torrent,decode_torrent
from mediaseed.upload_engine.nfo import box
from mediaseed.upload_nfo_repair import updated_torrent,original_file_text,NfoEdit

@pytest.mark.parametrize('size',[0,100,256*1024,3*256*1024+71])
def test_only_tail_hashes_change_and_result_matches_full_hashing(tmp_path,size):
 video=tmp_path/'Movie.mkv';video.write_bytes(b'x'*size);nfo=tmp_path/'Movie.nfo';old=b'old nfo';new=b'new nfo '*40000;nfo.write_bytes(old)
 files=[(video,video.name),(nfo,nfo.name)]
 original=make_torrent(files,'Movie','https://tracker.invalid/announce/synthetic')
 result=updated_torrent(original,video,video.name,nfo.name,old,new)
 nfo.write_bytes(new)
 assert result==make_torrent(files,'Movie','https://tracker.invalid/announce/synthetic')
 assert video.read_bytes()==b'x'*size


def test_wrong_original_file_is_replaced_only_in_credit_box():
 old='Converted.2025.HEVC.MDTECH.mkv';source='Source.2025.2160p.BluRay.REMUX-Original.mkv'
 before='\n'.join(box('UPLOADER DESCRIPTION',['Original file: '+old])+box('ORIGINAL SOURCE AND CREDITS',['Original source: Source', 'Original file: '+old]))
 after=original_file_text(before,old,source)
 assert 'Original file: '+source in after and 'Original file: '+old in after
 assert original_file_text(before,old,source).count(source)==1
 with pytest.raises(ValueError):original_file_text(before,'Unrelated.mkv',source)


@pytest.mark.parametrize('committed',[False,True])
def test_file_edit_recovery_respects_database_commit(tmp_path,committed):
 path=tmp_path/'Movie.nfo';path.write_bytes(b'old');journal=tmp_path/'journals';journal.mkdir()
 db=sqlite3.connect(':memory:');db.execute('CREATE TABLE upload_jobs(id TEXT,result TEXT)');db.execute('INSERT INTO upload_jobs VALUES(?,?)',('a'*24,json.dumps({'nfoRevision':'revision'} if committed else {})))
 @contextmanager
 def database():yield db
 edit=NfoEdit.prepare(journal,'a'*24,'revision',{path:b'new'})
 NfoEdit.recover(edit.journal,edit.value,database)
 assert path.read_bytes()==(b'new' if committed else b'old') and not list(journal.glob('*.json'))
 if committed:assert len(list((journal/'nfo-backups').glob('*.json')))==1
 db.close()
