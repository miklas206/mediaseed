"""Idempotent worker provisioning for a running MediaSeed Compose installation.

Run after installing/updating the MediaSeed app image. Preserves credentials,
volumes, existing device grants and download settings. No Core/Agent changes.
"""
import argparse
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import struct
import time


def stage_access(path, uid):
    """Grant only Docker's root user traversal; preserve other users and ACLs.

    On unprivileged LXC/FUSE, root cannot bypass owner-only media permissions.
    Do not make media world-readable to prepare a nested bind mount.
    """
    path=Path(path)
    if path.resolve()!=path or path.stat().st_uid!=uid:
        raise ValueError('Conversion storage must be owned by the app user without symbolic links')
    if path.stat().st_mode & 1:return
    import errno
    try:
        original=os.getxattr(path,'system.posix_acl_access')
        if len(original)<4 or struct.unpack('<I',original[:4])[0]!=2:
            raise ValueError('Unsupported storage access ACL')
        entries=[list(struct.unpack('<HHI',original[n:n+8])) for n in range(4,len(original),8)]
    except OSError as error:
        if error.errno not in (errno.ENODATA,):raise
        mode=path.stat().st_mode
        entries=[[1,(mode>>6)&7,0xffffffff],[4,(mode>>3)&7,0xffffffff],[32,mode&7,0xffffffff]]
        original=None
    root=next((e for e in entries if e[0]==2 and e[2]==0),None)
    if root:root[1]|=1
    else:entries.append([2,1,0])
    mask=next((e for e in entries if e[0]==16),None)
    if mask:mask[1]|=1
    else:entries.append([16,next(e[1] for e in entries if e[0]==4)|1,0xffffffff])
    entries.sort(key=lambda e:(e[0],e[2]))
    updated=struct.pack('<I',2)+b''.join(struct.pack('<HHI',*e) for e in entries)
    if original!=updated:os.setxattr(path,'system.posix_acl_access',updated)


def call(args, timeout=120):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Conversion provisioning failed: '+str(args[:2])+'; '+result.stderr[-500:])
    return result.stdout.strip()


def configuration(app, uid, gid, queue, image, toolkit=False):
    roots = []
    mounts = [{'type':'bind','source':str(queue),'target':'/conversion'}]
    for mount in app['Mounts']:
        target = mount['Destination']
        if target != '/downloads' and not target.startswith('/downloads/media/'): continue
        roots.append(target)
        mounts.append({'type':'bind','source':mount['Source'],'target':target,'read_only':True})
        mounts.append({'type':'bind','source':str(Path(mount['Source'])/'.incomplete'),
                       'target':target+'/.incomplete'})
    if not roots: raise ValueError('MediaSeed needs an approved download mount before conversion can be installed')
    result = {'image':image, 'user':f'{uid}:{gid}', 'network_mode':'none', 'read_only':True,
              'volumes':mounts, 'tmpfs':['/tmp:rw,nosuid,nodev,size=256m,mode=1777'],
              'environment':{'MEDIASEED_CONVERSION_QUEUE':'/conversion','MEDIASEED_CONVERSION_ROOTS':json.dumps(roots)},
              'cap_drop':['ALL'], 'security_opt':['no-new-privileges:true'], 'init':True,
              'labels':{'org.mediahub.package':'org.mediahub.mediaseed-converter'},
              'mem_limit':'3g','memswap_limit':'3g','cpus':2,'restart':'unless-stopped',
              'logging':{'driver':'json-file','options':{'max-size':'5m','max-file':'2'}}}
    if toolkit:
        result['deploy']={'resources':{'reservations':{'devices':[{'driver':'nvidia','count':1,'capabilities':['gpu']}]}}}
        result['environment'].update(NVIDIA_VISIBLE_DEVICES='all',NVIDIA_DRIVER_CAPABILITIES='compute,video,utility')
    else:
        drivers='/usr/local/lib/mediahub-nvidia'
        if not Path(drivers,'libnvidia-encode.so.1').exists() or not all(Path('/dev', n).exists() for n in ('nvidia0','nvidiactl','nvidia-uvm')):
            raise ValueError('Grant an NVIDIA GPU and matching driver libraries to this app host, or install NVIDIA Container Toolkit. Conversion has not been enabled.')
        result['devices']=[f'/dev/{n}:/dev/{n}' for n in ('nvidia0','nvidiactl','nvidia-uvm')]
        result['volumes'].append({'type':'bind','source':drivers,'target':drivers,'read_only':True})
        result['environment']['LD_LIBRARY_PATH']=drivers
    return result, roots


def prepare_worker_upgrade(queue, restart_active, stop):
    requests=list(Path(queue).glob('*.request.json'))
    active=[]
    for request in requests:
        status=request.with_name(request.name.removesuffix('.request.json')+'.status.json')
        state=json.loads(status.read_text()).get('state') if status.exists() else 'queued'
        if state not in {'ready','error','cancelled'}:active.append(request)
    if not active:return False
    if not restart_active:
        raise ValueError('Wait for queued/running conversions to finish or cancel them before updating the worker')
    # Explicit opt-in: stop just the encoder worker, retain all queue intent and
    # verified outputs. The new worker restarts only the incomplete video.
    stop()
    import fcntl
    for request in active:
        lock=request.with_name(request.name.removesuffix('.request.json')+'.lock')
        with lock.open('a') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('The old encoder still owns a job; worker upgrade was stopped') from None
    return True


def wait_for_idle(queue, sleep=time.sleep):
    """Wait without stopping the encoder or altering queued requests."""
    while True:
        try:
            prepare_worker_upgrade(queue,False,lambda:None)
            return
        except ValueError as error:
            if not str(error).startswith('Wait for queued/running'):raise
            sleep(15)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--app',default='mediahub-mediaseed-app-1')
    parser.add_argument('--image',required=True)
    parser.add_argument('--compose')
    parser.add_argument('--toolkit',action='store_true')
    parser.add_argument('--wait-for-idle',action='store_true',help='Automatically wait for existing jobs; preserve active encode progress')
    parser.add_argument('--restart-active',action='store_true',help='Explicitly restart incomplete encodes with the new worker; originals and queued intent are preserved')
    args=parser.parse_args()
    app=json.loads(call(['docker','inspect',args.app]))[0]
    if args.wait_for_idle and args.restart_active:raise ValueError('Choose either waiting or restarting')
    existing=next((m for m in app['Mounts'] if m['Destination']=='/conversion'),None)
    if args.wait_for_idle and existing:wait_for_idle(Path(existing['Source']))
    labels=app['Config']['Labels']
    if labels.get('com.docker.compose.service')!='app': raise ValueError('Choose the MediaSeed app service')
    project=labels['com.docker.compose.project']
    compose=Path(args.compose or labels['com.docker.compose.project.config_files'])
    if not compose.is_absolute() or ',' in str(compose): raise ValueError('Choose one absolute Compose file')
    original=compose.read_bytes()
    config=json.loads(call(['docker','compose','-p',project,'-f',str(compose),'config','--format','json']))
    baseline=copy.deepcopy(config)
    uid=int(call(['docker','exec',args.app,'id','-u']));gid=int(call(['docker','exec',args.app,'id','-g']))
    existing_queue=next((m for m in app['Mounts'] if m['Destination']=='/conversion'),None)
    queue=Path(existing_queue['Source']) if existing_queue else compose.parent/'conversion-state'
    worker,roots=configuration(app,uid,gid,queue,args.image,args.toolkit)
    old_worker=config['services'].get('conversion-worker')
    if old_worker:
        if old_worker.get('labels',{}).get('org.mediahub.package')!='org.mediahub.mediaseed-converter':
            raise ValueError('Existing conversion-worker service is not owned by MediaSeed; configuration was preserved')
        for key in ('mem_limit','memswap_limit','cpus','restart','logging'):
            if key in old_worker:worker[key]=old_worker[key]
    if queue.is_symlink(): raise ValueError('Conversion state cannot be a symbolic link')
    queue.mkdir(exist_ok=True,mode=0o700)
    os.chown(queue,uid,gid)
    # Fresh install and upgrade create each staging root automatically. Only a
    # newly created directory changes ownership; existing user data is untouched.
    setup="from pathlib import Path\nimport os,json,struct\n"+inspect.getsource(stage_access)+"""
for name in json.loads(os.environ['MEDIASEED_PREPARE_ROOTS']):
 stage_access(Path(name),os.getuid())
 p=Path(name)/'.incomplete'
 if not p.exists():p.mkdir(mode=0o700)
 if p.is_symlink() or not p.is_dir():raise ValueError('Invalid staging directory')
 stage_access(p,os.getuid())
"""
    call(['docker','exec','-e','MEDIASEED_PREPARE_ROOTS='+json.dumps(roots),args.app,'python','-c',setup])
    config['services']['conversion-worker']=worker
    service=config['services']['app']
    service.setdefault('environment',{})['MEDIASEED_CONVERSION_QUEUE']='/conversion'
    service.setdefault('volumes',[])
    if not any(isinstance(m,dict) and m.get('target')=='/conversion' for m in service['volumes']):
        service['volumes'].append({'type':'bind','source':str(queue),'target':'/conversion'})
    updated=(json.dumps(config,indent=2)+'\n').encode()
    backup=compose.with_name('compose.before-conversion-'+hashlib.sha256(original).hexdigest()[:16]+'.json')
    if not backup.exists():backup.write_bytes(original);backup.chmod(0o600)
    temporary=compose.with_name('.compose-conversion-next.json')
    temporary.write_bytes(updated);temporary.chmod(0o600)
    try:
        call(['docker','compose','-p',project,'-f',str(temporary),'config','--quiet'])
        normalized=json.loads(call(['docker','compose','-p',project,'-f',str(temporary),'config','--format','json']))
        if normalized==baseline:
            from install_seeding_storage import install_for_app
            install_for_app(args.app)
            print('Conversion worker configuration already current');return
        prepare_worker_upgrade(queue,args.restart_active,lambda:call(['docker','compose','-p',project,'-f',str(compose),'stop','--timeout','45','conversion-worker']))
        if compose.read_bytes()!=original: raise ValueError('Compose changed during preparation')
        temporary.replace(compose)
        call(['docker','compose','-p',project,'-f',str(compose),'up','-d','--no-deps','--pull','never','app','conversion-worker'])
    except BaseException:
        compose.write_bytes(original);compose.chmod(0o600)
        call(['docker','compose','-p',project,'-f',str(compose),'up','-d','--no-deps','--pull','never',*(['app','conversion-worker'] if old_worker else ['app'])])
        raise
    finally: temporary.unlink(missing_ok=True)
    from install_seeding_storage import install_for_app
    install_for_app(args.app)
    print('Conversion worker installed; existing downloads, credentials and seeding policies preserved')


if __name__=='__main__': main()
