"""Entry-line currency conversion (2026-08).

Reported on Halma (LSE, quoted in GBp) and Bel Fuse (US, quoted in USD): the
strategy entry line sat far away from the price. Cause: trades{year}.db books
every trade in the system currency -- all 362 rows carry 'EUR' -- while the
chart plots the stored OHLC untouched, so its axis runs in the listing
currency. Drawing the booked number straight onto that axis put Halma's entry
at 43.53 on a scale running 3000-5000 (98 % off) and Bel Fuse's at 201.75
instead of ~234 (15 % off).

The pence case is the one that hurts: GBp is a minor unit, so the factor is not
just FX but FX x 100. get_exchange_rate handles that itself, and these tests
pin that it stays handled.

The exchange rate is stubbed -- these tests must not depend on a live FX quote.

Run: .venv/Scripts/python.exe -m pytest tests/ -q
"""
import pytest

from tradinglib.main_page import render_mainpage as MP
from tradinglib.utils import DataUtils


@pytest.fixture
def fx(monkeypatch):
    """Stub get_exchange_rate: units of `symbol` per 1 `system_currency`."""
    rates = {('GBP', 'EUR'): 0.865, ('USD', 'EUR'): 1.162, ('CHF', 'EUR'): 0.94}

    def fake(symbol='EUR', system_currency='EUR', **kw):
        if symbol == system_currency:
            return 1.0
        major, factor = DataUtils.normalize_currency(symbol)
        return rates.get((major, system_currency), 1.0) * factor

    monkeypatch.setattr(DataUtils, 'get_exchange_rate', staticmethod(fake))
    return fake


def test_pence_listing_gets_the_minor_unit_factor(fx):
    """43.53 EUR is 3765 GBp, not 43.53 -- the reported Halma case."""
    got = MP._to_quote_currency(43.53, 'EUR', 'GBp')
    assert got == pytest.approx(43.53 * 0.865 * 100, rel=1e-6)
    assert 3000 < got < 5000          # on the chart's actual scale


def test_plain_foreign_listing(fx):
    """The Bel Fuse case: a 16 % error is subtle enough to look plausible."""
    assert MP._to_quote_currency(201.75, 'EUR', 'USD') == pytest.approx(234.43, abs=0.01)


def test_same_currency_is_untouched(fx):
    # A EUR listing booked in EUR must not be scaled at all.
    assert MP._to_quote_currency(18.23, 'EUR', 'EUR') == 18.23


def test_unknown_currency_does_not_convert(fx):
    """Fail open: without a known currency the value is drawn as booked.

    Guessing a rate would move the line to a wrong place silently; leaving it
    alone at worst reproduces the old behaviour for that one ticker.
    """
    assert MP._to_quote_currency(100.0, '', 'USD') == 100.0
    assert MP._to_quote_currency(100.0, 'EUR', '') == 100.0
    assert MP._to_quote_currency(100.0, 'EUR', None) == 100.0


@pytest.mark.parametrize('price', [0, 0.0, None])
def test_missing_price_is_passed_through(fx, price):
    assert MP._to_quote_currency(price, 'EUR', 'GBp') == price


def test_conversion_is_reversible(fx):
    """Round-trip against the inverse used elsewhere (price / rate)."""
    booked = 43.53
    drawn = MP._to_quote_currency(booked, 'EUR', 'GBp')
    rate = DataUtils.get_exchange_rate(symbol='GBp', system_currency='EUR')
    assert drawn / rate == pytest.approx(booked, rel=1e-9)


def test_broken_rate_lookup_leaves_the_price_alone(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError('no FX today')

    monkeypatch.setattr(DataUtils, 'get_exchange_rate', staticmethod(boom))
    assert MP._to_quote_currency(43.53, 'EUR', 'GBp') == 43.53
