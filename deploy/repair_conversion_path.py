"""Run inside the app as its normal user. Preview unless --apply is supplied."""
import argparse,json
from pathlib import Path
from mediaseed.app import create_app
from mediaseed.conversion import atomic
from mediaseed.conversion_naming import library_label
from mediaseed.conversion_layout import shorten_completed

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    app=create_app();manager=app.state.conversions
    job=next(j for j in manager.records() if j['id']==args.job)
    old=Path(job['destination']);selected=manager.storage.describe(str(old.parent))
    if selected is None:raise ValueError('Destination is outside approved storage')
    manager.storage.directory(selected['location'],selected['folder'],writable=True)
    target=old.with_name(library_label(job['name'])+' ['+job['id'][:8]+']')
    if args.apply:
        backup=manager.queue/(job['id']+'.before-path-repair.json')
        if not backup.exists():atomic(backup,job)
        job=shorten_completed(job,manager.save)
    print(json.dumps({'applied':args.apply,'old':str(old),'destination':str(target),'status':job['status']}))

if __name__=='__main__':main()
