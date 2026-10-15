import time
from mediaseed import feed_uploads
from test_tracker_credit import setup, row, HASH


def test_credit_bonus_is_observed_not_retroactively_invented_and_feed_survives_removal(
    tmp_path,
):
    ledger, _, _, cache = setup(tmp_path, created=1000000000)
    now = time.time()
    with ledger.database() as db:
        feed_uploads.record(db, "feed", HASH, "Release", now)
    first = ledger.observe(row(100, added_on=now - 60), now)
    assert first["account_credited_uploaded"] == 100  # Past double upload is unknown.
    second = ledger.observe(row(150, added_on=now - 60), now + 5)
    assert (
        second["account_credited_uploaded"] == 200
    )  # New 50 bytes earn observed double credit.
    third = ledger.observe(row(200, added_on=now - 60), now + 301)
    assert (
        third["account_credited_uploaded"] == 250
    )  # Expired evidence cannot invent a bonus.
    with ledger.database() as db:
        assert feed_uploads.summary(db, "feed")["creditedUploadedBytes"] == 250
    ledger.forget(HASH)
    with ledger.database() as db:
        assert feed_uploads.summary(db, "feed")["creditedUploadedBytes"] == 250


def test_old_feed_history_is_backfilled_once_and_unknown_upload_remains_unknown(
    tmp_path,
):
    ledger, _, _, _ = setup(tmp_path)
    with ledger.database() as db:
        db.execute(
            "CREATE TABLE rss_history(token TEXT PRIMARY KEY,feed TEXT,status TEXT,created REAL,torrent_hash TEXT)"
        )
        for token in ["article", "file"]:
            db.execute(
                "INSERT INTO rss_history VALUES (?,?,?,?,?)",
                (token, "feed", "added", 10, HASH),
            )
        db.execute(
            "INSERT INTO rss_history VALUES (?,?,?,?,?)",
            ("filtered", "feed", "title_filtered", 11, None),
        )
        feed_uploads.migrate(db)
        feed_uploads.migrate(db)
        assert feed_uploads.summary(db, "feed") == {
            "downloads": 1,
            "creditedUploadedBytes": None,
            "unknownDownloads": 1,
            "estimated": True,
        }
        assert feed_uploads.summary(db, "empty")["creditedUploadedBytes"] == 0
    ledger.observe(row(100), 1700000120)
    with ledger.database() as db:
        assert feed_uploads.summary(db, "feed")["downloads"] == 1
        assert feed_uploads.summary(db, "feed")["creditedUploadedBytes"] == 50


def test_readded_torrent_preserves_feed_total_without_double_counting(tmp_path):
    ledger, _, _, _ = setup(tmp_path, created=1000000000)
    now = time.time()
    with ledger.database() as db:
        feed_uploads.record(db, "feed", HASH, "Release", now)
    ledger.observe(row(100, added_on=now - 10), now)
    ledger.forget(HASH)
    ledger.observe(row(20, added_on=now + 1), now + 2)
    ledger.observe(row(20, added_on=now + 1), now + 3)
    with ledger.database() as db:
        assert feed_uploads.summary(db, "feed")["creditedUploadedBytes"] == 120


def test_old_measurements_do_not_recredit_upload_and_reimport_uses_current_feed(
    tmp_path,
):
    ledger, _, _, _ = setup(tmp_path)
    with ledger.database() as db:
        for feed in ["first", "second"]:
            feed_uploads.record(db, feed, HASH, "Release", 10)
        feed_uploads.observe(
            db, {"hash": HASH, "category": "MediaSeed-RSS-first", "added_on": 10}, 100
        )
        feed_uploads.observe(
            db, {"hash": HASH, "category": "MediaSeed-RSS-first", "added_on": 10}, 90
        )
        feed_uploads.observe(
            db, {"hash": HASH, "category": "MediaSeed-RSS-first", "added_on": 10}, 100
        )
        assert feed_uploads.summary(db, "first")["creditedUploadedBytes"] == 100
        assert feed_uploads.summary(db, "second")["creditedUploadedBytes"] is None
        feed_uploads.observe(
            db, {"hash": HASH, "category": "MediaSeed-RSS-second", "added_on": 20}, 20
        )
        assert feed_uploads.summary(db, "first")["creditedUploadedBytes"] == 100
        assert feed_uploads.summary(db, "second")["creditedUploadedBytes"] == 20


def test_manual_reimport_does_not_add_upload_to_original_feed(tmp_path):
    ledger,_,_,_=setup(tmp_path)
    with ledger.database() as db:
        feed_uploads.record(db,'feed',HASH,'Release',10)
        feed_uploads.observe(db,{'hash':HASH,'category':'MediaSeed-RSS-feed','added_on':10},100)
        feed_uploads.observe(db,{'hash':HASH,'category':'','added_on':20},20)
        assert feed_uploads.summary(db,'feed')['creditedUploadedBytes']==100
