"""Durable feed attribution; never infer credit from the feed's current badges."""

import re


def initialize(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS rss_downloads (feed TEXT NOT NULL, hash TEXT NOT NULL, title TEXT NOT NULL, added REAL NOT NULL, credited REAL, last_value REAL, identity REAL, PRIMARY KEY(feed,hash))"
    )
    db.execute("CREATE INDEX IF NOT EXISTS rss_downloads_hash ON rss_downloads(hash)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS rss_downloads_page ON rss_downloads(feed,added DESC,hash)"
    )


def migrate(db):
    initialize(db)
    db.execute(
        "INSERT OR IGNORE INTO rss_downloads(feed,hash,title,added) SELECT feed,lower(torrent_hash),'',MIN(created) FROM rss_history WHERE status='added' AND torrent_hash IS NOT NULL GROUP BY feed,lower(torrent_hash)"
    )


def record(db, feed, identifier, title, added):
    db.execute(
        "INSERT INTO rss_downloads(feed,hash,title,added) VALUES (?,?,?,?) ON CONFLICT(feed,hash) DO UPDATE SET title=excluded.title",
        (feed, identifier, str(title)[:300], added),
    )


def observe(db, row, value):
    identifier = str(row.get("hash", "")).lower()
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", identifier):
        return
    records = db.execute(
        "SELECT feed,credited,last_value,identity FROM rss_downloads WHERE hash=?",
        (identifier,),
    ).fetchall()
    category = str(row.get("category", ""))
    if category.startswith("MediaSeed-RSS-"):
        records = [
            r for r in records if r[0] == category.removeprefix("MediaSeed-RSS-")
        ]
    elif len(records) > 1:
        records = [r for r in records if r[3] and r[3] == row.get("added_on")]
        if len(records) != 1:
            return
    for feed, credited, last, identity in records:
        current_identity = row.get("added_on") or identity
        reset = bool(identity and current_identity and identity != current_identity)
        if reset and "category" in row and not category.startswith("MediaSeed-RSS-"):
            continue
        delta = value if last is None or reset else max(0, value - last)
        db.execute(
            "UPDATE rss_downloads SET credited=?,last_value=?,identity=?,title=CASE WHEN ?!='' THEN ? ELSE title END WHERE feed=? AND hash=?",
            (
                (credited or 0) + delta,
                value if reset or last is None else max(last, value),
                current_identity,
                str(row.get("name", ""))[:300],
                str(row.get("name", ""))[:300],
                feed,
                identifier,
            ),
        )


def summary(db, feed):
    count, known, total = db.execute(
        "SELECT COUNT(*),COUNT(credited),SUM(credited) FROM rss_downloads WHERE feed=?",
        (feed,),
    ).fetchone()
    return {
        "downloads": count,
        "creditedUploadedBytes": (total or 0) if known or not count else None,
        "unknownDownloads": count - known,
        "estimated": True,
    }
