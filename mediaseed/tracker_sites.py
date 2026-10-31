"""Tracker origins and bounded public Internet transport for UNIT3D adapters."""
import asyncio
import hashlib
import ipaddress
import re
import socket
from urllib.parse import urlsplit, unquote

import httpcore
import httpx
from fastapi import HTTPException


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


def download_address(account, value):
    try:
        if not isinstance(value,str) or len(value)>4096 or any(ord(c)<33 for c in value): raise ValueError()
        url=urlsplit(value)
        if (url.scheme!='https' or url.username or url.password or url.fragment or url.port not in (None,443)
            or 'https://'+str(url.hostname).lower()!=origin(account)
            or not re.fullmatch(r'/torrents?/download/[^\s]+',url.path)
            or '\\' in unquote(url.path) or any(p in ('.','..') for p in unquote(url.path).split('/'))): raise ValueError()
        return url
    except (ValueError,TypeError):
        raise HTTPException(502,'Unsafe tracker download address') from None


def account_data(data):
    if isinstance(data,dict) and isinstance(data.get('data'),dict): data=data['data']
    if not isinstance(data,dict) or not isinstance(data.get('stats'),dict):
        raise HTTPException(502,'This tracker does not provide a compatible account API')
    return data


class PublicNetworkBackend(httpcore.AnyIOBackend):
    """Resolve once per connection, validate every answer, then connect to that IP.

    TLS retains the original pool hostname for SNI and certificate verification.
    No second DNS lookup can redirect the connection to a private address.
    """
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            async with asyncio.timeout(min(timeout or 10,10)):
                answers=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM)
                addresses=list(dict.fromkeys(answer[4][0] for answer in answers))
                if port!=443 or not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                    raise httpcore.ConnectError('Tracker must resolve to public Internet addresses')
                for address in addresses:
                    try:
                        return await super().connect_tcp(address,port,timeout,local_address,socket_options)
                    except (httpcore.ConnectError,httpcore.ConnectTimeout):
                        continue
                raise httpcore.ConnectError('Tracker connection unavailable')
        except (TimeoutError,socket.gaierror):
            raise httpcore.ConnectError('Tracker DNS unavailable') from None


def secure_client(client):
    # httpx's default transport owns the httpcore pool. Keep a single bounded
    # pool, while replacing its dialer before any requests. Mock transports used
    # in tests have no socket pool and remain entirely synthetic.
    transport=client._transport
    if isinstance(transport,httpx.AsyncHTTPTransport):
        transport._pool._network_backend=PublicNetworkBackend()
    return client


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
