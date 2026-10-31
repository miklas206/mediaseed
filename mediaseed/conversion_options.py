"""Persisted conversion policy. Missing options retain the legacy preservation contract."""
from typing import Annotated, Literal
from pydantic import BaseModel,ConfigDict,Field

Language=Annotated[str,Field(pattern=r'^[a-z]{3}$')]

class ConversionOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    dolby_vision: bool=True
    preset: Literal['P4','P5','P7']='P7'
    audio: Literal['all','languages','none']='all'
    audio_languages: list[Language]=Field(default_factory=lambda:['dan','eng'],min_length=1,max_length=12)
    subtitles: Literal['all','languages','none']='all'
    subtitle_languages: list[Language]=Field(default_factory=lambda:['dan','eng'],min_length=1,max_length=12)
    chapters: bool=True
    attachments: bool=True
    external_subtitles: bool=True


def options_dict(value=None):
    return ConversionOptions.model_validate(value or {}).model_dump()


def language(value):
    value=str(value or 'und').lower()
    return {'da':'dan','dk':'dan','en':'eng','de':'deu','ger':'deu','fre':'fra','fr':'fra','no':'nor','nb':'nob','nn':'nno','sv':'swe'}.get(value,value)


def selected_streams(data,kind,value=None):
    options=options_dict(value)
    streams=[s for s in data['streams'] if s['codec_type']==kind]
    if kind=='attachment':return streams if options['attachments'] else []
    key='audio' if kind=='audio' else 'subtitles'
    if options[key]=='none':return []
    if options[key]=='all':return streams
    wanted={language(v) for v in options['audio_languages' if kind=='audio' else 'subtitle_languages']}
    return [s for s in streams if language(s.get('tags',{}).get('language')) in wanted]


def keep_companion(path,stem,value=None):
    options=options_dict(value)
    if path.suffix.lower() not in {'.srt','.ass','.ssa','.sub','.idx','.vtt'}:return options['attachments']
    if not options['external_subtitles'] or options['subtitles']=='none':return False
    if options['subtitles']=='all':return True
    labels=path.name[len(stem):].lower().split('.')[:-1]
    return bool({language(v) for v in labels}&{language(v) for v in options['subtitle_languages']})
