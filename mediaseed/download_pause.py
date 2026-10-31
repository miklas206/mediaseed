"""Pause eligibility uses the current client observation, not seeding history."""
import math
from fastapi import HTTPException

DOWNLOAD_STATES = {'downloading','forcedDL','stalledDL','queuedDL','metaDL','forcedMetaDL','stoppedDL','pausedDL'}


async def incomplete_download(qbit, identifier):
    response = await qbit('/api/v2/torrents/info', payload={'hashes':identifier,'limit':1})
    try:
        rows = response.json()
    except ValueError:
        raise HTTPException(503,'Cannot verify torrent download state') from None
    if response.status_code != 200 or not isinstance(rows,list):
        raise HTTPException(503,'Cannot verify torrent download state')
    if not rows:
        raise HTTPException(404,'Torrent not found')
    if len(rows)!=1 or not isinstance(rows[0],dict) or not isinstance(rows[0].get('hash'),str) or rows[0]['hash'].lower()!=identifier.lower():
        raise HTTPException(503,'Cannot verify torrent download state')
    row=rows[0];progress=row.get('progress')
    if type(progress) not in (int,float) or not math.isfinite(progress) or not 0<=progress<=1:
        raise HTTPException(503,'Cannot verify torrent download state')
    return progress<1 and row.get('state') in DOWNLOAD_STATES


def cancel_pending_start(cleanup, identifier):
    # Called under the cleanup lock only after the pause request is accepted.
    # An import still waiting for configuration must not restart behind the user.
    for rule in cleanup.records():
        if identifier.lower() in rule['hashes'] and rule.get('status') in {'preparing','waiting'} and rule.get('start'):
            rule['start']=False
            cleanup.save(rule)
