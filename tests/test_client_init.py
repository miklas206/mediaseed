import configparser

import pytest

from mediaseed.client_init import initialize


def test_internal_client_configuration_is_idempotent(tmp_path):
    config, secret = tmp_path / "config", tmp_path / "secrets"
    initialize(config, secret)
    original = (secret / "qbit-password").read_text()
    initialize(config, secret)
    assert (secret / "qbit-password").read_text() == original
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config / "qBittorrent/qBittorrent.conf")
    assert parser["Preferences"]["WebUI\\Username"] == "mediaseed"
    assert original not in (config / "qBittorrent/qBittorrent.conf").read_text()
    assert parser["BitTorrent"]["Session\\DefaultSavePath"] == "/downloads"


def test_never_overwrite_existing_client(tmp_path):
    config = tmp_path / "config/qBittorrent"
    config.mkdir(parents=True)
    (config / "qBittorrent.conf").write_text("existing")
    with pytest.raises(RuntimeError):
        initialize(tmp_path / "config", tmp_path / "secrets")


def test_older_preview_adds_rss_default_and_preserves_explicit_interval(tmp_path):
    config, secret = tmp_path / "config", tmp_path / "secrets"
    initialize(config, secret)
    path = config / "qBittorrent/qBittorrent.conf"
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path)
    assert parser["RSS"]["Session\\RefreshInterval"] == "5"
    original_password = (secret / "qbit-password").read_bytes()
    original_auth = dict(parser["Preferences"])
    parser.remove_section("RSS")
    with path.open("w") as output:
        parser.write(output)
    initialize(config, secret)
    parser.read(path)
    assert parser["RSS"]["Session\\RefreshInterval"] == "5"
    assert dict(parser["Preferences"]) == original_auth
    assert (secret / "qbit-password").read_bytes() == original_password
    parser["RSS"]["Session\\RefreshInterval"] = "15"
    with path.open("w") as output:
        parser.write(output)
    saved = path.read_bytes()
    initialize(config, secret)
    assert path.read_bytes() == saved
