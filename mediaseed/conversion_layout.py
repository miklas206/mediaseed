"""Reversible relocation of a verified completed copy; inputs are never renamed."""
import json
from pathlib import Path
from mediaseed.conversion_naming import library_label


def shorten_completed(job, save, release_name=None):
    from mediaseed.conversion import fingerprint
    from mediaseed.moves import file_path
    if job['status']!='complete':raise ValueError('Only completed conversions can be relocated')
    old=Path(job['destination'])
    if not old.is_absolute() or old.parent.resolve(strict=True)!=old.parent:
        raise ValueError('Invalid library directory')
    name=release_name or library_label(job['name'])+' ['+job['id'][:8]+']'
    if Path(name).name!=name or name in {'.','..'}:raise ValueError('Invalid release directory')
    target=old.with_name(name)
    if old==target:return job
    if not old.name.endswith(' ['+job['id'][:8]+']'):raise ValueError('Unexpected conversion directory')
    if old.exists() and target.exists():raise ValueError('Destination already exists')
    # If interrupted after rename but before ledger save, recover from the
    # verified receipt at the deterministic new location.
    existing=old if old.exists() else target
    if not existing.is_dir() or existing.resolve(strict=True)!=existing:raise ValueError('Converted copy unavailable')
    receipt=json.loads((existing/'.mediaseed-converted.json').read_text())
    if receipt.get('id')!=job['id'] or receipt.get('verified') is not True:raise ValueError('Missing verified receipt')
    from mediaseed.conversion_naming import output_name
    if receipt.get('files')!=[output_name(f) for f in job['files']]:raise ValueError('Wrong output manifest')
    for kind in ('identities','companionIdentities'):
        for name,identity in receipt.get(kind,{}).items():
            if fingerprint(file_path(existing,name))!=identity:raise ValueError('Converted file changed')
    if set(receipt.get('identities',{}))!=set(receipt['files']):raise ValueError('Missing output identities')
    updated={**job,'destination':str(target),'previousDestinations':[*job.get('previousDestinations',[]),str(old)]}
    moved=existing==old
    if moved:old.rename(target)
    try:save(updated)
    except BaseException:
        if moved:target.rename(old)
        raise
    return updated


def flatten_pending_library(manager):
    """Change unpublished destinations only; the worker writes to unchanged staging."""
    from fastapi import HTTPException
    try:_,root=manager.storage.directory('converted-movies','',writable=True)
    except HTTPException:return 0
    changed=0
    with manager.database() as db:
        rows=db.execute("SELECT id,status,document FROM media_conversions WHERE status NOT IN ('complete','cancelled')").fetchall()
        for identifier,state,raw in rows:
            job=json.loads(raw);old=Path(job.get('destination',''))
            if (old.parent not in {root,root/'Konverteret'} or not old.name.endswith(' ['+identifier[:8]+']')
                or old.exists() or old.is_symlink()):continue
            work=Path(job.get('work',''))
            if work.parent!=root/'.incomplete' or work.name!='mediaseed-conversion-'+identifier:continue
            from mediaseed.conversion_naming import library_folder
            target=root/library_folder(job['files'],job['name'],identifier)
            if target==old or target.exists() or target.is_symlink():continue
            job.update(destination=str(target),previousDestinations=[*job.get('previousDestinations',[]),str(old)])
            changed+=db.execute('UPDATE media_conversions SET document=? WHERE id=? AND status=? AND document=?',(json.dumps(job),identifier,state,raw)).rowcount
    if changed:
        try:(root/'Konverteret').rmdir() # Only an empty, redundant wrapper can be removed.
        except OSError:pass
    return changed
