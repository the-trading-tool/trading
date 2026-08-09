"""Tests for the index constituent sync (2026-08).

Index membership had been frozen since an Excel import in August 2024. The
sync reconciles it against public constituent lists, which means turning each
source's notation into Yahoo's. That conversion is where silent damage would
happen -- a wrong suffix or a mangled class share puts a plausible-looking but
wrong ticker into an index, and nothing downstream would flag it.

The dot is the crux: in a US listing it separates the share class (BRK.B ->
BRK-B), in a European one it is the exchange suffix and must survive (ADS.DE
stays ADS.DE). Hence class_dot per source rather than globally.

Run: .venv/Scripts/python.exe -m pytest tests/ -q
"""
import os

import pytest

import sync_index_members as sync


@pytest.mark.parametrize('index_name, raw, expected', [
    # US: dot means share class
    ('^SPX', 'AAPL', 'AAPL'),
    ('^SPX', 'BRK.B', 'BRK-B'),
    ('^DJI', 'BF.B', 'BF-B'),
    # Hong Kong: "SEHK: 5" -> zero-padded four digits
    ('^HSI', 'SEHK:\xa05', '0005.HK'),
    ('^HSI', 'SEHK:\xa0700', '0700.HK'),
    ('^HSI', 'SEHK:\xa01398', '1398.HK'),
    # Plain codes plus an exchange suffix
    ('^ASXJO', '360', '360.AX'),
    ('^ASXJO', 'A2M', 'A2M.AX'),
    ('^TECDAX', 'AIXA', 'AIXA.DE'),
    # Japan issues alphanumeric codes now, so no numeric assumption
    ('^N225', '7203', '7203.T'),
    ('^N225', '285A', '285A.T'),
    # Already Yahoo notation: the dot is the exchange, leave it alone
    ('^STOXX50E', 'ADS.DE', 'ADS.DE'),
    ('^STOXX50E', 'ADYEN.AS', 'ADYEN.AS'),
    ('^STOXX50E', 'NDA-FI.HE', 'NDA-FI.HE'),
    ('^IBEX', 'ACS.MC', 'ACS.MC'),
    # Brazil: the trailing digit is the share class (3 ordinary, 4 preferred,
    # 11 unit) and part of the code -- it must survive untouched.
    ('^BVSP', 'PETR4', 'PETR4.SA'),
    ('^BVSP', 'VALE3', 'VALE3.SA'),
    ('^BVSP', 'KLBN11', 'KLBN11.SA'),
])
def test_symbol_notation_per_source(index_name, raw, expected):
    assert sync.to_yahoo_symbol(raw, sync.SOURCES[index_name]) == expected


def test_european_dot_is_never_rewritten():
    """The regression that would silently break every European index."""
    for name, src in sync.SOURCES.items():
        if src.get('class_dot'):
            continue
        assert sync.to_yahoo_symbol('ADS.DE', src).startswith('ADS.DE'), name


def test_suffix_is_not_applied_twice():
    # Sources are re-read on every run; a symbol already carrying its suffix
    # must not grow another one.
    assert sync.to_yahoo_symbol('7203.T', sync.SOURCES['^N225']) == '7203.T'
    assert sync.to_yahoo_symbol('A2M.AX', sync.SOURCES['^ASXJO']) == 'A2M.AX'


def test_every_source_declares_a_credible_minimum():
    """The guard that stops a layout change from wiping an index.

    It has to be per source: a fixed floor of 50 would reject the Dow's 30
    members outright.
    """
    for name, src in sync.SOURCES.items():
        assert src.get('min'), f"{name} has no minimum"
        assert src.get('name'), f"{name} has no display name"
        assert src.get('url') or src.get('file'), f"{name} has no source"


def test_curated_file_source_parses(tmp_path, monkeypatch):
    """A file source ignores comments and blank lines."""
    src_dir = os.path.dirname(os.path.abspath(sync.__file__))
    rel = 'index_members/_pytest_tmp.txt'
    path = os.path.join(src_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('# comment\n\nAAA.DE\nBBB.DE  # trailing\n\nCCC.DE\n')
    monkeypatch.setitem(sync.SOURCES, '^TEST',
                        {'file': rel, 'name': 'Test', 'min': 3})
    try:
        assert sync.fetch_constituents('^TEST') == {'AAA.DE', 'BBB.DE', 'CCC.DE'}
    finally:
        os.remove(path)


def test_curated_file_below_minimum_is_refused(tmp_path, monkeypatch):
    src_dir = os.path.dirname(os.path.abspath(sync.__file__))
    rel = 'index_members/_pytest_tmp2.txt'
    path = os.path.join(src_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('AAA.DE\n')
    monkeypatch.setitem(sync.SOURCES, '^TEST2',
                        {'file': rel, 'name': 'Test', 'min': 60})
    try:
        with pytest.raises(SystemExit):
            sync.fetch_constituents('^TEST2')
    finally:
        os.remove(path)


def test_unknown_index_is_refused():
    with pytest.raises(SystemExit):
        sync.fetch_constituents('^NOPE')


def test_sdax_file_holds_the_curated_list():
    """The SDAX has no machine-readable source, so the file is the source."""
    src_dir = os.path.dirname(os.path.abspath(sync.__file__))
    path = os.path.join(src_dir, sync.SOURCES['^SDAXI']['file'])
    assert os.path.exists(path)
    symbols = sync.fetch_constituents('^SDAXI')
    assert len(symbols) >= 60
    # Share classes resolved via ISIN -- these are the ones a name match got
    # wrong (Draegerwerk preference, Sixt ordinary, KSB preference).
    for expected in ('DRW3.DE', 'SIX2.DE', 'KSB3.DE'):
        assert expected in symbols
