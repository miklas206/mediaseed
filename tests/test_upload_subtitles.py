from mediaseed.media_languages import ROWS,NAMES,language_code
from mediaseed.upload_comparison import measured_facts
from mediaseed.upload_engine.media_tags import subtitle_languages
from mediaseed.upload_engine.description import generate_description,subtitle_text
from mediaseed.upload_engine.nfo import generate_nfo,box
from mediaseed.upload_engine.upload_helper import DescriptionRequest
from mediaseed.upload_nfo_repair import subtitle_field_text


def test_every_reported_language_and_track_format_is_in_preview_and_nfo():
 tracks=[{'@type':'General'},{'@type':'Video','Format':'HEVC'},*[{'@type':'Text','Language':code,'Format':'UTF-8'} for code in NAMES],{'@type':'Text','Language':'eng','Format':'PGS','Title':'English SDH','Forced':'No'},{'@type':'Text','Language':'fra','Format':'PGS','Forced':'Yes'}]
 assert subtitle_languages(tracks)==set(NAMES)
 video={'file':'Film.mkv','languages':['da','en','fi','ja','no','sv'],'comparison':measured_facts(tracks),'technical':tracks[:2]}
 body=DescriptionRequest(title='Film.2025.HEVC',tmdb=12,videos=['Film.mkv'],source_credit='Original source',source_service='none')
 for text in [generate_description(body,[video]),generate_nfo(body,[video]),subtitle_text(video)]:
  for name in NAMES.values():assert name in text
  assert 'SDH' in text and 'forced' in text
 assert 'English (SRT, PGS SDH)' in subtitle_text(video)
 assert 'French (SRT, PGS forced)' in subtitle_text(video)


def test_iso_codes_aliases_and_unknowns_are_preserved_without_guessing():
 for code,name,aliases in ROWS:
  for alias in [code,name,*aliases.split()]:assert language_code(alias)==code
 assert language_code('de-DE')=='de' and language_code('ar')=='ar'
 assert language_code('is')=='is' and language_code('isl')=='isl'
 for value in ['und','zxx','mul','English / French','',None]:assert language_code(value) is None


def test_legacy_subtitle_field_can_be_repaired_without_changing_other_fields():
 before='\n'.join(box('MEDIA INFO',['FILE         Film.mkv','SUBTITLES    Danish, English','FILE SIZE    22.93 GiB'])+box('ORIGINAL SOURCE AND CREDITS',['Original source unchanged']))
 after=subtitle_field_text(before,'Danish (PGS); Arabic (SRT); English (SRT, PGS SDH)')
 assert 'Arabic (SRT)' in after and 'PGS SDH' in after
 assert 'FILE SIZE    22.93 GiB' in after and 'Original source unchanged' in after
