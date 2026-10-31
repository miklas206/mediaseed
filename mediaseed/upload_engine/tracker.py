"""Private tracker settings; credentials never appear in status responses."""
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

NORDICBYTES_ANNOUNCE = 'https://t.nordicbytes.org/announce/'


def normalize_announce(value, profile):
    value = value.strip()
    if profile == 'nordicbytes' and re.fullmatch(r'[a-fA-F0-9]{32}', value):
        value = NORDICBYTES_ANNOUNCE + value
    match = re.match(r'^d8:announce(\d{1,4}):', value)
    if match:
        # Accept pasted torrent text, including a link bracket added by chat.
        raw = value[match.end():].removeprefix('[').encode()
        length = int(match[1])
        if not 1 <= length <= 4096 or len(raw) < length:
            raise ValueError('Torrentteksten indeholder ikke en komplet announce-adresse')
        try:
            value = raw[:length].decode('utf-8')
        except UnicodeError:
            raise ValueError('Torrentteksten indeholder ikke en gyldig announce-adresse') from None
    try:
        url = urlsplit(value)
        valid = url.scheme == 'https' and url.hostname and not url.username and not url.password and not url.fragment and not any(c.isspace() for c in value) and len(value) <= 4096
        if profile == 'nordicbytes':
            valid = valid and (url.hostname == 'nordicbytes.org' or url.hostname.endswith('.nordicbytes.org'))
        if not valid:
            raise ValueError()
    except ValueError:
        raise ValueError('Angiv din NordicBytes-passkey eller trackerens egen HTTPS announce-URL') from None
    return value


class TrackerSettings:
    def __init__(self, root):
        self.path = Path(root) / 'nordicbytes.json'

    def announce(self):
        if not self.path.is_file():
            return ''
        try:
            return normalize_announce(json.loads(self.path.read_text())['passkey'], 'nordicbytes')
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('Den gemte NordicBytes-passkey kunne ikke læses. Gem den igen') from None

    def configured(self):
        try:
            return bool(self.announce())
        except ValueError:
            return False

    def configure(self, passkey):
        if not isinstance(passkey, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', passkey.strip()):
            raise ValueError('NordicBytes-passkey skal være præcis 32 hex-tegn')
        with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'passkey': passkey.strip()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        temporary.replace(self.path)
        return {'configured': True}
