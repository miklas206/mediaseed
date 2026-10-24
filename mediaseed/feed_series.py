"""Read-only series comparisons over the durable RSS upload ledger."""
import hashlib
import re
import unicodedata

EPISODE = re.compile(
    r'\b(?:S\d{1,3}(?:E\d{1,4})*(?:[- ]?E\d{1,4})*|\d{1,2}x\d{1,3}(?:x\d{1,3})*|'
    r'(?:19|20)\d{2}[- ](?:0[1-9]|1[0-2])[- ](?:0[1-9]|[12]\d|3[01]))\b', re.IGNORECASE)


def searchable(value):
    return re.sub(r'\s+', ' ', re.sub(r'[._]+', ' ', unicodedata.normalize('NFKC', value).casefold())).strip()


def describe(identifier, title):
    title = str(title or '')[:300]
    normalized = re.sub(r'[._]+', ' ', unicodedata.normalize('NFKC', title))
    match = EPISODE.search(normalized)
    name = normalized[:match.start()].strip(' -[(') if match else ''
    if match and name and any(letter.isalpha() for letter in name):
        name = re.sub(r'\s+', ' ', name).strip()
        identity = re.sub(r'[\W_]+', ' ', name.casefold()).strip()
        kind, episode = 'series', match.group().upper()
        key = hashlib.sha256(('series:' + identity).encode()).hexdigest()
    else:
        name, kind, episode = title or identifier, 'torrent', None
        key = hashlib.sha256(('torrent:' + identifier.lower()).encode()).hexdigest()
    return key, name, kind, episode


def remember(db, feed, identifier, title):
    key, name, kind, episode = describe(identifier, title)
    db.execute('''INSERT INTO rss_series VALUES (?,?,?,?,?,?,?,?)
                  ON CONFLICT(feed,hash) DO UPDATE SET series_key=excluded.series_key,
                  name=excluded.name,kind=excluded.kind,episode=excluded.episode,
                  source_title=excluded.source_title,search_name=excluded.search_name WHERE rss_series.source_title!=excluded.source_title''',
               (feed, identifier, key, name, kind, episode, str(title or '')[:300], searchable(name)))


def backfill(db):
    # Bound each batch and advance by row identity; never copy or reset credit.
    after = 0
    while True:
        rows = db.execute('''SELECT d.rowid,d.feed,d.hash,d.title FROM rss_downloads d
                             LEFT JOIN rss_series s ON s.feed=d.feed AND s.hash=d.hash
                             WHERE d.rowid>? AND (s.hash IS NULL OR s.source_title!=substr(d.title,1,300))
                             ORDER BY d.rowid LIMIT 500''', (after,)).fetchall()
        if not rows:
            return
        for rowid, feed, identifier, title in rows:
            remember(db, feed, identifier, title)
        after = rows[-1][0]


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS rss_series (
        feed TEXT NOT NULL, hash TEXT NOT NULL, series_key TEXT NOT NULL,
        name TEXT NOT NULL, kind TEXT NOT NULL, episode TEXT,
        source_title TEXT NOT NULL, search_name TEXT NOT NULL, PRIMARY KEY(feed,hash))''')
    db.execute('CREATE INDEX IF NOT EXISTS rss_series_groups ON rss_series(feed,series_key,hash)')
    backfill(db)


AGGREGATE = '''SELECT s.series_key,MIN(s.name),s.kind,COUNT(*),COUNT(d.credited),
                     SUM(d.credited),AVG(d.credited),MIN(d.added),MAX(d.added)
              FROM rss_downloads d JOIN rss_series s ON s.feed=d.feed AND s.hash=d.hash
              WHERE d.feed=?'''


def public(row):
    key, name, kind, count, known, total, average, first, latest = row
    return {'key': key, 'name': name, 'kind': kind, 'downloads': count,
            'knownDownloads': known, 'unknownDownloads': count-known,
            'creditedUploadedBytes': total, 'averageUploadedBytes': average,
            'firstAdded': first, 'latestAdded': latest, 'estimated': True}


def groups(db, feed, page, page_size, sort, search=""):
    order = {'upload': 'SUM(d.credited) DESC', 'average': 'AVG(d.credited) DESC', 'recent': 'MAX(d.added) DESC'}[sort]
    term = searchable(search)
    total = db.execute('''SELECT COUNT(DISTINCT s.series_key) FROM rss_series s
                         JOIN rss_downloads d ON d.feed=s.feed AND d.hash=s.hash WHERE d.feed=? AND instr(s.search_name,?)>0''', (feed, term)).fetchone()[0]
    records = db.execute(AGGREGATE + ' AND instr(s.search_name,?)>0 GROUP BY s.series_key ORDER BY ' + order + ',s.series_key LIMIT ? OFFSET ?',
                         (feed, term, page_size, (page-1)*page_size)).fetchall()
    return {'groups': [public(row) for row in records], 'page': page, 'pageSize': page_size,
            'total': total, 'view': 'series', 'sort': sort}


def members(db, feed, key, page, page_size):
    header = db.execute(AGGREGATE + ' AND s.series_key=? GROUP BY s.series_key', (feed, key)).fetchone()
    if not header:
        return {'entries': [], 'page': page, 'pageSize': page_size, 'total': 0, 'series': None}
    rows = db.execute('''SELECT d.hash,d.title,d.added,d.credited,s.episode FROM rss_downloads d
                         JOIN rss_series s ON s.feed=d.feed AND s.hash=d.hash
                         WHERE d.feed=? AND s.series_key=? ORDER BY d.added DESC,d.hash LIMIT ? OFFSET ?''',
                      (feed, key, page_size, (page-1)*page_size)).fetchall()
    return {'entries': [{'hash': h, 'title': title or h, 'created': at, 'creditedUploadedBytes': uploaded,
                         'status': 'added', 'episode': episode} for h, title, at, uploaded, episode in rows],
            'page': page, 'pageSize': page_size, 'total': header[3], 'series': public(header)}
