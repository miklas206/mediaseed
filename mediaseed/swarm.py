"""Display-only tracker swarm samples; never used to stop or remove torrents."""
import asyncio
import logging
import re
import time

from mediaseed.tracker_sites import identity, matching_accounts

POLL_SECONDS = 60
STALE_SECONDS = 180
REQUEST_BUDGET = 6  # Per independent 15-second background tick, shared across accounts.


def peer_count(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,12}', value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', value)


def scope(account):
    return str(account['id']) + ':' + identity(account).hex()


class SwarmMonitor:
    def __init__(self, database, accounts, qbit, tracker_read, record_id):
        self.database, self.accounts, self.qbit = database, accounts, qbit
        self.tracker_read, self.record_id = tracker_read, record_id
        self.lock = asyncio.Lock()
        with database() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS torrent_swarm (
                hash TEXT NOT NULL, scope TEXT NOT NULL, checked REAL NOT NULL,
                seeders INTEGER, leechers INTEGER, next_poll REAL NOT NULL,
                failed INTEGER NOT NULL, PRIMARY KEY(hash,scope))''')

    def observe(self, identifier, account, attributes, now=None):
        if not valid_hash(identifier) or not isinstance(attributes, dict) or str(attributes.get('info_hash', '')).lower() != identifier.lower():
            return False
        now = time.time() if now is None else now
        seeds, leeches = (peer_count(attributes.get(key)) for key in ('seeders', 'leechers'))
        if seeds is None and leeches is None:
            return False
        with self.database() as db:
            db.execute('INSERT OR REPLACE INTO torrent_swarm VALUES (?,?,?,?,?,?,0)',
                       (identifier.lower(), scope(account), now, seeds, leeches, now + POLL_SECONDS))
            db.execute('DELETE FROM torrent_swarm WHERE rowid IN (SELECT rowid FROM torrent_swarm ORDER BY checked DESC LIMIT -1 OFFSET 5000)')
        return True

    def snapshot(self, rows, now=None):
        """One local read; dashboard/list requests never call a tracker API."""
        now = time.time() if now is None else now
        accounts = self.accounts('trackers')[:10]
        identifiers = list(dict.fromkeys(row['hash'].lower() for row in rows[:500]
                                        if isinstance(row, dict) and valid_hash(row.get('hash'))))
        if not identifiers or not accounts:
            return {}
        with self.database() as db:
            samples = {(r[0], r[1]): r[2:] for r in db.execute(
                'SELECT hash,scope,checked,seeders,leechers,failed FROM torrent_swarm WHERE hash IN (' + ','.join('?' for _ in identifiers) + ')', identifiers)}
        result = {}
        for row in rows[:500]:
            if not isinstance(row, dict) or not valid_hash(row.get('hash')) or not row.get('tracker'):
                continue
            matches = matching_accounts(accounts, row['tracker'])
            if not matches:
                continue
            account, identifier = matches[0], row['hash'].lower()
            sample = samples.get((identifier, scope(account)))
            checked, seeds, leeches, failed = sample if sample else (None, None, None, False)
            status = ('unavailable' if failed else 'ok' if checked is not None and 0 <= now - checked <= STALE_SECONDS
                      else 'stale' if checked else 'pending')
            result[identifier] = {'tracker': str(account['name'])[:80], 'seeders': seeds, 'leechers': leeches,
                                  'checkedAt': checked or None, 'status': status}
        return result

    async def tick(self):
        async with self.lock:
            accounts = self.accounts('trackers')[:10]
            if not accounts:
                return
            rows = (await self.qbit('/api/v2/torrents/info', payload={'limit': 500})).json()
            if not isinstance(rows, list):
                return
            now = time.time()
            with self.database() as db:
                clocks = {(r[0], r[1]): r[2] for r in db.execute('SELECT hash,scope,next_poll FROM torrent_swarm')}
            candidates = {}
            for row in rows[:500]:
                if not isinstance(row, dict) or not valid_hash(row.get('hash')) or not row.get('tracker'):
                    continue
                matches = matching_accounts(accounts, row['tracker'])
                if not matches:
                    continue
                account, identifier = matches[0], row['hash'].lower()
                key = (identifier, scope(account))
                due = clocks.get(key, 0)
                if due > now:
                    continue
                # Reuse the existing exact-hash metadata lookup. Do not search
                # every tracker on every dashboard refresh or guess record IDs.
                record = str(self.record_id(identifier, account) or '')
                if re.fullmatch(r'[1-9][0-9]{0,17}', record):
                    candidates[key] = (due, identifier, account, record)
            for _, identifier, account, record in sorted(candidates.values(), key=lambda item: item[0])[:REQUEST_BUDGET]:
                checked = time.time()
                with self.database() as db:
                    # Reserve before I/O so restarts and errors cannot busy-loop.
                    db.execute('INSERT INTO torrent_swarm VALUES (?,?,0,NULL,NULL,?,1) ON CONFLICT(hash,scope) DO UPDATE SET next_poll=excluded.next_poll',
                               (identifier, scope(account), checked + POLL_SECONDS))
                try:
                    async with asyncio.timeout(15):
                        data = await self.tracker_read(account, '/api/torrents/' + record, None)
                    value = data.get('data', data) if isinstance(data, dict) else {}
                    attributes = value.get('attributes') if isinstance(value, dict) else None
                    if str(value.get('id')) != record or not self.observe(identifier, account, attributes):
                        raise ValueError('Tracker swarm did not match the requested torrent')
                except Exception as error:  # Isolate accounts; never log URLs/tokens.
                    with self.database() as db:
                        db.execute('UPDATE torrent_swarm SET failed=1,next_poll=? WHERE hash=? AND scope=?',
                                   (time.time() + (300 if getattr(error, 'status_code', None) == 429 else POLL_SECONDS), identifier, scope(account)))
                    logging.getLogger(__name__).warning('Swarm observation deferred (%s)', type(error).__name__)
            with self.database() as db:
                active = [scope(account) for account in accounts]
                db.execute('DELETE FROM torrent_swarm WHERE scope NOT IN (' + ','.join('?' for _ in active) + ')', active)
