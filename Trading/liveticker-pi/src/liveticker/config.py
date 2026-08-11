"""Configuration for the standalone collector.

No config.db and no Streamlit here: on a Raspberry Pi the collector is a service,
so its settings come from an ini file and/or environment variables. Precedence
is CLI > environment > ini file > default.
"""

import configparser
import datetime as dt
import os
import urllib.parse

DEFAULT_TARGET = "http://localhost:8080"
DEFAULT_PAGE = "/realtimekurse"

# Searched in order; the first existing file wins.
CONFIG_PATHS = (
    os.environ.get('LIVETICKER_CONFIG', ''),
    os.path.expanduser('~/.config/liveticker.ini'),
    '/etc/liveticker.ini',
)

DEFAULTS = {
    'target': DEFAULT_TARGET,
    'api_key': '',
    'transport': 'auto',          # auto | ws | browser
    'page': DEFAULT_PAGE,
    'fetch_type': 'indices',      # indices | members
    'log': 'INFO',
    'headless': 'true',           # a Pi normally has no display
    'chrome_binary': '',          # e.g. /usr/bin/chromium-browser
    'chromedriver': '',           # e.g. /usr/bin/chromedriver
    'profile_dir': '',            # browser profile root (default: XDG data dir)
    'user_agent': '',             # override the browser's UA if the site needs it
    'source_host': 'finanzen.net',
    # Empty by default ON PURPOSE: the source renders its times server-side in
    # its own zone but converts them to the browser's zone once its JavaScript
    # has run, so a freshly loaded page can show both for a few seconds.
    # Converting unconditionally would then shift the already-converted rows.
    # Set an IANA zone (e.g. Europe/Berlin) only for a source that never converts.
    'source_timezone': '',
    'cycle_seconds': '20',
    'start_time': '06:00',
    'end_time': '21:59',
}

BOOL_KEYS = ('headless',)
INT_KEYS = ('cycle_seconds',)


def _from_file(path=''):
    """Read the [liveticker] section of the first config file that exists."""
    for candidate in ([path] if path else CONFIG_PATHS):
        if candidate and os.path.exists(candidate):
            parser = configparser.ConfigParser()
            parser.read(candidate, encoding='utf-8')
            if parser.has_section('liveticker'):
                return dict(parser['liveticker']), candidate
            return dict(parser.defaults()), candidate
    return {}, ''


def load(overrides=None, path=''):
    """Build the effective settings dict.

    Environment variables are named LIVETICKER_<KEY> (upper case).
    """
    settings = dict(DEFAULTS)
    from_file, source = _from_file(path)
    settings.update({k.lower(): v for k, v in from_file.items() if v != ''})

    for key in DEFAULTS:
        value = os.environ.get(f'LIVETICKER_{key.upper()}')
        if value not in (None, ''):
            settings[key] = value

    for key, value in (overrides or {}).items():
        if value not in (None, ''):
            settings[key] = value

    for key in BOOL_KEYS:
        settings[key] = str(settings[key]).strip().lower() in ('1', 'true', 'yes', 'ja', 'on')
    for key in INT_KEYS:
        try:
            settings[key] = int(settings[key])
        except (TypeError, ValueError):
            settings[key] = int(DEFAULTS[key])

    settings['start_time'] = parse_clock(settings['start_time'], dt.time(6, 0))
    settings['end_time'] = parse_clock(settings['end_time'], dt.time(21, 59, 30))
    settings['config_file'] = source
    return settings


def parse_clock(value, default):
    """Parse 'HH:MM' or 'HH:MM:SS' into a time object."""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).time()
        except (TypeError, ValueError):
            continue
    return default


def split_target(target=''):
    """Split a target URL into the (protocol, host, port, path) parts.

    Accepts "http://localhost:8080", "localhost:8080", "trading.example.com" and
    a base path. A missing scheme becomes http for loopback hosts and https for
    everything else.
    """
    text = (target or DEFAULT_TARGET).strip()
    if '://' not in text:
        host = text.split('/')[0].split(':')[0].lower()
        scheme = 'http' if host in ('localhost', '127.0.0.1', '::1', '0.0.0.0') else 'https'
        text = f'{scheme}://{text}'

    parts = urllib.parse.urlsplit(text)
    protocol = f'{parts.scheme}://'
    host = parts.hostname or 'localhost'
    try:
        port = f':{parts.port}' if parts.port else ''
    except ValueError:
        port = ''
    return protocol, host, port, parts.path.rstrip('/')
