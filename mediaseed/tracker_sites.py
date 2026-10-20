"""Tracker identities and announce matching for isolated metadata retry jobs."""
import hashlib
import re
from urllib.parse import urlsplit


def normalize_origin(value):
    try:
        url = urlsplit(value.strip())
        host = (url.hostname or '').encode('idna').decode('ascii').lower()
        if (url.scheme != 'https' or url.username or url.password or url.port not in (None,443)
            or url.path not in ('','/') or url.query or url.fragment or len(host)>253
            or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',host)
            or host.endswith(('.localhost','.local','.internal','.test','.invalid','.example'))):
            raise ValueError()
        return 'https://'+host
    except (ValueError,UnicodeError,AttributeError):
        raise ValueError('Use a public HTTPS tracker website without a path or credentials') from None


def origin(account):
    return normalize_origin(account.get('base_url') or 'https://nordicbytes.org')


def identity(account):
    return hashlib.sha256((origin(account)+'\0'+account['api_key']).encode()).digest()


def matching_accounts(accounts, tracker):
    if not tracker: return accounts
    try: host=urlsplit(tracker).hostname
    except (ValueError,TypeError): return []
    result=[]
    for account in accounts:
        site=urlsplit(origin(account)).hostname
        if host==site or (host and host.endswith('.'+site)) or (site=='nordicbytes.org' and host in {'t.nordicswarm.org','t.polarswarm.org'}):
            result.append(account)
    return result
