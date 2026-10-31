"""Provision the existing converted-movies bind for the MediaVPN torrent client."""
import argparse,copy,hashlib,json,subprocess
from pathlib import Path

TARGET='/downloads/media/converted-movies'


def configuration(seed,vpn):
    seed,vpn=copy.deepcopy(seed),copy.deepcopy(vpn)
    mounts=seed['services']['app'].get('volumes',[])
    matches=[m for m in mounts if isinstance(m,dict) and m.get('target')==TARGET]
    if not matches:return seed,vpn
    if len(matches)!=1 or matches[0].get('type')!='bind' or matches[0].get('read_only'):raise ValueError('Converted movies needs its existing approved writable bind')
    mount=matches[0];source=Path(mount['source'])
    if not source.is_absolute() or '..' in source.parts:raise ValueError('Invalid converted movie source')
    qbit=seed['services']['qbittorrent'].setdefault('volumes',[])
    existing=[m for m in qbit if isinstance(m,dict) and m.get('target')==TARGET]
    if existing:
        if len(existing)!=1 or existing[0].get('source')!=str(source) or existing[0].get('type')!='bind' or existing[0].get('read_only'):raise ValueError('Existing torrent mount conflicts with approved converted storage')
    else:qbit.append({'type':'bind','source':str(source),'target':TARGET})
    environment=vpn['services']['app'].setdefault('environment',{})
    rows=json.loads(environment.get('MEDIAVPN_MEDIASEED_STORAGE','[]'))
    if not isinstance(rows,list):raise ValueError('Invalid existing MediaVPN storage')
    found=[r for r in rows if r.get('id')=='converted-movies' or r.get('path')==TARGET]
    if found:
        if len(found)!=1 or found[0].get('source')!=str(source) or found[0].get('path')!=TARGET or found[0].get('id')!='converted-movies':raise ValueError('Existing MediaVPN storage conflicts with converted movies')
    else:rows.append({'id':'converted-movies','label':'Konverterede Film','path':TARGET,'source':str(source)})
    environment['MEDIAVPN_MEDIASEED_STORAGE']=json.dumps(rows,ensure_ascii=False)
    return seed,vpn


def call(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=120)
    if p.returncode:raise RuntimeError('Seeding storage provisioning failed: '+str(args[:2])+' '+p.stderr[-500:])
    return p.stdout.strip()


def install_for_app(app_name='mediahub-mediaseed-app-1',vpn_name='mediahub-mediavpn-app-1'):
    app=json.loads(call(['docker','inspect',app_name]))[0]
    env=dict(v.split('=',1) for v in app['Config']['Env'] if '=' in v)
    if not env.get('MEDIASEED_VPN_URL'):return {'changed':False,'reason':'Standalone client needs no MediaVPN storage grant'}
    vpn=json.loads(call(['docker','inspect',vpn_name]))[0]
    configs=[]
    for container in [app,vpn]:
        labels=container['Config']['Labels']
        if labels.get('com.docker.compose.service')!='app':raise ValueError('Expected owned app services')
        path=Path(labels['com.docker.compose.project.config_files'])
        if not path.is_absolute() or ',' in str(path):raise ValueError('Expected one absolute Compose file')
        project=labels['com.docker.compose.project'];command=['docker','compose','-p',project,'-f',str(path)]
        configs.append((path,path.read_bytes(),json.loads(call(command+['config','--format','json'])),command))
    planned=configuration(configs[0][2],configs[1][2])
    if all(p==c[2] for p,c in zip(planned,configs)):return {'changed':False,'reason':'Seeding storage already provisioned'}
    for (path,original,_,_),updated in zip(configs,planned):
        backup=path.with_name('compose.before-seeding-storage-'+hashlib.sha256(original).hexdigest()[:16]+'.json')
        if not backup.exists():backup.write_bytes(original);backup.chmod(0o600)
        temporary=path.with_name('.compose-seeding-storage.json');temporary.write_text(json.dumps(updated,indent=2)+'\n');temporary.chmod(0o600)
        try:call(['docker','compose','-f',str(temporary),'config','--quiet'])
        finally:temporary.unlink(missing_ok=True)
    try:
        for (path,original,_,_),updated in zip(configs,planned):
            if path.read_bytes()!=original:raise ValueError('Compose changed during seeding storage preparation')
            path.write_text(json.dumps(updated,indent=2)+'\n');path.chmod(0o600)
        # Keep the dormant source client stopped. MediaVPN owns the active client.
        call(configs[0][3]+['up','--no-start','--no-deps','--force-recreate','--pull','never','qbittorrent'])
        call(configs[1][3]+['up','-d','--no-deps','--pull','never','app'])
    except BaseException:
        for path,original,_,_ in configs:path.write_bytes(original);path.chmod(0o600)
        call(configs[0][3]+['up','--no-start','--no-deps','--force-recreate','--pull','never','qbittorrent'])
        call(configs[1][3]+['up','-d','--no-deps','--pull','never','app'])
        raise
    return {'changed':True,'approvedPath':TARGET,'vpnProfilePreserved':True,'torrentFilesPreserved':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--app',default='mediahub-mediaseed-app-1');parser.add_argument('--vpn-app',default='mediahub-mediavpn-app-1');args=parser.parse_args()
    print(json.dumps(install_for_app(args.app,args.vpn_app)))
