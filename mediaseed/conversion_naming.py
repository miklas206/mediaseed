"""Names for independent converted copies; never rename torrent inputs."""
import re
from pathlib import PurePosixPath

TECHNICAL = re.compile(r'(?i)(?<![a-z0-9])(?:blu[ ._-]?ray|uhd|remux|hybrid|hevc|[hx][ ._-]?26[45]|avc|dv|dovi|dolby[ ._-]?vision|hdr10p|hdr10\+?|hdr|(?:8|10)[ ._-]?bit|\d+(?:mbps|mbit))(?![a-z0-9])')
ANCHOR = re.compile(r'(?i)(?<![a-z0-9])(?:19\d{2}|20\d{2}|s\d{1,2}(?:e\d{1,3})?)(?![a-z0-9])')


def converted_stem(stem, bitrate, tag):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', tag):
        raise ValueError('Invalid conversion tag')
    stem = re.sub(r'(?i)\.(?:mkv|mp4|m4v|avi|mov|ts|m2ts|webm)$', '', stem)
    anchor = ANCHOR.search(stem)
    split = anchor.end() if anchor else 0
    title, tail = stem[:split], stem[split:]
    if TECHNICAL.search(tail):
        tail = re.sub(r'-(?!HD$|MA$|HRA$|Atmos$|AAC$|AC3$|DTS$)[A-Za-z0-9][A-Za-z0-9_]{1,39}$', '', tail, flags=re.I)
    tail = TECHNICAL.sub('.', tail)
    tail = re.sub(r'(?<![a-z0-9])-(?![a-z0-9])', '.', tail, flags=re.I)
    base = re.sub(r'[ ._]+', '.', title+tail).strip(' ._-') or 'Video'
    # Retain title, year, episode, resolution and copied audio labels. HDR/DV
    # input labels are omitted rather than claiming unchanged dynamic metadata.
    suffix = f'.H265.{bitrate}Mbps-{tag}'
    budget = 240-len((suffix+'.mkv').encode('utf-8'))
    base = base.encode('utf-8')[:budget].decode('utf-8', errors='ignore').rstrip(' ._-')
    return base+suffix


def converted_path(name, bitrate, tag):
    path = PurePosixPath(name)
    parents = [converted_stem(p, bitrate, tag) if TECHNICAL.search(p) else p for p in path.parts[:-1]]
    stem=converted_stem(path.stem,bitrate,tag)
    # A movie release directory often repeats the entire file title. The job
    # already provides a unique library folder; omit that redundant component.
    parents=[p for p in parents if p.casefold()!=stem.casefold()]
    return str(PurePosixPath(*parents,stem+'.mkv'))


def output_name(entry):
    """Missing output mapping is an old job and retains its original layout."""
    return entry.get('output', str(PurePosixPath(entry['name']).with_suffix('.mkv')))


def library_label(name):
    """Short title/year folder; technical details belong in the file name."""
    name=re.sub(r'[^\w .()\[\]-]','_',str(name))
    anchor=ANCHOR.search(name)
    if anchor:name=name[:anchor.end()]
    label=re.sub(r'[ ._]+','.',name).strip(' ._-') or 'Video'
    return label.encode('utf-8')[:64].decode('utf-8',errors='ignore').rstrip(' ._-')


def library_folder(files, name, identifier):
    """One movie uses its actual output stem; batches retain a unique title folder."""
    if len(files)==1:return PurePosixPath(output_name(files[0])).stem
    return library_label(name)+' ['+identifier[:8]+']'
