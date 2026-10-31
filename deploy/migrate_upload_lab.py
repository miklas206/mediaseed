"""Run on Proxmox: preserve CT109 history/settings/media for native MediaSeed.

Does not delete the old lab or expose private artifacts. Refuses active queues.
The old services stay stopped after success to prevent two preparation workers.
"""
import argparse,json,os,shutil,sqlite3,subprocess,tempfile
from pathlib import Path


def command(args,**kwargs):
    result=subprocess.run(args,capture_output=True,**kwargs)
    if result.returncode:raise RuntimeError('Migration command failed; original lab files preserved')
    return result.stdout


def manifest(root):
    return {str(p.relative_to(root)):[p.stat().st_size,p.stat().st_mtime_ns] for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def main():
    p=argparse.ArgumentParser();p.add_argument('--state',type=Path,required=True);a=p.parse_args();state=a.state
    if not state.is_absolute() or state.is_symlink() or not str(state).startswith('/mnt/mediahub-storage/'):
        raise ValueError('Choose a private directory on the approved media storage')
    if (state/'migration.json').is_file():print('Upload history already migrated; existing data preserved');return
    if state.exists() and any(state.iterdir()):raise ValueError('Destination already has data; refusing to overwrite it')
    check="import sqlite3,json;d=sqlite3.connect('/var/lib/mediaupload/mediaupload.sqlite');print(json.dumps({'active':d.execute(\"SELECT COUNT(*) FROM upload_jobs WHERE state IN ('queued','working')\").fetchone()[0],'jobs':d.execute('SELECT COUNT(*) FROM upload_jobs').fetchone()[0]}))"
    initial=json.loads(command(['pct','exec','109','--','python3','-c',check],text=True))
    if initial['active']:raise ValueError('Let queued/running uploads finish before migration')
    if 'hostname: mediaupload-lab' not in command(['pct','config','109'],text=True):raise ValueError('Wrong source container')
    source=Path('/mnt/file-storage/mediaupload-lab/work');before=manifest(source)
    if any(p.is_symlink() for p in source.rglob('*')):raise ValueError('Legacy work has links; review storage before migration')
    staging=state.with_name(state.name+'.migrating')
    if staging.exists():raise ValueError('An earlier migration is staged; inspect it before retrying')
    staging.mkdir(mode=0o700);(staging/'data').mkdir(mode=0o700)
    units=['mediaupload.service','mediaupload-metadata.service']
    command(['pct','exec','109','--','systemctl','stop',*units])
    try:
        # tar contains private state only and never enters a repository or log.
        for remote,local in [('/var/lib/mediaupload',staging/'data'),('/var/lib/mediaupload-metadata',staging/'data/metadata')]:
            local.mkdir(exist_ok=True,mode=0o700)
            with tempfile.NamedTemporaryFile(dir=staging) as package:
                package.write(command(['pct','exec','109','--','tar','-cf','-','-C',remote,'.']));package.flush()
                command(['tar','--no-same-owner','-xf',package.name,'-C',str(local)])
        shutil.copytree(source,staging/'work',copy_function=shutil.copy2)
        if before!=manifest(source) or before!=manifest(staging/'work'):raise ValueError('Legacy work changed during migration')
        with sqlite3.connect(staging/'data/mediaupload.sqlite') as db:
            jobs=db.execute('SELECT COUNT(*) FROM upload_jobs').fetchone()[0]
            if jobs!=initial['jobs'] or db.execute("SELECT COUNT(*) FROM upload_jobs WHERE state IN ('working','queued')").fetchone()[0]:raise ValueError('Queue changed during migration')
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Upload database failed integrity check')
        for path in [staging,*staging.rglob('*')]:
            os.chown(path,1000,1000)
            if path.is_dir():path.chmod(0o700)
            elif path.is_file():path.chmod(0o600)
        report={'sourceCt':109,'jobsPreserved':jobs,'preparedFilesPreserved':len(before),'preparedBytesPreserved':sum(v[0] for v in before.values()),'originalsPreserved':True,'metadataCredentialsPreserved':True}
        (staging/'migration.json').write_text(json.dumps(report,indent=2)+'\n');os.chown(staging/'migration.json',1000,1000);(staging/'migration.json').chmod(0o640)
        os.chown(staging/'migration.json',100000,1000)
        os.chown(staging,100000,1000);staging.chmod(0o750)
        if state.exists():state.rmdir()
        staging.rename(state)
        print(json.dumps(report))
    except BaseException:
        command(['pct','exec','109','--','systemctl','start',*units])
        raise


if __name__=='__main__':main()
