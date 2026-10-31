"""Retire the test address only after native MediaSeed verification. Run on Proxmox."""
import json,subprocess
from pathlib import Path


def execute(ct,*args,input=None):
    result=subprocess.run(['pct','exec',str(ct),'--',*args],input=input,capture_output=True,text=True)
    if result.returncode:raise RuntimeError('Test-route retirement failed; inspect the preserved backup')
    return result.stdout


def main():
    report=json.loads(execute(107,'cat','/opt/mediahub/local-apps/mediaseed/native-uploader-20261029/live-verification.json'))
    if not report.get('nativeUploader') or not report.get('legacyJobsPreserved') or not report.get('legacyServiceStopped'):
        raise ValueError('Verify native upload and imported history with the lab stopped first')
    script=r'''
from pathlib import Path
import subprocess
p=Path('/etc/nginx/sites-available/mdtech-studio');old=p.read_text()
backup=p.with_name(p.name+'.before-native-upload')
if not backup.exists():backup.write_text(old);backup.chmod(0o600)
new=old.replace(' include /etc/nginx/snippets/mediaupload-lab.conf;','')
line=' location ^~ /upload-lab { return 410; }'
if line not in new:
 if ' location / {' not in new:raise RuntimeError('Unknown Studio nginx layout')
 new=new.replace(' location / {',line+'\n location / {',1)
if new!=old:
 p.write_text(new)
 check=subprocess.run(['nginx','-t'],capture_output=True)
 if check.returncode:p.write_text(old);raise RuntimeError('Nginx validation failed; previous configuration restored')
 subprocess.run(['systemctl','reload','nginx'],check=True)
print('Legacy route retired')
'''
    # Prevent maintenance from recreating the removed nginx include.
    subprocess.run(['systemctl','disable','--now','mediaupload-origin.timer'],check=True,capture_output=True)
    execute(102,'python3','-c',script)
    execute(109,'systemctl','disable','--now','mediaupload.service','mediaupload-metadata.service')
    subprocess.run(['pct','set','109','-onboot','0'],check=True,capture_output=True)
    marker=Path('/usr/local/lib/mediaupload/native-retired.json')
    marker.write_text(json.dumps({'native':'MediaSeed','ct':107,'legacyAddressRemoved':True}));marker.chmod(0o600)
    # Keep a manual future invocation of the old maintainer from restoring the lab.
    gateway=Path('/usr/local/lib/mediaupload/gateway.py');old=gateway.read_text()
    guard="    if pathlib.Path('/usr/local/lib/mediaupload/native-retired.json').is_file():return\n"
    if guard not in old:
        backup=gateway.with_name('gateway.before-native-retirement.py')
        if not backup.exists():backup.write_text(old);backup.chmod(0o700)
        gateway.write_text(old.replace('def main():\n','def main():\n'+guard,1));gateway.chmod(0o700)
    print('upload-lab removed; lab services disabled; preserved CT109 and its original data')


if __name__=='__main__':main()
