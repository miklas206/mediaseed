"""One-torrent recheck watchdog. No start, download, move or delete operations.

Run periodically on the Docker host. Secrets stay inside the existing app;
only the existing MediaVPN integration is used. The state file is durable.
"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path

TERMINAL = {'verified_paused', 'verified', 'incomplete', 'exhausted', 'expired', 'manual_change', 'missing'}
DELAYS = (120, 300, 900, 3600, 10800)

# Executed as the app's configured user, with its existing integration token.
WORKER = r'''
import base64,json,os,signal,sys,time,urllib.request
from pathlib import Path

def deadline(*args): raise TimeoutError()
signal.signal(signal.SIGALRM,deadline);signal.alarm(40)
job=json.load(sys.stdin)
h=job['hash']
headers={'X-MediaSeed-Key':Path(os.environ['MEDIASEED_VPN_TOKEN_FILE']).read_text().strip(),'Content-Type':'application/json'}
base=os.environ['MEDIASEED_VPN_URL'].rstrip('/')+'/api/integration/'

def call(path,body=None):
    request=urllib.request.Request(base+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request,timeout=12) as response:return json.load(response)

def rpc(path,method='GET',payload=None):
    # No caller-supplied RPC operations or native start/download controls.
    allowed={'torrents/info','torrents/properties','torrents/files','torrents/stop','torrents/recheck'}
    if path not in allowed:raise ValueError()
    result=call('rpc',{'path':path,'method':method,'payload':payload or {}})
    if result['status'] not in (200,204):raise RuntimeError()
    return json.loads(base64.b64decode(result['body'])) if method=='GET' else True

def current():
    rows=rpc('torrents/info',payload={'hashes':h,'limit':1})
    if not isinstance(rows,list):raise ValueError()
    if not rows:return None
    row=rows[0]
    if row.get('hash')!=h or row.get('save_path')!=job['save_path'] or row.get('size')!=job['size']:
        return {'manualChange':True}
    return row

def probe(row):
    # Reuse MediaSeed's approved locations and symlink checks; never invent a mount.
    from mediaseed.storage import Storage
    catalog=Storage(Path(os.environ.get('MEDIASEED_DOWNLOADS','/downloads')))
    location=catalog.describe(job['save_path'])
    if location is None:raise ValueError()
    catalog.directory(location['location'],location['folder'])
    path=Path(row['content_path'])/job['probe_file']
    if not path.is_relative_to(Path(job['save_path'])) or path.resolve(strict=True)!=path:raise ValueError()
    with path.open('rb') as file:
        if len(file.read(4096))!=4096:raise OSError()

try:
    row=current()
    if row is None:result={'missing':True}
    elif row.get('manualChange'):result=row
    elif job['action']=='observe':
        fields=('name','state','progress','completed','downloaded','amount_left','size')
        result={'row':{key:row.get(key) for key in fields}}
    elif job['action']=='verify':
        if row['state'].startswith('checking') or row['state']=='error':result={'skipped':'state_changed'}
        else:
            properties=rpc('torrents/properties',payload={'hash':h})
            files=rpc('torrents/files',payload={'hash':h})
            complete=(row.get('progress')==1 and row.get('amount_left')==0
                      and properties.get('pieces_num',0)>0 and properties.get('pieces_have')==properties.get('pieces_num')
                      and len(files)==job['file_count'] and sum(f['size'] for f in files)==job['size']
                      and all(f.get('progress')==1 for f in files))
            result={'verified':complete,'fileCount':len(files),'piecesHave':properties.get('pieces_have'),
                    'piecesTotal':properties.get('pieces_num'),'state':row['state']}
    elif job['action']=='recover':
        if row['state'] not in ('error','missingFiles'):
            result={'skipped':'state_changed'}
        else:
            status=call('status')
            if not status.get('ready') or 'recheck' not in status.get('torrentCapabilities',[]):
                result={'skipped':'vpn_unavailable'}
            else:
                probe(row)
                rpc('torrents/stop','POST',{'hashes':h})
                stopped=False
                for attempt in range(12):
                    row=current()
                    if row is None or row.get('manualChange'):break
                    if row['state'].startswith(('paused','stopped')) or row['state'] in ('error','missingFiles'):
                        stopped=True;break
                    time.sleep(.25)
                if not stopped:result={'skipped':'stop_not_confirmed'}
                else:
                    rpc('torrents/recheck','POST',{'hashes':h})
                    result={'recheckAccepted':True,'paused':True}
    else:raise ValueError()
except Exception as error:
    # Exception messages may contain authenticated URLs. Never print them.
    result={'errorType':type(error).__name__}
    if isinstance(error,OSError):result['errno']=error.errno
print(json.dumps(result))
'''


def validate(config):
    if not re.fullmatch(r'[a-f0-9]{40}', config['hash']):
        raise ValueError('Expected one exact v1 torrent hash')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}', config['container']):
        raise ValueError('Expected an existing app container name')
    path = Path(config['save_path'])
    probe = Path(config['probe_file'])
    if not str(path).startswith('/downloads/') or '..' in path.parts or '\\' in str(path):
        raise ValueError('Expected an approved download path')
    if probe.is_absolute() or '..' in probe.parts or '\\' in str(probe):
        raise ValueError('Expected a relative probe filename')
    if type(config['size']) is not int or config['size'] <= 0 or not 1 <= config['file_count'] <= 10000:
        raise ValueError('Expected torrent size and file count')
    if not 1 <= config.get('max_retries', 5) <= 5 or not 1 <= config.get('hours', 72) <= 168:
        raise ValueError('Retry/time limit exceeds watchdog bounds')


def initial(now):
    return {'version': 1, 'created_at': now, 'retries': 0, 'status': 'observing', 'last_progress_at': now}


def atomic(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(path)


def evaluate(state, observation, config, now):
    """Plan only; callers persist the reserved retry before any mutation."""
    if state['status'] in TERMINAL:
        return None
    state['checked_at'] = now
    if now - state['created_at'] >= config.get('hours', 72) * 3600:
        state['status'] = 'expired'
        return None
    if observation.get('missing') or observation.get('manualChange'):
        state['status'] = 'missing' if observation.get('missing') else 'manual_change'
        return None
    if observation.get('errorType'):
        state.update(status='unavailable', last_error=observation)
        return None
    row = observation['row']
    if state.get('client_state') != row['state'] or row.get('completed') != state.get('completed'):
        state['last_progress_at'] = now
    state.update(client_state=row['state'], progress=row.get('progress'), completed=row.get('completed'), downloaded=row.get('downloaded'))
    if row['state'].startswith('checking') or row['state'] == 'moving':
        state['checking_seen'] = state.get('checking_seen', False) or row['state'].startswith('checking')
        state['status'] = 'stalled_check' if now - state['last_progress_at'] > 1800 else 'checking'
        return None  # A slow active check is never cancelled/restarted.
    if row.get('progress') == 1 and row.get('amount_left') == 0 and row['state'] in {'stoppedUP', 'pausedUP', 'stalledUP', 'uploading', 'queuedUP', 'forcedUP'}:
        state['status'] = 'verifying'
        return 'verify'
    if row['state'] not in {'error', 'missingFiles'}:
        if row['state'] in {'stoppedDL', 'pausedDL'} and state.get('checking_seen'):
            state['status'] = 'incomplete'
        else:
            state['status'] = 'manual_change'
        return None
    if state['retries'] >= config.get('max_retries', 5):
        state['status'] = 'exhausted'
        return None
    state['status'] = 'retry_wait'
    if 'retry_at' not in state:
        state['retry_at'] = now + DELAYS[0]
    if now < state['retry_at']:
        return None
    state['retries'] += 1
    state['retry_at'] = now + DELAYS[min(state['retries'], len(DELAYS) - 1)]
    state['status'] = 'retry_reserved'
    return 'recover'


def error_report(config, name):
    directory = config.get('client_log_dir')
    if not directory or not name:
        return []
    try:
        files = sorted(Path(directory).glob('qbittorrent.log*'), key=lambda path: path.stat().st_mtime)[-8:]
        lines = []
        for path in files:
            with path.open('rb') as source:
                source.seek(max(0, path.stat().st_size - 1024 * 1024))
                for line in source.read(1024 * 1024).decode('utf8', errors='replace').splitlines():
                    if 'File error alert.' in line and 'Torrent: "' + name + '"' in line:
                        line = re.sub(r'(?:https?|udp|sftp|smb)://[^\s"<>]+', '[URL]', line)
                        line = re.sub(r'(?i)(password|token|passkey|authorization)[=: ]+[^\s"<>]+', r'\1=[redacted]', line)
                        lines.append(line[:2000])
        return list(dict.fromkeys(lines))[-5:]
    except OSError as error:
        return [{'errorType': type(error).__name__}]


def worker(config, action):
    try:
        result = subprocess.run(
            ['docker', '--config', '/tmp/mediaseed-watch-docker', '-H', 'unix:///var/run/docker.sock', 'exec', '-i', config['container'], 'python3', '-c', WORKER],
            input=json.dumps({**config, 'action': action}), capture_output=True, text=True, timeout=50, check=False,
        )
        if result.returncode:
            return {'errorType': 'ContainerUnavailable'}
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {'errorType': 'InvalidResponse'}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {'errorType': type(error).__name__}


def tick(config, state_path, invoke=worker, clock=time.time):
    validate(config)
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with state_path.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = clock()
        state = json.loads(state_path.read_text()) if state_path.exists() else initial(now)
        # A saved scope prevents accidentally resuming a different job with this ledger.
        scope = {key: config[key] for key in ('hash', 'save_path', 'size', 'file_count')}
        if state.get('scope', scope) != scope or state.get('version') != 1:
            raise ValueError('Existing watchdog belongs to another job or version')
        state['scope'] = scope
        if state['status'] in TERMINAL:
            return state
        observation = invoke(config, 'observe')
        row = observation.get('row', {})
        if row.get('state') in {'error', 'missingFiles'} and state.get('client_state') != row['state']:
            state['last_file_errors'] = error_report(config, row.get('name'))
        action = evaluate(state, observation, config, now)
        atomic(state_path, state)  # Reserve before invoking stop/recheck; restarts keep the cap.
        if action:
            result = invoke(config, action)
            state['last_action'] = {'at': now, 'action': action, 'result': result}
            if action == 'verify' and result.get('verified'):
                state['status'] = 'verified_paused' if result['state'].startswith(('paused', 'stopped')) else 'verified'
            elif action == 'verify':
                state['status'] = 'observing' if result.get('skipped') else 'unavailable' if result.get('errorType') else 'incomplete'
            elif result.get('recheckAccepted'):
                state.update(status='checking', checking_seen=True, last_progress_at=now)
            elif state['retries'] >= config.get('max_retries', 5):
                state['status'] = 'exhausted'
            else:
                state['status'] = 'retry_wait'
            atomic(state_path, state)
        # The append-only bounded journal contains state changes and errors, not secrets.
        event = {'at': now, 'status': state['status'], 'progress': state.get('progress'),
                 'retries': state['retries'], 'client_state': state.get('client_state')}
        if row.get('state') in {'error', 'missingFiles'}:
            event['fileErrors'] = state.get('last_file_errors', [])
        if action:
            event['action'] = state['last_action']
        if observation.get('errorType'):
            event['error'] = observation
        journal = state_path.with_suffix('.jsonl')
        if journal.exists() and journal.stat().st_size > 2 * 1024 * 1024:
            journal.replace(journal.with_suffix('.jsonl.1'))
        with journal.open('a') as output:
            output.write(json.dumps(event) + '\n')
        journal.chmod(0o600)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    os.umask(0o077)
    state = tick(config, args.config.with_name('state.json'))
    print(json.dumps({key: state.get(key) for key in ('status', 'progress', 'retries', 'client_state', 'checked_at')}))


if __name__ == '__main__':
    main()
