"""Bounded automatic activity without private feed URLs or credentials."""
import hashlib
import time


class Activity:
    def __init__(self, database):
        self.database = database
        with database() as db:
            db.execute("CREATE TABLE IF NOT EXISTS automatic_activity (id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, feed TEXT, torrent_hash TEXT)")

    def record(self, source, key, title, status, feed=None, torrent_hash=None):
        identifier = hashlib.sha256((source + ':' + key + ':' + status).encode()).hexdigest()
        with self.database() as db:
            db.execute("INSERT OR IGNORE INTO automatic_activity VALUES (?,?,?,?,?,?,?)", (identifier, source, str(title)[:300], status, time.time(), feed, torrent_hash))
            db.execute("DELETE FROM automatic_activity WHERE id NOT IN (SELECT id FROM automatic_activity ORDER BY created DESC LIMIT 1000)")

    def public(self):
        with self.database() as db:
            rows = db.execute("SELECT source,title,status,created,feed,torrent_hash FROM automatic_activity ORDER BY created DESC LIMIT 10").fetchall()
        return {"entries": [dict(zip(('source','title','status','created','feed','hash'), row)) for row in rows]}
