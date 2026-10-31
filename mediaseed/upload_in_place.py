"""Small, journalled edits to an owned completed conversion; never source torrents."""
import ctypes,errno,hashlib,json,os,re
from pathlib import Path
from mediaseed.conversion import atomic,fingerprint
from mediaseed.conversion_naming import output_name


def rename_exclusive(source,target):
    if source==target:return
    function=getattr(ctypes.CDLL(None,use_errno=True),'renameat2',None)
    if function is not None:
        if not function(-100,os.fsencode(source),-100,os.fsencode(target),1):return
        error=ctypes.get_errno()
        if error==errno.EEXIST:raise ValueError('Det nye filnavn findes allerede. Eksisterende filer overskrives ikke')
        if error not in {errno.EINVAL,errno.ENOSYS,errno.EOPNOTSUPP}:raise OSError(error,os.strerror(error))
    # MergerFS can reject RENAME_NOREPLACE. Linking publishes the new name
    # exclusively without copying bytes; the journal covers the two-name window.
    try:os.link(source,target,follow_symlinks=False)
    except FileExistsError:raise ValueError('Det nye filnavn findes allerede. Eksisterende filer overskrives ikke') from None
    try:source.unlink()
    except OSError:
        if target.exists() and fingerprint(target)==fingerprint(source):target.unlink()
        raise


class InPlaceFiles:
    def __init__(self,data,conversions,unseeded):
        self.root=Path(data)/'in-place-journal';self.root.mkdir(exist_ok=True,mode=0o700)
        self.conversions,self.unseeded=conversions,unseeded
    def locate(self,storage,path):
        client=storage.client_path(path)
        base=self.conversions.storage.downloads/'media/converted-movies'
        target=None
        for prefix in ['/downloads/media/converted-movies','/downloads/media/Konverterede Film']:
            if client and client.startswith(prefix+'/'):target=base/client[len(prefix)+1:];break
        if target is None or not target.is_file() or target.resolve()!=target or fingerprint(target)!=fingerprint(path):
            raise ValueError('Klargøring i samme mappe understøtter kun færdige filer i Konverterede Film')
        result=self.verify(target)
        self.unseeded('/downloads/media/converted-movies/'+target.relative_to(base).as_posix())
        return result
    def verify(self,target):
        base=self.conversions.storage.downloads/'media/converted-movies'
        if not target.is_relative_to(base) or not target.is_file() or target.resolve()!=target:
            raise ValueError('Filen er ikke en verificeret konvertering')
        for job in self.conversions.records():
            if job.get('status')!='complete':continue
            directory=Path(job['destination'])
            try:relative=target.relative_to(directory).as_posix()
            except ValueError:continue
            receipt=directory/'.mediaseed-converted.json'
            if receipt.is_symlink() or not receipt.is_file():raise ValueError('Konverteringens kvittering mangler')
            record=json.loads(receipt.read_text())
            if record.get('identities',{}).get(relative)!=fingerprint(target):raise ValueError('Den konverterede fil er ændret; kontrollér den først')
            return target,job,receipt,record,relative
        raise ValueError('Filen tilhører ikke en færdig, verificeret MediaSeed-konvertering')
    def credited_conversion(self,storage,path,credit):
        """Read-only proof from a server-owned conversion record and receipt."""
        try:
            base=self.conversions.storage.downloads/'media/converted-movies'
            target=Path(path)
            if not target.is_relative_to(base):
                client=storage.client_path(target)
                for prefix in ['/downloads/media/converted-movies','/downloads/media/Konverterede Film']:
                    if client and client.startswith(prefix+'/'):target=base/client[len(prefix)+1:];break
            target,job,_,_,relative=self.verify(target)
            if fingerprint(target)!=fingerprint(path):return None
            source=str(job.get('name',''))
            normalise=lambda value:re.sub(r'[^a-z0-9]','',value.casefold())
            if not source or normalise(source) not in normalise(credit):return None
            entry=next((f for f in job.get('files',[]) if output_name(f)==relative),None)
            if not entry or not entry.get('name'):return None
            return {'id':job['id'],'source':source,'originalFile':entry['name']}
        except (OSError,ValueError,KeyError,TypeError):return None
    def begin(self,identifier,storage,path,new_name=None,nfo=False):
        target,job,receipt,record,relative=self.locate(storage,path)
        if new_name and (Path(new_name).name!=new_name or not new_name.endswith('.mkv')):raise ValueError('Vælg et almindeligt MKV-filnavn')
        new=target.with_name(new_name or target.name)
        nfo_path=new.with_suffix('.nfo') if nfo else None
        if new!=target and os.path.lexists(new):raise ValueError('Det nye videonavn findes allerede')
        if nfo_path and os.path.lexists(nfo_path):raise ValueError('NFO-filen findes allerede og bliver ikke overskrevet')
        value={'upload':identifier,'old':str(target),'new':str(new),'identity':fingerprint(target),'job':job,'receipt':str(receipt),'record':record,'relative':relative,'nfo':str(nfo_path) if nfo_path else None}
        if new_name and len(job['files'])==1 and target.parent==Path(job['destination']):
            folder=target.parent;renamed=folder.with_name(new.stem)
            if renamed!=folder:
                if os.path.lexists(renamed):raise ValueError('Mappen med det nye releasenavn findes allerede')
                value.update(folderOld=str(folder),folderNew=str(renamed))
        journal=self.root/(identifier+'.json');atomic(journal,value)
        return Edit(self,journal,value)
    def recover(self,database):
        for journal in self.root.glob('*.json'):
            value=json.loads(journal.read_text())
            if value.get('type')=='prepared-nfo':
                from mediaseed.upload_nfo_repair import NfoEdit
                NfoEdit.recover(journal,value,database);continue
            with database() as db:row=db.execute('SELECT state FROM upload_jobs WHERE id=?',(value['upload'],)).fetchone()
            if row and row[0]=='done':journal.unlink();continue
            Edit(self,journal,value).rollback()


class Edit:
    def __init__(self,owner,journal,value):self.owner,self.journal,self.value=owner,journal,value
    def apply(self,nfo_content=None):
        v=self.value;old,new=Path(v['old']),Path(v['new'])
        if fingerprint(old)!=v['identity']:raise ValueError('Filen ændrede sig før klargøringen')
        rename_exclusive(old,new)
        if nfo_content is not None:
            path=Path(v['nfo'])
            with path.open('xb') as stream:
                stat=os.fstat(stream.fileno());v['nfoIdentity']=[stat.st_dev,stat.st_ino];atomic(self.journal,v)
                stream.write(nfo_content);stream.flush();os.fsync(stream.fileno())
        record=json.loads(json.dumps(v['record']));job=json.loads(json.dumps(v['job']))
        relative=new.relative_to(Path(job['destination'])).as_posix()
        record['identities'].pop(v['relative']);record['identities'][relative]=fingerprint(new)
        for entry in job['files']:
            if output_name(entry)==v['relative']:entry['output']=relative
        receipt=Path(v['receipt'])
        if v.get('folderNew'):
            folder,renamed=Path(v['folderOld']),Path(v['folderNew'])
            if os.path.lexists(renamed):raise ValueError('Mappen med det nye releasenavn findes allerede')
            folder.rename(renamed)
            new=renamed/new.name;receipt=renamed/receipt.name
            job.update(destination=str(renamed),previousDestinations=[*job.get('previousDestinations',[]),str(folder)])
        atomic(receipt,record);self.owner.conversions.save(job)
        return new
    def commit(self):self.journal.unlink(missing_ok=True)
    def rollback(self):
        v=self.value;old,new=Path(v['old']),Path(v['new'])
        if v.get('folderNew'):
            folder,renamed=Path(v['folderOld']),Path(v['folderNew'])
            if renamed.exists():
                if folder.exists() or fingerprint(renamed/new.name)!=v['identity']:
                    raise ValueError('Afbrudt mappeomdøbning skal kontrolleres; eksisterende filer er bevaret')
                renamed.rename(folder)
        if v.get('nfoIdentity'):
            path=Path(v['nfo'])
            if path.exists() and not path.is_symlink():
                stat=path.stat()
                if [stat.st_dev,stat.st_ino]==v['nfoIdentity']:path.unlink()
        if old!=new and new.exists() and fingerprint(new)==v['identity']:
            if old.exists() and fingerprint(old)==v['identity']:new.unlink()
            elif not old.exists():rename_exclusive(new,old)
        if old.exists() and fingerprint(old)==v['identity']:
            atomic(Path(v['receipt']),v['record']);self.owner.conversions.save(v['job'])
        else:raise ValueError('Afbrudt omdøbning skal kontrolleres; eksisterende filer er bevaret')
        self.journal.unlink(missing_ok=True)
