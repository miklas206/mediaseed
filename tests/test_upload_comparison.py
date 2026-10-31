import copy,json
import pytest
from mediaseed.upload_comparison import measured_facts,report_tracks,compare

TRACKS=[{'@type':'General','FileSize':'24000000000','Duration':'8000','OverallBitRate':'24000000'},
 {'@type':'Video','Format':'HEVC','Width':'3840','Height':'2160','BitRate':'20000000','BitDepth':'10','FrameRate':'23.976','HDR_Format':'Dolby Vision / SMPTE ST 2086','HDR_Format_Compatibility':'HDR10'},
 {'@type':'Audio','Format':'MLP FBA','Format_Commercial_IfAny':'Dolby TrueHD with Dolby Atmos','Channels':'8','ChannelLayout':'L R C LFE Ls Rs Lb Rb','Language':'en','BitRate':'3200000'},
 {'@type':'Audio','Format':'E-AC-3','Format_AdditionalFeatures':'JOC','Channels':'6','ChannelLayout':'L R C LFE Ls Rs','Language':'en','BitRate':'768000'},
 *[{'@type':'Text','Format':'PGS','Language':lang,'Forced':'No'} for lang in ['da','sv','no','fi']]]
LOCAL=measured_facts(TRACKS)


def test_all_tracks_and_bitrates_are_preserved_and_equivalence_requires_all_dimensions():
 facts=measured_facts(TRACKS)
 assert facts['audio'][0]['codec']=='TrueHD' and facts['audio'][0]['channels']==8 and facts['audio'][0]['atmos']
 assert facts['audio'][1]['codec']=='DDP' and facts['audio'][1]['channels']==6
 assert facts['videoBitrate']==20000000 and facts['totalBitrate']==24000000
 assert {t['language'] for t in facts['subtitles']}=={'da','sv','no','fi'}
 assert compare(facts,facts)['status']=='equivalent'


@pytest.mark.parametrize('field',['audio','subtitles','hdr','size','videoBitrate'])
def test_relevant_differences_are_not_a_hard_duplicate(field):
 other=copy.deepcopy(LOCAL)
 if field=='audio':other[field]=[other[field][1]]
 elif field=='subtitles':other[field]=other[field][1:]
 elif field=='hdr':other[field]=['SDR']
 else:other[field]*=.7
 value=compare(LOCAL,other)
 assert value['status']=='different'
 assert next(r for r in value['fields'] if r['field']==field)['status']=='different'


def test_missing_report_or_bitrate_is_unknown_never_inferred_from_release_name():
 missing=measured_facts([],24000000000,source='tracker-report')
 assert compare(LOCAL,missing)['status']=='unknown'
 assert missing['audio'] is None and missing['subtitles'] is None and missing['hdr'] is None
 changed=copy.deepcopy(LOCAL);changed['videoBitrate']=None
 assert compare(LOCAL,changed)['status']=='unknown'


def test_media_info_text_and_json_units_and_audio_formats():
 text='''General
File size : 22.4 GiB
Duration : 2 h 13 min 20 s
Overall bit rate : 24.0 Mb/s
Video
Format : HEVC
Width : 3 840 pixels
Height : 2 160 pixels
Bit rate : 20.0 Mb/s
Bit depth : 10 bits
Frame rate : 23.976 (24000/1001) FPS
HDR format : Dolby Vision / HDR10
Audio #1
Format : MLP FBA 16-ch
Commercial name : Dolby TrueHD with Dolby Atmos
Channel(s) : 8 channels
Channel layout : L R C LFE Ls Rs Lb Rb
Language : English
Bit rate : 3 200 kb/s
Audio #2
Format : E-AC-3 JOC
Channel(s) : 6 channels
Language : English
Text #1
Format : UTF-8
Language : Danish
Forced : No
'''
 facts=measured_facts(report_tracks(text))
 assert facts['size']==pytest.approx(22.4*1024**3) and facts['duration']==8000
 assert facts['videoBitrate']==20000000 and facts['totalBitrate']==24000000
 assert facts['audio'][0]['codec']=='TrueHD' and facts['audio'][0]['bitrate']==3200000
 assert facts['audio'][1]['codec']=='DDP' and facts['audio'][1]['atmos']
 assert facts['subtitles']==[{'language':'da','format':'SRT','forced':False,'sdh':False}]
 assert report_tracks(json.dumps({'media':{'track':TRACKS}}))==TRACKS
 assert report_tracks({'media':[]})==[]
