from mediaseed.upload_engine.upload_helper import DescriptionRequest
from mediaseed.upload_engine.description import generate_description,bitrate
from mediaseed.upload_engine.nfo import generate_nfo
from mediaseed.upload_engine.source_service import source_credit
from mediaseed.upload_comparison import measured_facts
from test_upload_comparison import TRACKS


def test_bitrate_is_measured_and_displayed_in_nfo_and_description():
 body=DescriptionRequest(title='Film.2025.HEVC',tmdb=12,videos=['Film.mkv'],source_credit='Original BluRay credited',source_service='none')
 video={'file':'Film.mkv','technical':TRACKS,'comparison':measured_facts(TRACKS),'languages':['da'],'sizeBytes':24000000000}
 for text in [generate_nfo(body,[video]),generate_description(body,[video]),generate_nfo(body,[video],preview=True),generate_description(body,[video],preview=True)]:
  assert '20.00 Mbit/s' in text
  assert '24.00 Mbit/s' in text
  assert '3.20 Mbit/s' in text and '768 kbit/s' in text
  assert 'Download service:' not in text
 assert source_credit(body,[video])=='Original BluRay credited'
 body.source_credit+='\nDownload service: None (not a download; declared by uploader)'
 assert source_credit(body,[video])=='Original BluRay credited'
 body.source_service='NF'
 assert 'Download service: Netflix' in source_credit(body,[video])


def test_unknown_bitrates_are_not_guessed_from_filename_or_maximum():
 body=DescriptionRequest(title='Film.2025.HEVC.20Mbps',tmdb=12,videos=['Film.mkv'],source_credit='Original BluRay',source_service='none')
 video={'file':'Film.mkv','technical':[{'@type':'Video','Format':'HEVC','BitRate_Maximum':'40000000'}]}
 for text in [generate_nfo(body,[video]),generate_description(body,[video])]:
  assert 'Mbit/s' not in text and 'kbit/s' not in text
 for invalid in [None,'',0,-1,float('nan'),float('inf'),'not measured']:
  assert bitrate(invalid) is None
 assert bitrate('19 862 kb/s')=='19.86 Mbit/s'



def test_old_generated_conversion_credit_becomes_english_without_changing_source_name():
 old='Konverteret med NVIDIA HEVC af MDTech. Original kilde: Film.2025.REMUX-Original'
 expected='Converted with NVIDIA HEVC by MDTech. Original source: Film.2025.REMUX-Original'
 body=DescriptionRequest(title='Film.2025.HEVC',tmdb=12,videos=['Film.mkv'],source_credit=old,source_service='none')
 for text in [generate_description(body,[]),generate_nfo(body,[])]:
  assert 'Konverteret med' not in text and 'Original kilde:' not in text
  assert 'Converted with NVIDIA HEVC by MDTech.' in text and 'Film.2025.REMUX-Original' in text
 body.description_override='My own introduction.\n'+old+'\nMy own ending.'
 assert generate_description(body,[])=='My own introduction.\n'+expected+'\nMy own ending.'
