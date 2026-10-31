"""Choose NVDEC from actual decoder capabilities, never encoder capabilities.
Adapted to MediaSeed from qlerup/fjordseed fef95f88fe3e11a1ccbaf4f754b4e09466c79d49.
"""
from functools import lru_cache
from pathlib import Path
import re
import shutil
import subprocess


@lru_cache(maxsize=4)
def capabilities(executable, modified):
    try:
        result=subprocess.run([executable,'--check-features'],capture_output=True,text=True,
                              check=True,timeout=20,encoding='utf-8',errors='replace')
    except (OSError,subprocess.SubprocessError) as error:
        raise ValueError('NVIDIA-afkodning kunne ikke kontrolleres. Kontrollér GPU og decoder-driver.') from error
    section=result.stdout.partition('NVDec features')[2]
    return {codec.strip():{value.strip() for value in values.split(',')}
            for codec,values in re.findall(r'^\s*([^:\r\n]+):\s*([^\r\n]+)',section,re.MULTILINE)}


def select_decoder(stream, executable='nvencc'):
    codec={'hevc':'H.265/HEVC','h264':'H.264/AVC','mpeg2video':'MPEG2','vp9':'VP9','vc1':'VC-1','av1':'AV1'}.get(stream.get('codec_name'))
    pixel=stream.get('pix_fmt')
    if not codec or pixel not in ('yuv420p','yuv420p10le'):return 'avsw'
    resolved=Path(shutil.which(executable) or executable)
    try:modified=resolved.stat().st_mtime_ns
    except OSError as error:raise ValueError('NVEncC er ikke tilgængelig; konverteringen er ikke startet.') from error
    formats=capabilities(str(resolved),modified).get(codec,set())
    supported=('yv12(10bit)' in formats) if pixel=='yuv420p10le' else bool(formats&{'nv12','yv12'})
    return 'avhw' if supported else 'avsw'
