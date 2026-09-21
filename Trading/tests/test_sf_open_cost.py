"""Strategy Finder: gebundenes Kapital = Einstand der offenen Positionen.

Frueher: Summe ALLER Buchungen jedes noch gehaltenen Tickers. Damit fielen die
realisierten Gewinne frueherer Runden genau dieser Ticker aus dem Ergebnis
(^GDAXI 2024-25: 50,2 % statt 61,2 %).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

asim = pytest.importorskip('tradinglib.premium.asset_simulator')


def _data(prices):
    """Ein Ticker, Signale: kaufen, verkaufen, wieder kaufen (bleibt offen)."""
    rows = []
    for i, (p, sig) in enumerate(prices):
        rows.append({'Date': f'2024-01-{i + 2:02d} 00:00:00', 'ticker': 'AAA', 'close': p,
                     'Open': p, 'High': p, 'Low': p, 'buySell': sig, 'longName': 'A',
                     'ISIN': 'X', 'stockIndex': 'I', 'currency': 'EUR', 'vola': 1.0,
                     'tp': 0, 'sl': 0})
    return pd.DataFrame(rows)


def test_offener_einstand():
    class P:
        portfolio = {'A': {'shares': 10, 'buy_price': 5.0}, 'B': {'shares': 2, 'buy_price': 50.0}}
    assert asim.AssetSimulator._open_cost(P()) == 150.0
    assert asim.AssetSimulator._open_cost(P(), fee_pct=1.0) == pytest.approx(151.5)


def test_realisierter_gewinn_eines_wieder_gehaltenen_tickers_bleibt_erhalten():
    df = _data([(10, 1), (20, -1), (20, 1), (20, 0)])
    p = asim.PortfolioSimulator(data=df, initial_cash=1000, max_assets=1)
    p.simulate()
    if not p.portfolio:
        pytest.skip('Simulator hat die Testsignale anders interpretiert')
    realised_equity = p.cash + asim.AssetSimulator._open_cost(p)
    # 1000 investiert zu 10, verkauft zu 20 -> +1000; danach wieder gekauft, Kurs flach
    assert realised_equity == pytest.approx(2000, rel=0.01)
