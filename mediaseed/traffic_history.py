"""Physical qBittorrent lifetime counters, separate from tracker account credit."""
import json
import time
from mediaseed.intelligence import history_samples


class TrafficHistory:
    interval = 60

    def __init__(self, database):
        self.database = database
        self.failure = None
        with database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS insight_traffic (bucket INTEGER PRIMARY KEY, payload TEXT NOT NULL)')

    def observe(self, values, now=None):
        now = time.time() if now is None else now
        if not isinstance(values, dict):
            self.failure = 'unavailable'
            return False
        stats = {key: values.get(source) if type(values.get(source)) is int and 0 <= values[source] <= 2**63-1 else None
                 for key, source in [('real_uploaded', 'alltime_ul'), ('real_downloaded', 'alltime_dl')]}
        if all(value is None for value in stats.values()):
            self.failure = 'upgrade_required'
            return False
        with self.database() as db:
            newest = db.execute('SELECT MAX(bucket) FROM insight_traffic').fetchone()[0]
            bucket = int(now // self.interval)
            if newest is not None and newest > bucket:
                return False
            db.execute('INSERT OR REPLACE INTO insight_traffic VALUES (?,?)', (bucket, json.dumps({'stats': stats})))
            db.execute('DELETE FROM insight_traffic WHERE bucket<?', (int((now-90*86400)//self.interval),))
        self.failure = None
        return True

    def public(self, days=7, now=None):
        now = time.time() if now is None else now
        with self.database() as db:
            rows = db.execute('SELECT bucket,payload FROM insight_traffic WHERE bucket>=? ORDER BY bucket DESC LIMIT 129600', (int((now-days*86400)//self.interval),)).fetchall()[::-1]
            latest = db.execute('SELECT bucket,payload FROM insight_traffic ORDER BY bucket DESC LIMIT 1').fetchone()
        stride = max(1, (len(rows)+286)//287)
        at = latest[0]*self.interval if latest else None
        status = 'ok' if at is not None and 0 <= now-at <= 180 and not self.failure else 'stale' if latest else self.failure or 'pending'
        return {'status': status, 'scope': 'seedbox', 'checkedAt': at,
                'stats': json.loads(latest[1])['stats'] if latest else {},
                'sampleInterval': stride*self.interval,
                'samples': history_samples(rows, stride, interval=self.interval, keys=('real_uploaded', 'real_downloaded'))}
