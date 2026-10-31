from pathlib import Path
import json
from unittest.mock import Mock

from mediaseed.conversion import atomic
from mediaseed.conversion_advice import metadata,estimate,cache_key,analyze_inventory


def test_audio_container_overhead_and_variable_bitrate_interval_are_included():
    source={'identity':[60*1024**3,0,0,0]}
    data={'duration':7200,'codec':'hevc','audioBps':4_000_000,'audioEstimated':True}
    result=estimate(source,data,30)
    assert result['estimatedBytes']>7200*30_000_000/8
    assert result['savingLowBytes']<result['savingBytes']<result['savingHighBytes']
    assert result['recommended']
    assert estimate(source,data,40)['savingBytes']<result['savingBytes']
    assert not estimate({'identity':[1024**3,0,0,0]},data,30)['recommended']


def test_metadata_uses_header_probe_and_conservative_lossless_audio_fallback(monkeypatch):
    data={'format':{'duration':'7200'},'streams':[{'codec_type':'video','codec_name':'hevc','height':2160,'width':3840},
        {'codec_type':'audio','codec_name':'truehd'},{'codec_type':'audio','codec_name':'ac3','bit_rate':'640000'}]}
    calls=[]
    monkeypatch.setattr('mediaseed.conversion_advice.subprocess.run',lambda args,**kwargs:calls.append((args,kwargs)) or Mock(stdout=json.dumps(data)))
    info=metadata(Path('/synthetic.mkv'))
    assert info['audioBps']==8640000 and info['audioEstimated']
    assert '-show_frames' not in calls[0][0] and calls[0][1]['timeout']==60


def test_unapproved_analysis_paths_are_rejected_without_opening_media(tmp_path,monkeypatch):
    queue=tmp_path/'queue';queue.mkdir();root=tmp_path/'approved';root.mkdir()
    entry={'path':str(tmp_path/'outside.mkv'),'identity':[1000,0,0,0]}
    atomic(queue/'analysis-inventory.json',{'files':[entry]})
    monkeypatch.setattr('mediaseed.conversion_advice.metadata',lambda p:(_ for _ in ()).throw(AssertionError('Must not probe unapproved path')))
    analyze_inventory(queue,[root],lambda:False)
    assert json.loads((queue/('analysis-'+cache_key(entry)+'.json')).read_text())['error']
