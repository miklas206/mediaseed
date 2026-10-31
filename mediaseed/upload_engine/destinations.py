"""Explicit writable export roots shared with MediaSeed. Sources remain read-only."""
import json
import os
import stat
from pathlib import Path

from fastapi import HTTPException


class Destinations:
    def __init__(self,configuration='[]'):
        self.locations=json.loads(configuration)
        if not isinstance(self.locations,list) or len(self.locations)>32:raise ValueError('Invalid destination roots')
        seen=set()
        for row in self.locations:
            identifier=row.get('id');path=Path(row.get('path',''))
            if not isinstance(identifier,str) or not identifier.isascii() or not identifier.replace('-','').isalnum() or identifier in seen or not path.is_absolute() or '..' in path.parts:
                raise ValueError('Invalid destination root')
            if row.get('clientPath')!='/downloads/media/'+identifier:raise ValueError('Invalid MediaSeed path mapping')
            seen.add(identifier)

    def directory(self,location,relative=''):
        row=next((row for row in self.locations if row['id']==location),None)
        if not row or not isinstance(relative,str) or len(relative)>1000 or '\\' in relative:
            raise HTTPException(422,'Vælg en godkendt destinationsmappe på mediedrevet')
        parts=relative.split('/') if relative else []
        if any(not part or part.startswith('.') or any(ord(c)<32 for c in part) for part in parts):raise HTTPException(422,'Vælg en almindelig undermappe')
        path=Path(row['path']).joinpath(*parts)
        try:
            if not path.is_dir() or path.resolve(strict=True)!=path or not os.access(path,os.W_OK|os.X_OK):raise OSError()
        except OSError:
            raise HTTPException(409,'Destinationsmappen er utilgængelig eller skrivebeskyttet. Opdatér MediaUpload-mount og adgang til mediedrevet') from None
        return row,path

    def overview(self):
        rows=[]
        for row in self.locations:
            try:self.directory(row['id']);available=True;reason=''
            except HTTPException as exc:available=False;reason=exc.detail
            rows.append({'id':row['id'],'label':row['label'],'clientPath':row['clientPath'],'available':available,'reason':reason})
        return {'locations':rows,'configured':bool(rows)}

    def folders(self,location,relative=''):
        row,path=self.directory(location,relative)
        folders=[]
        with os.scandir(path) as entries:
            for index,entry in enumerate(entries):
                if index>=2000:raise HTTPException(422,'For mange undermapper. Vælg en mere præcis mappe')
                if not entry.name.startswith('.') and entry.is_dir(follow_symlinks=False):folders.append(entry.name)
        return {'folders':sorted(folders,key=str.casefold),'folder':relative,'clientPath':self.client_path(row,relative)}

    @staticmethod
    def client_path(row,relative=''):
        return row['clientPath']+('/'+relative if relative else '')

    def reserve(self,location,relative,name):
        row,parent=self.directory(location,relative)
        if not name or name.startswith('.') or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):raise ValueError('Ugyldigt releasenavn')
        content=parent/name
        try:content.mkdir(mode=0o750)
        except FileExistsError:raise ValueError('Releasemappen findes allerede i destinationen. Vælg en anden undermappe; eksisterende filer overskrives ikke') from None
        return content,{'location':location,'folder':relative,'label':row['label'],'clientSavePath':self.client_path(row,relative),'clientContentPath':self.client_path(row,relative)+'/'+name}

    def series_directory(self, location, relative, name, tmdb, create=True):
        """Reuse only an app-owned directory for the same verified series ID."""
        row, parent = self.directory(location, relative)
        if not name or name.startswith('.') or any(c in name for c in '/\\') or any(ord(c) < 32 for c in name):
            raise ValueError('Ugyldigt seriemappenavn')
        content = parent / name
        marker = {'owner': 'MediaUpload-series-v1', 'tmdb': tmdb}
        marker_name = '.mediaupload-series.json'
        if not os.path.lexists(content) and create:
            try:
                content.mkdir(mode=0o750)
            except FileExistsError:
                pass
            else:
                with (content / marker_name).open('x', encoding='utf-8') as stream:
                    json.dump(marker, stream)
        if os.path.lexists(content):
            try:
                if content.resolve(strict=True) != content or not content.is_dir():
                    raise ValueError()
                descriptor = os.open(content / marker_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
                        raise ValueError()
                    if json.loads(stream.read(1025)) != marker:
                        raise ValueError()
            except (OSError, ValueError):
                raise ValueError('Seriemappen findes allerede uden en matchende MediaUpload-identifikation. Vælg et andet mappenavn; eksisterende indhold ændres ikke') from None
        path = self.client_path(row, relative) + '/' + name
        return content, {'location': location, 'folder': relative, 'label': row['label'],
                         'layout': 'series', 'folderName': name,
                         'clientSavePath': path, 'clientContentPath': path}
