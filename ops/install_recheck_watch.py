"""Install/update one named watch as root on the Docker host; keep its ledger."""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from recheck_watch import atomic, validate


def install(config, destination, units, source, run=subprocess.run):
    validate(config)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = destination / 'config.json'
    if config_path.exists() and json.loads(config_path.read_text()) != config:
        raise ValueError('Existing watch configuration differs; its state was preserved')
    atomic(config_path, config)
    target = destination / 'recheck_watch.py'
    temporary = target.with_suffix('.new')
    shutil.copyfile(source, temporary)
    temporary.chmod(0o600)
    temporary.replace(target)
    name = destination.name
    if not re.fullmatch(r'[a-z0-9-]{1,80}', name):
        raise ValueError('Expected a simple watch name')
    # No app/VM/container restart. One timer, an exclusive lock and bounded work.
    service = f'''[Unit]
Description=MediaSeed scoped recheck watch ({name})
After=docker.service network.target
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 {target} --config {config_path}
TimeoutStartSec=65
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={destination}
RestrictAddressFamilies=AF_UNIX
'''
    timer = f'''[Unit]
Description=Check MediaSeed recheck progress every minute ({name})
[Timer]
OnBootSec=30s
OnUnitInactiveSec=60s
AccuracySec=5s
Unit={name}.service
[Install]
WantedBy=timers.target
'''
    units.mkdir(parents=True, exist_ok=True)
    (units / (name + '.service')).write_text(service)
    (units / (name + '.timer')).write_text(timer)
    run(['systemctl', 'daemon-reload'], check=True)
    run(['systemctl', 'enable', '--now', name + '.timer'], check=True)
    run(['systemctl', 'start', name + '.service'], check=True)
    return name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--name', default='mediaseed-julepakke-watch')
    args = parser.parse_args()
    if os.getuid() != 0:
        raise SystemExit('Run as root on the Docker host.')
    if shutil.which('docker') is None or not Path('/var/run/docker.sock').is_socket():
        raise SystemExit('An existing local Docker runtime is required; no apps or tunnels are installed by this tool.')
    if not re.fullmatch(r'[a-z0-9-]{1,80}', args.name):
        raise SystemExit('Invalid watch name.')
    config = json.loads(args.config.read_text())
    os.umask(0o077)
    name = install(config, Path('/opt/mediahub/maintenance') / args.name, Path('/etc/systemd/system'),
                   Path(__file__).with_name('recheck_watch.py'))
    print(json.dumps({'installed': name, 'ledgerPreserved': True}))


if __name__ == '__main__':
    main()
