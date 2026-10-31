"""One-time extension of existing time-based cleanup to 50 active hours."""
import json


def upgrade_cleanup_hours(db):
    db.execute('CREATE TABLE IF NOT EXISTS cleanup_migrations (name TEXT PRIMARY KEY)')
    name = 'active-seeding-50-hours-v1'
    if db.execute('SELECT 1 FROM cleanup_migrations WHERE name=?', (name,)).fetchone():
        return

    def extend(policy):
        hours = policy.get('hours')
        if policy.get('mode') in {'time', 'both', 'either'} and type(hours) in (int, float) and 0 < hours < 50:
            policy['hours'] = 50
            return True
        return False

    for identifier, payload in db.execute('SELECT id,payload FROM torrent_cleanup').fetchall():
        record = json.loads(payload)
        if extend(record):
            db.execute('UPDATE torrent_cleanup SET payload=? WHERE id=?', (json.dumps(record), identifier))
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
        for identifier, payload in db.execute("SELECT id,payload FROM settings WHERE kind='feeds'").fetchall():
            record = json.loads(payload)
            policy = record.get('cleanup')
            if isinstance(policy, dict) and extend(policy):
                db.execute("UPDATE settings SET payload=? WHERE kind='feeds' AND id=?", (json.dumps(record), identifier))
    db.execute('INSERT INTO cleanup_migrations VALUES (?)', (name,))
