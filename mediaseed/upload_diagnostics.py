"""Bounded tracker diagnostics; never retain request bodies or raw error pages."""
import html,re
from urllib.parse import quote,quote_plus


def safe_text(value,secrets=(),limit=400):
    if not isinstance(value,str):return ''
    text=html.unescape(value)
    for secret in secrets:
        if isinstance(secret,str) and len(secret)>=4:
            for variant in {secret,quote(secret,safe=''),quote_plus(secret)}:text=text.replace(variant,'[redacted]')
    text=re.sub(r'https?://[^\s<>"\']+','[URL removed]',text,flags=re.I)
    text=re.sub(r'(?i)\bBearer\s+[^\s,;]+','Bearer [redacted]',text)
    text=re.sub(r'(?i)\b(api[_-]?key|passkey|token|password|secret|cookie)\s*[:=]\s*["\']?[^\s,;"\']+',r'\1=[redacted]',text)
    text=re.sub(r'\b[a-fA-F0-9]{24,}\b','[redacted]',text)
    text=re.sub(r'<[^>]*>','',text)
    return ' '.join(text.split())[:limit]


def tracker_diagnostics(response,account):
    secrets=[value for key,value in account.items() if re.search(r'key|token|secret|password|cookie',key,re.I) and isinstance(value,str)]
    status=response.status_code
    kind={401:'authentication',403:'permission',422:'validation',429:'rate_limit'}.get(status,'server' if status>=500 else 'tracker_response')
    diagnostic={'httpStatus':status,'kind':kind,'message':'','errors':{}}
    try:payload=response.json()
    except ValueError:
        diagnostic['responseFormat']='html' if 'html' in response.headers.get('content-type','').lower() or response.content.lstrip().startswith(b'<') else 'non_json'
        return diagnostic
    diagnostic['responseFormat']='json'
    if not isinstance(payload,dict):return diagnostic
    for key in ['message','error','detail']:
        message=safe_text(payload.get(key),secrets)
        if message:diagnostic['message']=message;break
    errors=payload.get('errors')
    if payload.get('success') is False:
        data=payload.get('data')
        if not diagnostic['message']:diagnostic['message']=safe_text(data,secrets)
        if errors is None and isinstance(data,dict):
            errors=data.get('errors')
            if errors is None:
                fields={'name','description','mediainfo','bdinfo','category_id','type_id','resolution_id','tmdb','imdb','tvdb','mal','igdb','anonymous','personal_release','internal','stream','featured','free','doubleup','sticky','torrent','nfo','torrent_file','nfo_file','upload','user'}
                errors={key:message for key,message in data.items() if key in fields}
    if isinstance(errors,dict):
        for field,messages in list(errors.items())[:24]:
            if not isinstance(field,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}',field) or re.search('key|token|secret|password|cookie',field,re.I):continue
            if isinstance(messages,str):messages=[messages]
            if not isinstance(messages,list):continue
            clean=[safe_text(message,secrets,240) for message in messages[:3]]
            clean=[message for message in clean if message]
            if clean:diagnostic['errors'][field]=clean
    return diagnostic


def diagnostic_message(diagnostic):
    parts=[]
    if diagnostic.get('message'):parts.append(diagnostic['message'])
    for field,messages in diagnostic.get('errors',{}).items():parts.append(field+': '+'; '.join(messages))
    return ' '.join(parts)[:2500]
