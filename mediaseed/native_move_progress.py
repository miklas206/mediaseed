"""Bounded read-only filesystem observations of the client's native move queue."""
import math
import os
from pathlib import PurePosixPath
import stat
import threading
import time


class NativeMoveProgress:
    def __init__(self, storage, clock=time.time, interval=10):
        self.storage,self.clock,self.interval=storage,clock,interval
        self.lock=threading.Lock();self.sampled=0;self.cache={};self.previous={}

    def targets(self, row, budget):
        selected=self.storage.describe(row.get('save_path'))
        if selected is None:raise ValueError('unapproved')
        _,parent=self.storage.directory(selected['location'],selected['folder'])
        content=PurePosixPath(row.get('content_path',''))
        name=content.name
        if not name or name in {'.','..','.incomplete'} or '\\' in name:raise ValueError('unsafe')
        save=PurePosixPath(row['save_path'])
        if content.parent not in {save,save/'.incomplete'}:raise ValueError('unsafe')
        root=parent/name
        if root.is_symlink() or root.resolve(strict=False)!=root:raise ValueError('symlink')
        total=row.get('size')
        if type(total) not in (int,float) or not math.isfinite(total) or not 0<total<=2**63-1:raise ValueError('size')
        pending=[(root,name)];files={};scanned=0
        while pending:
            path,relative=pending.pop()
            try:info=path.lstat()
            except FileNotFoundError:continue
            scanned+=1
            budget[0]-=1
            if budget[0]<0:raise ValueError('limit')
            if scanned>2000:raise ValueError('limit')
            if stat.S_ISLNK(info.st_mode):raise ValueError('symlink')
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(path) as entries:
                    for entry in entries:
                        pending.append((path/entry.name,relative+'/'+entry.name))
                        if len(pending)+scanned>2000:raise ValueError('limit')
            elif stat.S_ISREG(info.st_mode):
                files[relative]=(info.st_size,min(info.st_size,info.st_blocks*512),info.st_mtime_ns)
        return int(total),files

    def snapshot(self, rows):
        moving=[r for r in rows if r.get('state')=='moving'][:50]
        with self.lock:
            now=self.clock();keys={r.get('hash') for r in moving}
            if now-self.sampled<self.interval and keys==set(self.cache):return self.cache.copy()
            result={};previous={};candidates=[];budget=[4000]
            for row in moving:
                key=row.get('hash')
                if not isinstance(key,str):continue
                try:
                    total,files=self.targets(row,budget);written=min(total,sum(v[1] for v in files.values()))
                    old=self.previous.get(key);changed=[]
                    if old:
                        changed=[name for name,value in files.items() if old['files'].get(name)!=value]
                    last_change=now if changed else old['lastChange'] if old else 0
                    current=changed[-1] if changed else old['currentFile'] if old else None
                    speed=max(0,written-old['written'])/(now-old['at']) if old and now>old['at'] else None
                    # A recent destination write can identify the first sample;
                    # subsequent samples compare actual allocation/mtime changes.
                    if not old and files:
                        latest=max(files,key=lambda n:files[n][2])
                        if 0<=now-files[latest][2]/1e9<30:last_change=files[latest][2]/1e9;current=latest
                    previous[key]={'files':files,'written':written,'at':now,'lastChange':last_change,'currentFile':current}
                    result[key]={'phase':'observing' if files else 'queued','estimated':True,'writtenBytes':written,'totalBytes':total,'fraction':min(.999,written/total),'speedBps':speed,'currentFile':current.rsplit('/',1)[-1] if current else None,'measuredAt':now}
                    if last_change and now-last_change<30:candidates.append((last_change,key))
                except (OSError,ValueError,TypeError):
                    result[key]={'phase':'unavailable','estimated':True,'measuredAt':now}
            if candidates:
                active=max(candidates)[1]
                for key,value in result.items():
                    if value['phase']!='unavailable':value['phase']='active' if key==active else 'queued'
            self.previous=previous;self.cache=result;self.sampled=now
            return result.copy()
