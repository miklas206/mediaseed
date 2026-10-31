"""Repair an unpublished owned NFO and its torrent without rereading video bodies."""
import base64,hashlib,json,math,os,re,secrets
from pathlib import Path
from mediaseed.conversion import atomic
from mediaseed.upload_engine.nfo import box


def original_file_text(text,old,new):
    block=re.search(r'(?ms)^  ╓─ ORIGINAL SOURCE AND CREDITS[^\n]*\n.*?^  ╙[^\n]*',text)
    match=re.search(r'(?ms)^  ║  Original file: .*?(?=^  ╙)',block[0]) if block else None
    if not match:raise ValueError('Den gemte NFO har ikke det forventede originalfil-felt')
    normal=lambda s:re.sub('[^a-z0-9]','',s.casefold())
    if normal(old) not in normal(match[0]):raise ValueError('Originalfil-feltet er ændret manuelt; kontrollér NFO før opdatering')
    replacement='\n'.join(box('', ['Original file: '+new])[2:-2])+'\n'
    return text[:block.start()+match.start()]+replacement+text[block.start()+match.end():]


def subtitle_field_text(text,summary):
    block=re.search(r'(?ms)^  ╓─ MEDIA INFO[^\n]*\n.*?^  ╙[^\n]*',text)
    match=re.search(r'(?ms)^  ║  SUBTITLES .*?(?=^  ║  FILE SIZE|^  ╙)',block[0]) if block else None
    if not match:raise ValueError('Den gemte NFO har ikke det forventede undertekstfelt')
    replacement='\n'.join(box('', ['SUBTITLES    '+summary])[2:-3])+'\n'
    return text[:block.start()+match.start()]+replacement+text[block.start()+match.end():]


def updated_torrent(data,video,video_name,nfo_name,old,new):
    from mediaseed.upload_engine.upload_helper import decode_torrent,bencode
    torrent=decode_torrent(data);info=torrent[b'info'];files=info.get(b'files',[])
    if len(files)!=2 or files[0].get(b'path')!=[video_name.encode()] or files[1].get(b'path')!=[nfo_name.encode()] or files[0].get(b'length')!=video.stat().st_size or files[1].get(b'length')!=len(old):
        raise ValueError('Torrentens filer svarer ikke til den klargjorte video og NFO')
    length=info.get(b'piece length');pieces=info.get(b'pieces',b'');size=video.stat().st_size
    if not isinstance(length,int) or not 0<length<=16*1024**2 or len(pieces)!=20*math.ceil((size+len(old))/length):raise ValueError('Torrentens stykker kunne ikke bekræftes')
    complete=size//length
    with video.open('rb') as stream:stream.seek(complete*length);tail=stream.read(length)
    hashes=lambda content:b''.join(hashlib.sha1(content[n:n+length]).digest() for n in range(0,len(content),length))
    if pieces[complete*20:]!=hashes(tail+old):raise ValueError('Videoens afslutning eller NFO er ændret siden torrenten blev lavet')
    info[b'pieces']=pieces[:complete*20]+hashes(tail+new);files[1][b'length']=len(new)
    encoded=bencode(torrent)
    if len(encoded)>1024**2:raise ValueError('Den opdaterede torrent overskrider 1 MiB')
    return encoded


def write_bytes(path,data,mode):
    temporary=path.with_name('.pending-'+secrets.token_hex(12))
    try:
        with temporary.open('xb') as stream:os.fchmod(stream.fileno(),mode);stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


class NfoEdit:
    def __init__(self,journal,value):self.journal,self.value=journal,value
    @classmethod
    def prepare(cls,root,identifier,revision,changes,previous_result=None):
        value={'type':'prepared-nfo','upload':identifier,'revision':revision,'files':[],'previousResult':previous_result}
        for path,new in changes.items():
            if not path.is_file() or path.resolve()!=path:raise ValueError('Et klargjort bilag er utilgængeligt')
            value['files'].append({'path':str(path),'old':base64.b64encode(path.read_bytes()).decode(),'newHash':hashlib.sha256(new).hexdigest(),'mode':path.stat().st_mode&0o777})
        journal=root/(identifier+'-nfo.json');atomic(journal,value);edit=cls(journal,value)
        try:
            for path,new in changes.items():write_bytes(path,new,path.stat().st_mode&0o777)
        except Exception:edit.rollback();raise
        return edit
    def commit(self):
        archive=self.journal.parent/'nfo-backups';archive.mkdir(exist_ok=True,mode=0o700)
        target=archive/(self.value['upload']+'-'+self.value['revision']+'.json');self.journal.chmod(0o600);os.replace(self.journal,target)
    def rollback(self):
        for item in reversed(self.value['files']):
            path=Path(item['path']);old=base64.b64decode(item['old'])
            if path.resolve()!=path:raise ValueError('Afbrudt NFO-opdatering kræver kontrol')
            current=path.read_bytes()
            if current==old:continue
            if hashlib.sha256(current).hexdigest()!=item['newHash']:raise ValueError('Et bilag er ændret; opdateringens backup er bevaret')
            write_bytes(path,old,item['mode'])
        self.journal.unlink(missing_ok=True)
    @classmethod
    def recover(cls,journal,value,database):
        with database() as db:row=db.execute('SELECT result FROM upload_jobs WHERE id=?',(value['upload'],)).fetchone()
        edit=cls(journal,value)
        if row and json.loads(row[0]).get('nfoRevision')==value['revision']:edit.commit()
        else:edit.rollback()
