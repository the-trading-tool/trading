"""Tests for SystemConfig.find_values — the namespace-independent lookup.

Used by the tick ingest endpoint, which runs before login and therefore has no
session user to namespace '<user>:api_key' with.
"""

import hmac

import pytest

from tradinglib import system_config as sysconf


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Redirect every DB path to a temp dir so no production DB is touched."""
    monkeypatch.setenv('TradingDB', str(tmp_path))
    return tmp_path


def test_find_values_reads_every_namespace(config_dir):
    for user in ('admin', 'kurt', 'None'):
        sysconf.SystemConfig(username=user, bare_mode=True).set_value('api_key', 'secret-42')

    found = sysconf.SystemConfig(username='api_key', bare_mode=True).find_values('api_key')

    # The namespaced read finds nothing for this user — that is the whole point.
    assert sysconf.SystemConfig(username='api_key',
                                bare_mode=True).get_value('api_key') is None
    assert sorted(found) == ['secret-42'] * 3


def test_find_values_deserialises_and_ignores_other_keys(config_dir):
    config = sysconf.SystemConfig(username='kurt', bare_mode=True)
    config.set_value('api_key', 'secret-42')
    config.set_value('rt_prices', True)

    assert config.find_values('api_key') == ['secret-42']      # JSON quotes stripped
    assert config.find_values('rt_prices') == [True]
    assert config.find_values('does_not_exist') == []


def test_find_values_does_not_match_a_key_suffix(config_dir):
    config = sysconf.SystemConfig(username='kurt', bare_mode=True)
    config.set_value('api_key', 'real')
    config.set_value('other_api_key', 'unrelated')

    # LIKE '%:api_key' must not swallow 'kurt:other_api_key'.
    assert config.find_values('api_key') == ['real']


def test_the_api_key_check_accepts_only_the_configured_key(config_dir):
    sysconf.SystemConfig(username='kurt', bare_mode=True).set_value('api_key', 'secret-42')
    configured = sysconf.SystemConfig(username='api_key', bare_mode=True).find_values('api_key')

    def _valid(presented):
        if not presented:
            return False
        return any(value and hmac.compare_digest(str(value), str(presented))
                   for value in configured)

    assert _valid('secret-42') is True
    assert _valid('wrong') is False
    assert _valid('') is False
    assert _valid(None) is False
