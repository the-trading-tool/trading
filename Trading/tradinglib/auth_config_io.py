"""Race-free reads and writes of the authenticator's config.yaml.

streamlit-authenticator rewrites the whole config.yaml on every cookie login
(only to set ``logged_in: true``): it opens the file with 'w' -- truncating it --
and dumps it again. All browser sessions are threads of one Streamlit process,
so after a server restart every open tab logs in at once, and a tab that reads
while another one has just truncated the file gets ``None`` from yaml.load:
"Login (unrendered) error: 'NoneType' object does not support item assignment"
(2026-11-17, five tabs reconnecting). Two overlapping writes could even leave a
garbled file.

``install()`` routes the library's three file helpers through this module:
one process-wide lock around every read-modify-write, writes go to a temp file
in the same folder that replaces the original in one step (readers outside the
lock never see a half-written file), and a read that still finds no mapping is
retried briefly instead of passed on as ``None``.
"""
import logging
import os
import tempfile
import threading
import time

import yaml
from yaml.loader import SafeLoader

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_READ_ATTEMPTS = 5
_REPLACE_ATTEMPTS = 20
_installed = False


def read_config(path: str) -> dict:
    """The parsed config.yaml as a dict (retried while the file reads as empty)."""
    with _LOCK:
        for attempt in range(_READ_ATTEMPTS):
            with open(path, 'r', encoding='utf-8') as file:
                config = yaml.load(file, Loader=SafeLoader)
            if isinstance(config, dict):
                return config
            time.sleep(0.05 * (attempt + 1))
    raise ValueError(f"{path}: no YAML mapping")


def write_config(path: str, config: dict) -> None:
    """Write *config* atomically: temp file next to *path*, then replace."""
    if not isinstance(config, dict):
        raise ValueError("config.yaml must be a mapping")
    with _LOCK:
        folder = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=folder, prefix='.config.', suffix='.yaml.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                yaml.dump(config, file, default_flow_style=False, allow_unicode=True)
            for attempt in range(_REPLACE_ATTEMPTS):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    # Windows: another handle on the target (a reader outside
                    # the lock, a virus scanner) blocks the replace briefly.
                    if attempt == _REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(0.05)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def update_config(path: str, key: str, items) -> None:
    """Set one top-level key in config.yaml under the lock."""
    with _LOCK:
        config = read_config(path)
        config[key] = items
        write_config(path, config)


def install() -> None:
    """Route streamlit-authenticator's config helpers through this module (idempotent)."""
    global _installed
    if _installed:
        return
    try:
        from streamlit_authenticator.utilities.helpers import Helpers
    except Exception:
        logger.debug("auth_config_io: streamlit_authenticator not importable", exc_info=True)
        return
    Helpers.read_config_file = classmethod(lambda cls, path: read_config(path))
    Helpers.write_config_file = classmethod(lambda cls, path, config: write_config(path, config))
    Helpers.update_config_file = classmethod(
        lambda cls, path, key, items: update_config(path, key, items))
    _installed = True
