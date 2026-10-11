from mediaseed.covers import matching_poster, poster_url, Covers


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