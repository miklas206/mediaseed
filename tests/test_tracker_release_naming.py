from types import SimpleNamespace
import pytest
from mediaseed.upload_engine.release_naming import release_names, technical_tokens, complete_video_metadata, NamingError
from mediaseed.upload_engine.media_tags import audio_tokens

def video(**overrides):
 return {'file':'film.mkv','languages':['da','no','sv','fi'],'technical':[{'@type':'Video','Format':'HEVC','Width':'3840','Height':'2160','ScanType':'Progressive','HDR_Format':'Dolby Vision / SMPTE ST 2086',**overrides},{'@type':'Audio','Default':'Yes','Format':'DTS','Format_Commercial_IfAny':'DTS-HD HRA + DTS:X','Channels':'8','ChannelLayout':'C L R LFE Lb Rb Lss Rss Objects'},{'@type':'Audio','Default':'No','Format':'DTS','Format_AdditionalFeatures':'XLL','Channels':'8','ChannelLayout':'L R C LFE Lb Rb Lss Rss'}]}
def body(**changes):
 return SimpleNamespace(catalogue_title='Furious 7',title='Furious 7 2015',kind='movie',edition='',source_type='BluRay',source_service='',release_format='',media_mode='in_place',**changes)

def test_actual_converted_movie_uses_observed_primary_audio_and_standard_quality():
 title,files=release_names(body(),[video()],{'year':2015})
 assert title=='Furious.7.2015.NORDiC.2160p.UHD.BluRay.HEVC.DV.HDR.DTS-HD.HRA7.1-MDTECH'
 assert files['film.mkv']==title.removesuffix('-MDTECH')+'.MDTECH.mkv'
 assert 'MA7.1' not in title and 'REMUX' not in title

@pytest.mark.parametrize('library,expected',[('x265 4.0','x265'),('Lavc hevc_nvenc','HEVC'),('','HEVC')])
def test_encoder_not_guessed_from_codec(library,expected):
 assert technical_tokens([video(Encoded_Library=library)])[1]==expected

def test_cropped_progressive_raster_uses_release_quality():
 assert technical_tokens([video(Height='1608')])[0]=='2160p'
 assert technical_tokens([video(Width='1920',Height='800')])[0]=='1080p'

def test_scan_is_verified_with_ffprobe_when_mediainfo_omits_it():
 tracks=video(ScanType='')['technical'];calls=[]
 def run(args):calls.append(args);return '{"streams":[{"field_order":"progressive","tags":{"ENCODER":"x265"}}]}'
 complete_video_metadata(tracks,'synthetic.mkv',run)
 assert tracks[0]['ScanType']=='Progressive' and tracks[0]['Encoded_Library']=='x265'
 assert calls[0][0]=='ffprobe'

def test_unknown_scan_blocks_name_instead_of_guessing():
 with pytest.raises(NamingError):technical_tokens([video(ScanType='')])

def test_converted_film_cannot_claim_remux():
 b=body();b.release_format='REMUX'
 with pytest.raises(NamingError,match='konverteret'):release_names(b,[video()],{'year':2015})

@pytest.mark.parametrize('evidence,label',[('DTS-HD Master Audio','DTS-HD.MA7.1'),('DTS-HD HRA','DTS-HD.HRA7.1'),('','DTS')])
def test_dts_profiles(evidence,label):
 v=video();v['technical']=[{**v['technical'][1],'Format_Commercial_IfAny':evidence}]
 assert audio_tokens([v])[0]==label
