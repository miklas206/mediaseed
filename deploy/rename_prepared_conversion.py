"""Explicit repair of an unpublished, unseeded one-movie upload; no video copying."""
import base64,json,os,sqlite3
from pathlib import Path
from mediaseed.conversion import fingerprint,atomic
from mediaseed.conversion_naming import output_name
from mediaseed.upload_engine.upload_helper import decode_torrent,bencode
from mediaseed.upload_engine.artifact_exports import attachment_stem
from mediaseed.upload_nfo_repair import write_bytes


def prepare(job,result,artifacts,identifier=None):
    old=Path(job['destination'])
    if job['status']!='complete' or len(job['files'])!=1:raise ValueError('Requires a completed single movie')
    name=output_name(job['files'][0])
    if Path(name).name!=name:raise ValueError('Requires a flat movie folder')
    target=old.with_name(Path(name).stem)
    if target==old:raise ValueError('Folder already matches')
    if old.resolve()!=old or not old.is_dir() or os.path.lexists(target):raise ValueError('Folder unavailable or destination occupied')
    if not old.name.endswith(' ['+job['id'][:8]+']'):raise ValueError('Unexpected old folder name')
    receipt=old/'.mediaseed-converted.json';record=json.loads(receipt.read_bytes())
    if record.get('id')!=job['id'] or record.get('verified') is not True:raise ValueError('Missing verified conversion')
    video=old/name;identity=fingerprint(video)
    if record.get('identities',{}).get(name)!=identity:raise ValueError('Movie identity changed')
    torrent=artifacts/'upload.torrent';before=torrent.read_bytes();tree=decode_torrent(before);info=tree[b'info']
    if info.get(b'name')!=old.name.encode() or b'files' not in info:raise ValueError('Unexpected torrent root')
    for entry in info[b'files']:
        parts=[p.decode() for p in entry[b'path']]
        if len(parts)!=1 or parts[0] not in {name,result.get('payloadNfoFile')}:raise ValueError('Unexpected payload')
        path=old/parts[0]
        if path.resolve()!=path or path.stat().st_size!=entry[b'length']:raise ValueError('Payload changed')
    info[b'name']=target.name.encode();after=bencode(tree)
    updated=json.loads(json.dumps(result))
    def replace(value):
        if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
        if isinstance(value,list):return [replace(v) for v in value]
        if isinstance(value,str):
            for prefix in [str(old),'/downloads/media/converted-movies/'+old.name]:
                if value==prefix or value.startswith(prefix+'/'):return str(target) + value[len(prefix):] if prefix==str(old) else '/downloads/media/converted-movies/'+target.name+value[len(prefix):]
        return value
    updated=replace(updated);updated['source']['torrentName']=target.name
    if updated['source'].get('folder')==old.name:updated['source']['folder']=target.name
    converted={**job,'destination':str(target),'previousDestinations':[*job.get('previousDestinations',[]),str(old)]}
    record['files']=[name]
    changes={torrent:after,artifacts/'checklist.json':json.dumps({k:v for k,v in updated.items() if k!='videos'},ensure_ascii=False,indent=2).encode()}
    exported=result.get('artifactExport')
    if exported:
        folder=Path(exported['folder']);marker=folder/'.mediaupload-export.json'
        if folder.resolve()!=folder or marker.is_symlink() or json.loads(marker.read_text()).get('job')!=(identifier or result.get('id')):raise ValueError('Export ownership changed')
        exported_torrent=folder/(attachment_stem(result)+'.torrent')
        from mediaseed.upload_engine.upload_helper import nordicbytes_download
        filter_=nordicbytes_download if result.get('profile')=='nordicbytes' else lambda b:b
        if exported_torrent.read_bytes()!=filter_(before):raise ValueError('Exported torrent changed')
        changes[exported_torrent]=filter_(after)
    return {'old':str(old),'new':str(target),'job':job,'updatedJob':converted,'result':result,'updatedResult':updated,'identity':identity,'video':name,'receipt':receipt.read_text(),'updatedReceipt':json.dumps(record),'changes':[{'path':str(p),'before':base64.b64encode(p.read_bytes()).decode(),'after':base64.b64encode(b).decode(),'mode':p.stat().st_mode&0o777} for p,b in changes.items()]}


def apply(plan,save):
    old,new=Path(plan['old']),Path(plan['new'])
    if fingerprint(old/plan['video'])!=plan['identity'] or os.path.lexists(new):raise ValueError('Files changed before rename')
    for f in plan['changes']:
        p=Path(f['path'])
        if p.resolve()!=p or p.read_bytes()!=base64.b64decode(f['before']):raise ValueError('Attachment changed before rename')
    old.rename(new)
    try:
        write_bytes(new/'.mediaseed-converted.json',plan['updatedReceipt'].encode(),0o600)
        for f in plan['changes']:write_bytes(Path(f['path']),base64.b64decode(f['after']),f['mode'])
        save(plan['updatedJob'],plan['updatedResult'])
    except BaseException:
        for f in plan['changes']:write_bytes(Path(f['path']),base64.b64decode(f['before']),f['mode'])
        write_bytes(new/'.mediaseed-converted.json',plan['receipt'].encode(),0o600)
        new.rename(old)
        save(plan['job'],plan['result'])
        raise
