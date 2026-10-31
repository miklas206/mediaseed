"""FjordSeed preservation contract adapted for MediaSeed's isolated worker.
Reference: qlerup/fjordseed 06015c5f6aa5e2053f30681a758520de302ef29b.
NVEncC 9.39 handles HEVC and DV 7/8.1. Original files are read-only inputs.
"""
import json, os, subprocess
from collections import Counter
from fractions import Fraction
from mediaseed.conversion_decoder import select_decoder
from mediaseed.conversion_options import options_dict,selected_streams

def probe(path):
    result=subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-show_chapters',
                           '-of','json',str(path)],capture_output=True,text=True,check=True,timeout=120)
    data=json.loads(result.stdout)
    frames=subprocess.run(['ffprobe','-v','error','-threads','1','-select_streams','v:0',
        '-read_intervals','0%+#3','-show_frames','-show_entries','frame_side_data','-of','json',str(path)],
        capture_output=True,text=True,check=True,timeout=120)
    video=next(s for s in data['streams'] if s['codec_type']=='video')
    sides=video.setdefault('side_data_list',[])
    present={s['side_data_type'] for s in sides}
    data['_rpu_frames']=0
    for frame in json.loads(frames.stdout).get('frames',[]):
        for side in frame.get('side_data_list',[]):
            kind=side.get('side_data_type')
            if kind=='Dolby Vision RPU Data':data['_rpu_frames']+=1
            if kind in ('Mastering display metadata','Content light level metadata') and kind not in present:
                sides.append(side);present.add(kind)
    return data

def video(data):
    streams=[s for s in data['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')]
    if len(streams)!=1:
        raise ValueError('Filen skal have ét almindeligt videospor; originalen beholdes.')
    return streams[0]

def dovi(data):
    return next((s for s in video(data).get('side_data_list',[]) if s.get('side_data_type')=='DOVI configuration record'),{})

def command(source,target,bitrate,data,reader=None,options=None):
    policy=options_dict(options)
    v=video(data)
    dv=dovi(data)
    if dv and not (dv.get('dv_profile')==7 or (dv.get('dv_profile')==8 and dv.get('dv_bl_signal_compatibility_id')==1)):
        raise ValueError('Denne Dolby Vision-profil understøttes ikke; originalen beholdes.')
    depth='10' if '10' in v.get('pix_fmt','') or dv or v.get('color_transfer') in ('smpte2084','arib-std-b67') else '8'
    if v.get('pix_fmt') not in ('yuv420p','yuv420p10le'):
        raise ValueError('Videoens farveformat understøttes ikke; originalen beholdes.')
    reader=reader or select_decoder(v)
    if reader not in ('avhw','avsw'):raise ValueError('Unsupported video reader')
    args=['nvencc','--'+reader,'-i',str(source),'-o',str(target),'-c','hevc',
          '--profile','main10' if depth=='10' else 'main','--output-depth',depth,'--preset',policy['preset'],
          '--vbr',str(bitrate*1000),'--max-bitrate',str(bitrate*1500),'--vbv-bufsize',str(bitrate*1500),
          '--colormatrix','auto','--transfer','auto','--colorprim','auto','--chromaloc','auto',
          '--metadata','copy','--avsync','vfr']
    for kind,flag in (('audio','audio'),('subtitle','sub')):
        selected=selected_streams(data,kind,policy)
        original=[s for s in data['streams'] if s['codec_type']==kind]
        if kind=='audio' and original and policy['audio']=='languages' and not selected:
            raise ValueError('Ingen lydspor matcher de valgte sprog')
        if selected:
            indexes=[str(i+1) for i,stream in enumerate(original) if stream in selected]
            args+=['--'+flag+'-copy',','.join(indexes),'--'+flag+'-metadata','copy','--'+flag+'-disposition','copy']
    if policy['attachments']:args+=['--attachment-copy']
    if policy['chapters']:args+=['--chapter-copy']
    if v.get('color_transfer')=='smpte2084':args+=['--max-cll','copy','--master-display','copy']
    if dv and policy['dolby_vision']:args+=['--dolby-vision-profile','8.1','--dolby-vision-rpu','copy']
    return args

def validate(original,result,options=None):
    policy=options_dict(options)
    before,after=video(original),video(result)
    for field in ('width','height','pix_fmt','color_transfer','color_primaries','color_space'):
        if before.get(field)!=after.get(field):
            raise ValueError('Kontrol fejlede: videoens '+field+' blev ændret.')
    a,b=Fraction(before['avg_frame_rate']),Fraction(after['avg_frame_rate'])
    if abs(float(a-b))>.01:raise ValueError('Billedhastigheden blev ændret.')
    if abs(float(original['format']['duration'])-float(result['format']['duration']))>3:
        raise ValueError('Kontrol fejlede: varigheden blev ændret.')
    def duration(data):
        stream=video(data)
        tag=stream.get('tags',{}).get('DURATION','').split(':')
        if len(tag)==3:return sum(float(x)*m for x,m in zip(tag,(3600,60,1)))
        return float(stream.get('duration',data['format']['duration']))
    if abs(duration(original)-duration(result))>3:
        raise ValueError('Kontrol fejlede: videospor er afkortet.')
    def tracks(data,kind,expected=False):
        return Counter((s.get('codec_name'),s.get('tags',{}).get('language','und'),s.get('channels'),
                        s.get('tags',{}).get('title',''),s.get('disposition',{}).get('default',0),s.get('disposition',{}).get('forced',0))
                       for s in (selected_streams(data,kind,policy) if expected else [s for s in data['streams'] if s['codec_type']==kind]))
    for kind in ('audio','subtitle','attachment'):
        if tracks(original,kind,True)!=tracks(result,kind):raise ValueError('Kontrol fejlede: '+kind+'-spor ændret.')
    chapters=original.get('chapters',[]) if policy['chapters'] else []
    if len(chapters)!=len(result.get('chapters',[])):
        raise ValueError('Kontrol fejlede: kapitler mangler.')
    for old,new in zip(chapters,result.get('chapters',[])):
        if abs(float(old['start_time'])-float(new['start_time']))>.1 or old.get('tags',{}).get('title')!=new.get('tags',{}).get('title'):
            raise ValueError('Kontrol fejlede: kapitler ændret.')
    required={s['side_data_type'] for s in before.get('side_data_list',[])} & {'Mastering display metadata','Content light level metadata'}
    if not required <= {s['side_data_type'] for s in after.get('side_data_list',[])}:
        raise ValueError('Kontrol fejlede: HDR-metadata mangler.')
    if dovi(original) and policy['dolby_vision']:
        dv=dovi(result)
        if not (dv.get('dv_profile')==8 and dv.get('dv_bl_signal_compatibility_id')==1 and result.get('_rpu_frames')):
            raise ValueError('Kontrol fejlede: Dolby Vision 8.1/RPU mangler.')
    if not policy['dolby_vision'] and (dovi(result) or result.get('_rpu_frames')):
        raise ValueError('Kontrol fejlede: Dolby Vision blev ikke fjernet.')

def restore_flags(original,target,options=None):
    # NVEncC's unqualified disposition copy may apply the first track's flags
    # to all tracks. Correct MKV headers in place, without remuxing DV video.
    args=['mkvpropedit',str(target)]
    for kind,prefix in (('audio','a'),('subtitle','s')):
        for index,stream in enumerate(selected_streams(original,kind,options),1):
            args+=['--edit',f'track:{prefix}{index}','--set','flag-default='+str(stream.get('disposition',{}).get('default',0)),
                   '--set','flag-forced='+str(stream.get('disposition',{}).get('forced',0))]
    # Rebuild statistics instead of retaining the original video's old bitrate.
    args+=['--add-track-statistics-tags']
    result=subprocess.run(args,capture_output=True,text=True,timeout=1800,env={**os.environ,'LC_ALL':'C.UTF-8'})
    if result.returncode not in (0,1):raise ValueError('MKV-metadata kunne ikke bevares; originalen beholdes.')
