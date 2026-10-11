from mediaseed.covers import matching_poster, poster_url, Covers
import asyncio
import json
import httpx


def test_only_approved_posters_and_exact_infohash_match():
    valid = "https://image.tmdb.org/t/p/w500/example.jpg"
    assert poster_url(valid) == valid
    for url in ("http://image.tmdb.org/t/p/w500/example.jpg", "https://localhost/a.jpg", valid + "?key=private", "https://image.tmdb.org.evil/t/p/w500/example.jpg", "https://image.tmdb.org/t/p/w500/example.svg"):
        assert poster_url(url) is None
    data = {"data": [{"attributes": {"name": "Same name", "info_hash": "a" * 40, "meta": {"poster": valid}}}]}
    assert matching_poster(data, "b" * 40) is None
    assert matching_poster(data, "A" * 40) == valid


def test_cache_paths_do_not_expose_remote_urls(tmp_path):
    cache = Covers(tmp_path, None, None, None)
    assert cache.path("../secret") is None
    assert cache.path("a" * 40).parent == tmp_path / "cover-cache"


def test_pagination_only_caches_exact_match_and_verified_flags(tmp_path):
    calls = []
    downloads = []
    poster = "https://image.tmdb.org/t/p/w500/example.jpg"
    async def tracker_read(account, path, params):
        calls.append(params)
        if "cursor" not in params:
            return {"data": [{"attributes": {"info_hash": "b" * 40, "meta": {"poster": poster}}}], "links": {"next": "https://nordicbytes.org/api/torrents/filter?cursor=next"}}
        return {"data": [{"attributes": {"info_hash": "a" * 40, "featured": True, "freeleech": 0, "meta": {"poster": poster}}}]}
    async def qbit(path, payload):
        return httpx.Response(200, json=[{"hash": "a" * 40, "name": "Test.Release"}])
    cache = Covers(tmp_path, lambda kind: [{"id": "test", "name": "Test account"}], tracker_read, qbit)
    async def download(url, path):
        downloads.append(url)
        path.write_bytes(b"\xff\xd8\xffsynthetic")
    cache.download = download
    asyncio.run(cache.tick())
    assert len(calls) == 2 and calls[1]["cursor"] == "next"
    assert downloads == [poster]
    evidence = json.loads(cache.path("a" * 40).with_suffix(".json").read_text())
    assert evidence["infoHash"] == "a" * 40
    assert evidence["freeleech"] == 100 and evidence["doubleUpload"] is True
    asyncio.run(cache.tick())
    assert len(calls) == 2