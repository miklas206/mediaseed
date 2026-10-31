"""Persist source-branch renames on a mergerfs host without remounting storage.

Run as root on the pool host after installing/updating MediaSeed. Ordinary
filesystems need no host helper. Only mergerfs's rename policy is changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

MARKER = '# Managed by MediaSeed pool rename provisioning\n'
KEY = 'user.mergerfs.ignorepponrename'


def call(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Pool rename provisioning failed: '+result.stderr[-400:])
    return result.stdout.strip()


def control(pool):
    pool = Path(pool)
    if not pool.is_absolute() or pool.resolve(strict=True) != pool or not os.path.ismount(pool):
        raise ValueError('Choose the actual mounted mergerfs pool without symbolic links')
    node = pool/'.mergerfs'
    current = os.getxattr(node, KEY)
    if current not in {b'true',b'false'}:
        raise ValueError('Pool does not expose the supported mergerfs rename policy')
    return node, current


def apply_live(pool):
    node, previous = control(pool)
    if previous != b'true':os.setxattr(node,KEY,b'true')
    if os.getxattr(node,KEY) != b'true':raise RuntimeError('Pool rename policy was not applied')
    return {'enabled':True,'changed':previous!=b'true','remounted':False}


def service_text(pool, mount_unit, script):
    return MARKER+f'''[Unit]
Description=Keep MediaSeed completion renames on their original disk
DefaultDependencies=no
Conflicts=umount.target
Requires={mount_unit}
After={mount_unit}
PartOf={mount_unit}
Before=pve-container@107.service umount.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {json.dumps(str(script))} --live --pool {json.dumps(str(pool))}
RemainAfterExit=yes

[Install]
WantedBy={mount_unit}
'''


def install(pool):
    if os.geteuid()!=0:raise PermissionError('Run the pool helper as root on the storage host')
    node, previous=control(pool)
    mount_unit=call(['systemd-escape','--path','--suffix=mount',str(pool)])
    suffix=hashlib.sha256(str(pool).encode()).hexdigest()[:12]
    name=f'mediaseed-pool-rename-{suffix}.service'
    script=Path('/usr/local/lib/mediaseed/pool_rename.py')
    unit=Path('/etc/systemd/system')/name
    desired={script:Path(__file__).read_bytes(),unit:service_text(pool,mount_unit,script).encode()}
    for target in desired:
        if target.is_symlink():raise ValueError('Managed pool helper must not be a symbolic link')
    if unit.exists() and not unit.read_text().startswith(MARKER):
        raise ValueError('An unmanaged service already uses the helper name')
    before={p:p.read_bytes() if p.exists() else None for p in desired}
    changed=any(before[p]!=data for p,data in desired.items())
    backup=None
    if changed or previous!=b'true':
        backup=Path('/var/backups/mediaseed')/('pool-rename-'+str(time.time_ns()))
        backup.mkdir(parents=True,mode=0o700)
        (backup/'before.json').write_text(json.dumps({'pool':str(pool),'previous':previous.decode(),'service':name,'filesExisted':{str(p):data is not None for p,data in before.items()}},indent=2))
        for p,data in before.items():
            if data is not None:(backup/p.name).write_bytes(data)
    try:
        for p,data in desired.items():
            if before[p]!=data:p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);p.chmod(0o644)
        if changed:call(['systemctl','daemon-reload'])
        result=apply_live(pool)
        call(['systemctl','enable','--now',name])
        assert call(['systemctl','is-enabled',name])=='enabled'
        assert os.getxattr(node,KEY)==b'true'
        return {**result,'provisioningChanged':changed,'service':name,'backup':str(backup) if backup else None}
    except BaseException:
        os.setxattr(node,KEY,previous)
        if before[unit] is None:subprocess.run(['systemctl','disable','--now',name],capture_output=True,timeout=30)
        for p,data in before.items():
            if data is None:p.unlink(missing_ok=True)
            else:p.write_bytes(data)
        call(['systemctl','daemon-reload'])
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pool',required=True,type=Path);parser.add_argument('--live',action='store_true');args=parser.parse_args()
    print(json.dumps(apply_live(args.pool) if args.live else install(args.pool)))
