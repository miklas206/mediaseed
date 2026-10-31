"""Preservation regression cases adapted from the pinned FjordSeed reference."""
from pathlib import Path
import pytest
from mediaseed.conversion_media import command,validate

@pytest.fixture(autouse=True)
def synthetic_decoder(monkeypatch):
    monkeypatch.setattr('mediaseed.conversion_media.select_decoder',lambda stream:'avhw')


def media():
    return {'format':{'duration':'900'},'_rpu_frames':3,'chapters':[], 'streams':[
        {'codec_type':'video','codec_name':'hevc','width':3840,'height':2160,'pix_fmt':'yuv420p10le',
         'avg_frame_rate':'24000/1001','color_transfer':'smpte2084','side_data_list':[
             {'side_data_type':'DOVI configuration record','dv_profile':8,'dv_bl_signal_compatibility_id':1},
             {'side_data_type':'Mastering display metadata'}]},
        {'codec_type':'audio','codec_name':'truehd','channels':8,'tags':{'language':'eng'}},
        {'codec_type':'subtitle','codec_name':'subrip','tags':{'language':'dan'}}]}


def test_gpu_preserves_tracks_and_rejects_missing_dovi():
    original=media();args=command(Path('in.mkv'),Path('out.mkv'),20,original)
    assert '--avhw' in args and '--avsw' not in args
    assert args[0]=='nvencc' and '--dolby-vision-rpu' in args and '--audio-copy' in args
    assert '--seek' not in args and '--seekto' not in args and '--trim' not in args
    assert validate(original,media()) is None
    result=media();result['_rpu_frames']=0
    with pytest.raises(ValueError,match='Dolby Vision'):validate(original,result)
    result=media();result['streams'].pop()
    with pytest.raises(ValueError,match='subtitle'):validate(original,result)


def test_unsupported_dovi_fails_without_losing_original():
    data=media();data['streams'][0]['side_data_list'][0]['dv_profile']=5
    with pytest.raises(ValueError,match='Dolby Vision'):command('in','out',20,data)


@pytest.mark.parametrize('rate',[20,30,40])
def test_quality_choice_reaches_encoder_with_variable_bitrate(rate):
    args=command(Path('in.mkv'),Path('out.mkv'),rate,media())
    assert args[args.index('--vbr')+1]==str(rate*1000)
    assert args[args.index('--max-bitrate')+1]==str(rate*1500)
    assert args[args.index('--output-depth')+1]=='10'


def test_strip_dolby_vision_retains_hdr10_and_validates_absence():
    import copy
    original=media();options={'dolby_vision':False,'preset':'P5'}
    args=command('in','out',20,original,options=options)
    assert '--dolby-vision-rpu' not in args and '--dolby-vision-profile' not in args
    assert '--master-display' in args and '--max-cll' in args
    assert args[args.index('--preset')+1]=='P5'
    result=copy.deepcopy(original);result['_rpu_frames']=0
    result['streams'][0]['side_data_list'].pop(0)
    validate(original,result,options)
    with pytest.raises(ValueError,match='ikke fjernet'):validate(original,original,options)
    result['streams'][0]['side_data_list']=[]
    with pytest.raises(ValueError,match='HDR'):validate(original,result,options)


def test_language_track_selection_and_flags(monkeypatch):
    import copy
    from mediaseed.conversion_media import restore_flags
    original=media()
    original['streams'].append({'codec_type':'audio','codec_name':'aac','channels':2,'tags':{'language':'dan'},'disposition':{'default':0,'forced':1}})
    options={'audio':'languages','audio_languages':['dan'],'subtitles':'none','attachments':False,'chapters':False}
    args=command('in','out',20,original,options=options)
    assert args[args.index('--audio-copy')+1]=='2'
    assert '--sub-copy' not in args and '--attachment-copy' not in args and '--chapter-copy' not in args
    result=copy.deepcopy(original);result['streams']=[s for s in result['streams'] if s['codec_type']=='video' or s.get('tags',{}).get('language')=='dan' and s['codec_type']=='audio']
    validate(original,result,options)
    with pytest.raises(ValueError,match='audio'):validate(original,original,options)
    observed=[]
    monkeypatch.setattr('mediaseed.conversion_media.subprocess.run',lambda args,**kwargs:observed.append(args) or type('Result',(),{'returncode':0})())
    restore_flags(original,'out',options)
    assert 'track:a1' in observed[0] and 'track:a2' not in observed[0] and 'flag-forced=1' in observed[0]
    with pytest.raises(ValueError,match='Ingen lydspor'):command('in','out',20,original,options={'audio':'languages','audio_languages':['fra']})
    args=command('in','out',20,original,options={'audio':'none'})
    assert '--audio-copy' not in args
