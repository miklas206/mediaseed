import base64
import configparser
import hashlib
import os
import secrets
from pathlib import Path


def initialize(config_root=Path("/config"), secret_root=Path("/secrets")):
    config = config_root / "qBittorrent" / "qBittorrent.conf"
    password_file = secret_root / "qbit-password"
    if config.exists() and password_file.exists():
        return
    if config.exists() or password_file.exists():
        raise RuntimeError("Incomplete client configuration; preserve existing files and repair explicitly")
    config.parent.mkdir(parents=True, exist_ok=True)
    secret_root.mkdir(parents=True, exist_ok=True)
    password = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100000, 64)
    encoded = base64.b64encode(salt).decode() + ":" + base64.b64encode(digest).decode()
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser["Preferences"] = {
        "WebUI\\Username": "mediaseed",
        "WebUI\\Password_PBKDF2": f'"@ByteArray({encoded})"',
        "WebUI\\Port": "8080",
        "WebUI\\Address": "*",
        "WebUI\\ServerDomains": "qbittorrent",
        "WebUI\\CSRFProtection": "true",
        "WebUI\\HostHeaderValidation": "true",
        "WebUI\\LocalHostAuth": "true",
        "WebUI\\AuthSubnetWhitelistEnabled": "false",
    }
    parser["BitTorrent"] = {"Session\\DefaultSavePath": "/downloads", "Session\\AddTorrentStopped": "true", "Session\\UPnP": "false"}
    parser["LegalNotice"] = {"Accepted": "true"}
    def private_opener(path, flags):
        return os.open(path, flags, 0o600)

    with open(config, "x", opener=private_opener) as output:
        parser.write(output, space_around_delimiters=False)
    with open(password_file, "x", opener=private_opener) as output:
        output.write(password)
    for path in [config, password_file]:
        path.chmod(0o600)
        if hasattr(os, "chown"):
            os.chown(path, 1000, 1000)


if __name__ == "__main__":
    initialize()