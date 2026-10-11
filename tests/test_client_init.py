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