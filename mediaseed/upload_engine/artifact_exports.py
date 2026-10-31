"""Publish upload attachments in owned folders, without touching media payloads."""
import json
import os
from pathlib import Path
import re
import stat
import secrets
import threading


def attachment_stem(result):
    videos = result.get('videos', [])
    if len(videos) == 1:
        name = Path(videos[0]['file']).stem
    else:
        name = result.get('releaseName') or result.get('title') or 'upload'
    name = re.sub(r'[\x00-\x1f\x7f/\\:<>"|?*]', '_', name).strip(' .') or 'upload'
    while len(name.encode('utf-8')) > 180:
        name = name[:-1]
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        name = '_' + name
    return name


def read_attachment(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Et uploadbilag er ugyldigt eller for stort')
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError('Et uploadbilag er for stort')
        return content


class ArtifactExports:
    def __init__(self, root='', torrent_filter=None):
        self.root = Path(root) if root else None
        self.torrent_filter = torrent_filter or (lambda content, result: content)
        self.lock = threading.Lock()

    def publish(self, identifier, result, artifacts):
        if self.root is None:
            return None
        if not re.fullmatch(r'[a-f0-9]{24}', identifier):
            raise ValueError('Ugyldigt upload-ID')
        if not self.root.is_absolute() or self.root.resolve(strict=True) != self.root or not self.root.is_dir():
            raise ValueError('Upload-mappen er utilgængelig. Fuldfør MediaUpload-opdateringen.')
        if artifacts.resolve(strict=True) != artifacts:
            raise ValueError('Uploadbilagene er utilgængelige')
        stem = attachment_stem(result)
        files = {}
        torrent = artifacts / 'upload.torrent'
        if torrent.exists() or torrent.is_symlink():
            files[stem + '.torrent'] = self.torrent_filter(read_attachment(torrent, 1024**2), result)
        if result.get('generatedNfo'):
            if result['generatedNfo'] != 'captain-quack.nfo':
                raise ValueError('Ukendt genereret NFO-fil')
            files[stem + '.nfo'] = read_attachment(artifacts / 'captain-quack.nfo', 2 * 1024**2)
        originals = result.get('originalNfoFiles', [])
        if len(originals) > 200:
            raise ValueError('For mange originale NFO-bilag')
        for number in range(1, len(originals) + 1):
            name = f'original-{number:03}.nfo'
            content = read_attachment(artifacts / name, 1024**2)
            while name in files:
                name = 'original-' + name
            files[name] = content
        if not files:
            raise ValueError('Pakken har ingen færdige torrent- eller NFO-filer')
        marker = '.mediaupload-export.json'
        # Traverse only verified directory descriptors when creating or reading exports.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with self.lock:
            root_fd = os.open('/', flags)
            try:
                for part in self.root.parts[1:]:
                    child_fd = os.open(part, flags, dir_fd=root_fd)
                    os.close(root_fd)
                    root_fd = child_fd
                for name in [stem, stem + ' [' + identifier + ']']:
                    created = False
                    try:
                        os.mkdir(name, 0o750, dir_fd=root_fd)
                        created = True
                    except FileExistsError:
                        pass
                    try:
                        folder_fd = os.open(name, flags, dir_fd=root_fd)
                    except OSError:
                        if created: raise
                        continue
                    try:
                        if created:
                            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder_fd)
                            with os.fdopen(fd, 'w') as stream:
                                json.dump({'job': identifier}, stream)
                                stream.flush(); os.fsync(stream.fileno())
                        else:
                            try:
                                fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
                                with os.fdopen(fd, 'rb') as stream:
                                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): continue
                                    owner = json.loads(stream.read(4097))
                                if not isinstance(owner, dict) or owner.get('job') != identifier: continue
                            except (OSError, ValueError):
                                continue
                        for filename, content in files.items():
                            temporary = '.pending-' + secrets.token_hex(12)
                            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder_fd)
                            try:
                                with os.fdopen(fd, 'wb') as stream:
                                    stream.write(content)
                                    stream.flush(); os.fsync(stream.fileno())
                                try:
                                    os.link(temporary, filename, src_dir_fd=folder_fd, dst_dir_fd=folder_fd, follow_symlinks=False)
                                except FileExistsError:
                                    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
                                    with os.fdopen(fd, 'rb') as stream:
                                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(len(content) + 1) != content:
                                            raise ValueError('Et eksporteret bilag er ændret. Eksisterende filer overskrives ikke.')
                            finally:
                                os.unlink(temporary, dir_fd=folder_fd)
                        return {'folder': str(self.root / name), 'displayFolder': 'media/upload/' + name if self.root == Path('/exports/media/upload') else str(self.root / name), 'files': list(files)}
                    finally:
                        os.close(folder_fd)
                raise ValueError('Upload-mappens navn er optaget. Eksisterende filer overskrives ikke.')
            finally:
                os.close(root_fd)
