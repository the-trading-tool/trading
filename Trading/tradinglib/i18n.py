import json
import logging
from pathlib import Path

import streamlit as st

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "de": "Deutsch",
}

_DEFAULT_LANG = "en"
_translations: dict[str, str] = {}
_current_lang: str = _DEFAULT_LANG


def _locales_dir() -> Path:
    """Return the path to the locales/ directory (sibling of tradinglib/)."""
    return Path(__file__).parent.parent / "locales"


def _load(lang: str) -> dict[str, str]:
    """Read and return the JSON translation dict for lang; returns {} on any error."""
    path = _locales_dir() / f"{lang}.json"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("i18n: locale file not found: %s", path)
        return {}
    except json.JSONDecodeError as e:
        logger.error("i18n: invalid JSON in %s: %s", path, e)
        return {}


def load_language(lang: str) -> None:
    """Load a language. Falls back to English if the file is missing."""
    global _translations, _current_lang
    if lang not in SUPPORTED_LANGUAGES:
        logger.warning("i18n: unsupported language '%s', falling back to 'en'", lang)
        lang = _DEFAULT_LANG
    data = _load(lang)
    if not data and lang != _DEFAULT_LANG:
        data = _load(_DEFAULT_LANG)
        lang = _DEFAULT_LANG
    _translations = data
    _current_lang = lang
    # st.session_state may not be available outside a Streamlit context
    try:
        st.session_state["_i18n_lang"] = lang
    except Exception:
        pass


def init_from_session(sys_config=None) -> None:
    """
    Call once per app run (top of asset_analyzer.py render()).
    Loads language from session_state, then config.db, then defaults to 'en'.
    """
    lang = st.session_state.get("_i18n_lang")
    if not lang and sys_config is not None:
        lang = sys_config.get_value("language", _DEFAULT_LANG)
    if not lang:
        lang = _DEFAULT_LANG
    if lang != _current_lang or not _translations:
        load_language(lang)


def current_language() -> str:
    """Return the language code that is currently active (e.g. 'en' or 'de')."""
    return _current_lang


def t(key: str, **kwargs) -> str:
    """Return the translated string for *key*, with optional {placeholder} substitution.

    Lazy-initialises English on the first call if no language has been loaded yet,
    so translations work even without an explicit init_from_session() call.
    Falls back to the key itself when a key is missing.

    When a key is not found in the in-memory dict, the JSON file is re-read once
    so that keys added after the process started are picked up without a restart.
    """
    if not _translations:
        load_language(_DEFAULT_LANG)
    if key not in _translations:
        # Key missing — could be a newly added translation; reload once from disk.
        fresh = _load(_current_lang)
        if key in fresh:
            _translations.update(fresh)
    text = _translations.get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text
