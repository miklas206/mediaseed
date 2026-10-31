"""Install native upload mounts idempotently; preserve other services and media."""
import argparse,json,os,subprocess
from pathlib import Path


def configuration(config,state,media,downloads,image=None):
    value=json.loads(json.dumps(config));app=value['services']['app']
    if image:app['image']=image
    env=app.setdefault('environment',{});env.pop('MEDIASEED_UPLOADER_CONFIG',None)
    env.update(MEDIASEED_UPLOAD_WORK='/work',MEDIASEED_UPLOAD_EXPORT_ROOT='/exports/media/upload',
        MEDIASEED_UPLOAD_SOURCE_ROOTS=json.dumps([
            {'id':'converted-movies','label':'Konverterede Film','path':'/upload-sources/converted-movies','clientPath':'/downloads/media/converted-movies'},
            {'id':'library','label':'Mediebibliotek (kun læsning)','path':'/media/library','clientPath':'/downloads/media'},
            {'id':'downloads','label':'Downloads (kun læsning)','path':'/media/downloads','clientPath':'/downloads/media/downloads'}]),
        MEDIASEED_UPLOAD_DESTINATION_ROOTS=json.dumps([{'id':key,'label':label,'path':'/exports/media/'+key,'clientPath':'/downloads/media/'+key} for key,label in [('movies','Film'),('tv','TV-serier'),('other','Andet')]]))
    mounts=app.setdefault('volumes',[])
    mounts[:]=[m for m in mounts if not isinstance(m,dict) or m.get('target')!='/run/secrets/mediaseed-uploader.json']
    additions=[(state/'data','/data/uploader',False),(state/'work','/work',False),(media,'/media/library',True),(downloads,'/media/downloads',True),(media/'Konverterede Film','/upload-sources/converted-movies',True)]
    additions += [(media/key,'/exports/media/'+key,False) for key in ['movies','tv','other','upload']]
    for source,target,readonly in additions:
        expected={'type':'bind','source':str(source),'target':target,'read_only':readonly}
        prior=next((m for m in mounts if isinstance(m,dict) and m.get('target')==target),None)
        if prior is not None and prior!=expected:raise ValueError('Existing upload mount differs; preserve it and resolve the storage mapping: '+target)
        if prior is None:mounts.append(expected)
    return value


def prepare(state,media,downloads):
    for path in [state,media,downloads]:
        if not path.is_absolute() or path.is_symlink():raise ValueError('Use approved absolute storage directories')
    if not media.is_dir() or not downloads.is_dir():raise ValueError('Approved media/download storage is unavailable')
    if not state.exists():state.mkdir(mode=0o750,parents=True);os.chown(state,0,1000)
    for path in [state/'data',state/'work']:
        if not path.exists():path.mkdir(mode=0o700,parents=True);os.chown(path,1000,1000)
        if path.resolve()!=path:raise ValueError('Upload storage contains a symbolic link')
    for name in ['movies','tv','other','upload','Konverterede Film']:
        path=media/name
        if not path.exists():path.mkdir(mode=0o750);os.chown(path,1000,1000)
        if path.resolve()!=path:raise ValueError('Media storage contains a symbolic link')


def main():
    p=argparse.ArgumentParser();p.add_argument('--compose',type=Path,required=True);p.add_argument('--state',type=Path,required=True);p.add_argument('--media',type=Path,required=True);p.add_argument('--downloads',type=Path,required=True);p.add_argument('--image');a=p.parse_args()
    prepare(a.state,a.media,a.downloads)
    original=a.compose.read_bytes();current=json.loads(original);updated=configuration(current,a.state,a.media,a.downloads,a.image)
    if updated==current:print('Native uploader already installed; no restart');return
    app_name='mediahub-mediaseed-app-1'
    exists=subprocess.run(['docker','inspect',app_name],capture_output=True).returncode==0
    if exists:
        probe="from pathlib import Path;import sqlite3;p=Path('/data/uploader/mediaupload.sqlite');print(sqlite3.connect(p).execute(\"SELECT COUNT(*) FROM upload_jobs WHERE state IN ('working','queued')\").fetchone()[0] if p.exists() else 0)"
        active=int(subprocess.check_output(['docker','exec',app_name,'python','-c',probe],text=True))
        if active:raise ValueError('Let native upload preparation finish before updating; no services restarted')
    backup=a.compose.with_name('compose.before-native-upload.json')
    if not backup.exists():backup.write_bytes(original);backup.chmod(0o600)
    staged=a.compose.with_name('.compose-native-upload.json');staged.write_text(json.dumps(updated,indent=2)+'\n');staged.chmod(0o600)
    cmd=['docker','compose','-p','mediahub-mediaseed','-f',str(a.compose),'up','-d','--no-deps','--pull','never','app']
    try:
        subprocess.run(['docker','compose','-f',str(staged),'config','--quiet'],check=True,capture_output=True)
        if a.compose.read_bytes()!=original:raise ValueError('Compose changed during installation')
        staged.replace(a.compose);subprocess.run(cmd,check=True,capture_output=True)
    except BaseException:
        a.compose.write_bytes(original);a.compose.chmod(0o600);subprocess.run(cmd,check=True,capture_output=True);raise
    finally:staged.unlink(missing_ok=True)
    print('Native uploader installed; other services and media preserved')


if __name__=='__main__':main()
